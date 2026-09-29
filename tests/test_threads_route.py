import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_drop import server
from video_drop.core import Store


class ThreadsPostRouteTests(unittest.TestCase):
    def test_phone_failure_before_final_tap_keeps_release_retryable(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "accounts.json").write_text('{"threads":"@creator"}', encoding="utf-8")
            source = state / "clip.mp4"
            source.write_bytes(b"finished video")
            db = state / "video-drop.sqlite"
            with Store(db, {"threads": "@creator"}) as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "instagram", "@creator", "", "Approved caption", "")
                store.save_text(release_id, "threads", "@creator", "", "", "")
                store.authorize(release_id, "threads")
            with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                    patch("scripts.phone_threads.run", side_effect=RuntimeError("USB link unavailable")):
                server.queue_threads_post(release_id)
                for _ in range(50):
                    result = server.phone_action_status()
                    if result["status"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(result["status"], "failed")
                self.assertIn("USB link unavailable", result["message"])
                with Store(db, {"threads": "@creator"}) as store:
                    self.assertEqual(store.release(release_id)["status"], "draft")
                    self.assertEqual(next(d for d in store.release(release_id)["destinations"]
                                          if d["platform"] == "threads")["status"], "pending")

    def test_one_confirmed_native_post_action_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "accounts.json").write_text('{"threads":"@creator"}', encoding="utf-8")
            source = state / "clip.mp4"
            source.write_bytes(b"finished video")
            db = state / "video-drop.sqlite"
            with Store(db, {"threads": "@creator"}) as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "instagram", "@creator", "", "Approved caption", "")
                store.save_text(release_id, "threads", "@creator", "", "Approved caption", "")
                store.authorize(release_id, "threads")

            started, finish = threading.Event(), threading.Event()

            def fake_run(item_id, db_path, *, commit):
                self.assertEqual((item_id, db_path, commit), (release_id, db, True))
                started.set()
                self.assertTrue(finish.wait(5))
                with Store(db_path, {"threads": "@creator"}) as store:
                    store.mark_unconfirmed(item_id, "threads")
                return {"message": "Final tap sent; check native Threads receipt before any retry"}

            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                        patch("scripts.phone_threads.run", side_effect=fake_run):
                    url = f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/threads-post"

                    def post():
                        return Request(url, data=b"{}", method="POST")

                    with urlopen(post(), timeout=5) as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(json.load(response)["status"], "running")
                    self.assertTrue(started.wait(5))
                    with self.assertRaises(HTTPError) as duplicate:
                        urlopen(post(), timeout=5)
                    self.assertEqual(duplicate.exception.code, 400)
                    self.assertIn("already running", duplicate.exception.read().decode())
                    duplicate.exception.close()

                    finish.set()
                    for _ in range(50):
                        with urlopen(f"http://127.0.0.1:{http.server_port}/api/phone-action", timeout=5) as response:
                            result = json.load(response)
                        if result["status"] != "running":
                            break
                        time.sleep(0.02)
                    self.assertEqual(result["status"], "needs_check")
                    with Store(db, {"threads": "@creator"}) as store:
                        self.assertEqual(next(d for d in store.release(release_id)["destinations"]
                                              if d["platform"] == "threads")["status"], "unconfirmed")
                    with self.assertRaises(HTTPError) as replay:
                        urlopen(post(), timeout=5)
                    self.assertEqual(replay.exception.code, 400)
                    replay.exception.close()
            finally:
                finish.set()
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
