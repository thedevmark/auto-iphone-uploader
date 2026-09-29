import unittest

from video_drop.instagram_schedule import matching_scheduled_reel, scheduled_list_label
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
