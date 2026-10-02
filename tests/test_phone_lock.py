import json
import os
import tempfile
import unittest
from pathlib import Path

from video_drop import phone_lock


class PhoneLockTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state = Path(self.folder.name)
        self.lock = self.state / phone_lock.LOCK_FILE

    def test_a_free_phone_is_held_and_released(self):
        self.assertIsNone(phone_lock.holder(self.state))
        with phone_lock.hold(self.state, "TikTok"):
            data = json.loads(self.lock.read_text(encoding="utf-8"))
            self.assertEqual((data["owner"], data["pid"]), ("TikTok", os.getpid()))
            self.assertIsNone(phone_lock.holder(self.state))  # this process may keep working
        self.assertFalse(self.lock.exists())

    def test_another_live_process_refuses_before_any_phone_work(self):
        self.lock.write_text(json.dumps({"owner": "YouTube", "pid": 999999}), encoding="utf-8")
        ran = []
        with self.assertRaisesRegex(phone_lock.PhoneLockHeld, "in use by YouTube; nothing was sent"):
            with phone_lock.hold(self.state, "TikTok", alive=lambda pid: True):
                ran.append(1)
        self.assertEqual(ran, [])
        self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["owner"], "YouTube")

    def test_a_crashed_holder_does_not_block_the_phone_for_good(self):
        self.lock.write_text(json.dumps({"owner": "YouTube", "pid": 999999}), encoding="utf-8")
        self.assertIsNone(phone_lock.holder(self.state, alive=lambda pid: False))
        with phone_lock.hold(self.state, "TikTok", alive=lambda pid: False):
            self.assertEqual(json.loads(self.lock.read_text(encoding="utf-8"))["owner"], "TikTok")
        self.assertFalse(self.lock.exists())

    def test_a_hand_written_lock_is_always_honoured(self):
        self.lock.write_text("lead: day7 real post on stream", encoding="utf-8")
        self.assertEqual(phone_lock.holder(self.state, alive=lambda pid: False), "lead: day7 real post on stream")
        with self.assertRaises(phone_lock.PhoneLockHeld):
            with phone_lock.hold(self.state, "receipt check"):
                pass
        self.assertTrue(self.lock.exists())

    def test_nested_holds_in_one_process_keep_the_outer_lock(self):
        with phone_lock.hold(self.state, "Post now"):
            with phone_lock.hold(self.state, "receipt check"):
                pass
            self.assertTrue(self.lock.exists())
        self.assertFalse(self.lock.exists())

    def test_flows_hold_the_lock_next_to_their_database(self):
        seen = []

        @phone_lock.locked("TikTok")
        def run(release_id, db, *, commit=False):
            seen.append(json.loads((Path(db).parent / phone_lock.LOCK_FILE).read_text(encoding="utf-8"))["owner"])
            return {"kind": "ready"}

        self.assertEqual(run(1, self.state / "video-drop.sqlite"), {"kind": "ready"})
        self.assertEqual(run(1, db=self.state / "video-drop.sqlite", commit=True), {"kind": "ready"})
        self.assertEqual(seen, ["TikTok", "TikTok"])
        self.assertFalse(self.lock.exists())


class ServerLockTests(unittest.TestCase):
    def test_a_slot_post_is_not_claimed_while_another_process_holds_the_phone(self):
        from unittest.mock import patch
        from video_drop import server
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / phone_lock.LOCK_FILE).write_text(json.dumps({"owner": "TikTok", "pid": 999999}),
                                                      encoding="utf-8")
            with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                    patch.object(server, "PHONE_ACTION_RUNNING", False), \
                    patch.object(server.phone_lock, "holder", lambda s: "TikTok"):
                with self.assertRaisesRegex(server.PhoneBusy, "in use by TikTok"):
                    server.queue_phone_post(1, "youtube")
                with self.assertRaisesRegex(server.PhoneBusy, "in use by TikTok"):
                    server.queue_release_run(1, "post_now")


if __name__ == "__main__":
    unittest.main()


class FailureCaptureTests(unittest.TestCase):
    def test_a_failing_flow_leaves_its_screen_and_error_behind(self):
        from unittest.mock import patch
        from video_drop import failure_capture
        from video_drop.phone import capture as phone_capture
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)

            @phone_lock.locked("TikTok")
            def run(release_id, db):
                raise RuntimeError("Expected one Post button")

            with patch.object(phone_capture, "pixels_png", lambda: b"PNGDATA"), \
                    patch("video_drop.ocr.read_png", lambda png: []):
                with self.assertRaisesRegex(RuntimeError, "Expected one Post button"):
                    run(1, state / "video-drop.sqlite")
            [saved] = list((state / "failures").iterdir())
            self.assertEqual((saved / "screen.png").read_bytes(), b"PNGDATA")
            self.assertIn("Expected one Post button", (saved / "error.txt").read_text(encoding="utf-8"))
            self.assertFalse((state / phone_lock.LOCK_FILE).exists())

    def test_capture_never_hides_the_flow_error(self):
        from unittest.mock import patch
        from video_drop.phone import capture as phone_capture
        with tempfile.TemporaryDirectory() as folder:
            @phone_lock.locked("YouTube")
            def run(release_id, db):
                raise ValueError("real error")

            def broken():
                raise OSError("no screenshot")

            with patch.object(phone_capture, "pixels_png", broken):
                with self.assertRaisesRegex(ValueError, "real error"):
                    run(1, Path(folder) / "video-drop.sqlite")
