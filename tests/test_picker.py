import json
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from http.server import ThreadingHTTPServer

from video_drop import server
from video_drop.core import Store
from video_drop.watch import WatchFolder


class PickerRouteTests(unittest.TestCase):
    def test_settings_routes_persist_slots_and_watch_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            exports = Path(directory) / "exports"
            exports.mkdir()
            watcher = WatchFolder(state, lambda _id: None)
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state), patch.object(server, "WATCHER", watcher):
                    base = f"http://127.0.0.1:{http.server_port}"
                    with urlopen(base + "/api/settings") as response:
                        self.assertEqual(json.load(response)["postingSlots"], ["10:00", "19:00"])
                    request = Request(base + "/api/settings/slots", data=b'{"slots":["09:30","19:00"]}',
                                      method="POST", headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        self.assertEqual(json.load(response)["postingSlots"], ["09:30", "19:00"])
                    watcher.configure(folder=exports)
                    request = Request(base + "/api/watch/toggle", data=b'{"enabled":true}',
                                      method="POST", headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        self.assertTrue(json.load(response)["watch"]["enabled"])
                    with urlopen(base + "/api/settings") as response:
                        settings = json.load(response)
                    self.assertEqual(settings["postingSlots"], ["09:30", "19:00"])
                    self.assertEqual(settings["watch"]["path"], str(exports.resolve()))
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_model_recovery_waits_for_both_local_models_and_retries_partial_draft(self):
        models = {"models": [{"name": server.VISION_MODEL}, {"name": server.TEXT_MODEL}]}
        with patch.object(server, "urlopen", return_value=io.BytesIO(json.dumps(models).encode())):
            self.assertTrue(server.local_models_ready())
        models["models"].pop()
        with patch.object(server, "urlopen", return_value=io.BytesIO(json.dumps(models).encode())):
            self.assertFalse(server.local_models_ready())

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "finished.mp4"
            source.write_bytes(b"video fixture")
            with Store(state / "video-drop.sqlite") as store:
                release_id = store.import_file(source)["id"]
                store.save_analysis(release_id, {"status": "partial", "warnings": ["local text model: connection refused"]})
            with patch.object(server, "STATE", state), patch.object(server, "queue_analysis") as queue:
                self.assertTrue(server.retry_waiting_draft(force=True))
                queue.assert_called_once_with(release_id)
            with Store(state / "video-drop.sqlite") as store:
                store.save_analysis(release_id, {"status": "partial", "warnings": ["transcription: missing model"]})
            with patch.object(server, "STATE", state), patch.object(server, "queue_analysis") as queue:
                self.assertFalse(server.retry_waiting_draft(force=True))
                queue.assert_not_called()

            with Store(state / "video-drop.sqlite") as store:
                store.save_analysis(release_id, {"status": "partial", "attempts": 3,
                                                 "warnings": ["local text model: timed out"]})
            with patch.object(server, "STATE", state), patch.object(server, "queue_analysis") as queue:
                self.assertFalse(server.retry_waiting_draft(force=True))
                queue.assert_not_called()

        class ThreeChecks:
            checks = 0

            def is_set(self):
                return self.checks >= 3

            def wait(self, _):
                self.checks += 1

        with patch.object(server, "local_models_ready", side_effect=[False, True, True]), \
             patch.object(server, "retry_waiting_draft") as retry:
            server.model_recovery_loop(ThreeChecks())
            self.assertEqual(retry.call_count, 2)
            retry.assert_any_call(force=True)
            retry.assert_any_call(force=False)

    def test_video_streams_exact_original_with_seek_ranges_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "finished.mp4"
            source.write_bytes(b"0123456789")
            with Store(state / "video-drop.sqlite") as store:
                release_id = store.import_file(source)["id"]
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state):
                    url = f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/video"
                    with urlopen(url, timeout=5) as response:
                        self.assertEqual(response.headers["Content-Type"], "video/mp4")
                        self.assertEqual(response.headers["Accept-Ranges"], "bytes")
                        self.assertEqual(response.read(), b"0123456789")
                    request = Request(url, headers={"Range": "bytes=3-6"})
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers["Content-Range"], "bytes 3-6/10")
                        self.assertEqual(response.read(), b"3456")
                    request = Request(url, headers={"Range": "bytes=-2"})
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.read(), b"89")
                    request = Request(url, headers={"Range": "bytes=20-"})
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(request, timeout=5)
                    self.assertEqual(rejected.exception.code, 416)
                    self.assertEqual(rejected.exception.headers["Content-Range"], "bytes */10")
                    rejected.exception.close()
                    source.write_bytes(b"different video")
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(url, timeout=5)
                    self.assertEqual(rejected.exception.code, 404)
                    rejected.exception.close()
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_local_server_rejects_cross_origin_and_rebound_host(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state):
                    url = f"http://127.0.0.1:{http.server_port}/api/phone/inspect"
                    for headers in ({"Origin": "https://outside.example"},
                                    {"Host": f"outside.example:{http.server_port}"}):
                        with self.subTest(headers=headers):
                            request = Request(url, data=b"", method="POST", headers=headers)
                            with self.assertRaises(HTTPError) as rejected:
                                urlopen(request, timeout=5)
                            self.assertEqual(rejected.exception.code, 403)
                            rejected.exception.close()
                    self.assertFalse((state / "phone-inspection.json").exists())
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_picker_imports_original_without_media_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "finished.mp4"
            source.write_bytes(b"video fixture")
            state = base / "state"
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state), patch.object(server, "choose_local_video", return_value=source), \
                     patch.object(server, "complete_video", return_value=True), patch.object(server, "queue_analysis"):
                    url = f"http://127.0.0.1:{http.server_port}/api/pick-file"
                    with urlopen(Request(url, data=b"", method="POST"), timeout=5) as response:
                        result = json.load(response)
                    self.assertEqual(result["release"]["source_path"], str(source.resolve()))
                    self.assertFalse((state / "media").exists())
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_path_import_rejects_unfinished_video(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            source = Path(directory) / "still-rendering.mp4"
            source.write_bytes(b"partial media")
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/import-path",
                                      data=json.dumps({"path": str(source)}).encode(),
                                      method="POST", headers={"Content-Type": "application/json"})
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(request, timeout=5)
                    self.assertEqual(rejected.exception.code, 400)
                    self.assertIn("still exporting", rejected.exception.read().decode())
                    rejected.exception.close()
                with Store(state / "video-drop.sqlite") as store:
                    self.assertEqual(store.db.execute("SELECT COUNT(*) FROM release").fetchone()[0], 0)
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_schedule_endpoint_cannot_claim_a_local_reservation_is_native(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "finished.mp4"
            source.write_bytes(b"video fixture")
            with Store(state / "video-drop.sqlite") as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
                store.authorize(release_id, "youtube")
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/schedule",
                                      data=b"{}", method="POST")
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=5)
                    self.assertEqual(raised.exception.code, 400)
                    self.assertIn("Native platform scheduling is not connected", raised.exception.read().decode())
                    raised.exception.close()
                    threads_schedule = Request(f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/schedule",
                                               data=b'{"platform":"threads"}', method="POST")
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(threads_schedule, timeout=5)
                    self.assertEqual(rejected.exception.code, 400)
                    self.assertIn("Threads only posts now", rejected.exception.read().decode())
                    rejected.exception.close()
                    receipt = Request(f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/receipt",
                                      data=b'{"platform":"youtube","url":"https://youtube.com/shorts/unverified"}',
                                      method="POST")
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(receipt, timeout=5)
                    self.assertEqual(rejected.exception.code, 400)
                    self.assertIn("Provider receipt verification is not connected", rejected.exception.read().decode())
                    rejected.exception.close()
                with Store(state / "video-drop.sqlite") as store:
                    self.assertEqual(store.release(release_id)["status"], "draft")
                    self.assertIsNone(store.release(release_id)["scheduled_at"])
                    self.assertEqual(store.release(release_id)["destinations"][0]["status"], "pending")
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_shared_title_endpoint_updates_platform_copy_without_hash_change(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "finished.mp4"
            source.write_bytes(b"video fixture")
            with Store(state / "video-drop.sqlite") as store:
                release_id = store.import_file(source)["id"]
                store.save_analysis(release_id, {"status": "complete", "suggestions": {
                    "youtube_title": "Old", "youtube_description": "Details #Game", "youtube_tags": ["Game"],
                    "instagram_caption": "Old #reels", "tiktok_caption": "Old #fyp"}})
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/shared-title",
                                      data=json.dumps({"title": "New hook"}).encode(),
                                      headers={"Content-Type": "application/json"}, method="POST")
                    with urlopen(request, timeout=5) as response:
                        result = json.load(response)["release"]
                by_platform = {d["platform"]: d for d in result["destinations"]}
                self.assertEqual(by_platform["youtube"]["title"], "New hook")
                self.assertEqual(by_platform["youtube"]["description"], "Details #Game")
                self.assertEqual(by_platform["instagram"]["description"], "New hook #reels")
                self.assertEqual(by_platform["threads"]["description"], "New hook")
                self.assertEqual(by_platform["tiktok"]["description"], "New hook #fyp")
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_shared_copy_endpoint_builds_reviewable_platform_text(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "finished.mp4"
            source.write_bytes(b"video fixture")
            with Store(state / "video-drop.sqlite") as store:
                release_id = store.import_file(source)["id"]
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/shared-copy",
                                      data=json.dumps({"title": "Valheim kick", "hashtags": "#Valheim #Kick"}).encode(),
                                      headers={"Content-Type": "application/json"}, method="POST")
                    with urlopen(request, timeout=5) as response:
                        result = json.load(response)["release"]
                by_platform = {d["platform"]: d for d in result["destinations"]}
                self.assertTrue(all(d["title"] == "Valheim kick" for d in by_platform.values()))
                self.assertEqual(by_platform["youtube"]["title"], "Valheim kick")
                self.assertEqual(by_platform["youtube"]["description"], "#Valheim #Kick #shorts")
                self.assertEqual(by_platform["instagram"]["description"], "Valheim kick #Valheim #Kick #reels")
                self.assertEqual(by_platform["threads"]["description"], "Valheim kick #Valheim #Kick")
                self.assertEqual(by_platform["tiktok"]["description"], "Valheim kick #Valheim #Kick #fyp")
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_delivery_mode_route_persists_post_now_without_a_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "finished.mp4"
            source.write_bytes(b"video fixture")
            with Store(state / "video-drop.sqlite") as store:
                release_id = store.import_file(source)["id"]
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/delivery-mode",
                                      data=b'{"mode":"post_now"}', method="POST",
                                      headers={"Content-Type": "application/json"})
                    with urlopen(request, timeout=5) as response:
                        release = json.load(response)["release"]
                    self.assertEqual(release["delivery_mode"], "post_now")
                    self.assertIsNone(release["scheduled_at"])
                with Store(state / "video-drop.sqlite") as store:
                    self.assertEqual(store.release(release_id)["delivery_mode"], "post_now")
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
