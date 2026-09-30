import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from scripts import phone_threads
from scripts.phone_threads import release_input
from video_drop.core import Store

CROSSPOSTS = ("instagram", "facebook", "threads")


def approved_release(store: Store, folder: Path, *, mode: str = "post_now", caption: str = "Approved caption #game",
                     account: str = "@creator") -> int:
    source = folder / "clip.mp4"
    source.write_bytes(b"finished source")
    release_id = store.import_file(source)["id"]
    store.save_text(release_id, "instagram", account, "", caption, "")
    for platform in CROSSPOSTS:
        if platform != "instagram":
            store.save_text(release_id, platform, account, "", "", "")
        store.authorize(release_id, platform)
    store.set_delivery_mode(release_id, mode)
    return release_id


def status(store: Store, release_id: int, platform: str) -> str:
    return next(d for d in store.release(release_id)["destinations"] if d["platform"] == platform)["status"]


class ThreadsSafetyTests(unittest.TestCase):
    def test_only_exact_approved_revision_can_enter_phone_composer(self):
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "release.sqlite", {"threads": "@creator"}) as store:
            store.set_phone_checks({"crosspostThreads": False, "threadsSeparatePost": True})
            release_id = approved_release(store, Path(folder))
            self.assertEqual(release_input(store, release_id)["caption"], "Approved caption #game")
            with Store(Path(folder) / "release.sqlite", {"threads": "@another"}) as wrong_target:
                with self.assertRaisesRegex(ValueError, "differs from local accounts.json"):
                    release_input(wrong_target, release_id)
            store.db.execute("UPDATE destination SET description='Changed' WHERE release_id=? AND platform='threads'",
                             (release_id,))
            store.db.commit()
            with self.assertRaisesRegex(Exception, "changed after approval"):
                release_input(store, release_id)

    def test_threads_crossposted_by_instagram_is_never_replayed(self):
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "release.sqlite", {"threads": "@creator"}) as store:
            release_id = approved_release(store, Path(folder))
            store.mark_unconfirmed(release_id, "instagram")
            self.assertEqual(status(store, release_id, "threads"), "unconfirmed")
            self.assertIsNone(store.release(release_id)["scheduled_at"])
            with self.assertRaisesRegex(Exception, "Also share on"):
                release_input(store, release_id)
            with self.assertRaisesRegex(ValueError, "Also share on"):
                store.mark_unconfirmed(release_id, "threads")

    def test_scheduled_threads_takes_a_slot_before_any_phone_attempt(self):
        # The claim a future native Threads scheduler makes; never an immediate post.
        with tempfile.TemporaryDirectory() as folder, Store(Path(folder) / "release.sqlite") as store:
            release_id = approved_release(store, Path(folder), mode="schedule", account="@examplechannel")
            with self.assertRaisesRegex(ValueError, "No platform action"):
                store.mark_unconfirmed(release_id, "threads")
            self.assertIsNotNone(store.reserve_slot(release_id)["scheduled_at"])
            self.assertEqual(status(store, release_id, "threads"), "pending")
            store.mark_unconfirmed(release_id, "threads")
            self.assertEqual(status(store, release_id, "threads"), "unconfirmed")

    def test_parallel_instagram_claims_allow_one_final_tap(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "release.sqlite"
            with Store(path) as store:
                release_id = approved_release(store, Path(folder), account="@examplechannel")
                revision = next(d for d in store.release(release_id)["destinations"]
                                if d["platform"] == "instagram")["revision_hash"]

            def claim():
                with Store(path) as store:
                    try:
                        store.mark_unconfirmed(release_id, "instagram", expected_revision=revision)
                        return True
                    except ValueError:
                        return False

            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(lambda _: claim(), range(2))), [False, True])
            with Store(path) as store:
                claims = store.db.execute("""SELECT platform, COUNT(*) FROM event WHERE kind='unconfirmed'
                    GROUP BY platform ORDER BY platform""").fetchall()
            self.assertEqual([tuple(row) for row in claims], [("facebook", 1), ("instagram", 1), ("threads", 1)])


class ThreadsRunRefusalTests(unittest.TestCase):
    """The owner's rule: no immediate Threads post unless Settings allow a separate one."""

    def refused(self, *, mode: str, checks: dict | None = None, reserve: bool = False) -> str:
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            (state / "accounts.json").write_text('{"threads":"@creator"}', encoding="utf-8")
            db = state / "video-drop.sqlite"
            with Store(db, {"threads": "@creator"}) as store:
                if checks:
                    store.set_phone_checks(checks)
                release_id = approved_release(store, state, mode=mode)
                if reserve:
                    store.reserve_slot(release_id)
            with patch.object(phone_threads.share, "connect_sidetap",
                              side_effect=AssertionError("must not touch the phone")) as connect:
                with self.assertRaises(phone_threads.share.PhoneUploadError) as caught:
                    phone_threads.run(release_id, db, commit=True)
                connect.assert_not_called()
            with Store(db) as store:
                self.assertEqual(status(store, release_id, "threads"), "pending")
            return str(caught.exception)

    def test_post_now_goes_through_instagram_not_a_separate_post(self):
        message = self.refused(mode="post_now")
        self.assertIn("Also share on", message)
        self.assertIn("Nothing was posted", message)

    def test_schedule_never_posts_immediately(self):
        message = self.refused(mode="schedule", reserve=True)
        self.assertIn("scheduled natively in the Threads app", message)
        self.assertIn("not built yet", message)
        self.assertIn("Nothing was posted", message)

    def test_schedule_ignores_the_separate_post_setting(self):
        message = self.refused(mode="schedule", reserve=True,
                               checks={"crosspostThreads": False, "threadsSeparatePost": True})
        self.assertIn("not built yet", message)

    def test_crosspost_off_without_the_separate_option_posts_nothing(self):
        message = self.refused(mode="post_now", checks={"crosspostThreads": False})
        self.assertIn("“Post Threads separately” is off", message)


if __name__ == "__main__":
    unittest.main()
