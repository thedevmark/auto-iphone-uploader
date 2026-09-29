import os
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from video_drop import server
from video_drop.server import local_video_files, local_video_path


class LocalVideoFilesTests(unittest.TestCase):
    def test_lists_finished_video_candidates_without_copying_them(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clip = folder / "finished.mp4"
            clip.write_bytes(b"video")
            (folder / "partial.mp4.part").write_bytes(b"partial")
            with patch.dict(os.environ, {"VIDEO_DROP_SOURCE_DIR": directory}):
                result = local_video_files()
                self.assertEqual([item["name"] for item in result["files"]], ["finished.mp4"])
                self.assertEqual(local_video_path(str(clip)), clip.resolve())
            self.assertTrue(clip.exists())

    def test_rejects_unlisted_location(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "outside.mp4"
            outside.write_bytes(b"video")
            with patch.dict(os.environ, {"VIDEO_DROP_SOURCE_DIR": source}):
                with self.assertRaisesRegex(ValueError, "video folder"):
                    local_video_path(str(outside))

    def test_browser_list_imports_exact_selected_path_without_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "videos"
            folder.mkdir()
            clip = folder / "next.mp4"
            clip.write_bytes(b"video")
            outside = Path(directory) / "elsewhere.mp4"
            outside.write_bytes(b"another video")
            state = Path(directory) / "state"
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {"VIDEO_DROP_SOURCE_DIR": str(folder)}), patch.object(server, "STATE", state), patch.object(server, "complete_video", return_value=True), patch.object(server, "queue_analysis"):
                    base = f"http://127.0.0.1:{http.server_port}"
                    with urlopen(base + "/api/source-files") as response:
                        listed = json.load(response)
                    self.assertTrue(Path(listed["files"][0]["path"]).samefile(clip))
                    request = Request(base + "/api/source-files/import", data=json.dumps({"path": str(clip)}).encode(), method="POST", headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        imported = json.load(response)["release"]
                    self.assertTrue(Path(imported["source_path"]).samefile(clip))
                    self.assertEqual(clip.read_bytes(), b"video")
                    request = Request(base + "/api/import-path", data=json.dumps({"path": str(outside)}).encode(), method="POST", headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        imported = json.load(response)["release"]
                    self.assertTrue(Path(imported["source_path"]).samefile(outside))
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
