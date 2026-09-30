"""Agent-facing primitives over WebDriverAgent: perception, gestures, unlock.

Vendored from SideTap (MIT, (c) 2026 Wes Sander) `src/phone_harness/helpers.py`
at upstream 0c75c53; see VENDORED.md. This is the subset the posting scripts
use. Gone: Messages (send/read/thread walking), the send-approval gate and the
`trust` taint tracking (this app never sends on the agent's behalf), Home
Screen paging, text-field editing and the MCP-only cache peek. Kept verbatim:
unlock() and its helpers (six on-device incident fixes live there), the tree
and window-size memos, press_home's springboard wait, open_app's foreground
wait, wait_for_text/wait_for_app's duty cycle, wait_stable and compact().

All coordinates are in points (what the UI tree uses), origin top-left.
"""

from __future__ import annotations

import difflib
import time
from pathlib import Path

from . import capture, config, device
from .wda_client import WDAClient, WDAError, activity_file, redact_actions, runner_bundle

_client: WDAClient | None = None


def client() -> WDAClient:
    global _client
    if _client is None:
        _client = WDAClient()
    return _client


# ---- perception ------------------------------------------------------------


def screenshot(path: str | None = None) -> bytes:
    """PNG of the current screen. Saves to `path` if given, returns the bytes.

    Uses go-ios (no WebDriverAgent needed), so viewing works even before the
    input driver is signed.
    """
    png = capture.screenshot_png()
    if path:
        Path(path).write_bytes(png)
    return png


def screen_info() -> dict:
    """Window size in points; tap coordinates must stay inside this."""
    w, h = _window_size()
    return {"width": w, "height": h, "units": "points"}


# window_size() is a measured 201ms round trip (WDA resolves the ACTIVE
# APPLICATION's frame to answer it), and half a dozen call sites paid it on
# every single scroll/page/read. Memoised here, NOT inside WDAClient: about a
# dozen tests in test_wda_client.py use window_size() as their one-request
# session-heal probe, and caching it at the client would silence exactly the
# round trip those tests assert on.
#
# The guard is session id AND orientation, and the orientation half is not
# optional. It is NOT a screen constant: width and height swap when the device
# rotates, and before this memo existed every call site handled that correctly
# and for free by always asking. Session id alone cannot see a rotation, and
# the memo lives as long as the process — which for the MCP server is the whole
# session. A stale value here is not a slow tap, it is a tap at coordinates off
# the side of the screen: unlock() swipes from x=w/2, so a cached landscape
# 844x390 makes it swipe at x=422 on a 390-point-wide portrait lock screen, the
# bottom-edge swipe never lands, the pad never appears and unlock() raises.
# "The Unlock button did nothing" is a symptom this project has already
# debugged three times (docs/ERRORS.md 2026-08-12, 2026-08-13 x2), and
# find_on_home_screen's page swipe and the screen_info() MCP tool read the same
# value. orientation() costs 7.7ms against 201ms, so the guard keeps ~193ms of
# the saving and gives correctness back.
_size_cache: dict = {"wh": None, "session_id": None, "orientation": None}


def _window_size(c: WDAClient | None = None) -> tuple[float, float]:
    c = c or client()
    # 7.7ms to prove the phone has not rotated under a 201ms memo. Read the
    # session id AFTER it, never before: orientation() is a session request, so
    # it heals an evicted session, and a sid sampled first would still be the
    # dead one — which is exactly what the stale entry is keyed on, so the
    # guard would match itself and serve the stale size.
    orient = c.orientation()
    sid = getattr(c, "session_id", None)
    if (
        _size_cache["wh"] is not None
        and _size_cache["session_id"] == sid
        and _size_cache["orientation"] == orient
    ):
        return _size_cache["wh"]
    wh = c.window_size()
    _size_cache["wh"] = wh
    _size_cache["session_id"] = sid
    _size_cache["orientation"] = orient
    return wh


def collect_texts(node: dict, out: list | None = None) -> list[dict]:
    """Walk a WDA source tree; return visible elements that carry text.

    Pure function (unit-tested). Each hit: {text, x, y, rect, type} where
    x,y is the element center in points.
    """
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    visible = str(node.get("isVisible", "1")) in ("1", "true", "True")
    text = node.get("label") or node.get("name") or node.get("value") or ""
    rect = node.get("rect") or {}
    if visible and text and rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
        out.append(
            {
                "text": str(text),
                "x": rect["x"] + rect["width"] / 2,
                "y": rect["y"] + rect["height"] / 2,
                "rect": rect,
                "type": node.get("type", ""),
            }
        )
    for child in node.get("children") or []:
        collect_texts(child, out)
    return out


# /source serializes the whole tree (~3s/200KB on a busy screen), so back-to-back
# reads reuse one fetch. Any action invalidates; the short TTL bounds staleness
# when the screen changes on its own (animations, notifications).
_TREE_TTL = 2.0
_tree_cache: dict = {"tree": None, "ts": 0.0, "flags": [], "act": 0.0}


def _invalidate_tree() -> None:
    _tree_cache["tree"] = None


def _foreign_activity() -> float:
    """mtime of the shared action log, which EVERY process appends to.

    _invalidate_tree() only fires inside the process that acted, but the viewer
    and the MCP server are separate processes. A human tap in the viewer used to
    leave the agent serving a cached tree for up to the TTL, then tapping the
    coordinates of a screen that had already changed. Every action POST already
    records itself here (wda_client._request), so the mtime is a cross-process
    "something moved" signal for free. Same shared-state-file pattern as the
    WDA session id.
    """
    try:
        return activity_file().stat().st_mtime
    except OSError:
        return 0.0


def ui_tree() -> dict:
    """Raw UI element tree (nested dicts). The precise view of the screen.

    Every text read in this module reaches the screen through here. Upstream
    also marks the session tainted here for its send-approval gate; this app
    never sends messages on the agent's behalf, so that bookkeeping is gone.
    """
    now = time.monotonic()
    act = _foreign_activity()
    if (
        _tree_cache["tree"] is not None
        and now - _tree_cache["ts"] < _TREE_TTL
        and act == _tree_cache["act"]
    ):
        return _tree_cache["tree"]
    tree = client().source()
    _tree_cache.update(tree=tree, ts=time.monotonic(), flags=[], act=act)
    return tree


def ocr() -> list[dict]:
    """All visible on-screen text with center coordinates.

    Name kept from the original harness; here it reads the real UI element
    tree, so results are exact, not OCR guesses.
    """
    return collect_texts(ui_tree())


# ---- action ----------------------------------------------------------------


def tap(x: float, y: float) -> None:
    """Tap at (x, y) in points."""
    _invalidate_tree()
    client().tap(x, y)


def long_press(x: float, y: float, seconds: float = 1.0) -> None:
    """Press and hold at (x, y) in points for `seconds`, then release.

    Opens context menus, the app icon jiggle/rearrange mode, and text selection.
    """
    _invalidate_tree()
    client().long_press(x, y, seconds)


def swipe(x1: float, y1: float, x2: float, y2: float, seconds: float = 0.3) -> None:
    """Drag from (x1, y1) to (x2, y2) in points over `seconds`.

    A raw physical drag: the finger travels exactly the path given, with no
    direction abstraction. To scroll a list, prefer scroll(), which takes a
    named direction and inverts it for you. Use this for edge gestures (swipe
    down from the top edge for Notification Center) and for drag-and-drop.
    """
    _invalidate_tree()
    client().swipe(x1, y1, x2, y2, seconds)


def scroll(direction: str = "down", amount: float = 0.4) -> None:
    """Scroll the screen content. direction: up/down/left/right.

    'down' means see content further down (content moves up).
    """
    w, h = _window_size()
    cx, cy = w / 2, h / 2
    dx = dy = 0.0
    if direction == "down":
        dy = -h * amount
    elif direction == "up":
        dy = h * amount
    elif direction == "left":
        dx = -w * amount
    elif direction == "right":
        dx = w * amount
    else:
        raise ValueError("direction must be up, down, left, or right")
    _invalidate_tree()
    client().swipe(cx, cy, cx + dx, cy + dy, 0.3)


def find_text(text: str, exact: bool = False) -> list[dict]:
    """All elements whose text matches (case-insensitive), best match first.

    Substring match by default; `exact=True` requires the whole text to match.
    Results are ordered exact matches first, then shortest text, so index 0 is
    the least noisy match. That order is what `tap_text(index=N)` selects from.
    """
    needle = text.lower().strip()
    hits = []
    for el in ocr():
        hay = el["text"].lower().strip()
        if (hay == needle) if exact else (needle in hay):
            hits.append(el)
    # exact matches first, then shortest text (least noisy match)
    hits.sort(key=lambda e: (e["text"].lower().strip() != needle, len(e["text"])))
    return hits


def tap_text(text: str, index: int = 0, exact: bool = False) -> dict:
    """Find text on screen and tap it. Returns the element tapped.

    `index` picks from find_text()'s order: exact matches first, then shortest
    text. Use it to disambiguate two controls with the same label. `exact=True`
    narrows the match instead of matching any substring.
    """
    hits = find_text(text, exact=exact)
    if not hits:
        raise WDAError(
            f"Text not found on screen: {text!r}. Call ocr() to see what is visible."
        )
    if index >= len(hits) or index < -len(hits):
        # Name what we already have: a bare IndexError cost the agent another
        # find_text()/ocr() round trip just to learn the count.
        labels = ", ".join(repr(h["text"]) for h in hits[:6])
        raise WDAError(
            f"index {index} is out of range for {text!r}: {len(hits)} match"
            f"{'' if len(hits) == 1 else 'es'} on screen ({labels})."
        )
    el = hits[index]
    tap(el["x"], el["y"])
    return el


def type_text(text: str) -> None:
    """Type into the currently focused text field (tap the field first).

    Refuses to type PHONE_PASSCODE. Nothing the agent legitimately types
    contains it, and an injected instruction must not be able to spend it into
    a note, a search box or a message. unlock() types it straight through the
    client, so unlocking is unaffected.
    """
    if config.PHONE_PASSCODE and config.PHONE_PASSCODE in text:
        raise WDAError(
            "Refused: this text contains your phone passcode. Only unlock() "
            "may type it. If this was not you, an instruction on the phone "
            "screen may have tried to steal it."
        )
    _invalidate_tree()
    client().type_text(text)


def set_clipboard(text: str) -> None:
    """Set the iPhone system clipboard content.

    Refuses text containing PHONE_PASSCODE to prevent accidental leakage.
    """
    if config.PHONE_PASSCODE and config.PHONE_PASSCODE in text:
        raise WDAError(
            "Refused: this text contains your phone passcode. "
            "If this was not you, an instruction on the phone screen may have tried to steal it."
        )
    if runner_bundle() is None:
        # iOS 16+ only lets the FRONTMOST app touch the pasteboard, so the
        # client foregrounds the WDA runner around the write — but only when
        # it knows the runner's bundle id (.state/phone/wda_bundle, written by
        # device.detect_wda_bundle). A fresh state folder has no cache yet,
        # and a bare write answers 200 while setting nothing; fill the cache
        # first so the first caption paste after a migration is not silent.
        device.detect_wda_bundle()
    client().set_clipboard(text)


def get_clipboard() -> str:
    """Read text from the iPhone system clipboard."""
    return client().get_clipboard()


def _duty_rest(started: float, interval: float, deadline: float) -> float:
    """Rest between two polls: never shorter than the read, never past `deadline`.

    The floor keeps a shorter interval from bursting into WDA's one-at-a-time
    queue on an expensive read; the deadline clamp keeps the rest from sleeping
    straight through the timeout it is gated by and buying another whole read.
    """
    rest = max(interval, time.monotonic() - started)
    return max(0.0, min(rest, deadline - time.monotonic()))


_SPRINGBOARD = "com.apple.springboard"
_HOME_POLL = 0.05
# Wall clock, not an attempt count: at a 0.25s interval on top of a ~102ms
# active_app() read the cycle was ~352ms against a recorded ~830ms arrival, so
# detection landed ~280ms late. Bounding the ceiling in seconds means a shorter
# interval buys more looks, not less patience — and the ceiling is the old
# loop's own effective one (8 reads at ~102ms plus 7 x 0.25s), so this is a
# detection win with no patience given up on a path whose recorded failure was
# returning EARLY.
_HOME_DEADLINE = 2.8


def press_home() -> None:
    """Go to the Home Screen, as if the physical Home gesture were used.

    Leaves whatever app was open. It does NOT change which Home Screen page you
    are on: /wda/homescreen is a no-op once you are already on the Home Screen
    (verified on device — two consecutive calls from page 4 both stayed on page
    4). Use goto_home_page() to reach a specific page.

    Waits for the springboard to actually come forward, because /wda/homescreen
    is NOT reliably synchronous: measured 2026-08-12, it returned in ~50ms with
    the app still frontmost on two tries out of three and the springboard
    arrived at ~830ms, while the third call took 1.4s and was done on return.
    Returning early is not a cosmetic problem — the viewer's second Home press
    then read a stale active app and pressed home again instead of walking to
    page 1, and goto_home_page() read no PageIndicator and raised. Bounded and
    silent on timeout: the physical gesture cannot fail, so neither may this;
    callers that need to know check the screen.
    """
    _invalidate_tree()
    c = client()
    c.home()
    deadline = time.monotonic() + _HOME_DEADLINE
    while True:
        started = time.monotonic()
        if c.active_app().get("bundleId") == _SPRINGBOARD:
            return
        if time.monotonic() >= deadline:
            return
        # Rest at least as long as the read took, so the loop can never spend
        # more than half its time inside WDA. active_app() resolves the active
        # application, one of the calls that can block with no upper bound in a
        # wedging app, and this loop is what the viewer's Home button drives —
        # a warm 10ms read would otherwise turn the interval into a 40-request
        # burst into WDA's one-at-a-time queue.
        time.sleep(max(_HOME_POLL, time.monotonic() - started))


BUNDLE_IDS = {
    "settings": "com.apple.Preferences",
    "safari": "com.apple.mobilesafari",
    "messages": "com.apple.MobileSMS",
    "mail": "com.apple.mobilemail",
    "photos": "com.apple.mobileslideshow",
    "camera": "com.apple.camera",
    "notes": "com.apple.mobilenotes",
    "music": "com.apple.Music",
    "app store": "com.apple.AppStore",
    "maps": "com.apple.Maps",
    "calendar": "com.apple.mobilecal",
    "clock": "com.apple.mobiletimer",
    "phone": "com.apple.mobilephone",
    "facetime": "com.apple.facetime",
    "reminders": "com.apple.reminders",
    "files": "com.apple.DocumentsApp",
    "shortcuts": "com.apple.shortcuts",
    "health": "com.apple.Health",
    "wallet": "com.apple.Passbook",
    "calculator": "com.apple.calculator",
    "weather": "com.apple.weather",
}


def current_app() -> dict:
    """Frontmost app info from WDA (bundleId, name, pid)."""
    return client().active_app()


# needs device check: a floor on the poll gap, over a 100-156ms active_app()
# read (measured). 0.5s was three intervals of nothing per launch; the rest is
# the read's own duration, so the loop still cannot outrun WDA (see press_home).
_APP_POLL = 0.1


def wait_for_app(
    bundle_id: str, timeout: float = 10.0, interval: float = _APP_POLL
) -> bool:
    """Poll until `bundle_id` is frontmost. True on success, False on timeout.

    Lets open_app() flows fail fast and loud instead of inferring foreground
    state from wait_stable() timing.

    Rests at least as long as the last read took: active_app() resolves the
    active application, one of the calls that can block with no upper bound in
    a wedging app, so a shorter interval must buy looks and not a burst into
    WDA's one-at-a-time queue. The rest is clamped to the deadline, so `timeout`
    still bounds the call at one read of overshoot rather than two.
    """
    deadline = time.monotonic() + timeout
    while True:
        started = time.monotonic()
        if current_app().get("bundleId") == bundle_id:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_duty_rest(started, interval, deadline))


def _resolve_bundle(name: str) -> str:
    """Friendly name ('Settings'), bundle id, or installed-app name -> bundle id.

    Shared by open_app() and close_app(), so both accept exactly the same
    spellings and raise the same "Did you mean" hint.
    """
    key = name.lower().strip()
    # A dot alone does NOT make it a bundle id: `ios apps --list` reports names
    # with the version attached ("YouTube 21.32.4", "TikTok 46.4.0"), so a bare
    # dot test shipped every one of those straight to app_launch as a bundle id
    # and iOS answered "Application info provider returned nil" — including for
    # the exact name this function's own "Did you mean" hint suggests. Bundle
    # ids are reverse-DNS and never contain spaces; installed names here do.
    looks_like_bundle = "." in name and " " not in name.strip()
    bundle = BUNDLE_IDS.get(key) or (name if looks_like_bundle else None)
    installed: list[str] = []
    if not bundle:
        for app in device.list_apps():
            installed.append(app["name"])
            if key == app["name"].lower().strip():
                bundle = app["bundle_id"]
                break
    if not bundle:
        # We just walked every installed name; hand back the near misses instead
        # of making the agent spend another call to list them. BUNDLE_IDS is in
        # the pool too: `ios apps --list` omits system apps, so without it a typo
        # for Messages or Settings would get no suggestion at all.
        pool = list(dict.fromkeys(installed + list(BUNDLE_IDS)))
        near = difflib.get_close_matches(name, pool, n=3, cutoff=0.6)
        if not near:
            near = difflib.get_close_matches(key, pool, n=3, cutoff=0.6)
        if not near:
            near = [n for n in pool if key in n.lower()][:3]
        hint = (
            f" Did you mean: {', '.join(repr(n) for n in near)}?"
            if near
            else " Check installed names with `ios apps --list`."
        )
        raise WDAError(f"Unknown app {name!r}.{hint}")
    return bundle


def close_app(name: str) -> bool:
    """Force-quit an app: the switcher's swipe-up. Takes the same names as
    open_app(). False when it was not running."""
    _invalidate_tree()
    return device.kill_app(_resolve_bundle(name))


def open_app(name: str, wait_seconds: float = 0.0) -> None:
    """Open an app by friendly name ('Settings'), bundle id, or installed-app name.

    Pass `wait_seconds` to have the launch CONFIRMED before this returns, and
    raise if the app never reached the foreground. The default 0.0 launches and
    returns exactly as before, and must stay 0.0: viewer.py calls this inside
    _action_slot(), so a non-zero default would hold _ACTION_LOCK for the whole
    wait and 409-drop the human's next taps (_ACTION_WAIT is 2s).

    This is the only way to get the correct foreground wait without knowing the
    bundle id: wait_for_app() needs one, open_app resolves it privately, and
    inside act() a later step cannot read an earlier step's result — so a
    batched open-then-look otherwise settles for wait_stable()'s "the screen
    stopped moving", which a launch that bounced back to the Home Screen also
    satisfies.
    """
    bundle = _resolve_bundle(name)
    _invalidate_tree()
    client().app_launch(bundle)
    if wait_seconds > 0 and not wait_for_app(bundle, timeout=wait_seconds):
        # Loud, not a return value: keeping `-> None` leaves the MCP output
        # schema, the viewer's own call and four test stubs untouched, and a
        # raise stops an act() batch here instead of letting the next step tap
        # the previous screen's coordinates.
        raise WDAError(
            f"{name!r} did not reach the foreground within {wait_seconds}s. "
            "Call current_app() to see what did, or retry with a longer wait."
        )


# needs device check: a floor on the poll gap. Every turn here drops the cache
# and re-reads the WHOLE tree, so the interval is pure tail on top of a read
# that already costs 0.22s inside an app and 3.0-5.7s on the Home Screen — the
# rest is the read's own duration, which is what actually paces this loop.
_TEXT_POLL = 0.25


def wait_for_text(
    text: str, timeout: float = 10.0, interval: float = _TEXT_POLL, exact: bool = False
) -> dict | None:
    """Poll until `text` appears on screen; returns the element or None.

    The complement of wait_stable(): that says the screen stopped moving, this
    says the thing you were waiting for actually showed up. The returned
    element carries x/y, so the caller can tap it without re-searching.

    Rests at least as long as the last read took (press_home's duty cycle): a
    tree read is the most expensive perception call there is, so a shorter
    interval buys looks on a cheap screen and cannot burst on an expensive one.
    The rest is clamped to the deadline, so `timeout` still bounds the call at
    one read of overshoot rather than two.
    """
    deadline = time.monotonic() + timeout
    while True:
        started = time.monotonic()
        hits = find_text(text, exact=exact)
        if hits:
            return hits[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(_duty_rest(started, interval, deadline))
        _invalidate_tree()  # never poll the cached tree — read a fresh one


# Gap between two stability compares. One WDA round trip (~50-100ms) is how
# far apart two screenshots have to be to differ mid-animation, which is a
# LOWER bound on this, not proof that 0.15 is enough. needs device check: the
# interval is only paid while the screen is genuinely still moving, and
# scroll_until_found is the one caller where nothing else waits list momentum
# out (WDA_ANIM_COOLOFF=0), so this is the one constant to walk back to the
# 0.5 it shipped with if a scroll starts stopping short.
_STABLE_INTERVAL = 0.15


def wait_stable(timeout: float = 10.0, interval: float = _STABLE_INTERVAL) -> bool:
    """Wait until two consecutive screenshots are identical. True if stable.

    The first comparison happens IMMEDIATELY. Callers reach here straight after
    a gesture that WDA already settled server-side (~0.7s per swipe, measured —
    see config.WDA_ANIM_COOLOFF), so the screen is usually still by the time we
    are asked. Sleeping before the first compare made that common case cost a
    guaranteed extra `interval`, up to 9 times per scroll_until_found and 17
    times per find_on_home_screen. Two screenshots are a WDA round trip apart
    (~50-100ms), which is far enough to differ mid-animation — and that same
    number is why `interval` defaults to 0.15 and not the 0.5s it used to: the
    interval is only paid while the screen is genuinely still moving, so
    anything longer than one round trip is overshoot on the tail.
    """
    prev = capture.screenshot_png()
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = capture.screenshot_png()
        if cur == prev:
            return True
        prev = cur
        time.sleep(interval)
    return False


def _passcode_pad_visible(tree: dict) -> bool:
    """Is the lock-screen passcode pad on screen?

    Pure function (unit-tested). True when the digit pad (buttons 0-9) or a
    "... Passcode" prompt is visible. unlock() must never type the passcode
    without this — on any other screen the digits would land in whatever field
    happens to be focused (a search box, a message...).
    """
    texts = collect_texts(tree)
    # The real pad's digits are Key elements (device dump 2026-08-13); Button
    # stays accepted for older tree shapes. Counting digits matters beyond
    # belt-and-braces: a localized pad has no "passcode" text to match.
    digits = {
        e["text"]
        for e in texts
        if e["type"] in ("Button", "Key") and e["text"].isdigit()
    }
    if len(digits) >= 9:
        return True
    return any("passcode" in e["text"].lower() for e in texts)


def _on_lock_screen(tree: dict) -> bool:
    """True when this tree is the (still-locked) lock screen — CoverSheet.

    unlock()'s "lit and no pad, so it was just asleep and is now usable"
    shortcut assumes a lit screen means unlocked. A PRIORITY NOTIFICATION
    breaks that: it keeps the lock screen LIT while the phone stays locked,
    so when the wake swipe fails to raise the pad (the ~16s wake-transition
    hang, then the notification-lit screen never darkens to trigger the retry)
    the shortcut returned {ok: true} over a phone still on its lock screen
    ("runs ~20s then nothing happens"; Wes 2026-08-20). The lock screen's own
    markers — SBCoverSheetWindow, "Swipe up to unlock", "Locked" — say which
    lit screen this is (device dump 2026-08-20). Same lying-success class as
    the dark-screen silent return fixed 2026-08-13, pointed at the lit case.
    """
    for e in collect_texts(tree):
        t = e["text"]
        if e["type"] == "Window" and "CoverSheet" in t:
            return True
        low = t.lower()
        if "swipe up to unlock" in low or low == "locked":
            return True
    return False


def _pad_digit_probe(pad_tree: dict) -> str:
    """Class chain matching one digit of the pad we are about to tap.

    The post-type "is the pad still on screen" question does not need a tree:
    a bounded find_first answers it in 0.11s where the full /source of the
    freshly unlocked Home Screen — /source's worst case — costs 3.0-5.7s
    (both measured on device 2026-08-14). Probing a digit the pad actually
    showed, by its own element type, keeps the check honest for a
    Button-shaped pad too.
    """
    for e in collect_texts(pad_tree):
        if (
            e["type"] in ("Button", "Key")
            and len(e["text"]) == 1
            and e["text"].isdigit()
        ):
            return f'**/XCUIElementType{e["type"]}[`label == "{e["text"]}"`]'
    # Alphanumeric fallback: the passcode keyboard's keys. Best-effort — the
    # digit pad above is the real device's shape.
    return '**/XCUIElementTypeKey[`label == "5"`]'


def _scrub_secret(message: str, secret: str | None) -> str:
    """Blank a secret out of an error message before it reaches logs/output."""
    return message.replace(secret, "•••") if secret else message


_UNLOCK_TIMEOUT = 45.0  # first gesture after a deep sleep: 20.5s measured live

# Is the display on? PNG size is the cheap probe, and these are the real
# numbers off the device (2026-08-12): display OFF 50 KB, Calculator 245 KB
# (a mostly-black UI, so close to the worst case for a lit app), Home Screen
# 888 KB. 120 KB sits ~2.4x above the dark frame and ~2x below the darkest lit
# screen measured. It is a heuristic, not a lock-state oracle: an app painting
# a near-pure-black full screen could still read as dark. That is the accepted
# residual — the alternative probes either act on the phone (press_button wakes
# by EXITING the app, verified) or come from /wda/locked, which lies.
_LIT_SCREEN_BYTES = 120_000


def _pad_dismissed(c, probe: str) -> bool:
    """True once the pad's digit probe stops matching — the pad left the
    screen. Attempt-counted with a wall-clock cap, same shape as unlock()'s
    pad_appears: tests with a no-op sleep stay instant, and a slow probe
    cannot stretch the check much past ~3s."""
    start = time.monotonic()
    for i in range(8):
        if c.find_first(probe) is None:
            return True
        if i >= 1 and time.monotonic() - start > 3.0:
            break
        time.sleep(0.3)
    return False


def _enter_passcode(c, passcode: str, pad_tree: dict) -> None:
    """Put the passcode in: TYPE it in one request, fall back to pad taps.

    One /wda/keys request enters every digit at once — the near-instant
    entry unlock had before 2026-08-13 — against ~2.8s of visible
    one-finger taps. But /wda/keys goes to the FOCUSED element, and the pad
    being on screen does not mean the pad holds focus: a lock-screen
    priority notification held focus while the pad sat behind it and ate
    all six typed digits (live 2026-08-13). So the typed attempt is never
    trusted: the pad must LEAVE the screen (bounded digit probe, 0.11s —
    never a /source), and a pad still up falls back to TAPPING the digit
    buttons, which need no focus. Eaten digits consume no iOS lockout
    attempt, so the fallback is free in the exact case it exists for; a
    wrong PHONE_PASSCODE now burns two attempts (one typed, one tapped)
    before unlock()'s exit check raises — accepted: that is a persistent
    .env misconfiguration the error names out loud, not a live race.

    Alphanumeric passcodes get a full keyboard instead of the pad, so any
    character without a digit button keeps the plain typing path.
    """
    centers = {}
    for e in collect_texts(pad_tree):
        if (
            e["type"] in ("Button", "Key")
            and len(e["text"]) == 1
            and e["text"].isdigit()
        ):
            centers.setdefault(e["text"], (e["x"], e["y"]))
    if not (passcode and all(ch in centers for ch in passcode)):
        try:
            c.type_text(passcode)
        except WDAError as exc:
            raise WDAError(_scrub_secret(str(exc), passcode)) from None
        return
    # The tap coordinates ARE the digits — keep them out of the live feed.
    with redact_actions("passcode entry"):
        # The pad is static, so idle settling buys nothing: the whole entry
        # runs at waitForIdleTimeout 0 (six taps went 4.94s -> 2.8s measured
        # live 2026-08-14; the typed request rides the same setting). Restore
        # is a finally: the setting rides the SHARED session. Do NOT batch
        # the fallback taps into one /actions request instead: six down/up
        # cycles in one pointer source enter deterministically WRONG digits,
        # and six parallel pointer sources KILL WDA outright (both on device
        # 2026-08-14; docs/ERRORS.md).
        c.set_wait_for_idle(0)
        try:
            try:
                c.type_text(passcode)
            except WDAError as exc:
                # A typing ERROR is not "digits eaten": a timeout's keys may
                # still land, and tapping on top of them garbles the attempt.
                # Raise instead — unlock is one attempt per click by design.
                raise WDAError(_scrub_secret(str(exc), passcode)) from None
            if _pad_dismissed(c, _pad_digit_probe(pad_tree)):
                return  # typed digits landed: unlocked, no taps needed
            for ch in passcode:
                x, y = centers[ch]
                # Name the finger contact instead of riding the client's
                # default: a dropped pad tap burns an iOS lockout attempt,
                # so this path must not silently follow a shorter default
                # tuned for dense app screens.
                c.tap(x, y, hold_ms=80)
        finally:
            try:
                c.set_wait_for_idle(config.WDA_IDLE_WAIT)
            except WDAError:
                pass  # digits are in; a session on eager waits self-heals
                # at the next fresh session, failing here would be a lie


def unlock(c: WDAClient | None = None) -> None:
    """Make the phone usable: wake it and, if the passcode pad comes up, enter
    PHONE_PASSCODE from .env (opt-in). Scrubs the passcode from any error.

    Decides from what is actually on screen — NEVER from /wda/locked, which
    can report unlocked while the pad is on screen (seen live 2026-08-09).
    Pass `c` to reuse an existing client; with the shared session model a
    patient clone adopts the same session instead of stealing it.
    """
    c = c or client()
    # The first gesture after the phone has slept a while can block WDA for
    # 10-20s (measured 20.5s live 2026-08-09). A short-timeout client (the
    # viewer's is 10s) aborts a swipe that is still going to land, so the
    # sequence runs on a patient clone sharing the same session.
    if isinstance(c, WDAClient) and c.timeout < _UNLOCK_TIMEOUT:
        patient = WDAClient(base_url=c.base_url, timeout=_UNLOCK_TIMEOUT)
        patient.session_id = c.session_id
        c = patient
    try:
        frontmost = c.active_app().get("bundleId")
    except WDAError as exc:
        # /wda/activeAppInfo CRASHES while the lock screen is LIT — "attempt
        # to insert nil object from objects[2]" (live 2026-08-13, reproduced:
        # lit lock screen -> crash, dark -> answers springboard). A priority
        # notification keeps the lock screen lit for as long as it shows, so
        # every unlock during one died right here, before the first gesture.
        # The crash only happens on the lock screen — a real frontmost app
        # answers fine — so it cannot mean "in use": carry on with the wake.
        # ONLY that crash, though: any other WDAError (timeout, dead session)
        # leaves the phone's state unknown, and carrying on would Home-press
        # and edge-swipe a phone that may be unlocked with an app open.
        if "insert nil object" not in str(exc):
            raise
        frontmost = None
    if frontmost is not None and frontmost != "com.apple.springboard":
        # ...but only when the phone is genuinely in use. active_app() goes
        # STALE behind a lock: a phone that locked with an app frontmost keeps
        # naming that app until the display wakes, so this return used to
        # refuse the exact state unlock() exists for, and every launch after it
        # failed "device was not, or could not be, unlocked" (bit live
        # 2026-08-12). A lit screen is what "in use" actually means.
        if len(c.screenshot()) >= _LIT_SCREEN_BYTES:
            return  # frontmost app on a lit screen — touch nothing
    # A session that crossed a screen lock is POISONED: it keeps answering
    # GETs but its first /actions hangs ~16s inside XCTest's snapshot timeout
    # before failing "point.x != INFINITY" (16.23s measured on device
    # 2026-08-14 — long enough for the woken lock screen to re-sleep, so the
    # wake swipe burned on a dark screen and unlock ran 30-50s; a priority
    # notification makes this the RELIABLE case by keeping the poisoned
    # session alive). A fresh session is 0.02s (same run), is born after the
    # lock, and cannot be poisoned — mint one instead of discovering the
    # poison mid-wake. Past the in-use return above, so a phone someone is
    # using never gets its session churned; a merely-asleep phone loses a
    # healthy session, which is fine: the new id lands in .state/wda_session,
    # every client adopts it, and the viewer retunes its stream on change.
    c.fresh_session()
    # Deliberately NOT the _window_size() memo: unlock reads this once, and a
    # lock almost always evicts the session, so the memo would miss anyway and
    # the orientation guard would just add a SECOND round trip to the most
    # timing-sensitive path here — the first gesture after a deep sleep blocked
    # WDA 20.5s (measured), and this function already has three ERRORS.md
    # entries. One call, before the wake, so it can't eat awake-time.
    w, h = c.window_size()
    _invalidate_tree()  # about to change the screen, like any other action

    def wake_and_swipe():
        # On a locked phone the bottom-edge swipe summons the passcode pad;
        # on a merely-asleep phone it lands on the home screen. Higher swipe
        # starts scroll the lock-screen notification list instead.
        # The flat sleeps in here stay flat ON PURPOSE — the one path in this
        # file whose waits are not tuned down. Three ERRORS.md entries live on
        # it (burned swipe on a re-slept screen, the ~16s poisoned-session
        # hang, the notification that ate six typed digits) and nothing here
        # can be re-measured without the phone in hand.
        c.press_button("home")  # wake the display
        time.sleep(0.5)
        c.swipe(w / 2, h * 0.98, w / 2, h * 0.30, 0.25)
        time.sleep(1.0)

    pad_tree: dict = {}

    def pad_appears(seconds: float) -> bool:
        # Poll, don't peek once: the pad animates in, and a slow swipe can
        # land well after the call returns. Attempt-counted (not wall-clock)
        # so tests with a no-op sleep stay instant. Keeps the tree that showed
        # the pad: _enter_passcode aims its digit taps with it.
        nonlocal pad_tree
        start = time.monotonic()
        attempts = max(1, int(seconds / 0.4))
        for i in range(attempts):
            pad_tree = c.source()
            if _passcode_pad_visible(pad_tree):
                return True
            if i < attempts - 1:
                # The attempt count assumes a 0.4s /source, but a lock-screen
                # /source can run ~3s, and 7 polls of a screen a burned swipe
                # never changed cost 21s (live 2026-08-14). Wall clock caps
                # the spend; two reads minimum so a pad that animates in
                # after a slow first read is still caught.
                if i >= 1 and time.monotonic() - start > seconds:
                    return False
                time.sleep(0.4)  # flat by design too — see wake_and_swipe above
        return False

    wake_and_swipe()
    if not pad_appears(3.0):
        if len(c.screenshot()) >= _LIT_SCREEN_BYTES and not _on_lock_screen(pad_tree):
            return  # lit and no pad, not the lock screen: was just asleep,
            # now awake+usable. A notification-lit lock screen fails this and
            # falls through to a second wake+swipe instead of a false success.
        # Dark again: a slow swipe landed after the lock screen re-slept and
        # burned on a black screen. One more charge, then stop — endless
        # gesturing at a phone that will not show a pad helps nobody. But
        # stopping is not success: a silent return here made the viewer answer
        # {"ok": true} and the MCP tool say "unlocked" over a still-dark phone.
        wake_and_swipe()
        if not pad_appears(3.0):
            if len(c.screenshot()) >= _LIT_SCREEN_BYTES and not _on_lock_screen(
                pad_tree
            ):
                return  # lit without a pad, not the lock screen: awake+usable
            if _on_lock_screen(pad_tree):
                raise WDAError(
                    "Woke the phone but the passcode pad never appeared — a "
                    "lock-screen notification can hold the swipe. Swipe up on "
                    "the phone to bring up the passcode, then try again."
                )
            raise WDAError(
                "Woke the phone twice but the screen stayed dark and no "
                "passcode pad appeared. Wake it by hand (side button), then "
                "try again."
            )
    if not config.PHONE_PASSCODE:
        raise WDAError(
            "Phone is locked. Set PHONE_PASSCODE in .env or unlock it by hand."
        )
    # The tree fetch above can take seconds when the viewer is streaming, and
    # the lock screen re-sleeps fast — taps on a dark screen go nowhere, so
    # re-probe and wake again if it slept.
    if len(c.screenshot()) < _LIT_SCREEN_BYTES:
        wake_and_swipe()
        pad_tree = c.source()  # the screen was redrawn: re-aim the digit taps
    _enter_passcode(c, config.PHONE_PASSCODE, pad_tree)
    # Success = the pad leaves the screen. This used to be sleep(0.7) plus a
    # full /source of the just-unlocked Home Screen — /source's worst case,
    # 3.0-5.7s measured — so the viewer sat ~5s behind its busy label over a
    # phone that was visibly unlocked (Wes, live 2026-08-14). A bounded probe
    # for one of the pad's own digits answers in 0.11s. Attempt-counted with
    # a wall-clock cap, same shape as pad_appears: tests with a no-op sleep
    # stay instant, and a slow probe cannot stretch the check past ~3s.
    if _pad_dismissed(c, _pad_digit_probe(pad_tree)):
        return  # pad gone: unlocked
    raise WDAError(
        "Typed the passcode but the pad is still on screen — wrong "
        "PHONE_PASSCODE, or the screen slept mid-type. Not retrying "
        "automatically (repeated wrong attempts lock the phone out)."
    )


# ---- screen compaction -------------------------------------------------
# Lives here, not at the MCP boundary, so a CLI-piped script gets the same
# ~64% smaller read the MCP tools get. ocr()/find_text() still return the
# raw list: viewer.py, send_message and the tests need the full tree.
# Wrappers around the whole screen; never a target, never state. `Other` is
# deliberately NOT here: the Home Screen search affordance is an `Other`, so
# dropping the type loses a real tap target. Redundant `Other` containers are
# handled by enclosure and duplicate collapsing below instead.
_NOISE_TYPES = frozenset({"Application", "Window"})
# Types worth keeping when the same text lands twice in the same place.
_ACTIONABLE = frozenset(
    {"Button", "Cell", "Switch", "SearchField", "TextField", "Icon"}
)
# Types that are only ever labels. Anything else may be independently tappable
# — a Switch inside its row is the case that makes a blanket rule unsafe — so
# only these are eligible to be dropped as duplicates of an enclosing element.
_LABEL_TYPES = frozenset({"StaticText", "Image"})


def _encloses(outer: dict, inner: dict) -> bool:
    """True when outer's rect covers inner's and outer is the larger of the two."""
    o, i = outer.get("rect"), inner.get("rect")
    if not o or not i:
        return False
    return (
        o["x"] <= i["x"]
        and o["y"] <= i["y"]
        and o["x"] + o["width"] >= i["x"] + i["width"]
        and o["y"] + o["height"] >= i["y"] + i["height"]
        and o["width"] * o["height"] > i["width"] * i["height"]
    )


def _overlaps(a: dict, b: dict) -> bool:
    """True when the two rects intersect. Rows without geometry never match."""
    ra, rb = a.get("rect"), b.get("rect")
    if not ra or not rb:
        return False
    return not (
        ra["x"] + ra["width"] <= rb["x"]
        or rb["x"] + rb["width"] <= ra["x"]
        or ra["y"] + ra["height"] <= rb["y"]
        or rb["y"] + rb["height"] <= ra["y"]
    )


def _rank(el: dict) -> tuple[bool, float]:
    """Tap-worthiness: an actionable type first, then the larger target."""
    r = el.get("rect") or {}
    return (el.get("type") in _ACTIONABLE, r.get("width", 0) * r.get("height", 0))


def compact(rows: list[dict], limit: int | None = 60) -> list[dict]:
    """Strip a screen read down to what the model can act on.

    Two thirds of a raw read is noise: containers, and a label repeating the
    text of the control that encloses it. Dropping the label is also the safer
    target — tapping the inner StaticText instead of its Button is the classic
    mis-tap. rect goes too; x/y is what a tap needs.
    """
    keep = [r for r in rows if r.get("type") not in _NOISE_TYPES]
    survivors = [
        r
        for r in keep
        if not (
            r.get("type") in _LABEL_TYPES
            and any(
                o is not r and r["text"] in o["text"] and _encloses(o, r) for o in keep
            )
        )
    ]
    # Same text, same place, twice over (a row and its identical twin, repeated
    # scroll-bar chrome): keep whichever is worth tapping, in original order.
    chosen: list[dict] = []
    for r in survivors:
        twin = next(
            (c for c in chosen if c["text"] == r["text"] and _overlaps(c, r)), None
        )
        if twin is None:
            chosen.append(r)
        elif _rank(r) > _rank(twin):
            chosen[chosen.index(twin)] = r
    order = {id(r): i for i, r in enumerate(survivors)}
    chosen.sort(key=lambda r: order[id(r)])
    out = [{k: v for k, v in r.items() if k != "rect"} for r in chosen]
    if limit is not None and len(out) > limit:
        dropped = len(out) - limit
        out = out[:limit]
        # Say so out loud: a silently truncated screen reads as a complete one.
        out.append(
            {
                "text": f"[{dropped} more rows not shown; narrow with find_text()]",
                "type": "Truncation",
                "x": 0,
                "y": 0,
            }
        )
    return out


__all__ = [
    "client",
    "screenshot",
    "screen_info",
    "ocr",
    "ui_tree",
    "collect_texts",
    "tap",
    "long_press",
    "swipe",
    "scroll",
    "find_text",
    "tap_text",
    "type_text",
    "set_clipboard",
    "get_clipboard",
    "compact",
    "press_home",
    "open_app",
    "close_app",
    "current_app",
    "wait_for_app",
    "wait_stable",
    "wait_for_text",
    "unlock",
    "WDAError",
]
