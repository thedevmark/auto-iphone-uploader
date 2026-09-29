import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.record_screen import crop_icon, draft_map
from video_drop.screens.snapshot import Element, Snapshot, save_fixture


def el(type_, label, name=""):
    return Element(type_, label, name or label, "", 0, 0, 40, 20)


class RecordScreenTests(unittest.TestCase):
    def test_draft_prefers_identifiers_and_lists_unique_controls(self):
        snap = Snapshot(440, 956, (el("Other", "", "id.elements.components.metadata_editor.title"),
                                   el("Button", "Next"), el("Button", "Back"), el("Button", "Back"),
                                   el("StaticText", "Add details")))
        draft = draft_map(snap, "youtube", "details")
        self.assertEqual(draft["signature"]["require"], [{"id": "id.elements.components.metadata_editor.title"}])
        self.assertEqual(draft["elements"], {"next": {"label": "Next", "type": "Button"}})
        self.assertNotIn("'x':", str(draft))

    def test_draft_falls_back_to_static_text_markers(self):
        snap = Snapshot(440, 956, (el("StaticText", "Crop your video"), el("Button", "Next")))
        self.assertEqual(draft_map(snap, "youtube", "trim")["signature"]["require"], [{"label": "Crop your video"}])

    def test_crop_icon_uses_points(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1320, 2868), "white").save(buffer, "PNG")
        with tempfile.TemporaryDirectory() as folder:
            stem = Path(folder) / "shot"
            save_fixture(Snapshot(440, 956, (), buffer.getvalue()), stem)
            out = crop_icon(stem, 20, 400, 24, 24, Path(folder) / "icons" / "radio.png")
            self.assertEqual(Image.open(out).size, (24, 24))


if __name__ == "__main__":
    unittest.main()
