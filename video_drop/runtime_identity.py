"""Identify the exact local source tree loaded by the desktop server."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


def source_fingerprint(root: Path) -> str:
    files = [*root.joinpath("video_drop").rglob("*.py"),
             *root.joinpath("scripts").rglob("*.py"),
             root / "web" / "index.html", root / "requirements.txt"]
    digest = sha256()
    for path in sorted((file for file in files if file.is_file()),
                       key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
