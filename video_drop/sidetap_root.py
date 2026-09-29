"""Locate the SideTap install the phone scripts load.

Windows keeps files written by a packaged app (for example a terminal inside
Codex or Claude) in that app's private storage, so a SideTap installed from
there is invisible at the normal path to every other program.
"""

from __future__ import annotations

import os
from pathlib import Path


def sidetap_root() -> Path:
    configured = os.environ.get("SIDETAP_ROOT", "").strip()
    if configured:
        return Path(configured)
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    default = local / "SideTap"
    for candidate in [default, *sorted(local.glob("Packages/*/LocalCache/Local/SideTap"))]:
        if (candidate / "src" / "phone_harness").is_dir():
            return candidate
    return default
