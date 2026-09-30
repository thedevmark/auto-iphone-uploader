import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_drop import server, setup_check
from video_drop.phone_space import GB
from video_drop.setup_check import SetupProbes, checklist, cloud_folders, folder_item, llm_item

ONEDRIVE = Path("C:/Users/tester/OneDrive")
VISION, TEXT = "qwen2.5vl:7b", "qwen3:14b"
# A free Apple ID signature made six days before this fixed "now"; the tests pass ``now`` in, never the wall clock.
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
SIGNED_UNTIL = (NOW + timedelta(days=6, hours=3)).isoformat()


def probes(**changes) -> SetupProbes:
    """A fully ready PC and iPhone; each test breaks one thing. Nothing here touches a phone or network."""
    values = {
        "driver": lambda: {"importError": "", "goIos": "C:/Users/tester/AppData/Roaming/npm/ios.exe", "legacyKeys": []},
        "ios_devices": lambda: {"found": True, "count": 1, "error": ""},
        "wda_status": lambda: {"value": {"ready": True, "os": {"version": "26.7"}}},
        "wda_bundle": lambda: "com.facebook.WebDriverAgentRunner.xctrunner",
        "ollama_tags": lambda: {"models": [{"name": VISION, "details": {"family": "qwen25vl"}},
                                           {"name": TEXT, "details": {"family": "qwen3"}}]},
        "ollama_installed": lambda: True,
        "cloud_folders": lambda: [("OneDrive", ONEDRIVE)],
        "watch": lambda: {"path": str(ONEDRIVE / "_Videos"), "enabled": True, "problem": "", "fix": ""},
        "inspection": lambda: {"status": "ready", "screenPoints": {"width": 440.0, "height": 956.0},
                               "installed": ["onedrive", "youtube", "instagram", "threads"]},
        "phone_space": lambda: None,
        "usb_power": lambda: {"selectiveSuspend": False, "hubsAllowedOff": 0},
        "wda_signature": lambda: {"expires": SIGNED_UNTIL, "source": "phone", "error": ""},
        "passcode": lambda: {"set": True, "source": "dotenv", "envPath": "C:/App/.env"},
        "apple_service": lambda: {"installed": True, "running": True},
        "vision_model": VISION,
        "text_model": TEXT,
    }
    values.update(changes)
    return SetupProbes(**values)


def statuses(result: dict) -> dict:
    return {item["key"]: item["status"] for item in result["items"]}


class ChecklistTests(unittest.TestCase):
    def test_ready_machine_passes_every_required_item_in_order(self):
        result = checklist(probes())
        self.assertTrue(result["ready"])
        self.assertEqual([item["key"] for item in result["items"]],
                         ["driver", "phone", "link", "inventory", "space", "llm", "folder", "signature", "passcode",
                          "appleService", "usbPower"])
        self.assertTrue(all({"key", "status", "title", "detail", "fix"} <= set(item) for item in result["items"]))
        self.assertEqual(statuses(result)["space"], "blocked")
        self.assertIn("440 × 956 points", result["items"][3]["detail"])

    def test_missing_go_ios_blocks_the_phone_chain(self):
        result = checklist(probes(driver=lambda: {"importError": "", "goIos": None, "legacyKeys": []},
                                  ios_devices=lambda: {"found": False, "count": 0, "error": ""},
                                  wda_status=lambda: None, inspection=lambda: {"status": "idle"}))
        self.assertFalse(result["ready"])
        self.assertEqual([statuses(result)[key] for key in ("driver", "phone", "link", "inventory")],
                         ["action", "blocked", "blocked", "blocked"])
        self.assertIn("npm install -g go-ios", result["items"][0]["fix"])
        self.assertIn("GO_IOS_PATH", result["items"][0]["fix"])
        self.assertNotIn("SideTap", result["items"][0]["fix"])

    def test_driver_that_cannot_load_reports_the_import_error(self):
        result = checklist(probes(driver=lambda: {"importError": "ModuleNotFoundError: No module named 'requests'",
                                                  "goIos": None, "legacyKeys": []}))
        self.assertEqual(result["items"][0]["status"], "action")
        self.assertIn("No module named 'requests'", result["items"][0]["detail"])
        self.assertIn("pip install -r requirements.txt", result["items"][0]["fix"])

    def test_settings_still_borrowed_from_sidetap_are_named_but_do_not_block(self):
        result = checklist(probes(driver=lambda: {"importError": "", "goIos": "C:/npm/ios.exe",
                                                  "legacyKeys": ["PHONE_PASSCODE"]}))
        self.assertTrue(result["ready"])
        self.assertEqual(result["items"][0]["status"], "ok")
        self.assertIn("copy PHONE_PASSCODE into this app's .env", result["items"][0]["detail"])

    def test_phone_connection_problems_each_get_one_plain_fix(self):
        cases = {
            "none": ({"found": True, "count": 0, "error": ""}, "tap Trust"),
            "two": ({"found": True, "count": 2, "error": ""}, "Unplug the others"),
            "usbmux": ({"found": True, "count": 0, "error": "dial tcp 127.0.0.1:27015: connectex refused"},
                       "Apple Devices"),
        }
        for name, (ios, fix) in cases.items():
            with self.subTest(name):
                result = checklist(probes(ios_devices=lambda ios=ios: ios))
                self.assertEqual(statuses(result)["phone"], "action")
                self.assertIn(fix, result["items"][1]["fix"])
                self.assertEqual(statuses(result)["link"], "blocked")

    def test_link_needs_webdriveragent_answering(self):
        for status in (None, {"value": {"ready": False}}, {"unexpected": True}):
            with self.subTest(status=status):
                result = checklist(probes(wda_status=lambda status=status: status))
                self.assertEqual(statuses(result)["link"], "action")
                self.assertIn("link supervisor", result["items"][2]["fix"])
                self.assertNotIn("SideTap", result["items"][2]["fix"])
                self.assertFalse(result["ready"])

    def test_link_without_webdriveragent_on_the_phone_sends_the_user_to_sideloadly(self):
        result = checklist(probes(wda_status=lambda: None, wda_bundle=lambda: None))
        self.assertEqual(statuses(result)["link"], "action")
        self.assertIn("not installed on the iPhone", result["items"][2]["detail"])
        self.assertIn("Sideloadly", result["items"][2]["fix"])

    def test_phone_apps_are_only_listed_when_the_link_is_silent(self):
        def never():
            raise AssertionError("listed the phone's apps while WDA was answering")

        self.assertTrue(checklist(probes(wda_bundle=never))["ready"])
        unplugged = checklist(probes(wda_bundle=never, wda_status=lambda: None,
                                     ios_devices=lambda: {"found": True, "count": 0, "error": ""}))
        self.assertEqual(statuses(unplugged)["link"], "blocked")

    def test_phone_details_come_from_the_last_inspection(self):
        idle = checklist(probes(inspection=lambda: {"status": "idle"}))
        self.assertEqual(idle["items"][3]["status"], "action")
        self.assertEqual(idle["items"][3]["action"], "inspect")
        failed = checklist(probes(inspection=lambda: {"status": "failed", "error": "YouTube did not open"}))
        self.assertIn("YouTube did not open", failed["items"][3]["detail"])
        running = checklist(probes(inspection=lambda: {"status": "inspecting"}))
        self.assertEqual(running["items"][3]["status"], "blocked")
        no_social = checklist(probes(inspection=lambda: {"status": "ready", "installed": ["onedrive"],
                                                         "screenPoints": {"width": 393, "height": 852}}))
        self.assertEqual(no_social["items"][3]["status"], "action")
        partial = checklist(probes())
        self.assertIn("not installed: Facebook, TikTok", partial["items"][3]["detail"])

    def test_free_space_is_optional_and_only_reported_when_known(self):
        low = checklist(probes(phone_space=lambda: {"freeBytes": 2 * GB, "checkedAt": "2026-09-30T12:00:00+00:00"}))
        self.assertEqual(low["items"][4]["status"], "action")
        self.assertIn("2.0 GB free", low["items"][4]["detail"])
        self.assertTrue(low["ready"])
        ample = checklist(probes(phone_space=lambda: {"freeBytes": 40 * GB}))
        self.assertEqual(ample["items"][4]["status"], "ok")
        self.assertFalse(ample["items"][4]["required"])


class LocalModelTests(unittest.TestCase):
    def test_setup_completes_without_a_local_model_and_says_what_it_adds(self):
        for tags, installed in ((None, False), (None, True), ({"models": []}, True)):
            with self.subTest(tags=tags, installed=installed):
                result = checklist(probes(ollama_tags=lambda: tags, ollama_installed=lambda: installed))
                entry = result["items"][5]
                self.assertTrue(result["ready"])
                self.assertEqual(entry["status"], "action")
                self.assertFalse(entry["required"])
                self.assertTrue(entry["recommended"])
                self.assertIn("not required", entry["detail"])
                self.assertIn("drafts titles and captions", entry["detail"])
                self.assertIn("calibration", entry["detail"])

    def test_a_ready_model_is_still_recommended_not_required(self):
        entry = checklist(probes())["items"][5]
        self.assertEqual((entry["status"], entry["required"], entry["recommended"]), ("ok", False, True))
        self.assertNotIn("not required", entry["detail"])

    def test_missing_ollama_explains_the_one_command_install(self):
        entry = llm_item(None, False, VISION, TEXT)
        self.assertEqual(entry["status"], "action")
        self.assertIn("winget install Ollama.Ollama", entry["fix"])
        self.assertIn(f"ollama pull {VISION}", entry["fix"])

    def test_installed_but_stopped_ollama_asks_to_open_it(self):
        entry = llm_item(None, True, VISION, TEXT)
        self.assertIn("Start menu", entry["fix"])

    def test_missing_model_names_the_exact_pull(self):
        entry = llm_item({"models": [{"name": TEXT}, {"name": "llava:7b", "details": {"families": ["llama", "clip"]}}]},
                         True, VISION, TEXT)
        self.assertEqual(entry["status"], "action")
        self.assertEqual(entry["fix"], f"Run ollama pull {VISION}, then check again.")
        self.assertEqual({model["name"]: model["vision"] for model in entry["models"]}, {TEXT: False, "llava:7b": True})
        self.assertIn("llava:7b (vision)", entry["detail"])

    def test_configured_models_mark_the_vision_model(self):
        entry = llm_item({"models": [{"name": VISION, "details": {"family": "qwen25vl"}}, {"name": TEXT}]},
                         True, VISION, TEXT)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual([model["vision"] for model in entry["models"]], [True, False])

    def test_latest_tag_matches_an_untagged_model_name(self):
        entry = llm_item({"models": [{"name": "llava:latest"}, {"name": "qwen3:latest"}]}, True, "llava", "qwen3")
        self.assertEqual(entry["status"], "ok")


class CloudFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_each_provider_is_found_from_its_usual_place(self):
        onedrive = self.home / "OneDrive"
        dropbox = self.home / "Work Dropbox"
        appdata = self.home / "AppData" / "Roaming"
        for folder in (onedrive, dropbox, self.home / "iCloudDrive", self.home / "Google Drive", appdata / "Dropbox"):
            folder.mkdir(parents=True)
        (appdata / "Dropbox" / "info.json").write_text(json.dumps({"business": {"path": str(dropbox)}}), encoding="utf-8")
        mounted = Path("G:/") / "My Drive"
        found = cloud_folders({"OneDrive": str(onedrive), "OneDriveConsumer": str(onedrive), "APPDATA": str(appdata)},
                              self.home, ["G:/"], lambda path: path == mounted or path.is_dir(),
                              lambda path: path.read_text(encoding="utf-8"))
        self.assertEqual(found, [("OneDrive", onedrive), ("Google Drive", mounted),
                                 ("Google Drive", self.home / "Google Drive"), ("Dropbox", dropbox),
                                 ("iCloud Drive", self.home / "iCloudDrive")])

    def test_nothing_is_reported_when_no_sync_folder_exists(self):
        self.assertEqual(cloud_folders({"LOCALAPPDATA": str(self.home)}, self.home, [], Path.is_dir,
                                       lambda path: path.read_text(encoding="utf-8")), [])


class FolderItemTests(unittest.TestCase):
    clouds = [("OneDrive", ONEDRIVE), ("Dropbox", ONEDRIVE / "Nested Dropbox")]

    def test_no_folder_lists_the_cloud_folders_found(self):
        entry = folder_item({"path": "", "enabled": False}, self.clouds)
        self.assertEqual((entry["status"], entry["action"]), ("action", "folder"))
        self.assertIn(str(ONEDRIVE), entry["detail"])

    def test_folder_outside_the_cloud_cannot_reach_the_phone(self):
        entry = folder_item({"path": "C:/Exports", "enabled": True}, self.clouds)
        self.assertEqual(entry["status"], "action")
        self.assertIn("inside OneDrive, Google Drive, Dropbox or iCloud Drive", entry["fix"])
        none_found = folder_item({"path": "C:/Exports", "enabled": True}, [])
        self.assertIn("No OneDrive, Google Drive, Dropbox or iCloud Drive folder was found", none_found["fix"])

    def test_other_clouds_pass_through_the_files_app_with_the_phone_note(self):
        for provider, root in (("Google Drive", Path("G:/My Drive")), ("Dropbox", Path("C:/Users/tester/Dropbox")),
                               ("iCloud Drive", Path("C:/Users/tester/iCloudDrive"))):
            for clouds in ([(provider, root)], [("OneDrive", ONEDRIVE), (provider, root)]):
                with self.subTest(provider=provider, onedrive=len(clouds) > 1):
                    entry = folder_item({"path": str(root / "Clips"), "enabled": True}, clouds)
                    self.assertEqual((entry["status"], entry["action"], entry["provider"]), ("ok", "folder", provider))
                    self.assertIn(f"inside {provider}", entry["detail"])
                    self.assertIn("through the Files app", entry["detail"])
                    self.assertIn(f"The {provider} app must be installed and signed in on the iPhone", entry["detail"])
                    self.assertIn("turned on in Files", entry["detail"])
                    result = checklist(probes(cloud_folders=lambda clouds=clouds: clouds,
                                              watch=lambda root=root: {"path": str(root / "Clips"), "enabled": True}))
                    self.assertEqual(statuses(result)["folder"], "ok")

    def test_onedrive_keeps_the_onedrive_app_route_without_the_files_note(self):
        entry = folder_item({"path": str(ONEDRIVE / "_Videos"), "enabled": True}, self.clouds)
        self.assertEqual((entry["status"], entry["provider"]), ("ok", "OneDrive"))
        self.assertIn("opens videos in the OneDrive app", entry["detail"])
        self.assertNotIn("turned on in Files", entry["detail"])

    def test_choosing_a_folder_names_every_provider_the_phone_can_open(self):
        only_onedrive = folder_item({"path": "", "enabled": False}, [("OneDrive", ONEDRIVE)])
        mixed = folder_item({"path": "", "enabled": False}, [("OneDrive", ONEDRIVE), ("Dropbox", Path("D:/Dropbox"))])
        for entry in (only_onedrive, mixed):
            self.assertIn("Pick one inside OneDrive, Google Drive, Dropbox or iCloud Drive", entry["fix"])
            self.assertNotIn("can't be used for posting", entry["fix"])

    def test_watch_must_be_on(self):
        entry = folder_item({"path": str(ONEDRIVE / "_Videos"), "enabled": False}, self.clouds)
        self.assertEqual((entry["status"], entry["action"], entry["provider"]), ("action", "watch", "OneDrive"))

    def test_watch_problem_is_shown_with_its_fix(self):
        entry = folder_item({"path": str(ONEDRIVE / "_Videos"), "enabled": True, "problem": "Folder unavailable",
                             "fix": "Reconnect the drive or choose the export folder again."}, self.clouds)
        self.assertEqual(entry["status"], "action")
        self.assertEqual(entry["fix"], "Reconnect the drive or choose the export folder again.")

    def test_the_most_specific_provider_wins(self):
        # Posting routes a nested folder by its most specific provider: here Dropbox, through Files.
        entry = folder_item({"path": str(ONEDRIVE / "Nested Dropbox" / "Clips"), "enabled": True}, self.clouds)
        self.assertEqual((entry["status"], entry["provider"]), ("ok", "Dropbox"))
        self.assertIn("inside Dropbox · the iPhone opens videos through the Files app", entry["detail"])
        sibling = folder_item({"path": str(ONEDRIVE) + " Backup", "enabled": True}, self.clouds)
        self.assertEqual(sibling["status"], "action")


class SetupRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        # The module watcher points at the real state folder; keep this test inside its own.
        self.watcher = patch.object(server, "WATCHER", server.WatchFolder(self.state, lambda release_id: None))
        self.watcher.start()

    def tearDown(self):
        self.watcher.stop()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def get(self, path: str) -> dict:
        with urlopen(self.base + path, timeout=5) as response:
            return json.load(response)

    def complete(self) -> dict:
        request = Request(self.base + "/api/setup/complete", data=b"{}",
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_setup_is_recorded_only_after_every_required_item_passes(self):
        broken = probes(wda_status=lambda: None)
        with patch.object(server, "STATE", self.state), \
                patch.object(server, "setup_probes", lambda state, watch, inspection: broken):
            result = self.get("/api/setup")
            self.assertFalse(result["ready"])
            self.assertEqual(result["completedAt"], "")
            with self.assertRaises(HTTPError) as rejected:
                self.complete()
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()
            self.assertEqual(self.get("/api/settings")["setupCompletedAt"], "")
        with patch.object(server, "STATE", self.state), \
                patch.object(server, "setup_probes", lambda state, watch, inspection: probes()):
            self.assertTrue(self.get("/api/setup")["ready"])
            first = self.complete()["completedAt"]
            self.assertTrue(first)
            self.assertEqual(self.complete()["completedAt"], first)
            self.assertEqual(self.get("/api/setup")["completedAt"], first)
            self.assertEqual(self.get("/api/settings")["setupCompletedAt"], first)

    def test_route_passes_the_state_folder_and_app_readers_to_the_probes(self):
        seen = {}

        def factory(state, watch, inspection):
            seen.update(state=state, inspection=inspection())
            return probes()

        (self.state / "phone-inspection.json").write_text('{"status": "failed", "error": "old"}', encoding="utf-8")
        with patch.object(server, "STATE", self.state), patch.object(server, "setup_probes", factory):
            self.get("/api/setup")
        self.assertEqual(seen, {"state": self.state, "inspection": {"status": "failed", "error": "old"}})

    def test_measured_free_space_is_kept_for_the_checklist(self):
        with patch.object(server, "STATE", self.state), patch.object(server, "phone_free_bytes", return_value=7 * GB):
            self.assertEqual(server.measured_phone_space(), 7 * GB)
        real = setup_check.local_probes(self.state, lambda: {}, lambda: {})
        self.assertEqual(real.phone_space()["freeBytes"], 7 * GB)
        with patch.object(server, "STATE", self.state), patch.object(server, "phone_free_bytes", return_value=None):
            self.assertIsNone(server.measured_phone_space())
        self.assertEqual(real.phone_space()["freeBytes"], 7 * GB)


class GoIosParsingTests(unittest.TestCase):
    def run_list(self, stdout: str, stderr: str = "", code: int = 0) -> dict:
        completed = type("Completed", (), {"stdout": stdout, "stderr": stderr, "returncode": code})()
        with patch("video_drop.setup_check.subprocess.run", return_value=completed) as run:
            result = setup_check.ios_devices("ios.exe")
        self.assertEqual(run.call_args.args[0], ["ios.exe", "list"])
        return result

    def test_device_ids_are_counted_not_returned(self):
        result = self.run_list('{"deviceList":["00008140-0001"]}\n')
        self.assertEqual(result, {"found": True, "count": 1, "error": ""})

    def test_go_ios_error_is_reported(self):
        result = self.run_list("", '{"level":"fatal","msg":"could not connect to usbmuxd"}\n', 1)
        self.assertEqual(result["count"], 0)
        self.assertIn("usbmuxd", result["error"])

    def test_missing_go_ios_is_not_run(self):
        with patch("video_drop.setup_check.subprocess.run") as run:
            self.assertEqual(setup_check.ios_devices(None), {"found": False, "count": 0, "error": ""})
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class UsbPowerTests(unittest.TestCase):
    # 2026-09-30: Windows surprise-removed the iPhone mid-upload with selective suspend on.
    def test_power_saving_needs_action_with_the_exact_fix(self):
        from video_drop.setup_check import usb_power_item
        entry = usb_power_item({"selectiveSuspend": True, "hubsAllowedOff": 2})
        self.assertEqual(entry["status"], "action")
        self.assertTrue(entry["required"])
        self.assertIn("selective suspend is on", entry["detail"])
        self.assertIn("2 USB hubs", entry["detail"])
        self.assertIn("powercfg /setacvalueindex", entry["fix"])

    def test_power_saving_off_is_ok_and_unreadable_is_blocked(self):
        from video_drop.setup_check import usb_power_item
        self.assertEqual(usb_power_item({"selectiveSuspend": False, "hubsAllowedOff": 0})["status"], "ok")
        self.assertEqual(usb_power_item(None)["status"], "blocked")


class SignatureTests(unittest.TestCase):
    """The WebDriverAgent signature is the user's own (Sideloadly); the checklist counts its days down."""

    def entry(self, **changes) -> dict:
        return [item for item in checklist(probes(**changes))["items"] if item["key"] == "signature"][0]

    def test_days_left_are_counted_from_the_phone_profile(self):
        entry = setup_check.signature_item({"expires": SIGNED_UNTIL, "source": "phone"}, True, True, now=NOW)
        self.assertEqual(entry["status"], "ok")
        self.assertIn("6 days left", entry["detail"])
        self.assertNotIn("re-sign soon", entry["detail"])
        self.assertTrue(checklist(probes())["ready"])

    def test_last_two_days_say_to_re_sign_soon_without_blocking(self):
        soon = (NOW + timedelta(days=1, hours=12)).isoformat()
        entry = setup_check.signature_item({"expires": soon}, True, True, now=NOW)
        self.assertEqual(entry["status"], "ok")
        self.assertIn("1 day left", entry["detail"])
        self.assertIn("phone_resign.py", entry["detail"])

    def test_expired_or_expiring_today_needs_the_re_sign_steps(self):
        for expires in ((NOW - timedelta(hours=2)).isoformat(), (NOW + timedelta(hours=5)).isoformat()):
            with self.subTest(expires=expires):
                entry = setup_check.signature_item({"expires": expires}, True, True, now=NOW)
                self.assertEqual(entry["status"], "action")
                self.assertIn("phone_resign.py", entry["fix"])
                self.assertIn("Sideloadly", entry["fix"])
        expired = self.entry(wda_signature=lambda: {"expires": "2020-01-01T00:00:00+00:00"})
        self.assertEqual(expired["status"], "action")

    def test_unreadable_expiry_names_the_missing_tool_but_does_not_block(self):
        entry = setup_check.signature_item(
            {"expires": None, "error": "pymobiledevice3 is required to read the provisioning profile"},
            True, True, now=NOW)
        self.assertEqual(entry["status"], "ok")
        self.assertIn("requirements-resign.txt", entry["detail"])
        unknown = setup_check.signature_item(None, True, True, now=NOW)
        self.assertEqual(unknown["status"], "ok")
        self.assertIn("days left unknown", unknown["detail"])

    def test_missing_runner_gets_the_exact_sideloadly_steps(self):
        entry = self.entry(wda_status=lambda: None, wda_bundle=lambda: None)
        self.assertEqual(entry["status"], "action")
        for step in ("sideloadly.io", "WebDriverAgent.ipa", "Apple ID", "VPN & Device Management",
                     "phone_resign.py", "7 days"):
            self.assertIn(step, entry["fix"])

    def test_signature_waits_for_the_phone_and_never_reads_it_first(self):
        def never():
            raise AssertionError("read the phone's profiles without a phone connected")

        entry = self.entry(ios_devices=lambda: {"found": True, "count": 0, "error": ""}, wda_signature=never)
        self.assertEqual(entry["status"], "blocked")
        missing = self.entry(wda_status=lambda: None, wda_bundle=lambda: None, wda_signature=never)
        self.assertEqual(missing["status"], "action")

    def test_answering_wda_counts_as_installed_without_listing_apps(self):
        def never():
            raise AssertionError("listed the phone's apps while WDA was answering")

        self.assertEqual(self.entry(wda_bundle=never)["status"], "ok")


class PasscodeTests(unittest.TestCase):
    def test_missing_passcode_tells_the_user_where_to_put_it_and_never_asks_for_it_here(self):
        entry = setup_check.passcode_item({"set": False, "source": "", "envPath": "C:/App/.env"})
        self.assertEqual(entry["status"], "action")
        self.assertIn("C:/App/.env", entry["fix"])
        self.assertIn("PHONE_PASSCODE=", entry["fix"])
        self.assertIn("never shows or logs it", entry["fix"])
        self.assertFalse(checklist(probes(passcode=lambda: {"set": False, "source": "", "envPath": ".env"}))["ready"])

    def test_saved_passcode_is_reported_without_its_value(self):
        entry = setup_check.passcode_item({"set": True, "source": "dotenv", "envPath": "C:/App/.env"})
        self.assertEqual(entry["status"], "ok")
        self.assertIn("never shown", entry["detail"])
        legacy = setup_check.passcode_item({"set": True, "source": "legacy", "envPath": "C:/App/.env"})
        self.assertEqual(legacy["status"], "ok")
        self.assertIn("copy PHONE_PASSCODE into this app's .env", legacy["detail"])

    def test_probe_reports_where_the_passcode_comes_from_and_not_what_it_is(self):
        import os

        from video_drop.phone import config

        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / ".env"
            with patch.object(config, "ENV_FILE", env_file), patch.object(config, "_legacy", {}), \
                    patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PHONE_PASSCODE", None)
                self.assertEqual(setup_check.passcode_probe(), {"set": False, "source": "", "envPath": str(env_file)})
                env_file.write_text("PHONE_PASSCODE=123456\n", encoding="utf-8")
                probe = setup_check.passcode_probe()
                self.assertEqual(probe, {"set": True, "source": "dotenv", "envPath": str(env_file)})
                self.assertNotIn("123456", json.dumps(probe))
            env_file.write_text("", encoding="utf-8")
            with patch.object(config, "ENV_FILE", env_file), patch.object(config, "_legacy", {"PHONE_PASSCODE": "1"}), \
                    patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PHONE_PASSCODE", None)
                self.assertEqual(setup_check.passcode_probe()["source"], "legacy")


class AppleServiceTests(unittest.TestCase):
    def test_service_states_each_get_one_fix(self):
        missing = setup_check.apple_service_item({"installed": False, "running": False})
        self.assertEqual(missing["status"], "action")
        self.assertIn("Apple Devices", missing["fix"])
        stopped = setup_check.apple_service_item({"installed": True, "running": False})
        self.assertEqual(stopped["status"], "action")
        self.assertIn("services.msc", stopped["fix"])
        self.assertEqual(setup_check.apple_service_item({"installed": True, "running": True})["status"], "ok")
        self.assertEqual(setup_check.apple_service_item(None)["status"], "blocked")
        self.assertFalse(checklist(probes(apple_service=lambda: {"installed": True, "running": False}))["ready"])

    def test_sc_query_output_is_parsed(self):
        running = ("\nSERVICE_NAME: Apple Mobile Device Service \n        TYPE               : 10  WIN32_OWN_PROCESS  \n"
                   "        STATE              : 4  RUNNING \n")
        self.assertEqual(setup_check.parse_service_query(0, running), {"installed": True, "running": True})
        stopped = running.replace("4  RUNNING", "1  STOPPED")
        self.assertEqual(setup_check.parse_service_query(0, stopped), {"installed": True, "running": False})
        absent = "[SC] EnumQueryServicesStatus:OpenService FAILED 1060:\n\nThe specified service does not exist.\n"
        self.assertEqual(setup_check.parse_service_query(1060, absent), {"installed": False, "running": False})
        self.assertIsNone(setup_check.parse_service_query(0, "garbage"))
