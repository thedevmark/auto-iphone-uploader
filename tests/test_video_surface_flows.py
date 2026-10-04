"""Every flow gets through a playing screen from pixels and OCR, never an accessibility request.

YouTube's trim and editor screens play the clip, so a guard covering TikTok only lets the
phone fall off USB inside the YouTube flow. These tests drive the flows
with a phone whose accessibility reads raise VideoSurfaceError (what the driver does on
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

    def test_an_app_scrolled_past_the_rail_start_is_found_by_reversing(self):
        # The rail can sit at its end with TikTok clipped off the left edge,
        # unlisted, and scrolling only toward the end never finds it.
        apps = ["TikTok", "Instagram", "X", "YouTube", "Snapchat", "More"]
        rail = {"offset": 200}

        def screen():
            rows = [{"text": "shareSheet.activity.contentView", "x": 220, "y": 500}]
            for i, app in enumerate(apps):
                x = 60 + 100 * i - rail["offset"]
                if -50 <= x <= 490:
                    rows.append({"text": app, "type": "Cell", "x": x, "y": 400})
            return rows

        def swipe(x1, y1, x2, y2, duration):
            rail["offset"] = max(0, min(200, rail["offset"] - (x2 - x1)))

        taps = []
        phone = MagicMock()
        phone.swipe.side_effect = swipe
        phone.tap.side_effect = lambda x, y: taps.append(x)
        with patch.object(phone_youtube, "phone", phone), patch.object(phone_youtube, "screen", screen),                 patch.object(phone_youtube, "layout", lambda **k: PhoneLayout(440, 956)),                 patch.object(phone_youtube.time, "sleep"):
            phone_youtube.choose_share_app("TikTok")
        self.assertEqual(taps, [60])

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


    def test_a_scrolled_feed_is_scrolled_back_until_the_tab_bar_shows(self):
        # Home scrolled down, mini-player up, tab bar hidden.
        phone = MagicMock()
        phone.screen_info.return_value = {"width": 440, "height": 956}
        feed = [{"text": "Your custom feed", "x": 210, "y": 131}, {"text": "All", "x": 260, "y": 131},
                {"text": "A video title", "x": 170, "y": 200}]
        screens = iter([feed, [{"text": "Home", "x": 40, "y": 904}, {"text": "You", "x": 400, "y": 904}]])
        with patch("video_drop.youtube_nav.visible_rows", lambda p: next(screens)),                 patch("video_drop.youtube_nav.time.sleep"):
            youtube_nav.open_tabs(phone)
        phone.swipe.assert_called_once()
        x1, y1, x2, y2, _ = phone.swipe.call_args[0]
        self.assertLess(y1, y2)  # a downward drag scrolls the feed up
        phone.tap.assert_not_called()

    def test_an_unknown_screen_without_the_feed_chip_still_stops(self):
        self.assertFalse(youtube_nav.feed_with_hidden_tabs([{"text": "All", "x": 200, "y": 600}],
                                                           youtube_nav.PhoneLayout(440, 956)))

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


def test_a_playing_trim_screen_read_by_ocr_exits_by_its_measured_x():
    # YouTube can reopen on an unfinished upload's trim screen; OCR cannot read the X icon.
    from video_drop import youtube_nav
    labels = ["1:08", "Chat can pay to", "30.2s", "Choose a part of the video", "Next"]
    assert youtube_nav.exit_target(labels) == youtube_nav.TRIM_X
    assert youtube_nav.TRIM_X_POINT == (20.0, 86.0)


def test_a_restored_description_editor_is_left_by_back():
    # YouTube can reopen on an unfinished upload's description editor.
    from video_drop import youtube_nav
    labels = ["id.creation.modes.view", "Back", "Add description", "Hashtags", "Q", "W"]
    assert youtube_nav.exit_target(labels) == "Back"


def test_youtube_waits_in_front_until_the_upload_is_shown_finished():
    # Leaving YouTube right after the tap leaves the Short as a draft.
    from scripts import phone_youtube as yt
    reads = iter([["Uploading 34%"], ["Uploading 80%"], ["Uploaded to Your Channel"]])
    now = [0.0]
    assert yt.wait_for_upload(83_000_000, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
                              read=lambda: next(reads)) is True


def test_youtube_upload_wait_is_bounded_and_says_so():
    from scripts import phone_youtube as yt
    now = [0.0]
    assert yt.wait_for_upload(10_000_000, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
                              read=lambda: ["Uploading 10%"]) is False
    assert now[0] >= (90 + 15) * 1.5


class DescriptionReadBack(unittest.TestCase):
    """The typed text is on screen but the first read can list no field."""

    def tree(self, *values):
        return {"type": "XCUIElementTypeApplication", "children": [
            {"type": "XCUIElementTypeTextView", "name": "", "label": "", "value": v, "isVisible": "1",
             "rect": {"x": 10, "y": 100, "width": 400, "height": 80}} for v in values]}

    def test_an_empty_read_is_read_again(self):
        phone = MagicMock()
        phone.ui_tree.side_effect = [self.tree(), self.tree("one line")]
        with patch.object(phone_youtube, "phone", phone), patch.object(phone_youtube.time, "sleep"):
            phone_youtube.type_description("one line")
        self.assertEqual(phone.ui_tree.call_count, 2)

    def test_a_field_with_other_text_still_stops_at_once(self):
        phone = MagicMock()
        phone.ui_tree.side_effect = [self.tree("wrong"), self.tree("one line")]
        with patch.object(phone_youtube, "phone", phone), patch.object(phone_youtube.time, "sleep"), \
                self.assertRaisesRegex(phone_youtube.PhoneUploadError, "does not match"):
            phone_youtube.type_description("one line")


class DescriptionEditorByOcr(unittest.TestCase):
    def test_the_ocr_read_editor_is_left_by_its_measured_back_chevron(self):
        # OCR reads "< Add description" and no "Back".
        labels = ["7:42", "< Add description", "#a #b", "Hashtags", "space", "return"]
        self.assertEqual(youtube_nav.exit_target(labels), youtube_nav.DESCRIPTION_BACK)
        self.assertEqual(youtube_nav.exit_target(["Add description", "Back"]), "Back")

    def test_a_tree_holding_only_the_keyboard_is_read_by_ocr(self):
        # The tree shows only the keyboard on the description editor.
        phone = MagicMock()
        phone.screen_info.return_value = {"width": 440, "height": 956}
        keyboard = [{"text": "space", "x": 221, "y": 858}, {"text": "return", "x": 385, "y": 858}]
        tabs = [{"text": "Home", "x": 40, "y": 904}, {"text": "You", "x": 400, "y": 904}]
        trees = iter([keyboard, tabs, tabs])
        with patch("video_drop.youtube_nav.visible_rows", lambda p: next(trees)), \
                patch("video_drop.youtube_nav.pixel_rows",
                      lambda p: [{"text": "< Add description", "x": 13, "y": 74}]), \
                patch("video_drop.youtube_nav.time.sleep"):
            youtube_nav.open_tabs(phone)
        phone.tap.assert_called_once_with(20.0, 86.0)

    def test_the_ocr_read_shorts_editor_is_left_by_its_measured_back_arrow(self):
        # Discard leads to the playing editor; OCR has no "Exit editor".
        labels = ["7:51", "Add sound", "Swipe up to edit", "Edit", "Next"]
        self.assertEqual(youtube_nav.exit_target(labels), youtube_nav.EDITOR_BACK)
        self.assertEqual(youtube_nav.exit_target(labels + ["Exit editor"]), "Exit editor")
