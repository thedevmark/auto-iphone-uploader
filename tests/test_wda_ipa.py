import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from video_drop import wda_ipa


def runner_zip(path: Path, *, names=("Info.plist", "WebDriverAgentRunner-Runner", "PlugIns/WebDriverAgentRunner.xctest/Info.plist")) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("__MACOSX/._junk", b"resource fork")
        z.writestr(wda_ipa.APP_DIR + "PlugIns/WebDriverAgentRunner.xctest.dSYM/Contents/Info.plist", b"debug symbols")
        for name in names:
            info = zipfile.ZipInfo(wda_ipa.APP_DIR + name, date_time=(2026, 9, 19, 15, 0, 0))
            info.external_attr = (0o755 if name.endswith("-Runner") else 0o644) << 16
            z.writestr(info, name.encode())


class RepackTests(unittest.TestCase):
    def test_release_zip_becomes_an_ipa_under_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            src, dst = Path(folder) / "runner.zip", Path(folder) / "WebDriverAgent.ipa"
            runner_zip(src)
            self.assertEqual(wda_ipa.repack(src, dst), 3)
            with zipfile.ZipFile(dst) as z:
                names = z.namelist()
                self.assertEqual(names, ["Payload/WebDriverAgentRunner-Runner.app/Info.plist",
                                         "Payload/WebDriverAgentRunner-Runner.app/WebDriverAgentRunner-Runner",
                                         "Payload/WebDriverAgentRunner-Runner.app/PlugIns/WebDriverAgentRunner.xctest/Info.plist"])
                self.assertTrue(all("\\" not in name for name in names))
                binary = z.getinfo("Payload/WebDriverAgentRunner-Runner.app/WebDriverAgentRunner-Runner")
                self.assertEqual(binary.external_attr >> 16, 0o755)
                self.assertEqual(z.read(binary), b"WebDriverAgentRunner-Runner")
            self.assertEqual(wda_ipa.main([str(src), str(dst)]), 0)

    def test_refuses_the_wrong_zip(self):
        with tempfile.TemporaryDirectory() as folder:
            wrong = Path(folder) / "other.zip"
            with zipfile.ZipFile(wrong, "w") as z:
                z.writestr("Payload/" + wda_ipa.APP_DIR + "Info.plist", b"")
                z.writestr(wda_ipa.APP_DIR + "Info.plist", b"")
            with self.assertRaisesRegex(ValueError, "already an .ipa"):
                wda_ipa.repack(wrong, Path(folder) / "out.ipa")
            with zipfile.ZipFile(wrong, "w") as z:
                z.writestr("README.md", b"")
            with self.assertRaisesRegex(ValueError, "does not contain"):
                wda_ipa.repack(wrong, Path(folder) / "out.ipa")
            self.assertEqual(wda_ipa.main([str(wrong), str(Path(folder) / "out.ipa")]), 1)
            self.assertEqual(wda_ipa.main([]), 2)


if __name__ == "__main__":
    unittest.main()
