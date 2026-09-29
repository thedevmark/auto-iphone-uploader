import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_drop import server
from video_drop.core import Store


class InstagramVerifyRouteTests(unittest.TestCase):
    def test_phone_check_reports_scheduled_only_after_store_transition(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "accounts.json").write_text('{"instagram":"@creator"}', encoding="utf-8")
            source = state / "clip.mp4"
            source.write_bytes(b"finished clip")
            db = state / "video-drop.sqlite"
            with Store(db, {"instagram": "@creator"}) as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "instagram", "@creator", "Title", "Caption #reels", "")
                store.authorize(release_id, "instagram")
                store.reserve_slot(release_id, datetime(2026, 9, 29, 8, tzinfo=timezone.utc))
                store.mark_unconfirmed(release_id, "instagram")

            started, finish = threading.Event(), threading.Event()

            def fake_run(item_id, db_path):
                self.assertEqual((item_id, db_path), (release_id, db))
                started.set()
                self.assertTrue(finish.wait(5))
                with Store(db_path) as store:
                    with store.db:
                        store.db.execute("UPDATE destination SET status='scheduled' WHERE release_id=? AND platform='instagram'",
                                         (item_id,))
                        store.db.execute("UPDATE release SET status='scheduled' WHERE id=?", (item_id,))
                return {"kind": "scheduled"}

            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                        patch.object(server, "PHONE_ACTION_RUNNING", False), \
                        patch("scripts.phone_instagram_receipt.run", side_effect=fake_run):
                    url = f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/instagram-verify"
                    post = lambda: Request(url, data=b"{}", method="POST")
                    with urlopen(post(), timeout=5) as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(json.load(response)["status"], "running")
                    self.assertTrue(started.wait(5))
                    with self.assertRaises(HTTPError) as duplicate:
                        urlopen(post(), timeout=5)
                    self.assertEqual(duplicate.exception.code, 400)
                    duplicate.exception.close()
                    finish.set()
                    for _ in range(100):
                        with urlopen(f"http://127.0.0.1:{http.server_port}/api/phone-action", timeout=5) as response:
                            result = json.load(response)
                        if result["status"] != "running":
                            break
                        time.sleep(0.02)
                    self.assertEqual(result["status"], "scheduled")
                    with self.assertRaises(HTTPError) as repeated:
                        urlopen(post(), timeout=5)
                    self.assertEqual(repeated.exception.code, 400)
                    repeated.exception.close()
            finally:
                finish.set()
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
