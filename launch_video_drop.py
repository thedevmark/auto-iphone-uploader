"""One-click Windows launcher for the local Video Drop editor."""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:4748/"


def server_is_ready() -> bool:
    try:
        with urllib.request.urlopen(URL, timeout=1) as response:
            return response.status == 200 and b"<title>Video Drop</title>" in response.read(2048)
    except (OSError, urllib.error.URLError):
        return False


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Use python -m video_drop.server on this system")
    if not server_is_ready():
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
            if server_is_ready():
                break
            time.sleep(0.25)
        else:
            raise SystemExit(f"Video Drop did not start. See {state / 'server.log'}")
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
