import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_drop import server

ON = {"preparePhone": True, "youtubeQualityEveryUpload": True, "inspectPhoneOnOpen": True}
OFF = {**ON, "youtubeQualityEveryUpload": False}


def ready(status):
    return {"status": "ready", "youtube": {"uploadQuality": {"status": status}}}


class YouTubeQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.patch = patch.object(server, "STATE", self.state)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def remember(self, status):
        (self.state / "phone-inspection.json").write_text(json.dumps(ready(status)), encoding="utf-8")

    def test_first_upload_always_checks_even_when_rechecks_are_off(self):
        with patch.object(server, "run_phone_inspection", return_value=ready("full")) as inspect:
            self.assertEqual(server.youtube_quality_gate(OFF), "full")
        inspect.assert_called_once()

    def test_later_uploads_reuse_the_result_when_rechecks_are_off(self):
        self.remember("full")
        with patch.object(server, "run_phone_inspection") as inspect:
            self.assertEqual(server.youtube_quality_gate(OFF), "full")
        inspect.assert_not_called()

    def test_every_upload_setting_checks_again(self):
        self.remember("full")
        with patch.object(server, "run_phone_inspection", return_value=ready("full")) as inspect:
            server.youtube_quality_gate(ON)
        inspect.assert_called_once()

    def test_reduced_quality_stops_the_upload(self):
        with patch.object(server, "run_phone_inspection", return_value=ready("limited")), \
                self.assertRaisesRegex(ValueError, "Full quality"):
            server.youtube_quality_gate(OFF)

    def test_failed_check_stops_the_upload(self):
        with patch.object(server, "run_phone_inspection", return_value={"status": "failed", "error": "no phone"}), \
                self.assertRaisesRegex(ValueError, "nothing was uploaded"):
            server.youtube_quality_gate(ON)

    def test_unreadable_setting_does_not_block(self):
        with patch.object(server, "run_phone_inspection", return_value=ready("unverified")):
            self.assertEqual(server.youtube_quality_gate(OFF), "unverified")


if __name__ == "__main__":
    unittest.main()
