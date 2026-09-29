"""Read back an Instagram Reel from the native Scheduled content list.

These checks consume SideTap's accessibility rows. They cannot prove the video
thumbnail or account identity; the phone runner must verify those separately.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .phone_ui import PhoneLayout


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def scheduled_list_label(scheduled_at: str, device_time_zone: str) -> str:
    """Use the phone's verified time zone, since Instagram's picker uses it."""
    instant = datetime.fromisoformat(scheduled_at)
    if instant.tzinfo is None:
        raise ValueError("Posting time must include a time zone")
    local = instant.astimezone(ZoneInfo(device_time_zone))
    hour = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    return f"Scheduled {MONTHS[local.month - 1]} {local.day} at {hour}:{local.minute:02d} {period}"


def _text(value: str) -> str:
    return " ".join(value.split())


def matching_scheduled_reel(rows: list[dict], caption: str, scheduled_at: str,
                            device_time_zone: str, layout: PhoneLayout) -> dict:
    """Require one scheduled row with the exact reviewed caption directly above it.

    The observed English Instagram screen gives a date/time label without a
    year. Unfamiliar layouts or localized labels fail closed for live mapping.
    """
    visible = [row for row in rows if layout.contains(row) and isinstance(row.get("text"), str)]
    if sum(_text(row["text"]) == "Scheduled content" for row in visible) != 1:
        raise ValueError("Instagram Scheduled content screen is missing or ambiguous")
    expected_label = scheduled_list_label(scheduled_at, device_time_zone)
    time_rows = [row for row in visible if _text(row["text"]) == expected_label]
    if len(time_rows) != 1:
        raise ValueError(f"Instagram has no unique scheduled entry at {expected_label}")
    scheduled_row = time_rows[0]
    nearby = sorted((row for row in visible
                     if 0 < scheduled_row["y"] - row["y"] < 0.13 * layout.height
                     and row["x"] > 0.15 * layout.width
                     and not re.fullmatch(r"[.\u2026 ]+", _text(row["text"]))),
                    key=lambda row: row["y"])
    wanted = _text(caption)
    if not wanted:
        raise ValueError("Expected Instagram caption is empty")
    matches = []
    for start in range(len(nearby)):
        for end in range(start + 1, len(nearby) + 1):
            if _text(" ".join(row["text"] for row in nearby[start:end])) == wanted:
                matches.append(nearby[start:end])
    if len(matches) != 1:
        raise ValueError("Instagram scheduled caption is missing or ambiguous near that time")
    return {"caption": wanted, "scheduledLabel": expected_label,
            "captionRows": matches[0], "timeRow": scheduled_row}
