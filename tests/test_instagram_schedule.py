import unittest
import subprocess
from unittest.mock import patch

from PIL import Image, ImageDraw

from video_drop.instagram_schedule import (matching_scheduled_cover,
                                           matching_scheduled_reel, read_device_time_zone,
                                           scheduled_list_label,
                                           verified_scheduled_reel)
from video_drop.phone_ui import PhoneLayout


SLOT = "2026-09-29T14:00:00+00:00"  # 10 AM in New York
CAPTION = "A finished clip #gaming #reels"
LAYOUT = PhoneLayout(440, 956)


def scheduled_rows(caption: str = CAPTION, time: str = "Scheduled Sep 29 at 10:00 AM") -> list[dict]:
    prose, tags = caption.split(" #", 1)
    return [
        {"text": "Scheduled content", "x": 220, "y": 90},
        {"text": prose, "x": 215, "y": 139},
        {"text": "#" + tags, "x": 200, "y": 158},
        {"text": time, "x": 215, "y": 181},
    ]


class InstagramScheduleTests(unittest.TestCase):
    def test_device_time_zone_must_come_from_a_valid_go_ios_value(self):
        valid = subprocess.CompletedProcess([], 0, '{"TimeZone":"America/New_York"}', "")
        with patch("video_drop.instagram_schedule.subprocess.run", return_value=valid) as run:
            self.assertEqual(read_device_time_zone("phone-one"), "America/New_York")
            self.assertEqual(run.call_args.args[0][-1], "--udid=phone-one")
        wrapped = subprocess.CompletedProcess([], 0, '{"Value":"America/New_York"}', "")
        with patch("video_drop.instagram_schedule.subprocess.run", return_value=wrapped):
            self.assertEqual(read_device_time_zone("phone-one"), "America/New_York")
        invalid = subprocess.CompletedProcess([], 0, '{"TimeZone":"not-a-zone"}', "")
        global_values = subprocess.CompletedProcess([], 0,
            '{"Value":{"Other":"America/Los_Angeles","TimeZone":"America/New_York"}}', "")
        with patch("video_drop.instagram_schedule.subprocess.run", side_effect=[invalid, global_values]) as run:
            self.assertEqual(read_device_time_zone("phone-one"), "America/New_York")
            self.assertEqual(run.call_args_list[1].args[0],
                             ("ios", "lockdown", "get", "--udid=phone-one"))
        unrelated = subprocess.CompletedProcess([], 0, '{"Other":"America/Los_Angeles"}', "")
        with patch("video_drop.instagram_schedule.subprocess.run", return_value=unrelated):
            with self.assertRaisesRegex(ValueError, "keep Instagram unconfirmed"):
                read_device_time_zone("phone-one")
        with patch("video_drop.instagram_schedule.subprocess.run", return_value=invalid):
            with self.assertRaisesRegex(ValueError, "keep Instagram unconfirmed"):
                read_device_time_zone("phone-one")
        with self.assertRaisesRegex(ValueError, "Select one connected iPhone"):
            read_device_time_zone("")

    def test_scheduled_thumbnail_matches_first_frame_but_rejects_another_clip(self):
        frame = Image.new("RGB", (320, 568), "#152020")
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 124, 320, 284), fill="#f2c849")
        draw.rectangle((0, 284, 320, 444), fill="#2878bd")
        draw.ellipse((100, 220, 220, 340), fill="#e92573")
        square = frame.crop((0, 124, 320, 444)).resize((219, 219))
        screenshot = Image.new("RGB", (1320, 2868), "white")
        screenshot.paste(square, (36, 390))
        verified = verified_scheduled_reel(scheduled_rows(), screenshot, frame,
                                           CAPTION, SLOT, "America/New_York", LAYOUT)
        self.assertLess(verified["difference"], 5)
        wrong = Image.new("RGB", (320, 568), "#d5eafb")
        wrong_draw = ImageDraw.Draw(wrong)
        wrong_draw.rectangle((0, 124, 320, 284), fill="#172bbd")
        wrong_draw.rectangle((0, 284, 320, 444), fill="#e93113")
        wrong_draw.ellipse((100, 220, 220, 340), fill="#12cb66")
        with self.assertRaisesRegex(ValueError, "does not match"):
            verified_scheduled_reel(scheduled_rows(), screenshot, wrong,
                                    CAPTION, SLOT, "America/New_York", LAYOUT)

    def test_uniform_first_frame_is_not_sufficient_cover_evidence(self):
        screenshot = Image.new("RGB", (1320, 2868), "black")
        frame = Image.new("RGB", (320, 568), "black")
        with self.assertRaisesRegex(ValueError, "too little visual detail"):
            matching_scheduled_cover(screenshot, frame, {"captionRows": [{"y": 140}]}, LAYOUT)

    def test_native_list_matches_caption_and_new_york_slot(self):
        match = matching_scheduled_reel(scheduled_rows(), CAPTION, SLOT,
                                        "America/New_York", LAYOUT)
        self.assertEqual(match["scheduledLabel"], "Scheduled Sep 29 at 10:00 AM")
        self.assertEqual(match["caption"], CAPTION)
        self.assertEqual(len(match["captionRows"]), 2)

    def test_device_time_zone_is_explicit(self):
        self.assertEqual(scheduled_list_label(SLOT, "America/Los_Angeles"),
                         "Scheduled Sep 29 at 7:00 AM")
        with self.assertRaisesRegex(ValueError, "time zone"):
            scheduled_list_label("2026-09-29T14:00:00", "America/New_York")

    def test_wrong_time_or_caption_does_not_count_as_receipt(self):
        with self.assertRaisesRegex(ValueError, "no unique scheduled entry"):
            matching_scheduled_reel(scheduled_rows(time="Scheduled Sep 29 at 7:00 PM"),
                                    CAPTION, SLOT, "America/New_York", LAYOUT)
        with self.assertRaisesRegex(ValueError, "caption is missing"):
            matching_scheduled_reel(scheduled_rows(), "Different clip #gaming #reels",
                                    SLOT, "America/New_York", LAYOUT)

    def test_duplicate_time_or_missing_screen_fails_closed(self):
        rows = scheduled_rows()
        with self.assertRaisesRegex(ValueError, "unique scheduled entry"):
            matching_scheduled_reel(rows + [rows[-1]], CAPTION, SLOT,
                                    "America/New_York", LAYOUT)
        with self.assertRaisesRegex(ValueError, "screen is missing"):
            matching_scheduled_reel(rows[1:], CAPTION, SLOT,
                                    "America/New_York", LAYOUT)


if __name__ == "__main__":
    unittest.main()
