"""Read a post back from the native apps, offline, so a final tap ends in a receipt.

Pure verifiers. Each takes what scripts/phone_receipts.py read from the phone
(accessibility elements, a go-ios screenshot) plus the source's first frame, and
returns the evidence or raises ReceiptError. Nothing here touches the phone or
the database. A screen that is not exactly as recorded fails closed: the
destination stays unconfirmed, which is honest, instead of posted, which might
not be true.

Recorded on the reference iPhone 16 Pro Max (440 x 956 pt, 3x), iOS 26.7,
2026-09-30: the Instagram profile header (fixtures instagram/profile-header-*)
and the profile grid after a Post now reel (evidence release5-instagram-receipt-grid).
Threads' in-progress profile row is built from the operator's 2026-09-30 notes;
Facebook, TikTok and YouTube have no recordings yet, see ROUTES.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .phone_ui import PhoneLayout
from .screens.snapshot import Element


class ReceiptError(ValueError):
    pass


# Newest reel tile vs its source's first frame, 2026-09-30 grid: 2.5. The nearest other
# clip on that grid (same caption template, another day) scored 13.7.
GRID_COVER_LIMIT = 8.0
# Instagram lets a profile pin at most three posts, and pinned posts lead the grid.
PIN_SLOTS = 3
# The compared band of a grid tile: above it sits the reel/pin icon, below it the view count.
TILE_BAND = (0.16, 0.84)
# Below this fraction of the screen height the grid's tab row is stuck under the status
# bar, so rows may be hidden under it; above it the grid is still in its natural place.
STICKY_TABS = 0.13
# The corner icon on a grid tile, in points from the tile's top-right corner. Icons keep
# their point size on every iPhone; only the tiles scale with the screen width.
ICON_BOX = (26.0, 6.0, 20.0)  # inset from the right edge, inset from the top, side
PURE_WHITE = 248  # the icon glyph is drawn 255 white; a bright frame behind it is ~220
THREADS_ROW_LEAD = 30.0  # points above a post's author link that still belong to that post


# ---- text -----------------------------------------------------------------------


def plain(text: str) -> str:
    """Compare captions the way the apps show them: NFC, straight apostrophes, single spaces."""
    text = unicodedata.normalize("NFC", text or "").replace("’", "'").replace("‘", "'")
    return " ".join(text.split())


def handle(account: str) -> str:
    return account.strip().lstrip("@").casefold()


# ---- before the final tap ---------------------------------------------------------


def baseline_path(state_dir: Path, release_id: int, platform: str) -> Path:
    return Path(state_dir) / "receipts" / f"release{int(release_id)}-{platform}-before.json"


def save_baseline(state_dir: Path, release_id: int, platform: str, values: dict) -> Path:
    """Keep what the profile showed before the upload (Instagram: its post count).

    Stored beside the database under the ignored state directory, not in the schema.
    A later save for the same release replaces it: the last read before the tap counts.
    """
    path = baseline_path(state_dir, release_id, platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"releaseId": int(release_id), "platform": platform,
              "savedAt": datetime.now(timezone.utc).isoformat(), **values}
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_baseline(state_dir: Path, release_id: int, platform: str) -> dict | None:
    path = baseline_path(state_dir, release_id, platform)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("releaseId") != int(release_id) or data.get("platform") != platform:
        raise ReceiptError(f"{path.name} does not belong to release {release_id} {platform}")
    return data


# ---- Instagram profile header ------------------------------------------------------


def _one(found: list[Element], what: str) -> Element:
    unique = {(e.left, e.top, e.width, e.height): e for e in found}
    if len(unique) != 1:
        raise ReceiptError(f"Expected one {what} on the phone, found {len(unique)}")
    return next(iter(unique.values()))


def instagram_profile(elements) -> dict:
    """The active account and exact post count from the profile header.

    The header names the handle on user-switch-title-button and the count on
    user-detail-header-media-button ("90 posts"). An abbreviated count
    ("1.2 thousand posts") cannot prove a +1 and fails closed.
    """
    title = _one([e for e in elements if e.type == "Button" and e.name == "user-switch-title-button"],
                 "Instagram profile header")
    if not re.fullmatch(r"[A-Za-z0-9._]+", title.label):
        raise ReceiptError("Instagram profile header has no readable handle")
    count = _one([e for e in elements if e.type == "Button" and e.name == "user-detail-header-media-button"],
                 "Instagram post count")
    shown = " ".join(count.value.split())
    match = re.fullmatch(r"([\d,]+) posts?", shown)
    if not match:
        raise ReceiptError(f"Instagram's post count is not exact ({shown!r}); cannot prove a new post")
    return {"account": "@" + title.label.casefold(), "posts": int(match.group(1).replace(",", ""))}


def instagram_count_receipt(before: int, after: int) -> dict:
    """Exactly one new post since the pre-upload read.

    More than one could be another release with the same first frame (two cuts of
    one clip share it), so the newest tile would no longer identify this upload.
    """
    if after == before + 1:
        return {"postsBefore": before, "postsAfter": after}
    if after == before:
        raise ReceiptError(f"Instagram still shows {after} posts; the reel is not on the profile yet")
    if after > before:
        raise ReceiptError(f"Instagram's post count rose by {after - before} ({before} to {after}); "
                           "cannot tell which post is this release")
    raise ReceiptError(f"Instagram's post count fell ({before} to {after}); check the profile by hand")


# ---- grid tiles -------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    left: float
    top: float
    width: float
    height: float
    pinned: bool | None = None  # None: the tile does not say

    @property
    def frame(self) -> list[float]:
        return [round(v, 1) for v in (self.left, self.top, self.width, self.height)]


def detailed(frame: Image.Image) -> Image.Image:
    frame = frame.convert("RGB")
    if min(ImageStat.Stat(frame.resize((90, 160))).stddev) < 20:
        raise ReceiptError("The first frame has too little visual detail to identify the post")
    return frame


def _scale(screenshot: Image.Image, layout: PhoneLayout) -> tuple[float, float]:
    if abs(screenshot.width / screenshot.height - layout.width / layout.height) > 0.01:
        raise ReceiptError("The screenshot does not match the iPhone screen")
    return screenshot.width / layout.width, screenshot.height / layout.height


def _center_crop(frame: Image.Image, aspect: float) -> Image.Image:
    fw, fh = frame.size
    if fw / fh > aspect:
        width = fh * aspect
        box = ((fw - width) / 2, 0, (fw + width) / 2, fh)
    else:
        height = fw / aspect
        box = (0, (fh - height) / 2, fw, (fh + height) / 2)
    return frame.crop(tuple(round(v) for v in box))


def _band(image: Image.Image) -> Image.Image:
    top, bottom = TILE_BAND
    return image.crop((0, round(image.height * top), image.width, round(image.height * bottom))).resize((64, 48))


def tile_difference(screenshot: Image.Image, layout: PhoneLayout, tile: Tile, frame: Image.Image) -> float:
    """Mean difference (0-255) between a grid tile and the first frame center-cropped to the tile.

    Grids fill each tile with the centered cover; only the middle band is compared,
    because the platform draws its icon above it and the view count below it.
    """
    sx, sy = _scale(screenshot, layout)
    box = (tile.left * sx, tile.top * sy, (tile.left + tile.width) * sx, (tile.top + tile.height) * sy)
    shown = _band(screenshot.convert("RGB").crop(tuple(round(v) for v in box)))
    expected = _band(_center_crop(frame.convert("RGB"), tile.width / tile.height))
    return sum(ImageStat.Stat(ImageChops.difference(shown, expected)).mean) / 3


def _fill(mask: Image.Image, box: tuple[float, float, float, float], scale: tuple[float, float]) -> float:
    sx, sy = scale
    region = mask.crop((round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)))
    return ImageStat.Stat(region).mean[0] / 255


def _icon_at(mask: Image.Image, left: float, top: float, scale: tuple[float, float]) -> str:
    def at(x0, y0, x1, y1):
        return _fill(mask, (left + x0, top + y0, left + x1, top + y1), scale)
    tl, tr, bl, br, center = at(3, 3, 7, 7), at(13, 3, 17, 7), at(3, 13, 7, 17), at(13, 13, 17, 17), at(8, 8, 12, 12)
    # Reel: a filled rounded square with a play-triangle hole in its middle.
    if min(tl, tr, bl, br) >= 0.6 and center <= 0.35:
        return "reel"
    # Pin: a push-pin tilted from the top-right down to the bottom-left; the other corners stay clear.
    if tl <= 0.15 and br <= 0.15 and tr >= 0.5 and bl >= 0.25 and center >= 0.5:
        return "pin"
    return "unknown"


def corner_icon(screenshot: Image.Image, layout: PhoneLayout, tile: Tile) -> str:
    """"pin", "reel" or "unknown" for the white glyph in a grid tile's top-right corner.

    Only pure-white pixels count, so a bright frame behind the glyph (measured ~220)
    does not; a pure-white frame there leaves the tile "unknown". A 1 pt search
    tolerates rounding between screen scales; both shapes seen at once is "unknown".
    """
    scale = _scale(screenshot, layout)
    right_inset, top_inset, side = ICON_BOX
    left, top = tile.left + tile.width - right_inset, tile.top + top_inset
    sx, sy = scale
    crop_box = (left - 1, top - 1, left + side + 1, top + side + 1)
    region = screenshot.convert("RGB").crop(tuple(round(v * s) for v, s in zip(crop_box, (sx, sy, sx, sy))))
    red, green, blue = region.split()
    mask = ImageChops.darker(red, ImageChops.darker(green, blue)).point(lambda v: 255 if v >= PURE_WHITE else 0)
    seen = {_icon_at(mask, 1 + dx, 1 + dy, scale) for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - {"unknown"}
    return seen.pop() if len(seen) == 1 else "unknown"


def newest_grid_match(screenshot: Image.Image, layout: PhoneLayout, tiles: list[Tile], first_frame: Image.Image,
                      *, limit: float = GRID_COVER_LIMIT, pin_slots: int = PIN_SLOTS, app: str = "Instagram") -> dict:
    """The newest unpinned tile, in reading order, must show the source's first frame.

    Pinned tiles lead a grid, at most ``pin_slots`` of them, so a tile past those slots
    is unpinned. Inside them, a tile whose pin state is unreadable is pinned only when a
    later tile is a proven pin; otherwise it may be the newest post and the check fails
    closed rather than let an older post (possibly with the same first frame) stand in.
    """
    frame = detailed(first_frame)
    states = [tile.pinned if index < pin_slots else False for index, tile in enumerate(tiles)]
    for index, tile in enumerate(tiles):
        if index >= pin_slots and tile.pinned:
            raise ReceiptError(f"{app} shows a pinned tile after {pin_slots} tiles; the grid is not as recorded")
        if states[index] is None and True in states[index + 1:pin_slots]:
            states[index] = True
        if states[index] is True and False in states[:index]:
            raise ReceiptError(f"{app} shows a pinned tile after an unpinned one; the grid is not as recorded")
    newest = next((index for index, state in enumerate(states) if state is not True), None)
    if newest is None:
        raise ReceiptError(f"No unpinned {app} tile is fully on screen")
    if states[newest] is None:
        raise ReceiptError(f"Cannot tell whether {app} tile {newest + 1} is pinned; its corner icon is unreadable")
    tile = tiles[newest]
    score = tile_difference(screenshot, layout, tile, frame)
    if score > limit:
        raise ReceiptError(f"{app}'s newest post is not this clip: its tile differs from the first frame "
                           f"(difference {score:.1f})")
    return {"tileIndex": newest, "tile": tile.frame, "difference": round(score, 1), "pinnedTiles": newest}


# ---- Instagram grid ---------------------------------------------------------------


def _grid_tab(elements, layout: PhoneLayout) -> Element:
    return _one([e for e in elements if e.type == "Cell" and e.label == "Grid" and layout.contains({"x": e.x, "y": e.y})],
                "Instagram Grid tab")


def _tab_bar_top(elements, layout: PhoneLayout) -> float:
    bars = [e.top for e in elements if e.name == "tab-bar-container" and 0 < e.top < layout.height]
    return min(bars, default=layout.height)


def _thumbnails(elements) -> list[Element]:
    found = {(e.left, e.top, e.width, e.height): e for e in elements
             if e.type == "Button" and e.name == "media-thumbnail-cell"}
    return sorted(found.values(), key=lambda e: (round(e.top), e.left))


def instagram_grid_scroll(elements, layout: PhoneLayout, rows: int = 2) -> float:
    """How far to move the profile up so its first ``rows`` grid rows clear the floating tab bar.

    Stops short of sticking the Grid tab row under the status bar: a stuck row can hide
    tiles above the first visible one. 0 means the rows are already on screen.
    """
    grid = _grid_tab(elements, layout)
    tiles = _thumbnails(elements)
    tile_height = tiles[0].height if tiles else (layout.width - 2) / 3 * 195 / 146
    needed = grid.top + grid.height + rows * (tile_height + 1) - (_tab_bar_top(elements, layout) - 4)
    if needed <= 0:
        return 0.0
    room = grid.top - STICKY_TABS * layout.height - 8
    if needed > room:
        raise ReceiptError("Instagram's grid cannot show its first rows without hiding one under the tabs")
    return (needed + room) / 2


def instagram_grid_tiles(elements, screenshot: Image.Image, layout: PhoneLayout) -> list[Tile]:
    """The grid's leading tiles, fully on screen, in reading order, each with its pin state.

    The Grid tab must still be in its natural place (not stuck under the status bar)
    and the first tiles must start right under it, so no row is hidden above them.
    """
    grid = _grid_tab(elements, layout)
    if grid.top < STICKY_TABS * layout.height:
        raise ReceiptError("Instagram's grid is scrolled past its first row")
    cells = _thumbnails(elements)
    if not cells:
        raise ReceiptError("Instagram's profile shows no grid tiles")
    if not grid.top + grid.height - 1 <= cells[0].top <= grid.top + grid.height + 4:
        raise ReceiptError("Instagram's grid does not start right under its tabs")
    bar = _tab_bar_top(elements, layout)
    tiles = []
    for cell in cells:
        if cell.top + cell.height > bar + 0.5 or cell.left < -0.5 or cell.left + cell.width > layout.width + 0.5:
            break  # reading order must stay complete: stop at the first tile not fully shown
        tile = Tile(cell.left, cell.top, cell.width, cell.height)
        icon = corner_icon(screenshot, layout, tile)
        tiles.append(Tile(cell.left, cell.top, cell.width, cell.height,
                          pinned={"pin": True, "reel": False}.get(icon)))
    return tiles


def verified_instagram_post(header, grid, screenshot: Image.Image, layout: PhoneLayout,
                            first_frame: Image.Image, *, account: str, posts_before: int) -> dict:
    """Post now receipt: the right account, exactly one new post, and it is this clip.

    ``header`` is the profile read before scrolling (handle and count), ``grid`` and
    ``screenshot`` the read after it.
    """
    profile = instagram_profile(header)
    if handle(profile["account"]) != handle(account):
        raise ReceiptError(f"Instagram shows {profile['account']}, not {account}")
    count = instagram_count_receipt(int(posts_before), profile["posts"])
    match = newest_grid_match(screenshot, layout, instagram_grid_tiles(grid, screenshot, layout), first_frame)
    return {"account": profile["account"], **count, **match,
            "verification": "instagram_profile_count_newest_tile_first_frame"}


# ---- Threads profile ----------------------------------------------------------------


def threads_newest_post(elements, layout: PhoneLayout, *, account: str, caption: str) -> dict:
    """The newest post on the Threads profile is the account's, finished, with the exact caption.

    While a crosspost is still uploading the profile shows an ActivityIndicator labelled
    "In progress" beside the author, and the caption is still the composer's
    composer-text-view: either means not posted yet. Pinned posts are skipped.
    """
    for e in elements:
        if e.type == "ActivityIndicator" and "in progress" in e.label.casefold():
            raise ReceiptError("Threads is still posting (In progress); not a receipt yet")
        if e.name == "composer-text-view":
            raise ReceiptError("Threads still shows the post in its composer; not a receipt yet")
        if plain(e.label).startswith(("Posting…", "Posting...")):
            raise ReceiptError("Threads is still posting; not a receipt yet")
    wanted = plain(caption)
    if not wanted:
        raise ReceiptError("Expected Threads caption is empty")
    tabs = [(a, b) for a in elements if a.label == "Threads" and a.height < 80
            for b in elements if b.label == "Replies" and b.height < 80 and abs(a.y - b.y) < 12]
    if len({(round(a.y), round(b.y)) for a, b in tabs}) != 1:
        raise ReceiptError("Threads profile tabs are not on screen")
    below = max(max(a.top + a.height, b.top + b.height) for a, b in tabs)
    bottom = min((e.top for e in elements if e.type == "TabBar" and e.top > below), default=layout.height)
    authors = sorted({round(e.top): e for e in elements
                      if e.type == "Link" and below <= e.top < bottom and re.fullmatch(r"[A-Za-z0-9._]+", e.label)}.values(),
                     key=lambda e: e.top)
    if not authors:
        raise ReceiptError("Threads profile shows no posts")
    for index, author in enumerate(authors):
        if handle(author.label) != handle(account):
            raise ReceiptError(f"The newest Threads post is by {author.label}, not {account}")
        # A post's rows start a little above its author link (a "Pinned" line sits there).
        start = max(below, author.top - THREADS_ROW_LEAD)
        end = authors[index + 1].top - THREADS_ROW_LEAD if index + 1 < len(authors) else bottom
        block = [e for e in elements if start <= e.top < end]
        if any(e.label == "Pinned" for e in block):
            continue
        shown = [e for e in block if wanted in (plain(e.label), plain(e.value))]
        if not shown:
            raise ReceiptError("The newest Threads post does not have the approved caption")
        return {"account": "@" + handle(author.label), "caption": wanted, "postIndex": index,
                "verification": "threads_profile_newest_caption"}
    raise ReceiptError("Threads profile shows only pinned posts")


# ---- routes -----------------------------------------------------------------------

# How each destination's receipt is read back. "verify": built and tested here.
# "needs_recording": the screens were never recorded on the phone, so nothing is read
# and the destination stays unconfirmed; "record" lists what one live session must capture.
ROUTES = {
    ("instagram", "post_now"): {
        "status": "verify",
        "navigation": "Instagram -> Profile tab (blind tap: its feed autoplays) -> header read "
                      "-> slow swipe until two grid rows clear the tab bar -> grid read + go-ios screenshot",
        "check": "exact post count = before + 1, handle matches, newest unpinned tile = first frame",
        "record": ["Profile after the swipe, with the accessibility tree (grid tiles' frames were "
                   "measured from the header read only)"],
    },
    ("instagram", "schedule"): {
        "status": "verify",
        "navigationUnrecorded": True,
        "navigation": "Instagram -> Profile tab -> 'Tap to open settings & activity' -> row "
                      "'Scheduled content' (scroll up to 4 times) -> Scheduled content list",
        "check": "instagram_schedule.verified_scheduled_reel: caption, slot label, first-frame cover",
        "record": ["Settings & activity menu showing the Scheduled content row",
                   "Scheduled content list holding a scheduled reel (tree + screenshot)"],
    },
    ("threads", "post_now"): {
        "status": "verify",
        "navigation": "Threads -> Profile tab (blind tap: the feed may autoplay) -> profile read",
        "check": "newest unpinned post is the account's, no 'In progress', exact caption",
        "record": ["Threads profile with the finished crosspost (tree), to confirm how a crossposted "
                   "Instagram caption with hashtags is shown (Threads keeps one topic tag)",
                   "Whether the profile autoplays the new video post; if it wedges WDA, read it by pixels"],
    },
    ("threads", "schedule"): {
        "status": "needs_recording",
        "plan": "Threads' native schedule is not built yet (scripts/phone_threads.py refuses it). Once it "
                "is: Threads composer '...' menu -> Scheduled posts list: caption + slot label.",
        "record": ["Threads Scheduled posts list with one scheduled video post"],
    },
    ("facebook", "post_now"): {
        "status": "needs_recording",
        "plan": "Meta Business Suite (static lists, no autoplay) -> the Page -> Content -> Reels -> "
                "Published: newest reel's caption equals Instagram's and its thumbnail = first frame. "
                "Fallback: Facebook app -> Page -> Reels tab grid (tile compare like Instagram).",
        "record": ["Meta Business Suite home with the Page name", "Content -> Reels -> Published list",
                   "Same list while the crosspost is still processing, to learn its pending state"],
    },
    ("facebook", "schedule"): {
        "status": "needs_recording",
        "plan": "Meta Business Suite -> Content -> Reels -> Scheduled: a row with Instagram's caption at the "
                "slot. Instagram's Scheduled content row does not show the Facebook crosspost.",
        "record": ["Meta Business Suite Content -> Reels -> Scheduled with one crossposted reel"],
    },
    ("tiktok", "post_now"): {
        "status": "needs_recording",
        "plan": "Never open For You: TikTok cold-starts into it and a tree read there freezes WDA. "
                "Either launch TikTok and blind-tap the Profile tab (bottom-right, bottom_sheet_point) "
                "before any read, confirming the profile by go-ios pixels, or open the profile's "
                "tiktok.com/@<handle> link from Safari so the universal link lands on the profile. "
                "Then newest_grid_match on the Videos grid: newest tile without a 'Pinned' badge = first frame.",
        "record": ["TikTok profile Videos grid (tree + screenshot), including a pinned video",
                   "Which of the two routes above reaches the profile without For You playing",
                   "Whether grid tiles' accessibility labels carry the caption (would guard two clips "
                   "sharing a first frame; TikTok shows no post count)"],
    },
    ("youtube", "post_now"): {
        "status": "needs_recording",
        "plan": "YouTube Studio -> Content -> Shorts: newest row's title and 'Public' + thumbnail = first "
                "frame. Fallback: YouTube -> You -> Your videos -> Shorts (close the miniplayer first).",
        "record": ["YouTube Studio Content -> Shorts list (or You -> Your videos) with the new Short"],
    },
    ("youtube", "schedule"): {
        "status": "needs_recording",
        "plan": "YouTube Studio -> Content -> Shorts: row with the title, 'Scheduled' and the slot.",
        "record": ["The same list holding a scheduled Short"],
    },
}
ROUTES[("tiktok", "schedule")] = {**ROUTES[("tiktok", "post_now")],
                                  "plan": "TikTok is posted by this app at the slot, so its receipt is a "
                                          "posted one: " + ROUTES[("tiktok", "post_now")]["plan"]}


def receipt_route(platform: str, mode: str) -> dict:
    try:
        return {"platform": platform, "mode": mode, **ROUTES[(platform, mode)]}
    except KeyError:
        raise ReceiptError(f"No receipt route for {platform} in {mode} mode") from None
