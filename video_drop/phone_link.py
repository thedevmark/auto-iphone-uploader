"""Keep the phone link alive from the posting scripts' side.

The link itself (tunnel, WDA runner, port forwards) is owned by one detached
supervisor (video_drop/link_supervisor.py). Scripts only tell it what they know:
a wedge they just hit (`recover`), and the windows in which the phone is doing
something long and precious (`busy`), so no probe or restart lands on an upload.
No script starts or stops a go-ios process itself: when nothing supervises the
link, `recover` starts the supervisor and asks it, instead of restarting WDA.

Restarting WDA while its test session is still alive makes iOS end that session
and reset the developer tunnel, which drops every connection and invites another
restart. A frozen app (video playing, a provider loading) is released by putting
SpringBoard in front instead; SideTap's own viewer may also be healing, so wait
before restarting.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import link_supervisor

WDA_STATUS = "http://127.0.0.1:8100/status"


def wda_ready(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(WDA_STATUS, timeout=timeout) as response:
            return bool(json.load(response).get("value", {}).get("ready"))
    except (OSError, ValueError):
        return False


def release_frozen_app(ios_path: str | None) -> None:
    if ios_path:
        subprocess.run([ios_path, "launch", "com.apple.springboard"], capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)


def pixels(ios_path: str | None) -> bytes:
    """PNG of the screen through go-ios only. Never asks WebDriverAgent, so a
    playing video (which wedges WDA's snapshot AND its /screenshot) cannot stall it."""
    if not ios_path:
        raise OSError("go-ios is not installed")
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "shot.png"
        subprocess.run([ios_path, "screenshot", "--output", str(out)], capture_output=True, timeout=30,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if not out.is_file() or out.stat().st_size == 0:
            raise OSError("go-ios screenshot failed; is the developer image mounted?")
        return out.read_bytes()


@contextlib.contextmanager
def busy(reason: str, seconds: float, *, linger: float = 0.0, state: Path | None = None):
    """Declare a window in which the supervisor must not probe hard or restart anything.

    ``seconds`` covers the block; ``linger`` keeps the window open after it for
    work the phone finishes on its own (an upload after the final tap).
    """
    state = state or link_supervisor.state_dir()
    link_supervisor.declare_busy(state, reason, seconds)
    try:
        yield
    finally:
        if linger > 0:
            link_supervisor.declare_busy(state, reason, linger)
        else:
            link_supervisor.clear_busy(state)


def wait_ready(timeout: float = 180.0, *, poll: float = 3.0, status=wda_ready, sleep=time.sleep,
               clock=time.monotonic, state: Path | None = None) -> dict:
    """Block until the supervisor reports the link ready and WDA answers, or the timeout passes.

    Scripts call this before their first phone action instead of running
    `phone-harness up` themselves. Returns the last supervisor status; check
    ``["state"] == "ready"``. While no supervisor runs, WDA answering is enough.
    """
    deadline = clock() + timeout
    last: dict = {}
    while True:
        last = link_supervisor.read_status(state)
        supervised = last.get("alive", False)
        if status() and (not supervised or last.get("state") == "ready"):
            return {**last, "state": "ready"}
        if clock() >= deadline:
            return last
        sleep(poll)


def upload_linger(size_bytes: int) -> float:
    """How long to shield the link after a final tap: the app uploads on its own."""
    return 90.0 + size_bytes / 1_000_000 * 1.5


def recover(admin=None, *, status=wda_ready, release=None, sleep=time.sleep, clock=time.monotonic,
            wait: float = 45.0, poll: float = 3.0, supervisor_state: Path | None = None,
            supervised=None, start=None) -> bool:
    """Return True once WDA answers. The last resort is the supervisor's job, never a restart from here.

    First the frozen app is released and WDA given ``wait`` to answer. Then the
    supervisor is asked to look now (started first if nothing supervises the
    link) and given three times that. ``admin`` is accepted and ignored: the
    scripts used to pass SideTap's admin module for a final ``up()`` here, and
    a second process restarting a tunnel the supervisor may be mid-way through
    recovering is exactly the two-healers fight this avoids.
    """
    if status():
        return True
    if release is not None:
        release()
    deadline = clock() + wait
    while clock() < deadline:
        sleep(poll)
        if status():
            return True
    state = supervisor_state or link_supervisor.state_dir()
    if supervised is None:
        supervised = link_supervisor.supervisor_alive(supervisor_state)
    if not supervised:
        (start or link_supervisor.ensure_running)(state)
    link_supervisor.request_recovery(state)
    deadline = clock() + wait * 3
    while clock() < deadline:
        sleep(poll)
        if status():
            return True
    return False
