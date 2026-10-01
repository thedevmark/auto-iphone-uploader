import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "update_virustotal_badge.py"
sys.path.insert(0, str(SCRIPT.parent))
from update_virustotal_badge import README_BADGE, endpoint, report_page, verdict  # noqa: E402

SHA = "c4ae924bcd67620b0248bd3a628f42fc9e7e28496058e6066dfd3640743b8f6b"


class VirusTotalBadgeTests(unittest.TestCase):
    def test_readme_shows_the_endpoint_badge_once(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(README_BADGE), 1)
        self.assertNotIn("img.shields.io/badge/VirusTotal", text)

    def test_clean_and_flagged_releases(self):
        self.assertEqual(endpoint("v1.0.0-rc.4", SHA, 0, 66),
                         {"schemaVersion": 1, "label": "VirusTotal v1.0.0-rc.4", "message": "0/66 flagged",
                          "color": "brightgreen"})
        self.assertEqual(endpoint("v1", "a" * 64, 2, 70)["color"], "red")
        self.assertIn(f"https://www.virustotal.com/gui/file/{SHA}", report_page("v1", SHA, 0, 66))

    def test_refuses_incomplete_scans(self):
        for args in (("v1", SHA, 0, 0), ("v1", "nothex", 0, 65), ("v1", SHA, 66, 65)):
            with self.assertRaises(ValueError):
                endpoint(*args)

    def test_verdict_counts_flagging_and_scanning_engines(self):
        report = ('{"data":{"attributes":{"last_analysis_stats":{"malicious":1,"suspicious":1,'
                  '"undetected":60,"harmless":3,"timeout":2,"type-unsupported":9}}}}')
        self.assertEqual(verdict(report), "2 65")
        for missing in ("", "not json", '{"error":{"code":"NotFoundError"}}'):
            self.assertEqual(verdict(missing), "")

    def test_writes_both_files_for_the_badges_branch(self):
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable, str(SCRIPT), "v1.0.0", SHA, "0", "66", folder], check=True)
            self.assertEqual(json.loads((Path(folder) / "virustotal.json").read_text())["message"], "0/66 flagged")
            self.assertIn(SHA, (Path(folder) / "virustotal.md").read_text())


if __name__ == "__main__":
    unittest.main()
