"""Every flow gets through a playing screen from pixels and OCR, never an accessibility request.

The 2026-10-01 17:22 soak lost the phone off USB inside the YouTube flow: the guard covered
TikTok only, and YouTube's trim and editor screens play the clip. These tests drive the flows
with a phone whose accessibility reads raise VideoSurfaceError (what the driver now does on
any playing video app) and check what they read and tap instead.
"""

import io
import json
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from scripts import phone_instagram_preflight as preflight
from scripts import phone_youtube
from video_drop import ocr, youtube_nav
from video_drop.phone.helpers import VideoSurfaceError
from video_drop.phone_ui import PhoneLayout

LAYOUT = PhoneLayout(440, 956)


def png(width=1320, height=2868):
    out = io.BytesIO()
    Image.new("RGB", (width, height)).save(out, format="PNG")
    return out.getvalue()


def ocr_lines(*items):
    """OCR output for (text, center_x_pt, center_y_pt) on a 3x screenshot."""
    return ocr.parse("\n".join(json.dumps({"text": text, "x": round(x * 3) - 30, "y": round(y * 3) - 15,
                                           "w": 60, "h": 30}) for text, x, y in items))


class PlayingPhone:
    """A video app in front, playing: every accessibility request is refused."""

    def __init__(self):
        self.taps = []
        self.noted = []
        self.events = []

    def ocr(self):
        raise VideoSurfaceError("com.google.ios.youtube is playing video")

    ui_tree = ocr
    current_app = ocr

    def compact(self, rows):
        return rows

    def note_front_app(self, bundle):
        self.noted.append(bundle)
        self.events.append(("note", bundle))

    def tap(self, x, y):
        self.taps.append((x, y))
        self.events.append(("tap", (x, y)))


@patch("scripts.phone_youtube.time.sleep", lambda seconds: None)
class YouTubePlayingScreens(unittest.TestCase):
    def setUp(self):
        self.phone = PlayingPhone()
        for p in (patch.object(phone_youtube, "phone", self.phone),
                  patch.object(phone_youtube, "layout", lambda **kw: LAYOUT),
                  patch.object(phone_youtube, "screen_pixels", lambda: png())):
            p.start()
            self.addCleanup(p.stop)

    def test_a_playing_screen_is_read_by_ocr_in_points(self):
        with patch.object(ocr, "read_png", lambda data: ocr_lines(("Crop your video", 220, 848), ("Next", 326, 894))):
            rows = phone_youtube.screen()
        self.assertEqual([(r["text"], r["x"], r["y"], r["type"]) for r in rows],
                         [("Crop your video", 220.0, 848.0, "OcrText"), ("Next", 326.0, 894.0, "OcrText")])

    def test_ocr_that_cannot_run_stops_the_run_instead_of_asking_wda(self):
        def broken(data):
            raise ocr.OcrError("Screen OCR needs Windows' built-in OCR engine")
        with patch.object(ocr, "read_png", broken):
            with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "only be read by OCR"):
                phone_youtube.screen()

    def test_trim_processing_and_editor_are_told_apart_from_ocr_words(self):
        rows = lambda *texts: [{"text": t} for t in texts]  # noqa: E731
        self.assertEqual(phone_youtube.trim_state(rows("Crop your video", "Next")), "trim")
        self.assertEqual(phone_youtube.trim_state(rows("Processing 45%", "Crop your video", "Next")), "processing")
        self.assertEqual(phone_youtube.trim_state(rows("Swipe  up to edit", "Edit", "Next")), "editor")
        self.assertIsNone(phone_youtube.trim_state(rows("Crop your video")))

    def test_trim_next_is_tapped_from_ocr_and_the_editor_is_proven_by_ocr(self):
        screens = iter([
            ocr_lines(("Crop your video", 220, 848), ("Next", 326, 894)),
            ocr_lines(("Processing", 220, 480)),
            ocr_lines(("Swipe up to edit", 220, 848), ("Edit", 114, 894), ("Next", 326, 894)),
        ])
        with patch.object(ocr, "read_png", lambda data: next(screens)):
            phone_youtube.advance_trim_to_editor()
        self.assertEqual(self.phone.taps, [(326.0, 894.0)])

    def test_the_clips_own_word_next_is_never_tapped(self):
        rows = [{"text": "Next", "x": 220.0, "y": 400.0}, {"text": "Next", "x": 326.0, "y": 894.0}]
        self.assertEqual(phone_youtube.bottom_next(rows)["y"], 894.0)
        with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "found 2"):
            phone_youtube.bottom_next(rows + [{"text": "next", "x": 100.0, "y": 900.0}])

    def test_share_to_youtube_arms_the_guard_before_the_tap_and_never_asks_activeappinfo(self):
        rows = [{"text": "shareSheet.activity.contentView", "x": 220, "y": 500}]
        with patch.object(phone_youtube, "screen", lambda: rows), \
                patch.object(phone_youtube, "share_app_position", lambda *a: ("tap", {"x": 100, "y": 392})), \
                patch.object(phone_youtube, "share_rail_y", lambda *a: 392):
            phone_youtube.choose_share_app("YouTube", expected_bundle="com.google.ios.youtube")
        self.assertEqual(self.phone.events, [("note", "com.google.ios.youtube"), ("tap", (100, 392))])

    def test_a_still_share_destination_is_still_confirmed_by_bundle(self):
        phone = MagicMock()
        phone.current_app.side_effect = [{"bundleId": "com.microsoft.skydrive"}, {"bundleId": "com.burbn.barcelona"}]
        with patch.object(phone_youtube, "phone", phone):
            phone_youtube.share_landed("Threads", "com.burbn.barcelona")
        phone.current_app.return_value = {"bundleId": "com.microsoft.skydrive"}
        phone.current_app.side_effect = None
        clock = iter(range(0, 100, 3))
        with patch.object(phone_youtube, "phone", phone), \
                patch("scripts.phone_youtube.time.monotonic", lambda: next(clock)):
            with self.assertRaisesRegex(phone_youtube.PhoneUploadError, "did not open Threads"):
                phone_youtube.share_landed("Threads", "com.burbn.barcelona")

    def test_you_tab_is_the_one_on_the_tab_bar(self):
        rows = [{"text": "You", "x": 200.0, "y": 300.0}, {"text": "You", "x": 396.0, "y": 905.0}]
        with patch.object(phone_youtube, "screen", lambda: rows):
            phone_youtube.tap_you_tab()
        self.assertEqual(self.phone.taps, [(396.0, 905.0)])

    def test_still_tree_waits_out_a_preview_then_reads(self):
        phone = MagicMock()
        phone.ui_tree.side_effect = [VideoSurfaceError("playing"), {"type": "XCUIElementTypeApplication"}]
        self.assertEqual(phone_youtube.still_tree(driver=phone), {"type": "XCUIElementTypeApplication"})
        phone.ui_tree.side_effect = VideoSurfaceError("playing")
        clock = iter(range(0, 100, 5))
        with patch("scripts.phone_youtube.time.monotonic", lambda: next(clock)):
            with self.assertRaises(VideoSurfaceError):
                phone_youtube.still_tree(driver=phone)


class YouTubeHomeFeed(unittest.TestCase):
    def test_a_playing_home_feed_is_read_by_ocr(self):
        phone = PlayingPhone()
        phone.screen_info = lambda: {"width": 440, "height": 956}
        lines = ocr_lines(("Home", 44, 905), ("Shorts", 132, 905), ("You", 396, 905))
        with patch.object(ocr, "read_png", lambda data: lines), \
                patch("video_drop.phone.capture.pixels_png", lambda: png()):
            labels = [row["text"] for row in youtube_nav.visible_rows(phone)]
        self.assertEqual(labels, ["Home", "Shorts", "You"])

    def test_open_tabs_accepts_a_launch_onto_a_playing_feed(self):
        phone = MagicMock()
        phone.open_app.side_effect = VideoSurfaceError("playing")
        with patch("video_drop.youtube_nav.visible_rows", return_value=[{"text": "Home"}, {"text": "You"}]):
            youtube_nav.open_tabs(phone)
        phone.current_app.assert_not_called()
        phone.tap.assert_not_called()


@patch("scripts.phone_instagram_preflight.time.sleep", lambda seconds: None)
class InstagramOpensOnAPlayingScreen(unittest.TestCase):
    PROFILE = {"type": "XCUIElementTypeApplication", "children": [
        {"type": "XCUIElementTypeButton", "name": "user-switch-title-button", "label": "examplechannel",
         "value": "", "rect": {"x": 100, "y": 50, "width": 40, "height": 40}, "isVisible": "1"}]}

    def phone(self, *, story=False):
        phone = MagicMock()
        phone.screen_info.return_value = {"width": 440, "height": 956}
        phone.open_app.side_effect = VideoSurfaceError("playing")
        state = {"left": False}

        def tree():
            if not state["left"]:
                raise VideoSurfaceError("com.burbn.instagram is playing video")
            return self.PROFILE
        phone.ui_tree.side_effect = tree
        phone.tap.side_effect = lambda *a: state.update(left=True)
        phone.swipe.side_effect = lambda *a: state.update(left=True)
        lines = ocr_lines(("Send message", 160, 900)) if story else ocr_lines(("Liked by creator", 160, 700))
        return phone, lines

    def test_a_playing_feed_is_left_by_the_profile_tab_tapped_blind(self):
        phone, lines = self.phone()
        with patch.object(preflight, "phone", phone), patch.object(ocr, "read_png", lambda data: lines), \
                patch("video_drop.phone.capture.pixels_png", lambda: png()):
            self.assertEqual(preflight.selected_instagram_account(), "@examplechannel")
        phone.current_app.assert_not_called()
        phone.swipe.assert_not_called()
        self.assertEqual(phone.tap.call_args_list[0].args, LAYOUT.bottom_sheet_point(*preflight.PROFILE_TAB))

    def test_a_playing_story_is_swiped_closed_never_tapped(self):
        phone, lines = self.phone(story=True)
        with patch.object(preflight, "phone", phone), patch.object(ocr, "read_png", lambda data: lines), \
                patch("video_drop.phone.capture.pixels_png", lambda: png()):
            self.assertEqual(preflight.selected_instagram_account(), "@examplechannel")
        phone.swipe.assert_called_once()
        phone.tap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
