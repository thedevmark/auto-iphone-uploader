"""Decisions for entering a reserved slot in an app's native iOS scheduler.

Pure functions only: the phone scripts read the live tree, call these, and prove every tap
by the next screen. Everything here was measured on the reference recordings (iPhone 16
Pro Max, 440 x 956 points, iOS 26.7, English): YouTube's Schedule date alert and time
wheels, and Instagram's "Schedule reel" sheet and date popover. An unfamiliar label or
layout raises ValueError, so a script stops before it could schedule the wrong time.

The slot is shown in the phone's time zone. Both apps say so ("Local Time", "Time zone
is based on your device's settings"); the app's own zone setting must match the iPhone.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, tzinfo
from typing import Iterable

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TODAY_PREFIX = "Today, "
SELECTED_DAY = "1"  # iOS marks the chosen day button with value "1"
# A slot closer than this cannot be entered, read back and submitted safely; reserve a later one.
MIN_LEAD = timedelta(minutes=10)

# YouTube 21.38.3 "Set visibility" in Schedule mode: the date/time field is drawn but not in
# the accessibility tree. Its center sits this far above the bottom of the privacy-settings
# container (field 294-341, container bottom 367 on the recording).
YOUTUBE_FIELD_ABOVE_CONTAINER_BOTTOM = 49.0
# Instagram "Schedule reel" sheet: the Date and Time rows are drawn but not in the tree.
# Their centers sit this far above the live Done button's center (Done 850-895).
INSTAGRAM_DATE_ABOVE_DONE = 128.0
INSTAGRAM_TIME_ABOVE_DONE = 69.0

Frame = tuple[float, float, float, float]  # left, top, width, height


# ---- the target moment ------------------------------------------------------------


def slot_on_phone(scheduled_at: str, phone_zone: tzinfo, now: datetime) -> datetime:
    """The reserved slot as wall-clock time on the iPhone; it must still be in the future."""
    instant = datetime.fromisoformat(scheduled_at)
    if instant.tzinfo is None:
        raise ValueError("The reserved slot has no time zone")
    if now.tzinfo is None:
        raise ValueError("now must include a time zone")
    if instant <= now:
        raise ValueError("The reserved slot has passed; reserve a future slot")
    return instant.astimezone(phone_zone)


def day_button_label(day: date) -> str:
    """The iOS calendar's accessibility label for one day: "Wednesday, September 30"."""
    return f"{WEEKDAYS[day.weekday()]}, {MONTHS[day.month - 1]} {day.day}"


def is_day_button(label: str, day: date) -> bool:
    """True for this day's button; iOS prefixes today's with "Today, "."""
    wanted = day_button_label(day)
    return label == wanted or label == TODAY_PREFIX + wanted


def month_title(day: date) -> str:
    """The calendar header value: "September 2026"."""
    return f"{MONTHS[day.month - 1]} {day.year}"


def month_offset(shown: str, day: date) -> int:
    """How many months forward (+) or back (-) the calendar must page to show ``day``."""
    match = re.fullmatch(r"([A-Z][a-z]+) (\d{4})", " ".join(shown.split()))
    if not match or match.group(1) not in MONTHS:
        raise ValueError(f"Unfamiliar calendar month {shown!r}")
    shown_index = int(match.group(2)) * 12 + MONTHS.index(match.group(1))
    return day.year * 12 + day.month - 1 - shown_index


def phone_today(labels: Iterable[str], year: int) -> date | None:
    """The phone's own date from the calendar's "Today, ..." button, when that month is shown."""
    for label in labels:
        if label.startswith(TODAY_PREFIX):
            match = re.fullmatch(r"([A-Z][a-z]+), ([A-Z][a-z]+) (\d{1,2})", label[len(TODAY_PREFIX):])
            if match and match.group(2) in MONTHS:
                return date(year, MONTHS.index(match.group(2)) + 1, int(match.group(3)))
    return None


def same_text(a: str, b: str) -> bool:
    """Compare labels that differ only in spacing (iOS puts U+202F before AM/PM)."""
    return " ".join(a.replace(" ", " ").replace(" ", " ").split()) == \
        " ".join(b.replace(" ", " ").replace(" ", " ").split())


def picker_time_label(moment: datetime) -> str:
    """The date picker's time button label in 12-hour English: "10:00 AM"."""
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


# ---- time wheels -------------------------------------------------------------------


def wheel_reading(value: str) -> tuple[str, int | str] | None:
    """Classify one PickerWheel value as the iOS time picker writes it, or None."""
    text = " ".join(value.replace("’", "'").split())
    if match := re.fullmatch(r"(\d{1,2}) o'clock", text):
        return "hour", int(match.group(1))
    if match := re.fullmatch(r"(\d{1,2}) minutes?", text):
        return "minute", int(match.group(1))
    if text in ("AM", "PM"):
        return "meridiem", text
    return None


def classify_wheels(values: list[str]) -> dict[str, int]:
    """Index of the hour, minute and (12-hour clock) AM/PM wheel; anything else is unmapped."""
    found: dict[str, int] = {}
    for index, value in enumerate(values):
        reading = wheel_reading(value)
        if reading is None or reading[0] in found:
            raise ValueError(f"Unfamiliar time wheels {values!r}")
        found[reading[0]] = index
    if "hour" not in found or "minute" not in found:
        raise ValueError(f"Unfamiliar time wheels {values!r}")
    return found


def wheel_targets(moment: datetime, twelve_hour: bool) -> dict[str, int | str]:
    if not twelve_hour:
        return {"hour": moment.hour, "minute": moment.minute}
    return {"hour": moment.hour % 12 or 12, "minute": moment.minute,
            "meridiem": "AM" if moment.hour < 12 else "PM"}


def wheel_direction(kind: str, current: int | str, target: int | str, *, twelve_hour: bool = True) -> int:
    """+1 to move to the next row (tap below center), -1 for the previous, 0 when set.

    Hour and minute wheels loop, so take the shorter way round.
    """
    if current == target:
        return 0
    if kind == "meridiem":
        return 1 if (current, target) == ("AM", "PM") else -1
    if kind == "hour" and twelve_hour:
        size, current, target = 12, int(current) % 12, int(target) % 12
    else:
        size = 24 if kind == "hour" else 60
    forward = (int(target) - int(current)) % size
    return 1 if forward <= size - forward else -1


def wheel_step_point(frame: Frame, direction: int) -> tuple[float, float]:
    """One row above or below the wheel's selection line, from the live wheel frame.

    The selected row is at the wheel's vertical center; iOS rows are about 32-36 points,
    so 15% of the recorded 248-point wheel lands on the neighbouring row. Each tap is
    read back, so a two-row move is corrected rather than trusted.
    """
    if direction not in (-1, 1):
        raise ValueError("A wheel step is one row up or down")
    left, top, width, height = frame
    offset = min(44.0, max(24.0, 0.15 * height))
    return left + width / 2, top + height / 2 + direction * offset


# ---- geometry for controls missing from the tree -------------------------------------


def youtube_date_field_point(schedule_button: Frame, container: Frame) -> tuple[float, float]:
    """The "Sep 30, 2026 at 10:00 AM Local Time" field under YouTube's Schedule row."""
    s_left, s_top, s_width, s_height = schedule_button
    c_left, c_top, c_width, c_height = container
    y = c_top + c_height - YOUTUBE_FIELD_ABOVE_CONTAINER_BOTTOM
    if not s_top + s_height + 20 <= y <= c_top + c_height - 20:
        raise ValueError("YouTube's schedule field is not below its Schedule row as recorded")
    return c_left + c_width / 2, y


def instagram_row_points(done: Frame, title: Frame, note: Frame | None = None) -> dict[str, tuple[float, float]]:
    """Centers of the sheet's Date and Time rows, placed from the live Done button."""
    d_left, d_top, d_width, d_height = done
    x, done_y = d_left + d_width / 2, d_top + d_height / 2
    date_y, time_y = done_y - INSTAGRAM_DATE_ABOVE_DONE, done_y - INSTAGRAM_TIME_ABOVE_DONE
    header = note or title
    header_bottom = header[1] + header[3]
    if title[1] >= header_bottom or not header_bottom + 15 <= date_y < time_y <= d_top - 15:
        raise ValueError("Instagram's schedule sheet does not have room for Date and Time rows as recorded")
    return {"date": (x, date_y), "time": (x, time_y)}


def outside_point(candidates: Iterable[tuple[float, float]], popovers: Iterable[Frame],
                  margin: float = 10.0) -> tuple[float, float]:
    """The first candidate point clear of every popover frame.

    Candidates are chosen by the caller as harmless places under the popover's dismiss
    region (a sheet's header text, a label), so a tap that closes the popover cannot
    select anything if it passes through.
    """
    frames = list(popovers)
    for x, y in candidates:
        if all(not (left - margin <= x <= left + width + margin and top - margin <= y <= top + height + margin)
               for left, top, width, height in frames):
            return x, y
    raise ValueError("No safe place to close the picker; every candidate is under it")


def sheet_header_candidates(title: Frame, note: Frame | None, popover: Frame,
                            sheet_left: float, sheet_right: float) -> list[tuple[float, float]]:
    """Points on the sheet's own header text, beside the popover rather than under it."""
    left_gap = (sheet_left + popover[0]) / 2
    right_gap = (popover[0] + popover[2] + sheet_right) / 2
    rows = [f[1] + f[3] / 2 for f in (note, title) if f]
    return [(x, y) for y in rows for x in (left_gap, right_gap)]


# ---- driving a picker through injected phone reads and taps ------------------------------
# ``read`` returns the live tree as screens.snapshot.Element rows, ``tap`` touches a point,
# ``pause`` waits. Every tap is proven by a later read; nothing is assumed to have landed.


class ScheduleError(ValueError):
    pass


def frame_of(element) -> Frame:
    return element.left, element.top, element.width, element.height


def _one(items, predicate, what: str):
    found = list(dict.fromkeys(e for e in items if predicate(e)))
    if len(found) != 1:
        raise ScheduleError(f"Expected one {what}, found {len(found)}; nothing was scheduled")
    return found[0]


def shown_month(items) -> str:
    header = _one(items, lambda e: e.name == "DatePicker.Show", "calendar month header")
    return header.value or header.label


def wait_until(read, predicate, what: str, *, pause, attempts: int = 12, interval: float = 0.6):
    for _ in range(attempts):
        items = read()
        if predicate(items):
            return items
        pause(interval)
    raise ScheduleError(f"The phone did not show {what}; nothing was scheduled")


def pick_day(read, tap, day: date, *, pause) -> tuple:
    """Page the iOS calendar to ``day``'s month, tap its day button, prove it is selected."""
    items = read()
    for _ in range(14):
        offset = month_offset(shown_month(items), day)
        if offset == 0:
            break
        control_id = "DatePicker.NextMonth" if offset > 0 else "DatePicker.PreviousMonth"
        control = _one(items, lambda e: e.name == control_id, control_id)
        before = shown_month(items)
        tap(control.x, control.y)
        items = wait_until(read, lambda rows: shown_month(rows) != before, "the next calendar month", pause=pause)
    else:
        raise ScheduleError(f"The calendar never reached {month_title(day)}; nothing was scheduled")
    button = _one(items, lambda e: e.type == "Button" and is_day_button(e.label, day), day_button_label(day))
    if button.value != SELECTED_DAY:
        tap(button.x, button.y)
        items = wait_until(read, lambda rows: any(e.type == "Button" and is_day_button(e.label, day)
                                                  and e.value == SELECTED_DAY for e in rows)
                           or not any(e.name == "DatePicker.Show" for e in rows),
                           f"{day_button_label(day)} selected", pause=pause)
    return items


def day_selected(items, day: date) -> bool:
    """The calendar shows ``day``'s month and has exactly that day selected."""
    if month_offset(shown_month(items), day) != 0:
        return False
    day_label = re.compile(r"(?:Today, )?[A-Z][a-z]+, [A-Z][a-z]+ \d{1,2}")
    chosen = [e for e in dict.fromkeys(items) if e.type == "Button" and e.value == SELECTED_DAY
              and day_label.fullmatch(e.label)]
    return len(chosen) == 1 and is_day_button(chosen[0].label, day)


def _wheels(items) -> list:
    return sorted((e for e in items if e.type == "PickerWheel"), key=lambda e: e.left)


def wheel_values(items) -> list[str]:
    return [e.value for e in _wheels(items)]


def wheels_show(items, moment: datetime) -> bool:
    values = wheel_values(items)
    try:
        kinds = classify_wheels(values)
    except ValueError:
        return False
    targets = wheel_targets(moment, "meridiem" in kinds)
    return all(wheel_reading(values[index]) == (kind, targets[kind]) for kind, index in kinds.items())


def set_wheels(read, tap, moment: datetime, *, pause, settle: float = 0.8, limit: int = 40) -> list[str]:
    """Turn the hour, minute and AM/PM wheels to ``moment`` one read-back row at a time."""
    items = read()
    kinds = classify_wheels(wheel_values(items))
    twelve = "meridiem" in kinds
    targets = wheel_targets(moment, twelve)
    count = len(_wheels(items))
    for _ in range(2):  # a second pass catches an hour wheel that flipped AM/PM on its way round
        for kind in ("hour", "minute", "meridiem"):
            if kind not in kinds:
                continue
            still = 0
            for _ in range(limit):
                wheels = _wheels(items)
                if len(wheels) != count:
                    raise ScheduleError("The time wheels changed while being set; nothing was scheduled")
                wheel = wheels[kinds[kind]]
                reading = wheel_reading(wheel.value)
                if reading is None or reading[0] != kind:
                    raise ScheduleError(f"The {kind} wheel reads {wheel.value!r}; nothing was scheduled")
                direction = wheel_direction(kind, reading[1], targets[kind], twelve_hour=twelve)
                if direction == 0:
                    break
                tap(*wheel_step_point(frame_of(wheel), direction))
                pause(settle)
                items = read()
                moved = _wheels(items)
                still = still + 1 if len(moved) == count and moved[kinds[kind]].value == wheel.value else 0
                if still >= 3:
                    raise ScheduleError(f"The {kind} wheel does not move when tapped; nothing was scheduled")
            else:
                raise ScheduleError(f"The {kind} wheel never reached {targets[kind]}; nothing was scheduled")
        items = read()
        if wheels_show(items, moment):
            return wheel_values(items)
    raise ScheduleError(f"The time wheels do not show {picker_time_label(moment)}; nothing was scheduled")
