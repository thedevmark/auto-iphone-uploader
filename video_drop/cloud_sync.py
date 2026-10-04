"""Can the phone reach the finished video through OneDrive yet?

The phone apps pick the video from the OneDrive app, so the PC's copy must be
uploaded before a run touches the phone. On 2026-10-03 OneDrive was not
running after a restart, the video never uploaded, and YouTube stopped on the
phone at "No Results". This check refuses the run on the PC instead, with a
plain reason, before anything on the phone changes.

Windows reports a synced file's state through the shell property
System.StorageProviderState. Values (propkey.h, STORAGE_PROVIDER_STATE):
0 none, 1 online-only, 2 in sync, 3 pinned (always on this device),
4 pending upload, 5 pending download, 6 transferring, 7 error, 8 warning,
9 excluded, 10 pending. Only 1, 2 and 3 mean the cloud has the file. An empty
value means OneDrive is not answering for that folder.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

# The cloud copy exists: online-only, in sync, or pinned.
CLOUD_HAS_IT = {"1", "2", "3"}
STATE_WORDS = {
    "4": "still uploading", "5": "still downloading", "6": "still transferring", "7": "in a sync error",
    "8": "showing a sync warning", "9": "excluded from sync", "10": "waiting to sync",
}


def onedrive_roots() -> list[Path]:
    """Folders OneDrive syncs on this PC (personal and work)."""
    roots = []
    for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    return roots


def in_onedrive(path: Path, roots: list[Path] | None = None) -> bool:
    resolved = Path(path).resolve()
    for root in onedrive_roots() if roots is None else roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def onedrive_running() -> bool:
    if sys.platform != "win32":
        return True
    proc = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OneDrive.exe", "/NH", "/FO", "CSV"],
                          capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return proc.stdout.strip().lower().startswith('"onedrive.exe"')


def sync_state(path: Path) -> str:
    """The file's System.StorageProviderState as text ("" when OneDrive gives none)."""
    if sys.platform != "win32":
        return "2"
    path = Path(path)
    folder = str(path.parent).replace("'", "''")
    name = path.name.replace("'", "''")
    script = (f"$f=(New-Object -ComObject Shell.Application).NameSpace('{folder}').ParseName('{name}');"
              "if ($f) { $f.ExtendedProperty('System.StorageProviderState') }")
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    return proc.stdout.strip()


def refusal(path: Path, *, roots: list[Path] | None = None, running: Callable[[], bool] = onedrive_running,
            state: Callable[[Path], str] = sync_state) -> str | None:
    """Why a run must not start yet for this video, or None. Videos outside OneDrive pass."""
    path = Path(path)
    if not in_onedrive(path, roots):
        return None
    if not running():
        return (f"OneDrive isn't running on this PC, so the phone can't find {path.name}. "
                "Start OneDrive, wait until it shows the video as up to date, then try again. Nothing was posted.")
    value = state(path)
    if value in CLOUD_HAS_IT:
        return None
    if value == "":
        return (f"OneDrive isn't reporting {path.name} yet, so the phone may not find it. "
                "Wait until OneDrive shows the video as up to date, then try again. Nothing was posted.")
    return (f"{path.name} is {STATE_WORDS.get(value, 'not synced')} in OneDrive, so the phone can't find it yet. "
            "Wait until OneDrive shows it as up to date, then try again. Nothing was posted.")
