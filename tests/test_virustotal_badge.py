import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".github" / "scripts"))
from update_virustotal_badge import README, badge, update, verdict  # noqa: E402

SHA = "c4ae924bcd67620b0248bd3a628f42fc9e7e28496058e6066dfd3640743b8f6b"


class VirusTotalBadgeTests(unittest.TestCase):
    def test_readme_badge_line_is_replaceable(self):
        text = README.read_text(encoding="utf-8")
        line = badge("v9.9.9", SHA, 0, 65)
        updated = update(text, line)
        self.assertIn(line + "\n", updated)
        self.assertEqual(len(updated.splitlines()), len(text.splitlines()))

    def test_flagged_release_turns_red_and_links_its_report(self):
        line = badge("v0.2.0-rc.1", "a" * 64, 2, 70)
        self.assertIn("VirusTotal%20v0.2.0--rc.1-2%2F70%20flagged-red", line)
        self.assertTrue(line.endswith(f"(https://www.virustotal.com/gui/file/{'a' * 64})"))

    def test_refuses_incomplete_scans(self):
        for args in (("v1", SHA, 0, 0), ("v1", "nothex", 0, 65), ("v1", SHA, 66, 65)):
            with self.assertRaises(ValueError):
                badge(*args)

    def test_verdict_counts_flagging_and_scanning_engines(self):
        report = ('{"data":{"attributes":{"last_analysis_stats":{"malicious":1,"suspicious":1,'
                  '"undetected":60,"harmless":3,"timeout":2,"type-unsupported":9}}}}')
        self.assertEqual(verdict(report), "2 65")
        for missing in ("", "not json", '{"error":{"code":"NotFoundError"}}'):
            self.assertEqual(verdict(missing), "")

    def test_requires_exactly_one_badge_line(self):
        with self.assertRaises(ValueError):
            update("# no badge\n", badge("v1", SHA, 0, 65))


if __name__ == "__main__":
    unittest.main()
