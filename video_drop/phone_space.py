"""Stop a phone upload before it starts when the iPhone cannot hold the video.

A nearly full iPhone drops "Save Video" without an error and destabilises the
developer tunnel under heavy writes, so the check runs before any phone work.
"""

from __future__ import annotations

import json
import subprocess
import sys

GB = 1_000_000_000
MARGIN = GB
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def required_bytes(file_size: int, *, on_phone: bool = False) -> int:
    """The copy the phone receives (unless it already holds it) plus the copy an app exports, and a
    gigabyte for the system to breathe."""
    return (file_size if on_phone else 2 * file_size) + MARGIN


def room_problem(file_size: int, free: int | None, *, on_phone: bool = False) -> str | None:
    """Why the iPhone cannot take this video now, or None."""
    if free is None:
        return "The app could not read the iPhone's free space; reconnect the phone, then try again"
    need = required_bytes(file_size, on_phone=on_phone)
    if free < need:
        return (f"The iPhone needs {need / GB:.1f} GB free for this {file_size / GB:.1f} GB video "
                f"and has {free / GB:.1f} GB. Free up space on the iPhone, then try again.")
    return None


def ensure_room(file_size: int, free: int | None, *, on_phone: bool = False) -> None:
    if problem := room_problem(file_size, free, on_phone=on_phone):
        raise ValueError(problem)


# AFC answers a write to a full phone with error 18 (NoSpaceLeft); apps and go-ios word it like this.
FULL_WORDS = ("nospaceleft", "no space left", "error code: 18", "storage is full", "not enough storage")


def says_full(message: str) -> bool:
    lowered = message.casefold()
    return any(word in lowered for word in FULL_WORDS)


FULL_MESSAGE = "The iPhone's storage is full. Free up space on the iPhone, then try again. Nothing was posted"


def parse_free_bytes(output: str) -> int | None:
    for line in reversed(output.strip().splitlines()):
        try:
            value = json.loads(line).get("FreeBytes")
        except (ValueError, AttributeError):
            continue
        if isinstance(value, int):
            return value
    return None


def free_bytes() -> int | None:
    """The phone's free bytes through the same go-ios binary (GO_IOS_PATH) and phone pin
    (PHONE_UDID) every other phone command uses, with no console window; None when unreadable."""
    from .phone import device

    try:
        path = device.ios_path()
    except device.DeviceError:
        return None
    if not path:
        return None
    try:
        completed = subprocess.run([path, *device.pin_udid(["diskspace"])], capture_output=True, text=True,
                                   timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_free_bytes(completed.stdout + "\n" + completed.stderr)
