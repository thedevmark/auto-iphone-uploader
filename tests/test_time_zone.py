import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from video_drop import server, timezones
from video_drop.core import Store
from video_drop.timezones import WINDOWS_ZONES, pc_time_zone, pc_zone_name

# 5 AM in Los Angeles, 8 AM in New York, 2 PM in Berlin.
MORNING = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def reviewed(store: Store, folder: Path, name: str) -> int:
    source = folder / name
    source.write_bytes(name.encode())
    release_id = store.import_file(source)["id"]
    store.save_text(release_id, "youtube", "@channel", "Title", "Description", "tag")
    store.authorize(release_id, "youtube")
    return release_id


class PcZoneTests(unittest.TestCase):
    def test_every_windows_zone_maps_to_a_real_iana_zone(self):
        for windows, iana in WINDOWS_ZONES.items():
            with self.subTest(windows=windows):
                ZoneInfo(iana)
        self.assertEqual(WINDOWS_ZONES["Eastern Standard Time"], "America/New_York")
        self.assertEqual(WINDOWS_ZONES["W. Europe Standard Time"], "Europe/Berlin")

    @unittest.skipUnless(os.name == "nt", "reads the Windows registry name")
    def test_windows_registry_name_becomes_the_iana_zone(self):
        with patch.object(timezones, "windows_zone_key", return_value="Pacific Standard Time"):
            self.assertEqual(pc_zone_name(), "America/Los_Angeles")
        with patch.object(timezones, "windows_zone_key", return_value="Nowhere Standard Time"):
            self.assertIsNone(pc_zone_name())

    def test_unnamed_zone_falls_back_to_the_current_offset(self):
        with patch.object(timezones, "pc_zone_name", return_value=None):
            zone = pc_time_zone()
        self.assertEqual(zone.utcoffset(datetime.now()), datetime.now().astimezone().utcoffset())

    def test_posix_tz_variable_is_used_when_it_names_a_zone(self):
        self.assertEqual(timezones.posix_zone_name({"TZ": ":Asia/Tokyo"}), "Asia/Tokyo")


class StoreZoneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def local(self, release: dict, zone: str) -> str:
        return datetime.fromisoformat(release["scheduled_at"]).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")

    def test_slots_follow_this_pcs_time_zone_by_default(self):
        release_id = reviewed(self.store, self.root, "a.mp4")
        with patch.object(timezones, "pc_zone_name", return_value="America/Los_Angeles"):
            self.assertEqual(self.store.time_zone(), ZoneInfo("America/Los_Angeles"))
            planned = self.store.reserve_slot(release_id, MORNING)
        self.assertEqual(self.local(planned, "America/Los_Angeles"), "2026-09-28 10:00")
        self.assertEqual(planned["scheduled_at"], "2026-09-28T17:00:00+00:00")

    def test_setting_overrides_the_pc_zone_and_clears_back_to_it(self):
        self.assertEqual(self.store.time_zone_setting(), "")
        self.assertEqual(self.store.set_time_zone("Europe/Berlin"), "Europe/Berlin")
        with Store(self.root / "state.sqlite") as reopened:
            self.assertEqual(reopened.time_zone(), ZoneInfo("Europe/Berlin"))
        release_id = reviewed(self.store, self.root, "a.mp4")
        planned = self.store.reserve_slot(release_id, MORNING)
        # 10:00 in Berlin had passed; 19:00 Berlin is the next free time.
        self.assertEqual(self.local(planned, "Europe/Berlin"), "2026-09-28 19:00")
        for bad in ("Mars/Olympus", "../etc", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.set_time_zone(bad)
        self.assertEqual(self.store.set_time_zone(""), "")
        self.assertEqual(self.store.time_zone(), ZoneInfo("America/New_York"))

    def test_changing_the_zone_keeps_held_times_as_the_same_moment(self):
        release_id = reviewed(self.store, self.root, "a.mp4")
        held = self.store.reserve_slot(release_id, MORNING)["scheduled_at"]
        self.store.set_time_zone("Asia/Tokyo")
        self.assertEqual(self.store.release(release_id)["scheduled_at"], held)
        again = self.store.reserve_slot(release_id, MORNING + timedelta(minutes=5))
        self.assertEqual(again["scheduled_at"], held)

    def test_a_chosen_time_is_read_in_the_configured_zone(self):
        self.store.set_time_zone("Europe/Berlin")
        release_id = reviewed(self.store, self.root, "a.mp4")
        with self.assertRaisesRegex(ValueError, "not one of the posting times"):
            self.store.reserve_slot(release_id, MORNING, at=datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("America/New_York")))
        planned = self.store.reserve_slot(release_id, MORNING, at=datetime(2026, 9, 29, 10, tzinfo=ZoneInfo("Europe/Berlin")))
        self.assertEqual(planned["scheduled_at"], "2026-09-29T08:00:00+00:00")

    def test_stored_utc_slots_from_before_the_change_stay_valid(self):
        release_id = reviewed(self.store, self.root, "a.mp4")
        with self.store.db:
            self.store.db.execute("UPDATE release SET status='reserved', scheduled_at=? WHERE id=?",
                                  ("2026-09-29T14:00:00+00:00", release_id))
        self.store.set_time_zone("America/Los_Angeles")
        other = reviewed(self.store, self.root, "b.mp4")
        planned = self.store.reserve_slot(other, MORNING)
        self.assertEqual(self.store.release(release_id)["scheduled_at"], "2026-09-29T14:00:00+00:00")
        self.assertEqual(self.local(planned, "America/Los_Angeles"), "2026-09-28 10:00")


class SettingsZoneRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        self.patches = [patch.object(server, "STATE", self.state),
                        patch.object(server, "WATCHER", server.WatchFolder(self.state, lambda release_id: None)),
                        patch("video_drop.server.utc_now", return_value=MORNING)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def get_settings(self) -> dict:
        with urlopen(self.base + "/api/settings", timeout=5) as response:
            return json.load(response)

    def post_zone(self, value) -> dict:
        request = Request(self.base + "/api/settings/time-zone", data=json.dumps({"timeZone": value}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_settings_report_the_pc_zone_and_accept_an_override(self):
        settings = self.get_settings()
        self.assertEqual((settings["timeZone"], settings["pcTimeZone"], settings["timeZoneSetting"]),
                         ("America/New_York", "America/New_York", ""))
        self.assertEqual(len(settings["nextSlots"]), 10)
        self.assertEqual(settings["nextSlots"][0], "2026-09-28T10:00:00-04:00")
        self.assertEqual(self.post_zone("Asia/Tokyo"), {"timeZoneSetting": "Asia/Tokyo", "timeZone": "Asia/Tokyo"})
        settings = self.get_settings()
        self.assertEqual(settings["timeZone"], "Asia/Tokyo")
        self.assertEqual(settings["nextSlots"][0], "2026-09-29T10:00:00+09:00")
        with self.assertRaises(HTTPError) as rejected:
            self.post_zone("Nowhere/Land")
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()
        self.assertEqual(self.post_zone("")["timeZone"], "America/New_York")


if __name__ == "__main__":
    unittest.main()
