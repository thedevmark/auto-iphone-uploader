import unittest

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


if __name__ == "__main__":
    unittest.main()
