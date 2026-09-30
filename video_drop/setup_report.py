"""Print the setup checklist in a terminal: ``python -m video_drop.setup_report``.

The same read-only probes the editor's checklist uses (``setup_check``), run
without the server, so the Windows installer can end with the real state of
this PC and iPhone. Exit status 0 when every required row is ready, 1 when
something still needs doing, so a script can branch on it. Never taps the
phone, never downloads, never writes anything but the phone-space cache the
probes already keep.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import setup_check
from .watch import WatchFolder

ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("VIDEO_DROP_STATE", ROOT / ".state")).resolve()
MARK = {"ok": "[ok]", "action": "[to do]", "blocked": "[waiting]"}


def phone_inspection(state: Path) -> dict:
    path = state / "phone-inspection.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "idle"}
    if data.get("status") == "inspecting":
        # Only the server can be mid-inspection; from here it is a stale file.
        return {"status": "failed", "error": "Phone inspection was interrupted. Inspect again."}
    return data if isinstance(data, dict) else {"status": "idle"}


def run_checklist(state: Path = STATE) -> dict:
    state.mkdir(parents=True, exist_ok=True)
    watcher = WatchFolder(state, lambda release_id: None)
    return setup_check.checklist(setup_check.local_probes(state, watcher.status, lambda: phone_inspection(state)))


def render(result: dict) -> str:
    lines = []
    for entry in result["items"]:
        tag = "" if entry.get("required", True) else (" (recommended)" if entry.get("recommended") else " (optional)")
        lines.append(f"{MARK.get(entry['status'], '[?]'):10}{entry['title']}{tag}")
        lines.append(f"{'':10}{entry['detail']}")
        if entry["status"] != "ok" and entry.get("fix"):
            lines.append(f"{'':10}-> {entry['fix']}")
    lines.append("")
    lines.append("Ready for the first post." if result["ready"] else "Not ready yet: finish the rows marked [to do].")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    result = run_checklist()
    if "--json" in args:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(render(result))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
