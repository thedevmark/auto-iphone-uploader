import tempfile
import unittest
import shutil
import subprocess
import os
import threading
from pathlib import Path
from unittest.mock import patch

from video_drop.core import Store
from video_drop.watch import WatchFolder, complete_video, readable_without_writer


class WatchFolderTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows file sharing check")
    def test_open_export_handle_blocks_ingest(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.mp4"
            clip.write_bytes(b"still open")
            with clip.open("rb"):
                self.assertFalse(readable_without_writer(clip))
            self.assertTrue(readable_without_writer(clip))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg tools unavailable")
    def test_full_media_validation_rejects_truncated_export(self):
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                            "-c:v", "mpeg4", str(clip)], check=True, capture_output=True)
            self.assertTrue(complete_video(clip))
            clip.write_bytes(clip.read_bytes()[:128])
            self.assertFalse(complete_video(clip))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg tools unavailable")
    def test_background_watch_imports_a_finished_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / "exports"
            exports.mkdir()
            added = threading.Event()
            watcher = WatchFolder(root / "state", lambda _id: added.set())
            watcher.configure(folder=exports, enabled=True)
            stop = threading.Event()
            with patch("video_drop.watch.SETTLE_SECONDS", 0.1), patch("video_drop.watch.POLL_SECONDS", 0.05):
                thread = threading.Thread(target=watcher.run, args=(stop,), daemon=True)
                thread.start()
                try:
                    clip = exports / "finished.mp4"
                    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                                    "-c:v", "mpeg4", str(clip)], check=True, capture_output=True)
                    self.assertTrue(added.wait(5), "finished export was not imported")
                    with Store(root / "state" / "video-drop.sqlite") as store:
                        self.assertEqual(store.current()["source_path"], str(clip.resolve()))
                finally:
                    stop.set()
                    thread.join(timeout=2)

    def test_skips_existing_and_waits_for_complete_new_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / "exports"
            exports.mkdir()
            old = exports / "old.mp4"
            old.write_bytes(b"existing")
            ingested = []
            watcher = WatchFolder(root / "state", ingested.append)
            watcher.configure(folder=exports, enabled=True)
            self.assertEqual(watcher.scan(now=0), [])

            first = exports / "first.mp4"
            second = exports / "second.mov"
            temporary = exports / "third.mp4.tmp"
            first.write_bytes(b"partial")
            second.write_bytes(b"second")
            temporary.write_bytes(b"temporary")
            with patch("video_drop.watch.complete_video", return_value=True) as complete:
                self.assertEqual(watcher.scan(now=1), [])
                first.write_bytes(b"finished first")
                self.assertEqual(watcher.scan(now=20), [])
                self.assertEqual(watcher.scan(now=35), [1])  # second settled; first was modified
                self.assertEqual(watcher.scan(now=51), [2])
                self.assertEqual(complete.call_count, 2)
            with Store(root / "state" / "video-drop.sqlite") as store:
                self.assertEqual([r["source_name"] for r in store.drafts()], ["second.mov", "first.mp4"])
            self.assertEqual(ingested, [1, 2])
            self.assertEqual(old.read_bytes(), b"existing")
            self.assertEqual(first.read_bytes(), b"finished first")

    def test_premiere_intermediate_is_never_imported_even_if_decodable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / "exports"
            exports.mkdir()
            watcher = WatchFolder(root / "state", lambda _id: None)
            watcher.configure(folder=exports, enabled=True)
            intermediate = exports / "day5clip.19052.32088.m4v"
            intermediate.write_bytes(b"video stream without final mux")
            (exports / "day5clip.19052.32088.m4v.md0").write_bytes(b"sidecar")
            (exports / "day5clip.19052.32088.aac").write_bytes(b"audio stream")
            with patch("video_drop.watch.complete_video", return_value=True) as complete:
                self.assertEqual(watcher.scan(now=0), [])
                self.assertEqual(watcher.scan(now=31), [])
                self.assertEqual(complete.call_count, 0)
                finished = exports / "day5clip.mp4"
                finished.write_bytes(b"final mux")
                self.assertEqual(watcher.scan(now=32), [])
                self.assertEqual(watcher.scan(now=63), [1])
                self.assertEqual(complete.call_count, 1)
            with Store(root / "state" / "video-drop.sqlite") as store:
                self.assertEqual(store.current()["source_name"], "day5clip.mp4")

    def test_retries_invalid_file_and_stops_when_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / "exports"
            exports.mkdir()
            watcher = WatchFolder(root / "state", lambda _id: None)
            watcher.configure(folder=exports, enabled=True)
            clip = exports / "unfinished.mp4"
            clip.write_bytes(b"unfinished")
            with patch("video_drop.watch.complete_video", side_effect=[False, True]) as complete:
                watcher.scan(now=0)
                self.assertEqual(watcher.scan(now=31), [])
                self.assertEqual(watcher.scan(now=40), [])
                self.assertEqual(watcher.scan(now=62), [1])
                self.assertEqual(complete.call_count, 2)
            watcher.configure(enabled=False)
            later = exports / "later.mp4"
            later.write_bytes(b"later")
            self.assertEqual(watcher.scan(now=100), [])

    def test_restart_keeps_watch_folder_and_accepts_export_created_while_app_was_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exports = root / "exports"
            exports.mkdir()
            WatchFolder(root / "state", lambda _id: None).configure(folder=exports, enabled=True)
            clip = exports / "after-restart.mp4"
            clip.write_bytes(b"finished")
            ingested = []
            restarted = WatchFolder(root / "state", ingested.append)
            with patch("video_drop.watch.complete_video", return_value=True):
                self.assertEqual(restarted.scan(now=0), [])
                self.assertEqual(restarted.scan(now=31), [1])
            self.assertEqual(ingested, [1])


if __name__ == "__main__":
    unittest.main()
