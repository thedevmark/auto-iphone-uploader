import json
import tempfile
import unittest
from pathlib import Path

from video_drop.screens.labels import Labels
from video_drop.screens.model import Locator, load_map, load_maps

GOOD = {
    "app": "youtube", "screen": "details", "locale": "en",
    "signature": {"require": [{"id": "id.elements.components.metadata_editor.title"}],
                  "forbid": [{"label": "@label:processing"}]},
    "elements": {"title": {"id": "id.elements.components.metadata_editor.title"},
                 "upload": {"label": "@label:upload_short", "type": "Button"},
                 "radio": {"relative_to": {"label": "@label:kids_no"}, "side": "left", "icon": "radio.png"}},
    "actions": {"open_title": {"tap": "title", "expect": "title_editor"},
                "post": {"tap": "upload", "expect": "uploading", "irreversible": True}},
}


class ScreenModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data, name="details.json"):
        path = self.root / "youtube" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_loads_signature_elements_and_actions(self):
        screen = load_map(self.write(GOOD))
        self.assertEqual((screen.app, screen.screen), ("youtube", "details"))
        self.assertEqual(screen.require[0].id, "id.elements.components.metadata_editor.title")
        self.assertEqual(screen.elements["radio"].relative_to, Locator(label="@label:kids_no"))
        self.assertTrue(screen.actions["post"].irreversible)
        self.assertFalse(screen.actions["open_title"].irreversible)

    def test_rejects_coordinates_anywhere(self):
        for bad in ({**GOOD, "elements": {"x": {"label": "Next", "x": 20}}},
                    {**GOOD, "signature": {"require": [{"rect": {"x": 1}}], "forbid": []}}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "coordinates"):
                load_map(self.write(bad))

    def test_rejects_actions_on_unknown_elements_and_unknown_keys(self):
        with self.assertRaisesRegex(ValueError, "unknown element"):
            load_map(self.write({**GOOD, "actions": {"go": {"tap": "missing", "expect": "x"}}}))
        with self.assertRaisesRegex(ValueError, "Unknown locator key"):
            load_map(self.write({**GOOD, "elements": {"a": {"text": "Next"}}}))

    def test_value_and_inside_are_accepted_and_value_may_be_a_marker(self):
        screen = load_map(self.write({**GOOD, "signature": {"require": [{"type": "Slider", "value": "Less than a second"}]},
                                      "elements": {"switch": {"id": "igds-switch", "relative_to": {"label": "Row"},
                                                              "side": "inside"}}, "actions": {}}))
        self.assertEqual(screen.require[0].value, "Less than a second")
        self.assertEqual(screen.elements["switch"].side, "inside")
        with self.assertRaisesRegex(ValueError, "side"):
            load_map(self.write({**GOOD, "elements": {"s": {"id": "a", "relative_to": {"label": "R"}, "side": "under"}},
                                 "actions": {}}))

    def test_signature_needs_at_least_one_required_marker(self):
        with self.assertRaisesRegex(ValueError, "require"):
            load_map(self.write({**GOOD, "signature": {"require": [], "forbid": []}}))

    def test_load_maps_reads_every_app_folder_and_skips_labels(self):
        self.write(GOOD)
        (self.root / "labels").mkdir()
        (self.root / "labels" / "en.json").write_text("{}", encoding="utf-8")
        self.assertEqual([m.screen for m in load_maps(self.root)], ["details"])

    def test_labels_resolve_keys_and_pass_literals(self):
        (self.root / "labels").mkdir()
        (self.root / "labels" / "en.json").write_text(json.dumps({"next": "Next"}), encoding="utf-8")
        labels = Labels.load(self.root, "en")
        self.assertEqual(labels.text("@label:next"), "Next")
        self.assertEqual(labels.text("Upload Short"), "Upload Short")
        with self.assertRaisesRegex(ValueError, "missing"):
            labels.text("@label:missing")


if __name__ == "__main__":
    unittest.main()
