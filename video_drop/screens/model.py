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
    bundle: str = ""


def _ban_coordinates(data: dict) -> None:
    """Reject coordinate keys in locator and action bodies; element and action names are free text."""
    for key in data:
        if key in COORDINATE_KEYS:
            raise ValueError(f"Maps may not contain screen coordinates ({key!r})")


def locator(data: dict) -> Locator:
    if not isinstance(data, dict) or not data:
        raise ValueError("A locator must be a non-empty object")
    _ban_coordinates(data)
    unknown = set(data) - LOCATOR_KEYS
    if unknown:
        raise ValueError(f"Unknown locator key: {sorted(unknown)[0]}")
    if not any(data.get(key) for key in ("id", "label", "type", "icon")):
        raise ValueError("A locator needs an id, label, type, or icon")
    side = data.get("side")
    if (side is None) != ("relative_to" not in data) or (side is not None and side not in SIDES):
        raise ValueError("relative_to and side go together; side is left, right, above, or below")
    return Locator(
        id=data.get("id"), label=data.get("label"), type=data.get("type"),
        contains=bool(data.get("contains", False)),
        relative_to=locator(data["relative_to"]) if "relative_to" in data else None,
        side=side, icon=data.get("icon"), within=float(data.get("within", 120.0)),
        fallback=tuple(locator(item) for item in data.get("fallback", [])),
    )


def marker(data: dict) -> Locator:
    """A signature marker is matched by presence, so it may only use id, label, type, and contains."""
    found = locator(data)
    if found.relative_to or found.icon or found.fallback:
        raise ValueError("A signature marker cannot use relative_to, icon, or fallback")
    return found


def load_map(path: Path) -> ScreenMap:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        _ban_coordinates(data)
        signature = data.get("signature") or {}
        _ban_coordinates(signature)
        require = tuple(marker(item) for item in signature.get("require", []))
        if not require:
            raise ValueError("signature.require needs at least one marker")
        forbid = tuple(marker(item) for item in signature.get("forbid", []))
        elements = {name: locator(item) for name, item in (data.get("elements") or {}).items()}
        actions = {}
        for name, item in (data.get("actions") or {}).items():
            _ban_coordinates(item)
            if item.get("tap") not in elements:
                raise ValueError(f"action {name!r} taps unknown element {item.get('tap')!r}")
            actions[name] = Action(name, item["tap"], item["expect"], bool(item.get("irreversible", False)))
    except ValueError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    return ScreenMap(data["app"], data["screen"], data.get("locale", "en"), require, forbid,
                     elements, actions, path, data.get("bundle", ""))


def load_maps(folder: Path) -> list[ScreenMap]:
    folder = Path(folder)
    return [load_map(path) for path in sorted(folder.glob("*/*.json")) if path.parent.name != "labels"]
