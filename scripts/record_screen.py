"""Record one phone screen as a fixture and print a draft screen map. Never taps."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_drop.screens.icons import to_points  # noqa: E402
from video_drop.screens.snapshot import Snapshot, capture, load_fixture, save_fixture  # noqa: E402

CONTROL_TYPES = {"Button", "Cell", "TextField", "TextView", "Switch", "Link", "MenuItem"}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")[:40] or "element"


def draft_map(snapshot: Snapshot, app: str, screen: str) -> dict:
    ids = [e.name for e in snapshot.elements if e.name and e.name != e.label and "." in e.name]
    require = [{"id": name} for name in dict.fromkeys(ids)][:3]
    if not require:
        texts = [e.label for e in snapshot.elements if e.type == "StaticText" and e.label]
        require = [{"label": text} for text in dict.fromkeys(texts)][:3]
    counts = {}
    for e in snapshot.elements:
        if e.type in CONTROL_TYPES and e.label:
            counts[(e.type, e.label)] = counts.get((e.type, e.label), 0) + 1
    elements = {slug(label): {"label": label, "type": kind}
                for (kind, label), count in counts.items() if count == 1}
    return {"app": app, "screen": screen, "locale": "en", "signature": {"require": require, "forbid": []},
            "elements": elements, "actions": {}}


def crop_icon(stem: Path, left: float, top: float, width: float, height: float, out: Path) -> Path:
    snapshot = load_fixture(stem)
    if not snapshot.screenshot:
        raise ValueError("Fixture has no screenshot")
    image = to_points(snapshot.screenshot, snapshot.width, snapshot.height)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.crop((round(left), round(top), round(left + width), round(top + height))).save(out)
    return out


def connect():
    from video_drop.phone import helpers
    return helpers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app")
    parser.add_argument("screen")
    parser.add_argument("--out", type=Path, default=ROOT / ".state" / "fixtures")
    parser.add_argument("--crop", nargs=5, metavar=("NAME", "LEFT", "TOP", "WIDTH", "HEIGHT"))
    parser.add_argument("--from", dest="stem", type=Path)
    args = parser.parse_args()
    if args.crop:
        if not args.stem:
            raise SystemExit("--crop needs --from FIXTURE_STEM")
        name, *box = args.crop
        out = crop_icon(args.stem, *map(float, box), ROOT / "maps" / args.app / "icons" / f"{name}.png")
        print(json.dumps({"icon": str(out)}))
        return
    snapshot = capture(connect())
    stem = args.out / args.app / f"{args.screen}-{time.strftime('%Y%m%d-%H%M%S')}"
    save_fixture(snapshot, stem)
    print(json.dumps({"fixture": str(stem), "draft": draft_map(snapshot, args.app, args.screen)}, indent=1))


if __name__ == "__main__":
    main()
