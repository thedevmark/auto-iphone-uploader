"""Save what the phone showed when a flow failed, so one failure is enough to diagnose it.

Writes .state/failures/<flow>-<timestamp>/ with screen.png (an AX-free screenshot), ocr.json
(Windows OCR lines with positions) and error.txt. Best effort: capturing never hides or
replaces the flow's own error.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path


def capture(state: Path, flow: str, exc: BaseException) -> Path | None:
    try:
        folder = Path(state) / "failures" / f"{flow.replace(' ', '-')}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "error.txt").write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8")
        from .phone import capture as phone_capture
        png = phone_capture.pixels_png()
        (folder / "screen.png").write_bytes(png)
        try:
            from . import ocr
            lines = [vars(line) for line in ocr.read_png(png)]
        except Exception as ocr_exc:  # the screenshot alone still helps
            lines = [{"error": str(ocr_exc)}]
        (folder / "ocr.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
        return folder
    except Exception:
        return None


# The run in progress, set by phone_lock.locked: lets the phone guards capture the failing screen
# BEFORE they restore Do Not Disturb / rotation lock (which opens Control Center and goes Home).
CURRENT: dict = {"state": None, "flow": None, "done": False}


def snap(exc: BaseException) -> None:
    """Capture once per run, at the first place that sees the failure."""
    if CURRENT["state"] is not None and not CURRENT["done"]:
        CURRENT["done"] = capture(CURRENT["state"], CURRENT["flow"] or "flow", exc) is not None
