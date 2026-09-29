import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_drop.sidetap_root import sidetap_root


def install(root: Path) -> Path:
    (root / "src" / "phone_harness").mkdir(parents=True)
    return root


class SideTapRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.local = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def find(self, configured=""):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local), "SIDETAP_ROOT": configured}):
            return sidetap_root()

    def test_explicit_setting_wins(self):
        self.assertEqual(self.find("D:/tools/SideTap"), Path("D:/tools/SideTap"))

    def test_normal_install_is_preferred(self):
        normal = install(self.local / "SideTap")
        install(self.local / "Packages" / "OpenAI.Codex_x" / "LocalCache" / "Local" / "SideTap")
        self.assertEqual(self.find(), normal)

    def test_install_inside_packaged_app_storage_is_found(self):
        packaged = install(self.local / "Packages" / "OpenAI.Codex_x" / "LocalCache" / "Local" / "SideTap")
        self.assertEqual(self.find(), packaged)

    def test_missing_install_reports_the_normal_path(self):
        self.assertEqual(self.find(), self.local / "SideTap")


if __name__ == "__main__":
    unittest.main()
