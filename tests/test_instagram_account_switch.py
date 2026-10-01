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
        self.taps = []

    def open_app(self, bundle, wait_seconds=0):
        pass

    def current_app(self):
        return {"bundleId": "com.burbn.instagram"}

    def rows(self):
        return {handle: (220, 541 + 64 * i) for i, handle in enumerate(self.signed_in)}

    def ui_tree(self):
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

    def test_the_wrong_account_is_switched_through_instagram_and_proven(self):
        # 2026-09-30 reference release: Instagram was on @secondchannel and the run stopped for a hand switch.
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
