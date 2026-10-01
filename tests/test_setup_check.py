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
        "usb_helper": lambda: {"supported": True, "installed": True, "script": True, "task": True, "version": 1,
                               "userSid": "S-1-5-21-1-2-3-1001", "detail": "installed"},
        "usb_path": lambda: {"found": True, "chain": [
            {"instanceId": "USB\\VID_05AC&PID_12A8\\00008140EXAMPLE000000000", "name": "Apple iPhone", "class": "USB"},
            {"instanceId": "USB\\ROOT_HUB30\\5&1F32783&0&0", "name": "USB Root Hub (USB 3.0)", "class": "USB"},
            {"instanceId": "PCI\\VEN_1022&DEV_149C&SUBSYS_11421B21&REV_00\\3&11583659&0&41",
             "name": "AMD USB 3.10 eXtensible Host Controller", "class": "USB"}]},
        "battery": lambda: {"CurrentCapacity": 63, "IsCharging": True, "InstantAmperage": 812, "Voltage": 3980,
                            "Temperature": 2950},
        "screen_text": lambda: {"available": True, "language": "en-US", "error": ""},
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
                          "appleService", "usbPower", "usbHelper", "usbPath", "charging", "screenText"])
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


class UsbHelperRowTests(unittest.TestCase):
    """The elevated helper is what turns 'replug the phone' into an automatic fix; the row says so."""

    def test_installed_helper_is_ok_and_required(self):
        row = checklist(probes())["items"][11]
        self.assertEqual((row["key"], row["status"], row["required"]), ("usbHelper", "ok", False))
        self.assertIn("before ever asking for a replug", row["detail"])

    def test_missing_helper_names_the_one_command_and_the_uac_prompt(self):
        result = checklist(probes(usb_helper=lambda: {"supported": True, "installed": False, "script": False,
                                                       "task": False, "version": None,
                                                       "detail": "helper script missing; scheduled task missing"}))
        row = result["items"][11]
        self.assertEqual(row["status"], "action")
        self.assertTrue(result["ready"])  # recommended, not required: posting works without it
        self.assertFalse(row["required"])
        self.assertIn("-InstallUsbHelper", row["fix"])
        self.assertIn("administrator prompt once", row["fix"])
        self.assertIn("exactly two things", row["fix"])
        self.assertIn("unplugging and replugging", row["detail"])

    def test_half_installed_and_outdated_helpers_say_what_is_off(self):
        half = checklist(probes(usb_helper=lambda: {"supported": True, "installed": False, "script": True, "task": False,
                                                     "version": 1, "detail": "scheduled task missing"}))["items"][11]
        self.assertEqual(half["status"], "action")
        self.assertIn("scheduled task missing", half["detail"])
        old = checklist(probes(usb_helper=lambda: {"supported": True, "installed": True, "script": True, "task": True,
                                                    "version": 0, "detail": "installed version 0, app expects 1"}))["items"][11]
        self.assertEqual(old["status"], "action")
        self.assertIn("app expects 1", old["detail"])

    def test_unreadable_is_blocked_and_other_systems_are_fine(self):
        self.assertEqual(checklist(probes(usb_helper=lambda: None))["items"][11]["status"], "blocked")
        other = checklist(probes(usb_helper=lambda: {"supported": False, "installed": False}))["items"][11]
        self.assertEqual((other["status"], other["required"]), ("ok", False))


class UsbPathRowTests(unittest.TestCase):
    """Which controller and hubs the phone hangs off: chipset controller or a hub = plain advice, never a block."""

    def test_cpu_controller_without_a_hub_is_ok(self):
        row = checklist(probes())["items"][12]
        self.assertEqual((row["key"], row["status"], row["required"]), ("usbPath", "ok", False))
        self.assertIn("iPhone -> root hub -> AMD CPU USB controller (DEV_149C)", row["detail"])
        self.assertIn("no hub", row["detail"])
        self.assertEqual(row["controller"], "149C")

    def test_chipset_controller_through_a_via_hub_gets_the_port_advice(self):
        chain = [
            {"instanceId": "USB\\VID_05AC&PID_12A8\\00008140EXAMPLE000000000", "name": "Apple iPhone"},
            {"instanceId": "USB\\VID_2109&PID_0817\\6&2A1B&0&3", "name": "USB 3.0 Hub", "class": "USB"},
            {"instanceId": "USB\\ROOT_HUB30\\5&4087D53&0&0", "name": "USB Root Hub (USB 3.0)"},
            {"instanceId": "PCI\\VEN_1022&DEV_43D5&SUBSYS_11421B21&REV_01\\4&2C18E2E3&0&000B",
             "name": "AMD USB 3.10 eXtensible Host Controller - 1.10 (Microsoft)"},
        ]
        result = checklist(probes(usb_path=lambda: {"found": True, "chain": chain}))
        row = result["items"][12]
        self.assertEqual(row["status"], "action")
        self.assertTrue(result["ready"])  # advice only
        self.assertIn("iPhone -> VIA hub (VID_2109) -> root hub -> AMD 500-series chipset USB controller (DEV_43D5)",
                      row["detail"])
        self.assertIn("known to drop busy USB devices", row["detail"])
        self.assertIn("1 hub", row["detail"])
        self.assertIn("rear port on the PC's own (CPU) USB controller", row["fix"])
        self.assertIn("no hub", row["fix"])
        self.assertEqual((row["controller"], row["hubs"]), ("43D5", 1))

    def test_unknown_controller_without_a_hub_is_ok_but_says_so(self):
        chain = [{"instanceId": "USB\\VID_05AC&PID_12A8\\X"}, {"instanceId": "USB\\ROOT_HUB30\\5&1&0&0"},
                 {"instanceId": "PCI\\VEN_1B21&DEV_2142\\4&1&0&0", "name": "ASMedia USB 3.1 eXtensible Host Controller"}]
        row = checklist(probes(usb_path=lambda: {"found": True, "chain": chain}))["items"][12]
        self.assertEqual(row["status"], "ok")
        self.assertIn("ASMedia USB 3.1 eXtensible Host Controller (DEV_2142)", row["detail"])
        self.assertIn("not in the known list", row["detail"])

    def test_waits_for_the_phone_and_never_reads_the_path_without_it(self):
        def never():
            raise AssertionError("read the USB path with no phone")

        result = checklist(probes(usb_path=never, ios_devices=lambda: {"found": True, "count": 0, "error": ""},
                                  wda_status=lambda: None))
        self.assertEqual(result["items"][12]["status"], "blocked")
        unreadable = checklist(probes(usb_path=lambda: None))["items"][12]
        self.assertEqual(unreadable["status"], "blocked")
        gone = checklist(probes(usb_path=lambda: {"found": False}))["items"][12]
        self.assertEqual(gone["status"], "blocked")
        self.assertIn("Reconnect", gone["fix"])

    def test_powershell_output_is_parsed_from_its_last_json_line(self):
        out = 'WARNING: something\n{"chain":[{"instanceId":"USB\\\\VID_05AC&PID_12A8\\\\X"}],"found":true}\n'
        self.assertEqual(setup_check.parse_usb_path(out)["found"], True)
        self.assertEqual(setup_check.parse_usb_path('{"found":false}'), {"found": False})
        self.assertIsNone(setup_check.parse_usb_path("nothing"))
        self.assertIsNone(setup_check.parse_usb_path("{not json"))
        # The probe is read-only PowerShell: PnP reads only, no restart, disable or remove.
        for banned in ("Restart-", "Disable-", "Enable-", "Remove-", "pnputil"):
            self.assertNotIn(banned, setup_check.USB_PATH_SCRIPT)
        self.assertIn("DEVPKEY_Device_Parent", setup_check.USB_PATH_SCRIPT)


class ChargingRowTests(unittest.TestCase):
    """The port must actually charge the phone: the 2026-09-30 stalls all happened at 1-2% while 'charging'."""

    def test_charging_phone_is_ok_and_never_required(self):
        row = checklist(probes())["items"][13]
        self.assertEqual((row["key"], row["status"], row["required"]), ("charging", "ok", False))
        self.assertIn("63% · charging · +812 mA", row["detail"])

    def test_draining_while_plugged_in_names_the_port(self):
        row = checklist(probes(battery=lambda: {"CurrentCapacity": 1, "IsCharging": True, "InstantAmperage": -2463,
                                                "Voltage": 3363, "Temperature": 2929}))["items"][13]
        self.assertEqual(row["status"], "action")
        self.assertIn("draining at 2463 mA while plugged in", row["detail"])
        self.assertIn("battery at 1%", row["detail"])
        self.assertIn("wall charger", row["fix"])

    def test_not_charging_and_unreadable_and_no_phone(self):
        row = checklist(probes(battery=lambda: {"CurrentCapacity": 55, "IsCharging": False, "InstantAmperage": -300}))["items"][13]
        self.assertEqual(row["status"], "action")
        self.assertIn("not charging on this port", row["detail"])
        row = checklist(probes(battery=lambda: {"error": "no go-ios"}))["items"][13]
        self.assertEqual(row["status"], "blocked")
        row = checklist(probes(ios_devices=lambda: {"found": False, "count": 0, "error": ""},
                               wda_status=lambda: None, inspection=lambda: {"status": "idle"}))["items"][13]
        self.assertEqual((row["status"], row["detail"]), ("blocked", "Waiting for the iPhone."))


class ScreenTextTests(unittest.TestCase):
    def test_english_engine_is_ok(self):
        self.assertEqual(statuses(checklist(probes()))["screenText"], "ok")

    def test_missing_or_non_english_engine_blocks_ready_with_the_language_fix(self):
        for probe in ({"available": False, "language": "", "error": "No Windows OCR language is installed"},
                      {"available": True, "language": "de-DE", "error": ""}, None):
            result = checklist(probes(screen_text=lambda probe=probe: probe))
            row = next(i for i in result["items"] if i["key"] == "screenText")
            self.assertNotEqual(row["status"], "ok")
            self.assertIn("Optical character recognition", row["fix"])
            self.assertFalse(result["ready"])
