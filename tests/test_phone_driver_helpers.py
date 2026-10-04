"""Vendored from SideTap's tests/test_helpers.py (upstream 0c75c53): the subset
that covers video_drop.phone.helpers. Pins unlock()'s behavior, the tree
and window-size memos, press_home, open_app, the wait_* duty cycles and
compact(). No phone needed. See video_drop/phone/VENDORED.md."""

import pytest

from video_drop.phone import config, helpers, wda_client
from video_drop.phone.config import _load_env
from video_drop.phone.helpers import _passcode_pad_visible, collect_texts
from video_drop.phone.wda_client import WDAError

SAMPLE_TREE = {
    "type": "Application",
    "label": "",
    "rect": {"x": 0, "y": 0, "width": 390, "height": 844},
    "children": [
        {
            "type": "Button",
            "label": "General",
            "isVisible": "1",
            "rect": {"x": 20, "y": 100, "width": 350, "height": 44},
            "children": [],
        },
        {
            "type": "Button",
            "label": "Hidden Thing",
            "isVisible": "0",
            "rect": {"x": 20, "y": 200, "width": 350, "height": 44},
        },
        {
            "type": "StaticText",
            "label": "",
            "name": "Bluetooth",
            "isVisible": "1",
            "rect": {"x": 20, "y": 300, "width": 350, "height": 44},
        },
        {
            "type": "Other",
            "label": "Zero Size",
            "isVisible": "1",
            "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
        },
        {
            "type": "Cell",
            "label": "",
            "isVisible": "1",
            "rect": {"x": 0, "y": 400, "width": 390, "height": 60},
            "children": [
                {
                    "type": "StaticText",
                    "value": "Nested Value",
                    "isVisible": "1",
                    "rect": {"x": 30, "y": 410, "width": 200, "height": 20},
                },
            ],
        },
    ],
}


def test_collect_texts_finds_visible_text_with_centers():
    hits = collect_texts(SAMPLE_TREE)
    texts = [h["text"] for h in hits]
    assert texts == ["General", "Bluetooth", "Nested Value"]
    general = hits[0]
    assert general["x"] == 20 + 350 / 2
    assert general["y"] == 100 + 44 / 2


def test_collect_texts_skips_invisible_and_zero_size():
    texts = [h["text"] for h in collect_texts(SAMPLE_TREE)]
    assert "Hidden Thing" not in texts
    assert "Zero Size" not in texts


def _buttons_tree(labels, kind="Button"):
    return {
        "type": "Application",
        "rect": {"x": 0, "y": 0, "width": 390, "height": 844},
        "children": [
            {
                "type": kind,
                "label": label,
                "isVisible": "1",
                "rect": {"x": 10, "y": 100 + i * 90, "width": 100, "height": 80},
            }
            for i, label in enumerate(labels)
        ],
    }


def test_passcode_pad_visible_with_digit_buttons():
    assert _passcode_pad_visible(_buttons_tree(list("1234567890")))


def test_passcode_pad_visible_with_key_digits():
    """The real pad's digits are Key elements, not Buttons (device dump).
    Detection must count them: on a localized pad there is no
    'passcode' text to fall back on, so the digit count is the only signal."""
    assert _passcode_pad_visible(_buttons_tree(list("1234567890"), kind="Key"))


def test_passcode_pad_visible_with_passcode_text():
    tree = _buttons_tree(["Emergency"])
    tree["children"].append(
        {
            "type": "StaticText",
            "label": "Enter Passcode",
            "isVisible": "1",
            "rect": {"x": 100, "y": 200, "width": 200, "height": 30},
        }
    )
    assert _passcode_pad_visible(tree)


def test_passcode_pad_not_visible_on_ordinary_screen():
    assert not _passcode_pad_visible(SAMPLE_TREE)


class StubPhone:
    """Stands in for WDAClient in unlock() tests. Locked until typed at."""

    def __init__(
        self,
        tree,
        type_error=None,
        unlock_error=None,
        frame=None,
        app="com.apple.springboard",
        wrong_pin=False,
        eats_typing=False,
    ):
        self.tree = tree
        self.typed = []
        self.tapped = []  # pad-digit labels resolved from tap coordinates
        self.hold_ms = []  # finger contact time asked for, per tap
        self.pressed = []
        self.ops = []  # gesture/session events in order, for ordering tests
        self.swipes = 0
        self.source_calls = 0
        self.finds = 0  # bounded find_first probes
        self.idle_waits = []  # set_wait_for_idle calls, in order
        self.type_error = type_error
        self.unlock_error = unlock_error
        self.app = app
        self.wrong_pin = wrong_pin
        # A lock-screen priority notification holds focus and swallows every
        # typed digit while the pad sits behind it.
        self.eats_typing = eats_typing
        # A lit screen compresses to a big PNG; a dark one to almost nothing.
        self.frame = frame if frame is not None else b"\0" * 200_000

    def active_app(self):
        return {"bundleId": self.app}

    def screenshot(self):
        return self.frame

    def unlock(self):
        if self.unlock_error:
            raise self.unlock_error

    def press_button(self, name):
        self.pressed.append(name)
        self.ops.append("press")

    def fresh_session(self):
        self.ops.append("mint")

    def window_size(self):
        return (390.0, 844.0)

    def orientation(self):
        return "PORTRAIT"

    def swipe(self, *_args):
        self.swipes += 1
        self.ops.append("swipe")

    def source(self):
        self.source_calls += 1
        return self.tree

    def find_first(self, _class_chain):
        # The bounded "is a digit key still on screen" probe. Answers from
        # the same tree source() serves, like the real WDA endpoint would.
        self.finds += 1
        for e in helpers.collect_texts(self.tree):
            if (
                e["type"] in ("Button", "Key")
                and len(e["text"]) == 1
                and e["text"].isdigit()
            ):
                return "pad-digit"
        return None

    def set_wait_for_idle(self, seconds):
        self.idle_waits.append(seconds)

    def type_text(self, text):
        if self.type_error:
            raise self.type_error
        self.typed.append(text)
        if not self.wrong_pin and not self.eats_typing:
            self.tree = SAMPLE_TREE  # accepted: pad dismissed, home screen

    def tap(self, x, y, hold_ms=None):
        # Resolve the tap back to whichever button's rect holds the point,
        # like the real pad would.
        self.hold_ms.append(hold_ms)
        for e in helpers.collect_texts(self.tree):
            r = e["rect"]
            if (
                r["x"] <= x <= r["x"] + r["width"]
                and r["y"] <= y <= r["y"] + r["height"]
            ):
                self.tapped.append(e["text"])
                break
        want = config.PHONE_PASSCODE or ""
        if not self.wrong_pin and "".join(self.tapped) == want:
            self.tree = SAMPLE_TREE  # accepted: pad dismissed, home screen


@pytest.fixture(autouse=True)
def _no_leftover_front_app(monkeypatch):
    """A video app one test launched must not arm the video guard (or mark go-ios dead) for the next."""
    monkeypatch.setattr(helpers, "_front_bundle", None)
    monkeypatch.setattr(helpers.capture, "_go_ios_dead_until", 0.0)


@pytest.fixture()
def fast(monkeypatch):
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "PHONE_PASSCODE", "246810")

    def use(stub):
        monkeypatch.setattr(helpers, "_client", stub)
        return stub

    return use


def test_unlock_types_nothing_when_no_pad_appears(fast):
    """Wake + swipe lands somewhere without a pad (phone was just asleep):
    unlock() is done — and must never type the passcode blind."""
    stub = fast(StubPhone(SAMPLE_TREE))
    helpers.unlock()
    assert stub.pressed == ["home"]  # woke the screen
    assert stub.typed == []


def test_unlock_types_the_passcode_in_one_request(fast):
    """On a clean lock screen the pad holds focus and one /wda/keys request
    puts every digit in at once, the near-instant entry. Verified, never
    trusted: the pad leaving the screen is what lets the typed attempt stand,
    and no fallback taps fire."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    helpers.unlock()
    assert stub.typed == ["246810"]
    assert stub.tapped == []


def test_unlock_falls_back_to_taps_when_typed_digits_are_eaten(fast):
    """/wda/keys sends keystrokes to the FOCUSED element, and the pad being on
    screen does not mean the pad holds focus: a lock-screen priority
    notification can keep focus while the pad sits behind it, so all six typed
    digits go into the void and the phone stays locked. The
    pad still up after typing means exactly that: a tap on a digit button
    needs no focus, so the digits go in by taps and unlock still succeeds."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890")), eats_typing=True))
    helpers.unlock()
    assert stub.typed == ["246810"]  # the fast attempt, swallowed
    assert stub.tapped == list("246810")  # the fallback that landed


def test_unlock_taps_key_digits_like_the_real_pad(fast):
    """Pin the device's actual tree shape: the pad digits are Key '1'..'0'.
    The tap path must accept Key, not only Button, or it silently falls back
    to typing."""
    stub = fast(
        StubPhone(_buttons_tree(list("1234567890"), kind="Key"), eats_typing=True)
    )
    helpers.unlock()
    assert stub.tapped == list("246810")


def test_unlock_digit_taps_drop_the_idle_wait_and_restore_it(fast):
    """Each pad tap would otherwise pay the session's waitForIdleTimeout (2s ceiling) plus a
    0.15s sleep: six digits took 4.94s of visible one-finger typing (measured
    on device). The pad is static, so idle settling buys nothing there:
    the burst must run at waitForIdleTimeout 0 and put the configured value
    back afterwards, because the setting rides the shared session everyone
    else gestures on. (Do NOT batch the taps into one /actions request
    instead: six down/up cycles in one pointer source entered
    deterministically WRONG digits, and six parallel pointer sources KILLED
    WDA outright, both observed on device.)"""
    stub = fast(
        StubPhone(_buttons_tree(list("1234567890"), kind="Key"), eats_typing=True)
    )
    helpers.unlock()
    assert stub.tapped == list("246810")
    assert stub.idle_waits == [0, config.WDA_IDLE_WAIT]


def test_unlock_typed_fast_path_also_restores_the_idle_wait(fast):
    """The typed request rides the same waitForIdleTimeout-0 window as the
    taps; a fast-path return must still put the shared session's setting
    back."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    helpers.unlock()
    assert stub.typed == ["246810"]
    assert stub.idle_waits == [0, config.WDA_IDLE_WAIT]


def test_enter_passcode_restores_idle_wait_when_a_tap_raises(fast):
    """The idle-wait restore is a finally, not a happy-path tail: a tap that
    dies mid-entry must still put the shared session's settings back."""

    class Dies(StubPhone):
        def tap(self, x, y, hold_ms=None):
            super().tap(x, y, hold_ms)
            if len(self.tapped) == 2:
                raise WDAError("boom")

    stub = fast(Dies(_buttons_tree(list("1234567890"), kind="Key"), eats_typing=True))
    with pytest.raises(WDAError, match="boom"):
        helpers._enter_passcode(stub, "246810", stub.tree)
    assert stub.idle_waits == [0, config.WDA_IDLE_WAIT]


def test_unlock_pad_gone_check_is_a_bounded_probe_not_a_source(fast):
    """After the last digit tap the phone is visibly unlocked, so unlock() must not
    hold the viewer busy ~5s with a fixed 0.7s sleep plus one full /source
    of the freshly unlocked Home Screen (/source's worst case, 3.0-5.7s
    measured) just to ask "is the pad gone?". A bounded find_first answers
    the same question in 0.11s (no-match, measured on device), so
    the success path must pay exactly ONE full /source: the read that found
    the pad and aimed the digit taps. Holds through the tap fallback too."""
    stub = fast(
        StubPhone(_buttons_tree(list("1234567890"), kind="Key"), eats_typing=True)
    )
    helpers.unlock()
    assert stub.tapped == list("246810")
    assert stub.source_calls == 1
    assert stub.finds >= 1


def test_unlock_falls_back_to_typing_for_alphanumeric_passcode(fast, monkeypatch):
    """An alphanumeric passcode gets a full keyboard, not a pad — there are no
    digit buttons to tap for its letters, so the typing path stays."""
    monkeypatch.setattr(config, "PHONE_PASSCODE", "az2468")
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    helpers.unlock()
    assert stub.typed == ["az2468"]
    assert stub.tapped == []




def test_unlock_digit_taps_are_redacted_in_the_activity_log(fast):
    """A pad tap's coordinates ARE the digit — logged raw they would spell out
    the passcode. Every digit tap must run inside wda_client.redact_actions."""
    class SpyPhone(StubPhone):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.redactions = []

        def tap(self, x, y, hold_ms=None):
            self.redactions.append(getattr(wda_client._REDACT, "label", None))
            super().tap(x, y, hold_ms)

    stub = fast(SpyPhone(_buttons_tree(list("1234567890")), eats_typing=True))
    helpers.unlock()
    assert len(stub.redactions) == 6
    assert all(stub.redactions)


def test_unlock_never_consults_wda_locked(fast):
    """/wda/locked lies (it returns False with the pad on screen).
    unlock() must decide from the screen, never that endpoint."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    stub.is_locked = None  # noqa: vulture  (poison: any call raises TypeError)
    helpers.unlock()
    assert stub.typed == ["246810"]


def test_unlock_locked_without_passcode_raises(fast, monkeypatch):
    """Pad on screen but no PHONE_PASSCODE configured: clear error, no typing."""
    monkeypatch.setattr(config, "PHONE_PASSCODE", None)
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    with pytest.raises(WDAError, match="PHONE_PASSCODE"):
        helpers.unlock()
    assert stub.typed == []


def test_unlock_leaves_foreground_app_alone(fast):
    """An app is frontmost on a LIT screen -> the phone is unlocked and in use.
    The edge swipe would yank the user out of the app; unlock() must not
    gesture at all. Lit-ness is load-bearing here, see the next test."""
    stub = fast(
        StubPhone(SAMPLE_TREE, frame=b"\0" * 200_000, app="com.apple.mobilesafari")
    )
    helpers.unlock()
    assert stub.pressed == []
    assert stub.swipes == 0
    assert stub.typed == []


def test_unlock_wakes_a_phone_that_locked_with_an_app_open(fast):
    """active_app() goes STALE behind a lock. A phone that
    locks with Calculator frontmost keeps answering "Calculator", so unlock()
    would take the "in use, touch nothing" exit and never wake it, and every launch
    afterwards fails with "device was not, or could not be, unlocked". A dark
    screen is the tell: a phone actually in use is a lit one."""

    class LockedBehindApp(StubPhone):
        def swipe(self, *args):
            super().swipe(*args)
            self.tree = _buttons_tree(list("1234567890"))
            self.frame = b"\0" * 200_000  # the wake lit the screen

    stub = fast(LockedBehindApp(SAMPLE_TREE, frame=b"tiny", app="com.apple.calculator"))
    helpers.unlock()
    assert stub.pressed == ["home"]  # it woke the phone instead of giving up
    assert stub.typed == ["246810"]


def test_unlock_survives_active_app_crash_on_lit_lock_screen(fast):
    """/wda/activeAppInfo CRASHES while the lock screen is LIT: WDA answers
    "attempt to insert nil object from objects[2]" (reproduced
    on device: lit frame -> crash, dark frame -> springboard). A priority
    notification keeps the lock screen lit for as long as it shows, so every
    Unlock press during one dies on unlock()'s FIRST call, before a single
    gesture reaches the phone. The crash only happens on the lock screen (a
    real frontmost app answers fine), so it can never mean "in use": unlock()
    must treat it as nothing-frontmost and carry on with the wake."""

    class CrashingActiveApp(StubPhone):
        def active_app(self):
            raise WDAError(
                "GET /wda/activeAppInfo: unknown error: *** "
                "-[__NSPlaceholderDictionary initWithObjects:forKeys:count:]: "
                "attempt to insert nil object from objects[2]"
            )

    # frame is LIT: that is what the notification does, and what made the old
    # code reach active_app() in a state where it blows up.
    stub = fast(
        CrashingActiveApp(_buttons_tree(list("1234567890")), frame=b"\0" * 200_000)
    )
    helpers.unlock()
    assert stub.pressed == ["home"]
    assert stub.typed == ["246810"]


def test_unlock_wrong_pin_raises_and_never_retries(fast):
    """Pad still up after entering the code = wrong PIN (or a lost gesture).
    Bounded and loud — iOS lockout escalates on repeated wrong passcodes, so
    unlock() raises instead of looping. A wrong PIN costs the typed attempt
    plus the one tap fallback (the code cannot tell "wrong PIN" from "digits
    eaten by a notification"); that pair is the ceiling, and the error tells
    the human not to retry."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890")), wrong_pin=True))
    with pytest.raises(WDAError, match="still on screen"):
        helpers.unlock()
    assert stub.typed == ["246810"]  # the fast attempt
    assert stub.tapped == list("246810")  # the one fallback, then it raised


def test_unlock_resummons_pad_when_screen_slept(fast):
    """If the screen went dark during the (slow) pad check, unlock() must wake
    and swipe again before entering the code — taps on a dark screen go
    nowhere."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890")), frame=b"tiny"))
    helpers.unlock()
    assert stub.pressed == ["home", "home"]  # woke twice
    assert stub.typed == ["246810"]


def test_unlock_retries_swipe_when_it_burned_on_a_dark_screen(fast):
    """The first gesture after a deep sleep can block WDA
    20.5s, so the swipe lands after the lock screen re-sleeps: dark screen,
    no pad, and unlock gives up ('the button only wakes my phone'). unlock() must
    spend one more wake+swipe when the screen is dark again after the first."""

    class SleepyPhone(StubPhone):
        def swipe(self, *args):
            super().swipe(*args)
            if self.swipes == 2:  # the second swipe lands on a lit screen
                self.tree = _buttons_tree(list("1234567890"))
                self.frame = b"\0" * 200_000

    stub = fast(SleepyPhone(SAMPLE_TREE, frame=b"tiny"))
    helpers.unlock()
    assert stub.swipes == 2
    assert stub.typed == ["246810"]


def test_unlock_mints_a_fresh_session_before_the_first_gesture(fast):
    """A session that crossed a screen lock keeps answering GETs but its
    first /actions hangs ~16s inside XCTest's snapshot timeout before
    failing point.x != INFINITY (16.23s measured on device),
    long enough for the woken lock screen to re-sleep, so the wake swipe
    burns and unlock runs 30-50s. A fresh session is 0.02s and cannot be
    poisoned: unlock() must mint one BEFORE any gesture rides the old id."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    helpers.unlock()
    assert stub.ops and stub.ops[0] == "mint"
    assert stub.typed == ["246810"]


def test_unlock_never_mints_when_phone_is_in_use(fast):
    """The mint evicts whatever session the viewer and the agent are riding.
    On the in-use early return (lit screen, real frontmost app) unlock()
    touches nothing — including the session."""
    stub = fast(StubPhone(SAMPLE_TREE, app="com.apple.calculator"))
    helpers.unlock()
    assert stub.ops == []


def test_unlock_gives_up_after_two_dark_swipes(fast):
    """Never loop gestures forever at a phone that will not show a pad, but
    say so OUT LOUD. A silent return would make the viewer answer {"ok": true}
    and the MCP tool say "unlocked" over a phone that is still dark: success
    reported, state unknown."""
    stub = fast(StubPhone(SAMPLE_TREE, frame=b"tiny"))
    with pytest.raises(WDAError, match="stayed dark"):
        helpers.unlock()
    assert stub.swipes == 2  # still bounded: two attempts, never a loop
    assert stub.typed == []


def _lock_screen_tree():
    """The lit-but-locked lock screen a priority notification produces
    (device dump): a CoverSheet window, no passcode pad yet."""
    return {
        "type": "Application",
        "rect": {"x": 0, "y": 0, "width": 390, "height": 844},
        "children": [
            {
                "type": "Window",
                "name": "SBCoverSheetWindow",
                "isVisible": "1",
                "rect": {"x": 0, "y": 0, "width": 390, "height": 844},
                "children": [
                    {
                        "type": "Other",
                        "label": "Swipe up to unlock",
                        "isVisible": "1",
                        "rect": {"x": 100, "y": 780, "width": 190, "height": 20},
                    },
                    {
                        "type": "Other",
                        "label": "Locked",
                        "isVisible": "1",
                        "rect": {"x": 170, "y": 60, "width": 50, "height": 20},
                    },
                ],
            }
        ],
    }


def test_on_lock_screen_detects_coversheet():
    assert helpers._on_lock_screen(_lock_screen_tree())
    assert not helpers._on_lock_screen(SAMPLE_TREE)
    assert not helpers._on_lock_screen(_buttons_tree(list("1234567890")))


def test_unlock_does_not_claim_success_on_a_lit_lock_screen(fast):
    """A priority notification keeps the lock screen LIT while still locked, so
    the "lit and no pad, must be awake+usable" shortcut would return
    {ok: true} over a phone still on its lock screen. A lit CoverSheet is not an unlocked phone:
    unlock() must NOT return success, and must raise if the pad never comes."""
    stub = fast(StubPhone(_lock_screen_tree()))  # lit (default 200 KB frame)
    with pytest.raises(WDAError, match="never appeared"):
        helpers.unlock()
    assert stub.swipes == 2  # bounded: tried the second wake+swipe, no loop
    assert stub.typed == [] and stub.tapped == []


def test_unlock_recovers_when_the_second_swipe_finally_raises_the_pad(fast):
    """The pad is behind the notification and the second wake+swipe brings it
    up: unlock() must NOT bail on the first lit-lock-screen read, but retry and
    then enter the passcode."""

    class NotifiedPhone(StubPhone):
        def swipe(self, *args):
            super().swipe(*args)
            if self.swipes == 2:  # second swipe finally raises the pad
                self.tree = _buttons_tree(list("1234567890"))

    # The notification is still up, so it eats the typed digits too: the
    # entry lands via the tap fallback.
    stub = fast(NotifiedPhone(_lock_screen_tree(), eats_typing=True))
    helpers.unlock()
    assert stub.swipes == 2
    assert stub.tapped == list("246810")


def test_unlock_reraises_unrelated_active_app_errors(fast):
    """Only the lit-lock-screen "insert nil object" crash means carry-on. Any
    OTHER WDAError from active_app() (timeout, dead session) leaves the
    phone's state unknown, and swallowing it would Home-press and edge-swipe a
    phone that may be unlocked with an app open.
    It must propagate, and no gesture may fire."""

    class FlakyActiveApp(StubPhone):
        def active_app(self):
            raise WDAError("Cannot reach WebDriverAgent: connection timed out")

    stub = fast(FlakyActiveApp(SAMPLE_TREE, frame=b"\0" * 200_000))
    with pytest.raises(WDAError, match="timed out"):
        helpers.unlock()
    assert stub.pressed == []
    assert stub.swipes == 0


def test_unlock_uses_the_client_it_is_given(fast):
    """The viewer passes its own client (WDA holds one session; a second
    client steals it mid-sequence). unlock(c) must not touch the singleton."""
    singleton = fast(StubPhone(_buttons_tree(list("1234567890"))))
    mine = StubPhone(_buttons_tree(list("1234567890")))
    helpers.unlock(mine)
    assert mine.typed == ["246810"]
    assert singleton.typed == [] and singleton.tapped == []


def test_unlock_scrubs_passcode_from_errors(fast, monkeypatch):
    # Alphanumeric passcode: the typing fallback is the path that can echo
    # the secret back inside a WDA error message.
    monkeypatch.setattr(config, "PHONE_PASSCODE", "az2468")
    err = WDAError("POST /wda/keys: could not type 'az2468'")
    fast(StubPhone(_buttons_tree(list("1234567890")), type_error=err))
    with pytest.raises(WDAError) as exc_info:
        helpers.unlock()
    assert "az2468" not in str(exc_info.value)


def test_unlock_digit_typing_error_raises_scrubbed_and_never_taps(fast):
    """A typing ERROR on the digit fast path is not "digits eaten": a
    timeout's keys may still land, and tapping on top of them would garble
    the attempt toward an iOS lockout. It must raise — scrubbed — with zero
    fallback taps."""
    err = WDAError("POST /wda/keys: could not type '246810'")
    stub = fast(StubPhone(_buttons_tree(list("1234567890")), type_error=err))
    with pytest.raises(WDAError) as exc_info:
        helpers.unlock()
    assert "246810" not in str(exc_info.value)
    assert stub.tapped == []
    assert stub.idle_waits == [0, config.WDA_IDLE_WAIT]  # finally still ran


class CountingClient:
    """WDAClient stand-in that counts /source fetches for the cache tests."""

    def __init__(self, tree=SAMPLE_TREE):
        self.tree = tree
        self.source_calls = 0

    def source(self):
        self.source_calls += 1
        return self.tree

    def tap(self, x, y):
        pass

    def type_text(self, text):
        pass


def _fresh_counting_client(monkeypatch):
    helpers._invalidate_tree()  # cache is module state; start every test clean
    stub = CountingClient()
    monkeypatch.setattr(helpers, "_client", stub)
    return stub


def test_ui_tree_cached_for_consecutive_reads(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    assert helpers.find_text("general")  # fetches
    assert helpers.find_text("bluetooth")  # cache hit, no second fetch
    assert stub.source_calls == 1


def test_actions_invalidate_tree_cache(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    helpers.ui_tree()
    helpers.tap(10, 20)  # the screen may now differ; cache must not be reused
    helpers.ui_tree()
    assert stub.source_calls == 2


def test_type_text_invalidates_tree_cache(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    helpers.ui_tree()
    helpers.type_text("hi")
    helpers.ui_tree()
    assert stub.source_calls == 2


def test_unlock_invalidates_tree_cache(fast):
    stub = fast(StubPhone(SAMPLE_TREE))
    helpers._invalidate_tree()  # cache is module state; start the test clean
    helpers.ui_tree()  # cache the pre-unlock screen
    helpers.unlock()  # polls source() itself; screen changed
    before = stub.source_calls
    helpers.ui_tree()  # must refetch, not reuse the pre-unlock tree
    assert stub.source_calls == before + 1


def test_tree_cache_expires_by_ttl(monkeypatch):
    stub = _fresh_counting_client(monkeypatch)
    helpers.ui_tree()
    monkeypatch.setattr(helpers.time, "monotonic", lambda: helpers.time.time() + 3600)
    helpers.ui_tree()  # the screen can change on its own; a stale tree is unsafe
    assert stub.source_calls == 2


@pytest.fixture()
def fake_clock(monkeypatch):
    """time.sleep advances a fake monotonic clock, so poll loops run instantly."""
    clock = {"t": 0.0}
    monkeypatch.setattr(helpers.time, "monotonic", lambda: clock["t"])

    def sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(helpers.time, "sleep", sleep)
    return clock


class AppearingClient(CountingClient):
    """Tree gains the text 'Target' from the given fetch onward."""

    def __init__(self, appear_at=3):
        super().__init__()
        self.appear_at = appear_at

    def source(self):
        self.source_calls += 1
        if self.source_calls >= self.appear_at:
            return _buttons_tree(["Target"])
        return SAMPLE_TREE


def test_wait_for_text_returns_element_when_it_appears(fake_clock, monkeypatch):
    helpers._invalidate_tree()
    stub = AppearingClient(appear_at=3)
    monkeypatch.setattr(helpers, "_client", stub)
    el = helpers.wait_for_text("Target", timeout=10)
    assert el and el["text"] == "Target"
    assert stub.source_calls >= 3  # each poll dropped the cache and re-read
    assert 0 < fake_clock["t"] < 10  # waited between polls, returned before timeout


def test_wait_for_text_times_out_to_none(fake_clock, monkeypatch):
    helpers._invalidate_tree()
    stub = CountingClient()
    monkeypatch.setattr(helpers, "_client", stub)
    assert helpers.wait_for_text("Never There", timeout=3) is None
    assert fake_clock["t"] >= 3  # gave the full timeout before giving up


class AppSwitchingClient:
    """active_app() reports springboard first, then the target app."""

    def __init__(self, switch_at=2):
        self.calls = 0
        self.switch_at = switch_at

    def active_app(self):
        self.calls += 1
        if self.calls >= self.switch_at:
            return {"bundleId": "com.apple.MobileSMS"}
        return {"bundleId": "com.apple.springboard"}


def test_wait_for_app_true_when_app_arrives(fake_clock, monkeypatch):
    monkeypatch.setattr(helpers, "_client", AppSwitchingClient(switch_at=3))
    assert helpers.wait_for_app("com.apple.MobileSMS", timeout=10) is True
    assert fake_clock["t"] < 10  # returned as soon as the app arrived


def test_wait_for_app_false_on_timeout(fake_clock, monkeypatch):
    monkeypatch.setattr(helpers, "_client", AppSwitchingClient(switch_at=10_000))
    assert helpers.wait_for_app("com.apple.MobileSMS", timeout=2) is False
    assert fake_clock["t"] >= 2  # gave the full timeout before giving up


class BusyTreeClient(CountingClient):
    """source() itself costs `cost` seconds of the fake clock — the whole-tree
    read wait_for_text pays every single turn (0.22s inside an app, 3.0-5.7s on
    the Home Screen, measured)."""

    def __init__(self, clock, cost=1.0):
        super().__init__()
        self.clock, self.cost = clock, cost
        self.reads = []

    def source(self):
        self.reads.append(self.clock["t"])
        self.clock["t"] += self.cost
        return super().source()


def test_wait_for_text_polls_at_a_quarter_second_not_a_half(fake_clock, monkeypatch):
    # Half a second between turns of a poll whose own read is 0.22s is pure
    # tail: the thing appeared, and nobody looked for another 0.5s.
    helpers._invalidate_tree()
    monkeypatch.setattr(helpers, "_client", AppearingClient(appear_at=2))

    assert helpers.wait_for_text("Target", timeout=10)

    assert helpers._TEXT_POLL == 0.25
    assert fake_clock["t"] == pytest.approx(helpers._TEXT_POLL), (
        f"waited {fake_clock['t']}s to take the second look"
    )


def test_wait_for_text_cannot_burst_into_a_slow_tree_read(fake_clock, monkeypatch):
    # Same duty cycle press_home runs: a tree read is the most expensive
    # perception call there is, and WDA serves one request at a time, so a
    # shorter interval must buy looks on a cheap screen without stacking turns
    # onto an expensive one. Rest at least as long as the read took.
    helpers._invalidate_tree()
    stub = BusyTreeClient(fake_clock, cost=1.0)
    monkeypatch.setattr(helpers, "_client", stub)

    assert helpers.wait_for_text("Never There", timeout=10) is None

    gaps = [b - a for a, b in zip(stub.reads, stub.reads[1:])]
    assert gaps and min(gaps) >= 2 * stub.cost, (
        f"reads {stub.reads}: a 1.0s /source polled every {min(gaps)}s spends "
        "more than half the loop inside WDA"
    )


class BusyAppReader(AppSwitchingClient):
    """active_app() that costs `cost` of the fake clock — the wedging call."""

    def __init__(self, clock, cost=1.0):
        super().__init__(switch_at=10_000)  # never arrives
        self.clock, self.cost = clock, cost
        self.reads = []

    def active_app(self):
        self.reads.append(self.clock["t"])
        self.clock["t"] += self.cost
        return super().active_app()


def test_wait_for_app_polls_faster_than_half_a_second(fake_clock, monkeypatch):
    # 0.5s between looks over a 100-156ms active_app() read (measured) is three
    # intervals of nothing on every open_app().
    monkeypatch.setattr(helpers, "_client", AppSwitchingClient(switch_at=2))

    assert helpers.wait_for_app("com.apple.MobileSMS", timeout=10) is True

    assert helpers._APP_POLL == 0.1
    assert fake_clock["t"] == pytest.approx(helpers._APP_POLL), (
        f"waited {fake_clock['t']}s to take the second look"
    )


def test_wait_for_app_cannot_burst_into_a_slow_active_app(fake_clock, monkeypatch):
    # active_app() resolves the active application, which can block with no
    # upper bound in a wedging app. The interval alone does not bound the loop.
    stub = BusyAppReader(fake_clock, cost=1.0)
    monkeypatch.setattr(helpers, "_client", stub)

    assert helpers.wait_for_app("com.apple.MobileSMS", timeout=10) is False

    gaps = [b - a for a, b in zip(stub.reads, stub.reads[1:])]
    assert gaps and min(gaps) >= 2 * stub.cost, (
        f"reads {stub.reads}: a 1.0s active_app polled every {min(gaps)}s"
    )


def test_wait_for_text_returns_inside_its_timeout(fake_clock, monkeypatch):
    # `timeout` is what an MCP agent reads as the bound. The duty-cycle rest
    # can outlast the deadline that gates it — _await_keyboard already clamps
    # the same shape — so a slow tree read stacked a WHOLE extra read past the
    # timeout: on a Home Screen /source (5.7s) a wait_for_text(timeout=10) came
    # back at ~17s. One read of overshoot is unavoidable (the deadline is
    # checked between reads, and giving up early would under-wait the caller);
    # a second one is the rest sleeping straight through the deadline.
    stub = BusyTreeClient(fake_clock, cost=1.0)
    monkeypatch.setattr(helpers, "_client", stub)

    assert helpers.wait_for_text("Never There", timeout=9.5) is None

    assert fake_clock["t"] <= 9.5 + stub.cost, (
        f"returned at t={fake_clock['t']} from a 9.5s timeout: the rest slept "
        "past the deadline and paid for another read"
    )


def test_wait_for_app_returns_inside_its_timeout(fake_clock, monkeypatch):
    # Same clamp, same reason: both are registered MCP tools.
    stub = BusyAppReader(fake_clock, cost=1.0)
    monkeypatch.setattr(helpers, "_client", stub)

    assert helpers.wait_for_app("com.apple.MobileSMS", timeout=9.5) is False

    assert fake_clock["t"] <= 9.5 + stub.cost, (
        f"returned at t={fake_clock['t']} from a 9.5s timeout"
    )


def test_wait_for_polls_keep_their_interval_parameter():
    # Both are registered MCP tools: the schema tracks the real signature, so
    # a caller passing the old interval must still be accepted.
    import inspect

    for fn, default in ((helpers.wait_for_text, 0.25), (helpers.wait_for_app, 0.1)):
        param = inspect.signature(fn).parameters["interval"]
        assert param.default == default, f"{fn.__name__} default is {param.default}"


def test_load_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text('A=1\n# comment\nB = "two"\nbroken line\n', encoding="utf-8")
    assert _load_env(env) == {"A": "1", "B": "two"}


def test_load_env_missing_file(tmp_path):
    assert _load_env(tmp_path / "nope.env") == {}


def test_type_text_refuses_the_passcode(fast):
    stub = fast(StubPhone(SAMPLE_TREE))  # the `fast` fixture sets passcode 246810
    with pytest.raises(WDAError) as exc:
        helpers.type_text("the code is 246810")
    assert "passcode" in str(exc.value).lower()
    assert "246810" not in str(exc.value)  # never echo the secret back
    assert stub.typed == []


def test_type_text_allows_ordinary_text(fast):
    stub = fast(StubPhone(SAMPLE_TREE))
    helpers.type_text("on my way")
    assert stub.typed == ["on my way"]


def test_unlock_still_enters_the_passcode(fast, monkeypatch):
    """The guard is on the public helper; unlock() drives the client directly.
    Digit passcodes type in via the client (pad taps as the fallback); an
    alphanumeric one exercises the plain typing path — both bypass the
    guard."""
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    helpers.unlock()
    assert stub.typed == ["246810"]
    monkeypatch.setattr(config, "PHONE_PASSCODE", "az2468")
    stub = fast(StubPhone(_buttons_tree(list("1234567890"))))
    helpers.unlock()
    assert stub.typed == ["az2468"]


def test_wait_stable_does_not_pay_the_interval_on_an_already_still_screen(
    monkeypatch,
):
    # The sleep sat BEFORE the first comparison, so the earliest possible
    # return was one full interval even when the screen never moved. Callers
    # (scroll_until_found, find_on_home_screen) already paid WDA's own ~0.7s
    # server-side settle before calling this.
    slept = []
    monkeypatch.setattr(helpers.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(helpers.capture, "screenshot_png", lambda: b"same")

    assert helpers.wait_stable(timeout=5.0, interval=0.5) is True
    assert sum(slept) == 0, f"slept {sum(slept)}s on a screen that never moved"


def test_wait_stable_still_waits_out_a_moving_screen(monkeypatch):
    frames = [b"a", b"b", b"c", b"c"]
    monkeypatch.setattr(helpers.time, "sleep", lambda s: None)
    monkeypatch.setattr(helpers.capture, "screenshot_png", lambda: frames.pop(0))

    assert helpers.wait_stable(timeout=5.0, interval=0.01) is True
    assert frames == []


class _SizedClient:
    """Mirrors real WDAClient's session_id attribute (existing test stubs in
    this file lack it), so the memo's session-guard has something to key on."""

    def __init__(self, session_id, wh=(390.0, 844.0), orientation="PORTRAIT"):
        self.session_id = session_id
        self.wh = wh
        self.orient = orientation
        self.calls = 0

    def window_size(self):
        self.calls += 1
        return self.wh

    def orientation(self):
        return self.orient


def _fresh_size_cache(monkeypatch):
    """The memo is module state; a leftover entry would fake a hit or a miss."""
    monkeypatch.setattr(
        helpers, "_size_cache", {"wh": None, "session_id": None, "orientation": None}
    )


def test_window_size_is_memoised_within_a_session(monkeypatch):
    # window_size() is a measured 201ms round trip. scroll(),
    # scroll_until_found(), find_on_home_screen(), read_messages() and unlock()
    # all paid it fresh every time.
    stub = _SizedClient(session_id="abc")
    monkeypatch.setattr(helpers, "client", lambda: stub)
    _fresh_size_cache(monkeypatch)

    assert helpers._window_size() == (390.0, 844.0)
    assert helpers._window_size() == (390.0, 844.0)

    assert stub.calls == 1, f"window_size() was called {stub.calls} times, want 1"


def test_window_size_refetches_after_a_session_change(monkeypatch):
    # A stale (w, h) served across a session change is one half of the
    # regression this guards against; rotation is the other half, below.
    stub = _SizedClient(session_id="abc", wh=(390.0, 844.0))
    monkeypatch.setattr(helpers, "client", lambda: stub)
    _fresh_size_cache(monkeypatch)
    helpers._window_size()

    stub.session_id = "def"
    stub.wh = (428.0, 926.0)
    assert helpers._window_size() == (428.0, 926.0)

    assert stub.calls == 2, f"window_size() was called {stub.calls} times, want 2"


def test_window_size_refetches_after_a_rotation(monkeypatch):
    # /window/size reports the ACTIVE APPLICATION's frame, so width and height
    # swap when the device rotates — the session id cannot see that, and the
    # memo outlives the whole MCP session. Serving a stale landscape 844x390
    # makes unlock() swipe from x=422 on a 390-point-wide portrait lock screen,
    # so the bottom-edge swipe never lands and the pad never appears: exactly
    # the "Unlock did nothing" symptom in docs/ERRORS.md. orientation() is
    # 7.7ms against 201ms, which is why the guard is affordable.
    stub = _SizedClient(session_id="abc", wh=(390.0, 844.0))
    monkeypatch.setattr(helpers, "client", lambda: stub)
    _fresh_size_cache(monkeypatch)
    assert helpers._window_size() == (390.0, 844.0)

    stub.orient = "LANDSCAPE"
    stub.wh = (844.0, 390.0)
    assert helpers._window_size() == (844.0, 390.0), "served a stale portrait size"

    assert stub.calls == 2, f"window_size() was called {stub.calls} times, want 2"


def test_tap_text_out_of_range_index_reports_the_hit_count(monkeypatch):
    # hits[index] raised a bare IndexError with no count, forcing the agent to
    # spend another find_text()/ocr() round trip to learn what it already had.
    monkeypatch.setattr(
        helpers,
        "ocr",
        lambda: [
            {"type": "Button", "text": "Send", "x": 10, "y": 20},
            {"type": "Button", "text": "Send to", "x": 10, "y": 40},
        ],
    )
    with pytest.raises(WDAError) as exc:
        helpers.tap_text("Send", index=5)
    msg = str(exc.value)
    assert "2" in msg, f"error does not name the hit count: {msg}"
    assert "Send" in msg


def test_ui_tree_refetches_when_another_process_touched_the_phone(
    monkeypatch, tmp_path
):
    # The viewer and the MCP server are separate processes. _invalidate_tree()
    # only fires in the calling one, so a human tap in the viewer left the agent
    # reading a stale screen for up to the 2s TTL and tapping the old layout.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    fetches = []

    class FakeClient:
        def source(self):
            fetches.append(1)
            return {"type": "App", "children": []}

    monkeypatch.setattr(helpers, "client", lambda: FakeClient())
    helpers._invalidate_tree()

    helpers.ui_tree()
    helpers.ui_tree()
    assert len(fetches) == 1, "the within-process cache stopped working"

    wda_client.activity_file().parent.mkdir(parents=True, exist_ok=True)
    wda_client.activity_file().write_text("someone else acted\n", encoding="utf-8")

    helpers.ui_tree()
    assert len(fetches) == 2, (
        "another process acted but the cached tree was served anyway"
    )


def test_compact_caps_a_dense_screen_and_says_it_truncated():
    # collect_texts() has no limit, so a long Mail inbox or Settings list costs
    # several times an average screen with nothing stopping it. Silent
    # truncation is worse than none: a clipped screen must not read as complete.
    rows = [
        {
            "text": f"Row {i}",
            "type": "Cell",
            "x": 100,
            "y": 100 + i * 40,
            "rect": {"x": 0, "y": 100 + i * 40, "width": 300, "height": 30},
        }
        for i in range(100)
    ]

    out = helpers.compact(rows, limit=60)
    assert len(out) == 61, "expected 60 rows plus one truncation marker"
    assert out[-1]["type"] == "Truncation"
    assert "40 more" in out[-1]["text"]
    assert "find_text()" in out[-1]["text"]

    # The assertion is real: with no limit nothing is dropped and no marker exists.
    uncapped = helpers.compact(rows, limit=None)
    assert len(uncapped) == 100
    assert all(r["type"] != "Truncation" for r in uncapped)


def test_open_app_error_names_near_miss_app_names(monkeypatch):
    # open_app already walks every installed app; discarding what it saw forces
    # the agent to go and list them itself after a typo.
    monkeypatch.setattr(
        helpers.device,
        "list_apps",
        lambda: [
            {"name": "Messages", "bundle_id": "com.apple.MobileSMS"},
            {"name": "Photos", "bundle_id": "com.apple.mobileslideshow"},
            {"name": "Calendar", "bundle_id": "com.apple.mobilecal"},
        ],
    )
    with pytest.raises(WDAError) as exc:
        helpers.open_app("Mesages")
    msg = str(exc.value)
    assert "Messages" in msg, f"error did not suggest the near match: {msg}"


def test_open_app_launches_an_installed_name_that_carries_a_version(monkeypatch):
    # `ios apps --list` reports "YouTube 21.32.4", and the old dot test called
    # that a bundle id and handed it to iOS verbatim ("Application info provider
    # returned nil"). It is the very string open_app's own "Did you mean" hint
    # suggests, so the suggested fix could not work. Bundle ids still pass through.
    monkeypatch.setattr(
        helpers.device,
        "list_apps",
        lambda: [{"name": "YouTube 21.32.4", "bundle_id": "com.google.ios.youtube"}],
    )
    launched = []
    monkeypatch.setattr(helpers, "client", lambda: _LaunchSpy(launched))

    helpers.open_app("YouTube 21.32.4")
    assert launched == ["com.google.ios.youtube"]

    helpers.open_app("com.burbn.instagram")  # a real bundle id still goes direct
    assert launched[-1] == "com.burbn.instagram"


class _LaunchSpy:
    def __init__(self, sink, frontmost=None):
        self.sink = sink
        # A scripted queue of bundle ids for active_app(); the last one repeats
        # forever, so a wait that never succeeds still terminates on the clock.
        self.frontmost = list(frontmost or [])
        self.app_reads = 0

    def app_launch(self, bundle_id):
        self.sink.append(bundle_id)

    def active_app(self):
        self.app_reads += 1
        if not self.frontmost:
            raise AssertionError("open_app read the foreground without being asked")
        bundle = self.frontmost[0]
        if len(self.frontmost) > 1:
            self.frontmost.pop(0)
        return {"bundleId": bundle}


def test_open_app_does_not_wait_by_default(monkeypatch):
    # viewer.py calls open_app(name) inside _action_slot(), i.e. holding
    # _ACTION_LOCK, and _ACTION_WAIT is 2s: a wait on the default would
    # 409-drop the human's next taps. The default must stay byte-identical —
    # no active_app() read at all — so this spy raises if one happens.
    launched = []
    spy = _LaunchSpy(launched)
    monkeypatch.setattr(helpers, "client", lambda: spy)

    helpers.open_app("com.burbn.instagram")
    assert launched == ["com.burbn.instagram"]
    assert spy.app_reads == 0, "paid a foreground read nobody asked for"


def test_open_app_waits_for_the_foreground_when_asked(fake_clock, monkeypatch):
    # wait_for_app() needs a bundle id that open_app resolves privately and
    # never returns, and inside act() a later step cannot read an earlier
    # step's result — so wait_seconds is the only way to express a
    # foreground-confirmed launch without knowing the bundle id.
    launched = []
    spy = _LaunchSpy(
        launched,
        frontmost=[
            "com.apple.springboard",
            "com.apple.springboard",
            "com.apple.Preferences",
        ],
    )
    monkeypatch.setattr(helpers, "client", lambda: spy)

    assert helpers.open_app("com.apple.Preferences", wait_seconds=5) is None
    assert launched == ["com.apple.Preferences"]
    assert spy.app_reads == 3, f"polled {spy.app_reads} times, not until it arrived"


def test_open_app_raises_when_the_app_never_arrives(fake_clock, monkeypatch):
    # Loud, not a return value: a raise keeps the -> None annotation (so the MCP
    # output schema, viewer.py and four test stubs are untouched) and stops an
    # act() batch here instead of letting the next step tap the previous screen.
    launched = []
    spy = _LaunchSpy(launched, frontmost=["com.apple.springboard"])
    monkeypatch.setattr(helpers, "client", lambda: spy)

    with pytest.raises(WDAError) as exc:
        helpers.open_app("com.apple.Preferences", wait_seconds=5)
    assert "foreground" in str(exc.value), str(exc.value)
    assert launched == ["com.apple.Preferences"], (
        "the launch itself must still have happened: this is a wait failure, "
        "not a launch failure, and the error has to read that way"
    )


def test_open_app_suggests_system_apps_that_ios_apps_list_omits(monkeypatch):
    # `ios apps --list` returns user apps only, so on a real device the pool was
    # empty for a Messages/Settings typo and the agent got no suggestion.
    monkeypatch.setattr(helpers.device, "list_apps", lambda: [])
    with pytest.raises(WDAError) as exc:
        helpers.open_app("Mesages")
    assert "messages" in str(exc.value).lower(), str(exc.value)


# ---- Home Screen position ---------------------------------------------------


class SlowSpringboard:
    """/wda/homescreen returns, but the springboard takes a moment to arrive."""

    def __init__(self, arrives_on=3):
        self.arrives_on, self.checks, self.homed = arrives_on, 0, False

    def home(self):
        self.homed = True

    def active_app(self):
        self.checks += 1
        late = self.homed and self.checks >= self.arrives_on
        return {"bundleId": "com.apple.springboard" if late else "com.apple.calculator"}


def test_press_home_waits_for_the_springboard(monkeypatch):
    """/wda/homescreen is not reliably synchronous: measured, it
    returned in ~50ms with the app still frontmost on two tries of three, the
    springboard arriving ~830ms later. Returning early makes the viewer's second
    Home press read a stale active app and press home again instead of walking,
    and leaves goto_home_page() raising "no PageIndicator"."""
    stub = SlowSpringboard()
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    helpers.press_home()
    assert stub.homed
    assert stub.checks >= 3  # it kept looking instead of trusting the return


def test_press_home_gives_up_rather_than_hanging(fake_clock, monkeypatch):
    """The physical gesture cannot fail, so this must not raise either — but it
    must stay bounded. Callers that need to know check the screen."""
    stub = SlowSpringboard(arrives_on=10_000)  # never
    monkeypatch.setattr(helpers, "_client", stub)
    helpers.press_home()  # returns, does not raise
    assert fake_clock["t"] <= helpers._HOME_DEADLINE + helpers._HOME_POLL


def test_press_home_returns_at_once_when_already_home(monkeypatch):
    stub = SlowSpringboard(arrives_on=1)
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    helpers.press_home()
    assert stub.checks == 1


class StubClipboardClient:
    def __init__(self, clip=""):
        self.clip = clip

    def get_clipboard(self):
        return self.clip

    def set_clipboard(self, text, content_type="plaintext"):
        self.clip = text



def test_helpers_get_and_set_clipboard(monkeypatch):
    stub = StubClipboardClient("initial")
    monkeypatch.setattr(helpers, "_client", stub)
    assert helpers.get_clipboard() == "initial"
    helpers.set_clipboard("updated text")
    assert helpers.get_clipboard() == "updated text"


def test_helpers_set_clipboard_refuses_passcode(monkeypatch):
    stub = StubClipboardClient()
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(config, "PHONE_PASSCODE", "123456")
    with pytest.raises(WDAError, match="Refused"):
        helpers.set_clipboard("my secret is 123456")


# ---- latency: bounded probes instead of whole-tree reads ---------------------


class BusyReader(SlowSpringboard):
    """active_app() itself takes time — the wedging call WDA serves one at a
    time. `cost` seconds of the fake clock per read."""

    def __init__(self, clock, cost=0.2):
        super().__init__(arrives_on=10_000)  # never
        self.clock, self.cost = clock, cost

    def active_app(self):
        self.clock["t"] += self.cost
        return super().active_app()


def test_press_home_cannot_burst_requests_into_a_slow_wda(fake_clock, monkeypatch):
    # The interval alone does not bound the loop: a warm active_app() turns
    # 0.05s into a 40-request burst, and a SLOW one is worse, because
    # active_app resolves the active application — one of the calls that can
    # block with no upper bound in a wedging app, on the path the viewer's
    # Home button drives. Resting at least as long as the read took caps the
    # loop at half its time inside WDA.
    stub = BusyReader(fake_clock, cost=0.2)
    monkeypatch.setattr(helpers, "_client", stub)

    helpers.press_home()

    ceiling = helpers._HOME_DEADLINE / (2 * 0.2) + 2  # +2: the last read overruns
    assert stub.checks <= ceiling, (
        f"{stub.checks} reads in {helpers._HOME_DEADLINE}s at 0.2s each; "
        f"a >=50% duty cycle allows at most {ceiling}"
    )


def test_press_home_polls_fast_inside_a_wall_clock_ceiling(fake_clock, monkeypatch):
    # A 0.25s interval on top of a ~102ms active_app() read is a ~352ms cycle
    # against a recorded ~830ms arrival, so detection landed ~280ms late. The
    # ceiling is wall clock now, so shortening the interval buys looks, not
    # patience: still bounded, still never raises.
    stub = SlowSpringboard(arrives_on=10_000)  # never
    monkeypatch.setattr(helpers, "_client", stub)

    helpers.press_home()  # returns, does not raise

    assert stub.checks >= 20, (
        f"looked {stub.checks} times in ~2s; a 0.05s interval should look far more"
    )
    assert helpers._HOME_POLL == 0.05
    assert fake_clock["t"] <= helpers._HOME_DEADLINE + helpers._HOME_POLL, (
        "the wall-clock ceiling moved when the interval did"
    )


def test_wait_stable_interval_defaults_to_one_round_trip():
    # Two screenshots are a WDA round trip apart (~50-100ms, the docstring's
    # own number), so 0.5s between compares was five intervals of nothing.
    import inspect

    default = inspect.signature(helpers.wait_stable).parameters["interval"].default
    assert default == 0.15


def test_enter_passcode_taps_with_a_short_hold(fast):
    # The pad is static and each tap's 80ms contact is pure scripted wait, but
    # a dropped pad tap burns an iOS lockout attempt — so this path names its
    # hold explicitly instead of riding whatever the client defaults to.
    stub = fast(StubPhone(_buttons_tree(list("1234567890")), eats_typing=True))

    helpers.unlock()

    assert stub.hold_ms == [80] * len(config.PHONE_PASSCODE), (
        f"pad taps asked for hold_ms {stub.hold_ms}"
    )


# ---- video surfaces: no accessibility snapshot while a feed plays --------------------------


def _png(seed: int, noise: bool) -> bytes:
    from io import BytesIO

    from PIL import Image

    img = Image.new("L", (64, 128), 40)
    if noise:
        px = img.load()
        for y in range(128):
            for x in range(64):
                px[x, y] = (x * 7 + y * 13 + seed * 61) % 256
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _guarded(monkeypatch, frames):
    """A CountingClient behind ui_tree, TikTok in front, go-ios frames served from `frames`."""
    stub = _fresh_counting_client(monkeypatch)
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "AX_VIDEO_APPS", frozenset({"com.zhiliaoapp.musically"}))
    monkeypatch.setattr(helpers, "_front_bundle", "com.zhiliaoapp.musically")
    it = iter(frames)
    monkeypatch.setattr(helpers.capture, "_go_ios_screenshot", lambda: next(it))
    return stub


def test_tree_read_refused_while_the_feed_plays(monkeypatch):
    stub = _guarded(monkeypatch, [_png(1, True), _png(2, True)])
    with pytest.raises(helpers.VideoSurfaceError) as err:
        helpers.ui_tree()
    assert "playing video" in str(err.value) and "screenshot()" in str(err.value)
    assert stub.source_calls == 0  # WDA never asked


def test_still_screen_in_a_video_app_reads_the_tree(monkeypatch):
    stub = _guarded(monkeypatch, [_png(1, False), _png(1, False)])
    assert helpers.ui_tree() == SAMPLE_TREE
    assert stub.source_calls == 1


def test_guard_only_looks_at_listed_apps_and_clears_on_home(monkeypatch):
    stub = _guarded(monkeypatch, [_png(1, True), _png(2, True)])
    monkeypatch.setattr(helpers, "_front_bundle", "com.apple.Preferences")  # not listed: no frames taken
    assert helpers.ui_tree() == SAMPLE_TREE
    assert stub.source_calls == 1
    helpers._invalidate_tree()
    monkeypatch.setattr(config, "AX_VIDEO_APPS", frozenset())  # guard off entirely
    monkeypatch.setattr(helpers, "_front_bundle", "com.zhiliaoapp.musically")
    assert helpers.ui_tree() == SAMPLE_TREE
    assert stub.source_calls == 2


def test_open_app_and_press_home_track_the_front_bundle(fast):
    class Launcher(StubPhone):
        def app_launch(self, bundle):
            self.launched = bundle

        def home(self):
            pass

    stub = fast(Launcher(SAMPLE_TREE))
    monkeypatch_front = helpers._front_bundle
    helpers.open_app("com.zhiliaoapp.musically")
    assert helpers._front_bundle == "com.zhiliaoapp.musically" and stub.launched == "com.zhiliaoapp.musically"
    helpers.press_home()
    assert helpers._front_bundle is None
    del monkeypatch_front


def test_frame_comparison_thresholds():
    assert not helpers._frames_moving(_png(1, False), _png(1, False))
    assert helpers._frames_moving(_png(1, True), _png(2, True))


class SettingsClient(CountingClient):
    """Records every /appium/settings write, like the shared session would hold them."""

    def __init__(self):
        super().__init__()
        self.writes = []
        self.fail_restore = False

    def set_settings(self, settings):
        if self.fail_restore and settings.get("snapshotMaxDepth") != 15:
            raise WDAError("link dropped")
        self.writes.append(dict(settings))


def test_media_profile_applies_shallow_no_wait_and_restores(monkeypatch):
    helpers._invalidate_tree()
    stub = SettingsClient()
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(config, "WDA_MEDIA_SNAPSHOT_DEPTH", 15)
    monkeypatch.setattr(config, "WDA_SNAPSHOT_MAX_DEPTH", 0)
    monkeypatch.setattr(config, "WDA_IDLE_WAIT", 2.0)
    monkeypatch.setattr(config, "WDA_ANIM_COOLOFF", 0.0)
    with helpers.media_profile():
        helpers.ui_tree()
    assert stub.writes == [
        {"snapshotMaxDepth": 15, "waitForIdleTimeout": 0, "animationCoolOffTimeout": 0},
        {"snapshotMaxDepth": 50, "waitForIdleTimeout": 2.0, "animationCoolOffTimeout": 0.0},
    ]


def test_media_profile_restores_on_error_and_reports_a_failed_restore(monkeypatch, capsys):
    helpers._invalidate_tree()
    stub = SettingsClient()
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(config, "WDA_MEDIA_SNAPSHOT_DEPTH", 15)
    with pytest.raises(RuntimeError):
        with helpers.media_profile():
            raise RuntimeError("flow failed")
    assert stub.writes[-1]["snapshotMaxDepth"] == 50  # restored despite the error
    stub.fail_restore = True
    with helpers.media_profile(depth=15):
        pass
    assert "media profile not restored" in capsys.readouterr().err


def test_video_in_front_and_note_front_app(monkeypatch):
    frames = iter([_png(1, True), _png(2, True), _png(3, False), _png(3, False)])
    monkeypatch.setattr(helpers.capture, "_go_ios_screenshot", lambda: next(frames))
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    assert helpers.video_in_front() is True
    assert helpers.video_in_front() is False
    helpers.note_front_app("com.zhiliaoapp.musically")
    assert helpers._front_bundle == "com.zhiliaoapp.musically"
    helpers.note_front_app(None)
    assert helpers._front_bundle is None


def test_press_home_leaves_a_video_app_over_usb_without_asking_wda(monkeypatch):
    stub = SlowSpringboard(arrives_on=10_000)  # WDA would never confirm; it must not be asked
    monkeypatch.setattr(helpers, "_client", stub)
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "AX_VIDEO_APPS", frozenset({"com.zhiliaoapp.musically"}))
    launched = []
    monkeypatch.setattr(helpers.device, "foreground_springboard", lambda: launched.append(1) or True)
    helpers.note_front_app("com.zhiliaoapp.musically")
    helpers.press_home()
    assert launched == [1] and not stub.homed and stub.checks == 0
    assert helpers._front_bundle is None
    # go-ios unavailable: fall back to WDA's Home press as before
    monkeypatch.setattr(helpers.device, "foreground_springboard", lambda: False)
    helpers.note_front_app("com.zhiliaoapp.musically")
    helpers.press_home()
    assert stub.homed
    # an app that is not listed never takes the go-ios path
    launched.clear()
    monkeypatch.setattr(helpers.device, "foreground_springboard", lambda: launched.append(1) or True)
    helpers.note_front_app("com.apple.Preferences")
    helpers.press_home()
    assert launched == []


def test_unlock_never_asks_activeappinfo_on_a_lit_playing_screen(monkeypatch):
    # unlock() -> /wda/activeAppInfo with TikTok's feed in front stalls the link.
    import io
    import os as _os
    from PIL import Image

    def frame(seed):
        image = Image.frombytes("RGB", (300, 600), _os.urandom(300 * 600 * 3) if seed else bytes(300 * 600 * 3))
        out = io.BytesIO()
        image.save(out, format="PNG", compress_level=0)
        return out.getvalue()

    class Fake:
        timeout = 999
        def __init__(self):
            self.frames = [frame(1), frame(2)]
        def screenshot(self):
            return self.frames.pop(0) if self.frames else frame(3)
        def active_app(self):
            raise AssertionError("activeAppInfo must not be asked on a playing screen")

    monkeypatch.setattr(helpers, "_front_bundle", None)
    monkeypatch.setattr(helpers, "_LIT_SCREEN_BYTES", 1000)
    monkeypatch.setattr(helpers.time, "sleep", lambda s: None)
    helpers.unlock(Fake())  # returns: in use, nothing asked


def test_default_guard_covers_every_app_with_a_playing_surface():
    # A TikTok-only guard misses the YouTube flow, where the phone can fall off USB.
    # Every video app a flow drives is guarded by default.
    for bundle in ("com.zhiliaoapp.musically", "com.google.ios.youtube", "com.burbn.instagram",
                   "com.burbn.basel", "com.burbn.barcelona", "com.facebook.Facebook"):
        assert bundle in config.VIDEO_APP_BUNDLES


def test_activeappinfo_refused_while_a_video_app_plays(monkeypatch):
    spy = _LaunchSpy([], frontmost=["com.google.ios.youtube"])
    monkeypatch.setattr(helpers, "client", lambda: spy)
    monkeypatch.setattr(helpers.time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "AX_VIDEO_APPS", frozenset({"com.google.ios.youtube"}))
    helpers.note_front_app("com.google.ios.youtube")
    frames = iter([_png(1, True), _png(2, True), _png(3, False), _png(3, False)])
    monkeypatch.setattr(helpers.capture, "_go_ios_screenshot", lambda: next(frames))
    with pytest.raises(helpers.VideoSurfaceError) as err:
        helpers.current_app()
    assert "/wda/activeAppInfo" in str(err.value)
    assert spy.app_reads == 0  # WDA never asked while the trim screen plays
    assert helpers.current_app() == {"bundleId": "com.google.ios.youtube"}  # still: asked once
    assert spy.app_reads == 1


def test_wait_for_app_on_a_playing_video_app_says_so_instead_of_absent(fake_clock, monkeypatch):
    spy = _LaunchSpy([], frontmost=["com.google.ios.youtube"])
    monkeypatch.setattr(helpers, "client", lambda: spy)
    monkeypatch.setattr(config, "AX_VIDEO_APPS", frozenset({"com.google.ios.youtube"}))
    monkeypatch.setattr(helpers, "_video_surface_playing", lambda: True)
    with pytest.raises(helpers.VideoSurfaceError):
        helpers.open_app("com.google.ios.youtube", wait_seconds=4)
    assert spy.sink == ["com.google.ios.youtube"] and spy.app_reads == 0
    # Once the feed's preview stops, the same wait confirms the app as before.
    monkeypatch.setattr(helpers, "_video_surface_playing", lambda: False)
    assert helpers.wait_for_app("com.google.ios.youtube", timeout=4) is True
    assert spy.app_reads == 1
