import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from video_drop import server
from video_drop.core import Store

PLATFORMS = ("youtube", "instagram", "facebook", "threads", "tiktok")


def scheduled_release(store: Store, folder: Path, name: str, slot: str, platforms=PLATFORMS) -> int:
    source = folder / name
    source.write_bytes(name.encode())
    release_id = store.import_file(source)["id"]
    store.db.execute("UPDATE release SET status='needs_check', scheduled_at=? WHERE id=?", (slot, release_id))
    for platform in platforms:
        store.db.execute("UPDATE destination SET revision_hash='r', status='unconfirmed' WHERE release_id=? AND platform=?",
                         (release_id, platform))
    store.db.commit()
    return release_id


def app_verified(store: Store, release_id: int, platform: str, status: str = "scheduled") -> None:
    """What a native read-back leaves behind: the destination status plus its app receipt event."""
    with store.db:
        store.db.execute("UPDATE destination SET status=? WHERE release_id=? AND platform=?", (status, release_id, platform))
        store._event(release_id, platform, "native_schedule_observed", {"verification": "test"})


def statuses(store: Store, release_id: int) -> dict:
    release = store.release(release_id)
    return {"release": release["status"], **{d["platform"]: d["status"] for d in release["destinations"]}}


class ManualReceiptTests(unittest.TestCase):
    def test_manual_receipt_is_its_own_event_and_updates_release_status(self):
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "db.sqlite") as store:
            release_id = scheduled_release(store, Path(folder), "a.mp4", "2026-09-30T14:00:00+00:00",
                                           ("youtube", "instagram"))
            store.record_manual_receipt(release_id, "youtube", "scheduled")
            self.assertEqual(statuses(store, release_id)["release"], "partial")
            store.record_manual_receipt(release_id, "instagram", "posted")
            self.assertEqual(statuses(store, release_id)["release"], "scheduled")
            events = store.db.execute("SELECT platform, kind, payload FROM event WHERE kind LIKE 'receipt%' OR kind LIKE 'native%'").fetchall()
            self.assertEqual([(row[0], row[1]) for row in events],
                             [("youtube", "receipt_manual"), ("instagram", "receipt_manual")])
            payload = json.loads(events[0][2])
            self.assertEqual((payload["choice"], payload["previous"], payload["verification"]),
                             ("scheduled", "unconfirmed", "user_checked_phone"))
            datetime.fromisoformat(payload["confirmed_at"])

    def test_all_posted_marks_the_release_posted(self):
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "db.sqlite") as store:
            release_id = scheduled_release(store, Path(folder), "a.mp4", "2026-09-30T14:00:00+00:00", ("threads",))
            store.record_manual_receipt(release_id, "threads", "posted")
            self.assertEqual(statuses(store, release_id)["release"], "posted")

    def test_a_pending_destination_the_user_posted_by_hand_can_be_confirmed(self):
        # A TikTok never approved here,, posted by a separate runner.
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "db.sqlite") as store:
            release_id = scheduled_release(store, Path(folder), "a.mp4", "2026-09-30T14:00:00+00:00",
                                           ("youtube", "instagram", "facebook"))
            store.record_manual_receipt(release_id, "tiktok", "posted")
            self.assertEqual(statuses(store, release_id)["tiktok"], "posted")
            self.assertEqual(statuses(store, release_id)["release"], "partial")

    def test_only_pending_or_unconfirmed_and_only_posted_or_scheduled(self):
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "db.sqlite") as store:
            release_id = scheduled_release(store, Path(folder), "a.mp4", "2026-09-30T14:00:00+00:00")
            with self.assertRaisesRegex(ValueError, "Posted or Scheduled"):
                store.record_manual_receipt(release_id, "youtube", "published")
            store.record_manual_receipt(release_id, "youtube", "posted")
            with self.assertRaisesRegex(ValueError, "already has a receipt"):
                store.record_manual_receipt(release_id, "youtube", "scheduled")
            with self.assertRaisesRegex(ValueError, "Unknown destination"):
                store.record_manual_receipt(release_id, "myspace", "posted")

    def test_route_records_the_users_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with Store(state / "video-drop.sqlite") as store:
                release_id = scheduled_release(store, state, "a.mp4", "2026-09-30T14:00:00+00:00", ("threads",))
            http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(server, "STATE", state):
                    request = Request(f"http://127.0.0.1:{http.server_port}/api/releases/{release_id}/manual-receipt",
                                      data=json.dumps({"platform": "threads", "choice": "posted"}).encode(), method="POST")
                    with urlopen(request, timeout=5) as response:
                        release = json.load(response)["release"]
                    with urlopen(f"http://127.0.0.1:{http.server_port}/api/settings", timeout=5) as response:
                        settings = json.load(response)
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)
            self.assertEqual(next(d for d in release["destinations"] if d["platform"] == "threads")["status"], "posted")
            self.assertEqual(settings["unattendedStreak"], {"count": 0, "goal": 20})

    def test_page_has_the_receipt_control_and_streak(self):
        page = (server.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="receiptPosted"', 'id="receiptScheduled"', "/manual-receipt", 'id="streak"',
                       "Unattended streak:", "unattendedStreak", "tiktokPostable", "TikTok missed"):
            self.assertIn(needle, page)


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def day(offset: int) -> str:
    return (datetime(2026, 10, 1, 14, tzinfo=timezone.utc) + timedelta(days=offset)).isoformat()


class StreakTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)
        self.store = Store(self.path / "db.sqlite")

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def automated(self, name: str, slot: str, platforms=PLATFORMS) -> int:
        release_id = scheduled_release(self.store, self.path, name, slot, platforms)
        for platform in platforms:
            app_verified(self.store, release_id, platform)
        return release_id

    def streak(self, now=NOW) -> int:
        return self.store.unattended_streak(now)["count"]

    def test_counts_consecutive_app_verified_releases(self):
        for index in range(3):
            self.automated(f"{index}.mp4", day(index))
        self.assertEqual(self.store.unattended_streak(NOW), {"count": 3, "goal": 20})

    def test_manual_receipt_breaks_the_streak(self):
        self.automated("old.mp4", day(0))
        fixed = scheduled_release(self.store, self.path, "fixed.mp4", day(1), ("youtube", "tiktok"))
        app_verified(self.store, fixed, "youtube")
        self.store.record_manual_receipt(fixed, "tiktok", "posted")
        self.automated("new1.mp4", day(2))
        self.automated("new2.mp4", day(3))
        self.assertEqual(self.streak(), 2)

    def test_manual_intervention_breaks_the_streak(self):
        self.automated("old.mp4", day(0))
        rescued = self.automated("rescued.mp4", day(1))
        self.store.record_manual_intervention(rescued, "tiktok", "post_now_after_missed_slot")
        self.assertEqual(self.streak(), 0)

    def test_receipt_without_an_app_read_back_does_not_count(self):
        self.automated("old.mp4", day(0))
        release_id = scheduled_release(self.store, self.path, "hand.mp4", day(1), ("threads",))
        self.store.db.execute("UPDATE destination SET status='posted' WHERE release_id=?", (release_id,))
        self.store.db.commit()
        self.assertEqual(self.streak(), 0)

    def test_in_flight_releases_are_skipped_until_their_receipts_are_overdue(self):
        self.automated("done.mp4", day(0))
        slot = NOW - timedelta(minutes=30)
        scheduled_release(self.store, self.path, "flight.mp4", slot.isoformat(), ("youtube",))
        future = scheduled_release(self.store, self.path, "future.mp4", day(30), ("youtube",))
        self.store.db.execute("UPDATE destination SET status='pending' WHERE release_id=?", (future,))
        self.store.db.commit()
        self.assertEqual(self.streak(), 1)
        self.assertEqual(self.streak(slot + timedelta(hours=1)), 0)

    def test_a_missed_slot_breaks_the_streak(self):
        self.automated("done.mp4", day(0))
        missed = scheduled_release(self.store, self.path, "missed.mp4", day(1), ("youtube", "tiktok"))
        app_verified(self.store, missed, "youtube")
        self.store.db.execute("UPDATE destination SET status='pending' WHERE release_id=? AND platform='tiktok'", (missed,))
        self.store.db.commit()
        slot = datetime.fromisoformat(day(1))
        self.assertEqual(self.streak(slot + timedelta(minutes=5)), 1)
        self.assertEqual(self.streak(slot + timedelta(minutes=16)), 0)

    def test_post_now_and_drafts_are_not_part_of_the_streak(self):
        self.automated("done.mp4", day(0))
        source = self.path / "now.mp4"
        source.write_bytes(b"now")
        post_now = self.store.import_file(source)["id"]
        self.store.save_text(post_now, "instagram", "@me", "", "Caption", "")
        self.store.save_text(post_now, "threads", "@me", "", "", "")
        self.store.authorize(post_now, "threads")
        self.store.set_delivery_mode(post_now, "post_now")
        self.store.mark_unconfirmed(post_now, "threads")
        self.store.record_manual_receipt(post_now, "threads", "posted")
        source = self.path / "draft.mp4"
        source.write_bytes(b"draft")
        self.store.import_file(source)
        self.assertEqual(self.streak(), 1)


if __name__ == "__main__":
    unittest.main()
