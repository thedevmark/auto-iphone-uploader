import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from video_drop import server
from video_drop.core import Store


class BatchPlanRouteTests(unittest.TestCase):
    def test_four_reviewed_clips_take_successive_slots_without_claiming_native_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with Store(state / "video-drop.sqlite") as store:
                ids = []
                for index in range(4):
                    source = state / f"clip-{index}.mp4"
                    source.write_bytes(f"finished clip {index}".encode())
                    release_id = store.import_file(source)["id"]
                    store.save_text(release_id, "youtube", "@channel", f"Title {index}", "Description", "tag")
                    store.authorize(release_id, "youtube")
                    ids.append(release_id)

            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
                with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                        patch("video_drop.core.utc_now", return_value=now):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/queue/plan",
                                      data=json.dumps({"releaseIds": list(reversed(ids))}).encode(),
                                      headers={"Content-Type": "application/json"}, method="POST")
                    with urlopen(request, timeout=5) as response:
                        result = json.load(response)
                self.assertFalse(result["nativeScheduled"])
                self.assertEqual([release["id"] for release in result["releases"]], ids)
                new_york = ZoneInfo("America/New_York")
                self.assertEqual([
                    datetime.fromisoformat(release["scheduled_at"]).astimezone(new_york).strftime("%Y-%m-%d %H:%M")
                    for release in result["releases"]
                ], ["2026-09-28 10:00", "2026-09-28 19:00",
                    "2026-09-29 10:00", "2026-09-29 19:00"])
                self.assertTrue(all(release["status"] == "reserved" for release in result["releases"]))
                self.assertTrue(all(destination["status"] == "pending"
                                    for release in result["releases"] for destination in release["destinations"]))
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)

    def test_unreviewed_clip_rolls_back_all_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with Store(state / "video-drop.sqlite") as store:
                ids = []
                for index in range(2):
                    source = state / f"clip-{index}.mp4"
                    source.write_bytes(f"finished clip {index}".encode())
                    release_id = store.import_file(source)["id"]
                    if index == 0:
                        store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
                        store.authorize(release_id, "youtube")
                    ids.append(release_id)
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/queue/plan",
                                      data=json.dumps({"releaseIds": ids}).encode(), method="POST")
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(request, timeout=5)
                    self.assertEqual(rejected.exception.code, 400)
                    rejected.exception.close()
                with Store(state / "video-drop.sqlite") as store:
                    self.assertEqual([store.release(item)["scheduled_at"] for item in ids], [None, None])
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
