"""Conservative local export watcher. It never copies or publishes media."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .core import Store, utc_now

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
PREMIERE_INTERMEDIATE = re.compile(r"^.+\.\d+\.\d+\.m4v$", re.IGNORECASE)
SETTLE_SECONDS = 30
POLL_SECONDS = 3


def eligible(path: Path) -> bool:
    return (path.suffix.lower() in VIDEO_EXTENSIONS and not path.name.startswith((".", "~"))
            and not PREMIERE_INTERMEDIATE.fullmatch(path.name)
            and not path.is_symlink() and path.is_file())


def readable_without_writer(path: Path) -> bool:
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    open_file = ctypes.windll.kernel32.CreateFileW
    open_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                          wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    open_file.restype = wintypes.HANDLE
    handle = open_file(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def complete_video(path: Path) -> bool:
    """Require readable metadata and a full decode, then verify the file did not change."""
    try:
        before = path.stat()
        if before.st_size == 0 or not readable_without_writer(path):
            return False
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                               capture_output=True, timeout=30)
        if probe.returncode:
            return False
        data = json.loads(probe.stdout)
        if not any(stream.get("codec_type") == "video" for stream in data.get("streams", [])):
            return False
        if float(data.get("format", {}).get("duration", 0)) <= 0:
            return False
        decode = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                                 "-map", "0:v", "-map", "0:a?", "-f", "null", "-"],
                                capture_output=True, timeout=1800)
        after = path.stat()
        return (decode.returncode == 0 and before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns and readable_without_writer(path))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return False


class WatchFolder:
    def __init__(self, state: Path, on_import):
        self.state = state
        self.on_import = on_import
        self.pending: dict[str, tuple[int, int, float]] = {}
        self.retry_after: dict[str, float] = {}

    def configuration(self) -> dict:
        with Store(self.state / "video-drop.sqlite") as store:
            row = store.db.execute("SELECT value FROM setting WHERE key='watch_folder'").fetchone()
        return json.loads(row[0]) if row else {"enabled": False, "path": ""}

    def status(self) -> dict:
        config = self.configuration()
        problem = ""
        fix = ""
        if config["enabled"] and not Path(config["path"]).is_dir():
            problem = "Folder unavailable"
            fix = "Reconnect the drive or choose the export folder again."
        elif config["enabled"] and (not shutil.which("ffmpeg") or not shutil.which("ffprobe")):
            problem = "ffmpeg and ffprobe are required"
            fix = "Install FFmpeg with ffprobe, add both to PATH, then restart Video Drop."
        return {**config, "problem": problem, "fix": fix}

    def configure(self, folder: Path | None = None, enabled: bool | None = None) -> dict:
        old = self.configuration()
        if folder is not None:
            folder = folder.expanduser().resolve(strict=True)
            if not folder.is_dir():
                raise ValueError("Choose an existing folder")
        path = str(folder) if folder is not None else old["path"]
        active = bool(enabled) if enabled is not None else bool(old["enabled"])
        if active and not path:
            raise ValueError("Choose a folder before turning on Watch folder")
        if active and not Path(path).is_dir():
            raise ValueError("Watch folder is missing")
        changed = path != old["path"] or (active and not old["enabled"])
        self.pending.clear()
        self.retry_after.clear()
        with Store(self.state / "video-drop.sqlite") as store, store.db:
            if changed:
                store.db.execute("DELETE FROM watch_file")
                for item in Path(path).iterdir():
                    if eligible(item):
                        info = item.stat()
                        store.db.execute("INSERT INTO watch_file(path,size,mtime_ns,state,updated_at) VALUES(?,?,?,?,?)",
                                         (str(item.resolve()), info.st_size, info.st_mtime_ns, "existing", utc_now().isoformat()))
            value = {"path": path, "enabled": active}
            store.db.execute("INSERT INTO setting(key,value) VALUES('watch_folder',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (json.dumps(value),))
        return value

    def scan(self, now: float | None = None) -> list[int]:
        config = self.configuration()
        if not config["enabled"]:
            self.pending.clear()
            return []
        folder = Path(config["path"])
        if not folder.is_dir():
            return []
        now = time.monotonic() if now is None else now
        imported = []
        with Store(self.state / "video-drop.sqlite") as store:
            recorded = {row["path"]: row for row in store.db.execute("SELECT * FROM watch_file")}
        for item in sorted(folder.iterdir()):
            if not eligible(item):
                continue
            path = str(item.resolve())
            try:
                info = item.stat()
            except OSError:
                continue
            recorded_file = recorded.get(path)
            if recorded_file and (recorded_file["size"], recorded_file["mtime_ns"]) == (info.st_size, info.st_mtime_ns):
                self.pending.pop(path, None)
                continue
            signature = (info.st_size, info.st_mtime_ns)
            prior = self.pending.get(path)
            if not prior or prior[:2] != signature:
                self.pending[path] = (*signature, now)
                continue
            if now - prior[2] < SETTLE_SECONDS or now < self.retry_after.get(path, 0):
                continue
            if not complete_video(item):
                self.retry_after[path] = now + SETTLE_SECONDS
                continue
            if self.configuration() != config:
                continue
            try:
                with Store(self.state / "video-drop.sqlite") as store:
                    release = store.import_file(item)
                    imported.append(release["id"])
                    with store.db:
                        store.db.execute("INSERT INTO watch_file(path,size,mtime_ns,state,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,state=excluded.state,updated_at=excluded.updated_at",
                                         (path, info.st_size, info.st_mtime_ns, "imported", utc_now().isoformat()))
            except ValueError as exc:
                if "already in Video Drop" not in str(exc):
                    self.retry_after[path] = now + SETTLE_SECONDS
                    continue
                with Store(self.state / "video-drop.sqlite") as store, store.db:
                    store.db.execute("INSERT INTO watch_file(path,size,mtime_ns,state,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,state=excluded.state,updated_at=excluded.updated_at",
                                     (path, info.st_size, info.st_mtime_ns, "duplicate", utc_now().isoformat()))
            self.pending.pop(path, None)
        for release_id in imported:
            self.on_import(release_id)
        return imported

    def run(self, stop) -> None:
        while not stop.is_set():
            try:
                self.scan()
            except Exception as exc:
                print(f"Watch folder scan failed: {exc}", file=sys.stderr, flush=True)
            stop.wait(POLL_SECONDS)
