import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import launch_video_drop as launcher
from video_drop import server
from video_drop.runtime_identity import source_fingerprint


class RuntimeIdentityTests(unittest.TestCase):
    def test_fingerprint_changes_with_loaded_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "video_drop").mkdir()
            (root / "scripts").mkdir()
            (root / "web").mkdir()
            (root / "video_drop" / "core.py").write_text("one")
            (root / "web" / "index.html").write_text("page")
            (root / "requirements.txt").write_text("")
            before = source_fingerprint(root)
            (root / "video_drop" / "core.py").write_text("two")
            self.assertNotEqual(before, source_fingerprint(root))
            (root / "requirements.txt").unlink()
            self.assertEqual(len(source_fingerprint(root)), 64)

    def test_launcher_accepts_only_the_same_running_checkout_and_source(self):
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(launcher, "URL", f"http://127.0.0.1:{http.server_port}/"):
                identity = launcher.running_server()
                self.assertEqual(identity["sourceFingerprint"], source_fingerprint(launcher.ROOT))
                launcher.assert_current_server(identity)
                with self.assertRaisesRegex(RuntimeError, "older code"):
                    launcher.assert_current_server({**identity, "sourceFingerprint": "old"})
                with self.assertRaisesRegex(RuntimeError, "another app checkout"):
                    launcher.assert_current_server({**identity, "root": "C:/elsewhere"})
        finally:
            http.shutdown()
            http.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
