import json
import tempfile
import threading
import unittest
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


def probes(**changes) -> SetupProbes:
    """A fully ready PC and iPhone; each test breaks one thing. Nothing here touches a phone or network."""
    values = {
        "sidetap_root": Path("C:/Tools/SideTap"),
        "sidetap_import_error": lambda root: "",
        "ios_devices": lambda: {"found": True, "count": 1, "error": ""},
        "wda_status": lambda: {"value": {"ready": True, "os": {"version": "26.7"}}},
        "ollama_tags": lambda: {"models": [{"name": VISION, "details": {"family": "qwen25vl"}},
                                           {"name": TEXT, "details": {"family": "qwen3"}}]},
        "ollama_installed": lambda: True,
        "cloud_folders": lambda: [("OneDrive", ONEDRIVE)],
        "watch": lambda: {"path": str(ONEDRIVE / "_Videos"), "enabled": True, "problem": "", "fix": ""},
        "inspection": lambda: {"status": "ready", "screenPoints": {"width": 440.0, "height": 956.0},
                               "installed": ["onedrive", "youtube", "instagram", "threads"]},
        "phone_space": lambda: None,
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
                         ["sidetap", "phone", "link", "inventory", "space", "llm", "folder"])
        self.assertTrue(all({"key", "status", "title", "detail", "fix"} <= set(item) for item in result["items"]))
        self.assertEqual(statuses(result)["space"], "blocked")
        self.assertIn("440 × 956 points", result["items"][3]["detail"])

    def test_missing_sidetap_blocks_the_phone_chain(self):
        result = checklist(probes(sidetap_import_error=lambda root: None,
                                  ios_devices=lambda: {"found": False, "count": 0, "error": ""},
                                  wda_status=lambda: None, inspection=lambda: {"status": "idle"}))
        self.assertFalse(result["ready"])
        self.assertEqual([statuses(result)[key] for key in ("sidetap", "phone", "link", "inventory")],
                         ["action", "blocked", "blocked", "blocked"])
        self.assertIn("Install SideTap", result["items"][0]["fix"])

    def test_sidetap_that_cannot_load_reports_the_import_error(self):
        result = checklist(probes(sidetap_import_error=lambda root: "ModuleNotFoundError: No module named 'PIL'"))
        self.assertEqual(result["items"][0]["status"], "action")
        self.assertIn("No module named 'PIL'", result["items"][0]["detail"])

    def test_phone_connection_problems_each_get_one_plain_fix(self):
        cases = {
            "none": ({"found": True, "count": 0, "error": ""}, "tap Trust"),
            "two": ({"found": True, "count": 2, "error": ""}, "Unplug the others"),
            "usbmux": ({"found": True, "count": 0, "error": "dial tcp 127.0.0.1:27015: connectex refused"},
                       "Apple Devices"),
            "no go-ios": ({"found": False, "count": 0, "error": ""}, "Reinstall SideTap"),
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
                self.assertIn("SideTap", result["items"][2]["fix"])
                self.assertFalse(result["ready"])

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
        self.assertIn("OneDrive or Dropbox", entry["fix"])
        none_found = folder_item({"path": "C:/Exports", "enabled": True}, [])
        self.assertIn("Install one of them", none_found["fix"])

    def test_watch_must_be_on(self):
        entry = folder_item({"path": str(ONEDRIVE / "_Videos"), "enabled": False}, self.clouds)
        self.assertEqual((entry["status"], entry["action"], entry["provider"]), ("action", "watch", "OneDrive"))

    def test_watch_problem_is_shown_with_its_fix(self):
        entry = folder_item({"path": str(ONEDRIVE / "_Videos"), "enabled": True, "problem": "Folder unavailable",
                             "fix": "Reconnect the drive or choose the export folder again."}, self.clouds)
        self.assertEqual(entry["status"], "action")
        self.assertEqual(entry["fix"], "Reconnect the drive or choose the export folder again.")

    def test_the_most_specific_provider_wins(self):
        entry = folder_item({"path": str(ONEDRIVE / "Nested Dropbox" / "Clips"), "enabled": True}, self.clouds)
        self.assertEqual((entry["status"], entry["provider"]), ("ok", "Dropbox"))
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
