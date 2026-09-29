"""Resolve map locators against one snapshot. Never guesses between matches."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .icons import IconError, locate, to_points
from .labels import Labels
from .model import Locator, ScreenMap
from .snapshot import Element, Snapshot


class MatchError(RuntimeError):
    pass


class AmbiguousMatch(MatchError):
    """Several targets matched; a fallback must never narrow this into a guess."""


@dataclass(frozen=True)
class Target:
    x: float
    y: float
    element: Element | None = None


def _own_filter(element: Element, locator: Locator, labels: Labels) -> bool:
    if locator.id and element.name != locator.id:
        return False
    if locator.type and element.type != locator.type:
        return False
    if locator.label:
        wanted = labels.text(locator.label)
        if not (wanted in element.label if locator.contains else element.label == wanted):
            return False
    return True


def candidates(snapshot: Snapshot, locator: Locator, labels: Labels) -> list[Element]:
    return [element for element in snapshot.elements if _own_filter(element, locator, labels)]


def _band(anchor: Element, side: str, within: float) -> tuple[float, float, float, float]:
    if side == "left":
        return anchor.left - within, anchor.top, anchor.left, anchor.top + anchor.height
    if side == "right":
        right = anchor.left + anchor.width
        return right, anchor.top, right + within, anchor.top + anchor.height
    if side == "above":
        return anchor.left, anchor.top - within, anchor.left + anchor.width, anchor.top
    bottom = anchor.top + anchor.height
    return anchor.left, bottom, anchor.left + anchor.width, bottom + within


def _single(found: list[Element], locator: Locator) -> Target:
    # iOS nests wrappers that repeat one control with an identical frame; that is one target, not a choice.
    found = list(dict.fromkeys(found))
    if len(found) > 1:
        raise AmbiguousMatch(f"{len(found)} matches for {locator}")
    if not found:
        raise MatchError(f"No match for {locator}")
    return Target(found[0].x, found[0].y, found[0])


def _find_once(snapshot: Snapshot, locator: Locator, labels: Labels, icon_dir: Path | None) -> Target:
    if locator.relative_to is None:
        if locator.icon:
            raise MatchError("An icon locator needs relative_to so its search band is known")
        return _single(candidates(snapshot, locator, labels), locator)
    anchor = find(snapshot, locator.relative_to, labels, icon_dir).element
    if anchor is None:
        raise MatchError("A relative anchor must be an element, not an icon")
    band = _band(anchor, locator.side, locator.within)
    if locator.icon:
        if not snapshot.screenshot or icon_dir is None:
            raise MatchError("Icon locator needs a screenshot and the map's icon folder")
        screen = to_points(snapshot.screenshot, snapshot.width, snapshot.height)
        try:
            x, y = locate(screen, Image.open(Path(icon_dir) / locator.icon), band)
        except IconError as exc:
            raise MatchError(str(exc)) from exc
        return Target(x, y, None)
    inside = [e for e in candidates(snapshot, locator, labels)
              if e is not anchor and band[0] <= e.x <= band[2] and band[1] <= e.y <= band[3]]
    return _single(inside, locator)


def find(snapshot: Snapshot, locator: Locator, labels: Labels, icon_dir: Path | None = None) -> Target:
    errors = []
    for option in (locator, *locator.fallback):
        try:
            return _find_once(snapshot, option, labels, icon_dir)
        except AmbiguousMatch:
            raise
        except MatchError as exc:
            errors.append(str(exc))
    raise MatchError("; ".join(errors))


def present(snapshot: Snapshot, locator: Locator, labels: Labels) -> bool:
    return bool(candidates(snapshot, locator, labels))


def identify(snapshot: Snapshot, maps: list[ScreenMap], labels: Labels) -> ScreenMap:
    matched = [screen for screen in maps
               if not (screen.bundle and snapshot.app and screen.bundle != snapshot.app)
               and all(present(snapshot, marker, labels) for marker in screen.require)
               and not any(present(snapshot, marker, labels) for marker in screen.forbid)]
    if len(matched) == 1:
        return matched[0]
    if matched:
        raise MatchError(f"Ambiguous screen: {', '.join(f'{m.app}/{m.screen}' for m in matched)}")
    visible = [text for element in snapshot.elements for text in element.texts][:30]
    raise MatchError(f"Unknown screen; visible: {visible}")
