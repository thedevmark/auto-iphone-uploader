import unittest
from unittest import mock

from video_drop.phone_focus import FocusError, focus_state, optional_focus, upload_focus
from video_drop.screens.snapshot import Element


def node(kind, name, x, y, value="", label=""):
    return {"type": "XCUIElementType" + kind, "name": name, "label": label or name, "value": value,
            "rect": {"x": x - 30, "y": y - 30, "width": 60, "height": 60}, "isVisible": "1"}


class FakePhone:
    """Control Center as recorded on iOS 26.7 (fixtures/control-center/*)."""

    def __init__(self, focus=""):
        self.focus = focus
        self.in_control = False
        self.in_menu = False
        self.taps = []

    def screen_info(self):
        return {"width": 440, "height": 956}

    def press_home(self):
        self.in_control = self.in_menu = False

    def swipe(self, x1, y1, x2, y2, seconds):
        self.in_control = y1 < 5 and y2 > y1

    def ui_tree(self):
        if self.in_menu:
            children = [node("Button", "mode-Do Not Disturb", 220, 263, label="Do Not Disturb, Silence all notifications"),
                        node("Button", "mode-Work", 220, 435, label="Work")]
        elif self.in_control:
            children = [node("Button", "focus-module", 129, 450, self.focus, "Focus")]
        else:
            children = [node("Button", "Video composer", 200, 200)]
        return {"type": "XCUIElementTypeApplication", "children": children}

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.in_menu and (x, y) == (220, 263):
            self.focus = "" if self.focus == "Do Not Disturb" else "Do Not Disturb"
        elif self.in_control and (x, y) == (129, 450):
            self.in_menu = True


@mock.patch("video_drop.phone_focus.time.sleep", lambda seconds: None)
class PhoneFocusTests(unittest.TestCase):
    def test_restores_off_even_when_upload_fails(self):
        phone = FakePhone()
        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            with upload_focus(phone):
                self.assertEqual(phone.focus, "Do Not Disturb")
                raise RuntimeError("upload failed")
        self.assertEqual(phone.focus, "")
        self.assertFalse(phone.in_control)

    def test_switched_off_check_leaves_focus_untouched(self):
        phone = FakePhone()
        with optional_focus(phone, False):
            self.assertEqual(phone.focus, "")
        self.assertEqual(phone.taps, [])

    def test_switched_on_check_holds_dnd(self):
        phone = FakePhone()
        with optional_focus(phone, True):
            self.assertEqual(phone.focus, "Do Not Disturb")
        self.assertEqual(phone.focus, "")

    def test_preserves_dnd_that_was_already_on(self):
        phone = FakePhone("Do Not Disturb")
        with upload_focus(phone):
            self.assertEqual(phone.focus, "Do Not Disturb")
        self.assertEqual(phone.focus, "Do Not Disturb")
        self.assertEqual(phone.taps, [])

    def test_another_focus_is_left_alone(self):
        phone = FakePhone("Work")
        with self.assertRaisesRegex(FocusError, "'Work' Focus is on"):
            with upload_focus(phone):
                pass
        self.assertEqual(phone.focus, "Work")
        self.assertEqual(phone.taps, [])

    def test_state_reads_the_recorded_module_value(self):
        module = lambda value: (Element("Button", "Focus", "focus-module", value, 46, 412, 166, 76),)
        self.assertEqual(focus_state(module("")), "off")
        self.assertEqual(focus_state(module("Do Not Disturb")), "dnd")
        with self.assertRaisesRegex(FocusError, "found 0"):
            focus_state(())
