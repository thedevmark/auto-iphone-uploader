import tempfile
import unittest
from pathlib import Path

from video_drop.screens.snapshot import Element, Snapshot, capture, elements_from_tree, load_fixture, save_fixture

TREE = {"type": "XCUIElementTypeApplication", "rect": {"x": 0, "y": 0, "width": 440, "height": 956}, "children": [
    {"type": "XCUIElementTypeButton", "label": "Next", "name": "Next", "rect": {"x": 360, "y": 880, "width": 60, "height": 30}},
    {"type": "XCUIElementTypeTextField", "label": "", "name": "id.title", "value": "hello",
     "rect": {"x": 20, "y": 200, "width": 400, "height": 40}},
    {"type": "Button", "label": "Hidden", "isVisible": "0", "rect": {"x": 1, "y": 1, "width": 5, "height": 5}},
    {"type": "Other", "label": "Zero", "rect": {"x": 1, "y": 1, "width": 0, "height": 5}},
]}


class FakePhone:
    def ui_tree(self):
        return TREE

    def screen_info(self):
        return {"width": 440, "height": 956}

    def screenshot(self):
        return b"\x89PNG fake"

    def current_app(self):
        return {"bundleId": "com.google.ios.youtube"}


class SnapshotTests(unittest.TestCase):
    def test_flattens_visible_sized_elements_and_strips_type_prefix(self):
        elements = elements_from_tree(TREE)
        self.assertEqual([e.type for e in elements], ["Application", "Button", "TextField"])
        button = elements[1]
        self.assertEqual((button.x, button.y), (390.0, 895.0))
        self.assertEqual(elements[2].name, "id.title")
        self.assertEqual(elements[2].texts, ("id.title", "hello"))

    def test_capture_reads_tree_size_app_and_screenshot(self):
        snapshot = capture(FakePhone())
        self.assertEqual((snapshot.width, snapshot.height, snapshot.app), (440.0, 956.0, "com.google.ios.youtube"))
        self.assertEqual(snapshot.screenshot, b"\x89PNG fake")

    def test_fixture_round_trip(self):
        snapshot = capture(FakePhone())
        with tempfile.TemporaryDirectory() as folder:
            stem = Path(folder) / "youtube-details"
            save_fixture(snapshot, stem)
            self.assertTrue(stem.with_suffix(".json").is_file())
            self.assertEqual(load_fixture(stem), snapshot)

    def test_snapshot_without_screenshot_saves_json_only(self):
        snapshot = Snapshot(440, 956, (Element("Button", "Next", "Next", "", 0, 0, 10, 10),))
        with tempfile.TemporaryDirectory() as folder:
            stem = Path(folder) / "plain"
            save_fixture(snapshot, stem)
            self.assertFalse(stem.with_suffix(".png").exists())
            self.assertEqual(load_fixture(stem), snapshot)


if __name__ == "__main__":
    unittest.main()
