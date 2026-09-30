"""Find an unlabeled icon near a matched anchor.

Icons are cropped from the same phone at one pixel per point, so matching
needs no scale search: zero-mean normalized cross-correlation over a band.
"""

from __future__ import annotations

import io

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image

MIN_SCORE = 0.8
AMBIGUITY_MARGIN = 0.03


class IconError(RuntimeError):
    pass


class AmbiguousIcon(IconError):
    """Two near-equal matches in one band; the matcher must not fall back from this."""


def to_points(screenshot_png: bytes, width: float, height: float) -> Image.Image:
    image = Image.open(io.BytesIO(screenshot_png)).convert("L")
    return image.resize((round(width), round(height)), Image.LANCZOS)


def locate(screen: Image.Image, icon: Image.Image, band: tuple[float, float, float, float]) -> tuple[float, float]:
    left, top = max(0, int(band[0])), max(0, int(band[1]))
    right, bottom = min(screen.width, int(band[2])), min(screen.height, int(band[3]))
    area = np.asarray(screen.convert("L").crop((left, top, right, bottom)), dtype=np.float64)
    needle = np.asarray(icon.convert("L"), dtype=np.float64)
    if area.shape[0] < needle.shape[0] or area.shape[1] < needle.shape[1]:
        raise IconError("Icon not found: search band is smaller than the icon")
    needle = needle - needle.mean()
    windows = sliding_window_view(area, needle.shape)
    centered = windows - windows.mean(axis=(2, 3), keepdims=True)
    denominator = np.sqrt((centered ** 2).sum(axis=(2, 3)) * (needle ** 2).sum())
    scores = np.where(denominator > 0, (centered * needle).sum(axis=(2, 3)) / np.maximum(denominator, 1e-9), 0)
    best = np.unravel_index(int(scores.argmax()), scores.shape)
    if scores[best] < MIN_SCORE:
        raise IconError(f"Icon not found (best match {scores[best]:.2f})")
    h, w = needle.shape
    suppressed = scores.copy()
    suppressed[max(0, best[0] - h + 1):best[0] + h, max(0, best[1] - w + 1):best[1] + w] = -1
    if suppressed.max() >= scores[best] - AMBIGUITY_MARGIN:
        raise AmbiguousIcon("Icon match is ambiguous inside the search band")
    return float(left + best[1] + w / 2), float(top + best[0] + h / 2)
