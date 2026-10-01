import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from video_drop import secret_store

WINDOWS = sys.platform == "win32"


@unittest.skipUnless(WINDOWS, "DPAPI is Windows-only")
class DpapiTests(unittest.TestCase):
    def test_round_trip_and_no_plain_text(self):
        token = secret_store.protect("246810")
        self.assertNotIn("246810", token)
        self.assertEqual(secret_store.unprotect(token), "246810")

    def test_a_damaged_token_reads_as_no_passcode(self):
        self.assertIsNone(secret_store.passcode({secret_store.KEY: "not base64 !!"}))
        self.assertIsNone(secret_store.passcode({secret_store.KEY: "QUJD"}))  # base64, not a DPAPI blob
        self.assertIsNone(secret_store.passcode({}))

    def test_set_passcode_writes_only_the_encrypted_form(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts import set_passcode
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("GO_IOS_PATH=C:/tools/ios.exe\nPHONE_PASSCODE=246810\n", encoding="utf-8")
            set_passcode.save("135790", env)
            text = env.read_text(encoding="utf-8")
            self.assertNotIn("246810", text)
            self.assertNotIn("135790", text)
            self.assertNotIn("PHONE_PASSCODE=", text)
            self.assertIn("GO_IOS_PATH=C:/tools/ios.exe", text)
            values = dict(line.split("=", 1) for line in text.splitlines())
            self.assertEqual(secret_store.unprotect(values[secret_store.KEY]), "135790")
            set_passcode.save("112233", env)  # saving again replaces, never duplicates
            self.assertEqual(env.read_text(encoding="utf-8").count(secret_store.KEY), 1)

    def test_config_decrypts_the_saved_passcode(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text(f"{secret_store.KEY}={secret_store.protect('246810')}\n", encoding="utf-8")
            with patch.dict(os.environ, {"VIDEO_DROP_ENV_FILE": str(env), "VIDEO_DROP_NO_LEGACY_ENV": "1"}):
                os.environ.pop("PHONE_PASSCODE", None)
                from video_drop.phone import config
                try:
                    self.assertEqual(importlib.reload(config).PHONE_PASSCODE, "246810")
                finally:
                    pass
            importlib.reload(config)


class NonWindowsTests(unittest.TestCase):
    @unittest.skipIf(WINDOWS, "only meaningful off Windows")
    def test_off_windows_it_says_so(self):
        with self.assertRaisesRegex(secret_store.SecretStoreError, "Windows"):
            secret_store.protect("1234")


if __name__ == "__main__":
    unittest.main()
