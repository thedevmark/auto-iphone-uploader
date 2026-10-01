import tempfile
import unittest
from pathlib import Path
from unittest import mock

from video_drop import link_supervisor
from video_drop.phone_link import recover, wait_ready


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


class PhoneLinkRecoveryTests(unittest.TestCase):
    def run_recover(self, answers, *, releases=None, supervised=False, state=None, started=None):
        clock = Clock()
        answers = iter(answers)
        released = releases if releases is not None else []
        started = started if started is not None else []
        ok = recover(status=lambda: next(answers, False), release=lambda: released.append(clock.now),
                     sleep=clock.sleep, clock=clock.time, wait=10, supervised=supervised,
                     supervisor_state=state, start=lambda s: started.append(s))
        return ok, released, started

    def test_wait_ready_follows_the_supervisor_verdict(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            verdicts = iter(["wedged", "wedged", "ready"])
            with mock.patch.object(link_supervisor, "read_status",
                                   lambda s=None: {"state": next(verdicts, "ready"), "alive": True}):
                result = wait_ready(30, status=lambda: True, sleep=clock.sleep, clock=clock.time, state=state)
            self.assertEqual(result["state"], "ready")
            self.assertEqual(clock.now, 6.0)
            with mock.patch.object(link_supervisor, "read_status", lambda s=None: {"state": "needs-replug", "alive": True}):
                result = wait_ready(10, status=lambda: True, sleep=clock.sleep, clock=clock.time, state=state)
            self.assertEqual(result["state"], "needs-replug")

    def test_supervisor_is_asked_instead_of_restarting_from_the_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, released, started = self.run_recover([False] * 6 + [True], supervised=True, state=Path(tmp))
            self.assertTrue(ok)
            self.assertEqual((started, len(released)), ([], 1))
            self.assertTrue((Path(tmp) / "link-request").is_file())

    def test_answering_wda_is_left_alone(self):
        ok, released, started = self.run_recover([True])
        self.assertTrue(ok)
        self.assertEqual((started, released), ([], []))

    def test_frozen_app_is_released_without_restarting_wda(self):
        ok, released, started = self.run_recover([False, False, True])
        self.assertTrue(ok)
        self.assertEqual(len(released), 1)
        self.assertEqual(started, [])

    def test_unsupervised_link_starts_the_supervisor_once_and_only_after_waiting(self):
        # Nothing supervising the link is no license to restart WDA from the
        # script: the last resort is to start the one owner and ask it.
        with tempfile.TemporaryDirectory() as tmp:
            ok, released, started = self.run_recover([False] * 6 + [True], state=Path(tmp))
            self.assertTrue(ok)
            self.assertEqual((started, len(released)), ([Path(tmp)], 1))
            self.assertTrue((Path(tmp) / "link-request").is_file())

    def test_a_link_that_never_answers_reports_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, _released, started = self.run_recover([False] * 50, state=Path(tmp))
            self.assertFalse(ok)
            self.assertEqual(started, [Path(tmp)])

    def test_scripts_may_still_pass_the_old_admin_argument(self):
        class Admin:
            calls = 0

            def up(self):
                Admin.calls += 1
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            ok = recover(Admin(), status=lambda: False, sleep=lambda s: None, clock=iter(range(0, 10_000, 5)).__next__,
                         wait=10, supervisor_state=Path(tmp), supervised=True)
        self.assertFalse(ok)
        self.assertEqual(Admin.calls, 0, "SideTap's up() must never run from a script")


if __name__ == "__main__":
    unittest.main()


class VideoSurfaceTests(unittest.TestCase):
    """Two go-ios frames: a playing video moves a large share of the main region; a static screen does not."""

    @staticmethod
    def frame(seed: int, *, noise: int = 0, moving: bool = False, size=(120, 260)) -> bytes:
        from io import BytesIO
        from PIL import Image, ImageDraw

        image = Image.new("RGB", size, (30, 30, 30))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, size[0], 20), fill=(200, 200, 200))  # status bar (always changing clock)
        draw.text((4, 4), f"{seed:02d}:00", fill=(0, 0, 0))
        if moving:
            offset = (seed * 37) % 80
            for i in range(0, size[1], 10):
                shade = (i * 3 + seed * 50) % 255
                draw.rectangle((0, i + offset, size[0], i + offset + 5), fill=(shade, 255 - shade, shade // 2))
        else:
            draw.rectangle((10, 60, 110, 200), fill=(90, 120, 200))
            draw.text((20, 220), "Post", fill=(255, 255, 255))
            if noise:
                draw.rectangle((50, 240, 50 + noise, 250), fill=(255, 0, 0))  # a spinner-sized change
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    def test_static_screen_measures_near_zero_and_video_measures_high(self):
        from video_drop.phone_link import changed_fraction

        self.assertEqual(changed_fraction(self.frame(1), self.frame(2)), 0.0)  # the clock sits outside the region
        self.assertLess(changed_fraction(self.frame(1), self.frame(2, noise=6)), 0.02)
        self.assertGreater(changed_fraction(self.frame(1, moving=True), self.frame(2, moving=True)), 0.3)
        self.assertEqual(changed_fraction(self.frame(1), self.frame(1, size=(260, 120))), 1.0)  # rotated mid-check

    def test_video_in_front_takes_two_frames_a_gap_apart_and_never_touches_wda(self):
        from video_drop.phone_link import VIDEO_CHANGE_FRACTION, VideoCheck, video_in_front

        clock = Clock()
        frames = iter([self.frame(1, moving=True), self.frame(2, moving=True)])
        grabs = []

        def grab():
            grabs.append(clock.now)
            clock.sleep(0.12)  # go-ios takes ~100-300 ms per frame
            return next(frames)

        check = video_in_front(None, grab=grab, sleep=clock.sleep, clock=clock.time)
        self.assertIsInstance(check, VideoCheck)
        self.assertTrue(check.video)
        self.assertGreaterEqual(grabs[1] - grabs[0], 0.3)
        self.assertGreaterEqual(check.gap_ms, 300)
        self.assertEqual(check.size, (120, 260))
        self.assertEqual(check.threshold, VIDEO_CHANGE_FRACTION)
        still = video_in_front(None, grab=lambda: self.frame(3), sleep=clock.sleep, clock=clock.time)
        self.assertFalse(still.video)
        self.assertEqual(still.changed, 0.0)

    def test_thresholds_are_documented_as_still_to_measure(self):
        from video_drop import phone_link

        source = Path(phone_link.__file__).read_text(encoding="utf-8")
        self.assertIn("TODO-to-measure", source)
        for name in ("VIDEO_CHANGE_FRACTION", "VIDEO_PIXEL_DELTA", "VIDEO_REGION", "VIDEO_GAP_SECONDS"):
            self.assertTrue(hasattr(phone_link, name), name)
        self.assertTrue(0 < phone_link.VIDEO_CHANGE_FRACTION < 1)
