"""Screen maps: what identifies a screen and how to find its controls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

COORDINATE_KEYS = frozenset({"x", "y", "point", "points", "coordinate", "coordinates", "frame", "rect"})
LOCATOR_KEYS = frozenset({"id", "label", "type", "contains", "relative_to", "side", "icon", "within", "fallback"})
SIDES = frozenset({"left", "right", "above", "below"})


@dataclass(frozen=True)
class Locator:
    id: str | None = None
    label: str | None = None
    type: str | None = None
    contains: bool = False
    relative_to: "Locator | None" = None
    side: str | None = None
    icon: str | None = None
    within: float = 120.0
    fallback: tuple["Locator", ...] = ()


@dataclass(frozen=True)
class Action:
    name: str
    tap: str
    expect: str
    irreversible: bool = False


@dataclass(frozen=True)
class ScreenMap:
    app: str
    screen: str
    locale: str
    require: tuple[Locator, ...]
    forbid: tuple[Locator, ...]
    elements: dict
    actions: dict
    path: Path = field(compare=False)


def _ban_coordinates(value, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in COORDINATE_KEYS:
                raise ValueError(f"{where}: maps may not contain screen coordinates ({key!r})")
            _ban_coordinates(item, where)
    elif isinstance(value, list):
        for item in value:
            _ban_coordinates(item, where)


def locator(data: dict) -> Locator:
    if not isinstance(data, dict) or not data:
        raise ValueError("A locator must be a non-empty object")
    unknown = set(data) - LOCATOR_KEYS
    if unknown:
        raise ValueError(f"Unknown locator key: {sorted(unknown)[0]}")
    if not any(data.get(key) for key in ("id", "label", "type", "icon")):
        raise ValueError("A locator needs an id, label, type, or icon")
    side = data.get("side")
    if (side is None) != (data.get("relative_to") is None) or (side is not None and side not in SIDES):
        raise ValueError("relative_to and side go together; side is left, right, above, or below")
    return Locator(
        id=data.get("id"), label=data.get("label"), type=data.get("type"),
        contains=bool(data.get("contains", False)),
        relative_to=locator(data["relative_to"]) if data.get("relative_to") else None,
        side=side, icon=data.get("icon"), within=float(data.get("within", 120.0)),
        fallback=tuple(locator(item) for item in data.get("fallback", [])),
    )


def load_map(path: Path) -> ScreenMap:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    _ban_coordinates(data, path.name)
    signature = data.get("signature") or {}
    require = tuple(locator(item) for item in signature.get("require", []))
    if not require:
        raise ValueError(f"{path.name}: signature.require needs at least one marker")
    elements = {name: locator(item) for name, item in (data.get("elements") or {}).items()}
    actions = {}
    for name, item in (data.get("actions") or {}).items():
        if item.get("tap") not in elements:
            raise ValueError(f"{path.name}: action {name!r} taps unknown element {item.get('tap')!r}")
        actions[name] = Action(name, item["tap"], item["expect"], bool(item.get("irreversible", False)))
    return ScreenMap(data["app"], data["screen"], data.get("locale", "en"), require,
                     tuple(locator(item) for item in signature.get("forbid", [])),
                     elements, actions, path)


def load_maps(folder: Path) -> list[ScreenMap]:
    folder = Path(folder)
    return [load_map(path) for path in sorted(folder.glob("*/*.json")) if path.parent.name != "labels"]
