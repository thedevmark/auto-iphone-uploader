"""The Windows installer's contract: pinned downloads, no secrets in its output, plain progress."""

import re
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parent.parent / "scripts" / "install_windows.ps1"
README = Path(__file__).resolve().parent.parent / "README.md"
SETUP_DOC = Path(__file__).resolve().parent.parent / "docs" / "setup-windows.md"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.text = INSTALLER.read_text(encoding="utf-8")

    def test_pins_and_hash_checks_stay(self):
        self.assertIn("$GoIosVersion = '1.3.2'", self.text)
        self.assertIn("$GoIosSha256  = '939C6BCAAFED183A92AFB9F79CC11B1F935FA6389BFC94D3902E3F52C4DFF3FE'", self.text)
        self.assertIn("$WdaVersion   = '16.12.9'", self.text)
        self.assertIn("$WdaSha256    = '8A48EC564DA204EFA60CC4769395C95D19F58489BDE7E612BC3C869AE904F9D9'", self.text)
        # Every download goes through the verifying helper; a bad file is deleted, never kept.
        self.assertEqual(self.text.count("Get-Download '"), 2)
        self.assertIn("Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue", self.text)
        self.assertIn("nothing was installed", self.text)

    def test_runs_without_questions_in_ci_and_with_no_prompt(self):
        for switch in ("[switch]$CheckOnly", "[switch]$NoPrompt", "[switch]$NoLaunch", "[switch]$NoChecklist"):
            self.assertIn(switch, self.text)
        self.assertIn("$interactive = -not $NoPrompt -and -not $CheckOnly -and [Environment]::UserInteractive", self.text)
        # Every Read-Host sits behind the interactive gate (the Ask helper), never on the plain path.
        self.assertEqual(self.text.count("Read-Host"), 1)
        self.assertIn("if ($interactive) {", self.text)

    def test_passcode_is_offered_through_the_hidden_prompt_and_never_printed(self):
        self.assertIn("scripts\\set_passcode.py", self.text)
        self.assertIn("Values are never returned or printed by this script", self.text)
        self.assertNotIn("$keys['PHONE_PASSCODE']\"", self.text)
        self.assertIn("iPhone passcode saved (never shown)", self.text)
        for line in self.text.splitlines():
            if "Write-Host" in line or "Say " in line:
                self.assertNotRegex(line, r"PHONE_PASSCODE\s*=", line)

    def test_progress_is_numbered_and_every_failure_names_the_fix(self):
        self.assertEqual(sorted(set(re.findall(r"^Step (\d) '", self.text, re.MULTILINE))), list("1234567"))
        self.assertIn("$StepCount   = 7", self.text)
        for throw in re.findall(r"throw ['\"](.+?)['\"]\s*$", self.text, re.MULTILINE):
            self.assertRegex(throw, r"(run this command again|then try again|Pass -|On another system, use)", throw)

    def test_user_facing_lines_avoid_jargon(self):
        shown = "\n".join(line for line in self.text.splitlines()
                          if re.search(r"\b(Say|Ok|Todo|Note|Step|Ask|throw)\b", line) and not line.strip().startswith("#"))
        for jargon in ("usbmuxd", "tunnel", "WDA ", "xctrunner"):
            self.assertNotIn(jargon, shown, jargon)

    def test_opens_the_app_at_the_end_unless_told_not_to(self):
        self.assertIn("if (-not $CheckOnly -and -not $NoLaunch) {", self.text)
        self.assertIn("Start-Process -FilePath $launcher -ArgumentList $launchScript", self.text)


class InstallDocsTests(unittest.TestCase):
    COMMAND = "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\install_windows.ps1"

    def test_readme_and_setup_guide_give_the_one_command(self):
        for path in (README, SETUP_DOC):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(self.COMMAND, text)
                self.assertIn("Sideloadly", text)
                self.assertIn("phone_resign.py", text)


if __name__ == "__main__":
    unittest.main()
