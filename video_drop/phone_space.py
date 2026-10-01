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


def required_bytes(file_size: int) -> int:
    """The download copy plus the Photos copy, with room for the system to breathe."""
    return 2 * file_size + MARGIN


def ensure_room(file_size: int, free: int | None) -> None:
    if free is None:
        raise ValueError("The app could not read the iPhone's free space; reconnect the phone, then try again")
    need = required_bytes(file_size)
    if free < need:
        raise ValueError(f"The iPhone needs {need / GB:.1f} GB free for this {file_size / GB:.1f} GB video "
                         f"and has {free / GB:.1f} GB. Free up space on the iPhone, then try again.")


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
