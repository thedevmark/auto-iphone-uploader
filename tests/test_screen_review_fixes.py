import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.record_screen import draft_map
from video_drop.screens.icons import IconError, locate
from video_drop.screens.labels import Labels
from video_drop.screens.matcher import MatchError, find, identify
from video_drop.screens.model import Locator, load_map, load_maps
from video_drop.screens.runner import Runner
from video_drop.screens.snapshot import Element, Snapshot, elements_from_tree

LABELS = Labels("en", {})


def el(type_, label, left=0, top=0):
    return Element(type_, label, label, "", left, top, 40, 20)


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data, name="s"):
        path = self.root / "youtube" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"app": "youtube", "screen": name, **data}), encoding="utf-8")
        return path

    def test_read_error_after_irreversible_tap_is_reported_unconfirmed_and_not_retried(self):
        self.write({"signature": {"require": [{"label": "Details"}]}, "elements": {"up": {"label": "Upload"}},
                    "actions": {"post": {"tap": "up", "expect": "done", "irreversible": True}}}, "details")
        taps, calls = [], []

        class Phone:
            def tap(self, x, y):
                taps.append((x, y))

        def read(phone, screenshot=True):
            calls.append(1)
            if len(calls) > 1:
                raise ConnectionError("WDA gone")
            return Snapshot(440, 956, (el("StaticText", "Details"), el("Button", "Upload", top=100)))
        now = [0.0]
        runner = Runner(Phone(), load_maps(self.root), LABELS, authorized=frozenset({"post"}), read=read,
                        sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0], timeout=1)
        with self.assertRaisesRegex(MatchError, "unconfirmed"):
            runner.act("post")
        self.assertEqual(len(taps), 1)
        self.assertGreater(len(calls), 2)

    def test_empty_relative_anchor_is_rejected(self):
        with self.assertRaises(ValueError):
            load_map(self.write({"signature": {"require": [{"label": "A"}]},
                                 "elements": {"done": {"label": "Done", "relative_to": {}, "side": "left"}}}))

    def test_signature_markers_cannot_be_relative_icons_or_fallbacks(self):
        for marker in ({"icon": "a.png"}, {"label": "Done", "relative_to": {"label": "T"}, "side": "left"},
                       {"label": "Done", "fallback": [{"label": "Ok"}]}):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "signature"):
                load_map(self.write({"signature": {"require": [marker]}}))

    def test_fallback_is_not_used_when_primary_is_ambiguous(self):
        snap = Snapshot(440, 956, (el("Button", "Next"), el("Button", "Next", top=100), el("Button", "Next step", top=200)))
        loc = Locator(label="Next", type="Button", fallback=(Locator(label="Next s", contains=True),))
        with self.assertRaisesRegex(MatchError, "2 matches"):
            find(snap, loc, LABELS)

    def test_snapshot_tolerates_string_and_missing_numbers_and_keeps_zero_values(self):
        tree = {"type": "Other", "rect": {"x": 0, "y": 0, "width": "440", "height": "956"}, "children": [
            {"type": None, "label": "Bad", "rect": {"x": None, "y": 0, "width": 5, "height": 5}},
            {"type": "Switch", "label": "Wifi", "value": 0, "rect": {"x": "1", "y": "2", "width": "3", "height": "4"}}]}
        elements = elements_from_tree(tree)
        self.assertEqual([e.type for e in elements], ["Other", "Switch"])
        self.assertEqual(elements[1].value, "0")
        self.assertEqual(elements[1].left, 1.0)

    def test_icon_duplicate_touching_above_or_left_is_ambiguous(self):
        icon = Image.new("L", (20, 20), 30)
        ImageDraw.Draw(icon).ellipse((3, 3, 16, 16), outline=230, width=3)
        for offset in ((0, 20), (20, 0)):
            screen = Image.new("L", (120, 120), 30)
            screen.paste(icon, (40, 40))
            screen.paste(icon, (40 - offset[0], 40 - offset[1]))
            with self.subTest(offset=offset), self.assertRaisesRegex(IconError, "ambiguous"):
                locate(screen, icon, (0, 0, 120, 120))

    def test_elements_and_actions_may_use_coordinate_like_names(self):
        screen = load_map(self.write({"signature": {"require": [{"label": "A"}]},
                                      "elements": {"x": {"label": "X", "type": "Button"}, "frame": {"label": "Frame"}},
                                      "actions": {"x": {"tap": "x", "expect": "s"}}}))
        self.assertIn("x", screen.elements)
        with self.assertRaisesRegex(ValueError, "coordinates"):
            load_map(self.write({"signature": {"require": [{"label": "A", "x": 3}]}}))
        draft = draft_map(Snapshot(440, 956, (el("StaticText", "Title"), el("Button", "X"))), "youtube", "s2")
        self.write(draft, "s2")
        load_map(self.root / "youtube" / "s2.json")

    def test_screens_from_another_app_are_not_identified(self):
        screen = load_map(self.write({"bundle": "com.google.ios.youtube", "signature": {"require": [{"label": "Next"}]}}))
        mine = Snapshot(440, 956, (el("Button", "Next"),), app="com.google.ios.youtube")
        other = Snapshot(440, 956, (el("Button", "Next"),), app="com.burbn.instagram")
        self.assertIs(identify(mine, [screen], LABELS), screen)
        with self.assertRaisesRegex(MatchError, "Unknown screen"):
            identify(other, [screen], LABELS)


if __name__ == "__main__":
    unittest.main()
