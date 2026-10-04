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
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from . import link_supervisor

WDA_STATUS = "http://127.0.0.1:8100/status"

# ---- "is a video surface in front?" -----------------------------------------------------
# Two go-ios screenshots a few hundred milliseconds apart: a playing video changes a large
# share of the pixels in the main region, a static screen changes almost none. Scripts ask
# this before any WebDriverAgent tree read, because an accessibility snapshot on a video
# surface is the documented trigger of the wedge (appium/WebDriverAgent#1210) and of the
# pipe stall that followed it here (docs/link-root-cause.md). Reading pixels never touches
# WDA, so the check itself cannot wedge anything.
#
# TODO-to-measure (the experiment agent owns the numbers; these are starting points):
#   VIDEO_CHANGE_FRACTION  fraction of sampled pixels that must change to call it video. A
#                          static screen with a blinking cursor or a spinner measures well
#                          under 0.01; a playing feed should measure 0.10-0.60. 0.08 is the
#                          bootstrap; set it from a recording of each app's video and static
#                          screens (scripts/record_screen.py) before trusting it on the post path.
#   VIDEO_PIXEL_DELTA      grey-level change (0-255) that counts one pixel as changed; 24 ignores
#                          JPEG-free PNG noise and subtle UI fades.
#   VIDEO_REGION           the main region as fractions (left, top, right, bottom): skips the status
#                          bar and the tab/home bar, whose clocks and indicators change on their own.
#   VIDEO_GAP_SECONDS      target gap between the two frames; go-ios itself takes 100-300 ms per
#                          frame on Windows, so the measured gap (reported) is longer.
VIDEO_CHANGE_FRACTION = 0.08
VIDEO_PIXEL_DELTA = 24
VIDEO_REGION = (0.0, 0.10, 1.0, 0.88)
VIDEO_GAP_SECONDS = 0.3
VIDEO_SAMPLE = 4  # every Nth pixel in each direction (the diff is 16x cheaper; motion is not that fine)


@dataclass(frozen=True)
class VideoCheck:
    video: bool  # True: treat the screen as a video surface (no WDA tree reads; pixels and taps only)
    changed: float  # fraction of sampled main-region pixels that changed between the two frames
    threshold: float
    gap_ms: int  # measured gap between the two frames
    elapsed_ms: int  # the whole check, both screenshots included
    size: tuple[int, int]  # frame size in pixels (0, 0 when the frames differed in size)


def changed_fraction(before: bytes, after: bytes, *, region=VIDEO_REGION, pixel_delta: int = VIDEO_PIXEL_DELTA,
                     sample: int = VIDEO_SAMPLE) -> float:
    """Share of main-region pixels whose grey level moved by more than ``pixel_delta`` between two PNGs.

    Frames of different sizes (an orientation change mid-check) count as fully changed.
    """
    from PIL import Image, ImageChops

    first = Image.open(BytesIO(before)).convert("L")
    second = Image.open(BytesIO(after)).convert("L")
    if first.size != second.size:
        return 1.0
    width, height = first.size
    box = (int(width * region[0]), int(height * region[1]), int(width * region[2]), int(height * region[3]))
    first, second = first.crop(box), second.crop(box)
    if sample > 1:
        first = first.reduce(sample)
        second = second.reduce(sample)
    total = first.size[0] * first.size[1]
    if total == 0:
        return 0.0
    histogram = ImageChops.difference(first, second).histogram()
    return sum(histogram[pixel_delta + 1:]) / total


def video_in_front(ios_path: str | None, *, threshold: float = VIDEO_CHANGE_FRACTION, gap: float = VIDEO_GAP_SECONDS,
                   region=VIDEO_REGION, pixel_delta: int = VIDEO_PIXEL_DELTA, grab=None, sleep=time.sleep,
                   clock=time.monotonic) -> VideoCheck:
    """Is a video playing on the screen in front? Two go-ios screenshots, no WebDriverAgent.

    ``grab`` (default ``pixels``) is injected so the rule is testable without a phone. Use it
    on the surfaces that wedge WDA (TikTok feed/editor, Reels, YouTube Shorts, autoplaying
    feeds): when ``.video`` is True, drive by coordinates and read pixels; never ``ui_tree()``.
    """
    grab = grab or (lambda: pixels(ios_path))
    started = clock()
    first = grab()
    shot_at = clock()
    remaining = gap - (shot_at - started)
    if remaining > 0:
        sleep(remaining)
    second_at = clock()
    second = grab()
    changed = changed_fraction(first, second, region=region, pixel_delta=pixel_delta)
    from PIL import Image

    try:
        size = Image.open(BytesIO(first)).size
    except OSError:
        size = (0, 0)
    return VideoCheck(video=changed >= threshold, changed=round(changed, 4), threshold=threshold,
                      gap_ms=int((second_at - started) * 1000), elapsed_ms=int((clock() - started) * 1000),
                      size=size)


def wda_ready(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(WDA_STATUS, timeout=timeout) as response:
            return bool(json.load(response).get("value", {}).get("ready"))
    except (OSError, ValueError):
        return False


UPLOADS_FILE = "uploads-pending.json"


def note_upload(bundle: str, seconds: float, state: Path | None = None) -> None:
    """Mark an app's upload as possibly still running for `seconds`: nothing force-quits it then."""
    state = state or link_supervisor.state_dir()
    path = state / UPLOADS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[bundle] = time.time() + seconds
    state.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def clear_upload(bundle: str, state: Path | None = None) -> None:
    note_upload(bundle, 0, state)


def upload_pending(bundle: str, state: Path | None = None, now: float | None = None) -> bool:
    state = state or link_supervisor.state_dir()
    try:
        until = float(json.loads((state / UPLOADS_FILE).read_text(encoding="utf-8")).get(bundle, 0))
    except (OSError, ValueError, TypeError):
        return False
    return until > (now if now is not None else time.time())


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
    link) and given three times that. ``admin`` is accepted and ignored: a final
    ``up()`` from SideTap's admin module would be a second process restarting a tunnel
    the supervisor may be mid-way through recovering, which is exactly the two-healers
    fight this avoids.
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
