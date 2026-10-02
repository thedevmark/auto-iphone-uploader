"""YouTube native Schedule on a synthetic phone built from the recorded 440 x 956 frames.

Screens follow the reference recordings (YouTube 21.38.3, iOS 26.7): Set visibility with
Schedule chosen, whose date field is drawn but absent from the tree; the date alert with its
calendar, Time button, OK and Cancel; the time-wheel popover. Placeholder names only.
"""

from __future__ import annotations

import calendar
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import phone_youtube as yt
from scripts import phone_youtube_schedule as ys
from video_drop import native_schedule as ns
from video_drop.core import Store
from video_drop.phone_ui import PhoneLayout
from zoneinfo import ZoneInfo

ZoneNY = ZoneInfo("America/New_York")
TARGETS = {"youtube": "@creator"}
IDENTITY = [{"text": "id.elements.components.identity_chip_component"}, {"text": "creator, @creator"}]
NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)  # 11:00 AM in New York


def node(kind, label="", name="", value="", frame=(0, 0, 440, 956)):
    return {"type": f"XCUIElementType{kind}", "label": label, "name": name, "value": value,
            "rect": dict(zip(("x", "y", "width", "height"), frame)), "isVisible": "1"}


def inside(frame, x, y):
    return frame[0] <= x <= frame[0] + frame[2] and frame[1] <= y <= frame[1] + frame[3]


class FakeYouTube:
    """Details -> Set visibility -> Schedule -> date alert -> time wheels, as recorded."""

    FIELD = (24, 294, 392, 47)  # drawn, never in the tree
    WHEELS = [(171, 357, 56, 248), (231, 357, 51, 248), (286, 357, 66, 248)]

    def __init__(self, *, field_opens=True):
        self.state = "details"
        self.field_opens = field_opens
        self.saved = (date(2026, 9, 30), 0, 0)  # YouTube's default: next midnight
        self.draft = None
        self.shown = (2026, 9)
        self.wheels = None
        self.taps = []

    # -- screens --
    def rows(self):
        back = node("Button", "Back", "id.elements.components.metadata_editor.app_bar.back", "", (-4, 60, 48, 48))
        title = node("Other", "Set visibility", "Set visibility", "", (48, 71, 118, 27))
        if self.state == "details":
            return [node("ScrollView", name="id.metadata_editor.scroll_view", frame=(0, 100, 440, 750)),
                    node("Other", "creator, @creator", "id.elements.components.identity_chip_component", "",
                         (0, 336, 440, 69)),
                    node("Button", "Visibility, Public", "id.elements.components.metadata_editor.privacy",
                         "", (0, 404, 440, 57)),
                    node("Button", "Upload Short", "id.metadata_editor.upload_button", "", (12, 862, 416, 48))]
        if self.state == "visibility":
            return [back, title,
                    node("Other", name="id.elements.components.privacy_settings", frame=(0, 106, 440, 364)),
                    node("Button", "Publish now", "Publish now", "", (0, 106, 440, 64)),
                    node("Button", "Public, Anyone can search for and view", "p", "1", (0, 170, 440, 72)),
                    node("Button", "Unlisted, Anyone with the link can view", "u", "", (0, 243, 440, 72)),
                    node("Button", "Private, Only people you choose can view", "v", "", (0, 316, 440, 72)),
                    node("Button", "Schedule", "Schedule", "", (0, 397, 440, 72))]
        if self.state == "schedule":
            return [back, title,
                    node("Other", name="id.elements.components.privacy_settings", frame=(0, 106, 440, 261)),
                    node("Button", "Publish now", "Publish now", "", (0, 106, 440, 64)),
                    node("Button", "Schedule", "Schedule", "", (0, 179, 440, 72))]
        day, hour, minute = self.draft
        year, month = self.shown
        rows = [node("DatePicker", frame=(56, 266, 328, 372)),
                node("Button", "Show year picker", "DatePicker.Show", f"{calendar.month_name[month]} {year}",
                     (64, 282, 157, 38)),
                node("Button", "Previous Month", "DatePicker.PreviousMonth", "", (303, 282, 44, 38)),
                node("Button", "Next Month", "DatePicker.NextMonth", "", (346, 282, 44, 38))]
        first = date(year, month, 1)
        for number in range(1, calendar.monthrange(year, month)[1] + 1):
            current = first.replace(day=number)
            slot = (first.weekday() + 1) % 7 + number - 1
            label = ns.day_button_label(current)
            if current == NOW.date():
                label = "Today, " + label
            rows.append(node("Button", label, label, "1" if current == day else "",
                             (56 + 47 * (slot % 7), 352 + 40 * (slot // 7), 47, 40)))
        text = ns.picker_time_label(datetime(2026, 1, 1, hour, minute)).replace(" ", " ")
        rows += [node("Button", text, text, "", (64, 591, 313, 37)),
                 node("StaticText", "Time", "Time", "Time", (64, 599, 40, 22)),
                 node("Button", "Cancel", "Cancel", "", (56, 649, 160, 45)),
                 node("Button", "OK", "OK", "", (224, 649, 160, 45)),
                 node("Button", "Dismiss alert", "Dismiss alert", "", (0, 0, 440, 956))]
        if self.state == "wheels":
            hour12 = hour % 12 or 12
            rows = [node("DatePicker", frame=(152, 395, 219, 172)),
                    node("PickerWheel", value=f"{hour12} o’clock", frame=self.WHEELS[0]),
                    node("PickerWheel", value=f"{minute:02d} minutes", frame=self.WHEELS[1]),
                    node("PickerWheel", value="AM" if hour < 12 else "PM", frame=self.WHEELS[2]),
                    node("Button", "dismiss popup", "PopoverDismissRegion", "", (0, 0, 440, 956))]
        return rows

    # -- phone API --
    def ui_tree(self):
        return {"type": "XCUIElementTypeApplication", "rect": {"x": 0, "y": 0, "width": 440, "height": 956},
                "children": self.rows()}

    def swipe(self, *args):
        pass

    def tap(self, x, y):
        self.taps.append((x, y))
        if self.state == "visibility" and inside((0, 397, 440, 72), x, y):
            self.state = "schedule"
        elif self.state == "schedule":
            if inside(self.FIELD, x, y) and self.field_opens:
                self.state, self.draft = "picker", self.saved
                self.shown = (self.saved[0].year, self.saved[0].month)
            elif inside((-4, 60, 48, 48), x, y):
                self.state = "details"
        elif self.state == "picker":
            self.picker_tap(x, y)
        elif self.state == "wheels":
            self.wheel_tap(x, y)

    def picker_tap(self, x, y):
        day, hour, minute = self.draft
        year, month = self.shown
        if inside((224, 649, 160, 45), x, y):
            self.saved, self.state = self.draft, "schedule"
        elif inside((56, 649, 160, 45), x, y):
            self.state = "schedule"
        elif inside((346, 282, 44, 38), x, y):
            self.shown = (year + 1, 1) if month == 12 else (year, month + 1)
        elif inside((303, 282, 44, 38), x, y):
            self.shown = (year - 1, 12) if month == 1 else (year, month - 1)
        elif inside((64, 591, 313, 37), x, y):
            self.state = "wheels"
        else:
            for row in self.rows():
                rect = row["rect"]
                if row["label"].count(",") and inside(tuple(rect.values()), x, y):
                    self.draft = (date(year, month, int(row["label"].split()[-1])), hour, minute)
                    return

    def wheel_tap(self, x, y):
        day, hour, minute = self.draft
        column = next((i for i, f in enumerate(self.WHEELS) if inside(f, x, y)), None)
        if column is None:
            self.state = "picker"  # popover dismissed
            return
        step = 1 if y > 481 else -1
        if column == 0:
            hour = (hour // 12) * 12 + ((hour % 12) + step) % 12
        elif column == 1:
            minute = (minute + step) % 60
        else:
            hour = (hour + 12) % 24
        self.draft = (day, hour, minute)


def patched(fake):
    def matches(label, *, exact=True):
        return [{"text": "Visibility, Public", "x": 220, "y": 432}] if fake.state == "details" else []

    def tap(label, *, timeout=20, exact=True):
        assert fake.state == "details" and label == "Visibility"
        fake.state = "visibility"

    return (mock.patch.object(yt, "phone", fake), mock.patch.object(yt, "matches", matches),
            mock.patch.object(yt, "tap", tap), mock.patch.object(yt, "stage"),
            mock.patch.object(yt, "layout", return_value=PhoneLayout(440, 956)),
            mock.patch.object(ys.time, "sleep"))


class EnterSlotTests(unittest.TestCase):
    def enter(self, fake, moment):
        patches = patched(fake)
        for patch in patches:
            patch.start()
        try:
            return ys.enter_slot(moment, ZoneNY, NOW)
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_sets_the_slot_through_the_date_alert_and_reads_it_back(self):
        fake = FakeYouTube()
        moment = datetime(2026, 10, 1, 19, 0, tzinfo=ZoneNY)
        shown = self.enter(fake, moment)
        self.assertEqual(shown, {"date": "Thursday, October 1", "time": "7:00 PM", "month": "October 2026"})
        self.assertEqual(fake.saved, (date(2026, 10, 1), 19, 0))
        self.assertEqual(fake.state, "details")
        # The untreed field was reached twice (set, then read back), from the live container.
        self.assertEqual(fake.taps.count((220, 318)), 2)
        self.assertNotIn((220, 886), fake.taps)  # never Upload Short

    def test_a_field_tap_that_opens_nothing_stops(self):
        fake = FakeYouTube(field_opens=False)
        with mock.patch.object(ys, "SCREEN_TIMEOUT", 0),                 self.assertRaisesRegex(ys.Stop, "date field tap did not land"):
            self.enter(fake, datetime(2026, 9, 30, 10, 0, tzinfo=ZoneNY))
        self.assertEqual(fake.saved, (date(2026, 9, 30), 0, 0))

    def test_a_wrong_phone_date_stops_before_choosing(self):
        fake = FakeYouTube()
        with self.assertRaisesRegex(ys.Stop, "iPhone's date differs"):
            patches = patched(fake)
            for patch in patches:
                patch.start()
            try:
                ys.enter_slot(datetime(2026, 9, 30, 10, 0, tzinfo=ZoneNY), ZoneNY, NOW + timedelta(days=2))
            finally:
                for patch in reversed(patches):
                    patch.stop()


def confirmed(state: Path, *, mode="schedule", visibility="public", reserve=True) -> tuple[Path, int]:
    (state / "accounts.json").write_text('{"youtube":"@creator"}', encoding="utf-8")
    source = state / "clip.mp4"
    source.write_bytes(b"finished video")
    db = state / "video-drop.sqlite"
    with Store(db, TARGETS) as store:
        release_id = store.import_file(source)["id"]
        store.save_text(release_id, "youtube", "@creator", "Exact title", "#game #clip A simple line", "game",
                        visibility)
        store.authorize(release_id, "youtube")
        store.set_delivery_mode(release_id, mode)
        if reserve and mode == "schedule":
            store.reserve_slot(release_id, NOW)
    return db, release_id


class InputTests(unittest.TestCase):
    def refused(self, pattern, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = confirmed(Path(folder), **kwargs)
            with mock.patch.object(yt, "connect_sidetap", side_effect=AssertionError("phone opened")):
                with self.assertRaisesRegex(ys.Stop, pattern):
                    ys.run(release_id, db, now=lambda: NOW)

    def test_post_now_uses_the_post_now_script(self):
        self.refused("Post now", mode="post_now")

    def test_needs_a_reserved_slot(self):
        self.refused("Reserve a future slot", reserve=False)

    def test_scheduled_shorts_publish_as_public(self):
        self.refused("approve Public", visibility="private")

    def test_a_slot_inside_the_lead_time_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = confirmed(Path(folder))
            with Store(db, TARGETS) as store:
                slot = datetime.fromisoformat(store.release(release_id)["scheduled_at"])
            late = lambda: slot - timedelta(minutes=5)  # noqa: E731
            with mock.patch.object(yt, "connect_sidetap", side_effect=AssertionError("phone opened")):
                with self.assertRaisesRegex(ys.Stop, "passed"):
                    ys.run(release_id, db, now=late)

    def test_the_slot_is_entered_in_the_app_time_zone(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = confirmed(Path(folder))
            with Store(db, TARGETS) as store:
                data, moment = ys.release_input(store, release_id, NOW)
        # Default posting times are 10 AM and 7 PM New York; the next after 11 AM is 7 PM.
        self.assertEqual((moment.date(), ns.picker_time_label(moment)), (date(2026, 9, 29), "7:00 PM"))


class FinalTapTests(unittest.TestCase):
    def test_marked_unconfirmed_before_the_single_upload_tap(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = confirmed(Path(folder))
            seen = []

            class Phone:
                def unlock(self):
                    pass

                def current_app(self):
                    return {"bundleId": ys.BUNDLE}

                def tap(self, x, y):
                    with Store(db, TARGETS) as store:
                        seen.append(next(d["status"] for d in store.release(release_id)["destinations"]
                                         if d["platform"] == "youtube"))

            with mock.patch.object(yt, "connect_sidetap"), mock.patch.object(yt, "phone", Phone()), \
                    mock.patch.object(yt, "run_guards", return_value=nullcontext()), \
                    mock.patch.object(yt, "busy", return_value=nullcontext()), \
                    mock.patch.object(yt, "layout", return_value=PhoneLayout(440, 956)), \
                    mock.patch.object(yt, "ensure_youtube_channel"), \
                    mock.patch.object(yt, "open_source_file"), mock.patch.object(yt, "prepare_youtube"), \
                    mock.patch.object(ys, "enter_slot", return_value={"date": "d", "time": "t"}), \
                    mock.patch.object(yt, "screen", return_value=IDENTITY), \
                    mock.patch.object(yt, "matches", return_value=[{"text": "Upload Short", "x": 220, "y": 886}]):
                result = ys.run(release_id, db, commit=True, now=lambda: NOW)
            self.assertEqual(result["kind"], "unconfirmed")
            self.assertIn("receipt is not mapped", result["message"])
            self.assertEqual(seen, ["unconfirmed"])

    def test_ready_run_never_taps_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = confirmed(Path(folder))

            class Phone:
                def unlock(self):
                    pass

                def tap(self, x, y):
                    raise AssertionError("tapped")

            with mock.patch.object(yt, "connect_sidetap"), mock.patch.object(yt, "phone", Phone()), \
                    mock.patch.object(yt, "run_guards", return_value=nullcontext()), \
                    mock.patch.object(yt, "busy", return_value=nullcontext()), \
                    mock.patch.object(yt, "layout", return_value=PhoneLayout(440, 956)), \
                    mock.patch.object(yt, "ensure_youtube_channel"), \
                    mock.patch.object(yt, "open_source_file"), mock.patch.object(yt, "prepare_youtube"), \
                    mock.patch.object(ys, "enter_slot", return_value={"date": "d", "time": "t"}):
                result = ys.run(release_id, db, now=lambda: NOW)
            self.assertEqual(result["kind"], "ready")
            with Store(db, TARGETS) as store:
                self.assertEqual(store.release(release_id)["status"], "reserved")

    def test_a_link_drop_after_the_mark_is_never_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            db, release_id = confirmed(Path(folder))

            class LinkLost(Exception):
                pass

            class Phone:
                taps = 0

                def unlock(self):
                    pass

                def current_app(self):
                    return {"bundleId": ys.BUNDLE}

                def tap(self, x, y):
                    self.taps += 1
                    raise LinkLost("gone")

            phone = Phone()
            with mock.patch.object(yt, "connect_sidetap"), mock.patch.object(yt, "phone", phone), \
                    mock.patch.object(yt, "WDAError", LinkLost), \
                    mock.patch.object(yt, "run_guards", return_value=nullcontext()), \
                    mock.patch.object(yt, "busy", return_value=nullcontext()), \
                    mock.patch.object(yt, "layout", return_value=PhoneLayout(440, 956)), \
                    mock.patch.object(yt, "ensure_youtube_channel"), \
                    mock.patch.object(yt, "open_source_file"), mock.patch.object(yt, "prepare_youtube"), \
                    mock.patch.object(ys, "enter_slot", return_value={"date": "d", "time": "t"}), \
                    mock.patch.object(yt, "screen", return_value=IDENTITY), \
                    mock.patch.object(yt, "matches", return_value=[{"text": "Upload Short", "x": 220, "y": 886}]):
                with self.assertRaisesRegex(ys.Stop, "uncertain"):
                    ys.run(release_id, db, commit=True, now=lambda: NOW)
            self.assertEqual(phone.taps, 1)


if __name__ == "__main__":
    unittest.main()
