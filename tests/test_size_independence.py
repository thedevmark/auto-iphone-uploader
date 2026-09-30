"""Prove the phone flows hold on every supported iPhone size without a second phone.

Two transforms move a 440 x 956 @3x screen onto another iPhone:

* ``scale_snapshot`` scales every frame and resizes the screenshot to the
  device's real pixel size (@2x or @3x). It is the worst case for anything
  that secretly depends on absolute geometry.
* ``render`` lays the screen out the way UIKit does (fixed point sizes, pinned
  to the safe-area edges) and draws it at the device's pixel density. Icons
  keep their point size on every iPhone, so icon locators are proven here.

Screens below are synthetic, modeled on recorded reference-phone snapshots;
they carry no private file names. ``RecordedFixtureSizeTests`` additionally
runs the ignored local recordings when they exist.
"""

from __future__ import annotations

import functools
import io
import math
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from scripts import phone_youtube
from video_drop.phone_ui import PhoneLayout, filled_radio, first_frame_point, share_rail_y
from video_drop.screens.icons import to_points
from video_drop.screens.labels import Labels
from video_drop.screens.matcher import AmbiguousMatch, MatchError, find, identify
from video_drop.screens.model import Locator, load_maps
from video_drop.screens.runner import Runner
from video_drop.screens.snapshot import Element, Snapshot, load_fixture

ROOT = Path(__file__).resolve().parent.parent
MAPS = ROOT / "maps"
FIXTURES = ROOT / ".state" / "fixtures"
REF_W, REF_H, REF_TOP, REF_BOTTOM = 440.0, 956.0, 62.0, 34.0
BACKGROUND, INK = 15, 241
YOUTUBE = "com.google.ios.youtube"


@dataclass(frozen=True)
class Device:
    name: str
    width: float
    height: float
    scale: float          # pixels per point
    top: float            # safe-area top inset, points
    bottom: float         # safe-area bottom inset, points
    pixels: tuple[int, int] | None = None

    @property
    def size(self) -> tuple[int, int]:
        return self.pixels or (round(self.width * self.scale), round(self.height * self.scale))

    @property
    def layout(self) -> PhoneLayout:
        return PhoneLayout.from_info({"width": self.width, "height": self.height})


DEVICES = (
    Device("iPhone SE 375x667@2x", 375, 667, 2, 20, 0),
    Device("iPhone 11 414x896@2x", 414, 896, 2, 48, 34),
    Device("iPhone 13/14 390x844@3x", 390, 844, 3, 47, 34),
    Device("iPhone 15/16 393x852@3x", 393, 852, 3, 59, 34),
    Device("iPhone 16 Pro 402x874@3x", 402, 874, 3, 62, 34),
    Device("iPhone Plus 430x932@3x", 430, 932, 3, 59, 34),
    Device("iPhone 16 Pro Max 440x956@3x", 440, 956, 3, 62, 34),
    # Display-Zoom style: fewer points drawn into the full panel, a non-integer scale.
    Device("zoomed 402x874 in 1320x2868", 402, 874, 1320 / 402, 62, 34, (1320, 2868)),
)
REFERENCE = next(d for d in DEVICES if (d.width, d.height, d.scale) == (440, 956, 3))


# ---- layout model ------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One control measured on the reference phone and how UIKit pins it."""
    type: str | None
    label: str
    frame: tuple[float, float, float, float]
    h: str = "left"       # left | right | center | stretch | half_left | half_right
    v: str = "top"        # top | bottom | stretch
    name: str = ""
    value: str = ""
    glyph: str | None = None


def reflow(item: Item, device: Device) -> tuple[float, float, float, float]:
    left, top, width, height = item.frame
    if item.h == "right":
        left = device.width - (REF_W - left)
    elif item.h == "center":
        left = device.width / 2 + (left - REF_W / 2)
    elif item.h == "stretch":
        width = device.width - (REF_W - width)
    elif item.h in ("half_left", "half_right"):
        width = (device.width - 48) / 2
        left = 16 if item.h == "half_left" else device.width - 16 - width
    if item.v == "bottom":
        top = device.height - device.bottom - (REF_H - REF_BOTTOM - top)
    else:
        top = top - REF_TOP + device.top
        if item.v == "stretch":
            height = height + (device.height - device.top - device.bottom) - (REF_H - REF_TOP - REF_BOTTOM)
    return left, top, width, height


def elements(items: list[Item], device: Device) -> tuple[Element, ...]:
    return tuple(Element(i.type, i.label, i.name or i.label, i.value, *reflow(i, device))
                 for i in items if i.type)


def _glyph(draw: ImageDraw.ImageDraw, kind: str, box: tuple[float, float, float, float], unit: float) -> None:
    left, top, right, bottom = box
    cx, cy = (left + right) / 2, (top + bottom) / 2
    if kind == "button":
        draw.rounded_rectangle(box, radius=8 * unit, fill=INK)
    elif kind in ("radio", "radio_on"):
        r = 10.5 * unit
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=INK, width=max(1, round(2 * unit)))
        if kind == "radio_on":
            dot = 6.5 * unit
            draw.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=INK)
    elif kind == "chevron":
        draw.line([(left + 3 * unit, cy + 4 * unit), (cx, cy - 4 * unit), (right - 3 * unit, cy + 4 * unit)],
                  fill=INK, width=max(1, round(2 * unit)))
    else:
        raise ValueError(kind)


def render(items: list[Item], device: Device) -> bytes:
    return _render(tuple(items), device)


@functools.lru_cache(maxsize=None)
def _render(items: tuple[Item, ...], device: Device) -> bytes:
    """Draw each glyph anti-aliased at the device's own pixel density, like UIKit does."""
    sx, sy = device.size[0] / device.width, device.size[1] / device.height
    canvas = Image.new("RGB", device.size, (BACKGROUND,) * 3)
    ss = 8
    for item in items:
        if not item.glyph:
            continue
        left, top, width, height = reflow(item, device)
        x0, y0 = math.floor(left * sx) - 2, math.floor(top * sy) - 2
        w, h = math.ceil(width * sx) + 5, math.ceil(height * sy) + 5
        patch_ = Image.new("L", (w * ss, h * ss), BACKGROUND)
        box = ((left * sx - x0) * ss, (top * sy - y0) * ss,
               ((left + width) * sx - x0) * ss, ((top + height) * sy - y0) * ss)
        _glyph(ImageDraw.Draw(patch_), item.glyph, box, sx * ss)
        canvas.paste(patch_.resize((w, h), Image.BOX).convert("RGB"), (x0, y0))
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    return buffer.getvalue()


def rendered_snapshot(items: list[Item], device: Device, app: str = YOUTUBE) -> Snapshot:
    return Snapshot(device.width, device.height, elements(items, device), render(items, device), app)


def scale_snapshot(snapshot: Snapshot, device: Device) -> Snapshot:
    """Scale every frame to the device and resize the screenshot to its real pixel size."""
    fx, fy = device.width / snapshot.width, device.height / snapshot.height
    scaled = tuple(replace(e, left=e.left * fx, top=e.top * fy, width=e.width * fx, height=e.height * fy)
                   for e in snapshot.elements)
    shot = None
    if snapshot.screenshot:
        buffer = io.BytesIO()
        Image.open(io.BytesIO(snapshot.screenshot)).convert("RGB").resize(device.size, Image.LANCZOS).save(buffer, "PNG")
        shot = buffer.getvalue()
    return Snapshot(device.width, device.height, scaled, shot, snapshot.app, snapshot.app_version)


# ---- screens modeled on the recorded reference phone -------------------------

def back(y=60.0):
    return [Item("Button", "Back", (-4, y, 48, 48), name="id.elements.components.metadata_editor.app_bar.back")] * 2


def radio_row(label, top, height, *, inset=35.5, selected=False, value=""):
    cy = top + height / 2
    return [Item("Button", label, (0, top, 440, height), "stretch", value=value),
            Item(None, "", (inset - 10.5, cy - 10.5, 21, 21), glyph="radio_on" if selected else "radio")]


SCREENS = {
    "trim": [
        Item("Button", "Exit trim", (12, 78, 40, 40)),
        Item("StaticText", "Crop your video", (120, 838, 200, 20), "center", "bottom"),
        Item("Button", "Next", (228, 874, 196, 40), "half_right", "bottom", glyph="button"),
    ],
    "editor": [
        Item("Button", "Exit editor", (12, 78, 40, 40)),
        Item("Image", "", (210, 812, 20, 20), "center", "bottom", name="youtube_outline/chevron_up_24pt", glyph="chevron"),
        Item("StaticText", "Swipe up to edit", (120, 838, 200, 20), "center", "bottom"),
        Item("Button", "Edit", (16, 874, 196, 40), "half_left", "bottom"),
        Item("Button", "Next", (228, 874, 196, 40), "half_right", "bottom", glyph="button"),
    ],
    "details": [
        Item("Button", "Back", (-2, 56, 44, 45), name="id.ui.browse.back.button"),
        Item("StaticText", "Add details", (46, 65, 109, 27)),
        Item("ScrollView", "", (0, 100, 440, 750), "stretch", "stretch", name="id.metadata_editor.scroll_view"),
        Item("Other", "Edit thumbnail, 0:30", (12, 75, 86, 131), name="id.elements.components.metadata_editor.open_shorts"),
        Item("Other", "", (109, 101, 320, 21), "stretch", name="id.elements.components.metadata_editor.title"),
        Item("Other", "Creator, @creator", (0, 299, 440, 69), "stretch",
             name="id.elements.components.identity_chip_component"),
        Item("Button", "Visibility, Private", (0, 367, 440, 57), "stretch",
             name="id.elements.components.metadata_editor.privacy_picker"),
        Item("Button", "Select audience", (0, 423, 440, 57), "stretch",
             name="id.elements.components.metadata_editor.audience_picker"),
        # YouTube nests an unlabeled copy of each picker under the same id; an id alone is ambiguous.
        Item("Button", "", (0, 423, 440, 57), "stretch", name="id.elements.components.metadata_editor.audience_picker"),
        Item("Button", "Show more", (12, 483, 416, 49), "stretch",
             name="id.elements.components.metadata_editor.expander.collapsed_button"),
        Item("Button", "Upload Short", (12, 862, 416, 48), "stretch", "bottom", name="id.metadata_editor.upload_button"),
        Item("StaticText", "Upload Short", (175, 877, 90, 18), "center", "bottom"),
    ],
    "visibility": [
        *back(),
        Item("Other", "Set visibility", (48, 71, 118, 27)),
        Item("ScrollView", "", (0, 106, 440, 816), "stretch", "stretch"),
        Item("Button", "Publish now", (0, 106, 440, 64), "stretch"),
        *radio_row("Public, Anyone can search for and view", 170, 72, selected=True, value="1"),
        *radio_row("Unlisted, Anyone with the link can view", 243, 72),
        *radio_row("Private, Only people you choose can view", 316, 72),
        Item("Button", "Schedule", (0, 397, 440, 72), "stretch"),
    ],
    "schedule": [
        *back(),
        Item("Other", "Set visibility", (48, 71, 118, 27)),
        Item("ScrollView", "", (0, 106, 440, 816), "stretch", "stretch"),
        Item("Button", "Publish now", (0, 106, 440, 64), "stretch"),
        Item("Button", "Schedule", (0, 179, 440, 72), "stretch"),
    ],
    "schedule_picker": [
        Item("DatePicker", "", (56, 266, 328, 372), "center"),
        Item("Button", "Show year picker", (64, 282, 157, 38), "center", name="DatePicker.Show", value="September 2026"),
        Item("Button", "Previous Month", (303, 282, 44, 38), "center", name="DatePicker.PreviousMonth"),
        Item("Button", "Next Month", (346, 282, 44, 38), "center", name="DatePicker.NextMonth"),
        Item("Button", "Wednesday, September 30", (196, 539, 47, 47), "center", value="1"),
        Item("Other", "Time Picker", (56, 591, 328, 39), "center"),
        Item("Button", "12:00 AM", (64, 591, 313, 37), "center"),
        Item("StaticText", "Time", (64, 599, 40, 22), "center"),
        Item("Button", "Cancel", (56, 649, 160, 45), "center"),
        Item("Button", "OK", (224, 649, 160, 45), "center"),
        Item("StaticText", "OK", (294, 663, 20, 18), "center"),
        Item("Button", "Dismiss alert", (0, 0, 440, 956), "stretch", "stretch"),
    ],
    "time_wheels": [
        Item("DatePicker", "", (152, 395, 219, 172), "center"),
        Item("PickerWheel", "", (171, 357, 56, 248), "center", value="12 o’clock"),
        Item("PickerWheel", "", (231, 357, 51, 248), "center", value="00 minutes"),
        Item("PickerWheel", "", (286, 357, 66, 248), "center", value="AM"),
        Item("Button", "dismiss popup", (0, 0, 440, 956), "stretch", "stretch", name="PopoverDismissRegion"),
    ],
    "thumbnail_editor": [
        Item("Button", "Back", (0, 66, 64, 64), name="id.metadata.thumbnail_editor.back.button"),
        Item("Button", "Done", (376, 66, 64, 64), "right", name="id.metadata.thumbnail_editor.done.button"),
        Item("Slider", "Thumbnail frame selector", (12, 857, 416, 65), "stretch", "bottom", value="12 seconds"),
    ],
    "thumbnail_first_frame": [
        Item("Button", "Back", (0, 66, 64, 64), name="id.metadata.thumbnail_editor.back.button"),
        Item("Button", "Done", (376, 66, 64, 64), "right", name="id.metadata.thumbnail_editor.done.button"),
        Item("Slider", "Thumbnail frame selector", (12, 857, 416, 65), "stretch", "bottom", value="Less than a second"),
    ],
    "paid_promotion": [
        *back(),
        Item("Other", "Paid promotion & brands", (48, 71, 237, 27)),
        Item("Button", "Yes", (0, 191, 440, 48), "stretch", name="paid_product_placement_setting_notify"),
        Item("Button", "", (0, 191, 440, 48), "stretch", name="paid_product_placement_setting_notify"),
        Item("Button", "No", (0, 239, 440, 48), "stretch", name="paid_product_placement_setting_no"),
        Item("Button", "", (0, 239, 440, 48), "stretch", name="paid_product_placement_setting_no"),
    ],
    "attributes": [
        *back(),
        Item("Other", "Attributes", (48, 71, 97, 27)),
        Item("Button", "AI use", (0, 106, 440, 56), "stretch",
             name="id.elements.components.metadata_editor.altered_content_picker"),
        Item("Button", "", (0, 106, 440, 56), "stretch", name="id.elements.components.metadata_editor.altered_content_picker"),
        Item("Other", "", (0, 162, 440, 52), "stretch", name="id.elements.components.metadata_editor.tag_editor"),
        Item("Button", "Add tags", (0, 162, 440, 52), "stretch"),
    ],
    "ai_use": [
        *back(),
        Item("Other", "AI use", (48, 71, 60, 27)),
        Item("ScrollView", "", (0, 106, 440, 816), "stretch", "stretch",
             name="id.elements.components.metadata_editor.altered_content_settings"),
        Item("Button", "Yes", (24, 329, 66, 24)),
        Item("Button", "No", (24, 385, 61, 24)),
    ],
    "description_editor": [
        *back(),
        Item("Other", "Add description", (48, 71, 152, 27)),
        Item("Other", "", (16, 117, 408, 20), name="id.elements.components.metadata_editor.description"),
        Item("TextView", "", (16, 117, 408, 20)),
        Item("Button", "Hashtags", (24, 623, 90, 24), name="id.elements.proactive_suggestion_category_0"),
    ],
    "audience": [
        *back(),
        Item("Other", "Select audience", (48, 71, 150, 27)),
        *radio_row("Yes, it's made for kids", 191, 48, inset=27.5),
        *radio_row("No, it's not made for kids", 239, 48, inset=27.5, selected=True),
    ],
}
LABELS = Labels.load(MAPS, "en")


def map_screens():
    return {m.screen: m for m in load_maps(MAPS) if m.app == "youtube"}


def resolve(snapshot: Snapshot, locator: Locator, icon_dir: Path | None = None):
    try:
        return find(snapshot, locator, LABELS, icon_dir)
    except MatchError as exc:
        return exc


class MapScreensAcrossSizesTests(unittest.TestCase):
    """Every map element resolves to the same control on every iPhone size."""

    def check(self, make):
        maps = map_screens()
        for screen_name, items in SCREENS.items():
            screen = maps[screen_name]
            reference = rendered_snapshot(items, REFERENCE)
            self.assertIs(identify(reference, list(maps.values()), LABELS), screen)
            for device in DEVICES:
                snapshot = make(reference, items, device)
                with self.subTest(screen=screen_name, device=device.name):
                    self.assertIs(identify(snapshot, list(maps.values()), LABELS), screen)
                    for name, locator in screen.elements.items():
                        before, after = resolve(reference, locator), resolve(snapshot, locator)
                        if isinstance(before, MatchError):
                            self.assertIsInstance(after, MatchError, name)
                            continue
                        self.assertNotIsInstance(after, MatchError, f"{name}: {after}")
                        index = reference.elements.index(before.element)
                        self.assertEqual(after.element, snapshot.elements[index], name)
                        self.assertTrue(0 <= after.x <= device.width and 0 <= after.y <= device.height, name)

    def test_scaled_frames_and_retina_screenshots(self):
        self.check(lambda reference, items, device: scale_snapshot(reference, device))

    def test_safe_area_layout_at_native_pixel_density(self):
        self.check(lambda reference, items, device: rendered_snapshot(items, device))

    def test_every_map_element_is_modeled(self):
        for screen_name, screen in map_screens().items():
            reference = rendered_snapshot(SCREENS[screen_name], REFERENCE)
            for name, locator in screen.elements.items():
                with self.subTest(screen=screen_name, element=name):
                    self.assertNotIsInstance(resolve(reference, locator), MatchError)


class RunnerAcrossSizesTests(unittest.TestCase):
    FLOW = {("trim", "Next"): "editor", ("editor", "Next"): "details",
            ("details", "Visibility, Private"): "visibility", ("visibility", "Back"): "details",
            ("details", "Select audience"): "audience", ("audience", "Back"): "details"}

    class Phone:
        def __init__(self, device, screen):
            self.device, self.screen, self.taps, self._shots = device, screen, [], {}

        def screen_info(self):
            return {"width": self.device.width, "height": self.device.height}

        def current_app(self):
            return {"bundleId": YOUTUBE}

        def ui_tree(self):
            children = [{"type": "XCUIElementType" + e.type, "label": e.label, "name": e.name, "value": e.value,
                         "isVisible": "1", "rect": {"x": e.left, "y": e.top, "width": e.width, "height": e.height}}
                        for e in elements(SCREENS[self.screen], self.device)]
            return {"type": "XCUIElementTypeApplication", "label": "YouTube", "isVisible": "1",
                    "rect": {"x": 0, "y": 0, "width": self.device.width, "height": self.device.height},
                    "children": children}

        def screenshot(self):
            if self.screen not in self._shots:
                self._shots[self.screen] = render(SCREENS[self.screen], self.device)
            return self._shots[self.screen]

        def tap(self, x, y):
            hits = [e for e in elements(SCREENS[self.screen], self.device)
                    if e.type in ("Button", "Other") and e.left <= x <= e.left + e.width and e.top <= y <= e.top + e.height]
            smallest = min(hits, key=lambda e: e.width * e.height).label if hits else None
            self.taps.append((x, y, smallest))
            self.screen = RunnerAcrossSizesTests.FLOW.get((self.screen, smallest), self.screen)

    def test_mapped_actions_land_on_their_control_on_every_size(self):
        maps = list(map_screens().values())
        steps = [("trim", "next", "editor"), ("editor", "next", "details"),
                 ("details", "open_visibility", "visibility"), ("visibility", "back", "details"),
                 ("details", "open_audience", "audience"), ("audience", "back", "details")]
        for device in DEVICES:
            with self.subTest(device=device.name):
                phone = self.Phone(device, "trim")
                runner = Runner(phone, maps, LABELS, timeout=0, sleep=lambda _: None)
                for start, action, expect in steps:
                    self.assertEqual(phone.screen, start)
                    self.assertEqual(runner.act(action).screen, expect)
                self.assertEqual(len(phone.taps), len(steps))


class IconsAcrossSizesTests(unittest.TestCase):
    """Icon templates are cropped once at @3x; they must still match at @2x and at other sizes."""

    RADIO_SCREEN = [
        Item("StaticText", "No, it's not made for kids", (60, 396, 300, 30)),
        Item(None, "", (25, 401.5, 21, 21), glyph="radio_on"),
    ]

    def crop(self, items, target: Item, folder: Path, name: str) -> Path:
        # The same crop record_screen.py makes: point scale, rounded frame, 2 pt of margin.
        image = to_points(render(items, REFERENCE), REF_W, REF_H)
        left, top, width, height = target.frame
        box = (round(left) - 2, round(top) - 2, round(left + width) + 2, round(top + height) + 2)
        path = folder / name
        image.crop(box).save(path)
        return path

    def assert_found(self, items, target: Item, locator: Locator):
        with tempfile.TemporaryDirectory() as folder:
            self.crop(items, target, Path(folder), locator.icon)
            for device in DEVICES:
                with self.subTest(device=device.name):
                    found = find(rendered_snapshot(items, device), locator, LABELS, Path(folder))
                    left, top, width, height = reflow(target, device)
                    self.assertLessEqual(abs(found.x - (left + width / 2)), 1.0)
                    self.assertLessEqual(abs(found.y - (top + height / 2)), 1.0)

    def test_radio_left_of_its_label(self):
        self.assert_found(self.RADIO_SCREEN, self.RADIO_SCREEN[1],
                          Locator(icon="radio.png", relative_to=Locator(label="@label:kids_no"), side="left", within=60))

    def test_chevron_above_editor_hint(self):
        items = SCREENS["editor"]
        self.assert_found(items, items[1],
                          Locator(icon="chevron.png", relative_to=Locator(label="@label:swipe_up_to_edit"),
                                  side="above", within=40))

    def test_half_point_offsets_still_match(self):
        shifted = [self.RADIO_SCREEN[0], replace(self.RADIO_SCREEN[1], frame=(25.5, 402, 21, 21))]
        self.assert_found(shifted, shifted[1],
                          Locator(icon="radio.png", relative_to=Locator(label="@label:kids_no"), side="left", within=60))

    def test_two_icons_in_band_stay_ambiguous_and_block_fallback(self):
        items = [Item("StaticText", "No, it's not made for kids", (95, 396, 300, 30)),
                 Item(None, "", (25, 401.5, 21, 21), glyph="radio_on"),
                 Item(None, "", (60, 401.5, 21, 21), glyph="radio_on"),
                 Item("Button", "Continue", (100, 700, 100, 40))]
        locator = Locator(icon="radio.png", relative_to=Locator(label="@label:kids_no"), side="left", within=95,
                          fallback=(Locator(label="Continue"),))
        with tempfile.TemporaryDirectory() as folder:
            self.crop(self.RADIO_SCREEN, self.RADIO_SCREEN[1], Path(folder), "radio.png")
            for device in DEVICES:
                with self.subTest(device=device.name), self.assertRaises(AmbiguousMatch):
                    find(rendered_snapshot(items, device), locator, LABELS, Path(folder))


class LegacyFlowGeometryTests(unittest.TestCase):
    """The hand-written flows in scripts/phone_youtube.py on every size."""

    def test_layout_knows_which_iphones_have_a_home_indicator(self):
        for device in DEVICES:
            with self.subTest(device=device.name):
                self.assertEqual(device.layout.bottom_inset, device.bottom)
                self.assertEqual(REFERENCE.layout.bottom_right_point(380, 880), (380, 880))

    def test_radio_is_read_at_its_fixed_point_inset(self):
        for device in DEVICES:
            for inset, x in ((35.5, 35), (27.5, 28)):
                for selected in (False, True):
                    items = radio_row("Row", 170, 72, inset=inset, selected=selected)
                    image = Image.open(io.BytesIO(render(items, device))).convert("RGB")
                    y = reflow(items[0], device)[1] + 36
                    with self.subTest(device=device.name, inset=inset, selected=selected):
                        self.assertEqual(filled_radio(image, device.layout, x, y), selected)

    def test_choose_radio_taps_the_circle_not_a_scaled_point(self):
        device = DEVICES[0]
        items = radio_row("No, it's not made for kids", 239, 48, inset=27.5)
        row = elements(items, device)[0]
        shots = [render(items, device), render(radio_row("No, it's not made for kids", 239, 48, inset=27.5,
                                                             selected=True), device)]
        taps = []
        phone = type("Phone", (), {"tap": staticmethod(lambda x, y: taps.append((x, y))),
                                   "screenshot": staticmethod(lambda: shots[min(len(taps), 1)])})
        with patch.object(phone_youtube, "phone", phone), \
                patch.object(phone_youtube, "layout", return_value=device.layout), \
                patch.object(phone_youtube, "wait", return_value={"x": row.x, "y": row.y}), \
                patch.object(phone_youtube.time, "sleep"):
            phone_youtube.choose_unlabeled_radio("No, it's not made for kids", x=28)
        self.assertEqual(taps, [(28.0, round(row.y))])

    def test_trim_next_readiness_follows_the_pinned_button(self):
        for device in DEVICES:
            left, top, width, height = reflow(SCREENS["trim"][2], device)
            for x in range(380, 421, 5):
                for y in range(880, 906, 5):
                    px, py = device.layout.bottom_right_point(x, y)
                    # Proportional sampling left a third of these outside the button on the SE.
                    self.assertTrue(left < px < left + width and top < py < top + height, (device.name, x, y))
            shot = render(SCREENS["trim"], device)
            phone = type("Phone", (), {"screenshot": staticmethod(lambda shot=shot: shot)})
            with self.subTest(device=device.name), patch.object(phone_youtube, "phone", phone), \
                    patch.object(phone_youtube, "layout", return_value=device.layout), \
                    patch.object(phone_youtube.time, "sleep"):
                phone_youtube.wait_for_trim_next(timeout=1)

    def fake_tree_phone(self, items, device, taps):
        tree_phone = RunnerAcrossSizesTests.Phone(device, "unused")
        tree_phone.screen = "live"
        with patch.dict(SCREENS, {"live": items}):
            tree = tree_phone.ui_tree()
        return type("Phone", (), {"ui_tree": staticmethod(lambda: tree),
                                  "tap": staticmethod(lambda x, y: taps.append((x, y)))})

    def test_thumbnail_first_frame_comes_from_the_live_slider(self):
        slider = Item("Slider", "Thumbnail frame selector", (12, 857, 416, 65), "stretch", "bottom",
                      value="Less than a second")
        items = [Item("Button", "Back", (0, 66, 64, 64)), Item("Button", "Done", (376, 66, 64, 64), "right"), slider]
        for device in DEVICES:
            taps = []
            with self.subTest(device=device.name), \
                    patch.object(phone_youtube, "phone", self.fake_tree_phone(items, device, taps)), \
                    patch.object(phone_youtube, "layout", return_value=device.layout):
                element = phone_youtube.live_element("Thumbnail frame selector", "Slider")
                x, y = first_frame_point(element)
                left, top, width, height = reflow(slider, device)
                self.assertTrue(left < x <= left + 5 and top < y < top + height)
        self.assertEqual(first_frame_point(elements([slider], REFERENCE)[0]), (17, 889.5))

    def test_text_editor_exit_taps_the_observed_back_button(self):
        items = [*back(), Item("Other", "Add description", (48, 71, 152, 27))]
        for device in DEVICES:
            taps = []
            with self.subTest(device=device.name), \
                    patch.object(phone_youtube, "phone", self.fake_tree_phone(items, device, taps)), \
                    patch.object(phone_youtube, "layout", return_value=device.layout), \
                    patch.object(phone_youtube, "matches", side_effect=[[], [{"text": "Add details"}]]):
                phone_youtube.leave_text_editor("Add details")
                left, top, width, height = reflow(items[0], device)
                self.assertEqual(taps, [(left + width / 2, top + height / 2)] * 2)

    def test_share_rail_scrolls_on_the_apps_own_row(self):
        for device in DEVICES:
            rail_y = 330.5 - REF_TOP + device.top
            clipped = [{"text": "shareSheet.activity.contentView", "x": 200, "y": 300},
                       {"text": "Threads", "x": -0.5, "y": rail_y, "type": "Cell"},
                       {"text": "Messages", "x": 99.5, "y": rail_y, "type": "Cell"}]
            with self.subTest(device=device.name):
                self.assertEqual(share_rail_y(clipped, "Threads", device.layout, -1), rail_y)
                self.assertEqual(share_rail_y(clipped[2:], "Threads", device.layout, -1), -1)

    def test_proportional_gestures_stay_inside_their_targets(self):
        """Proportional points that are kept: prove each lands inside its pinned target on every size."""
        scroll = Item("ScrollView", "", (0, 100, 440, 750), "stretch", "stretch")
        keyboard_return = Item("Button", "search", (330, 830, 110, 56), "right", "bottom")
        schedule = Item("Button", "Schedule", (0, 397, 440, 72), "stretch")
        for device in DEVICES:
            layout = device.layout
            with self.subTest(device=device.name):
                left, top, width, height = reflow(scroll, device)
                for point in ((220, 780), (220, 350), (220, 300)):
                    x, y = layout.reference_point(*point)
                    self.assertTrue(top < y < top + height and left < x < left + width, point)
                left, top, width, height = reflow(keyboard_return, device)
                self.assertTrue(layout.relative_band({"x": left + width / 2, "y": top + height / 2}, top=0.75))
                left, top, width, height = reflow(schedule, device)
                self.assertTrue(layout.relative_band({"x": left + width / 2, "y": top + height / 2}, top=0.3, bottom=0.8))


@unittest.skipUnless(any(FIXTURES.glob("*/*.json")), "no local recordings under .state/fixtures")
class RecordedFixtureSizeTests(unittest.TestCase):
    """Local only: the reference phone's own recordings, scaled to every size."""

    def test_recorded_screens_resolve_the_same_controls_on_every_size(self):
        maps = load_maps(MAPS)
        checked = 0
        for path in sorted(FIXTURES.glob("*/*.json")):
            reference = load_fixture(path.with_suffix(""))
            try:
                screen = identify(reference, maps, LABELS)
            except MatchError:
                continue
            checked += 1
            icons = any(loc.icon for loc in screen.elements.values())
            base = reference if icons else replace(reference, screenshot=None)
            for device in DEVICES:
                snapshot = scale_snapshot(base, device)
                with self.subTest(fixture=checked, screen=screen.screen, device=device.name):
                    self.assertIs(identify(snapshot, maps, LABELS), screen)
                    for name, locator in screen.elements.items():
                        before = resolve(base, locator, screen.path.parent / "icons")
                        after = resolve(snapshot, locator, screen.path.parent / "icons")
                        if isinstance(before, MatchError):
                            self.assertIsInstance(after, MatchError, name)
                            continue
                        self.assertNotIsInstance(after, MatchError, name)
                        self.assertAlmostEqual(after.x, before.x * device.width / reference.width, delta=0.5)
                        self.assertAlmostEqual(after.y, before.y * device.height / reference.height, delta=0.5)
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
