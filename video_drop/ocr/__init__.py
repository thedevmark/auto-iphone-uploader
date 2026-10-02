"""Offline screen text for rows an app hides from accessibility.

YouTube 21.38's "Add details" screen draws Description, Paid promotion and "AI use, Tags"
but leaves them out of the accessibility tree (2026-09-30), so the flows read them from a
screenshot with Windows' built-in OCR engine (Windows.Media.Ocr): offline, free, no model.
Matching never guesses: a label must appear exactly once, and every tap built on it is
proven by the screen that opens next.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCRIPT = Path(__file__).with_name("winocr.ps1")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class OcrError(RuntimeError):
    pass


@dataclass(frozen=True)
class Line:
    """One OCR line in screenshot pixels."""

    text: str
    x: int
    y: int
    w: int
    h: int

    def center_points(self, scale: float) -> tuple[float, float]:
        return (self.x + self.w / 2) / scale, (self.y + self.h / 2) / scale


def parse(output: str) -> list[Line]:
    lines = []
    for raw in output.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        item = json.loads(raw)
        lines.append(Line(str(item["text"]), int(item["x"]), int(item["y"]), int(item["w"]), int(item["h"])))
    return lines


def _plain(text: str) -> str:
    return " ".join(text.replace("’", "'").split()).casefold()


def find(lines: list[Line], label: str, *, exact: bool = True) -> Line:
    """The one line that reads ``label`` (exactly, or starting with it); anything else fails."""
    want = _plain(label)
    found = [line for line in lines if (_plain(line.text) == want if exact else _plain(line.text).startswith(want))]
    if len(found) != 1:
        raise OcrError(f"Expected one {label!r} on screen, OCR found {len(found)}")
    return found[0]


def rows(lines: list[Line], scale: float) -> list[dict]:
    """OCR lines as the flows' screen rows (text and center in points), typed "OcrText".

    The type keeps a pixel row from ever satisfying a check that needs a real control
    ("TextView", "Button", an accessibility id): those wait for a still screen instead."""
    out = []
    for line in lines:
        x, y = line.center_points(scale)
        out.append({"text": " ".join(line.text.replace("’", "'").split()), "x": x, "y": y,
                    "w": line.w / scale, "h": line.h / scale, "type": "OcrText"})
    return out


def screen_rows(png: bytes, width_points: float) -> list[dict]:
    """OCR rows for a whole-phone screenshot whose screen is ``width_points`` wide."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png)) as image:
        scale = image.width / width_points
    return rows(read_png(png), scale)


def read_png(png: bytes, *, timeout: float = 30) -> list[Line]:
    """Run Windows' OCR on a PNG screenshot."""
    if os.name != "nt":
        raise OcrError("Screen OCR needs Windows' built-in OCR engine")
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / "screen.png"
        image.write_bytes(png)
        try:
            done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                   str(SCRIPT), str(image)], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OcrError(f"Windows OCR did not run: {exc}") from exc
    if done.returncode != 0:
        raise OcrError(f"Windows OCR failed: {(done.stderr or done.stdout).strip()[-200:]}")
    return parse(done.stdout)


def probe(*, timeout: float = 30) -> dict:
    """Whether Windows has an OCR engine this app can use: {"available", "language", "error"}."""
    if os.name != "nt":
        return {"available": False, "language": "", "error": "not Windows"}
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                               "-Probe"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "language": "", "error": str(exc)}
    if done.returncode != 0:
        return {"available": False, "language": "", "error": (done.stderr or done.stdout).strip()[-200:]}
    try:
        language = str(json.loads(done.stdout.strip().splitlines()[-1])["language"])
    except (ValueError, KeyError, IndexError):
        return {"available": False, "language": "", "error": "unreadable probe output"}
    return {"available": True, "language": language, "error": ""}
