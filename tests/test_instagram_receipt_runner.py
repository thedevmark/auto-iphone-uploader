import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from scripts import phone_instagram_receipt as receipt
from video_drop.core import Store


CAPTION = "A finished clip #gaming #reels"


def frame_and_screen():
    frame = Image.new("RGB", (320, 568), "#152020")
    draw = ImageDraw.Draw(frame)
    draw.rectangle((0, 124, 320, 284), fill="#f2c849")
    draw.rectangle((0, 284, 320, 444), fill="#2878bd")
    draw.ellipse((100, 220, 220, 340), fill="#e92573")
    screen = Image.new("RGB", (1320, 2868), "white")
    screen.paste(frame.crop((0, 124, 320, 444)).resize((219, 219)), (36, 390))
    png = io.BytesIO()
    screen.save(png, format="PNG")
    return frame, png.getvalue()


class FakeInstagram:
    def __init__(self, screenshot: bytes, *, account: str = "creator", scheduled: bool = True):
        self.screenshot_bytes = screenshot
        self.account = account
        self.scheduled = scheduled
        self.screen = "home"
        self.tapped = []

    def unlock(self):
        pass

    def open_app(self, bundle_id, wait_seconds=2):
        self.screen = "home"

    def current_app(self):
        return {"bundleId": "com.burbn.instagram"}

    def screen_info(self):
        return {"width": 440, "height": 956}

    def compact(self, rows, limit=None):
        return rows

    def ocr(self):
        if self.screen == "home":
            return [{"text": "Profile", "type": "Button", "x": 410, "y": 920}]
        if self.screen == "profile":
            return [{"text": self.account, "type": "Button", "x": 220, "y": 85},
                    {"text": "Menu", "type": "Button", "x": 400, "y": 85}]
        if self.screen == "menu":
            return [{"text": "Scheduled content", "type": "Button", "x": 215, "y": 180}]
        rows = [{"text": "Scheduled content", "x": 220, "y": 90}]
        if self.scheduled:
            rows += [{"text": "A finished clip", "x": 215, "y": 139},
                     {"text": "#gaming #reels", "x": 200, "y": 158},
                     {"text": "Scheduled Sep 29 at 10:00 AM", "x": 215, "y": 181}]
        return rows

    def tap(self, x, y):
        self.tapped.append((x, y))
        self.screen = {"home": "profile", "profile": "menu", "menu": "scheduled"}[self.screen]

    def scroll(self, direction, amount):
        pass

    def screenshot(self):
        return self.screenshot_bytes


class InstagramReceiptRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        (self.state / "accounts.json").write_text('{"instagram":"@creator"}', encoding="utf-8")
        source = self.state / "clip.mp4"
        source.write_bytes(b"finished clip fixture")
        self.db = self.state / "video-drop.sqlite"
        with Store(self.db, {"instagram": "@creator"}) as store:
            self.release_id = store.import_file(source)["id"]
            store.save_text(self.release_id, "instagram", "@creator", "A finished clip", CAPTION, "")
            store.authorize(self.release_id, "instagram")
            store.reserve_slot(self.release_id, datetime(2026, 9, 29, 8, tzinfo=timezone.utc))
            store.mark_unconfirmed(self.release_id, "instagram")
        self.frame, screenshot = frame_and_screen()
        self.device = FakeInstagram(screenshot)

    def tearDown(self):
        self.temp.cleanup()

    def run_with_phone(self):
        with patch.object(receipt.preflight, "connect_sidetap"), \
                patch.object(receipt.preflight, "phone", self.device), \
                patch.object(receipt, "connected_usb_udid", return_value="phone-one"), \
                patch.object(receipt, "read_device_time_zone", return_value="America/New_York") as zone, \
                patch("video_drop.instagram_schedule.opening_frames", return_value=[self.frame]):
            result = receipt.run(self.release_id, self.db)
            zone.assert_called_once_with("phone-one")
            return result

    def test_usb_target_must_be_unique_and_match_sidetap_pin(self):
        self.assertEqual(receipt.single_usb_udid(["phone-one"], ""), "phone-one")
        self.assertEqual(receipt.single_usb_udid(["phone-one"], "phone-one"), "phone-one")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            receipt.single_usb_udid([], "")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            receipt.single_usb_udid(["phone-one", "phone-two"], "")
        with self.assertRaisesRegex(ValueError, "different iPhone"):
            receipt.single_usb_udid(["phone-one"], "phone-two")

    def test_matching_native_screen_records_schedule_once(self):
        result = self.run_with_phone()
        self.assertEqual(result["kind"], "scheduled")
        self.assertTrue(Path(result["evidence"]).is_file())
        self.assertEqual(len(self.device.tapped), 3)  # Profile, menu, Scheduled content.
        with Store(self.db) as store:
            destination = next(d for d in store.release(self.release_id)["destinations"]
                               if d["platform"] == "instagram")
            self.assertEqual(destination["status"], "scheduled")

    def test_wrong_account_or_missing_native_row_stays_unconfirmed(self):
        self.device.account = "wrongaccount"
        with self.assertRaisesRegex(ValueError, "expected @creator"):
            self.run_with_phone()
        self.device.account = "creator"
        self.device.scheduled = False
        with self.assertRaisesRegex(ValueError, "no unique scheduled entry"):
            self.run_with_phone()
        with Store(self.db) as store:
            destination = next(d for d in store.release(self.release_id)["destinations"]
                               if d["platform"] == "instagram")
            self.assertEqual(destination["status"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
