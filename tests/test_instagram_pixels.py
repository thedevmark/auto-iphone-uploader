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


    def run_cover(self, first: float, by_time: dict):
        """prove_cover with cover_score answering `first` for the first frame and by_time per sample."""
        items = (element("StaticText", "New reel", 180, 60, 80, 20),
                 element("TextView", "", 20, 120, 300, 40, name=ig.CAPTION_ID),
                 element("Other", "Edit cover", 300, 110, 120, 200),
                 element("Button", "Preview", 310, 115, 60, 20),
                 element("Button", "Edit cover", 310, 280, 80, 20))
        frames = {at: Image.new("RGB", (9, 16), (int(at * 10), 0, 0)) for at in by_time}
        first_image = Image.new("RGB", (9, 16), (255, 255, 255))
        score = lambda image, frame, *rest: first if frame is first_image else by_time[frame.getpixel((0, 0))[0] / 10]  # noqa: E731
        with tempfile.TemporaryDirectory() as folder,                 mock.patch.object(share, "pixel_source", lambda: png((1, 2, 3))),                 mock.patch.object(ig, "wait_for", return_value=items),                 mock.patch.object(ig, "first_frame", return_value=first_image),                 mock.patch.object(ig, "sampled_frames", return_value=sorted(frames.items())),                 mock.patch.object(ig, "cover_score", side_effect=score):
            return ig.prove_cover({"releaseId": 7, "sourcePath": "clip.mp4"}, Path(folder))

    def test_a_true_first_frame_above_the_fixed_limit_passes_by_best_match(self):
        # A true first frame can score 15.5, above the fixed limit of 10.
        result = self.run_cover(15.5, {0.0: 15.9, 0.5: 15.6, 1.0: 29.7, 2.0: 57.6})
        self.assertEqual(result["check"], "best match at the start")

    def test_a_cover_from_later_in_the_clip_still_stops(self):
        with self.assertRaisesRegex(ig.Stop, "best match at 2.0 s"):
            self.run_cover(15.5, {0.0: 15.9, 0.5: 16.0, 1.0: 12.0, 2.0: 4.0})

    def test_a_clip_that_does_not_change_cannot_prove_its_first_frame(self):
        with self.assertRaises(ig.Stop):
            self.run_cover(15.5, {0.0: 15.5, 0.5: 15.5, 1.0: 15.6, 2.0: 15.7})

if __name__ == "__main__":
    unittest.main()
