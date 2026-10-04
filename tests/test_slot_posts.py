import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from video_drop import server
from video_drop.core import Store
from video_drop.slot_posts import plan_slot_posts

SLOT = "2026-09-30T14:00:00+00:00"
AT_SLOT = datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc)


def destination(platform, status="pending", approved=True):
    return {"platform": platform, "status": status, "revision_hash": "r" if approved else ""}


def release(release_id=3, *, tiktok="pending", youtube="unconfirmed", instagram="unconfirmed",
            approved=True, mode="schedule", slot=SLOT):
    return {"id": release_id, "delivery_mode": mode, "scheduled_at": slot, "status": "needs_check",
            "destinations": [destination("youtube", youtube), destination("instagram", instagram),
                             destination("facebook", "unconfirmed"), destination("threads"),
                             destination("tiktok", tiktok, approved)]}


class FakeStore:
    def __init__(self, releases, marks=None):
        self.releases, self.marks = releases, marks or {}

    def slot_post_releases(self):
        return self.releases

    def slot_post_marks(self):
        return self.marks


def states(store, now):
    return [(plan.release_id, plan.platform, plan.state) for plan in plan_slot_posts(store, now)]


@patch("video_drop.core.APP_POSTED_DESTINATIONS", frozenset({"tiktok"}))  # the dormant slot engine
class SlotDecisionTests(unittest.TestCase):
    def test_future_slot_is_armed_once_then_posted_inside_the_window(self):
        store = FakeStore([release()])
        self.assertEqual(states(store, AT_SLOT - timedelta(hours=2)), [(3, "tiktok", "arm")])
        store.marks = {(3, "tiktok", SLOT): {"slot_armed"}}
        self.assertEqual(states(store, AT_SLOT - timedelta(minutes=1)), [(3, "tiktok", "armed")])
        self.assertEqual(states(store, AT_SLOT), [(3, "tiktok", "post")])
        self.assertEqual(states(store, AT_SLOT + timedelta(minutes=14, seconds=59)), [(3, "tiktok", "post")])

    def test_slot_older_than_the_grace_window_is_missed_never_posted(self):
        store = FakeStore([release()], {(3, "tiktok", SLOT): {"slot_armed"}})
        self.assertEqual(states(store, AT_SLOT + timedelta(minutes=15)), [(3, "tiktok", "missed")])
        self.assertEqual(states(store, AT_SLOT + timedelta(days=2)), [(3, "tiktok", "missed")])

    def test_a_slot_first_seen_after_it_passed_is_not_posted(self):
        # The app was not running before the slot; another runner may already have posted it.
        store = FakeStore([release()])
        self.assertEqual(states(store, AT_SLOT + timedelta(minutes=1)), [(3, "tiktok", "missed")])

    def test_queued_slot_is_never_started_twice(self):
        store = FakeStore([release()], {(3, "tiktok", SLOT): {"slot_armed", "slot_post_queued"}})
        self.assertEqual(states(store, AT_SLOT + timedelta(minutes=2)), [(3, "tiktok", "queued")])
        store.marks[(3, "tiktok", SLOT)].add("slot_post_failed")
        self.assertEqual(states(store, AT_SLOT + timedelta(minutes=3)), [(3, "tiktok", "missed")])

    def test_only_pending_approved_app_posted_destinations_in_schedule_mode(self):
        marks = {(release_id, "tiktok", SLOT): {"slot_armed"} for release_id in range(1, 6)}
        store = FakeStore([release(1, tiktok="unconfirmed"), release(2, tiktok="posted"),
                           release(3, approved=False), release(4, mode="post_now"), release(5, slot=None)], marks)
        self.assertEqual(states(store, AT_SLOT + timedelta(minutes=1)), [])

    def test_tiktok_waits_for_youtube_and_instagram(self):
        store = FakeStore([release(youtube="pending")], {(3, "tiktok", SLOT): {"slot_armed"}})
        [plan] = plan_slot_posts(store, AT_SLOT + timedelta(minutes=1))
        self.assertEqual(plan.state, "blocked")
        self.assertIn("youtube", plan.reason)
        self.assertEqual(states(store, AT_SLOT + timedelta(minutes=20)), [(3, "tiktok", "missed")])

    def test_replanned_slot_needs_its_own_arming(self):
        later = "2026-09-30T23:00:00+00:00"
        store = FakeStore([release(slot=later)], {(3, "tiktok", SLOT): {"slot_armed", "slot_post_queued"}})
        self.assertEqual(states(store, AT_SLOT + timedelta(hours=1)), [(3, "tiktok", "arm")])

    def test_now_needs_a_timezone(self):
        with self.assertRaises(ValueError):
            plan_slot_posts(FakeStore([]), datetime(2026, 9, 30, 14))


def schedule_release(state: Path, *, slot: str = SLOT, status: str = "needs_check") -> int:
    """A release shaped like the reference release: native apps already handled, TikTok pending and approved."""
    (state / "accounts.json").write_text('{"tiktok":"@creator"}', encoding="utf-8")
    source = state / "clip.mp4"
    source.write_bytes(b"finished video")
    with Store(state / "video-drop.sqlite", {"tiktok": "@creator"}) as store:
        release_id = store.import_file(source)["id"]
        store.save_text(release_id, "tiktok", "@creator", "", "Approved caption #game #fyp", "")
        store.authorize(release_id, "tiktok")
        store.db.execute("UPDATE release SET status=?, scheduled_at=? WHERE id=?", (status, slot, release_id))
        store.db.execute("""UPDATE destination SET status='unconfirmed', revision_hash='r'
            WHERE release_id=? AND platform IN ('youtube','instagram','facebook')""", (release_id,))
        store.db.commit()
    return release_id


def tiktok_status(state: Path, release_id: int) -> str:
    with Store(state / "video-drop.sqlite") as store:
        return next(d for d in store.release(release_id)["destinations"] if d["platform"] == "tiktok")["status"]


def wait_for_phone(testcase):
    for _ in range(100):
        result = server.phone_action_status()
        if result["status"] != "running":
            return result
        time.sleep(0.02)
    testcase.fail("phone action did not finish")


@patch("video_drop.core.APP_POSTED_DESTINATIONS", frozenset({"tiktok"}))  # the dormant slot engine
class SlotSchedulerTests(unittest.TestCase):
    def run_tick(self, state, now, **patches):
        with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                patch.object(server, "utc_now", return_value=now), \
                patch.object(server, "phone_free_bytes", return_value=10 ** 12):
            return server.slot_post_tick(now)

    def test_restart_after_the_slot_never_reposts_tiktok(self):
        """The reference TikTok is posted at 14:00Z by a separate runner that records nothing here.

        A server started after that slot, inside or after the grace window, must not post it again.
        """
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = schedule_release(state)
            with patch("scripts.phone_tiktok.run", side_effect=AssertionError("must not touch the phone")) as run, \
                    patch.object(server, "queue_phone_post", wraps=server.queue_phone_post) as queue:
                for minutes in (0, 1, 5, 14, 15, 16, 60, 60 * 24):
                    self.assertEqual(self.run_tick(state, AT_SLOT + timedelta(minutes=minutes)), [])
                queue.assert_not_called()
                run.assert_not_called()
            self.assertEqual(tiktok_status(state, release_id), "pending")
            with Store(state / "video-drop.sqlite") as store, patch.object(server, "utc_now",
                                                                             return_value=AT_SLOT + timedelta(minutes=5)):
                self.assertEqual(server.slot_post_state(store, release_id, "tiktok").state, "missed")
                kinds = [row[0] for row in store.db.execute("SELECT kind FROM event WHERE release_id=?", (release_id,))]
            self.assertNotIn("slot_post_queued", kinds)
            self.assertNotIn("slot_armed", kinds)

    def test_armed_slot_posts_once_and_a_restart_does_not_repeat_it(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = schedule_release(state)
            calls = []

            def fake_run(item_id, db_path, *, commit, not_after):
                calls.append((item_id, commit, not_after))
                with Store(db_path, {"tiktok": "@creator"}) as store:
                    store.mark_unconfirmed(item_id, "tiktok")
                return {"message": "Final tap sent"}

            with patch("scripts.phone_tiktok.run", side_effect=fake_run):
                self.assertEqual(self.run_tick(state, AT_SLOT - timedelta(minutes=10)), [])
                started = self.run_tick(state, AT_SLOT + timedelta(seconds=30))
                self.assertEqual([(plan.release_id, plan.platform) for plan in started], [(release_id, "tiktok")])
                with patch.object(server, "STATE", state):
                    self.assertEqual(wait_for_phone(self)["status"], "needs_check")
                for minutes in (1, 2, 10, 20):  # later ticks and restarts
                    self.assertEqual(self.run_tick(state, AT_SLOT + timedelta(minutes=minutes)), [])
            self.assertEqual(calls, [(release_id, True, AT_SLOT + timedelta(minutes=15))])
            self.assertEqual(tiktok_status(state, release_id), "unconfirmed")

    def test_failed_preparation_is_missed_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = schedule_release(state)
            with patch("scripts.phone_tiktok.run", side_effect=RuntimeError("cover is not the first frame")) as run:
                self.run_tick(state, AT_SLOT - timedelta(minutes=10))
                self.assertEqual(len(self.run_tick(state, AT_SLOT + timedelta(minutes=1))), 1)
                with patch.object(server, "STATE", state):
                    result = wait_for_phone(self)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(self.run_tick(state, AT_SLOT + timedelta(minutes=2)), [])
                self.assertEqual(run.call_count, 1)
            with Store(state / "video-drop.sqlite") as store, patch.object(server, "utc_now",
                                                                             return_value=AT_SLOT + timedelta(minutes=2)):
                self.assertEqual(server.slot_post_state(store, release_id, "tiktok").state, "missed")
            self.assertEqual(tiktok_status(state, release_id), "pending")

    def test_busy_phone_waits_for_the_next_tick_inside_the_window(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            schedule_release(state)
            self.run_tick(state, AT_SLOT - timedelta(minutes=10))
            with patch("scripts.phone_tiktok.run") as run, patch.object(server, "PHONE_RUNNING", True):
                self.assertEqual(self.run_tick(state, AT_SLOT + timedelta(minutes=1)), [])
                run.assert_not_called()
            with Store(state / "video-drop.sqlite") as store:
                marks = store.slot_post_marks()
            self.assertEqual(set().union(*marks.values()), {"slot_armed"})

    def test_missed_slot_offers_post_now_as_a_recorded_hand_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = schedule_release(state)
            late = AT_SLOT + timedelta(hours=2)
            with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                    patch.object(server, "utc_now", return_value=AT_SLOT - timedelta(hours=1)), \
                    patch.object(server, "phone_free_bytes", return_value=10 ** 12), \
                    patch("scripts.phone_tiktok.run", return_value={"message": "sent"}) as run:
                with self.assertRaisesRegex(ValueError, "missed slot"):
                    server.queue_phone_post(release_id, "tiktok")
                with patch.object(server, "utc_now", return_value=late):
                    server.queue_phone_post(release_id, "tiktok")
                    wait_for_phone(self)
            run.assert_called_once()
            self.assertIsNone(run.call_args.kwargs["not_after"])
            with Store(state / "video-drop.sqlite") as store:
                payloads = [json.loads(row[0]) for row in store.db.execute(
                    "SELECT payload FROM event WHERE kind='manual_intervention' AND release_id=?", (release_id,))]
            self.assertEqual(payloads, [{"action": "post_now_after_missed_slot"}])

    def test_queue_annotates_missed_tiktok_for_the_ui(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = schedule_release(state)
            with Store(state / "video-drop.sqlite") as store, \
                    patch.object(server, "utc_now", return_value=AT_SLOT + timedelta(minutes=30)):
                [annotated] = server.with_slot_posts(store, [store.release(release_id)])
            self.assertEqual(annotated["slotPosts"]["tiktok"]["state"], "missed")
            self.assertEqual(annotated["slotPosts"]["tiktok"]["slot"], SLOT)


class OwnerPostsTikTokTests(unittest.TestCase):
    """Owner decision 2026-10-04: Schedule happens only on the platforms' own schedulers.

    TikTok's phone app cannot schedule, so this app never posts it at the slot; the owner
    posts it themselves or presses Post now, whenever they choose."""

    def test_no_slot_is_ever_armed_or_posted(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = schedule_release(state)
            with Store(state / "video-drop.sqlite") as store:
                for moment in (AT_SLOT - timedelta(hours=1), AT_SLOT + timedelta(minutes=1)):
                    self.assertEqual([p for p in plan_slot_posts(store, moment) if p.release_id == release_id], [])

    def test_post_now_on_tiktok_works_before_the_slot_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = schedule_release(state)
            with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                    patch.object(server, "utc_now", return_value=AT_SLOT - timedelta(hours=1)), \
                    patch.object(server, "phone_free_bytes", return_value=10 ** 12), \
                    patch("scripts.phone_tiktok.run", return_value={"message": "sent"}) as run:
                server.queue_phone_post(release_id, "tiktok")
                wait_for_phone(self)
            run.assert_called_once()
            self.assertIsNone(run.call_args.kwargs["not_after"])
            with Store(state / "video-drop.sqlite") as store:
                payloads = [json.loads(row[0]) for row in store.db.execute(
                    "SELECT payload FROM event WHERE kind='manual_intervention' AND release_id=?", (release_id,))]
            self.assertEqual(payloads, [{"action": "post_now_in_schedule_mode"}])


if __name__ == "__main__":
    unittest.main()
