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
    # Another Focus (e.g. "Streaming", seen 2026-09-30) already silences the phone: the run
    # proceeds under it and never switches or restores the owner's own Focus.
    return "other"


def _settle(phone, name: str, timeout: float = 4.0, poll: float = 0.4) -> tuple[Element, ...]:
    deadline = time.monotonic() + timeout
    while True:
        elements = _elements(phone)
        if any(e.type == "Button" and e.name == name for e in elements):
            return elements
        if time.monotonic() >= deadline:
            raise FocusError(f"Control Center did not show {name!r}")
        time.sleep(poll)


def open_control_center(phone, *, retry: bool = True) -> tuple[Element, ...]:
    layout = PhoneLayout.from_info(phone.screen_info())
    phone.press_home()
    time.sleep(0.8)
    phone.swipe(layout.width * .92, 1, layout.width * .92, layout.height * .30, .3)
    time.sleep(1.0)
    # Not under the shallow media profile: depth 15 hid the Focus module (2026-09-30 23:44).
    elements = _elements(phone)
    if any(e.type == "Button" and e.name == MODULE for e in elements):
        return elements  # the usual case: one read proves Control Center, no second read
    if retry and any(e.name == "focus-modes-ui" for e in elements):
        # A run that stopped mid-check left the Focus menu up (2026-10-01 00:33); the swipe then
        # shows the menu, not Control Center. Home does not close it (measured 2026-10-01);
        # a tap on the empty area below the modes does. Then open Control Center once more.
        phone.tap(*layout.reference_point(220, 790))
        time.sleep(1.0)
        return open_control_center(phone, retry=False)
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


# A Focus the owner already had on is remembered for a while, so one Post now (YouTube →
# Instagram → TikTok) reads Control Center once instead of once per platform.
QUIET_MEMORY = 600.0
_quiet_until = 0.0


def enable_dnd(phone, *, clock=time.monotonic) -> bool:
    """Return True only if this run changed Focus from off to DND."""
    global _quiet_until
    if clock() < _quiet_until:
        return False
    elements = open_control_center(phone)
    if focus_state(elements) in ("dnd", "other"):
        close_control_center(phone)
        _quiet_until = clock() + QUIET_MEMORY
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


# ---- rotation lock ---------------------------------------------------------------
# Recorded on iOS 26.7 (2026-10-02): Control Center's rotation lock is a Switch named
# "orientation-lock" (label "Lock Rotation"), value "1" when locked. A phone left unlocked
# turned sideways mid-soak and every later flow failed on a 956 x 440 screen.
ROTATION_LOCK = "orientation-lock"


def _rotation_switch(elements: tuple[Element, ...]) -> Element:
    found = {(e.left, e.top): e for e in elements if e.type == "Switch" and e.name == ROTATION_LOCK}
    if len(found) != 1:
        raise FocusError(f"Expected one rotation lock control in Control Center; found {len(found)}")
    return next(iter(found.values()))


def _rotation_locked(elements: tuple[Element, ...]) -> bool:
    return _rotation_switch(elements).value.strip() == "1"


def _toggle_rotation(phone, elements: tuple[Element, ...], want: bool) -> None:
    switch = _rotation_switch(elements)
    phone.tap(switch.x, switch.y)
    time.sleep(0.8)
    close_control_center(phone)
    elements = open_control_center(phone)
    try:
        if _rotation_locked(elements) != want:
            raise FocusError("Rotation lock did not " + ("turn on" if want else "turn off") + "; check the phone")
    finally:
        close_control_center(phone)


def lock_rotation(phone) -> bool:
    """Lock the screen upright for the run. True only if this run turned the lock on."""
    elements = open_control_center(phone)
    if _rotation_locked(elements):
        close_control_center(phone)
        return False
    _toggle_rotation(phone, elements, True)
    return True


def restore_rotation(phone) -> None:
    elements = open_control_center(phone)
    if not _rotation_locked(elements):
        close_control_center(phone)
        return
    _toggle_rotation(phone, elements, False)


@contextmanager
def rotation_lock(phone):
    changed = lock_rotation(phone)
    try:
        yield
    finally:
        if changed:
            restore_rotation(phone)


@contextmanager
def run_guards(phone, checks: dict):
    """The phone-state guards a run holds, each its own Setting: Do Not Disturb, then rotation lock."""
    with optional_focus(phone, checks.get("doNotDisturb", True)), \
            (rotation_lock(phone) if checks.get("lockRotation", True) else nullcontext()):
        yield
