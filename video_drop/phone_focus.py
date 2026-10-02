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
from contextlib import ExitStack, contextmanager, nullcontext

from .phone_ui import PhoneLayout
from .screens.snapshot import Element, elements_from_tree

MODULE = "focus-module"
DND_MODE = "mode-Do Not Disturb"
DND_VALUE = "Do Not Disturb"


class FocusError(RuntimeError):
    pass


# Every name this module reads. Looked up by accessibility id where the driver can (0.87 s),
# not by reading the whole of Control Center (16-25 s each, measured 2026-10-02).
NAMES = ("focus-module", "orientation-lock", "focus-modes-ui", "mode-Do Not Disturb")


def _elements(phone, names: tuple[str, ...] = NAMES) -> tuple[Element, ...]:
    lookup = getattr(phone, "elements_by_ids", None)
    if lookup is None:
        return elements_from_tree(phone.ui_tree())
    out = []
    for item in lookup(names):
        rect = item["rect"]
        out.append(Element(item["type"].removeprefix("XCUIElementType"), item["label"], item["name"],
                           item["value"], float(rect["x"]), float(rect["y"]),
                           float(rect["width"]), float(rect["height"])))
    return tuple(out)


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
        elements = _elements(phone, (name,) + ((ROTATION_LOCK,) if name == MODULE else ()))
        if any(e.type == "Button" and e.name == name for e in elements):
            return elements
        if time.monotonic() >= deadline:
            raise FocusError(f"Control Center did not show {name!r}")
        time.sleep(poll)


def open_control_center(phone, *, retry: bool = True) -> tuple[Element, ...]:
    layout = PhoneLayout.from_info(phone.screen_info())
    _real_home(phone)
    time.sleep(0.8)
    phone.swipe(layout.width * .92, 1, layout.width * .92, layout.height * .30, .3)
    time.sleep(1.0)
    # Not under the shallow media profile: depth 15 hid the Focus module (2026-09-30 23:44).
    elements = _elements(phone, (MODULE, ROTATION_LOCK, "focus-modes-ui"))
    if any(e.type == "Button" and e.name == MODULE for e in elements):
        return elements  # the usual case: one read proves Control Center, no second read
    if retry and any(e.name == "focus-modes-ui" for e in elements):
        # A run that stopped mid-check left the Focus menu up (2026-10-01 00:33); the swipe then
        # shows the menu, not Control Center. Home does not close it (measured 2026-10-01);
        # a tap on the empty area below the modes does. Then open Control Center once more.
        phone.tap(*layout.reference_point(220, 790))
        time.sleep(1.0)
        return open_control_center(phone, retry=False)
    try:
        return _settle(phone, MODULE)
    except FocusError:
        if not retry or _cc_open(elements):
            raise
        # The edge swipe sometimes lands on the Home Screen without opening Control Center
        # (2026-10-02 12:15, captured). One more swipe, proven by the same read.
        phone.swipe(layout.width * .92, 1, layout.width * .92, layout.height * .30, .3)
        time.sleep(1.0)
        return _settle(phone, MODULE)


def _real_home(phone) -> None:
    """Press Home through WDA. A noted video app makes press_home() take the go-ios springboard
    launch instead, which brings up the Home Screen beneath an open Control Center and leaves
    Control Center up (soak 2026-10-02: YouTube and Instagram then read Control Center)."""
    forget = getattr(phone, "note_front_app", None)
    if forget is not None:
        forget(None)
    phone.press_home()


# Empty space below Control Center's modules, above the home indicator, on the 440 x 956
# reference phone. Measured 2026-10-02 11:34: a WDA Home press left Control Center open (both
# soak flows then read it), a tap here closed it.
CC_EMPTY = (220.0, 930.0)


def _cc_open(elements) -> bool:
    return any(e.name in (MODULE, ROTATION_LOCK) for e in elements)


def close_control_center(phone) -> None:
    """Close Control Center by a tap on its empty space, only while it is provably up, then prove it."""
    for _ in range(2):
        if not _cc_open(_elements(phone, (MODULE,))):
            return
        layout = PhoneLayout.from_info(phone.screen_info())
        phone.tap(*layout.reference_point(*CC_EMPTY))
        time.sleep(0.8)
    if _cc_open(_elements(phone, (MODULE,))):
        raise FocusError("Control Center did not close; nothing more was done on the phone")


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


def upright(phone, *, settle: float = 1.5) -> None:
    """Turn a sideways screen back to portrait before anything reads its layout.

    Soak 2026-10-02 16:38: the phone was left sideways with rotation unlocked; WDA read
    956 x 440 and YouTube and Instagram stopped before their first step. WDA can set the
    orientation (measured in Photos); rotation lock then holds it for the run.
    """
    info = phone.screen_info()
    if float(info["width"]) <= float(info["height"]):
        return
    phone.set_portrait()
    time.sleep(settle)
    info = phone.screen_info()
    if float(info["width"]) > float(info["height"]):
        raise FocusError("The phone is sideways and could not be turned upright; stand it in portrait")


def prepare_phone(phone, enabled: bool):
    """One Setting (owner, 2026-10-02: "bundle it all as one thing"): Low Power Mode off and
    Auto-Lock at Never, Do Not Disturb on, rotation locked. Each is restored afterwards only
    if this run changed it, in reverse order."""
    if not enabled:
        return nullcontext()
    from .phone_awake import stay_awake
    stack = ExitStack()
    try:
        stack.enter_context(stay_awake(phone))
        stack.enter_context(upload_focus(phone))
        stack.enter_context(rotation_lock(phone))
    except BaseException:
        stack.close()
        raise
    return stack


@contextmanager
def run_guards(phone, checks: dict):
    """Upright always, then the phone preparation the owner keeps on (see prepare_phone)."""
    upright(phone)
    with prepare_phone(phone, checks.get("preparePhone", True)):
        try:
            yield
        except BaseException as exc:
            # Capture the failing screen now: restoring the guards below opens Control Center.
            from .failure_capture import snap
            snap(exc)
            raise
