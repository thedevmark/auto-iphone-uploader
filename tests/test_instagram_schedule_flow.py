"""Instagram Schedule through Edits, on a synthetic phone built from the recorded 440 x 956 frames.

Screens follow the reference recordings (iOS 26.7): More options with "Schedule this reel",
the "Schedule reel" sheet whose Date and Time rows are drawn but not in the tree, the calendar
popover, and the scheduled composer ("Also share on…, 1 profile, 2 unavailable", final button
"Schedule"). The time popover was never recorded; its wheels here copy iOS's own time wheels.
Placeholder names only.
"""

from __future__ import annotations

import calendar
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from scripts import phone_instagram as ig
from video_drop import native_schedule as ns
from video_drop.core import Store
from video_drop.phone_ui import PhoneLayout
from video_drop.screens.snapshot import Element

NEW_YORK = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)  # Tuesday 11:00 AM in New York
TARGETS = {"instagram": "@creator", "facebook": "1234567890", "threads": "@creator"}
CAPTION = "Example title #example #reels"


def el(kind, label="", name="", value="", frame=(0, 0, 10, 10)):
    return Element(kind, label, name, value, *frame)


def inside(frame, x, y):
    return frame[0] <= x <= frame[0] + frame[2] and frame[1] <= y <= frame[1] + frame[3]


class FakeInstagram:
    SWITCH_ROW = (0, 468, 440, 77)
    SWITCH = (361, 483, 63, 29)
    DONE = (16, 850, 408, 45)
    DATE_ROW = (0, 715, 440, 60)   # drawn only
    TIME_ROW = (0, 775, 440, 60)   # drawn only
    CALENDAR = (94, 387, 320, 333)
    TIME_POPOVER = (152, 560, 219, 172)
    WHEELS = [(171, 522, 56, 248), (231, 522, 51, 248), (286, 522, 66, 248)]

    def __init__(self, *, time_picker="wheels", close_kills_sheet=False, already_on=False):
        self.state = "composer"
        self.scheduled = already_on
        self.saved = (date(2026, 9, 29), 23, 55)  # Instagram's default, recorded
        self.shown = (2026, 9)
        self.time_picker, self.close_kills_sheet = time_picker, close_kills_sheet
        self.taps = []

    def rows(self):
        if self.state == "composer":
            return (el("StaticText", "New reel", "New reel", "", (186, 74, 68, 20)),
                    el("Cell", "More options", "More options", "", (0, 801, 440, 49)))
        if self.state == "more_options":
            check = "Checked" if self.scheduled else "Not checked"
            return (el("Button", "Back", "BackButton", "", (24, 66, 36, 36)),
                    el("StaticText", "More options", "More options", "", (169, 74, 102, 20)),
                    el("Switch", f"{check}, Schedule this reel, Some features may be unavailable", "", "0",
                       self.SWITCH_ROW),
                    el("Switch", "", "igds-switch", "0", self.SWITCH))
        if self.state == "sheet":
            return (el("Other", "", "ig-partial-modal-sheet-view-controller-content", "", (0, 620, 440, 736)),
                    el("Button", "Dismiss", "Button", "", (203, 635, 34, 3)),
                    el("StaticText", "Schedule reel", "Schedule reel", "", (0, 658, 440, 25)),
                    el("StaticText", "Time zone is based on your device's settings", "", "", (16, 690, 408, 18)),
                    el("Button", "Done", "Done", "", self.DONE))
        day, hour, minute = self.draft
        if self.state == "calendar":
            year, month = self.shown
            rows = [el("DatePicker", frame=self.CALENDAR),
                    el("Button", "Show year picker", "DatePicker.Show", f"{calendar.month_name[month]} {year}",
                       (110, 403, 157, 38)),
                    el("Button", "Previous Month", "DatePicker.PreviousMonth", "", (325, 403, 44, 38)),
                    el("Button", "Next Month", "DatePicker.NextMonth", "", (368, 403, 44, 38))]
            first = date(year, month, 1)
            for number in range(1, calendar.monthrange(year, month)[1] + 1):
                current = first.replace(day=number)
                slot = (first.weekday() + 1) % 7 + number - 1
                label = ns.day_button_label(current)
                if current == NOW.astimezone(NEW_YORK).date():
                    label = "Today, " + label
                rows.append(el("Button", label, label, "1" if current == day else "",
                               (104 + 43 * (slot % 7), 473 + 40 * (slot // 7), 43, 40)))
            return (*rows, el("Button", "dismiss popup", "PopoverDismissRegion", "", (0, 0, 440, 956)))
        if self.time_picker == "unmapped":
            return (el("DatePicker", frame=self.TIME_POPOVER),
                    el("Button", "dismiss popup", "PopoverDismissRegion", "", (0, 0, 440, 956)))
        return (el("DatePicker", frame=self.TIME_POPOVER),
                el("PickerWheel", value=f"{hour % 12 or 12} o’clock", frame=self.WHEELS[0]),
                el("PickerWheel", value=f"{minute:02d} minutes", frame=self.WHEELS[1]),
                el("PickerWheel", value="AM" if hour < 12 else "PM", frame=self.WHEELS[2]),
                el("Button", "dismiss popup", "PopoverDismissRegion", "", (0, 0, 440, 956)))

    def ui_tree(self):
        def node(e):
            return {"type": f"XCUIElementType{e.type}", "label": e.label, "name": e.name, "value": e.value,
                    "rect": {"x": e.left, "y": e.top, "width": e.width, "height": e.height}}
        return {"type": "XCUIElementTypeApplication", "rect": {"x": 0, "y": 0, "width": 440, "height": 956},
                "children": [node(e) for e in self.rows()]}

    def tap(self, x, y):
        self.taps.append((round(x, 1), round(y, 1)))
        state = self.state
        if state == "composer" and inside((0, 801, 440, 49), x, y):
            self.state = "more_options"
        elif state == "more_options":
            if inside(self.SWITCH, x, y) and not self.scheduled:
                self.scheduled, self.state = True, "sheet"
            elif inside((24, 66, 36, 36), x, y):
                self.state = "composer"
        elif state == "sheet":
            if inside(self.DONE, x, y):
                self.state = "more_options"
            elif inside(self.DATE_ROW, x, y):
                self.state, self.draft = "calendar", self.saved
                self.shown = (self.saved[0].year, self.saved[0].month)
            elif inside(self.TIME_ROW, x, y):
                self.state, self.draft = "time", self.saved
        elif state == "calendar":
            self.calendar_tap(x, y)
        elif state == "time":
            self.time_tap(x, y)

    def close(self):
        self.saved = self.draft
        if self.close_kills_sheet:
            self.state, self.scheduled = "more_options", False
        else:
            self.state = "sheet"

    def calendar_tap(self, x, y):
        day, hour, minute = self.draft
        year, month = self.shown
        if not inside(self.CALENDAR, x, y):
            return self.close()
        if inside((368, 403, 44, 38), x, y):
            self.shown = (year + 1, 1) if month == 12 else (year, month + 1)
            return
        for row in self.rows():
            if row.type == "Button" and row.label.count(",") and inside(
                    (row.left, row.top, row.width, row.height), x, y):
                self.draft = (date(year, month, int(row.label.split()[-1])), hour, minute)

    def time_tap(self, x, y):
        day, hour, minute = self.draft
        column = next((i for i, f in enumerate(self.WHEELS) if inside(f, x, y)), None)
        if column is None:
            if not inside(self.TIME_POPOVER, x, y):
                self.close()
            return
        step = 1 if y > 646 else -1
        if column == 0:
            hour = (hour // 12) * 12 + ((hour % 12) + step) % 12
        elif column == 1:
            minute = (minute + step) % 60
        else:
            hour = (hour + 12) % 24
        self.draft = (day, hour, minute)


def on_phone(fake):
    return (mock.patch.object(ig, "phone", fake), mock.patch.object(ig.time, "sleep"),
            mock.patch.object(ig.share, "layout", lambda refresh=False: PhoneLayout(440, 956)),
            mock.patch.object(ig.share, "stage"), mock.patch.object(ig, "SHEET_TIMEOUT", 0),
            mock.patch.object(ig, "TIME_WHEELS_TIMEOUT", 0))


class ScheduleSheetTests(unittest.TestCase):
    def schedule(self, fake, moment):
        patches = on_phone(fake)
        for patch in patches:
            patch.start()
        try:
            return ig.schedule_reel(moment, NOW)
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_enters_the_slot_and_reads_both_rows_back(self):
        fake = FakeInstagram()
        shown = self.schedule(fake, datetime(2026, 10, 2, 19, 0, tzinfo=NEW_YORK))
        self.assertEqual(shown, {"date": "Friday, October 2", "time": "7:00 PM"})
        self.assertEqual(fake.saved, (date(2026, 10, 2), 19, 0))
        self.assertEqual((fake.state, fake.scheduled), ("composer", True))
        # Rows placed from Done (850-895): Date 128 pt above its center, Time 69 pt; each opened twice.
        self.assertEqual(fake.taps.count((220, 744.5)), 2)
        self.assertEqual(fake.taps.count((220, 803.5)), 2)
        # Popovers were closed on the sheet's header note beside them, never on a row.
        self.assertIn((47, 699), fake.taps)

    def test_unrecorded_time_picker_stops_before_done(self):
        fake = FakeInstagram(time_picker="unmapped")
        with self.assertRaisesRegex(ig.Stop, "time picker is not mapped yet"):
            self.schedule(fake, datetime(2026, 9, 30, 10, 0, tzinfo=NEW_YORK))
        self.assertNotIn((220, 872.5), fake.taps)  # Done never tapped

    def test_a_close_that_drops_the_sheet_stops(self):
        fake = FakeInstagram(close_kills_sheet=True)
        with self.assertRaisesRegex(ig.Stop, "scheduling may have turned off"):
            self.schedule(fake, datetime(2026, 9, 30, 10, 0, tzinfo=NEW_YORK))

    def test_a_schedule_already_on_is_not_trusted(self):
        with self.assertRaisesRegex(ig.Stop, "already on"):
            self.schedule(FakeInstagram(already_on=True), datetime(2026, 9, 30, 10, 0, tzinfo=NEW_YORK))

    def test_a_wrong_phone_date_stops(self):
        fake = FakeInstagram()
        patches = on_phone(fake)
        for patch in patches:
            patch.start()
        try:
            with self.assertRaisesRegex(ig.Stop, "iPhone's date differs"):
                ig.schedule_reel(datetime(2026, 10, 2, 10, 0, tzinfo=NEW_YORK), NOW + timedelta(days=3))
        finally:
            for patch in reversed(patches):
                patch.stop()


class ScheduledCrosspostTests(unittest.TestCase):
    """Recorded after scheduling: Threads is Off with no switch, Facebook is On."""

    def screen(self, threads_state):
        return (el("StaticText", "Also share on…", "", "", (160, 74, 120, 20)),
                el("Cell", f"creator, Threads · Public, {threads_state}", "", "", (0, 124, 440, 60)),
                el("Cell", "Creator Page, Facebook · Public, On", "", "", (0, 184, 440, 60)),
                el("Switch", "", "share-service-cell-switch", "0", (361, 200, 63, 28)),
                el("Button", "Back", "BackButton", "", (24, 66, 36, 36)),
                el("Cell", "Also share on…, 1 profile, 2 unavailable", "", "", (0, 724, 440, 49)))

    def check(self, threads_state):
        screen = self.screen(threads_state)
        phone = mock.Mock()
        with mock.patch.object(ig, "phone", phone), mock.patch.object(ig, "elements", lambda: screen), \
                mock.patch.object(ig.time, "sleep"), \
                mock.patch.object(ig.share, "layout", lambda refresh=False: PhoneLayout(440, 956)):
            return ig.crossposts_on(ig.SCHEDULE_CROSSPOSTS, off={"threads": "Threads"})

    def test_facebook_on_threads_off(self):
        self.assertEqual(self.check("Off"), {"threads": "off", "facebook": "on"})

    def test_a_threads_crosspost_that_is_on_stops_a_scheduled_reel(self):
        with self.assertRaisesRegex(ig.Stop, "crosspost this scheduled reel to Threads"):
            self.check("On")


def scheduled_release(folder: str, *, threads=False) -> tuple[Path, int]:
    source = Path(folder) / "clip.mp4"
    source.write_bytes(b"finished source")
    db = Path(folder) / "video-drop.sqlite"
    with Store(db, TARGETS) as store:
        release_id = store.import_file(source)["id"]
        store.save_text(release_id, "instagram", "@creator", "Example title", CAPTION, "")
        store.authorize(release_id, "instagram")
        for platform in ("facebook", "threads") if threads else ("facebook",):
            store.save_text(release_id, platform, TARGETS[platform], "", "", "")
            store.authorize(release_id, platform)
        store.reserve_slot(release_id, NOW)
    return db, release_id


@mock.patch.object(ig, "edits_color_mode", lambda source: "SDR")
@mock.patch.object(ig, "source_frame_rate", lambda source: 59.94)
class ScheduleInputTests(unittest.TestCase):
    def test_schedule_needs_facebook_but_not_threads(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = scheduled_release(folder)
            with Store(db, TARGETS) as store:
                data = ig.release_input(store, release_id, NOW)
        self.assertEqual(data["mode"], "schedule")
        self.assertEqual((data["moment"].date(), ns.picker_time_label(data["moment"])),
                         (date(2026, 9, 29), "7:00 PM"))

    def test_facebook_crosspost_turned_off_in_settings_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = scheduled_release(folder)
            with Store(db, TARGETS) as store:
                if "instagramCrossposts" not in store.release(release_id):
                    self.skipTest("core has no crosspost settings")
                store.set_phone_checks({"crosspostFacebook": False})
                with self.assertRaisesRegex(ig.Stop, "needs the Facebook crosspost on"):
                    ig.release_input(store, release_id, NOW)

    def test_a_slot_inside_the_lead_time_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = scheduled_release(folder)
            with Store(db, TARGETS) as store:
                slot = datetime.fromisoformat(store.release(release_id)["scheduled_at"])
                with self.assertRaisesRegex(ig.Stop, "passed"):
                    ig.release_input(store, release_id, slot - timedelta(minutes=5))


class SubmitTests(unittest.TestCase):
    def submit(self, db, release_id, *, mark=None):
        statuses = []

        class Phone:
            def tap(self, x, y):
                with Store(db, TARGETS) as store:
                    statuses.append({d["platform"]: d["status"] for d in store.release(release_id)["destinations"]})

        post = el("Button", "Schedule", "share-sheet-share-button", "", (228, 865, 196, 45))
        moment = datetime(2026, 9, 29, 19, 0, tzinfo=NEW_YORK)
        data = {"releaseId": release_id, "moment": moment, "caption": CAPTION, "sizeBytes": 10,
                "revisionHash": None}
        with Store(db, TARGETS) as store:
            data["revisionHash"] = next(d for d in store.release(release_id)["destinations"]
                                        if d["platform"] == "instagram")["revision_hash"]
            patches = [mock.patch.object(ig, "phone", Phone()), mock.patch.object(ig.time, "sleep"),
                       mock.patch.object(ig, "schedule_reel", return_value={"date": "d", "time": "t"}),
                       mock.patch.object(ig, "crossposts_on", return_value={"facebook": "on", "threads": "off"}),
                       mock.patch.object(ig, "composer_ready", return_value=post),
                       mock.patch.object(ig, "leave_to_home"),
                       mock.patch.object(ig.share, "busy", return_value=mock.MagicMock())]
            if mark is not None:
                patches.append(mock.patch.object(store, "mark_unconfirmed", mark))
            for patch in patches:
                patch.start()
            try:
                result = ig.schedule_and_submit(store, data, {"difference": 1.0}, commit=True, clock=lambda: NOW)
            finally:
                for patch in reversed(patches):
                    patch.stop()
        return result, statuses

    def test_instagram_and_facebook_are_unconfirmed_before_the_one_schedule_tap(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = scheduled_release(folder, threads=True)
            result, statuses = self.submit(db, release_id)
        self.assertEqual(result["kind"], "unconfirmed")
        self.assertEqual(len(statuses), 1)
        self.assertEqual((statuses[0]["instagram"], statuses[0]["facebook"]), ("unconfirmed", "unconfirmed"))
        # Threads is scheduled on its own in the Threads app, never claimed by Instagram's scheduler.
        self.assertEqual(statuses[0]["threads"], "pending")

    def test_no_schedule_tap_when_facebook_was_not_claimed(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = scheduled_release(folder)
            with self.assertRaisesRegex(ig.Stop, "Schedule was NOT tapped"):
                self.submit(db, release_id, mark=lambda *args, **kwargs: None)


if __name__ == "__main__":
    unittest.main()
