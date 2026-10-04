"""phone_awake against a simulated Settings app built from iOS 26 recordings."""

import unittest
from unittest import mock

from video_drop import phone_awake
from video_drop.screens.snapshot import Element

CHOICES = ["30", "60", "120", "180", "240", "300", "-1"]


def el(kind, name, y, value="", label=""):
    return Element(kind, label or name, name, value, 20.0, y - 26.0, 400.0, 53.0)


class FakeSettings:
    """Pages and names as recorded: root -> Battery -> Power Mode; root -> Display -> Auto-Lock."""

    def __init__(self, low_power=True, auto_lock="-1"):
        self.low_power, self.auto_lock = low_power, auto_lock
        self.page, self.taps = "root", []

    def screen_info(self):
        return {"width": 440, "height": 956}

    def press_home(self):
        pass

    def open_app(self, bundle, wait_seconds=0):
        assert bundle == phone_awake.SETTINGS

    def swipe(self, *a):
        pass

    def elements(self):
        back = [Element("Button", "Back", "BackButton", "", 20, 62, 44, 44)] if self.page != "root" else []
        if self.page == "root":
            return [el("Button", phone_awake.DISPLAY, 300), el("Button", phone_awake.BATTERY, 400)]
        if self.page == "battery":
            return back + [el("Cell", phone_awake.POWER_MODE, 806)]
        if self.page == "power":
            return back + [el("Switch", phone_awake.LOW_POWER_SWITCH, 375, "1" if self.low_power else "0")]
        if self.page == "display":
            # Low Power Mode greys the row out: tapping it does nothing (measured).
            return back + [el("Cell", phone_awake.AUTO_LOCK, 543)]
        rows = [el("Cell", name, 160 + 53 * i) for i, name in enumerate(CHOICES)]
        mark = next(r for r in rows if r.name == self.auto_lock)
        return back + rows + [Element("Button", "checkmark", "checkmark", "", 378, mark.y - 9, 20, 18)]

    def tap(self, x, y):
        hit = [e for e in self.elements() if e.left <= x <= e.left + e.width and e.top <= y <= e.top + e.height]
        name = hit[0].name
        self.taps.append(name)
        if name == "BackButton":
            self.page = {"power": "battery", "battery": "root", "display": "root", "autolock": "display"}[self.page]
        elif name == phone_awake.BATTERY:
            self.page = "battery"
        elif name == phone_awake.POWER_MODE:
            self.page = "power"
        elif name == phone_awake.LOW_POWER_SWITCH:
            self.low_power = not self.low_power
        elif name == phone_awake.DISPLAY:
            self.page = "display"
        elif name == phone_awake.AUTO_LOCK and not self.low_power:
            self.page = "autolock"
        elif self.page == "autolock":
            self.auto_lock = name


@mock.patch("video_drop.phone_awake.time.sleep", lambda s: None)
class StayAwake(unittest.TestCase):
    def run_awake(self, phone):
        with mock.patch.object(phone_awake, "_elements", lambda p, names=None: tuple(e for e in p.elements() if names is None or e.name in names)):
            with phone_awake.stay_awake(phone):
                during = (phone.low_power, phone.auto_lock)
        return during

    def test_low_power_goes_off_first_so_auto_lock_can_change_and_both_come_back(self):
        # The recorded phone: Low Power Mode on, which holds Auto-Lock at 30 seconds.
        phone = FakeSettings(low_power=True, auto_lock="30")
        self.assertEqual(self.run_awake(phone), (False, "-1"))
        self.assertEqual((phone.low_power, phone.auto_lock), (True, "30"))

    def test_an_owner_already_on_never_keeps_it_and_only_low_power_is_restored(self):
        phone = FakeSettings(low_power=True, auto_lock="-1")
        self.assertEqual(self.run_awake(phone), (False, "-1"))
        self.assertEqual((phone.low_power, phone.auto_lock), (True, "-1"))
        self.assertEqual(phone.taps.count(phone_awake.LOW_POWER_SWITCH), 2)

    def test_a_phone_already_awake_is_not_touched(self):
        phone = FakeSettings(low_power=False, auto_lock="-1")
        self.run_awake(phone)
        self.assertNotIn(phone_awake.LOW_POWER_SWITCH, phone.taps)
        self.assertFalse([t for t in phone.taps if t in phone_awake_choices()])

    def test_a_failed_run_still_restores(self):
        phone = FakeSettings(low_power=True, auto_lock="60")
        with mock.patch.object(phone_awake, "_elements", lambda p, names=None: tuple(e for e in p.elements() if names is None or e.name in names)):
            with self.assertRaises(RuntimeError):
                with phone_awake.stay_awake(phone):
                    raise RuntimeError("flow failed")
        self.assertEqual((phone.low_power, phone.auto_lock), (True, "60"))

    def test_an_unrecorded_settings_layout_stops_before_changing_anything(self):
        phone = FakeSettings()
        with mock.patch.object(phone_awake, "_elements", lambda p, names=None: ()):
            with self.assertRaisesRegex(phone_awake.AwakeError, "has no"):
                with phone_awake.stay_awake(phone):
                    pass
        self.assertEqual(phone.taps, [])


def phone_awake_choices():
    return CHOICES


if __name__ == "__main__":
    unittest.main()
