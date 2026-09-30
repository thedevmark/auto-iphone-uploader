import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from video_drop.phone_ui import size_shown, PhoneLayout, filled_radio, share_app_position, youtube_identity, youtube_page_account
from video_drop.phone_onboarding import build_profile
from video_drop.youtube_nav import exit_target, open_tabs
from scripts.phone_onboard import upload_quality_from_labels


class PhoneUiTests(unittest.TestCase):
    def test_upload_quality_requires_selected_upload_value(self):
        self.assertEqual(upload_quality_from_labels(["Upload quality, Full quality"]), "full")
        self.assertEqual(upload_quality_from_labels(["Upload quality", "Full quality, Selected"]), "full")
        self.assertEqual(upload_quality_from_labels(["Upload quality, Standard quality"]), "limited")
        self.assertEqual(upload_quality_from_labels(["Video quality preferences", "Higher picture quality"]), "unverified")
        self.assertEqual(upload_quality_from_labels(["Upload quality", "Full quality", "Standard quality"]), "unverified")

    def test_share_picker_never_taps_a_clipped_app_or_recent_contact(self):
        layout = PhoneLayout(440, 956)
        rows = [
            {"text": "Threads", "x": -0.5, "y": 392.5, "type": "Cell"},
            {"text": "Threads", "x": 169.5, "y": 255, "type": "Cell"},
            {"text": "X", "x": 99.5, "y": 392.5, "type": "Cell"},
        ]
        self.assertEqual(share_app_position(rows, "Threads", layout), ("right", None))
        rows[0]["x"] = 63.5
        direction, target = share_app_position(rows, "Threads", layout)
        self.assertEqual(direction, "tap")
        self.assertEqual(target["x"], 63.5)

    def test_reference_points_scale_for_another_portrait_iphone(self):
        layout = PhoneLayout.from_info({"width": 390, "height": 844})
        self.assertEqual(layout.reference_point(220, 478), (195, 422))
        self.assertTrue(layout.relative_band({"x": 350, "y": 700}, top=0.75))
        self.assertFalse(layout.contains({"x": 450, "y": 700}))

    def test_landscape_fails_before_a_fallback_tap(self):
        with self.assertRaisesRegex(ValueError, "portrait"):
            PhoneLayout.from_info({"width": 844, "height": 390})

    def test_youtube_handle_is_read_from_identity_chip(self):
        labels = ["Add details", "id.elements.components.identity_chip_component",
                  "Example Creator, @examplechannel", "Visibility, Public"]
        self.assertEqual(youtube_identity(labels), "@examplechannel")
        labels[2] = "Second Creator, @secondchannel"
        self.assertEqual(youtube_identity(labels), "@secondchannel")
        with self.assertRaisesRegex(ValueError, "missing"):
            youtube_identity(["Add details"])

    def test_onboarding_distinguishes_installed_from_verified_account(self):
        profile = build_profile(
            {"width": 390, "height": 844},
            [{"bundle_id": "com.google.ios.youtube"}, {"bundle_id": "com.burbn.instagram"}],
            {"youtube": {"selected": "@examplechannel", "available": ["@examplechannel"],
                         "uploadQuality": {"status": "full"}}},
            {"youtube": "@examplechannel", "instagram": "@other"},
        )
        self.assertTrue(profile["apps"]["youtube"]["accountVerified"])
        self.assertTrue(profile["apps"]["youtube"]["targetMatched"])
        self.assertEqual(profile["apps"]["youtube"]["uploadQuality"]["status"], "full")
        self.assertTrue(profile["apps"]["instagram"]["installed"])
        self.assertFalse(profile["apps"]["instagram"]["accountVerified"])
        self.assertFalse(profile["apps"]["instagram"]["targetMatched"])
        self.assertFalse(profile["apps"]["tiktok"]["installed"])
        wrong = build_profile(
            {"width": 390, "height": 844},
            [{"bundle_id": "com.google.ios.youtube"}],
            {"youtube": {"selected": "@other", "available": ["@other"]}},
            {"youtube": "@examplechannel"},
        )
        self.assertTrue(wrong["apps"]["youtube"]["accountVerified"])
        self.assertFalse(wrong["apps"]["youtube"]["targetMatched"])

    def test_selected_channel_comes_from_youtube_header_only(self):
        labels = ["id.elements.components.page_header", "Example Creator", "@examplechannel",
                  "View channel", "Other creator, @otherchannel"]
        self.assertEqual(youtube_page_account(labels), "@examplechannel")
        with self.assertRaisesRegex(ValueError, "missing"):
            youtube_page_account(["@examplechannel"])

    def test_youtube_composer_has_a_known_reverse_path(self):
        self.assertEqual(exit_target(["Discard changes?", "Cancel", "Discard"]), "Discard")
        self.assertEqual(exit_target(["Set visibility", "Back", "Schedule"]), "Back")
        self.assertEqual(exit_target(["Add details", "Back", "Upload Short"]), "Back")
        self.assertEqual(exit_target(["Exit editor", "Next"]), "Exit editor")
        self.assertEqual(exit_target(["Exit trim", "Next"]), "Exit trim")
        self.assertIsNone(exit_target(["Home", "You", "Create"]))
        with self.assertRaisesRegex(ValueError, "not on an observed"):
            exit_target(["Upload Short"])

    def test_youtube_waits_for_tabs_after_a_transient_launch_screen(self):
        phone = MagicMock()
        phone.current_app.return_value = {"bundleId": "com.google.ios.youtube"}
        with patch("video_drop.youtube_nav.visible_rows", side_effect=[
            [{"text": "YouTube"}], [{"text": "Home"}, {"text": "You"}],
        ]), patch("video_drop.youtube_nav.time.sleep"):
            open_tabs(phone)
        phone.press_home.assert_called_once()
        phone.tap.assert_not_called()

    def test_radio_reads_selected_center_in_both_phone_themes(self):
        layout = PhoneLayout.from_info({"width": 440, "height": 956})
        for background, ring in ((15, 241), (241, 15)):
            image = Image.new("RGB", (1320, 2868), (background,) * 3)
            image.putpixel((75, 1056), (ring,) * 3)
            self.assertFalse(filled_radio(image, layout, 35, 352))
            image.putpixel((105, 1056), (ring,) * 3)
            self.assertTrue(filled_radio(image, layout, 35, 352))


class SizeShownTests(unittest.TestCase):
    def test_accepts_each_way_the_phone_rounds_the_size(self):
        self.assertTrue(size_shown(1_101_781_282, ["episode6.mp4", "1 GB"]))
        self.assertTrue(size_shown(1_101_781_282, ["Video · 1.1 GB"]))
        self.assertTrue(size_shown(271_151_905, ["271.2 MB"]))
        self.assertTrue(size_shown(271_151_905, ["258.6 MB"]))

    def test_rejects_a_different_size(self):
        self.assertFalse(size_shown(271_151_905, ["238.5 MB"]))
        self.assertFalse(size_shown(1_101_781_282, ["11 GB", "2 GB"]))


if __name__ == "__main__":
    unittest.main()
