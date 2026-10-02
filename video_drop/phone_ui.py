"""Measured iPhone geometry for the few controls without accessible labels.

Accessible element centers remain the primary targets. Reference coordinates
are allowed only for an observed, verified screen and are scaled to the current
portrait viewport; callers must check the resulting screen after a tap.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from PIL import Image


REFERENCE_WIDTH = 440
REFERENCE_HEIGHT = 956
# Bottom safe-area inset (home indicator) on every Face ID iPhone, in points.
HOME_INDICATOR = 34.0


@dataclass(frozen=True)
class PhoneLayout:
    width: float
    height: float

    @classmethod
    def from_info(cls, info: dict) -> "PhoneLayout":
        width, height = float(info["width"]), float(info["height"])
        if width <= 0 or height <= 0 or not 0.38 <= width / height <= 0.62:
            raise ValueError(f"Expected an iPhone upright in portrait: {width:g} x {height:g}")
        return cls(width, height)

    @property
    def bottom_inset(self) -> float:
        """Face ID iPhones are taller than 2:1 and keep a home-indicator strip; Home-button models have none."""
        return HOME_INDICATOR if self.height / self.width > 2 else 0.0

    def reference_point(self, x: float, y: float) -> tuple[float, float]:
        """Scale a measured 440 x 956 fallback point to this phone."""
        if not (0 <= x <= REFERENCE_WIDTH and 0 <= y <= REFERENCE_HEIGHT):
            raise ValueError("Reference point is outside the measured phone")
        return x * self.width / REFERENCE_WIDTH, y * self.height / REFERENCE_HEIGHT

    def bottom_right_point(self, x: float, y: float) -> tuple[float, float]:
        """Move a measured 440 x 956 point for a control pinned to the bottom-right safe area.

        iOS keeps such a control a fixed number of points from the right edge
        and from the bottom safe-area edge, so proportional scaling drifts it.
        """
        if not (0 <= x <= REFERENCE_WIDTH and 0 <= y <= REFERENCE_HEIGHT - HOME_INDICATOR):
            raise ValueError("Reference point is outside the measured safe area")
        return (self.width - (REFERENCE_WIDTH - x),
                self.height - self.bottom_inset - (REFERENCE_HEIGHT - HOME_INDICATOR - y))

    def bottom_sheet_point(self, x: float, y: float) -> tuple[float, float]:
        """Move a measured 440 x 956 point for a control in a full-width sheet on the bottom safe area.

        The sheet keeps its rows a fixed number of points above the bottom
        safe-area edge, while its buttons share the width proportionally.
        """
        if not (0 <= x <= REFERENCE_WIDTH and 0 <= y <= REFERENCE_HEIGHT - HOME_INDICATOR):
            raise ValueError("Reference point is outside the measured safe area")
        return (x * self.width / REFERENCE_WIDTH,
                self.height - self.bottom_inset - (REFERENCE_HEIGHT - HOME_INDICATOR - y))

    def contains(self, row: dict) -> bool:
        return 0 <= row.get("x", -1) <= self.width and 0 <= row.get("y", -1) <= self.height

    def relative_band(self, row: dict, *, left: float = 0, right: float = 1,
                      top: float = 0, bottom: float = 1) -> bool:
        return self.contains(row) and (left * self.width <= row["x"] <= right * self.width
                                       and top * self.height <= row["y"] <= bottom * self.height)


def share_app_position(rows: list[dict], name: str, layout: PhoneLayout) -> tuple[str, dict | None]:
    """Locate a share app in the icon rail without tapping a clipped icon.

    iOS exposes cells just outside the viewport. Their centers can be at x=0
    while the visible pixels belong to the neighboring app, so a tap there can
    open the wrong destination. Return a scroll direction until the requested
    icon is comfortably inside the viewport.
    """
    row = _share_cell(rows, name, layout)
    if row is None:
        return "left", None
    if row["x"] < 0.12 * layout.width:
        return "right", None
    if row["x"] > 0.88 * layout.width:
        return "left", None
    return "tap", row


def _share_cell(rows: list[dict], name: str, layout: PhoneLayout) -> dict | None:
    rail = [row for row in rows if row.get("type") == "Cell"
            and 0.3 * layout.height <= row.get("y", -1) <= 0.55 * layout.height]
    found = [row for row in rail if row.get("text", "").casefold() == name.casefold()]
    if len(found) > 1:
        raise ValueError(f"Ambiguous share app: {name}")
    return found[0] if found else None


def share_rail_labels(rows: list[dict], layout: PhoneLayout) -> tuple:
    """What the app rail shows now, to tell a scroll that moved it from one at its end."""
    return tuple(sorted((row.get("text", ""), round(row.get("x", 0))) for row in rows if row.get("type") == "Cell"
                        and 0.3 * layout.height <= row.get("y", -1) <= 0.55 * layout.height))


def share_rail_y(rows: list[dict], name: str, layout: PhoneLayout, fallback: float) -> float:
    """Scroll the rail on the row where iOS shows the requested app, even while clipped.

    The share sheet sits at a fixed offset below the safe-area top, so a
    proportional y lands on a different row on shorter phones.
    """
    row = _share_cell(rows, name, layout)
    return float(row["y"]) if row is not None else fallback


def first_frame_point(slider) -> tuple[float, float]:
    """The leftmost frame of YouTube's thumbnail slider, from its live frame."""
    return slider.left + min(5.0, slider.width / 2), slider.top + slider.height / 2


def youtube_identity(labels: list[str]) -> str:
    """Read only the channel handle attached to YouTube's composer identity chip."""
    marker = "id.elements.components.identity_chip_component"
    if marker not in labels:
        raise ValueError("YouTube composer identity is missing")
    index = labels.index(marker)
    if index + 1 >= len(labels):
        raise ValueError("YouTube composer channel label is missing")
    label = labels[index + 1]
    if ", @" not in label:
        raise ValueError(f"YouTube composer channel label is unexpected: {label!r}")
    return "@" + label.rsplit(", @", 1)[1].casefold()


def youtube_page_account(labels: list[str]) -> str:
    """Read the selected channel in YouTube's You-tab header."""
    marker = "id.elements.components.page_header"
    if marker not in labels:
        raise ValueError("YouTube account header is missing")
    nearby = labels[labels.index(marker) + 1:][:5]
    handles = [label.casefold() for label in nearby if re.fullmatch(r"@[A-Za-z0-9._-]+", label)]
    if len(handles) != 1:
        raise ValueError(f"YouTube account header is ambiguous: {handles}")
    return handles[0]


def filled_radio(image: Image.Image, layout: PhoneLayout, x: float, y: float) -> bool:
    """Read the center of an observed YouTube radio, not its unreliable AX value.

    A selected circle's center matches its ring; an empty circle's center
    matches the surrounding background. Compare those samples so dark and
    light phone themes follow the same rule.
    """
    sx, sy = image.width / layout.width, image.height / layout.height
    cx, cy = round(x * sx), round(y * sy)
    rx = round((x - 10) * sx)
    bx = round((x - 20) * sx)
    if min(cx, cy, rx, bx) < 0 or cx >= image.width or cy >= image.height:
        raise ValueError("Radio center is outside the screen")
    level = lambda px: sum(image.getpixel((px, cy))[:3]) / 3
    center, ring, background = level(cx), level(rx), level(bx)
    if abs(ring - background) < 80:
        raise ValueError("YouTube radio ring is not distinct from its background")
    if abs(center - ring) + 40 < abs(center - background):
        return True
    if abs(center - background) + 40 < abs(center - ring):
        return False
    raise ValueError("YouTube radio pixels are ambiguous")


def size_shown(size_bytes: int, labels) -> bool:
    """True if any label shows this file size the way OneDrive or iOS rounds it.

    OneDrive and the iOS share sheet use MB or GB, base 1000 or 1024, and 0-2
    decimals ("271.2 MB", "258.6 MB", "1 GB", "1.1 GB"). A different size fails.
    """
    forms = set()
    for base in (1_000, 1_024):
        for unit, power in (("MB", 2), ("GB", 3)):
            value = size_bytes / base ** power
            if unit == "MB" and value >= base or unit == "GB" and value < 1:
                continue
            for digits in (0, 1, 2):
                forms.add(f"{value:.{digits}f} {unit}")
    pattern = re.compile(r"(?<![\d.])(" + "|".join(re.escape(form) for form in forms) + r")(?![\d])")
    return any(pattern.search(label) for label in labels)
