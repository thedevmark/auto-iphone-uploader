import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from video_drop.screens.labels import Labels
from video_drop.screens.matcher import MatchError, find, identify, present
from video_drop.screens.model import Locator, load_map
from video_drop.screens.snapshot import Element, Snapshot


def el(type_, label="", name="", left=0, top=0, width=40, height=20, value=""):
    return Element(type_, label, name or label, value, left, top, width, height)


LABELS = Labels("en", {"next": "Next", "kids_no": "No, it's not made for kids"})


class MatcherTests(unittest.TestCase):
    def test_finds_by_identifier_label_and_type(self):
        snap = Snapshot(440, 956, (el("TextField", "", "id.title", 20, 200, 400, 40),
                                   el("Button", "Next", "", 360, 880, 60, 30),
                                   el("StaticText", "Next", "", 10, 10)))
        self.assertEqual(find(snap, Locator(id="id.title"), LABELS).y, 220)
        self.assertEqual(find(snap, Locator(label="@label:next", type="Button"), LABELS).x, 390)

    def test_ambiguous_and_missing_fail(self):
        snap = Snapshot(440, 956, (el("Button", "Next"), el("Button", "Next", top=100)))
        with self.assertRaisesRegex(MatchError, "2 matches"):
            find(snap, Locator(label="Next"), LABELS)
        with self.assertRaisesRegex(MatchError, "No match"):
            find(snap, Locator(label="Done"), LABELS)

    def test_nested_duplicate_with_identical_frame_is_one_target(self):
        accounts = el("Button", "Accounts", left=43, top=68, width=44, height=32)
        snap = Snapshot(440, 956, (accounts, accounts, el("Button", "Accounts", left=43, top=300)))
        with self.assertRaisesRegex(MatchError, "2 matches"):
            find(snap, Locator(label="Accounts"), LABELS)
        self.assertEqual(find(Snapshot(440, 956, (accounts, accounts)), Locator(label="Accounts"), LABELS).y, 84)

    def test_fallback_is_tried_after_primary_fails(self):
        snap = Snapshot(440, 956, (el("Button", "Continue", left=100),))
        loc = Locator(label="Next", fallback=(Locator(label="Continue"),))
        self.assertEqual(find(snap, loc, LABELS).x, 120)

    def test_relative_element_on_side_of_anchor(self):
        snap = Snapshot(440, 956, (el("StaticText", "No, it's not made for kids", left=60, top=400, width=300, height=24),
                                   el("Image", "", "radio", left=20, top=402, width=20, height=20),
                                   el("Image", "", "radio", left=20, top=600, width=20, height=20)))
        loc = Locator(type="Image", relative_to=Locator(label="@label:kids_no"), side="left", within=80)
        self.assertEqual(find(snap, loc, LABELS).y, 412)

    def test_value_filters_exactly_or_by_contains(self):
        labels = Labels("en", {"first_frame": "Less than a second"})
        slider = el("Slider", "Thumbnail frame selector", left=12, top=857, width=416, height=65,
                    value="Less than a second")
        snap = Snapshot(440, 956, (slider, el("PickerWheel", "", top=300, value="12 o’clock"),
                                   el("PickerWheel", "", left=100, top=300, value="00 minutes")))
        self.assertEqual(find(snap, Locator(type="Slider", value="@label:first_frame"), labels).element, slider)
        self.assertEqual(find(snap, Locator(type="PickerWheel", value="minutes", contains=True), labels).x, 120)
        moved = Snapshot(440, 956, (el("Slider", "Thumbnail frame selector", value="3 seconds"),))
        self.assertFalse(present(moved, Locator(type="Slider", value="@label:first_frame"), labels))
        with self.assertRaisesRegex(MatchError, "No match"):
            find(snap, Locator(type="PickerWheel", value="minutes"), labels)

    def test_inside_finds_the_control_within_its_row_only(self):
        row = el("Switch", "Checked, Upload at highest quality, Always", left=0, top=545, width=440, height=110)
        snap = Snapshot(440, 956, (el("Switch", "Not checked, Schedule this reel", left=0, top=468, width=440, height=77),
                                   el("Switch", "", "igds-switch", left=361, top=483, width=63, height=29),
                                   row, el("Switch", "", "igds-switch", left=361, top=559, width=63, height=29)))
        loc = Locator(id="igds-switch", relative_to=Locator(label="Upload at highest quality", contains=True),
                      side="inside")
        self.assertEqual(find(snap, loc, LABELS).y, 573.5)
        with self.assertRaisesRegex(MatchError, "2 matches"):
            find(snap, Locator(id="igds-switch"), LABELS)

    def test_relative_icon_uses_screenshot_band(self):
        screen = Image.new("L", (440, 956), 30)
        ImageDraw.Draw(screen).ellipse((21, 403, 39, 421), outline=230, width=3)
        buffer = io.BytesIO()
        screen.convert("RGB").save(buffer, "PNG")
        with tempfile.TemporaryDirectory() as folder:
            icon = Image.new("L", (24, 24), 30)
            ImageDraw.Draw(icon).ellipse((3, 3, 21, 21), outline=230, width=3)
            icon.save(Path(folder) / "radio.png")
            snap = Snapshot(440, 956, (el("StaticText", "No, it's not made for kids", left=60, top=400, width=300, height=24),),
                            buffer.getvalue())
            loc = Locator(icon="radio.png", relative_to=Locator(label="@label:kids_no"), side="left", within=60)
            target = find(snap, loc, LABELS, Path(folder))
        self.assertEqual((target.x, target.y), (30.0, 412.0))

    def test_identify_picks_the_single_matching_screen(self):
        with tempfile.TemporaryDirectory() as folder:
            def write(name, require, forbid=()):
                path = Path(folder) / "youtube" / f"{name}.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"app": "youtube", "screen": name,
                                            "signature": {"require": require, "forbid": list(forbid)}}), encoding="utf-8")
                return load_map(path)
            trim = write("trim", [{"label": "Crop your video"}], [{"label": "Processing"}])
            editor = write("editor", [{"label": "Swipe up to edit"}])
            snap = Snapshot(440, 956, (el("StaticText", "Crop your video"),))
            self.assertIs(identify(snap, [trim, editor], LABELS), trim)
            busy = Snapshot(440, 956, (el("StaticText", "Crop your video"), el("StaticText", "Processing", top=50)))
            with self.assertRaisesRegex(MatchError, "Unknown screen"):
                identify(busy, [trim, editor], LABELS)
            both = Snapshot(440, 956, (el("StaticText", "Crop your video"), el("StaticText", "Swipe up to edit", top=50)))
            with self.assertRaisesRegex(MatchError, "Ambiguous screen"):
                identify(both, [trim, editor], LABELS)

    def test_present_is_true_only_for_one_or_more_matches(self):
        snap = Snapshot(440, 956, (el("Button", "Next"),))
        self.assertTrue(present(snap, Locator(label="Next"), LABELS))
        self.assertFalse(present(snap, Locator(label="Done"), LABELS))


if __name__ == "__main__":
    unittest.main()
