"""Move the Edits project a posted release left behind to Edits' Trash.

    python scripts/phone_cleanup.py RELEASE_ID [--db PATH] [--dry-run]

Runs on its own from the server once Instagram has its native receipt, when Settings >
"Remove the video from the iPhone after it posts" is on. The Instagram flow noted Edits'
project tiles before its upload (note_projects); this moves exactly the one new
"Untitled project" to Trash (video_drop/edits_cleanup.py says how it is matched). Edits
keeps trashed projects under Projects > Trash, so nothing is erased.

Recorded on Edits 2026-10-01 (iOS 26.7): project tiles are Cells named
"project-tile-<uuid>" labelled "<name>, <age> · <size>"; a long press opens a menu whose
"Move to Trash" Button is named "menu-item-Move to Trash"; it asks "Move project to
Trash?" with a "Move to Trash" Button and a "Cancel" Button. Edits reopens the list where
it was last scrolled, so the list is scrolled to its top first.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_drop import phone_lock  # noqa: E402
from video_drop import edits_cleanup  # noqa: E402
from video_drop import receipts as rc  # noqa: E402
from video_drop.accounts import load_targets  # noqa: E402
from video_drop.core import Store  # noqa: E402
from video_drop.screens.snapshot import elements_from_tree  # noqa: E402
from scripts import phone_youtube as share  # noqa: E402

EDITS_BUNDLE = "com.burbn.basel"
NOTE = "edits"  # receipts baseline name: .state/receipts/release<N>-edits-before.json
TOP_SWIPES = 6
phone = share.phone


def elements():
    return elements_from_tree(phone.ui_tree())


def open_projects() -> dict[str, str]:
    """Edits' Projects list, scrolled to its top; returns {project id: tile label}."""
    phone.open_app(EDITS_BUNDLE)
    time.sleep(3.0)
    items = elements()
    if not any(e.name == "projects-tab" for e in items):
        raise edits_cleanup.CleanupSkipped("Edits did not open on its Projects list; leaving Edits alone")
    shown = edits_cleanup.tiles(items)
    layout = share.layout()
    for _ in range(TOP_SWIPES):
        phone.swipe(*layout.reference_point(220, 300), *layout.reference_point(220, 800), 0.4)
        time.sleep(1.0)
        again = edits_cleanup.tiles(elements())
        if again == shown:
            break
        shown = again
    return shown


def note_projects(state: Path, release_id: int, *, now=None) -> list[str]:
    """Before the Instagram upload: remember which projects Edits already has."""
    global phone
    phone = share.phone
    noted_at = (now or (lambda: datetime.now(timezone.utc)))()
    projects = sorted(open_projects())
    rc.save_baseline(state, release_id, NOTE, {"projects": projects, "notedAt": noted_at.isoformat()})
    phone.press_home()
    return projects


def trash(project: str) -> None:
    """Long press the tile, Move to Trash, confirm; the tile must then be gone."""
    tile = [e for e in elements() if e.type == "Cell" and e.name == edits_cleanup.TILE_PREFIX + project]
    if len(tile) != 1:
        raise edits_cleanup.CleanupSkipped("The project left the list before it could be moved; leaving Edits alone")
    phone.long_press(tile[0].x, tile[0].y, 1.0)
    time.sleep(1.2)
    menu = [e for e in elements() if e.type == "Button" and e.name == "menu-item-Move to Trash"]
    if len(menu) != 1:
        raise edits_cleanup.CleanupSkipped("Edits' project menu has no single Move to Trash; leaving Edits alone")
    phone.tap(menu[0].x, menu[0].y)
    time.sleep(1.2)
    items = elements()
    confirm = [e for e in items if e.type == "Button" and e.label == "Move to Trash"]
    if not any(e.label == "Move project to Trash?" for e in items) or len(confirm) != 1:
        cancel = [e for e in items if e.type == "Button" and e.label == "Cancel"]
        if cancel:
            phone.tap(cancel[0].x, cancel[0].y)
        raise edits_cleanup.CleanupSkipped("Edits did not ask to confirm the move; nothing was moved")
    phone.tap(confirm[0].x, confirm[0].y)
    time.sleep(2.0)
    if project in edits_cleanup.tiles(elements()):
        raise RuntimeError("Edits still shows the project after Move to Trash")


@phone_lock.locked("Edits cleanup")
def run(release_id: int, db: Path, *, dry_run: bool = False, now=None) -> dict:
    global phone
    now = now or datetime.now(timezone.utc)
    state = Path(db).parent
    with Store(db, load_targets(state)) as store:
        release = store.release(release_id)
    if not edits_cleanup.ready(release):
        return {"kind": "waiting", "releaseId": release_id, "message": "Instagram has no native receipt yet"}
    note = rc.load_baseline(state, release_id, NOTE)
    if not note or "projects" not in note:
        return {"kind": "skipped", "releaseId": release_id,
                "message": "No note of Edits' projects from before this upload; leaving Edits alone"}
    if note.get("cleanup"):
        return {"kind": "done", "releaseId": release_id, "cleanup": note["cleanup"]}
    share.connect_sidetap()
    phone = share.phone
    phone.unlock()
    share.layout(refresh=True)
    result: dict
    with share.busy("Edits cleanup", 120):
        try:
            shown = open_projects()
            project = edits_cleanup.new_project(note["projects"], shown, datetime.fromisoformat(note["notedAt"]), now)
            if dry_run:
                result = {"kind": "would_trash", "project": project, "tile": shown[project]}
            else:
                trash(project)
                result = {"kind": "trashed", "project": project, "tile": shown[project], "at": now.isoformat()}
        except edits_cleanup.CleanupSkipped as exc:
            result = {"kind": "skipped", "message": str(exc), "at": now.isoformat()}
        finally:
            try:
                phone.press_home()
            except Exception:
                pass
    if not dry_run:
        # Trashed or skipped, this release is settled: the cleanup never runs twice for it.
        rc.save_baseline(state, release_id, NOTE, {**{k: v for k, v in note.items()
                                                      if k not in {"releaseId", "platform", "savedAt"}},
                                                   "cleanup": result})
    return {"releaseId": release_id, **result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("release_id", type=int)
    parser.add_argument("--db", type=Path, default=ROOT / ".state" / "video-drop.sqlite")
    parser.add_argument("--dry-run", action="store_true", help="find the project, move nothing")
    args = parser.parse_args()
    print(json.dumps(run(args.release_id, args.db, dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"kind": "error", "message": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
