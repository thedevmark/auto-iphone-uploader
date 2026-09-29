"""Read back an Instagram Reel from the native Scheduled content list.

These checks consume SideTap's accessibility rows and screenshot, plus frames
from the source video's first 0.75 seconds. The phone runner verifies identity.
"""

from __future__ import annotations

import re
import json
import subprocess
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageStat

from .phone_ui import PhoneLayout


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def frame_at(source: Path, seconds: float = 0) -> Image.Image:
    """Decode one frame from the original video without making a media copy."""
    try:
        seek = ["-ss", str(seconds)] if seconds else []
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", str(source),
             *seek,
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=45,
        )
        return Image.open(BytesIO(result.stdout)).convert("RGB")
    except FileNotFoundError as exc:
        raise ValueError("ffmpeg is required to verify the Instagram cover") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError("Could not decode the source video's opening frames") from exc


def first_frame(source: Path) -> Image.Image:
    return frame_at(source)


def opening_frames(source: Path) -> list[Image.Image]:
    """Accept a cover from the first three quarters of a second only."""
    frames = [first_frame(source)]
    for seconds in (0.25, 0.5, 0.75):
        try:
            frames.append(frame_at(source, seconds))
        except ValueError:
            # Very short clips may have no frame at a later timestamp.
            continue
    return frames


def read_device_time_zone(udid: str) -> str:
    """Read the iPhone's current IANA zone from go-ios without changing settings."""
    if not udid:
        raise ValueError("Select one connected iPhone before reading its time zone")
    def zone_in(value, *, requested_key: bool) -> str | None:
        if isinstance(value, str):
            if not requested_key:
                return None
            try:
                ZoneInfo(value)
                return value
            except (ValueError, KeyError):
                return None
        if isinstance(value, dict):
            if "TimeZone" in value:
                return zone_in(value["TimeZone"], requested_key=True)
            # go-ios may wrap either the requested key or all global values.
            for key in ("Value", "value"):
                if key not in value:
                    continue
                found = zone_in(value[key], requested_key=requested_key)
                if found:
                    return found
        return None

    # go-ios exposes TimeZone in the global lockdown values. A full global read
    # covers versions that do not return the requested key on its own.
    for requested_key, args in ((True, ("ios", "lockdown", "get", "TimeZone", f"--udid={udid}")),
                                (False, ("ios", "lockdown", "get", f"--udid={udid}"))):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode:
            continue
        for text in (result.stdout.strip(), *reversed(result.stdout.splitlines())):
            try:
                zone = zone_in(json.loads(text), requested_key=requested_key)
            except json.JSONDecodeError:
                continue
            if zone:
                return zone
    raise ValueError("Could not read the iPhone time zone through go-ios; keep Instagram unconfirmed")


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


def matching_scheduled_cover(screenshot: Image.Image, opening: Image.Image | list[Image.Image],
                             match: dict, layout: PhoneLayout) -> dict:
    """Check the Reel thumbnail against the source's first 0.75 seconds.

    The scheduled-content row measured on iPhone puts a square cover left of
    its caption. Geometry scales with the phone viewport; an altered layout or
    a different selected cover must fail rather than become a receipt.
    """
    rows = match.get("captionRows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Scheduled Reel caption position is missing")
    caption_y = min(row["y"] for row in rows)
    size = 0.166 * layout.width
    left = 0.027 * layout.width
    sx, sy = screenshot.width / layout.width, screenshot.height / layout.height
    covers = []
    for index, original in enumerate(opening if isinstance(opening, list) else [opening]):
        frame = original.convert("RGB")
        side = min(frame.size)
        x, y = (frame.width - side) // 2, (frame.height - side) // 2
        cover = frame.crop((x, y, x + side, y + side)).resize((128, 128)).crop((8, 8, 120, 120))
        if min(ImageStat.Stat(cover).stddev) >= 20:
            covers.append((index, cover))
    if not covers:
        raise ValueError("Opening frames have too little visual detail to verify the cover")
    # Accessibility centers vary between a one-line and wrapped caption.
    # Search only the measured thumbnail band to tolerate that offset.
    best: tuple[float, tuple[int, int, int, int], int] | None = None
    screenshot = screenshot.convert("RGB")
    for half_point in range(-34, -11):
        top = caption_y + half_point / 2
        if top < 0 or left + size > layout.width or top + size > layout.height:
            continue
        box = tuple(round(value) for value in (left * sx, top * sy,
                                               (left + size) * sx, (top + size) * sy))
        thumbnail = screenshot.crop(box).resize((128, 128)).crop((8, 8, 120, 120))
        for frame_index, cover in covers:
            score = sum(ImageStat.Stat(ImageChops.difference(thumbnail, cover)).mean) / 3
            if best is None or score < best[0]:
                best = score, box, frame_index
    if best is None:
        raise ValueError("Scheduled Reel thumbnail is outside the screen")
    score, box, frame_index = best
    if score > 22:
        raise ValueError(f"Instagram scheduled cover does not match the opening video (difference {score:.1f})")
    return {"difference": round(score, 1), "thumbnailBox": box, "openingFrameIndex": frame_index}


def verified_scheduled_reel(rows: list[dict], screenshot: Image.Image, opening: Image.Image | list[Image.Image],
                            caption: str, scheduled_at: str, device_time_zone: str,
                            layout: PhoneLayout) -> dict:
    """Require both the approved copy/time and the source cover on one native screen."""
    match = matching_scheduled_reel(rows, caption, scheduled_at, device_time_zone, layout)
    return {**match, **matching_scheduled_cover(screenshot, opening, match, layout)}
