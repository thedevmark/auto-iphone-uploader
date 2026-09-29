"""Observed, reversible YouTube navigation used before account checks."""

from __future__ import annotations

import time

from .phone_ui import PhoneLayout


def visible_rows(phone) -> list[dict]:
    layout = PhoneLayout.from_info(phone.screen_info())
    return [row for row in phone.compact(phone.ocr()) if layout.contains(row)]


def exit_target(labels: list[str]) -> str | None:
    """Return the single observed back control, or None at YouTube's tabs."""
    if "You" in labels:
        return None
    if "Accounts" in labels and "Close" in labels:
        return "Close"
    if "Discard changes?" in labels and "Discard" in labels:
        return "Discard"
    if "Set visibility" in labels and "Back" in labels:
        return "Back"
    if "Add details" in labels and "Back" in labels:
        return "Back"
    if "Exit editor" in labels:
        return "Exit editor"
    if "Exit trim" in labels:
        return "Exit trim"
    if "Collapse video" in labels:
        return "Collapse video"
    if "id.navigation.search.text_field" in labels and "Back" in labels:
        return "Back"
    raise ValueError("YouTube is not on an observed navigation screen")


def open_tabs(phone) -> None:
    phone.press_home()
    phone.open_app("com.google.ios.youtube", wait_seconds=4)
    if phone.current_app().get("bundleId") != "com.google.ios.youtube":
        raise ValueError("YouTube did not open")
    for _ in range(5):
        deadline = time.monotonic() + 12
        while True:
            rows = visible_rows(phone)
            labels = [row["text"] for row in rows]
            try:
                target = exit_target(labels)
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.4)
        if target is None:
            return
        matches = [row for row in rows if row["text"] == target]
        if len(matches) != 1:
            raise ValueError(f"YouTube {target!r} control is ambiguous")
        phone.tap(matches[0]["x"], matches[0]["y"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            next_labels = [row["text"] for row in visible_rows(phone)]
            try:
                next_target = exit_target(next_labels)
            except ValueError:
                time.sleep(0.4)
                continue
            if next_target is None or next_target != target or next_labels != labels:
                break
            time.sleep(0.4)
        else:
            raise ValueError(f"YouTube {target!r} did not change the screen")
    raise ValueError("YouTube did not return to its main tabs")
