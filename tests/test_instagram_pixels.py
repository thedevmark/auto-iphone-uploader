"""Instagram's cover proof and Edits' segment check read go-ios pixels, never WDA's /screenshot.

WDA's /screenshot queues behind a snapshot a playing preview hangs. Fakes only; no phone.
"""

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from scripts import phone_instagram as ig
from scripts import phone_youtube as share
from video_drop.screens.snapshot import Element


def png(color) -> bytes:
    out = BytesIO()
    Image.new("RGB", (88, 191), color).save(out, format="PNG")
    return out.getvalue()


def element(kind, label, left, top, width, height, name=""):
    return Element(kind, label, name or label, "", left, top, width, height)


class ScreenImageTests(unittest.TestCase):
    def test_screen_image_is_the_go_ios_frame(self):
        phone = mock.Mock()
        phone.screenshot.side_effect = AssertionError("WDA /screenshot must not be asked")
        with mock.patch.object(ig, "phone", phone), mock.patch.object(share, "phone", phone), \
                mock.patch.object(share, "pixel_source", lambda: png((10, 200, 30))):
            image = ig.screen_image()
        self.assertEqual(image.getpixel((5, 5)), (10, 200, 30))
        phone.screenshot.assert_not_called()

    def test_cover_proof_stores_and_scores_the_go_ios_frame(self):
        items = (element("StaticText", "New reel", 180, 60, 80, 20),
                 element("TextView", "", 20, 120, 300, 40, name=ig.CAPTION_ID),
                 element("Other", "Edit cover", 300, 110, 120, 200),
                 element("Button", "Preview", 310, 115, 60, 20),
                 element("Button", "Edit cover", 310, 280, 80, 20))
        phone = mock.Mock()
        phone.screenshot.side_effect = AssertionError("WDA /screenshot must not be asked")
        scored = []
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(ig, "phone", phone), \
                mock.patch.object(share, "phone", phone), \
                mock.patch.object(share, "pixel_source", lambda: png((1, 2, 3))), \
                mock.patch.object(ig, "wait_for", return_value=items), \
                mock.patch.object(ig, "first_frame", return_value=Image.new("RGB", (9, 16))), \
                mock.patch.object(ig, "cover_score", side_effect=lambda image, *rest: scored.append(image) or 3.0):
            result = ig.prove_cover({"releaseId": 7, "sourcePath": "clip.mp4"}, Path(folder))
            saved = Image.open(Path(folder) / result["screenshot"]).convert("RGB")
            self.assertEqual(saved.getpixel((0, 0)), (1, 2, 3))
        self.assertEqual(scored[0].getpixel((0, 0)), (1, 2, 3))
        self.assertEqual(result["difference"], 3.0)
        phone.screenshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
