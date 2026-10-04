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


def threads_release(state: Path, *, mode: str = "post_now", checks: dict | None = None) -> int:
    """Instagram, Facebook and Threads approved; YouTube and TikTok not targeted."""
    (state / "accounts.json").write_text('{"threads":"@creator","instagram":"@creator"}', encoding="utf-8")
    source = state / "clip.mp4"
    source.write_bytes(b"finished video")
    with Store(state / "video-drop.sqlite", {"threads": "@creator"}) as store:
        if checks:
            store.set_phone_checks(checks)
        release_id = store.import_file(source)["id"]
        store.save_text(release_id, "instagram", "@creator", "", "Approved caption", "")
        for platform in ("instagram", "facebook", "threads"):
            if platform != "instagram":
                store.save_text(release_id, platform, "@creator", "", "", "")
            store.authorize(release_id, platform)
        store.set_delivery_mode(release_id, mode)
    return release_id


def threads_status(state: Path, release_id: int) -> str:
    with Store(state / "video-drop.sqlite") as store:
        return next(d for d in store.release(release_id)["destinations"] if d["platform"] == "threads")["status"]


class ThreadsPostRouteTests(unittest.TestCase):
    def setUp(self):
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def post(self, release_id: int) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.http.server_port}/api/releases/{release_id}/threads-post"
        try:
            with urlopen(Request(url, data=b"{}", method="POST"), timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def refused(self, **kwargs) -> str:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = threads_release(state, **kwargs)
            with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                    patch.object(server, "phone_free_bytes", return_value=10 ** 12), \
                    patch("scripts.phone_threads.run", side_effect=AssertionError("must not touch the phone")) as run:
                code, body = self.post(release_id)
            run.assert_not_called()
            self.assertEqual(code, 400)
            self.assertEqual(threads_status(state, release_id), "pending")
            return body["error"]

    def test_post_now_threads_is_instagrams_crosspost_not_a_separate_post(self):
        self.assertIn("Also share on", self.refused())

    def test_scheduled_threads_is_never_posted_immediately(self):
        self.assertIn("not built yet", self.refused(mode="schedule"))

    def test_crosspost_off_needs_the_separate_post_option(self):
        self.assertIn("“Post Threads separately” is off", self.refused(checks={"crosspostThreads": False}))

    def test_separate_post_option_runs_one_threads_post_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = threads_release(state, checks={"crosspostThreads": False, "threadsSeparatePost": True})
            with Store(state / "video-drop.sqlite") as store:  # Instagram (with Facebook) went first
                store.mark_unconfirmed(release_id, "instagram")
            started, finish = threading.Event(), threading.Event()

            def fake_run(item_id, db_path, *, commit):
                self.assertEqual((item_id, db_path, commit), (release_id, state / "video-drop.sqlite", True))
                started.set()
                self.assertTrue(finish.wait(5))
                with Store(db_path) as store:
                    store.mark_unconfirmed(item_id, "threads")
                return {"message": "Final tap sent; check native Threads receipt before any retry"}

            try:
                with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                        patch.object(server, "phone_free_bytes", return_value=10 ** 12), \
                        patch("scripts.phone_threads.run", side_effect=fake_run):
                    code, body = self.post(release_id)
                    self.assertEqual((code, body["status"]), (202, "running"))
                    self.assertTrue(started.wait(5))
                    code, body = self.post(release_id)
                    self.assertEqual(code, 400)
                    self.assertIn("already running", body["error"])
                    finish.set()
                    deadline = time.monotonic() + 10
                    while True:
                        result = server.phone_action_status()
                        if result["status"] != "running" or time.monotonic() > deadline:
                            break
                        time.sleep(0.02)
                    self.assertEqual(result["status"], "needs_check")
                    self.assertEqual(threads_status(state, release_id), "unconfirmed")
                    code, _ = self.post(release_id)
                    self.assertEqual(code, 400)
            finally:
                finish.set()


if __name__ == "__main__":
    unittest.main()
