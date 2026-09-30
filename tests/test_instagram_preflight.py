import unittest

from scripts.phone_instagram_preflight import profile_handle
from video_drop.screens.snapshot import Element


def button(label, name, x=220, y=87):
    return Element("Button", label, name, "", x - 50, y - 22, 100, 44)


class ProfileHandleTests(unittest.TestCase):
    # Recorded 2026-09-30: the handle sits in the account-switcher button, next to icon buttons.
    def test_reads_the_account_switcher_button(self):
        header = (button("example.creator", "user-switch-title-button"),
                  Element("StaticText", "example.creator", "example.creator", "example.creator", 124, 76, 164, 22),
                  button("Tap to open creation menu", "profile-add-button", 38),
                  button("Switch to Threads", "profile-app-switch-button", 348))
        self.assertEqual(profile_handle(header), "@example.creator")

    def test_missing_or_ambiguous_header_stops(self):
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            profile_handle((button("Reels", "reels-title"),))
        with self.assertRaisesRegex(ValueError, "missing or ambiguous"):
            profile_handle((button("one", "user-switch-title-button"), button("two", "user-switch-title-button", 300)))
