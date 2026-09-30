"""Owner's rule: Instagram's one upload carries Facebook and Threads; each keeps its own receipt."""

import tempfile
import unittest
from pathlib import Path

from video_drop.core import PHONE_CHECK_DEFAULTS, Store

FOLLOWERS = ("facebook", "threads")


class CrosspostTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.store = Store(self.root / "state.sqlite")

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def release(self, *, mode="post_now", approve=("instagram", "facebook", "threads")) -> int:
        source = self.root / "clip.mp4"
        source.write_bytes(b"finished video")
        release_id = self.store.import_file(source)["id"]
        self.store.save_text(release_id, "instagram", "@creator", "", "Caption #game", "")
        for platform in FOLLOWERS:
            self.store.save_text(release_id, platform, "@creator", "", "", "")
        for platform in approve:
            self.store.authorize(release_id, platform)
        self.store.set_delivery_mode(release_id, mode)
        return release_id

    def statuses(self, release_id: int) -> dict:
        return {d["platform"]: d["status"] for d in self.store.release(release_id)["destinations"]}

    def test_post_now_instagram_tap_claims_facebook_and_threads_together(self):
        release_id = self.release()
        self.assertEqual(self.store.release(release_id)["instagramCrossposts"], ["facebook", "threads"])
        claimed = self.store.mark_unconfirmed(release_id, "instagram")
        self.assertEqual(claimed["status"], "needs_check")
        self.assertEqual({p: s for p, s in self.statuses(release_id).items() if p != "youtube" and p != "tiktok"},
                         {"instagram": "unconfirmed", "facebook": "unconfirmed", "threads": "unconfirmed"})
        payloads = dict(self.store.db.execute(
            "SELECT platform, payload FROM event WHERE kind='unconfirmed' AND release_id=?", (release_id,)).fetchall())
        self.assertEqual(payloads, {"instagram": '{"crossposts": ["facebook", "threads"]}',
                                    "facebook": '{"via": "instagram_crosspost"}',
                                    "threads": '{"via": "instagram_crosspost"}'})

    def test_each_crosspost_keeps_its_own_receipt(self):
        release_id = self.release()
        self.store.mark_unconfirmed(release_id, "instagram")
        self.assertEqual(self.store.record_manual_receipt(release_id, "instagram", "posted")["status"], "partial")
        self.assertEqual(self.store.record_manual_receipt(release_id, "facebook", "posted")["status"], "partial")
        self.assertEqual(self.statuses(release_id)["threads"], "unconfirmed")
        self.assertEqual(self.store.record_manual_receipt(release_id, "threads", "posted")["status"], "posted")

    def test_post_now_crossposts_cannot_be_claimed_on_their_own(self):
        release_id = self.release()
        with self.assertRaisesRegex(ValueError, "Also share on"):
            self.store.mark_unconfirmed(release_id, "threads")
        with self.assertRaisesRegex(ValueError, "Facebook goes out with Instagram"):
            self.store.mark_unconfirmed(release_id, "facebook")
        self.assertEqual(self.statuses(release_id)["threads"], "pending")

    def test_post_now_instagram_refuses_an_unapproved_crosspost_and_claims_nothing(self):
        release_id = self.release(approve=("instagram", "facebook"))
        with self.assertRaisesRegex(ValueError, "crossposts to Threads; approve its text"):
            self.store.mark_unconfirmed(release_id, "instagram")
        self.assertEqual({self.statuses(release_id)[p] for p in ("instagram", *FOLLOWERS)}, {"pending"})

    def test_schedule_instagram_carries_facebook_but_never_threads(self):
        release_id = self.release(mode="schedule")
        self.store.reserve_slot(release_id)
        self.assertEqual(self.store.release(release_id)["instagramCrossposts"], ["facebook"])
        self.store.mark_unconfirmed(release_id, "instagram")
        statuses = self.statuses(release_id)
        self.assertEqual((statuses["facebook"], statuses["threads"]), ("unconfirmed", "pending"))
        self.assertFalse(self.store.release(release_id)["threadsSeparatePost"])

    def test_crosspost_settings_leave_switched_off_destinations_pending(self):
        self.store.set_phone_checks({"crosspostThreads": False})
        release_id = self.release()
        release = self.store.release(release_id)
        self.assertEqual((release["instagramCrossposts"], release["threadsSeparatePost"]), (["facebook"], False))
        self.store.mark_unconfirmed(release_id, "instagram")
        self.assertEqual(self.statuses(release_id)["threads"], "pending")
        with self.assertRaisesRegex(ValueError, "“Post Threads separately” is off"):
            self.store.mark_unconfirmed(release_id, "threads")
        self.store.set_phone_checks({"threadsSeparatePost": True})
        self.assertTrue(self.store.release(release_id)["threadsSeparatePost"])
        self.store.mark_unconfirmed(release_id, "threads")
        self.assertEqual(self.statuses(release_id)["threads"], "unconfirmed")

    def test_facebook_crosspost_off_is_not_claimed(self):
        self.store.set_phone_checks({"crosspostFacebook": False})
        release_id = self.release()
        self.store.mark_unconfirmed(release_id, "instagram")
        statuses = self.statuses(release_id)
        self.assertEqual((statuses["facebook"], statuses["threads"]), ("pending", "unconfirmed"))

    def test_a_recorded_tap_keeps_what_it_carried_after_settings_change(self):
        release_id = self.release()
        self.store.mark_unconfirmed(release_id, "instagram")
        self.store.set_phone_checks({"crosspostThreads": False, "crosspostFacebook": False, "threadsSeparatePost": True})
        release = self.store.release(release_id)
        self.assertEqual(release["instagramCrossposts"], ["facebook", "threads"])
        self.assertFalse(release["threadsSeparatePost"])


class SettingsPageTests(unittest.TestCase):
    def test_every_setting_has_a_switch_in_settings(self):
        page = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
        for key in PHONE_CHECK_DEFAULTS:
            self.assertIn(f'data-check="{key}"', page)


if __name__ == "__main__":
    unittest.main()
