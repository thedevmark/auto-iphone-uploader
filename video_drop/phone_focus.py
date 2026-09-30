"""Do Not Disturb guard for native iPhone upload runs.

Recorded on iOS 26.7 (2026-09-30): Control Center's Focus module is one Button
named "focus-module". Its value is empty while no Focus is on and reads
"Do Not Disturb" while DND is on. Tapping it opens the Focus menu, whose modes
are Buttons named "mode-<Focus>". Only those names authorize a tap; an unknown
layout stops before media enters a platform composer.

Reading the menu's tree right after choosing a mode froze WebDriverAgent on the
reference phone, so a mode tap is never followed by a read on that screen: the
run leaves Control Center and reopens it to prove the new state.
"""

from __future__ import annotations

import time
from contextlib import contextmanager, nullcontext

from .phone_ui import PhoneLayout
from .screens.snapshot import Element, elements_from_tree

MODULE = "focus-module"
DND_MODE = "mode-Do Not Disturb"
DND_VALUE = "Do Not Disturb"


class FocusError(RuntimeError):
    pass


def _elements(phone) -> tuple[Element, ...]:
    return elements_from_tree(phone.ui_tree())


def _one(elements: tuple[Element, ...], name: str) -> Element:
    found = {(e.left, e.top, e.width, e.height): e for e in elements if e.type == "Button" and e.name == name}
    if len(found) != 1:
        raise FocusError(f"Expected one {name!r} Control Center control; found {len(found)}")
    return next(iter(found.values()))


def focus_state(elements: tuple[Element, ...]) -> str:
    value = _one(elements, MODULE).value.strip()
    if not value:
        return "off"
    if value == DND_VALUE:
        return "dnd"
    raise FocusError(f"The {value!r} Focus is on; leaving the phone's Focus alone")


def _settle(phone, name: str, timeout: float = 4.0, poll: float = 0.4) -> tuple[Element, ...]:
    deadline = time.monotonic() + timeout
    while True:
        elements = _elements(phone)
        if any(e.type == "Button" and e.name == name for e in elements):
            return elements
        if time.monotonic() >= deadline:
            raise FocusError(f"Control Center did not show {name!r}")
        time.sleep(poll)


def open_control_center(phone) -> tuple[Element, ...]:
    layout = PhoneLayout.from_info(phone.screen_info())
    phone.press_home()
    time.sleep(0.8)
    phone.swipe(layout.width * .92, 1, layout.width * .92, layout.height * .30, .3)
    time.sleep(1.0)
    return _settle(phone, MODULE)


def close_control_center(phone) -> None:
    phone.press_home()
    time.sleep(0.8)


def _choose_dnd(phone, elements: tuple[Element, ...]) -> None:
    module = _one(elements, MODULE)
    phone.tap(module.x, module.y)
    time.sleep(1.2)
    mode = _one(_settle(phone, DND_MODE), DND_MODE)
    phone.tap(mode.x, mode.y)
    time.sleep(1.2)
    close_control_center(phone)


def _state_now(phone) -> str:
    try:
        return focus_state(open_control_center(phone))
    finally:
        close_control_center(phone)


def enable_dnd(phone) -> bool:
    """Return True only if this run changed Focus from off to DND."""
    elements = open_control_center(phone)
    if focus_state(elements) == "dnd":
        close_control_center(phone)
        return False
    _choose_dnd(phone, elements)
    try:
        if _state_now(phone) != "dnd":
            raise FocusError("Do Not Disturb did not turn on; upload stopped")
    except Exception:
        # The tap may have landed even if the proving read failed.
        try:
            restore_focus(phone)
        except Exception:
            pass
        raise
    return True


def restore_focus(phone) -> None:
    elements = open_control_center(phone)
    if focus_state(elements) != "dnd":
        close_control_center(phone)
        raise FocusError("Focus changed during upload; leaving the current phone state alone")
    _choose_dnd(phone, elements)
    if _state_now(phone) != "off":
        raise FocusError("Could not confirm Do Not Disturb was restored; check the phone")


@contextmanager
def upload_focus(phone):
    changed = enable_dnd(phone)
    try:
        yield
    finally:
        if changed:
            restore_focus(phone)


def optional_focus(phone, enabled: bool):
    """Hold Do Not Disturb for an upload only when the operator keeps that check on."""
    return upload_focus(phone) if enabled else nullcontext()
