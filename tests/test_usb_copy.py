"""The USB copy into the phone driver's shared folder, with a fake go-ios (no phone)."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import phone_youtube as share
from video_drop import usb_copy
from video_drop.source_route import FilesPath


class FakeIos:
    """Answers fsync tree/rm/push like go-ios 1.3.2 over a dict of file names."""

    def __init__(self, files=(), fail_push=False):
        self.files, self.fail_push, self.calls = set(files), fail_push, []

    def __call__(self, args, timeout):
        verb = args[3]
        self.calls.append((verb, timeout))
        out = ""
        if verb == "tree":
            out = "\n".join(["|-Inbox/", *[f"|-{name}" for name in sorted(self.files)]])
        elif verb == "rm":
            self.files.discard(args[4].split("/", 1)[1])
        elif verb == "push":
            if self.fail_push:
                out = json.dumps({"level": "ERROR", "msg": "push failed", "err": "afc error code: 7"})
            else:
                self.files.add(args[5].split("=", 1)[1].split("/", 1)[1])
        return subprocess.CompletedProcess(args, 0, out, "")


class UsbCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.record = Path(self.temp.name) / "usb-copy.json"
        self.source = Path(self.temp.name) / "day 12.mp4"
        self.source.write_bytes(b"video")

    def tearDown(self):
        self.temp.cleanup()

    def copier(self, ios):
        return usb_copy.UsbCopy("ios", "com.example.xctrunner", self.record, run=ios)

    def test_copies_once_keeps_only_this_video_and_names_the_files_route(self):
        ios = FakeIos(files={"day 11.mp4"})
        target = self.copier(ios).put(self.source, 5, "abc")
        self.assertEqual(target, FilesPath("On My iPhone", ("On My iPhone",), ("Auto iPhone Uploader",), "day 12.mp4"))
        self.assertEqual(ios.files, {"day 12.mp4"})  # the older clip is gone
        self.assertEqual([verb for verb, _ in ios.calls], ["tree", "rm", "push", "tree"])
        # The same file again: listed and recorded, so nothing is copied.
        ios.calls.clear()
        self.copier(ios).put(self.source, 5, "abc")
        self.assertEqual([verb for verb, _ in ios.calls], ["tree"])
        # A changed file under the same name is copied again.
        ios.calls.clear()
        self.copier(ios).put(self.source, 5, "def")
        self.assertIn("push", [verb for verb, _ in ios.calls])

    def test_a_failed_push_raises_and_leaves_no_record(self):
        with self.assertRaisesRegex(usb_copy.UsbCopyError, "afc error code: 7"):
            self.copier(FakeIos(fail_push=True)).put(self.source, 5, "abc")
        self.assertFalse(self.record.exists())

    def test_a_large_file_gets_a_longer_bound(self):
        self.assertEqual(usb_copy.copy_timeout(900_000_000), 150.0)
        self.assertEqual(usb_copy.copy_timeout(50_000_000_000), 1200.0)

    def test_reads_file_sharing_from_the_installed_driver(self):
        apps = [{"CFBundleIdentifier": "com.example.xctrunner", "UIFileSharingEnabled": True},
                {"CFBundleIdentifier": "com.other", "UIFileSharingEnabled": True}]
        answer = lambda args, timeout: subprocess.CompletedProcess(args, 0, json.dumps(apps), "")
        self.assertTrue(usb_copy.shares_files("ios", "com.example.xctrunner", run=answer))
        apps[0].pop("UIFileSharingEnabled")
        self.assertFalse(usb_copy.shares_files("ios", "com.example.xctrunner", run=answer))


class FlowRouteTests(unittest.TestCase):
    """open_source_file: USB first, the cloud route when the phone cannot take a copy."""

    def run_open(self, usb_result):
        opened = []
        files_app = mock.Mock()
        files_app.return_value.open_file.side_effect = lambda target, size: opened.append(target)
        cloud = FilesPath("OneDrive", ("OneDrive",), ("Files", "_Videos"), "day 12.mp4")
        with mock.patch.object(share, "usb_files_path", return_value=usb_result), \
                mock.patch.object(share.source_route, "release_route", return_value=mock.Mock(files=cloud)) as route, \
                mock.patch.object(share, "FilesApp", files_app), mock.patch.object(share, "stage"), \
                mock.patch.object(share, "phone"):
            share.open_source_file({"releaseId": 1, "filename": "day 12.mp4", "sizeBytes": 5},
                                   db=Path("state.sqlite"), clouds=[])
        return opened, route

    def test_the_usb_copy_is_opened_without_asking_the_cloud(self):
        usb = usb_copy.files_path("day 12.mp4")
        opened, route = self.run_open(usb)
        self.assertEqual(opened, [usb])
        route.assert_not_called()

    def test_no_usb_copy_falls_back_to_the_cloud_folder(self):
        opened, route = self.run_open(None)
        self.assertEqual(opened[0].provider, "OneDrive")
        route.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class StorageAndCleanupTests(unittest.TestCase):
    def test_a_video_already_on_the_phone_needs_only_the_export_room(self):
        from video_drop import phone_space
        gb = phone_space.GB
        self.assertIsNone(phone_space.room_problem(gb, int(2.5 * gb), on_phone=True))
        self.assertIn("needs 3.0 GB", phone_space.room_problem(gb, int(2.5 * gb)))
        self.assertIn("could not read", phone_space.room_problem(gb, None))
        for message in ("afc error code: 18 (NoSpaceLeft)", "Not enough storage"):
            self.assertTrue(phone_space.says_full(message))
        self.assertFalse(phone_space.says_full("afc error code: 8 (ObjectNotFound)"))

    def test_a_full_phone_stops_the_flow_instead_of_falling_back_to_the_cloud(self):
        full = usb_copy.PhoneFull("The iPhone needs 2.8 GB free. Nothing was posted")
        with mock.patch.object(share.source_route, "release_source", return_value=(Path("day 12.mp4"), "day 12.mp4", 5)), \
                mock.patch.object(share.source_route, "release_sha256", return_value="abc"), \
                mock.patch.object(share.usb_copy, "put_release_video", side_effect=full), mock.patch.object(share, "stage"):
            with self.assertRaisesRegex(share.PhoneUploadError, "needs 2.8 GB"):
                share.usb_files_path({"releaseId": 1, "filename": "day 12.mp4", "sizeBytes": 5}, Path("s.sqlite"))
        refused = usb_copy.UsbCopyError("go-ios fsync push failed: afc error code: 18 (NoSpaceLeft)")
        with mock.patch.object(share.source_route, "release_source", return_value=(Path("day 12.mp4"), "day 12.mp4", 5)), \
                mock.patch.object(share.source_route, "release_sha256", return_value="abc"), \
                mock.patch.object(share.usb_copy, "put_release_video", side_effect=refused), mock.patch.object(share, "stage"):
            with self.assertRaisesRegex(share.PhoneUploadError, "storage is full"):
                share.usb_files_path({"releaseId": 1, "filename": "day 12.mp4", "sizeBytes": 5}, Path("s.sqlite"))

    def test_the_copy_is_deleted_only_when_every_targeted_platform_is_done(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "usb-copy.json").write_text(json.dumps({"bundle": "b", "name": "day 12.mp4", "size": 5, "sha256": "abc"}))
            ios = FakeIos(files={"day 12.mp4"})
            release = {"destinations": [{"platform": "youtube", "revision_hash": "h", "status": "posted"},
                                        {"platform": "tiktok", "revision_hash": "h", "status": "pending"},
                                        {"platform": "threads", "revision_hash": "", "status": "pending"}]}
            with mock.patch.object(usb_copy, "driver", return_value=("ios", "b")), \
\
                    mock.patch("video_drop.phone.device.current_udid", return_value=None):
                self.assertIsNone(usb_copy.remove_when_done(state, lambda sha: release, run=ios))
                self.assertEqual(ios.files, {"day 12.mp4"})
                release["destinations"][1]["status"] = "posted"  # untargeted Threads does not hold it back
                self.assertEqual(usb_copy.remove_when_done(state, lambda sha: release, run=ios), "day 12.mp4")
            self.assertEqual(ios.files, set())
            self.assertFalse((state / "usb-copy.json").exists())
