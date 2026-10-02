import unittest
from unittest.mock import patch

from scripts import phone_instagram_preflight as preflight


def node(kind, name, x, y, label=None, value=""):
    return {"type": "XCUIElementType" + kind, "name": name, "label": label or name, "value": value,
            "rect": {"x": x - 20, "y": y - 20, "width": 40, "height": 40}, "isVisible": "1"}


class FakeInstagram:
    """Profile and account switcher as recorded 2026-10-01."""

    def __init__(self, active, signed_in=("secondchannel", "examplechannel", "example.creator")):
        self.active = active
        self.signed_in = signed_in
        self.switcher = False
        self.story = False
        self.reel = False
        self.taps = []

    def open_app(self, bundle, wait_seconds=0):
        pass

    def current_app(self):
        return {"bundleId": "com.burbn.instagram"}

    def rows(self):
        return {handle: (220, 541 + 64 * i) for i, handle in enumerate(self.signed_in)}

    def ui_tree(self):
        if self.reel:  # recorded 2026-10-02: one of the owner's Reels, no tab bar
            return {"type": "XCUIElementTypeApplication", "children": [
                node("Button", "back-button", 40, 87, "Back"),
                node("Button", "Insights on Edits", 98, 909)]}
        if self.story:  # recorded 2026-10-01: the story viewer has no tab bar
            return {"type": "XCUIElementTypeApplication", "children": [
                node("Button", "story-header", 120, 88, "creator's story."),
                node("Button", "story-dismiss-button", 420, 96, "Close stories to return to feed")]}
        if self.switcher:
            children = [node("Button", f"row-{h}", x, y, f"INSTAGRAM profile, {h}, 2 chats and 24 more",
                             "1" if h == self.active else "") for h, (x, y) in self.rows().items()]
        else:
            children = [node("Button", "user-switch-title-button", 120, 70, self.active),
                        node("Button", "profile-tab", 400, 900, "Profile")]
        return {"type": "XCUIElementTypeApplication", "children": children}

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.reel:
            self.reel = (x, y) != (40, 87)
            return
        if self.story:
            self.story = (x, y) != (420, 96)
            return
        if self.switcher:
            for handle, point in self.rows().items():
                if point == (x, y):
                    self.active, self.switcher = handle, False
        elif (x, y) == (120, 70):
            self.switcher = True


@patch("scripts.phone_instagram_preflight.time.sleep", lambda seconds: None)
class AccountSwitchTests(unittest.TestCase):
    def test_the_right_account_is_left_alone(self):
        phone = FakeInstagram("examplechannel")
        with patch.object(preflight, "phone", phone):
            self.assertEqual(preflight.ensure_instagram_account("@examplechannel"), "@examplechannel")
        self.assertNotIn((120, 70), phone.taps)

    def test_a_story_viewer_is_closed_before_the_profile_is_read(self):
        phone = FakeInstagram("examplechannel")
        phone.story = True
        with patch.object(preflight, "phone", phone):
            self.assertEqual(preflight.ensure_instagram_account("@examplechannel"), "@examplechannel")
        self.assertEqual(phone.taps[0], (420, 96))

    def test_a_single_reel_is_left_with_back_before_the_profile_is_read(self):
        phone = FakeInstagram("examplechannel")
        phone.reel = True
        with patch.object(preflight, "phone", phone):
            self.assertEqual(preflight.ensure_instagram_account("@examplechannel"), "@examplechannel")
        self.assertEqual(phone.taps[0], (40, 87))

    def test_the_wrong_account_is_switched_through_instagram_and_proven(self):
        # 2026-09-30 (reference release): Instagram was on @secondchannel and the run stopped for a hand switch.
        phone = FakeInstagram("secondchannel")
        with patch.object(preflight, "phone", phone):
            self.assertEqual(preflight.ensure_instagram_account("@examplechannel"), "@examplechannel")
        self.assertEqual(phone.active, "examplechannel")

    def test_a_handle_that_only_prefixes_another_is_not_chosen(self):
        phone = FakeInstagram("secondchannel", signed_in=("secondchannel", "examplechannel2"))
        with patch.object(preflight, "phone", phone):
            with self.assertRaisesRegex(ValueError, "0 rows"):
                preflight.ensure_instagram_account("@examplechannel")
        self.assertEqual(phone.active, "secondchannel")

    def test_a_switch_that_does_not_land_stops_before_export(self):
        phone = FakeInstagram("secondchannel")
        phone.tap = lambda x, y: setattr(phone, "switcher", (x, y) == (120, 70) or phone.switcher)
        clock = iter(range(0, 100, 5))
        with patch.object(preflight, "phone", phone), \
                patch("scripts.phone_instagram_preflight.time.monotonic", lambda: next(clock)):
            with self.assertRaisesRegex(ValueError, "did not switch"):
                preflight.ensure_instagram_account("@examplechannel")


if __name__ == "__main__":
    unittest.main()


class PlayingScreenTests(unittest.TestCase):
    """Leaving a playing Instagram screen from OCR text only (no accessibility request)."""

    def leave(self, texts):
        taps, swipes = [], []
        fake = type("P", (), {"screen_info": lambda self: {"width": 440, "height": 956},
                              "tap": lambda self, x, y: taps.append((x, y)),
                              "swipe": lambda self, *a: swipes.append(a)})()
        with patch.object(preflight, "phone", fake), \
                patch.object(preflight, "_playing_rows", lambda: [{"text": t} for t in texts]), \
                patch("scripts.phone_instagram_preflight.time.sleep", lambda s: None):
            preflight._leave_playing_screen()
        return taps, swipes

    def test_a_single_reel_taps_back_not_the_missing_tab_bar(self):
        # Recorded 2026-10-02 00:29: "Insights on Edits", "245K views", "Boost", no tab bar.
        taps, swipes = self.leave(["examplechannel", "Insights on Edits", "245K views", "Boost"])
        self.assertEqual((taps, swipes), ([(40.0, 87.0)], []))

    def test_a_story_is_swiped_closed_and_a_feed_taps_profile(self):
        self.assertEqual(len(self.leave(["Send message"])[1]), 1)
        taps, _ = self.leave(["For you", "Following"])
        self.assertEqual(taps, [(370.5, 904.0)])
