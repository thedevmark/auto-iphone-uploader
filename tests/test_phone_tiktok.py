import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.phone_tiktok import (NEXT_POINT, VIDEO_POINT, composer_ready, cover_match, cover_reference,
                                  post_button, release_input)
from video_drop.core import Store
from video_drop.phone_ui import PhoneLayout
from video_drop.screens.snapshot import Element, Snapshot

CAPTION = "I should really turn down the max brightness #twitch #fyp"


def approved_release(folder: str, caption: str = CAPTION) -> tuple[Store, int]:
    source = Path(folder) / "clip.mp4"
    source.write_bytes(b"finished source")
    store = Store(Path(folder) / "release.sqlite", {"tiktok": "@creator"})
    release_id = store.import_file(source)["id"]
    store.save_text(release_id, "tiktok", "@creator", "Title", caption, "")
    store.authorize(release_id, "tiktok")
    return store, release_id


def element(kind, label, left, top, width, height, value=""):
    return Element(kind, label, label, value, left, top, width, height)


# The recorded 440 x 956 post screen (.state/fixtures/tiktok/post-screen-*).
PREVIEW = element("StaticText", "Preview", 322, 122, 49, 17)
EDIT_COVER = element("Button", "Edit cover", 318, 246, 100, 26)
POST = element("Button", "Post", 223, 870, 205, 48)
DESCRIPTION = element("TextView", "Add description...", 16, 120, 280, 143, CAPTION)


def post_screen(*extra, app="com.zhiliaoapp.musically"):
    return Snapshot(440, 956, (DESCRIPTION, PREVIEW, EDIT_COVER, POST, *extra), app=app)


class TikTokInputTests(unittest.TestCase):
    def test_only_the_exact_approved_revision_enters_the_composer(self):
        with tempfile.TemporaryDirectory() as folder:
            store, release_id = approved_release(folder)
            with store:
                prepared = release_input(store, release_id)
                self.assertEqual(prepared["caption"], CAPTION)
                self.assertEqual(prepared["filename"], "clip.mp4")
                with Store(Path(folder) / "release.sqlite", {"tiktok": "@another"}) as wrong_target:
                    with self.assertRaisesRegex(ValueError, "differs from local accounts.json"):
                        release_input(wrong_target, release_id)
                store.db.execute("UPDATE destination SET description='Changed #fyp' WHERE release_id=? AND platform='tiktok'",
                                 (release_id,))
                store.db.commit()
                with self.assertRaisesRegex(Exception, "changed after approval"):
                    release_input(store, release_id)

    def test_unapproved_text_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "clip.mp4"
            source.write_bytes(b"finished source")
            with Store(Path(folder) / "release.sqlite", {"tiktok": "@creator"}) as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "tiktok", "@creator", "", CAPTION, "")
                with self.assertRaisesRegex(Exception, "not approved"):
                    release_input(store, release_id)

    def test_caption_must_keep_fyp(self):
        with tempfile.TemporaryDirectory() as folder:
            store, release_id = approved_release(folder, "Approved caption #fypx #game")
            with store, self.assertRaisesRegex(Exception, "#fyp"):
                release_input(store, release_id)

    def test_changed_source_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            store, release_id = approved_release(folder)
            with store:
                (Path(folder) / "clip.mp4").write_bytes(b"finished sourcf")
                with self.assertRaisesRegex(Exception, "Source content changed"):
                    release_input(store, release_id)
                (Path(folder) / "clip.mp4").write_bytes(b"longer finished source")
                with self.assertRaisesRegex(Exception, "filename or size"):
                    release_input(store, release_id)

    def test_an_attempted_tiktok_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            store, release_id = approved_release(folder)
            with store:
                store.set_delivery_mode(release_id, "post_now")
                store.mark_unconfirmed(release_id, "tiktok")
                with self.assertRaisesRegex(Exception, "already attempted"):
                    release_input(store, release_id)


class TikTokScreenTests(unittest.TestCase):
    def test_post_is_the_one_reachable_button(self):
        self.assertEqual(post_button(post_screen()), POST)
        keyboard = element("Keyboard", "", 0, 620, 440, 336)
        with self.assertRaisesRegex(Exception, "found 0"):
            post_button(post_screen(keyboard))
        top_post = element("Button", "Post", 366, 62, 60, 44)
        self.assertEqual(post_button(post_screen(keyboard, top_post)), top_post)
        with self.assertRaisesRegex(Exception, "found 2"):
            post_button(post_screen(top_post))

    def test_composer_needs_the_exact_caption_in_tiktok(self):
        self.assertEqual(composer_ready(post_screen(), CAPTION), POST)
        with self.assertRaisesRegex(Exception, "does not match"):
            composer_ready(post_screen(), CAPTION + " ")
        with self.assertRaisesRegex(Exception, "not in front"):
            composer_ready(post_screen(app="com.microsoft.skydrive"), CAPTION)

    def test_measured_points_hold_on_the_reference_phone_and_follow_safe_areas(self):
        reference = PhoneLayout(440, 956)
        self.assertEqual(reference.bottom_sheet_point(*VIDEO_POINT), VIDEO_POINT)
        self.assertEqual(reference.bottom_right_point(*NEXT_POINT), NEXT_POINT)
        standard = PhoneLayout(393, 852)
        x, y = standard.bottom_sheet_point(*VIDEO_POINT)
        self.assertAlmostEqual(x, 155 * 393 / 440)
        self.assertEqual(956 - 34 - 828, 852 - 34 - y)
        self.assertEqual(standard.bottom_right_point(*NEXT_POINT), (393 - 115, 852 - 34 - 26))
        home_button = PhoneLayout(375, 667)
        self.assertEqual(home_button.bottom_sheet_point(*VIDEO_POINT)[1], 667 - (956 - 34 - 828))


def detailed_frame(seed: int) -> Image.Image:
    rng = random.Random(seed)
    frame = Image.new("RGB", (1080, 1920))
    draw = ImageDraw.Draw(frame)
    for _ in range(160):
        x, y = rng.randrange(1080), rng.randrange(1920)
        draw.rectangle((x, y, x + rng.randrange(60, 260), y + rng.randrange(60, 260)),
                       fill=tuple(rng.randrange(256) for _ in range(3)))
    return frame


def screen_with_cover(frame: Image.Image) -> Image.Image:
    """Draw TikTok's cover card as recorded: the frame filling (312,120)-(424,272), labels on top."""
    scale = 3
    shot = Image.new("RGB", (440 * scale, 956 * scale))
    left, top, right, bottom = 312, 120, 424, 272
    fill = max((right - left) / frame.width, (bottom - top) / frame.height)
    shown = frame.resize((round(frame.width * fill * scale), round(frame.height * fill * scale)))
    ox = (shown.width - (right - left) * scale) // 2
    oy = (shown.height - (bottom - top) * scale) // 2
    shot.paste(shown.crop((ox, oy, ox + (right - left) * scale, oy + (bottom - top) * scale)),
               (left * scale, top * scale))
    draw = ImageDraw.Draw(shot)
    for item in (PREVIEW, EDIT_COVER):
        draw.rectangle((item.left * scale, item.top * scale, (item.left + item.width) * scale,
                        (item.top + item.height) * scale), fill=(240, 240, 240))
    return shot


class TikTokCoverTests(unittest.TestCase):
    def test_first_frame_cover_passes_and_another_frame_stops(self):
        layout = PhoneLayout(440, 956)
        first = detailed_frame(1)
        shot = screen_with_cover(first)
        match = cover_match(shot, cover_reference(first), PREVIEW, EDIT_COVER, layout)
        self.assertLess(match["difference"], 8)
        with self.assertRaisesRegex(ValueError, "not the first frame"):
            cover_match(screen_with_cover(detailed_frame(2)), cover_reference(first), PREVIEW, EDIT_COVER, layout)

    def test_flat_first_frame_cannot_prove_a_cover(self):
        with self.assertRaisesRegex(Exception, "too little detail"):
            cover_reference(Image.new("RGB", (1080, 1920), (20, 20, 20)))


if __name__ == "__main__":
    unittest.main()
