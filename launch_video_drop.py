"""One-click Windows launcher for Auto iPhone Uploader."""

from __future__ import annotations

import subprocess
import sys
import time
import json
import os
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


def app_window_browsers() -> list[Path]:
    """Edge ships with Windows; Chrome is the fallback. Either can show one page as an app window."""
    places = [os.environ.get(name, "") for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    return [Path(base) / tail for tail in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe")
            for base in places if base]


def open_window() -> None:
    """Open the editor in its own window, with its own taskbar entry; a browser tab if that fails.

    The window keeps a profile under .state, apart from the person's browser, so it opens
    without their tabs or extensions and remembers its own size.
    """
    for browser in app_window_browsers():
        if browser.is_file():
            try:
                subprocess.Popen([str(browser), f"--app={URL}", f"--user-data-dir={ROOT / '.state' / 'app-window'}",
                                  "--no-first-run", "--no-default-browser-check"],
                                 cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
                return
            except OSError:
                continue
    webbrowser.open(URL)


def main(argv: list[str] | None = None) -> None:
    """Start the server (and, through it, the phone link supervisor) and open the editor.

    ``--no-browser`` is what the Startup entry passes: bring the server and the
    link supervisor up at sign-in without opening a browser window.
    """
    if sys.platform != "win32":
        raise SystemExit("Use python -m video_drop.server on this system")
    args = sys.argv[1:] if argv is None else argv
    open_browser = "--no-browser" not in args
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
            raise RuntimeError(f"Auto iPhone Uploader did not start. See {state / 'server.log'}")
    assert_current_server(identity)
    # The server starts the supervisor itself; asking again is idempotent and
    # covers a server that was already up with its supervisor gone.
    from video_drop import link_supervisor

    link_supervisor.ensure_running()
    if open_browser:
        open_window()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "Auto iPhone Uploader", 0x10)
        raise SystemExit(1) from None
