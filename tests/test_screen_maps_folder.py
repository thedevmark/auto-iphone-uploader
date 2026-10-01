import json
import unittest
from pathlib import Path

from video_drop.screens.labels import Labels
from video_drop.screens.model import COORDINATE_KEYS, load_maps

MAPS = Path(__file__).resolve().parent.parent / "maps"


def keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


class MapsFolderTests(unittest.TestCase):
    def test_every_map_loads_and_has_no_coordinates(self):
        maps = load_maps(MAPS)
        self.assertGreaterEqual({(m.app, m.screen) for m in maps},
                                {("youtube", s) for s in ("trim", "editor", "details", "visibility", "audience")})
        for path in MAPS.glob("*/*.json"):
            with self.subTest(path=path.name):
                self.assertFalse(set(keys(json.loads(path.read_text(encoding="utf-8")))) & COORDINATE_KEYS)

    def test_every_label_key_resolves(self):
        labels = Labels.load(MAPS, "en")
        for screen in load_maps(MAPS):
            for loc in (*screen.require, *screen.forbid, *screen.elements.values()):
                for item in (loc, loc.relative_to, *loc.fallback):
                    for text in (item.label, item.value) if item else ():
                        if text:
                            with self.subTest(screen=screen.screen, text=text):
                                labels.text(text)

    def test_actions_expect_known_screens_and_upload_is_irreversible(self):
        maps = load_maps(MAPS)
        for screen in maps:
            names = {m.screen for m in maps if m.app == screen.app}
            for action in screen.actions.values():
                # "uploading" is the unmapped state after a final tap; the runner reports it unconfirmed.
                self.assertIn(action.expect, names | {"uploading"}, f"{screen.app}/{screen.screen}.{action.name}")
        details = next(m for m in maps if (m.app, m.screen) == ("youtube", "details"))
        self.assertTrue(details.actions["upload"].irreversible)


if __name__ == "__main__":
    unittest.main()
