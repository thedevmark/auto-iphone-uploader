import unittest

from video_drop.phone_focus import FocusError, focus_state, upload_focus


class FakePhone:
    def __init__(self, dnd=False):
        self.dnd = dnd
        self.in_control = False
        self.in_menu = False
        self.taps = []

    def screen_info(self):
        return {"width": 440, "height": 956}

    def swipe(self, x1, y1, x2, y2, seconds):
        self.in_control = y2 > y1
        self.in_menu = False

    def ocr(self):
        if self.in_menu:
            labels = ["Focus", "Do Not Disturb"]
        elif self.in_control:
            labels = ["Do Not Disturb" if self.dnd else "Focus"]
        else:
            labels = ["Video composer"]
        return [{"text": label, "x": 200, "y": 200 + i * 80, "type": "Button"}
                for i, label in enumerate(labels)]

    def compact(self, rows):
        return rows

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.in_menu and y == 280:
            self.dnd = not self.dnd
            self.in_menu = False
        elif self.in_control and self.dnd:
            self.dnd = False
        elif self.in_control:
            self.in_menu = True


class PhoneFocusTests(unittest.TestCase):
    def test_restores_off_even_when_upload_fails(self):
        phone = FakePhone()
        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            with upload_focus(phone):
                self.assertTrue(phone.dnd)
                raise RuntimeError("upload failed")
        self.assertFalse(phone.dnd)
        self.assertFalse(phone.in_control)

    def test_preserves_dnd_that_was_already_on(self):
        phone = FakePhone(dnd=True)
        with upload_focus(phone):
            self.assertTrue(phone.dnd)
        self.assertTrue(phone.dnd)
        self.assertEqual(phone.taps, [])

    def test_ambiguous_focus_fails_closed(self):
        with self.assertRaises(FocusError):
            focus_state([{"text": "Focus"}, {"text": "Do Not Disturb"}])


if __name__ == "__main__":
    unittest.main()
