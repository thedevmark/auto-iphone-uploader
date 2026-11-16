"""Copy a release's video to the iPhone over USB, so Files can open it with no cloud.

The phone driver (WebDriverAgent, signed by the owner) shares its Documents folder
(video_drop/wda_ipa.py turns that on), and Files lists that folder under
On My iPhone › Auto iPhone Uploader. go-ios writes into it over the cable
(``ios fsync --app=<driver> push``, Apple's house_arrest service; paths are relative to
the app's container). The folder keeps one video: copying the next one removes the
others, so the phone never fills up with old clips.

``ios fsync tree`` lists names but no sizes, so the copy's size is proven where it is
used: the Files route checks the exact name and size in the file's row before sharing.
A record of the last copy (name, size, SHA-256) skips copying the same file twice.
Nothing here taps the phone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .source_route import FilesPath
from .wda_ipa import APP_FOLDER

FOLDER = "Documents"
LOCATION = "On My iPhone"


class UsbCopyError(RuntimeError):
    pass


def copy_timeout(size_bytes: int) -> float:
    """Seconds for one push: a minute plus 10 MB/s (USB 2 moves about 30), at most 20 minutes."""
    return min(1200.0, 60.0 + size_bytes / 10_000_000)


def files_path(filename: str) -> FilesPath:
    """Where the copied video shows in Files: On My iPhone › Auto iPhone Uploader › <file>."""
    return FilesPath(LOCATION, (LOCATION,), (APP_FOLDER,), filename)


def parse_tree(output: str) -> set[str]:
    """File names from ``ios fsync tree`` ("|-name" lines; folders end in "/", log lines are JSON)."""
    names = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("|-") and not line.endswith("/"):
            names.add(line[2:])
    return names


def _has_error(output: str) -> bool:
    """go-ios reports a failed fsync as a JSON log line at level ERROR, sometimes with exit code 0."""
    for line in output.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("level") == "ERROR":
            return True
    return False


def _last_message(output: str) -> str:
    for line in reversed(output.strip().splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("level") == "ERROR":
            return str(entry.get("err") or entry.get("msg"))[:200]
    return output.strip().splitlines()[-1][:200] if output.strip() else "no output"


Run = Callable[[list[str], float], subprocess.CompletedProcess]


def _default_run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    # UTF-8: app names in `ios apps` are not all in Windows' default code page.
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=flags)


class UsbCopy:
    def __init__(self, ios: str, bundle: str, record: Path, *, udid: str | None = None, run: Run = _default_run):
        self.ios, self.bundle, self.record, self.udid, self.run_command = ios, bundle, Path(record), udid, run

    def _fsync(self, *args: str, timeout: float = 60.0) -> str:
        command = [self.ios, "fsync", f"--app={self.bundle}", *args]
        if self.udid:
            command.append(f"--udid={self.udid}")
        try:
            done = self.run_command(command, timeout)
        except subprocess.TimeoutExpired as exc:
            raise UsbCopyError(f"the USB copy did not answer within {round(timeout)} s") from exc
        except OSError as exc:
            raise UsbCopyError(f"go-ios could not run: {exc}") from exc
        output = (done.stdout or "") + (done.stderr or "")
        if done.returncode != 0 or _has_error(output):
            raise UsbCopyError(f"go-ios fsync {args[0]} failed: {_last_message(output)}")
        return output

    def listing(self) -> set[str]:
        return parse_tree(self._fsync("tree", f"--path={FOLDER}"))

    def _recorded(self) -> dict:
        try:
            value = json.loads(self.record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def put(self, source: Path, size_bytes: int, sha256: str) -> FilesPath:
        """Copy ``source`` into the shared folder unless this exact file is already there; remove the rest."""
        name = Path(source).name
        if "/" in name or name.startswith("."):
            raise UsbCopyError(f"{name} cannot be copied under that name")
        files = self.listing()
        for other in files - {name}:
            self._fsync("rm", f"--path={FOLDER}/{other}")
        wanted = {"bundle": self.bundle, "name": name, "size": size_bytes, "sha256": sha256}
        if name not in files or self._recorded() != wanted:
            self.record.unlink(missing_ok=True)
            self._fsync("push", f"--srcPath={source}", f"--dstPath={FOLDER}/{name}", timeout=copy_timeout(size_bytes))
            if name not in self.listing():
                raise UsbCopyError(f"{name} is not on the iPhone after the copy")
            self.record.parent.mkdir(parents=True, exist_ok=True)
            self.record.write_text(json.dumps(wanted), encoding="utf-8")
        return files_path(name)


def shares_files(ios: str, bundle: str, *, run: Run = _default_run) -> bool:
    """True when the installed driver's Info.plist turns on file sharing (read with ``ios apps``)."""
    try:
        done = run([ios, "apps"], 40.0)
    except (subprocess.TimeoutExpired, OSError):
        return False
    for line in (done.stdout or "").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        for app in entry if isinstance(entry, list) else [entry]:
            if isinstance(app, dict) and app.get("CFBundleIdentifier") == bundle:
                return app.get("UIFileSharingEnabled") is True
    return False


def driver() -> tuple[str, str] | None:
    """go-ios and the installed driver's bundle id when the driver shares its files, else None."""
    from .phone import device

    try:
        ios, bundle = device.ios_path(), device.detect_wda_bundle()
    except Exception:  # no go-ios or no phone: the cloud route still works
        return None
    if not ios or not bundle or not shares_files(ios, bundle):
        return None
    return ios, bundle


def put_release_video(source: Path, size_bytes: int, sha256: str, state_dir: Path) -> FilesPath | None:
    """Copy the video over USB when the driver shares files; None when this phone cannot take it that way."""
    found = driver()
    if found is None:
        return None
    from .phone import device

    ios, bundle = found
    return UsbCopy(ios, bundle, Path(state_dir) / "usb-copy.json", udid=device.current_udid()).put(source, size_bytes, sha256)
