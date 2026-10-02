"""Observed, reversible YouTube navigation used before account checks."""

from __future__ import annotations

import time

from . import ocr
from .phone.helpers import VideoSurfaceError
from .phone_ui import PhoneLayout

YOUTUBE = "com.google.ios.youtube"


def visible_rows(phone) -> list[dict]:
    """On-screen rows from the tree, or from OCR of a go-ios screenshot while YouTube plays.

    YouTube opens on its Home feed, whose previews autoplay: the driver refuses the
    accessibility read there (VideoSurfaceError), so that screen is read from pixels only."""
    layout = PhoneLayout.from_info(phone.screen_info())
    try:
        rows = phone.compact(phone.ocr())
    except VideoSurfaceError:
        from .phone import capture

        try:
            rows = ocr.screen_rows(capture.pixels_png(), layout.width)
        except ocr.OcrError as exc:
            raise ValueError(f"YouTube is playing video and OCR could not read it: {exc}") from exc
    return [row for row in rows if layout.contains(row)]


# YouTube restores an unfinished upload's trim screen when it reopens (soak 2026-10-02); that screen
# plays the clip, so it is read by OCR and its X (an icon) is tapped at its measured place:
# (20, 86) points on the 440 x 956 reference phone, recorded 2026-10-02 01:08.
TRIM_X = "trim X (measured)"
TRIM_X_POINT = (20.0, 86.0)


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
    # The description editor of an unfinished upload YouTube restored (recorded 2026-10-02 11:47:
    # title "Add description", "Back" at the top left, keyboard up). Back closes it.
    if "Add description" in labels and "Back" in labels:
        return "Back"
    if "Exit editor" in labels:
        return "Exit editor"
    if "Exit trim" in labels:
        return "Exit trim"
    if "Choose a part of the video" in labels and "Next" in labels:
        return TRIM_X  # the playing trim screen read by OCR: its X is an icon OCR cannot read
    if "Collapse video" in labels:
        return "Collapse video"
    if "id.navigation.search.text_field" in labels and "Back" in labels:
        return "Back"
    raise ValueError("YouTube is not on an observed navigation screen")


def feed_with_hidden_tabs(rows: list[dict], layout: PhoneLayout) -> bool:
    """A YouTube feed scrolled down hides the tab bar; its "All" chip stays at the top.

    Soak 2026-10-02 16:56 (captured): Home feed scrolled, a mini-player in the corner, no tab
    bar, so no "You" to read. A short scroll back up brings the tab bar back.
    """
    return any(row.get("text") == "All" and row.get("y", layout.height) < 0.2 * layout.height for row in rows)


def reveal_tab_bar(phone, rows: list[dict]) -> bool:
    if not any(row.get("text") == "All" for row in rows):
        return False
    layout = PhoneLayout.from_info(phone.screen_info())
    if not feed_with_hidden_tabs(rows, layout):
        return False
    # Left of center and above the corner mini-player; a downward drag scrolls the feed up.
    x = layout.width * 0.3
    phone.swipe(x, layout.height * 0.40, x, layout.height * 0.62, 0.4)
    time.sleep(1.0)
    return True


def open_tabs(phone) -> None:
    phone.press_home()
    try:
        # Confirms the launch with activeAppInfo, unless YouTube is already playing a feed
        # preview: then the driver refuses to ask, and the tab bar read below (and the You
        # page the caller opens next) is what proves YouTube is in front.
        phone.open_app(YOUTUBE, wait_seconds=4)
    except VideoSurfaceError:
        pass
    reveals = 0
    for _ in range(5):
        deadline = time.monotonic() + 12
        while True:
            rows = visible_rows(phone)
            labels = [row["text"] for row in rows]
            try:
                target = exit_target(labels)
                break
            except ValueError:
                if reveals < 2 and reveal_tab_bar(phone, rows):
                    reveals += 1
                    continue
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.4)
        if target is None:
            return
        if target == TRIM_X:
            phone.tap(*PhoneLayout.from_info(phone.screen_info()).reference_point(*TRIM_X_POINT))
        else:
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
