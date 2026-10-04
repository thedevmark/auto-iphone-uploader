import unittest
from unittest.mock import patch

from video_drop.phone import capture


class PixelsFallbackTests(unittest.TestCase):
    def setUp(self):
        capture._go_ios_dead_until = 0.0
        self.addCleanup(setattr, capture, "_go_ios_dead_until", 0.0)

    def test_go_ios_first(self):
        with patch.object(capture, "_go_ios_screenshot", lambda: b"go-ios"), \
                patch.object(capture, "_wda_screenshot", lambda: b"wda"):
            self.assertEqual(capture.pixels_png(), b"go-ios")

    def test_a_wedged_go_ios_service_falls_back_to_wda_and_rests_for_a_minute(self):
        # go-ios's screenshot service times out on every call while
        # WDA's /screenshot answers.
        calls = []

        def wedged():
            calls.append(1)
            raise capture.CaptureError("TakeScreenshot: Timed out waiting for response")

        now = [1000.0]
        with patch.object(capture, "_go_ios_screenshot", wedged), \
                patch.object(capture, "_wda_screenshot", lambda: b"wda"):
            self.assertEqual(capture.pixels_png(clock=lambda: now[0]), b"wda")
            now[0] += 30
            self.assertEqual(capture.pixels_png(clock=lambda: now[0]), b"wda")
            self.assertEqual(len(calls), 1)  # no second 5 s timeout inside the rest period
            now[0] += 31
            capture.pixels_png(clock=lambda: now[0])
            self.assertEqual(len(calls), 2)

    def test_both_routes_down_is_an_error_not_a_blank_frame(self):
        def wedged():
            raise capture.CaptureError("down")
        with patch.object(capture, "_go_ios_screenshot", wedged), patch.object(capture, "_wda_screenshot", lambda: None):
            with self.assertRaisesRegex(capture.CaptureError, "both failed"):
                capture.pixels_png()


if __name__ == "__main__":
    unittest.main()
