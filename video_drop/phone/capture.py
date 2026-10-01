"""Screen capture: WDA's HTTP screenshot when the session is up, go-ios otherwise.

Vendored whole from SideTap (MIT, (c) 2026 Wes Sander)
`src/phone_harness/capture.py` at upstream 0c75c53; see VENDORED.md.

`ios screenshot` uses the mounted Developer Disk Image's screenshot service,
so perception (viewing, OCR, wait_stable) works with zero app signing — but it
spawns a fresh subprocess + temp file per frame (~100-300ms each on Windows).
Once WDA answers, its GET /screenshot over the already-forwarded :8100
connection is much cheaper, so hot paths like wait_stable() prefer it.
Only touch INPUT needs the signed WDA driver.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import device
from .wda_client import WDAClient, WDAError


class CaptureError(RuntimeError):
    pass


_last_png: bytes | None = None
_last_at: float = 0.0

# One sessionless client for GET /screenshot (it never creates a WDA session,
# so it cannot steal the single session helpers/viewer hold). When WDA is down
# we back off instead of paying a connection error on every frame.
_wda: WDAClient | None = None
_wda_dead_until: float = 0.0
_WDA_RETRY_SECONDS = 10.0


def _wda_screenshot() -> bytes | None:
    """PNG via WDA's HTTP endpoint, or None if WDA is not answering."""
    global _wda, _wda_dead_until
    if time.time() < _wda_dead_until:
        return None
    if _wda is None:
        _wda = WDAClient(timeout=5)
    try:
        return _wda.screenshot()
    except WDAError:
        _wda_dead_until = time.time() + _WDA_RETRY_SECONDS
        return None


def _go_ios_screenshot() -> bytes:
    """PNG via `ios screenshot` (subprocess). Works with zero app signing."""
    exe = device.ios_path()
    if not exe:
        raise CaptureError(device.GO_IOS_MISSING)

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "shot.png"
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [exe, *device.pin_udid(["screenshot", "--output", str(out)])],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=flags,
        )
        if not out.exists() or out.stat().st_size == 0:
            raise CaptureError(
                "go-ios screenshot failed. Is the phone unlocked and the developer "
                f"image mounted? Detail: {proc.stderr.strip()[-300:]}"
            )
        return out.read_bytes()


# go-ios's screenshot service can wedge on the phone while every other service answers
# (measured 2026-10-01 03:25: DTX "Timed out waiting for response" on every call, a tunnel
# refresh did not clear it, WDA's /screenshot answered). After a failure, skip go-ios for
# a while instead of paying its timeout on every frame.
_go_ios_dead_until: float = 0.0
_GO_IOS_RETRY_SECONDS = 60.0


def pixels_png(*, clock=time.time) -> bytes:
    """A screenshot that never asks for an accessibility snapshot: go-ios first, then WDA.

    Both routes are AX-free. WDA's GET /screenshot kept answering on TikTok's playing feed
    (140/140, 2026-10-01 run 2), so it is the fallback whenever go-ios fails.
    """
    global _go_ios_dead_until
    if clock() >= _go_ios_dead_until:
        try:
            return _go_ios_screenshot()
        except (CaptureError, OSError, subprocess.SubprocessError):
            _go_ios_dead_until = clock() + _GO_IOS_RETRY_SECONDS
    png = _wda_screenshot()
    if png is None:
        raise CaptureError("No screenshot: go-ios's screenshot service and WebDriverAgent both failed")
    return png


def screenshot_png(max_age: float = 0.0) -> bytes:  # noqa: vulture  (called from viewer.py/mcp_server.py)
    """Return the current screen as PNG bytes.

    max_age > 0 returns a cached frame if it is younger than max_age seconds,
    which keeps the viewer smooth without hammering the device.
    """
    global _last_png, _last_at
    if max_age and _last_png is not None and (time.time() - _last_at) < max_age:
        return _last_png

    png = _wda_screenshot()
    if png is None:
        png = _go_ios_screenshot()

    _last_png, _last_at = png, time.time()
    return png
