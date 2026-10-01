"""Scheduling a video from the editor and when the phone-check receipt buttons appear."""

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from video_drop import server
from video_drop.core import Store
from video_drop.slot_posts import SlotPost, receipts_due

SLOT = "2026-09-30T14:00:00+00:00"
AT_SLOT = datetime(2026, 9, 30, 14, tzinfo=timezone.utc)
PLATFORMS = ("youtube", "instagram", "facebook", "threads", "tiktok")


def release(*, status="reserved", mode="schedule", slot=SLOT, **destinations) -> dict:
    """Each destination is "status" (approved) or ("status", approved)."""
    rows = []
    for platform in PLATFORMS:
        value = destinations.get(platform, ("pending", False))
        state, approved = value if isinstance(value, tuple) else (value, True)
        rows.append({"platform": platform, "status": state, "revision_hash": "r" if approved else ""})
    return {"id": 7, "status": status, "delivery_mode": mode, "scheduled_at": slot, "destinations": rows}


def plan(state: str, platform: str = "tiktok") -> list[SlotPost]:
    return [SlotPost(7, platform, SLOT, state)]


class ReceiptButtonTests(unittest.TestCase):
    def due(self, item: dict, now: datetime, plans: list[SlotPost] = ()) -> dict:
        return {platform for platform, value in receipts_due(item, list(plans), now).items() if value}

    def test_untouched_future_releases_never_offer_receipts(self):
        draft = release(status="draft", slot=None, youtube="pending", tiktok="pending")
        self.assertEqual(self.due(draft, AT_SLOT + timedelta(days=3)), set())
        reserved = release(youtube="pending", instagram="pending")
        self.assertEqual(self.due(reserved, AT_SLOT - timedelta(minutes=1)), set())
        post_now = release(status="draft", mode="post_now", slot=None, youtube="pending", threads="pending")
        self.assertEqual(self.due(post_now, AT_SLOT), set())

    def test_approved_destinations_pending_past_their_slot_offer_receipts_whatever_the_release_status(self):
        for status in ("draft", "reserved", "scheduled", "partial", "needs_check", "posted"):
            with self.subTest(status=status):
                item = release(status=status, youtube="pending", instagram="pending", facebook=("pending", False))
                self.assertEqual(self.due(item, AT_SLOT), {"youtube", "instagram"})

    def test_unconfirmed_always_offers_receipts_even_on_a_draft(self):
        item = release(status="draft", slot=None, threads="unconfirmed")
        self.assertEqual(self.due(item, AT_SLOT - timedelta(days=1)), {"threads"})

    def test_finished_and_discarded_destinations_do_not(self):
        item = release(youtube="scheduled", instagram="posted", tiktok="unconfirmed")
        self.assertEqual(self.due(item, AT_SLOT + timedelta(hours=1)), {"tiktok"})
        self.assertEqual(self.due(release(status="discarded", youtube="unconfirmed"), AT_SLOT), set())

    def test_app_posted_tiktok_waits_for_its_own_window(self):
        item = release(tiktok="pending")
        for state in ("waiting", "arm", "armed", "post", "queued", "blocked"):
            with self.subTest(state=state):
                self.assertEqual(self.due(item, AT_SLOT + timedelta(minutes=5), plan(state)), set())
        self.assertEqual(self.due(item, AT_SLOT + timedelta(minutes=20), plan("missed")), {"tiktok"})
        # Without a slot plan (say the release left the app's slot list), the window still applies.
        self.assertEqual(self.due(item, AT_SLOT + timedelta(minutes=14)), set())
        self.assertEqual(self.due(item, AT_SLOT + timedelta(minutes=15)), {"tiktok"})

    def test_post_now_offers_receipts_for_the_rest_once_phone_work_started(self):
        item = release(status="needs_check", mode="post_now", slot=None, youtube="unconfirmed", threads="pending")
        self.assertEqual(self.due(item, AT_SLOT), {"youtube", "threads"})

    def test_now_needs_a_timezone(self):
        with self.assertRaises(ValueError):
            receipts_due(release(), [], datetime(2026, 9, 30, 14))


def reviewed(store: Store, folder: Path, name: str, platforms=("youtube", "tiktok")) -> int:
    source = folder / name
    source.write_bytes(name.encode())
    release_id = store.import_file(source)["id"]
    for platform in platforms:
        store.save_text(release_id, platform, "@channel", "Title", "Caption #fyp", "tag")
        store.authorize(release_id, platform)
    return release_id


class MoveHeldSlotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite")
        self.now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)  # 8 AM New York

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_a_held_future_time_moves_to_another_free_slot(self):
        release_id = reviewed(self.store, self.root, "a.mp4")
        held = self.store.reserve_slot(release_id, self.now)["scheduled_at"]
        self.assertEqual(held, "2026-09-28T14:00:00+00:00")
        same = self.store.reserve_slot(release_id, self.now, at=datetime.fromisoformat(held))
        self.assertEqual(same["scheduled_at"], held)
        moved = self.store.reserve_slot(release_id, self.now, at=datetime.fromisoformat("2026-09-29T19:00:00-04:00"))
        self.assertEqual((moved["scheduled_at"], moved["status"]), ("2026-09-29T23:00:00+00:00", "reserved"))
        kinds = [row[0] for row in self.store.db.execute("SELECT kind FROM event WHERE kind LIKE 'slot_%' ORDER BY id")]
        self.assertEqual(kinds, ["slot_reserved", "slot_replanned"])
        # The old time is free again for another video.
        other = reviewed(self.store, self.root, "b.mp4")
        self.assertEqual(self.store.reserve_slot(other, self.now)["scheduled_at"], held)

    def test_a_held_time_cannot_move_onto_another_video_or_after_an_attempt(self):
        first = reviewed(self.store, self.root, "a.mp4")
        second = reviewed(self.store, self.root, "b.mp4")
        self.store.reserve_batch([first, second], self.now)
        taken = datetime.fromisoformat(self.store.release(second)["scheduled_at"])
        with self.assertRaisesRegex(ValueError, "already has that posting time"):
            self.store.reserve_slot(first, self.now, at=taken)
        with self.store.db:
            self.store.db.execute("UPDATE destination SET status='scheduled' WHERE release_id=? AND platform='youtube'",
                                  (first,))
        with self.assertRaisesRegex(ValueError, "cannot move"):
            self.store.reserve_slot(first, self.now, at=datetime.fromisoformat("2026-10-01T10:00:00-04:00"))


class PlanRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def call(self, path: str, body: dict | None = None) -> dict:
        request = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_editor_schedules_tiktok_at_the_chosen_slot_and_the_app_takes_it(self):
        with Store(self.state / "video-drop.sqlite") as store:
            release_id = reviewed(store, self.state, "a.mp4")
        now = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        with patch.object(server, "STATE", self.state), patch.object(server, "TEST_MODE", False), \
                patch("video_drop.core.utc_now", return_value=now), patch("video_drop.server.utc_now", return_value=now):
            choices = self.call("/api/settings")["nextSlots"]
            self.assertEqual(choices[:2], ["2026-09-28T10:00:00-04:00", "2026-09-28T19:00:00-04:00"])
            planned = self.call("/api/queue/plan", {"releaseIds": [release_id], "at": choices[1]})["releases"][0]
            self.assertEqual((planned["status"], planned["scheduled_at"]), ("reserved", "2026-09-28T23:00:00+00:00"))
            self.assertEqual(planned["slotPosts"]["tiktok"]["state"], "waiting")
            self.assertFalse(any(planned["receiptDue"].values()))
            self.assertNotIn("2026-09-28T19:00:00-04:00", self.call("/api/settings")["nextSlots"])
            moved = self.call("/api/queue/plan", {"releaseIds": [release_id], "at": choices[2]})["releases"][0]
            self.assertEqual(moved["scheduled_at"], "2026-09-29T14:00:00+00:00")
            self.assertEqual(moved["slotPosts"]["tiktok"]["slot"], moved["scheduled_at"])

    def test_release_payload_offers_receipts_past_the_slot_even_for_a_draft(self):
        with Store(self.state / "video-drop.sqlite") as store:
            release_id = reviewed(store, self.state, "a.mp4", ("youtube",))
            with store.db:
                store.db.execute("UPDATE release SET scheduled_at=? WHERE id=?", (SLOT, release_id))
        with patch.object(server, "STATE", self.state):
            with patch("video_drop.server.utc_now", return_value=AT_SLOT - timedelta(minutes=1)):
                before = self.call(f"/api/releases/{release_id}")["release"]
            with patch("video_drop.server.utc_now", return_value=AT_SLOT + timedelta(minutes=1)):
                after = self.call(f"/api/releases/{release_id}")["release"]
                queued = self.call("/api/queue")["releases"]
                receipt = self.call(f"/api/releases/{release_id}/manual-receipt",
                                    {"platform": "youtube", "choice": "scheduled"})["release"]
        self.assertEqual(before["status"], "draft")
        self.assertFalse(before["receiptDue"]["youtube"])
        self.assertTrue(after["receiptDue"]["youtube"])
        self.assertFalse(after["receiptDue"]["instagram"])
        self.assertTrue(next(item for item in queued if item["id"] == release_id)["receiptDue"]["youtube"])
        self.assertEqual(receipt["receiptDue"], {platform: False for platform in PLATFORMS})

    def test_plan_route_refuses_an_unreviewed_video(self):
        with Store(self.state / "video-drop.sqlite") as store:
            source = self.state / "raw.mp4"
            source.write_bytes(b"raw")
            release_id = store.import_file(source)["id"]
        with patch.object(server, "STATE", self.state), patch.object(server, "TEST_MODE", False):
            with self.assertRaises(HTTPError) as rejected:
                self.call("/api/queue/plan", {"releaseIds": [release_id], "at": "2030-01-01T10:00:00-05:00"})
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()


class EditorPageTests(unittest.TestCase):
    def test_editor_has_a_posting_time_control_and_honest_disabled_reasons(self):
        page = (server.ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="slotChoice"', "/api/queue/plan", "receiptDue", 'id="timeZone"', "/api/settings/time-zone",
                       "`Schedule for ${", "tiktokPostable", "title:'Extras',count:'Optional'"):
            self.assertIn(needle, page)
        self.assertNotIn("America/New_York", page)
        self.assertNotIn("Native scheduling is not connected yet", page)


if __name__ == "__main__":
    unittest.main()
