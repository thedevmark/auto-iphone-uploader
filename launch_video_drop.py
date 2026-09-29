"""One-click Windows launcher for Automated iPhone Social Media Uploads."""

from __future__ import annotations

import subprocess
import sys
import time
import json
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from video_drop.runtime_identity import source_fingerprint


ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:4748/"


def running_server() -> dict | None:
    try:
        with urllib.request.urlopen(URL + "api/runtime", timeout=1) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Port 4748 is already serving an older or different app. "
                           "Restart that server before opening this version.") from exc
    except (OSError, urllib.error.URLError):
        return None
    except ValueError as exc:
        raise RuntimeError("Port 4748 returned an invalid app identity. Restart its server.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Port 4748 returned an invalid app identity. Restart its server.")
    return result


def assert_current_server(identity: dict) -> None:
    root = identity.get("root")
    if not isinstance(root, str) or Path(root).resolve() != ROOT.resolve():
        raise RuntimeError("Port 4748 belongs to another app checkout. Close its server before opening this one.")
    if identity.get("sourceFingerprint") != source_fingerprint(ROOT):
        raise RuntimeError("The app server is running older code. Restart that server to load this update.")


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Use python -m video_drop.server on this system")
    identity = running_server()
    if identity is None:
        python = Path(sys.executable).with_name("pythonw.exe")
        if not python.is_file():
            python = Path(sys.executable)
        state = ROOT / ".state"
        state.mkdir(exist_ok=True)
        log = (state / "server.log").open("a", encoding="utf-8")
        try:
            subprocess.Popen(
                [str(python), "-m", "video_drop.server"],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        finally:
            log.close()
        for _ in range(40):
            identity = running_server()
            if identity is not None:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Automated iPhone Social Media Uploads did not start. See {state / 'server.log'}")
    assert_current_server(identity)
    webbrowser.open(URL)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "Automated iPhone Social Media Uploads", 0x10)
        raise SystemExit(1) from None
