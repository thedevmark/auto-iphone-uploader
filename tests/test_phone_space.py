import subprocess
import unittest
from unittest import mock

from video_drop import phone_space
from video_drop.phone import config, device
from video_drop.phone_space import ensure_room, parse_free_bytes, required_bytes

GB = 1_000_000_000


class PhoneSpaceTests(unittest.TestCase):
    def test_needs_twice_the_video_plus_a_margin(self):
        self.assertEqual(required_bytes(1_100_000_000), 3_200_000_000)

    def test_enough_room_passes(self):
        ensure_room(271_151_905, 4_700_000_000)

    def test_too_little_room_stops_with_amounts(self):
        with self.assertRaisesRegex(ValueError, r"needs 3\.2 GB free.*has 2\.7 GB"):
            ensure_room(1_100_000_000, 2_700_000_000)

    def test_unknown_free_space_stops(self):
        with self.assertRaisesRegex(ValueError, "could not read"):
            ensure_room(GB, None)

    def test_parses_go_ios_diskspace_output(self):
        output = ('{"level":"INFO","msg":"no udid specified"}\n'
                  '{"Model":"iPhone17,2","TotalBytes":255413800960,"FreeBytes":4712980480,"BlockSize":4096}\n')
        self.assertEqual(parse_free_bytes(output), 4712980480)
        self.assertIsNone(parse_free_bytes("not json"))

    def test_reads_space_through_the_pinned_go_ios_and_phone_with_no_window(self):
        """The space probe used shutil.which('ios') and no --udid: with GO_IOS_PATH set it could run a
        different ios.exe than every other phone command, and with two phones it read the wrong one."""
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, '{"FreeBytes": 4712980480}\n', "")

        with mock.patch.object(device, "ios_path", lambda: "C:/pinned/ios.exe"), \
                mock.patch.object(config, "SIDETAP_UDID", "00008140-ABCDEF"), \
                mock.patch.object(phone_space.subprocess, "run", run):
            self.assertEqual(phone_space.free_bytes(), 4712980480)
        self.assertEqual(calls[0][0], ["C:/pinned/ios.exe", "diskspace", "--udid=00008140-ABCDEF"])
        self.assertEqual(calls[0][1]["creationflags"], phone_space.NO_WINDOW)
        self.assertEqual(calls[0][1]["timeout"], 30)

    def test_a_broken_go_ios_pin_reads_as_unknown_space(self):
        def missing():
            raise device.DeviceError("GO_IOS_PATH points nowhere")

        with mock.patch.object(device, "ios_path", missing):
            self.assertIsNone(phone_space.free_bytes())
        with mock.patch.object(device, "ios_path", lambda: None):
            self.assertIsNone(phone_space.free_bytes())


if __name__ == "__main__":
    unittest.main()
