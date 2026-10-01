"""Post now's phone queue: YouTube, then Instagram (carrying Facebook and Threads), then TikTok."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from video_drop import server
from video_drop.core import Store

ACCOUNTS = '{"youtube":"@creator","instagram":"@creator","threads":"@creator","tiktok":"@creator"}'


def post_now_release(state: Path, platforms=("instagram", "facebook", "threads"), *, mode="post_now",
                     checks: dict | None = None) -> int:
    (state / "accounts.json").write_text(ACCOUNTS, encoding="utf-8")
    source = state / "clip.mp4"
    source.write_bytes(b"finished video")
    with Store(state / "video-drop.sqlite") as store:
        if checks:
            store.set_phone_checks(checks)
        release_id = store.import_file(source)["id"]
        store.save_text(release_id, "instagram", "@creator", "", "Approved caption #game", "")
        store.save_text(release_id, "youtube", "@creator", "Title", "Description", "tag")
        store.save_text(release_id, "tiktok", "@creator", "", "Approved caption #fyp", "")
        for platform in ("facebook", "threads"):
            store.save_text(release_id, platform, "@creator", "", "", "")
        for platform in platforms:
            store.authorize(release_id, platform)
        store.set_delivery_mode(release_id, mode)
    return release_id


def statuses(state: Path, release_id: int) -> dict:
    with Store(state / "video-drop.sqlite") as store:
        return {d["platform"]: d["status"] for d in store.release(release_id)["destinations"]}


def wait_for_phone(testcase) -> dict:
    for _ in range(200):
        result = server.phone_action_status()
        if result["status"] != "running":
            return result
        time.sleep(0.02)
    testcase.fail("phone action did not finish")


class InstagramPostNowTests(unittest.TestCase):
    def queue(self, state: Path, release_id: int, platform: str = "instagram", **patches):
        with patch.object(server, "STATE", state), patch.object(server, "TEST_MODE", False), \
                patch.object(server, "phone_free_bytes", return_value=patches.get("free", 10 ** 12)):
            result = server.queue_phone_post(release_id, platform)
            return result, wait_for_phone(self)

    def test_one_instagram_upload_claims_facebook_and_threads(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = post_now_release(state)

            def fake_run(item_id, db_path, *, commit):
                self.assertEqual((item_id, db_path, commit), (release_id, state / "video-drop.sqlite", True))
                with Store(db_path) as store:
                    store.mark_unconfirmed(item_id, "instagram")
                return {"kind": "unconfirmed", "message": "Share tapped; check Instagram, Facebook and Threads"}

            with patch("scripts.phone_instagram.release_input") as checked, \
                    patch("scripts.phone_instagram.run", side_effect=fake_run) as run, \
                    patch("scripts.phone_threads.run", side_effect=AssertionError("no separate Threads post")):
                started, finished = self.queue(state, release_id)
            checked.assert_called_once()
            run.assert_called_once()
            self.assertEqual(started["status"], "running")
            self.assertEqual(finished["status"], "needs_check")
            got = statuses(state, release_id)
            self.assertEqual((got["instagram"], got["facebook"], got["threads"]),
                             ("unconfirmed", "unconfirmed", "unconfirmed"))

    def test_an_uncertain_failure_names_every_crossposted_app(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = post_now_release(state)

            def tapped_then_lost(item_id, db_path, *, commit):
                with Store(db_path) as store:
                    store.mark_unconfirmed(item_id, "instagram")
                raise RuntimeError("link dropped after the Share tap")

            with patch("scripts.phone_instagram.release_input"), \
                    patch("scripts.phone_instagram.run", side_effect=tapped_then_lost):
                _, finished = self.queue(state, release_id)
            self.assertEqual(finished["status"], "needs_check")
            self.assertIn("Instagram, Facebook and Threads", finished["message"])

    def test_instagram_waits_for_youtube_then_tiktok_waits_for_instagram(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = post_now_release(state, ("youtube", "instagram", "facebook", "threads", "tiktok"))
            with patch("scripts.phone_instagram.run") as instagram, patch("scripts.phone_tiktok.run") as tiktok:
                with self.assertRaisesRegex(ValueError, "post YouTube first"):
                    self.queue(state, release_id, "instagram")
                with self.assertRaisesRegex(ValueError, "post YouTube and Instagram first"):
                    self.queue(state, release_id, "tiktok")
                with Store(state / "video-drop.sqlite") as store:
                    store.record_manual_receipt(release_id, "youtube", "posted")
                with self.assertRaisesRegex(ValueError, "post Instagram first"):
                    self.queue(state, release_id, "tiktok")
            instagram.assert_not_called()
            tiktok.assert_not_called()

    def test_order_setting_off_lets_instagram_go_first(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = post_now_release(state, ("youtube", "instagram", "facebook", "threads"),
                                          checks={"postNowInOrder": False})
            with patch("scripts.phone_instagram.release_input"), \
                    patch("scripts.phone_instagram.run", return_value={"message": "sent"}) as run:
                self.queue(state, release_id, "instagram")
            run.assert_called_once()

    def test_schedule_mode_instagram_is_not_posted_now(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = post_now_release(state, mode="schedule")
            with patch("scripts.phone_instagram.run") as run:
                with self.assertRaisesRegex(ValueError, "set to schedule"):
                    self.queue(state, release_id)
            run.assert_not_called()

    def test_full_iphone_stops_before_any_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            release_id = post_now_release(state)
            with patch("scripts.phone_instagram.run") as run:
                with self.assertRaisesRegex(ValueError, "needs .* GB free"):
                    self.queue(state, release_id, free=10)
            run.assert_not_called()
            self.assertEqual(set(statuses(state, release_id).values()), {"pending"})


if __name__ == "__main__":
    unittest.main()
