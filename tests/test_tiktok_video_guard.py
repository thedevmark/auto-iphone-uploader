"""TikTok's editor plays the clip full-screen: the flow reads pixels there, never the tree.

Fakes only; no phone. The post screen below is the recorded 440 x 956 one
(.state/fixtures/tiktok/post-screen-*), as in test_phone_tiktok.py.
"""

import unittest
from unittest import mock

from scripts import phone_tiktok as tiktok
from scripts import phone_youtube as share
from video_drop.phone.helpers import VideoSurfaceError
from video_drop.screens.snapshot import Element, Snapshot


def element(kind, label, left, top, width, height, value=""):
    return Element(kind, label, label, value, left, top, width, height)


POST_SCREEN = Snapshot(440, 956, (
    element("TextView", "Add description...", 16, 120, 280, 143),
    element("StaticText", "Preview", 322, 122, 49, 17),
    element("Button", "Edit cover", 318, 246, 100, 26),
    element("Button", "Post", 223, 870, 205, 48),
), app=tiktok.TIKTOK_BUNDLE)
OTHER_SCREEN = Snapshot(440, 956, (element("Button", "Back", 16, 60, 44, 44),), app=tiktok.TIKTOK_BUNDLE)


class Clock:
    """time.monotonic/time.sleep pair: sleeping advances the clock, nothing waits."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakePhone:
    def __init__(self, playing):
        self.playing = list(playing)
        self.fronts = []

    def video_in_front(self):
        return self.playing.pop(0) if self.playing else False

    def note_front_app(self, bundle):
        self.fronts.append(bundle)


class WaitForPostScreenTests(unittest.TestCase):
    def run_wait(self, phone, reads, timeout=30):
        clock = Clock()
        live = mock.Mock(side_effect=reads)
        with mock.patch.object(tiktok, "phone", phone), mock.patch.object(tiktok, "live", live), \
                mock.patch.object(tiktok.time, "monotonic", clock.monotonic), \
                mock.patch.object(tiktok.time, "sleep", clock.sleep):
            return tiktok.wait_for_post_screen(timeout), live

    def test_no_tree_read_while_the_editor_plays(self):
        phone = FakePhone([True, True, True, False])
        snapshot, live = self.run_wait(phone, [POST_SCREEN])
        self.assertIs(snapshot, POST_SCREEN)
        self.assertEqual(live.call_count, 1)  # only after the frames stopped moving

    def test_a_still_post_screen_is_read_at_once(self):
        snapshot, live = self.run_wait(FakePhone([False]), [POST_SCREEN])
        self.assertIs(snapshot, POST_SCREEN)
        self.assertEqual(live.call_count, 1)

    def test_editor_that_never_stops_fails_closed_without_reading(self):
        live = mock.Mock()
        clock = Clock()
        with mock.patch.object(tiktok, "phone", FakePhone([True] * 100)), mock.patch.object(tiktok, "live", live), \
                mock.patch.object(tiktok.time, "monotonic", clock.monotonic), \
                mock.patch.object(tiktok.time, "sleep", clock.sleep):
            with self.assertRaisesRegex(share.PhoneUploadError, "still playing after Next; nothing was posted"):
                tiktok.wait_for_post_screen(5)
        live.assert_not_called()

    def test_the_tree_guard_refusing_counts_as_still_playing_not_a_link_failure(self):
        # ui_tree()'s own two frames can catch motion ours missed; that is a wait, not a WDAError
        # that would throw away the whole preparation.
        refused = VideoSurfaceError("com.zhiliaoapp.musically is playing video")
        snapshot, live = self.run_wait(FakePhone([False, False]), [refused, POST_SCREEN])
        self.assertIs(snapshot, POST_SCREEN)
        self.assertEqual(live.call_count, 2)

    def test_refusals_until_the_deadline_say_the_editor_is_still_playing(self):
        refused = VideoSurfaceError("playing")
        with self.assertRaisesRegex(share.PhoneUploadError, "still playing after Next"):
            self.run_wait(FakePhone([False] * 100), [refused] * 100, timeout=3)

    def test_a_still_screen_that_is_not_the_post_screen_keeps_the_old_message(self):
        with self.assertRaisesRegex(share.PhoneUploadError, "post screen did not appear after Next"):
            self.run_wait(FakePhone([False] * 100), [OTHER_SCREEN] * 100, timeout=3)


class FrontAppTests(unittest.TestCase):
    """open_app() never sees TikTok (a share sheet opens it), so the flow names it for ui_tree()'s guard."""

    def test_tiktok_is_named_as_soon_as_the_share_sheet_hands_over(self):
        order = []
        phone = mock.Mock()
        phone.note_front_app.side_effect = lambda bundle: order.append(("front", bundle))
        phone.tap.side_effect = lambda *point: order.append(("tap", point))

        class Stop(Exception):
            pass

        layout = mock.Mock()
        layout.bottom_sheet_point.return_value = (1, 2)
        layout.bottom_right_point.return_value = (3, 4)
        with mock.patch.object(tiktok, "phone", phone),                 mock.patch.object(share, "layout", return_value=layout),                 mock.patch.object(share, "choose_share_app", side_effect=lambda name: order.append(("share", name))),                 mock.patch.object(share, "stage"), mock.patch.object(tiktok.time, "sleep"),                 mock.patch.object(tiktok, "wait_for_post_screen", side_effect=Stop):
            with self.assertRaises(Stop):
                tiktok.compose({"releaseId": 1}, None, None)
        self.assertEqual(order[:2], [("share", "TikTok"), ("front", tiktok.TIKTOK_BUNDLE)])
        self.assertEqual([step[0] for step in order[2:]], ["tap", "tap"])  # Video, then Next

    def test_leaving_tiktok_clears_the_guard_once_it_is_closed(self):
        phone = FakePhone([])
        with mock.patch.object(tiktok, "phone", phone), mock.patch.object(tiktok, "release_frozen_app"),                 mock.patch.object(tiktok.subprocess, "run"),                 mock.patch("video_drop.phone.device.ios_path", return_value="ios"):
            tiktok.leave_tiktok()
        self.assertEqual(phone.fronts, [None])

    def test_a_failed_leave_keeps_the_guard_on(self):
        phone = FakePhone([])
        with mock.patch.object(tiktok, "phone", phone),                 mock.patch.object(tiktok, "release_frozen_app", side_effect=OSError("tunnel down")),                 mock.patch("video_drop.phone.device.ios_path", return_value="ios"):
            tiktok.leave_tiktok()
        self.assertEqual(phone.fronts, [])


if __name__ == "__main__":
    unittest.main()
