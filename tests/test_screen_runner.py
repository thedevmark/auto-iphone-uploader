import json
import tempfile
import unittest
from pathlib import Path

from video_drop.screens.labels import Labels
from video_drop.screens.matcher import MatchError
from video_drop.screens.model import load_maps
from video_drop.screens.runner import IrreversibleError, Runner
from video_drop.screens.snapshot import Element, Snapshot

SCREENS = {
    "trim": [Element("Button", "Next", "Next", "", 360, 880, 60, 30), Element("StaticText", "Crop your video", "Crop your video", "", 0, 60, 200, 20)],
    "details": [Element("Button", "Upload Short", "Upload Short", "", 100, 880, 240, 40), Element("TextField", "", "id.title", "", 20, 200, 400, 40)],
    "uploading": [Element("StaticText", "Uploading", "Uploading", "", 0, 60, 200, 20)],
}
FLOW = {("trim", (390.0, 895.0)): "details", ("details", (220.0, 900.0)): "uploading"}


class FakePhone:
    def __init__(self, screen="trim", stuck=False):
        self.screen, self.stuck, self.taps = screen, stuck, []

    def tap(self, x, y):
        self.taps.append((x, y))
        if not self.stuck:
            self.screen = FLOW.get((self.screen, (x, y)), self.screen)


def maps_dir(folder: Path) -> Path:
    for name, data in {
        "trim": {"require": [{"label": "Crop your video"}], "elements": {"next": {"label": "Next", "type": "Button"}},
                 "actions": {"next": {"tap": "next", "expect": "details"}}},
        "details": {"require": [{"id": "id.title"}], "elements": {"upload": {"label": "Upload Short"}},
                    "actions": {"post": {"tap": "upload", "expect": "uploading", "irreversible": True}}},
        "uploading": {"require": [{"label": "Uploading"}]},
    }.items():
        path = folder / "youtube" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"app": "youtube", "screen": name,
                                    "signature": {"require": data["require"]},
                                    "elements": data.get("elements", {}), "actions": data.get("actions", {})}), encoding="utf-8")
    return folder


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.maps = load_maps(maps_dir(Path(self.temp.name)))
        self.now = 0.0

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, phone, **kwargs):
        def read(p, screenshot=True):
            return Snapshot(440, 956, tuple(SCREENS[p.screen]))
        def sleep(seconds):
            self.now += seconds
        return Runner(phone, self.maps, Labels("en", {}), read=read, sleep=sleep, clock=lambda: self.now,
                      timeout=2, **kwargs)

    def test_act_taps_matched_center_and_verifies_next_screen(self):
        phone = FakePhone()
        self.assertEqual(self.runner(phone).act("next").screen, "details")
        self.assertEqual(phone.taps, [(390.0, 895.0)])

    def test_irreversible_action_needs_authorization(self):
        phone = FakePhone("details")
        with self.assertRaises(IrreversibleError):
            self.runner(phone).act("post")
        self.assertEqual(phone.taps, [])
        self.assertEqual(self.runner(phone, authorized=frozenset({"post"})).act("post").screen, "uploading")

    def test_action_missing_on_current_screen_fails_without_tapping(self):
        phone = FakePhone("details")
        with self.assertRaisesRegex(MatchError, "not defined"):
            self.runner(phone).act("next")
        self.assertEqual(phone.taps, [])

    def test_unconfirmed_irreversible_tap_is_never_retried(self):
        phone = FakePhone("details", stuck=True)
        with self.assertRaisesRegex(MatchError, "unconfirmed"):
            self.runner(phone, authorized=frozenset({"post"})).act("post")
        self.assertEqual(len(phone.taps), 1)


if __name__ == "__main__":
    unittest.main()
