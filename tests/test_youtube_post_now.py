import json
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts import phone_youtube
from video_drop import server
from video_drop.core import Store


def confirmed_youtube(state: Path, *, post_now: bool) -> tuple[Path, int]:
    (state / "accounts.json").write_text('{"youtube":"@creator"}', encoding="utf-8")
    source = state / "clip.mp4"
    source.write_bytes(b"finished video")
    db = state / "video-drop.sqlite"
    with Store(db, {"youtube": "@creator"}) as store:
        release_id = store.import_file(source)["id"]
        store.save_text(release_id, "youtube", "@creator", "Exact title", "Exact description #shorts", "game, clip")
        store.authorize(release_id, "youtube")
        if post_now:
            store.set_delivery_mode(release_id, "post_now")
    return db, release_id


class YouTubePostNowTests(unittest.TestCase):
    def test_scheduled_release_cannot_tap_immediate_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            db, release_id = confirmed_youtube(state, post_now=False)
            with patch.object(phone_youtube, "connect_sidetap", side_effect=AssertionError("phone opened")):
                with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "choose Post now"):
                    phone_youtube.run(str(release_id), db, commit=True)
            with Store(db, {"youtube": "@creator"}) as store:
                self.assertEqual(store.release(release_id)["status"], "draft")

    def test_final_tap_is_one_shot_and_state_is_saved_before_it(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            db, release_id = confirmed_youtube(state, post_now=True)

            class LinkLost(Exception):
                pass

            class FakePhone:
                taps = 0

                def unlock(self):
                    pass

                def tap(self, x, y):
                    self.taps += 1
                    with Store(db, {"youtube": "@creator"}) as store:
                        destination = next(d for d in store.release(release_id)["destinations"]
                                           if d["platform"] == "youtube")
                        assert destination["status"] == "unconfirmed"
                    raise LinkLost("tap timed out")

            fake = FakePhone()
            labels = [{"text": "id.elements.components.identity_chip_component"},
                      {"text": "Creator, @creator"}]
            with patch.object(phone_youtube, "connect_sidetap"), \
                    patch.object(phone_youtube, "phone", fake), \
                    patch.object(phone_youtube, "WDAError", LinkLost), \
                    patch.object(phone_youtube, "upload_focus", return_value=nullcontext()), \
                    patch.object(phone_youtube, "layout"), \
                    patch.object(phone_youtube, "ensure_youtube_channel"), \
                    patch.object(phone_youtube, "open_onedrive_file"), \
                    patch.object(phone_youtube, "prepare_youtube"), \
                    patch.object(phone_youtube, "screen", return_value=labels), \
                    patch.object(phone_youtube, "matches", return_value=[{"x": 1, "y": 2}]):
                with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "final tap is uncertain"):
                    phone_youtube.run(str(release_id), db, commit=True)
            self.assertEqual(fake.taps, 1)
            with Store(db, {"youtube": "@creator"}) as store:
                self.assertEqual(store.release(release_id)["status"], "needs_check")

    def test_route_records_one_uncertain_final_tap_and_blocks_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            db, release_id = confirmed_youtube(state, post_now=True)
            started, finish = threading.Event(), threading.Event()

            def fake_run(item_id, db_path, *, commit):
                self.assertEqual((item_id, db_path, commit), (str(release_id), db, True))
                started.set()
                self.assertTrue(finish.wait(5))
                with Store(db_path, {"youtube": "@creator"}) as store:
                    store.mark_unconfirmed(release_id, "youtube")
                return {"message": "Final tap sent; check the native YouTube receipt"}

            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                        patch("scripts.phone_youtube.run", side_effect=fake_run):
                    url = f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/youtube-post"
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
                    for _ in range(50):
                        with urlopen(f"http://127.0.0.1:{http.server_port}/api/phone-action", timeout=5) as response:
                            result = json.load(response)
                        if result["status"] != "running":
                            break
                        time.sleep(0.02)
                    self.assertEqual(result["status"], "needs_check")
                    with Store(db, {"youtube": "@creator"}) as store:
                        youtube = next(d for d in store.release(release_id)["destinations"]
                                       if d["platform"] == "youtube")
                        self.assertEqual(youtube["status"], "unconfirmed")
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
