"""Which Edits project a release left behind, and when it may go to Edits' Trash.

Every Instagram post goes through Edits for the 4K export, and each one leaves an
"Untitled project" holding the clip (measured: 167.9 MB to 1.03 GB each);
nothing is saved to Photos. With the removeAfterPost setting on, the Instagram flow notes
the project tiles Edits shows before the upload. Once Instagram has its native receipt, the
cleanup moves exactly the one new project to Edits' Trash (recoverable from Projects >
Trash in Edits).

Matching never guesses. The project must be:
- absent from the list noted before the upload,
- still named "Untitled project" (a project the owner renamed is never touched),
- no older than the time since that note,
- the only tile meeting all three.
Anything else leaves Edits alone and says why.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

TILE_PREFIX = "project-tile-"
UNTITLED = "Untitled project"
FINISHED = frozenset({"posted", "scheduled"})
_AGE = re.compile(r"^(\d+)\s*([smhdw])$")
_UNITS = {"s": timedelta(seconds=1), "m": timedelta(minutes=1), "h": timedelta(hours=1),
          "d": timedelta(days=1), "w": timedelta(weeks=1)}


class CleanupSkipped(RuntimeError):
    pass


def tiles(elements) -> dict[str, str]:
    """{project id: tile label} for the project tiles on screen."""
    return {e.name[len(TILE_PREFIX):]: e.label for e in elements
            if e.type == "Cell" and e.name.startswith(TILE_PREFIX)}


def tile_age(label: str) -> timedelta | None:
    """'Untitled project, 58m · 167.9 MB' -> 58 minutes; 'now' -> 0; unreadable -> None."""
    try:
        age = label.rsplit(", ", 1)[1].split("·")[0].strip().casefold()
    except IndexError:
        return None
    if age in {"now", "just now"}:
        return timedelta(0)
    match = _AGE.match(age)
    return int(match[1]) * _UNITS[match[2]] if match else None


def new_project(before: list[str], shown: dict[str, str], noted_at: datetime, now: datetime) -> str:
    """The one project this release's upload created, or CleanupSkipped saying why not."""
    window = now - noted_at
    if window < timedelta(0):
        raise CleanupSkipped("The note of Edits' projects is from the future; leaving Edits alone")
    candidates = []
    for project, label in shown.items():
        if project in before or not label.startswith(UNTITLED + ","):
            continue
        age = tile_age(label)
        # Edits rounds ages down to the unit it shows; one unit of slack on top of the window.
        if age is None or age > window + timedelta(minutes=1):
            continue
        candidates.append(project)
    if len(candidates) != 1:
        raise CleanupSkipped(f"Expected one new Edits project from this upload, found {len(candidates)}; "
                             "leaving Edits alone")
    return candidates[0]


def ready(release: dict) -> bool:
    """Instagram, the only platform that uses the Edits project, has its native receipt.

    YouTube, TikTok and Threads open the source from the cloud folder, never from Edits."""
    instagram = next((d for d in release["destinations"] if d["platform"] == "instagram"), None)
    return instagram is not None and instagram["status"] in FINISHED
