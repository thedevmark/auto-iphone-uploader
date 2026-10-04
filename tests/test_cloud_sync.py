"""The OneDrive check that runs on the PC before a run touches the phone."""

import unittest
from pathlib import Path

from video_drop import cloud_sync

ROOT = Path("C:/Users/creator/OneDrive")
VIDEO = ROOT / "_Videos" / "clip.mp4"


def refusal(path=VIDEO, *, running=True, state="2"):
    return cloud_sync.refusal(path, roots=[ROOT], running=lambda: running, state=lambda _: state)


class CloudSyncTests(unittest.TestCase):
    def test_a_synced_video_passes(self):
        for state in ("1", "2", "3"):  # online-only, in sync, pinned: the cloud has it
            with self.subTest(state=state):
                self.assertIsNone(refusal(state=state))

    def test_onedrive_not_running_refuses_before_the_phone(self):
        # 2026-10-03: OneDrive was off after a restart and YouTube stopped on the phone at "No Results".
        message = refusal(running=False)
        self.assertIn("OneDrive isn't running", message)
        self.assertIn("clip.mp4", message)
        self.assertTrue(message.endswith("Nothing was posted."))

    def test_a_video_still_uploading_refuses_with_its_state(self):
        self.assertIn("still uploading", refusal(state="4"))
        self.assertIn("excluded from sync", refusal(state="9"))
        self.assertIn("not synced", refusal(state="42"))

    def test_no_answer_from_onedrive_refuses(self):
        self.assertIn("isn't reporting", refusal(state=""))

    def test_videos_outside_onedrive_are_not_its_business(self):
        self.assertIsNone(refusal(Path("C:/Clips/local.mp4"), running=False, state=""))

    def test_the_onedrive_folder_is_matched_by_path_not_by_name(self):
        self.assertTrue(cloud_sync.in_onedrive(VIDEO, [ROOT]))
        self.assertFalse(cloud_sync.in_onedrive(Path("C:/Users/creator/OneDrive-old/a.mp4"), [ROOT]))


if __name__ == "__main__":
    unittest.main()
