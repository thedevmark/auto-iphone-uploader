"""release_run's decisions on synthetic releases: which apps run now, in which order, and what the page shows."""

import unittest
from unittest.mock import patch

from video_drop import release_run


def release(mode="schedule", *, approved=("youtube", "instagram", "facebook", "threads", "tiktok"), status=None,
            crossposts=None, separate=False, slot="2030-01-02T15:00:00+00:00", **extra):
    status = status or {}
    return {"id": 7, "delivery_mode": mode, "status": "reserved", "scheduled_at": slot if mode == "schedule" else None,
            "destinations": [{"platform": p, "revision_hash": "r" if p in approved else "", "status": status.get(p, "pending")}
                             for p in ("youtube", "instagram", "facebook", "threads", "tiktok")],
            "instagramCrossposts": crossposts if crossposts is not None else
            (["facebook"] if mode == "schedule" else ["facebook", "threads"]),
            "threadsSeparatePost": separate, **extra}


class PlanTests(unittest.TestCase):
    def states(self, item, mode=None):
        return {r["platform"]: r["state"] for r in release_run.plan(item, mode or item["delivery_mode"])}

    def test_schedule_runs_youtube_and_instagram_only(self):
        rows = release_run.plan(release(), "schedule")
        self.assertEqual(release_run.runnable(rows), ["youtube", "instagram"])
        self.assertEqual(self.states(release()), {"youtube": "queued", "instagram": "queued", "facebook": "with_instagram",
                                                  "tiktok": "needs_you", "threads": "needs_you"})
        tiktok = next(r for r in rows if r["platform"] == "tiktok")
        self.assertIn("Post it yourself, or press Post now on TikTok", tiktok["note"])

    def test_post_now_order_puts_a_separate_threads_post_last(self):
        item = release("post_now", crossposts=["facebook"], separate=True)
        rows = release_run.plan(item, "post_now")
        self.assertEqual(release_run.runnable(rows), ["youtube", "instagram", "tiktok", "threads"])
        self.assertEqual([r["platform"] for r in rows], ["youtube", "instagram", "facebook", "tiktok", "threads"])

    def test_attempted_and_unapproved_destinations_never_run(self):
        item = release(approved=("youtube", "instagram", "facebook"), status={"youtube": "unconfirmed"})
        rows = release_run.plan(item, "schedule")
        self.assertEqual(release_run.runnable(rows), ["instagram"])
        self.assertNotIn("tiktok", {r["platform"] for r in rows})
        self.assertEqual(rows[0]["state"], "unconfirmed")

    def test_refusals(self):
        self.assertIn("switch it to Post now", release_run.mode_refusal(release(), "post_now"))
        self.assertIn("Reserve a posting time", release_run.mode_refusal(release(slot=None), "schedule"))
        self.assertIsNone(release_run.mode_refusal(release(), "schedule"))


class ProgressTests(unittest.TestCase):
    def test_nothing_is_queued_until_a_run_starts(self):
        rows = {r["platform"]: r["state"] for r in release_run.progress(release())}
        self.assertEqual(rows["youtube"], "ready")
        action = {"releaseId": 7, "steps": [{"platform": "youtube", "state": "running", "message": "Scheduling in YouTube"},
                                            {"platform": "instagram", "state": "queued", "message": ""}]}
        rows = {r["platform"]: r for r in release_run.progress(release(), action)}
        self.assertEqual((rows["youtube"]["state"], rows["youtube"]["note"]), ("running", "Scheduling in YouTube"))
        self.assertEqual(rows["instagram"]["state"], "queued")

    def test_another_releases_run_is_ignored(self):
        action = {"releaseId": 8, "steps": [{"platform": "youtube", "state": "running", "message": ""}]}
        self.assertEqual(release_run.progress(release(), action)[0]["state"], "ready")

    def test_the_recorded_status_wins_over_a_stale_step(self):
        item = release(status={"youtube": "unconfirmed"})
        action = {"releaseId": 7, "steps": [{"platform": "youtube", "state": "running", "message": ""}]}
        self.assertEqual(release_run.progress(item, action)[0]["state"], "unconfirmed")

    @patch("video_drop.core.APP_POSTED_DESTINATIONS", frozenset({"tiktok"}))  # the dormant slot engine
    def test_tiktok_slot_states(self):
        for slot_state, shown in (("waiting", "at_slot"), ("due", "running"), ("blocked", "at_slot"), ("missed", "needs_you")):
            with self.subTest(slot_state):
                item = release(slotPosts={"tiktok": {"state": slot_state, "reason": "Waiting for youtube first"}})
                rows = {r["platform"]: r for r in release_run.progress(item)}
                self.assertEqual(rows["tiktok"]["state"], shown)


if __name__ == "__main__":
    unittest.main()
