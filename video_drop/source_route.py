"""Which iPhone app opens a release's video, and where that video sits in it.

OneDrive videos open in the OneDrive app (the proven route) unless the
``filesAppForOneDrive`` setting sends them through Apple's Files app too. Every
other provider opens through Files, where its app shows up as a location once
it is installed and turned on. The location name and folder path are derived
from the source path relative to the sync root this PC detected, never guessed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePath

from .setup_check import cloud_for

ONEDRIVE_APP = "onedrive_app"
FILES_APP = "files_app"
FILES_SETTING = "filesAppForOneDrive"

# provider -> (names its location may carry under Files > Browse > Locations,
#              folders between that location and the PC's sync root).
# OneDrive's location lists "Files" (your OneDrive) beside "Shared" (reference iPhone, iOS 26.7).
# Google Drive's lists "My Drive" (the PC mounts that folder as its root) beside shared drives.
FILES_LOCATIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "OneDrive": (("OneDrive",), ("Files",)),
    "Google Drive": (("Google Drive", "Drive"), ("My Drive",)),
    "Dropbox": (("Dropbox",), ()),
    "iCloud Drive": (("iCloud Drive",), ()),
}


class SourceRouteError(ValueError):
    pass


@dataclass(frozen=True)
class FilesPath:
    provider: str
    locations: tuple[str, ...]
    folders: tuple[str, ...]
    filename: str

    @property
    def shown(self) -> str:
        return " › ".join((self.provider, *self.folders))


@dataclass(frozen=True)
class Route:
    kind: str
    provider: str
    files: FilesPath | None = None


def _relative_parts(root: PurePath, source: PurePath) -> tuple[str, ...]:
    # Windows paths compare case-insensitively; keep the source's own spelling for the phone.
    root_parts, parts = root.parts, source.parts
    if len(parts) <= len(root_parts) or [p.casefold() for p in parts[:len(root_parts)]] != \
            [p.casefold() for p in root_parts]:
        raise SourceRouteError(f"{source} is not inside {root}")
    return tuple(parts[len(root_parts):])


def files_path(provider: str, root: PurePath, source: PurePath) -> FilesPath:
    """Where ``source`` shows up in the Files app, e.g. Google Drive > My Drive > _Videos > clip.mp4."""
    if provider not in FILES_LOCATIONS:
        raise SourceRouteError(f"{provider} has no Files-app route")
    locations, prefix = FILES_LOCATIONS[provider]
    *folders, filename = _relative_parts(root, source)
    return FilesPath(provider, locations, (*prefix, *folders), filename)


def route_kind(provider: str, *, force_files: bool) -> str:
    return ONEDRIVE_APP if provider == "OneDrive" and not force_files else FILES_APP


def choose_route(source: PurePath, clouds: list[tuple[str, PurePath]], *, force_files: bool) -> Route:
    cloud = cloud_for(Path(source), [(provider, Path(root)) for provider, root in clouds])
    if cloud is None:
        raise SourceRouteError(f"{source.name} is not inside a OneDrive, Google Drive, Dropbox or iCloud Drive "
                               "folder on this PC, so the iPhone cannot open it. Nothing was opened")
    provider, root = cloud
    kind = route_kind(provider, force_files=force_files)
    return Route(kind, provider, files_path(provider, root, source) if kind == FILES_APP else None)


def release_source(db: Path, release_id: int) -> tuple[Path, str, int, bool]:
    """Read-only: the release's source path, name and size, and the Files-route setting."""
    try:
        connection = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=15)
    except sqlite3.Error as exc:
        raise SourceRouteError(f"Could not read {db}: {exc}") from exc
    try:
        row = connection.execute("SELECT source_path, source_name, file_size FROM release WHERE id=?",
                                 (release_id,)).fetchone()
        setting = connection.execute("SELECT value FROM setting WHERE key='phone_checks'").fetchone()
    except sqlite3.Error as exc:
        raise SourceRouteError(f"Could not read {db}: {exc}") from exc
    finally:
        connection.close()
    if row is None:
        raise SourceRouteError(f"Release {release_id} is not in {db}")
    try:
        checks = json.loads(setting[0]) if setting else {}
    except ValueError:
        checks = {}
    force = checks.get(FILES_SETTING) is True if isinstance(checks, dict) else False
    return Path(row[0]), str(row[1]), int(row[2]), force


def release_route(db: Path, data: dict, clouds: list[tuple[str, PurePath]]) -> Route:
    """The route for the exact file a flow already verified (``data`` carries its name and size)."""
    source, name, size, force = release_source(db, int(data["releaseId"]))
    if source.name != data["filename"] or name != data["filename"] or size != data["sizeBytes"]:
        raise SourceRouteError("The release's source file does not match the file being posted. Nothing was opened")
    return choose_route(source, clouds, force_files=force)
