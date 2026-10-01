"""Native scheduler decisions, on synthetic screens built from the recorded 440 x 956 frames.

Recorded on the reference iPhone 16 Pro Max, iOS 26.7: YouTube's Schedule date alert and
time wheels, Instagram's "Schedule reel" sheet and date popover. No private names appear.
"""

from __future__ import annotations

import calendar
import unittest
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from video_drop import native_schedule as ns
from video_drop.screens.snapshot import Element

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


class SlotTests(unittest.TestCase):
    def test_slot_is_wall_clock_time_in_the_phone_zone(self):
        moment = ns.slot_on_phone("2026-09-30T14:00:00+00:00", NEW_YORK, NOW)
        self.assertEqual((moment.date(), moment.hour, moment.minute), (date(2026, 9, 30), 10, 0))

    def test_slot_across_the_daylight_saving_change(self):
        # 2026-11-01 is the first standard-time day in New York.
        moment = ns.slot_on_phone("2026-11-02T00:00:00+00:00", NEW_YORK, NOW)
        self.assertEqual((moment.date(), ns.picker_time_label(moment)), (date(2026, 11, 1), "7:00 PM"))

    def test_same_instant_is_a_different_day_in_another_zone(self):
        moment = ns.slot_on_phone("2026-10-01T02:00:00+00:00", ZoneInfo("America/Los_Angeles"), NOW)
        self.assertEqual((moment.date(), ns.picker_time_label(moment)), (date(2026, 9, 30), "7:00 PM"))

    def test_past_or_zoneless_slots_are_refused(self):
        with self.assertRaisesRegex(ValueError, "passed"):
            ns.slot_on_phone("2026-09-29T14:00:00+00:00", NEW_YORK, NOW)
        with self.assertRaisesRegex(ValueError, "no time zone"):
            ns.slot_on_phone("2026-09-30T14:00:00", NEW_YORK, NOW)


class LabelTests(unittest.TestCase):
    def test_day_button_labels_match_the_ios_calendar(self):
        self.assertEqual(ns.day_button_label(date(2026, 9, 30)), "Wednesday, September 30")
        self.assertEqual(ns.day_button_label(date(2027, 1, 1)), "Friday, January 1")
        self.assertTrue(ns.is_day_button("Today, Tuesday, September 29", date(2026, 9, 29)))
        self.assertFalse(ns.is_day_button("Tuesday, September 29", date(2026, 9, 30)))
        self.assertFalse(ns.is_day_button("Wednesday, September 3", date(2026, 9, 30)))

    def test_month_paging(self):
        self.assertEqual(ns.month_title(date(2026, 10, 1)), "October 2026")
        self.assertEqual(ns.month_offset("September 2026", date(2026, 9, 30)), 0)
        self.assertEqual(ns.month_offset("September 2026", date(2026, 10, 1)), 1)
        self.assertEqual(ns.month_offset("December 2026", date(2027, 1, 5)), 1)
        self.assertEqual(ns.month_offset("October 2026", date(2026, 9, 30)), -1)
        with self.assertRaisesRegex(ValueError, "Unfamiliar"):
            ns.month_offset("Septembre 2026", date(2026, 9, 30))

    def test_phone_today_reads_the_today_marker(self):
        labels = ["Monday, September 28", "Today, Tuesday, September 29", "Wednesday, September 30"]
        self.assertEqual(ns.phone_today(labels, 2026), date(2026, 9, 29))
        self.assertIsNone(ns.phone_today(labels[:1], 2026))

    def test_time_button_label(self):
        self.assertEqual(ns.picker_time_label(datetime(2026, 9, 30, 19, 0)), "7:00 PM")
        self.assertEqual(ns.picker_time_label(datetime(2026, 9, 30, 0, 5)), "12:05 AM")
        self.assertEqual(ns.picker_time_label(datetime(2026, 9, 30, 12, 30)), "12:30 PM")
        # iOS separates the minutes from AM/PM with a narrow no-break space.
        self.assertTrue(ns.same_text("10:00 AM", "10:00 AM"))
        self.assertFalse(ns.same_text("10:00 PM", "10:00 AM"))


class WheelTests(unittest.TestCase):
    RECORDED = ["12 o’clock", "00 minutes", "AM"]

    def test_recorded_wheels_are_hour_minute_meridiem(self):
        self.assertEqual(ns.classify_wheels(self.RECORDED), {"hour": 0, "minute": 1, "meridiem": 2})
        self.assertEqual(ns.classify_wheels(["13 o'clock", "05 minutes"]), {"hour": 0, "minute": 1})

    def test_unfamiliar_wheels_are_not_mapped(self):
        for values in (["Wed Sep 30", "12 o’clock", "00 minutes"], ["12 o’clock", "1 o’clock"],
                       ["00 minutes"], []):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "Unfamiliar"):
                ns.classify_wheels(values)

    def test_targets_and_shortest_direction(self):
        self.assertEqual(ns.wheel_targets(datetime(2026, 9, 30, 19, 0), True),
                         {"hour": 7, "minute": 0, "meridiem": "PM"})
        self.assertEqual(ns.wheel_targets(datetime(2026, 9, 30, 0, 0), True)["hour"], 12)
        self.assertEqual(ns.wheel_targets(datetime(2026, 9, 30, 19, 0), False), {"hour": 19, "minute": 0})
        self.assertEqual(ns.wheel_direction("hour", 12, 10), -1)
        self.assertEqual(ns.wheel_direction("hour", 10, 12), 1)
        self.assertEqual(ns.wheel_direction("hour", 11, 1), 1)
        self.assertEqual(ns.wheel_direction("minute", 55, 5), 1)
        self.assertEqual(ns.wheel_direction("minute", 5, 55), -1)
        self.assertEqual(ns.wheel_direction("meridiem", "AM", "PM"), 1)
        self.assertEqual(ns.wheel_direction("meridiem", "PM", "AM"), -1)
        self.assertEqual(ns.wheel_direction("minute", 0, 0), 0)

    def test_step_lands_one_row_from_the_selection_line(self):
        x, below = ns.wheel_step_point((171, 357, 56, 248), 1)
        _, above = ns.wheel_step_point((171, 357, 56, 248), -1)
        self.assertEqual(x, 199)
        self.assertAlmostEqual(below - 481, 37.2)
        self.assertAlmostEqual(481 - above, 37.2)


class GeometryTests(unittest.TestCase):
    def test_youtube_field_sits_under_the_schedule_row(self):
        # Recorded: Schedule row 179-251, privacy-settings container 106-367, field centered at 318.
        self.assertEqual(ns.youtube_date_field_point((0, 179, 440, 72), (0, 106, 440, 261)), (220, 318))
        with self.assertRaisesRegex(ValueError, "not below"):
            ns.youtube_date_field_point((0, 179, 440, 72), (0, 106, 440, 180))

    def test_instagram_rows_are_placed_from_done(self):
        # Recorded: title 658-683, time-zone note 690-708, Done 850-895.
        rows = ns.instagram_row_points((16, 850, 408, 45), (0, 658, 440, 25), (16, 690, 408, 18))
        self.assertEqual(rows, {"date": (220, 744.5), "time": (220, 803.5)})
        # Scales with a different phone: the sheet keeps points from its own Done button.
        rows = ns.instagram_row_points((16, 745, 361, 45), (0, 553, 393, 25), (16, 585, 361, 18))
        self.assertEqual(rows["time"][1] - rows["date"][1], 59)

    def test_instagram_rows_refuse_a_cramped_sheet(self):
        with self.assertRaisesRegex(ValueError, "room"):
            ns.instagram_row_points((16, 850, 408, 45), (0, 740, 440, 25), (16, 770, 408, 18))

    def test_outside_point_avoids_the_popover(self):
        popover = (94, 387, 320, 333)  # recorded Instagram date popover
        candidates = ns.sheet_header_candidates((0, 658, 440, 25), (16, 690, 408, 18), popover, 0, 440)
        self.assertEqual(ns.outside_point(candidates, [popover]), (47, 699))
        with self.assertRaisesRegex(ValueError, "No safe place"):
            ns.outside_point([(200, 500)], [popover])


# ---- synthetic pickers ------------------------------------------------------------------


def el(kind, label="", name="", value="", frame=(0, 0, 10, 10)):
    return Element(kind, label, name, value, *frame)


class FakeCalendar:
    """An iOS inline calendar: month header, arrows and one button per day, as recorded."""

    def __init__(self, shown: date, selected: date, today: date, *, ignore_day_taps=False):
        self.year, self.month = shown.year, shown.month
        self.selected, self.today, self.ignore = selected, today, ignore_day_taps
        self.taps = []

    def read(self):
        rows = [el("Button", "Show year picker", "DatePicker.Show", f"{calendar.month_name[self.month]} {self.year}",
                   (64, 282, 157, 38)),
                el("Button", "Previous Month", "DatePicker.PreviousMonth", "", (303, 282, 44, 38)),
                el("Button", "Next Month", "DatePicker.NextMonth", "", (346, 282, 44, 38))]
        first = date(self.year, self.month, 1)
        for day in range(1, calendar.monthrange(self.year, self.month)[1] + 1):
            current = first.replace(day=day)
            slot = (first.weekday() + 1) % 7 + day - 1
            frame = (56 + 47 * (slot % 7), 352 + 47 * (slot // 7), 47, 47)
            label = ns.day_button_label(current)
            if current == self.today:
                label = "Today, " + label
            rows.append(el("Button", label, label, "1" if current == self.selected else "", frame))
        return tuple(rows)

    def tap(self, x, y):
        self.taps.append((x, y))
        for row in self.read():
            if row.left <= x <= row.left + row.width and row.top <= y <= row.top + row.height:
                if row.name == "DatePicker.NextMonth":
                    self.year, self.month = (self.year + 1, 1) if self.month == 12 else (self.year, self.month + 1)
                elif row.name == "DatePicker.PreviousMonth":
                    self.year, self.month = (self.year - 1, 12) if self.month == 1 else (self.year, self.month - 1)
                elif row.label and not self.ignore and row.name not in ("DatePicker.Show",):
                    self.selected = date(self.year, self.month, int(row.label.split()[-1]))
                return


class FakeWheels:
    """Three recorded wheels; a tap below the selection line moves one row forward."""

    FRAMES = [(171, 357, 56, 248), (231, 357, 51, 248), (286, 357, 66, 248)]

    def __init__(self, hour=12, minute=0, meridiem="AM", *, stuck=False, rows_per_tap=1):
        self.values = {"hour": hour, "minute": minute, "meridiem": meridiem}
        self.stuck, self.rows_per_tap, self.taps = stuck, rows_per_tap, 0

    def read(self):
        v = self.values
        return (el("DatePicker", frame=(152, 395, 219, 172)),
                el("PickerWheel", value=f"{v['hour']} o’clock", frame=self.FRAMES[0]),
                el("PickerWheel", value=f"{v['minute']:02d} minutes", frame=self.FRAMES[1]),
                el("PickerWheel", value=v["meridiem"], frame=self.FRAMES[2]),
                el("Button", "dismiss popup", "PopoverDismissRegion", frame=(0, 0, 440, 956)))

    def tap(self, x, y):
        self.taps += 1
        if self.stuck:
            return
        kind = next(k for k, f in zip(("hour", "minute", "meridiem"), self.FRAMES) if f[0] <= x <= f[0] + f[2])
        step = self.rows_per_tap if y > 481 else -self.rows_per_tap
        v = self.values
        if kind == "hour":
            v["hour"] = (v["hour"] - 1 + step) % 12 + 1
        elif kind == "minute":
            v["minute"] = (v["minute"] + step) % 60
        else:
            v["meridiem"] = "PM" if step > 0 else "AM"


def no_pause(_seconds):
    pass


class PickerDrivingTests(unittest.TestCase):
    def test_pick_a_day_in_the_shown_month(self):
        fake = FakeCalendar(date(2026, 9, 1), date(2026, 9, 29), date(2026, 9, 29))
        items = ns.pick_day(fake.read, fake.tap, date(2026, 9, 30), pause=no_pause)
        self.assertTrue(ns.day_selected(items, date(2026, 9, 30)))
        self.assertEqual(len(fake.taps), 1)

    def test_page_forward_across_a_year(self):
        fake = FakeCalendar(date(2026, 12, 1), date(2026, 12, 30), date(2026, 12, 30))
        items = ns.pick_day(fake.read, fake.tap, date(2027, 1, 2), pause=no_pause)
        self.assertEqual(ns.shown_month(items), "January 2027")
        self.assertTrue(ns.day_selected(items, date(2027, 1, 2)))

    def test_already_selected_day_is_not_tapped(self):
        fake = FakeCalendar(date(2026, 9, 1), date(2026, 9, 30), date(2026, 9, 29))
        ns.pick_day(fake.read, fake.tap, date(2026, 9, 30), pause=no_pause)
        self.assertEqual(fake.taps, [])

    def test_a_day_tap_that_does_not_select_stops(self):
        fake = FakeCalendar(date(2026, 9, 1), date(2026, 9, 29), date(2026, 9, 29), ignore_day_taps=True)
        with self.assertRaisesRegex(ns.ScheduleError, "selected"):
            ns.pick_day(fake.read, fake.tap, date(2026, 9, 30), pause=no_pause)

    def test_day_selected_requires_the_right_month(self):
        fake = FakeCalendar(date(2026, 10, 1), date(2026, 10, 30), date(2026, 9, 29))
        self.assertFalse(ns.day_selected(fake.read(), date(2026, 9, 30)))
        self.assertTrue(ns.day_selected(fake.read(), date(2026, 10, 30)))

    def test_wheels_reach_the_slot_and_read_back(self):
        fake = FakeWheels()
        values = ns.set_wheels(fake.read, fake.tap, datetime(2026, 9, 30, 19, 0), pause=no_pause)
        self.assertEqual(values, ["7 o’clock", "00 minutes", "PM"])
        self.assertTrue(ns.wheels_show(fake.read(), datetime(2026, 9, 30, 19, 0)))
        self.assertEqual(fake.taps, 6)  # 12 -> 7 backwards is five rows, then AM -> PM

    def test_a_two_row_tap_is_corrected_by_read_back(self):
        fake = FakeWheels(rows_per_tap=2, minute=0)
        values = ns.set_wheels(fake.read, fake.tap, datetime(2026, 9, 30, 10, 0), pause=no_pause)
        self.assertEqual(values, ["10 o’clock", "00 minutes", "AM"])

    def test_a_stuck_wheel_stops(self):
        with self.assertRaisesRegex(ns.ScheduleError, "does not move"):
            ns.set_wheels(FakeWheels(stuck=True).read, lambda x, y: None,
                          datetime(2026, 9, 30, 10, 0), pause=no_pause)

    def test_unmapped_time_picker_stops_before_any_tap(self):
        taps = []
        read = lambda: (el("PickerWheel", value="Wed Sep 30"), el("PickerWheel", value="10"))  # noqa: E731
        with self.assertRaisesRegex(ValueError, "Unfamiliar time wheels"):
            ns.set_wheels(read, lambda x, y: taps.append((x, y)), datetime(2026, 9, 30, 10, 0), pause=no_pause)
        self.assertEqual(taps, [])


if __name__ == "__main__":
    unittest.main()
