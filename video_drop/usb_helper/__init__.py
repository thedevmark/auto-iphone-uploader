"""Client side of the USB recovery helper: ask SYSTEM for one of two fixed repairs.

The app never runs elevated. Two steps of the pipe-stall recovery ladder need
administrator rights on Windows: restarting Apple Mobile Device Service and
restarting the iPhone's USB device node (``pnputil /restart-device``). Those
run inside a small on-demand scheduled task that the installer registers ONCE,
with the user's consent through UAC (``scripts\\install_windows.ps1
-InstallUsbHelper``). It runs as SYSTEM, has no trigger, and executes a script
that only administrators can write (``usb_helper.ps1`` beside this file is the
source; the installed copy lives under ``%ProgramData%\\AutoIphoneUploader``).

How a request travels (docs/usb-recovery-helper.md has the threat model):

1. This client writes ``requests\\<nonce>.json`` in the helper's folder. The
   file says only ``{"command": "restart-apple-service" | "restart-usb-device",
   "nonce": ..., "issued": ...}``; there is no argument anywhere.
2. It starts the task with ``schtasks /Run``. The task's security descriptor
   lets exactly the installing user start it.
3. The helper (SYSTEM) reads the request, checks that the file is owned by the
   installing user's SID, that its name is a nonce, that it is fresh, and that
   the command is one of its two table entries. It runs the fixed commands,
   writes ``results\\<nonce>.json`` and deletes the request.
4. This client polls for the result for a bounded time and returns it. A
   missing result is *unknown*, never a success: the supervisor verifies every
   step by lockdown answering again, not by this reply.

Everything here is read-only apart from that one request file; ``status()``
is the setup checklist's probe and touches nothing.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

COMMANDS = ("restart-apple-service", "restart-usb-device")
TASK_NAME = r"\AutoIphoneUploader\UsbRecovery"
SCRIPT_NAME = "usb_helper.ps1"
CONFIG_NAME = "config.json"
REQUESTS_DIR = "requests"
RESULTS_DIR = "results"
HELPER_VERSION = 1
SOURCE_SCRIPT = Path(__file__).resolve().parent / SCRIPT_NAME
INSTALL_HINT = ("run  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\install_windows.ps1 -InstallUsbHelper  "
                "in the app folder and accept the administrator prompt once")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def helper_root() -> Path:
    base = os.environ.get("ProgramData") or os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
    return Path(base) / "AutoIphoneUploader" / "usb-helper"


def _schtasks(args: list[str], timeout: float = 20.0) -> tuple[int, str]:
    """Run schtasks.exe (fixed arguments only) and return (returncode, combined output)."""
    try:
        proc = subprocess.run(["schtasks", *args], capture_output=True, text=True, timeout=timeout,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def status(root: Path | None = None, run: Callable[[list[str]], tuple[int, str]] = _schtasks) -> dict:
    """Read-only: is the helper installed on this PC.

    ``{"supported", "installed", "script", "task", "version", "userSid", "detail"}``.
    ``installed`` needs both the SYSTEM-side script and the registered task.
    """
    if sys.platform != "win32":
        return {"supported": False, "installed": False, "script": False, "task": False, "version": None,
                "userSid": "", "detail": "Windows only"}
    root = root or helper_root()
    script = (root / SCRIPT_NAME).is_file()
    version = None
    user_sid = ""
    try:
        config = json.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
        version = int(config.get("version") or 0) or None
        user_sid = str(config.get("userSid") or "")
    except (OSError, ValueError, TypeError):
        pass
    code, output = run(["/Query", "/TN", TASK_NAME])
    task = code == 0
    parts = []
    if not script:
        parts.append("helper script missing")
    if not task:
        parts.append("scheduled task missing")
    if script and task and version != HELPER_VERSION:
        parts.append(f"installed version {version}, app expects {HELPER_VERSION}")
    return {"supported": True, "installed": script and task, "script": script, "task": task, "version": version,
            "userSid": user_sid, "detail": "; ".join(parts) or "installed"}


def request(command: str, *, root: Path | None = None, run: Callable[[list[str]], tuple[int, str]] = _schtasks,
            wait: float = 45.0, poll: float = 1.0, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Ask the helper for one fixed command and wait up to ``wait`` seconds for its result.

    Returns ``{"ok": True | False | None, "command", "detail", "ms", "result"}``.
    ``ok`` is None when the helper started but no result arrived in time: the
    caller must verify the outcome itself (the supervisor probes lockdown).
    """
    if command not in COMMANDS:
        raise ValueError(f"unknown helper command {command!r}; allowed: {COMMANDS}")
    started = clock()
    root = root or helper_root()
    if not (root / SCRIPT_NAME).is_file():
        return {"ok": False, "command": command, "detail": f"USB recovery helper not installed: {INSTALL_HINT}",
                "ms": 0, "result": None}
    nonce = secrets.token_hex(8)
    request_file = root / REQUESTS_DIR / f"{nonce}.json"
    result_file = root / RESULTS_DIR / f"{nonce}.json"
    try:
        request_file.write_text(json.dumps({"command": command, "nonce": nonce, "issued": time.time()}),
                                encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "command": command, "detail": f"could not write the helper request: {exc}", "ms": 0,
                "result": None}
    code, output = run(["/Run", "/TN", TASK_NAME])
    if code != 0:
        try:
            request_file.unlink()
        except OSError:
            pass
        return {"ok": False, "command": command, "detail": f"could not start the helper task: {output[:200]}",
                "ms": int((clock() - started) * 1000), "result": None}
    deadline = started + wait
    while True:
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("nonce") == nonce:
            return {"ok": bool(data.get("ok")), "command": command, "detail": str(data.get("detail") or ""),
                    "ms": int((clock() - started) * 1000), "result": data}
        if clock() >= deadline:
            return {"ok": None, "command": command, "detail": f"helper started but gave no result within {wait:g}s",
                    "ms": int((clock() - started) * 1000), "result": None}
        sleep(poll)
