import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from video_drop import receipt_sweep as sweep

TAP = datetime(2026, 10, 1, 4, 30, tzinfo=timezone.utc)


def waiting(platform="instagram", mode="post_now", since=TAP):
    return [{"releaseId": 6, "platform": platform, "mode": mode, "since": since.isoformat()}]


class SweepTimingTests(unittest.TestCase):
    def test_nothing_before_the_first_look(self):
        self.assertEqual(sweep.due(waiting(), set(), TAP + timedelta(minutes=2)), [])

    def test_each_look_happens_once_inside_its_window(self):
        done = set()
        looks = []
        for minute in range(0, 120):
            for release_id, platform, key in sweep.due(waiting(), done, TAP + timedelta(minutes=minute)):
                done.add(key)
                looks.append(minute)
        self.assertEqual(looks, [3, 10, 25, 55])

    def test_a_missed_window_is_skipped_not_caught_up(self):
        # The PC slept through the first two looks: only the 25-minute look runs at 30 min.
        found = sweep.due(waiting(), set(), TAP + timedelta(minutes=30))
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0][2].endswith(":2"))
        self.assertEqual(sweep.due(waiting(), set(), TAP + timedelta(hours=3)), [])

    def test_only_recorded_routes_are_ever_read(self):
        later = TAP + timedelta(minutes=4)
        self.assertEqual(sweep.due(waiting("threads"), set(), later)[0][:2], (6, "threads"))
        for platform in ("facebook", "tiktok", "youtube"):
            self.assertEqual(sweep.due(waiting(platform), set(), later), [], platform)

    def test_a_new_final_tap_gets_its_own_looks(self):
        first = sweep.due(waiting(), set(), TAP + timedelta(minutes=4))
        again = sweep.due(waiting(since=TAP + timedelta(hours=2)), {first[0][2]}, TAP + timedelta(hours=2, minutes=4))
        self.assertEqual(len(again), 1)

    def test_attempts_survive_a_restart_and_age_out_after_a_day(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            self.assertEqual(sweep.load_done(state), set())
            key = sweep.due(waiting(), set(), TAP + timedelta(minutes=4))[0][2]
            sweep.save_done(state, {key}, TAP + timedelta(minutes=4))
            self.assertEqual(sweep.load_done(state), {key})
            sweep.save_done(state, {key}, TAP + timedelta(days=2))
            self.assertEqual(sweep.load_done(state), set())


class UnconfirmedQueryTests(unittest.TestCase):
    def test_reads_the_final_tap_time_of_each_unconfirmed_destination(self):
        from video_drop.core import Store
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "clip.mp4").write_bytes(b"video")
            with Store(root / "state.sqlite") as store:
                release_id = store.import_file(root / "clip.mp4")["id"]
                store.set_delivery_mode(release_id, "post_now")
                store.save_text(release_id, "youtube", "@creator", "Title", "Description", "tag")
                store.authorize(release_id, "youtube")
                store.mark_unconfirmed(release_id, "youtube")
                rows = sweep.unconfirmed(store.db)
        self.assertEqual([(r["releaseId"], r["platform"], r["mode"]) for r in rows],
                         [(release_id, "youtube", "post_now")])
        self.assertIsNotNone(datetime.fromisoformat(rows[0]["since"]).tzinfo)


class SweepServerTests(unittest.TestCase):
    def setUp(self):
        from video_drop import server
        self.server = server
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        state = Path(self.folder.name)
        for name, value in (("STATE", state), ("TEST_MODE", False), ("PHONE_ACTION_RUNNING", False),
                            ("PHONE_RUNNING", False)):
            patcher = patch.object(server, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.submitted = []
        patcher = patch.object(server.PHONE_POOL, "submit", lambda fn, *args: self.submitted.append((fn, args)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tick(self, *, link="ready", setting=True, rows=None):
        rows = waiting() if rows is None else rows
        with patch.object(self.server.receipt_sweep, "unconfirmed", lambda db: rows), \
                patch.object(self.server.link_supervisor, "read_status", lambda state: {"state": link}), \
                patch.object(self.server.Store, "phone_checks", lambda store: {"readReceipts": setting}):
            return self.server.receipt_sweep_tick(TAP + timedelta(minutes=4))

    def test_a_due_read_claims_the_phone_worker(self):
        self.assertEqual(self.tick(), [(6, "instagram")])
        self.assertTrue(self.server.PHONE_ACTION_RUNNING)
        self.assertEqual(self.submitted[0][0], self.server.run_receipt_checks)

    def test_setting_off_link_down_or_phone_busy_start_nothing(self):
        self.assertEqual(self.tick(setting=False), [])
        self.assertEqual(self.tick(link="waiting-tunnel"), [])
        (self.server.STATE / "phone.lock").write_text("investigator: link arms", encoding="utf-8")
        self.assertEqual(self.tick(), [])
        (self.server.STATE / "phone.lock").unlink()
        self.server.PHONE_ACTION_RUNNING = True
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.submitted, [])

    def test_a_declared_busy_window_stands_the_read_down_without_spending_it(self):
        # A hand-run posting script holds no phone.lock; its busy window (an upload still running)
        # is the only sign it has the phone. The attempt is kept for the next tick in the window.
        self.server.link_supervisor.declare_busy(self.server.STATE, "YouTube upload", 600)
        self.assertEqual(self.tick(), [])
        self.assertEqual(self.submitted, [])
        self.assertEqual(sweep.load_done(self.server.STATE), set())
        self.server.link_supervisor.clear_busy(self.server.STATE)
        self.assertEqual(self.tick(), [(6, "instagram")])

    def test_the_worker_spends_each_attempt_and_frees_the_phone(self):
        checks = [(6, "instagram", sweep.due(waiting(), set(), TAP + timedelta(minutes=4))[0][2])]
        self.server.PHONE_ACTION_RUNNING = True
        with patch("scripts.phone_receipts.run", side_effect=RuntimeError("link lost")):
            results = self.server.run_receipt_checks(checks, TAP + timedelta(minutes=4))
        self.assertEqual(results[0]["kind"], "error")
        self.assertFalse(self.server.PHONE_ACTION_RUNNING)
        self.assertEqual(sweep.load_done(self.server.STATE), {checks[0][2]})


if __name__ == "__main__":
    unittest.main()
