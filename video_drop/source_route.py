"""Where a release's video sits in Apple's Files app on the iPhone.

Every video opens through Files, whatever cloud holds it: the provider's app shows up as a
location there once it is installed and turned on. The location name and folder path are
derived from the source path relative to the sync root this PC detected, never guessed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePath

from .setup_check import cloud_for

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
    # shareddocuments:// URL of the last folder, when known: Files opens there directly.
    deeplink: str = ""

    @property
    def shown(self) -> str:
        return " › ".join((self.provider, *self.folders))


@dataclass(frozen=True)
class Route:
    provider: str
    files: FilesPath


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


def choose_route(source: PurePath, clouds: list[tuple[str, PurePath]]) -> Route:
    cloud = cloud_for(Path(source), [(provider, Path(root)) for provider, root in clouds])
    if cloud is None:
        raise SourceRouteError(f"{source.name} is not inside a OneDrive, Google Drive, Dropbox or iCloud Drive "
                               "folder on this PC, so the iPhone cannot open it. Nothing was opened")
    provider, root = cloud
    # Compare on-disk paths: a short (8.3) or junction spelling of either side must not break the route.
    real_root, real_source = Path(root).resolve(strict=False), Path(source).resolve(strict=False)
    return Route(provider, files_path(provider, real_root, real_source))


def release_source(db: Path, release_id: int) -> tuple[Path, str, int]:
    """Read-only: the release's source path, name and size."""
    try:
        connection = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=15)
    except sqlite3.Error as exc:
        raise SourceRouteError(f"Could not read {db}: {exc}") from exc
    try:
        row = connection.execute("SELECT source_path, source_name, file_size FROM release WHERE id=?",
                                 (release_id,)).fetchone()
    except sqlite3.Error as exc:
        raise SourceRouteError(f"Could not read {db}: {exc}") from exc
    finally:
        connection.close()
    if row is None:
        raise SourceRouteError(f"Release {release_id} is not in {db}")
    return Path(row[0]), str(row[1]), int(row[2])


def release_sha256(db: Path, release_id: int) -> str:
    """Read-only: the SHA-256 the release was imported with."""
    connection = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=15)
    try:
        row = connection.execute("SELECT sha256 FROM release WHERE id=?", (release_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise SourceRouteError(f"Release {release_id} is not in {db}")
    return str(row[0])


def release_route(db: Path, data: dict, clouds: list[tuple[str, PurePath]]) -> Route:
    """The route for the exact file a flow already verified (``data`` carries its name and size)."""
    source, name, size = release_source(db, int(data["releaseId"]))
    if source.name != data["filename"] or name != data["filename"] or size != data["sizeBytes"]:
        raise SourceRouteError("The release's source file does not match the file being posted. Nothing was opened")
    return choose_route(source, clouds)
