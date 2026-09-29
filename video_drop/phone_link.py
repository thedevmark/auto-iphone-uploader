"""Recover the SideTap phone link without restarting a live WebDriverAgent session.

Restarting WDA while its test session is still alive makes iOS end that session and
reset the developer tunnel, which drops every connection and invites another restart.
A frozen app (video playing, a provider loading) is released by putting SpringBoard in
front instead; SideTap's own viewer may also be healing, so wait before restarting.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request

WDA_STATUS = "http://127.0.0.1:8100/status"


def wda_ready(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(WDA_STATUS, timeout=timeout) as response:
            return bool(json.load(response).get("value", {}).get("ready"))
    except (OSError, ValueError):
        return False


def release_frozen_app(ios_path: str | None) -> None:
    if ios_path:
        subprocess.run([ios_path, "launch", "com.apple.springboard"], capture_output=True, timeout=30)


def recover(admin, *, status=wda_ready, release=None, sleep=time.sleep, clock=time.monotonic,
            wait: float = 45.0, poll: float = 3.0) -> bool:
    """Return True once WDA answers. Restarts WDA at most once, and only as a last resort."""
    if status():
        return True
    if release is not None:
        release()
    deadline = clock() + wait
    while clock() < deadline:
        sleep(poll)
        if status():
            return True
    return admin.up() == 0
