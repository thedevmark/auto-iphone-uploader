"""Receipt read-back: every verifier on synthetic screens, with the negative cases that must not count.

The screens copy the geometry recorded on the reference phone (440 x 956 pt, 3x):
the Instagram profile grid and header, the Threads profile row. Icons are the pixel
masks measured from the recorded grid. Names and captions are placeholders.
``LocalEvidenceTests`` replays the ignored local recordings and skips without them.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image, ImageDraw

from scripts import phone_receipts
from video_drop import receipts as rc
from video_drop.core import Store
from video_drop.phone_ui import PhoneLayout
from video_drop.screens.snapshot import Element

LAYOUT = PhoneLayout(440, 956)
SCALE = 3
COLUMNS = (0.0, 147.0, 294.0)
TILE_W, TILE_H = 146.0, 195.0
GRID_TOP = 300.0  # the Grid tab row, still in its natural place after the receipt swipe
FIRST_ROW = GRID_TOP + 42
BAR_TOP = 873.0
ROOT = Path(__file__).resolve().parent.parent

# The white glyphs in a tile's top-right corner, one character per point, measured
# from the recorded grid at (tile right - 26, tile top + 6).
PIN = """
....................
....................
............##......
............###.....
...........#####....
..........#######...
.........#########..
.......############.
....#############...
...############.....
....###########.....
.....#########......
......#######.......
......#######.......
.....##.#####.......
....##...###........
...##.....##........
..##................
....................
....................
"""
REEL = """
....................
....................
.....##########.....
....#############...
...###############..
...###############..
...#####.#########..
...####....#######..
..#####......#####..
..#####.......####..
..#####.......####..
..#####......#####..
...####....#######..
...#####.#########..
...###############..
...###############..
....#############...
.....##########.....
....................
....................
"""


def clip(*colors: str) -> Image.Image:
    """A 9:16 first frame with enough structure to identify it."""
    background, top, bottom, spot = colors
    frame = Image.new("RGB", (360, 640), background)
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 150, 360, 320), fill=top)
    draw.rectangle((0, 320, 360, 490), fill=bottom)
    draw.ellipse((110, 260, 250, 400), fill=spot)
    draw.rectangle((40, 60, 150, 130), fill=spot)
    return frame


OURS = clip("#152020", "#f2c849", "#2878bd", "#e92573")
OTHERS = [clip("#d5eafb", "#172bbd", "#e93113", "#12cb66"), clip("#3a0d4f", "#9ad14b", "#f7f7f7", "#1a1a1a"),
          clip("#40300a", "#1ec7c7", "#c71e7b", "#ffffff"), clip("#0a2f40", "#f28b30", "#6b30f2", "#d9f230"),
          clip("#202020", "#808080", "#c0c0c0", "#ff0000")]


def tile_frame(index: int, top: float = FIRST_ROW) -> tuple[float, float]:
    return COLUMNS[index % 3], top + (index // 3) * (TILE_H + 1)


def paint_icon(image: Image.Image, left: float, top: float, art: str) -> None:
    draw = ImageDraw.Draw(image)
    x0, y0 = left + TILE_W - 26, top + 6
    for row, line in enumerate(art.strip().splitlines()):
        for col, char in enumerate(line):
            if char == "#":
                draw.rectangle(((x0 + col) * SCALE, (y0 + row) * SCALE,
                                (x0 + col + 1) * SCALE - 1, (y0 + row + 1) * SCALE - 1), fill=(255, 255, 255))


def grid_screen(frames: list[Image.Image], icons: list[str | None], *, corner: tuple | None = None,
                top: float = FIRST_ROW) -> Image.Image:
    """The profile grid after the receipt swipe; ``corner`` fills each tile's icon corner first."""
    image = Image.new("RGB", (440 * SCALE, 956 * SCALE), "white")
    for index, (frame, icon) in enumerate(zip(frames, icons)):
        left, row_top = tile_frame(index, top)
        width, height = round(TILE_W * SCALE), round(TILE_H * SCALE)
        # Grids fill a 3:4 tile with the 9:16 cover's centered band.
        crop_height = frame.width * TILE_H / TILE_W
        shown = frame.crop((0, round((frame.height - crop_height) / 2), frame.width,
                            round((frame.height + crop_height) / 2))).resize((width, height))
        image.paste(shown, (round(left * SCALE), round(row_top * SCALE)))
        if corner is not None and corner[0] == index:
            ImageDraw.Draw(image).rectangle(((left + TILE_W - 30) * SCALE, row_top * SCALE,
                                             (left + TILE_W) * SCALE, (row_top + 30) * SCALE), fill=corner[1])
        if icon:
            paint_icon(image, left, row_top, {"pin": PIN, "reel": REEL}[icon])
    return image


def element(kind: str, label: str, frame: tuple, name: str = "", value: str = "") -> Element:
    return Element(kind, label, name, value, *frame)


def grid_elements(count: int, *, grid_top: float = GRID_TOP, first: float | None = None) -> list[Element]:
    first = grid_top + 42 if first is None else first
    items = [element("Cell", "Grid", (0, grid_top, 110, 41)),
             element("Cell", "Reels", (110, grid_top, 110, 41)),
             element("Other", "", (0, BAR_TOP, 440, 83), "tab-bar-container")]
    for index in range(count):
        left, top = tile_frame(index, first)
        items.append(element("Button", "Video by creator", (left, top, TILE_W, TILE_H), "media-thumbnail-cell"))
    return items


def header_elements(posts: str = "91 posts", handle: str = "creator", *, grid_top: float = 762.0) -> list[Element]:
    """The profile header as recorded (fixtures instagram/profile-header-*), placeholder handle."""
    return [element("Button", handle, (124, 31, 192, 112), "user-switch-title-button"),
            element("Button", "Tap to open settings & activity", (380, 65, 44, 44), "profile-more-button"),
            element("Button", "Posts count", (123, 159, 84, 78), "user-detail-header-media-button", posts),
            element("StaticText", posts.split()[0], (123, 167, 78, 20), posts.split()[0]),
            element("Cell", "Grid", (0, grid_top, 110, 41)),
            element("Other", "", (0, BAR_TOP, 440, 83), "tab-bar-container"),
            *[element("Button", "Video by creator", (left, grid_top + 43, TILE_W, TILE_H), "media-thumbnail-cell")
              for left in COLUMNS]]


class CornerIconTests(unittest.TestCase):
    def icon(self, image, index=0):
        left, top = tile_frame(index)
        return rc.corner_icon(image, LAYOUT, rc.Tile(left, top, TILE_W, TILE_H))

    def test_reads_pin_and_reel_glyphs(self):
        image = grid_screen(OTHERS[:2], ["pin", "reel"])
        self.assertEqual((self.icon(image, 0), self.icon(image, 1)), ("pin", "reel"))

    def test_pin_on_a_bright_frame_still_reads_as_a_pin(self):
        # Recorded: a pinned tile's corner was ~220 grey behind a 255 glyph.
        image = grid_screen(OTHERS[:1], ["pin"], corner=(0, (221, 221, 221)))
        self.assertEqual(self.icon(image), "pin")

    def test_pure_white_corner_or_no_glyph_is_unknown(self):
        self.assertEqual(self.icon(grid_screen(OTHERS[:1], ["pin"], corner=(0, (255, 255, 255)))), "unknown")
        self.assertEqual(self.icon(grid_screen(OTHERS[:1], [None])), "unknown")


class InstagramGridTests(unittest.TestCase):
    def tiles(self, image, count):
        return rc.instagram_grid_tiles(grid_elements(count), image, LAYOUT)

    def test_newest_unpinned_reel_is_the_first_frame(self):
        image = grid_screen([*OTHERS[:3], OURS, OTHERS[3]], ["pin", "pin", "pin", "reel", "reel"])
        tiles = self.tiles(image, 5)
        self.assertEqual([t.pinned for t in tiles], [True, True, True, False, False])
        match = rc.newest_grid_match(image, LAYOUT, tiles, OURS)
        self.assertEqual((match["tileIndex"], match["pinnedTiles"]), (3, 3))
        self.assertLess(match["difference"], 3)

    def test_wrong_first_frame_is_not_a_receipt(self):
        image = grid_screen([OTHERS[0], OURS], ["reel", "reel"])
        with self.assertRaisesRegex(rc.ReceiptError, "not this clip"):
            rc.newest_grid_match(image, LAYOUT, self.tiles(image, 2), OTHERS[2])

    def test_a_pinned_tile_showing_the_clip_does_not_count(self):
        # An older post of the same cut, pinned, while the newest post is something else.
        image = grid_screen([OURS, OTHERS[0]], ["pin", "reel"])
        with self.assertRaisesRegex(rc.ReceiptError, "not this clip"):
            rc.newest_grid_match(image, LAYOUT, self.tiles(image, 2), OURS)

    def test_the_newest_tile_decides_even_when_an_older_one_matches(self):
        image = grid_screen([OTHERS[0], OURS], ["reel", "reel"])
        with self.assertRaisesRegex(rc.ReceiptError, "not this clip"):
            rc.newest_grid_match(image, LAYOUT, self.tiles(image, 2), OURS)

    def test_unreadable_pin_slot_fails_closed_unless_a_later_pin_proves_it(self):
        blank = grid_screen([OTHERS[0], OURS], ["pin", "reel"], corner=(0, (255, 255, 255)))
        with self.assertRaisesRegex(rc.ReceiptError, "Cannot tell whether Instagram tile 1 is pinned"):
            rc.newest_grid_match(blank, LAYOUT, self.tiles(blank, 2), OURS)
        proven = grid_screen([OTHERS[0], OTHERS[1], OURS], ["pin", "pin", "reel"], corner=(0, (255, 255, 255)))
        self.assertEqual(rc.newest_grid_match(proven, LAYOUT, self.tiles(proven, 3), OURS)["tileIndex"], 2)

    def test_pins_only_lead_the_grid(self):
        image = grid_screen([OURS, OTHERS[0]], ["reel", "pin"])
        with self.assertRaisesRegex(rc.ReceiptError, "pinned tile after an unpinned one"):
            rc.newest_grid_match(image, LAYOUT, self.tiles(image, 2), OURS)

    def test_flat_first_frame_cannot_identify_a_post(self):
        image = grid_screen([OURS], ["reel"])
        with self.assertRaisesRegex(rc.ReceiptError, "too little visual detail"):
            rc.newest_grid_match(image, LAYOUT, self.tiles(image, 1), Image.new("RGB", (360, 640), "black"))

    def test_grid_scrolled_past_its_first_row_is_refused(self):
        image = grid_screen([OURS], ["reel"], top=130)
        with self.assertRaisesRegex(rc.ReceiptError, "scrolled past its first row"):
            rc.instagram_grid_tiles(grid_elements(1, grid_top=88), image, LAYOUT)

    def test_grid_must_start_right_under_its_tabs(self):
        image = grid_screen([OURS], ["reel"])
        with self.assertRaisesRegex(rc.ReceiptError, "start right under its tabs"):
            rc.instagram_grid_tiles(grid_elements(1, first=GRID_TOP + 42 + 196), image, LAYOUT)

    def test_tiles_under_the_floating_tab_bar_are_not_read(self):
        image = grid_screen([*OTHERS[:5], OURS, OTHERS[0]], ["reel"] * 7)
        # Rows start at 342: the third row ends at 929, below the tab bar at 873.
        self.assertEqual(len(self.tiles(image, 7)), 6)

    def test_scroll_brings_two_rows_above_the_tab_bar_without_sticking_the_tabs(self):
        header = header_elements()  # recorded: Grid tab at 762, first row at 805
        distance = rc.instagram_grid_scroll(header, LAYOUT)
        grid_top = 762 - distance
        self.assertGreater(grid_top, rc.STICKY_TABS * LAYOUT.height)
        self.assertLessEqual(grid_top + 41 + 2 * (TILE_H + 1), BAR_TOP - 4)
        self.assertEqual(rc.instagram_grid_scroll(header_elements(grid_top=GRID_TOP), LAYOUT), 0)
        # A tab bar this high leaves no swipe that shows two rows under an unstuck Grid tab.
        cramped = [e if e.name != "tab-bar-container" else element("Other", "", (0, 400, 440, 83), e.name)
                   for e in header_elements(grid_top=150)]
        with self.assertRaisesRegex(rc.ReceiptError, "cannot show its first rows"):
            rc.instagram_grid_scroll(cramped, LAYOUT)


class InstagramCountTests(unittest.TestCase):
    def test_header_gives_handle_and_exact_count(self):
        self.assertEqual(rc.instagram_profile(header_elements("1,204 posts", "Creator.Name")),
                         {"account": "@creator.name", "posts": 1204})
        self.assertEqual(rc.instagram_profile(header_elements("1 post"))["posts"], 1)

    def test_abbreviated_or_missing_header_fails_closed(self):
        with self.assertRaisesRegex(rc.ReceiptError, "not exact"):
            rc.instagram_profile(header_elements("1.2 thousand posts"))
        with self.assertRaisesRegex(rc.ReceiptError, "profile header"):
            rc.instagram_profile(header_elements()[1:])

    def test_exactly_one_new_post(self):
        self.assertEqual(rc.instagram_count_receipt(90, 91), {"postsBefore": 90, "postsAfter": 91})
        for after, message in ((90, "not on the profile yet"), (92, "rose by 2"), (89, "fell")):
            with self.subTest(after=after), self.assertRaisesRegex(rc.ReceiptError, message):
                rc.instagram_count_receipt(90, after)

    def test_post_now_receipt_needs_account_count_and_newest_tile(self):
        image = grid_screen([OTHERS[0], OURS], ["pin", "reel"])
        verified = rc.verified_instagram_post(header_elements("91 posts"), grid_elements(2), image, LAYOUT, OURS,
                                              account="@Creator", posts_before=90)
        self.assertEqual((verified["account"], verified["postsAfter"], verified["tileIndex"]), ("@creator", 91, 1))
        self.assertEqual(verified["verification"], "instagram_profile_count_newest_tile_first_frame")
        with self.assertRaisesRegex(rc.ReceiptError, "not @someone"):
            rc.verified_instagram_post(header_elements("91 posts"), grid_elements(2), image, LAYOUT, OURS,
                                       account="@someone", posts_before=90)
        with self.assertRaisesRegex(rc.ReceiptError, "not on the profile yet"):
            rc.verified_instagram_post(header_elements("90 posts"), grid_elements(2), image, LAYOUT, OURS,
                                       account="@creator", posts_before=90)


CAPTION = "What’s the move here #gaming #reels"


def threads_profile(*posts: dict, tabs: bool = True) -> list[Element]:
    """A Threads profile: header, the Threads/Replies tab row, then one row per post."""
    items = [element("StaticText", "creator", (16, 120, 200, 30)),
             element("Link", "creator", (16, 160, 90, 20)),  # the handle in the header is above the tabs
             element("TabBar", "Tab Bar", (0, BAR_TOP, 440, 83), "Tab Bar")]
    if tabs:
        items += [element("Button", "Threads", (0, 300, 110, 44)), element("Button", "Replies", (110, 300, 110, 44))]
    top = 380.0
    for post in posts:
        author = post.get("author", "creator")
        if post.get("pinned"):
            items.append(element("StaticText", "Pinned", (60, top - 18, 60, 16)))
        items.append(element("Link", author, (60, top, 80, 23)))
        items.append(element("Other", f"{author} 1m", (60, top + 2, 120, 20), "feed-item-header-title"))
        if post.get("in_progress"):
            items.append(element("ActivityIndicator", "In progress", (150, top, 20, 20)))
        if post.get("composer"):
            items.append(element("TextView", "", (60, top + 24, 368, 40), "composer-text-view", post["caption"]))
        else:
            items.append(element("Button", post["caption"], (60, top + 24, 368, 40)))
            items.append(element("StaticText", post["caption"], (60, top + 24, 368, 40)))
        top += 150
    return items


class ThreadsTests(unittest.TestCase):
    def newest(self, items, caption=CAPTION):
        return rc.threads_newest_post(items, LAYOUT, account="@creator", caption=caption)

    def test_finished_newest_post_with_the_exact_caption(self):
        found = self.newest(threads_profile({"caption": CAPTION}, {"caption": "older"}))
        self.assertEqual((found["account"], found["postIndex"]), ("@creator", 0))
        # Threads swaps straight and curly apostrophes between releases.
        self.newest(threads_profile({"caption": CAPTION.replace("’", "'")}))

    def test_threads_2026_10_tab_labels_and_folded_hashtags(self):
        # Recorded 2026-10-01 on the reference release's crosspost: "Threads tab" / "Replies tab", hashtags folded.
        items = [e if e.label not in ("Threads", "Replies") else
                 element("Button", e.label + " tab", (e.left, e.top, e.width, e.height))
                 for e in threads_profile({"caption": "What’s the move here… show hashtags"})]
        self.assertEqual(self.newest(items)["postIndex"], 0)

    def test_a_folded_caption_must_be_the_approved_text_before_its_hashtags(self):
        with self.assertRaisesRegex(rc.ReceiptError, "approved caption"):
            self.newest(threads_profile({"caption": "What’s the move… show hashtags"}))
        with self.assertRaisesRegex(rc.ReceiptError, "approved caption"):
            self.newest(threads_profile({"caption": "What’s the move here… show hashtags"}),
                        caption="What’s the move here")

    def test_in_progress_crosspost_is_not_a_receipt(self):
        with self.assertRaisesRegex(rc.ReceiptError, "In progress"):
            self.newest(threads_profile({"caption": CAPTION, "in_progress": True}))
        with self.assertRaisesRegex(rc.ReceiptError, "composer"):
            self.newest(threads_profile({"caption": CAPTION, "composer": True}))
        posting = threads_profile({"caption": CAPTION}) + [
            element("Other", "Posting…, Keep Threads open to finish uploading., Progress", (56, 817, 282, 21))]
        with self.assertRaisesRegex(rc.ReceiptError, "still posting"):
            self.newest(posting)

    def test_wrong_or_older_caption_is_not_a_receipt(self):
        with self.assertRaisesRegex(rc.ReceiptError, "approved caption"):
            self.newest(threads_profile({"caption": "something else"}, {"caption": CAPTION}))
        with self.assertRaisesRegex(rc.ReceiptError, "approved caption"):
            self.newest(threads_profile({"caption": CAPTION + " extra"}))

    def test_pinned_posts_are_skipped(self):
        found = self.newest(threads_profile({"caption": "pinned intro", "pinned": True}, {"caption": CAPTION}))
        self.assertEqual(found["postIndex"], 1)
        with self.assertRaisesRegex(rc.ReceiptError, "only pinned"):
            self.newest(threads_profile({"caption": CAPTION, "pinned": True}))

    def test_other_author_or_missing_tabs_fails_closed(self):
        with self.assertRaisesRegex(rc.ReceiptError, "by someone"):
            self.newest(threads_profile({"caption": CAPTION, "author": "someone"}))
        with self.assertRaisesRegex(rc.ReceiptError, "tabs are not on screen"):
            self.newest(threads_profile({"caption": CAPTION}, tabs=False))
        with self.assertRaisesRegex(rc.ReceiptError, "no posts"):
            self.newest(threads_profile())


class BaselineAndRouteTests(unittest.TestCase):
    def test_baseline_round_trip_under_the_state_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(rc.load_baseline(Path(folder), 7, "instagram"))
            path = rc.save_baseline(Path(folder), 7, "instagram", {"account": "@creator", "posts": 90})
            self.assertEqual(path.parent, Path(folder) / "receipts")
            self.assertEqual(rc.load_baseline(Path(folder), 7, "instagram")["posts"], 90)
            data = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps({**data, "releaseId": 8}), encoding="utf-8")
            with self.assertRaisesRegex(rc.ReceiptError, "does not belong"):
                rc.load_baseline(Path(folder), 7, "instagram")

    def test_every_destination_has_a_route_and_unrecorded_ones_say_what_to_record(self):
        for platform in ("youtube", "instagram", "facebook", "threads", "tiktok"):
            for mode in ("post_now", "schedule"):
                route = rc.receipt_route(platform, mode)
                self.assertIn(route["status"], ("verify", "needs_recording"))
                self.assertTrue(route["record"], (platform, mode))
                if route["status"] == "needs_recording":
                    self.assertTrue(route["plan"])
        self.assertIn("Never open For You", rc.receipt_route("tiktok", "schedule")["plan"])
        self.assertTrue(rc.receipt_route("instagram", "schedule")["navigationUnrecorded"])
        with self.assertRaises(rc.ReceiptError):
            rc.receipt_route("instagram", "later")


class NoPhone:
    def __getattr__(self, name):
        raise AssertionError(f"the phone was touched: {name}")


def release_with(store: Store, folder: Path, mode: str = "post_now") -> int:
    source = folder / "clip.mp4"
    source.write_bytes(b"finished source")
    release_id = store.import_file(source)["id"]
    for platform in ("instagram", "facebook", "threads", "tiktok"):
        store.save_text(release_id, platform, "@creator", "", "Approved caption", "")
        store.authorize(release_id, platform)
    store.set_delivery_mode(release_id, mode)
    return release_id


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = Path(self.folder.name)
        self.store = Store(self.state / "db.sqlite")
        self.release_id = release_with(self.store, self.state)
        self.store.mark_unconfirmed(self.release_id, "instagram")
        self.store.mark_unconfirmed(self.release_id, "tiktok")
        self.phone = phone_receipts.phone
        phone_receipts.phone = NoPhone()

    def tearDown(self):
        phone_receipts.phone = self.phone
        self.store.close()
        self.folder.cleanup()

    def test_targets_are_the_unconfirmed_destinations_in_posting_order(self):
        release = self.store.release(self.release_id)
        self.assertEqual(phone_receipts.targets(release), ["instagram", "tiktok", "facebook", "threads"])
        self.assertEqual(phone_receipts.targets(release, "youtube"), [])
        self.assertEqual(phone_receipts.expected_caption(release, "threads"), "Approved caption")

    def test_unrecorded_platform_reports_its_screens_without_touching_the_phone(self):
        result = phone_receipts.check(self.store, self.store.release(self.release_id), "tiktok", self.state,
                                      posts_before=None, record=True)
        self.assertEqual(result["kind"], "needs_recording")
        self.assertTrue(result["record"])

    def test_instagram_post_now_without_a_before_count_stays_unverified(self):
        result = phone_receipts.check(self.store, self.store.release(self.release_id), "instagram", self.state,
                                      posts_before=None, record=True)
        self.assertEqual(result["kind"], "unverified")
        self.assertIn("--posts-before", result["message"])

    def test_verified_post_keeps_evidence_and_leaves_status_when_core_cannot_record(self):
        stem = phone_receipts.evidence_stem(self.state, self.release_id, "instagram")
        result = phone_receipts.record_post(self.store, self.release_id, "instagram",
                                            {"verification": "instagram_profile_count_newest_tile_first_frame"},
                                            stem, record=True)
        self.assertEqual(result["kind"], "verified_unrecorded")
        self.assertTrue(Path(result["evidence"]).is_file())
        status = {d["platform"]: d["status"] for d in self.store.release(self.release_id)["destinations"]}
        self.assertEqual(status["instagram"], "unconfirmed")
        dry = phone_receipts.record_post(self.store, self.release_id, "instagram",
                                         {"verification": "x"}, stem, record=False)
        self.assertEqual(dry["kind"], "verified")


EVIDENCE = ROOT / ".state" / "evidence" / "release5-instagram-receipt-grid.png"
HEADER = sorted((ROOT / ".state" / "fixtures" / "instagram").glob("profile-header-*.json"))


class LocalEvidenceTests(unittest.TestCase):
    """The recorded grid and header, when this machine has them (never committed)."""

    @unittest.skipUnless(EVIDENCE.is_file(), "local receipt evidence not present")
    def test_recorded_grid_reads_three_pins_then_reels(self):
        image = Image.open(EVIDENCE).convert("RGB")
        # Measured row tops of the recorded grid after scrolling (points).
        icons = [rc.corner_icon(image, LAYOUT, rc.Tile(left, top, TILE_W, TILE_H))
                 for top in (128.3, 323.9, 519.5) for left in COLUMNS]
        self.assertEqual(icons, ["pin"] * 3 + ["reel"] * 6)

    @unittest.skipUnless(HEADER, "local Instagram header recording not present")
    def test_recorded_header_gives_an_exact_count(self):
        from video_drop.screens.snapshot import load_fixture
        snapshot = load_fixture(HEADER[-1].with_suffix(""))
        profile = rc.instagram_profile(snapshot.elements)
        self.assertTrue(profile["account"].startswith("@"))
        self.assertGreater(profile["posts"], 0)
        self.assertGreater(rc.instagram_grid_scroll(snapshot.elements, LAYOUT), 0)


if __name__ == "__main__":
    unittest.main()


class OpenProfileTests(unittest.TestCase):
    def test_profile_tab_is_tapped_twice_apart_to_scroll_the_header_back(self):
        taps, sleeps = [], []
        fake = type("P", (), {"open_app": lambda self, b: None,
                              "tap": lambda self, x, y: taps.append((x, y))})()
        with patch.object(phone_receipts, "phone", fake), \
                patch.object(phone_receipts.share, "layout", lambda **kw: LAYOUT), \
                patch.object(phone_receipts.time, "sleep", sleeps.append):
            phone_receipts.open_profile(phone_receipts.INSTAGRAM_BUNDLE, phone_receipts.INSTAGRAM_PROFILE_TAB)
        self.assertEqual(len(taps), 2)
        self.assertEqual(taps[0], taps[1])
        self.assertGreaterEqual(sleeps[1], 2.0)  # never a double tap (Instagram's account switch)


class ThreadsAccountSwitch(unittest.TestCase):
    """Recorded fixtures: threads/profile-*, threads/account-switcher-*."""

    PROFILE = [element("Button", "Display Name", (15, 133, 159, 29)),
               element("Image", "", (179, 141, 13, 13), name="ig_icon_chevron_down_filled_12"),
               element("Button", "Edit profile", (15, 287, 178, 36))]
    SHEET = [element("Button", "Dismiss", (203, 389, 34, 2), name="Button"),
             element("Button", "Log in as @first.acct", (24, 400, 392, 86)),
             element("Image", "", (384, 435, 16, 16), name="ig_icon_check_outline_24"),
             element("Button", "Log in as @creator", (24, 486, 392, 86)),
             element("Button", "Log in as @other.one", (24, 572, 392, 86))]

    def test_the_display_name_beside_the_chevron_opens_the_switcher(self):
        self.assertEqual(rc.threads_switcher_button(self.PROFILE).label, "Display Name")

    def test_rows_and_the_checked_account(self):
        self.assertEqual(set(rc.threads_switch_rows(self.SHEET)), {"first.acct", "creator", "other.one"})
        self.assertEqual(rc.threads_active_switch_row(self.SHEET), "first.acct")

    def test_no_check_mark_fails_closed(self):
        with self.assertRaises(rc.ReceiptError):
            rc.threads_active_switch_row([e for e in self.SHEET if e.name != "ig_icon_check_outline_24"])

    def test_the_password_sheet_is_recognised_and_the_profile_is_not(self):
        login = [element("Button", "", (0, 0, 440, 78), name="bottom_sheet_background"),
                 element("Button", "Close", (14, 106, 28, 28))]
        self.assertEqual(rc.threads_login_prompt(login).label, "Close")
        self.assertIsNone(rc.threads_login_prompt(self.PROFILE))
