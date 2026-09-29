"""Do Not Disturb guard for native iPhone upload runs.

Only observed Control Center labels authorize a tap. An unknown layout stops
before media enters a platform composer.
"""

from __future__ import annotations

from contextlib import contextmanager

from .phone_ui import PhoneLayout


class FocusError(RuntimeError):
    pass


def visible_rows(phone) -> list[dict]:
    layout = PhoneLayout.from_info(phone.screen_info())
    return [row for row in phone.compact(phone.ocr()) if layout.contains(row)]


def focus_state(rows: list[dict]) -> str:
    labels = [row.get("text", "").strip() for row in rows]
    if "Focus" in labels and "Do Not Disturb" not in labels:
        return "off"
    if "Do Not Disturb" in labels and "Focus" not in labels:
        return "dnd"
    raise FocusError("Cannot verify the current Focus state in Control Center")


def unique_row(rows: list[dict], label: str) -> dict:
    found = [row for row in rows if row.get("text", "").strip() == label]
    if len(found) != 1:
        raise FocusError(f"Expected one {label!r} Control Center control; found {len(found)}")
    return found[0]


def open_control_center(phone) -> list[dict]:
    layout = PhoneLayout.from_info(phone.screen_info())
    phone.swipe(layout.width * .92, 1, layout.width * .92, layout.height * .30, .3)
    rows = visible_rows(phone)
    focus_state(rows)
    return rows


def tap_row(phone, row: dict) -> None:
    phone.tap(row["x"], row["y"])


def close_control_center(phone) -> None:
    layout = PhoneLayout.from_info(phone.screen_info())
    phone.swipe(layout.width * .5, layout.height * .75, layout.width * .5, 1, .3)


def enable_dnd(phone) -> bool:
    """Return True only if this run changed Focus from off to DND."""
    rows = open_control_center(phone)
    if focus_state(rows) == "dnd":
        close_control_center(phone)
        return False
    tap_row(phone, unique_row(rows, "Focus"))
    rows = visible_rows(phone)
    tap_row(phone, unique_row(rows, "Do Not Disturb"))
    try:
        close_control_center(phone)
        rows = open_control_center(phone)
        enabled = focus_state(rows) == "dnd"
        close_control_center(phone)
        if not enabled:
            raise FocusError("Do Not Disturb did not turn on; upload stopped")
    except Exception:
        # The tap may have succeeded even if the subsequent phone read failed.
        try:
            restore_focus(phone)
        except Exception:
            pass
        raise
    return True


def restore_focus(phone) -> None:
    rows = open_control_center(phone)
    if focus_state(rows) != "dnd":
        close_control_center(phone)
        raise FocusError("Focus changed during upload; leaving the current phone state alone")
    tap_row(phone, unique_row(rows, "Do Not Disturb"))
    close_control_center(phone)
    rows = open_control_center(phone)
    restored = focus_state(rows) == "off"
    close_control_center(phone)
    if not restored:
        raise FocusError("Could not confirm Do Not Disturb was restored; check the phone")


@contextmanager
def upload_focus(phone):
    changed = enable_dnd(phone)
    try:
        yield
    finally:
        if changed:
            restore_focus(phone)
