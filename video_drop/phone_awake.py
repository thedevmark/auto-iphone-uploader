"""Keep the phone awake for a run: Low Power Mode off and Auto-Lock at Never, both restored after.

If the phone auto-locks during Edits' 4K export, the export pauses
("Export paused") and Instagram and TikTok both stop. Low Power Mode holds Auto-Lock at
30 seconds and greys out its row, so Low Power Mode goes off first.

Recorded on iOS 26 (fixtures settings/battery-switches-*, power-mode-*, display-brightness-*,
auto-lock-options-*): Settings rows are Buttons named com.apple.settings.<page>; Battery ->
Cell POWER_MODE_SPECIFIER_IDENTIFIER -> Switch LOW_POWER_MODE_IDENTIFIER_SWITCH (value "1" =
on); Display & Brightness -> Cell AUTOLOCK -> one Cell per choice named by its seconds ("30",
"60", ... "-1" = Never), the chosen one with a "checkmark" Button on its row. Only these names
authorize a tap; anything else stops the run before media enters an app.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from .phone_ui import PhoneLayout
from .screens.snapshot import Element, elements_from_tree

SETTINGS = "com.apple.Preferences"
BATTERY = "com.apple.settings.battery"
POWER_MODE = "POWER_MODE_SPECIFIER_IDENTIFIER"
LOW_POWER_SWITCH = "LOW_POWER_MODE_IDENTIFIER_SWITCH"
DISPLAY = "com.apple.settings.displayAndBrightness"
AUTO_LOCK = "AUTOLOCK"
NEVER = "-1"
CHECKMARK = "checkmark"


class AwakeError(RuntimeError):
    pass


def _elements(phone, names: tuple[str, ...] | None = None) -> tuple[Element, ...]:
    """Named rows by accessibility id when the driver can (about 1 s); else the whole page."""
    lookup = getattr(phone, "elements_by_ids", None)
    if names is None or lookup is None:
        return elements_from_tree(phone.ui_tree())
    out = []
    for item in lookup(names):
        rect = item["rect"]
        out.append(Element(item["type"].removeprefix("XCUIElementType"), item["label"], item["name"],
                           item["value"], float(rect["x"]), float(rect["y"]),
                           float(rect["width"]), float(rect["height"])))
    return tuple(out)


def _layout(phone) -> PhoneLayout:
    return PhoneLayout.from_info(phone.screen_info())


def settings_root(phone) -> None:
    """Settings on its first page: it reopens where it was left, so walk back first."""
    phone.press_home()
    time.sleep(0.8)
    phone.open_app(SETTINGS, wait_seconds=1)
    for _ in range(6):
        back = [e for e in _elements(phone, ("BackButton",)) if e.type == "Button" and e.name == "BackButton"]
        if not back:
            return
        phone.tap(back[0].x, back[0].y)
        time.sleep(1.2)
    raise AwakeError("Settings did not return to its first page")


def find_row(phone, name: str, types: tuple[str, ...]) -> Element:
    """The one on-screen row named `name`: scroll down (pages open at the top), and back up once
    the page stops moving. Scrolling up first costs 25 s on Battery (measured)."""
    layout = _layout(phone)
    x = layout.width / 2
    down, last, turns = True, None, 0
    top, bottom = 0.12 * layout.height, 0.9 * layout.height
    for _ in range(14):
        named = [e for e in _elements(phone, (name,)) if e.name == name and e.type in types]
        found = {(e.left, e.top): e for e in named if top < e.y < bottom}
        if len(found) == 1:
            return next(iter(found.values()))
        if len(found) > 1:
            raise AwakeError(f"Settings shows {name!r} more than once")
        if len(named) == 1:  # listed but off screen: its position says which way to scroll
            down = named[0].y >= bottom
            phone.swipe(x, layout.height * (0.75 if down else 0.30), x, layout.height * (0.30 if down else 0.75), 0.4)
            time.sleep(0.6)
            continue
        elements = _elements(phone)
        shown = tuple((e.name, round(e.y)) for e in elements if e.type in ("Button", "Cell", "Switch"))
        if len(shown) <= 1:  # only the back button: the page is still loading (Battery builds charts)
            time.sleep(1.0)
            continue
        if shown == last:  # the page did not move: that end is reached
            if turns:
                break
            down, turns = not down, 1
        last = shown
        if down:
            phone.swipe(x, layout.height * 0.75, x, layout.height * 0.30, 0.4)
        else:
            phone.swipe(x, layout.height * 0.30, x, layout.height * 0.75, 0.4)
        time.sleep(0.6)
    raise AwakeError(f"Settings has no {name!r} row on this iPhone")


def _open(phone, name: str, types: tuple[str, ...] = ("Button", "Cell"), settle: float = 2.0) -> None:
    row = find_row(phone, name, types)
    phone.tap(row.x, row.y)
    time.sleep(settle)


def _low_power_switch(phone) -> Element:
    return find_row(phone, LOW_POWER_SWITCH, ("Switch",))


def set_low_power(phone, on: bool) -> bool:
    """Set Low Power Mode, proven by a second read. True only if this changed it."""
    settings_root(phone)
    _open(phone, BATTERY, settle=3.0)
    _open(phone, POWER_MODE, ("Cell",))
    switch = _low_power_switch(phone)
    if (switch.value == "1") == on:
        return False
    phone.tap(switch.x, switch.y)
    time.sleep(1.5)
    if (_low_power_switch(phone).value == "1") != on:
        raise AwakeError("Low Power Mode did not turn " + ("on" if on else "off") + "; check the phone")
    return True


def checked_choice(elements) -> str:
    """The Auto-Lock choice (its seconds, "-1" = Never) whose row carries the checkmark."""
    marks = [e for e in elements if e.name == CHECKMARK]
    rows = [e for e in elements if e.type == "Cell" and e.name.lstrip("-").isdigit()]
    chosen = {row.name for row in rows for mark in marks if row.top <= mark.y <= row.top + row.height}
    if len(chosen) != 1:
        raise AwakeError("Auto-Lock does not show exactly one chosen time")
    return chosen.pop()


def _auto_lock_page(phone) -> None:
    settings_root(phone)
    _open(phone, DISPLAY)
    _open(phone, AUTO_LOCK, ("Cell",))


def set_auto_lock(phone, choice: str) -> str | None:
    """Choose an Auto-Lock time; returns the one it replaced, None if it already was `choice`."""
    _auto_lock_page(phone)
    before = checked_choice(_elements(phone))
    if before == choice:
        return None
    row = find_row(phone, choice, ("Cell",))
    phone.tap(row.x, row.y)
    time.sleep(1.2)
    if checked_choice(_elements(phone)) != choice:
        raise AwakeError("Auto-Lock did not change; check the phone")
    return before


@contextmanager
def stay_awake(phone):
    """Low Power Mode off and Auto-Lock at Never for the run; each restored only if changed here."""
    low_power_was_on = False
    auto_lock_was = None
    try:
        low_power_was_on = set_low_power(phone, False)
        auto_lock_was = set_auto_lock(phone, NEVER)
        phone.press_home()
        yield
    finally:
        try:
            if auto_lock_was is not None:
                set_auto_lock(phone, auto_lock_was)
            if low_power_was_on:
                set_low_power(phone, True)
        finally:
            phone.press_home()
