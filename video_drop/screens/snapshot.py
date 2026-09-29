"""One read of the phone: typed elements from WDA's raw tree, plus a screenshot."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

TYPE_PREFIX = "XCUIElementType"


@dataclass(frozen=True)
class Element:
    type: str
    label: str
    name: str
    value: str
    left: float
    top: float
    width: float
    height: float

    @property
    def x(self) -> float:
        return self.left + self.width / 2

    @property
    def y(self) -> float:
        return self.top + self.height / 2

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(text for text in (self.name, self.label, self.value) if text))


@dataclass(frozen=True)
class Snapshot:
    width: float
    height: float
    elements: tuple[Element, ...]
    screenshot: bytes | None = None
    app: str = ""
    app_version: str = ""


def elements_from_tree(node: dict, out: list | None = None) -> tuple[Element, ...]:
    out = [] if out is None else out
    if isinstance(node, dict):
        rect = node.get("rect") or {}
        visible = str(node.get("isVisible", "1")) in ("1", "true", "True")
        if visible and rect.get("width", 0) > 0 and rect.get("height", 0) > 0:
            kind = str(node.get("type", ""))
            out.append(Element(kind[len(TYPE_PREFIX):] if kind.startswith(TYPE_PREFIX) else kind,
                               str(node.get("label") or ""), str(node.get("name") or ""),
                               str(node.get("value") or ""), float(rect["x"]), float(rect["y"]),
                               float(rect["width"]), float(rect["height"])))
        for child in node.get("children") or []:
            elements_from_tree(child, out)
    return tuple(out)


def capture(phone, *, screenshot: bool = True, app_version: str = "") -> Snapshot:
    info = phone.screen_info()
    active = phone.current_app() if hasattr(phone, "current_app") else {}
    return Snapshot(float(info["width"]), float(info["height"]), elements_from_tree(phone.ui_tree()),
                    phone.screenshot() if screenshot else None,
                    str(active.get("bundleId", "")), app_version)


def save_fixture(snapshot: Snapshot, stem: Path) -> None:
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    data = {"width": snapshot.width, "height": snapshot.height, "app": snapshot.app,
            "appVersion": snapshot.app_version, "elements": [asdict(e) for e in snapshot.elements]}
    stem.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if snapshot.screenshot:
        stem.with_suffix(".png").write_bytes(snapshot.screenshot)


def load_fixture(stem: Path) -> Snapshot:
    stem = Path(stem)
    data = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
    image = stem.with_suffix(".png")
    return Snapshot(float(data["width"]), float(data["height"]),
                    tuple(Element(**item) for item in data["elements"]),
                    image.read_bytes() if image.is_file() else None,
                    data.get("app", ""), data.get("appVersion", ""))
