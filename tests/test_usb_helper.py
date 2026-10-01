"""The USB recovery helper: the client's request/result protocol, the read-only install check,
the WDA settings profiles, and static guarantees about the SYSTEM-side script."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from video_drop import usb_helper
from video_drop.phone import wda_profiles

HELPER_DIR = Path(usb_helper.__file__).resolve().parent
SCRIPT = HELPER_DIR / "usb_helper.ps1"
INSTALLER = HELPER_DIR / "install_usb_helper.ps1"
WINDOWS_INSTALLER = HELPER_DIR.parent.parent / "scripts" / "install_windows.ps1"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def installed_root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "requests").mkdir()
    (root / "results").mkdir()
    (root / usb_helper.SCRIPT_NAME).write_text("# helper", encoding="utf-8")
    (root / usb_helper.CONFIG_NAME).write_text(json.dumps({"version": 1, "userSid": "S-1-5-21-1-2-3-1001"}),
                                               encoding="utf-8")
    return root


class ClientTests(unittest.TestCase):
    def test_only_the_two_fixed_commands_exist(self):
        self.assertEqual(usb_helper.COMMANDS, ("restart-apple-service", "restart-usb-device"))
        with self.assertRaises(ValueError):
            usb_helper.request("Restart-Service anything", root=Path("."), run=lambda args: (0, ""))

    def test_request_writes_a_nonce_file_starts_the_task_and_returns_the_result(self):
        with TemporaryDirectory() as tmp:
            root = installed_root(tmp)
            clock = Clock()
            runs = []

            def run(args):
                runs.append(args)
                # The helper (SYSTEM) answers a moment later, by nonce, and deletes the request.
                request = next((root / "requests").glob("*.json"))
                body = json.loads(request.read_text())
                self.assertEqual(set(body), {"command", "nonce", "issued"})
                self.assertRegex(request.name, r"^[0-9a-f]{16}\.json$")
                (root / "results" / request.name).write_text(json.dumps(
                    {"nonce": body["nonce"], "ok": True, "command": body["command"], "detail": "started: Running"}))
                request.unlink()
                return 0, "SUCCESS"

            result = usb_helper.request("restart-apple-service", root=root, run=run, sleep=clock.sleep, clock=clock)
            self.assertEqual(runs, [["/Run", "/TN", usb_helper.TASK_NAME]])
            self.assertTrue(result["ok"])
            self.assertEqual(result["detail"], "started: Running")
            self.assertEqual(list((root / "requests").iterdir()), [])

    def test_no_result_in_time_is_unknown_never_success(self):
        with TemporaryDirectory() as tmp:
            root = installed_root(tmp)
            clock = Clock()
            result = usb_helper.request("restart-usb-device", root=root, run=lambda args: (0, ""), wait=10,
                                        sleep=clock.sleep, clock=clock)
            self.assertIsNone(result["ok"])
            self.assertIn("no result within 10s", result["detail"])
            self.assertGreaterEqual(clock.now, 10)

    def test_task_that_cannot_start_fails_and_removes_the_request(self):
        with TemporaryDirectory() as tmp:
            root = installed_root(tmp)
            result = usb_helper.request("restart-usb-device", root=root, run=lambda args: (1, "ERROR: Access is denied."),
                                        sleep=lambda s: None, clock=Clock())
            self.assertFalse(result["ok"])
            self.assertIn("could not start the helper task", result["detail"])
            self.assertEqual(list((root / "requests").iterdir()), [])

    def test_missing_helper_fails_fast_with_the_install_hint(self):
        with TemporaryDirectory() as tmp:
            result = usb_helper.request("restart-apple-service", root=Path(tmp), run=lambda args: (0, ""))
            self.assertFalse(result["ok"])
            self.assertIn("-InstallUsbHelper", result["detail"])

    def test_a_foreign_result_with_another_nonce_is_ignored(self):
        with TemporaryDirectory() as tmp:
            root = installed_root(tmp)
            clock = Clock()

            def run(args):
                (root / "results" / "0123456789abcdef.json").write_text(json.dumps({"nonce": "0123456789abcdef", "ok": True}))
                return 0, ""

            result = usb_helper.request("restart-apple-service", root=root, run=run, wait=3, sleep=clock.sleep, clock=clock)
            self.assertIsNone(result["ok"])


class StatusTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "the helper is a Windows scheduled task")
    def test_installed_needs_the_script_and_the_task(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            none = usb_helper.status(root, run=lambda args: (1, "ERROR: The system cannot find the file specified."))
            self.assertEqual((none["installed"], none["script"], none["task"]), (False, False, False))
            self.assertIn("helper script missing", none["detail"])
            installed_root(tmp)
            half = usb_helper.status(root, run=lambda args: (1, "missing"))
            self.assertEqual((half["installed"], half["script"], half["task"]), (False, True, False))
            full = usb_helper.status(root, run=lambda args: (0, "Folder: \\AutoIphoneUploader"))
            self.assertTrue(full["installed"])
            self.assertEqual(full["version"], usb_helper.HELPER_VERSION)
            self.assertEqual(full["userSid"], "S-1-5-21-1-2-3-1001")
            (root / usb_helper.CONFIG_NAME).write_text(json.dumps({"version": 0}), encoding="utf-8")
            stale = usb_helper.status(root, run=lambda args: (0, ""))
            self.assertTrue(stale["installed"])
            self.assertIn("app expects", stale["detail"])

    def test_status_only_queries_never_runs(self):
        seen = []
        usb_helper.status(Path("."), run=lambda args: seen.append(args) or (1, ""))
        if sys.platform == "win32":
            self.assertEqual(seen, [["/Query", "/TN", usb_helper.TASK_NAME]])
        else:
            self.assertEqual(seen, [])


class ScriptGuaranteesTests(unittest.TestCase):
    """What the threat model in docs/usb-recovery-helper.md promises, pinned against the source."""

    def setUp(self):
        self.script = SCRIPT.read_text(encoding="utf-8")
        self.installer = INSTALLER.read_text(encoding="utf-8")

    def test_helper_takes_no_parameters_and_never_evaluates_request_text(self):
        self.assertRegex(self.script, r"\[CmdletBinding\(\)\]\s*param\(\)")
        for banned in ("Invoke-Expression", "iex ", "$args", "Start-Process", "-Command", "cmd.exe", "[scriptblock]::Create"):
            self.assertNotIn(banned, self.script, banned)

    def test_only_two_commands_are_in_the_table_and_they_match_the_client(self):
        table = re.findall(r"^\s+'([a-z-]+)'\s+=\s+\{", self.script, re.MULTILINE)
        self.assertEqual(tuple(table), usb_helper.COMMANDS)

    def test_every_request_check_is_present(self):
        for check in ("ReparsePoint", "MaxRequestBytes", "Get-OwnerSid", "allowedSid", "'^([0-9a-f]{16})\\.json$'",
                      "MaxRequestAgeSeconds", "Test-RateLimit", "ContainsKey($command)"):
            self.assertIn(check, self.script, check)

    def test_device_instance_ids_come_from_windows_enumeration_not_the_request(self):
        self.assertIn("Get-PnpDevice -PresentOnly", self.script)
        self.assertIn("USB\\VID_05AC&PID_12A8\\", self.script)
        self.assertNotIn("$body.instance", self.script)
        self.assertNotIn("$body.id", self.script)
        # pnputil and taskkill are called by absolute System32 path with fixed switches.
        self.assertIn('"$env:SystemRoot\\System32\\pnputil.exe" /restart-device', self.script)
        self.assertIn('"$env:SystemRoot\\System32\\taskkill.exe" /F /IM $AppleServiceImage', self.script)

    def test_installer_locks_the_folder_and_grants_start_to_one_user(self):
        self.assertIn("/inheritance:r", self.installer)
        self.assertIn("(OI)(CI)RX", self.installer)  # everyone else: read only
        self.assertIn("SetSecurityDescriptor", self.installer)
        self.assertIn("GRGX;;;$UserSid", self.installer)
        self.assertIn("^S-1-5-21-", self.installer)  # a user SID, never a group like Everyone
        self.assertIn("Test-ReparsePoint", self.installer)
        self.assertIn("-MultipleInstances IgnoreNew", self.installer)
        self.assertIn("-ExecutionTimeLimit", self.installer)
        self.assertIn("-UserId $SystemSid -LogonType ServiceAccount", self.installer)
        # No trigger: the task only ever runs on demand.
        self.assertNotIn("New-ScheduledTaskTrigger", self.installer)

    def test_windows_installer_exposes_the_switches_and_never_installs_silently(self):
        text = WINDOWS_INSTALLER.read_text(encoding="utf-8")
        self.assertIn("[switch]$InstallUsbHelper", text)
        self.assertIn("[switch]$UninstallUsbHelper", text)
        self.assertIn("-Verb RunAs", text)  # UAC consent, never a silent elevation
        # The regular install path only reads; the elevated step needs the explicit switch.
        self.assertIn("if ($InstallUsbHelper -or $UninstallUsbHelper)", text)

    @unittest.skipUnless(sys.platform == "win32", "PowerShell parser")
    def test_scripts_parse_under_powershell(self):
        for path in (SCRIPT, INSTALLER, WINDOWS_INSTALLER):
            with self.subTest(path=path.name):
                probe = ("$t=$null;$e=$null;[System.Management.Automation.Language.Parser]::ParseFile("
                         f"'{path}',[ref]$t,[ref]$e)|Out-Null;$e.Count")
                completed = subprocess.run(["powershell", "-NoProfile", "-Command", probe], capture_output=True,
                                           text=True, timeout=60)
                self.assertEqual(completed.stdout.strip(), "0", completed.stdout + completed.stderr)


class ProfileTests(unittest.TestCase):
    def test_profiles_supply_defaults_only(self):
        env = {}
        applied = wda_profiles.apply_profile("lean", environ=env, env_file=Path(os.devnull))
        self.assertEqual(applied, {"MJPEG_SETTINGS": "0", "WDA_ACCESSIBILITY_DEADLINE": "0.5",
                                   "WDA_SNAPSHOT_MAX_DEPTH": "20", "WDA_IDLE_WAIT": "0"})
        # An explicit key (process env or .env) always wins over the profile.
        env = {"WDA_SNAPSHOT_MAX_DEPTH": "40"}
        with TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("WDA_IDLE_WAIT=1\n", encoding="utf-8")
            applied = wda_profiles.apply_profile("media", environ=env, env_file=dotenv)
        self.assertEqual(env["WDA_SNAPSHOT_MAX_DEPTH"], "40")
        self.assertNotIn("WDA_IDLE_WAIT", env)
        self.assertEqual(set(applied), {"MJPEG_SETTINGS", "WDA_ACCESSIBILITY_DEADLINE"})
        self.assertEqual(wda_profiles.apply_profile("default", environ={}, env_file=Path(os.devnull)), {})

    def test_profile_name_comes_from_env_then_dotenv_and_typos_raise(self):
        self.assertEqual(wda_profiles.profile_name({}, {}), "default")
        self.assertEqual(wda_profiles.profile_name({"WDA_SETTINGS_PROFILE": "Lean"}, {"WDA_SETTINGS_PROFILE": "media"}), "lean")
        self.assertEqual(wda_profiles.profile_name({}, {"WDA_SETTINGS_PROFILE": "media"}), "media")
        with self.assertRaises(ValueError):
            wda_profiles.apply_profile("lena", environ={}, env_file=Path(os.devnull))

    def test_media_profile_matches_the_published_advice(self):
        media = wda_profiles.PROFILES["media"]
        self.assertTrue(10 <= int(media["WDA_SNAPSHOT_MAX_DEPTH"]) <= 15)  # appium/appium#19255 on TikTok
        self.assertGreater(float(media["WDA_ACCESSIBILITY_DEADLINE"]), 0)  # WDA #1214: 0 = unbounded
        self.assertEqual(media["WDA_IDLE_WAIT"], "0")
        self.assertEqual(media["MJPEG_SETTINGS"], "0")

    def test_the_profile_is_applied_before_config_reads_its_keys(self):
        code = ("import os, video_drop.phone.config as c; "
                "print(c.WDA_ACCESSIBILITY_DEADLINE, c.WDA_SNAPSHOT_MAX_DEPTH, c.WDA_IDLE_WAIT, "
                "os.environ.get('MJPEG_SETTINGS'))")
        env = {**os.environ, "WDA_SETTINGS_PROFILE": "lean", "VIDEO_DROP_NO_LEGACY_ENV": "1"}
        for key in wda_profiles.PROFILES["lean"]:
            env.pop(key, None)
        # The child reads the real .env; a key set there legitimately beats the profile (checked below).
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                                   cwd=str(HELPER_DIR.parent.parent), timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        values = completed.stdout.split()
        dotenv = wda_profiles._dotenv(HELPER_DIR.parent.parent / ".env")
        if not any(key in dotenv for key in wda_profiles.PROFILES["lean"]):
            self.assertEqual(values, ["0.5", "20", "0.0", "0"])


if __name__ == "__main__":
    unittest.main()
