import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.phone_threads import release_input
from video_drop.core import Store


class ThreadsSafetyTests(unittest.TestCase):
    def test_only_exact_approved_revision_can_enter_phone_composer(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "clip.mp4"
            source.write_bytes(b"finished source")
            with Store(Path(folder) / "release.sqlite", {"threads": "@creator"}) as store:
                release_id = store.import_file(source)["id"]
                caption = "Approved caption #game"
                store.save_text(release_id, "instagram", "@creator", "", caption, "")
                store.save_text(release_id, "threads", "@creator", "", "", "")
                store.authorize(release_id, "threads")
                prepared = release_input(store, release_id)
                self.assertEqual(prepared["caption"], caption)
                with Store(Path(folder) / "release.sqlite", {"threads": "@another"}) as wrong_target:
                    with self.assertRaisesRegex(ValueError, "differs from local accounts.json"):
                        release_input(wrong_target, release_id)
                store.db.execute("UPDATE destination SET description='Changed' WHERE release_id=? AND platform='threads'",
                                 (release_id,))
                store.db.commit()
                with self.assertRaisesRegex(Exception, "changed after approval"):
                    release_input(store, release_id)

    def test_unconfirmed_threads_attempt_cannot_be_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "clip.mp4"
            source.write_bytes(b"finished source")
            with Store(Path(folder) / "release.sqlite", {"threads": "@creator"}) as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "instagram", "@creator", "", "Approved", "")
                store.save_text(release_id, "threads", "@creator", "", "", "")
                store.authorize(release_id, "threads")
                store.set_delivery_mode(release_id, "post_now")
                store.mark_unconfirmed(release_id, "threads")
                self.assertIsNone(store.release(release_id)["scheduled_at"])
                with self.assertRaisesRegex(Exception, "already attempted"):
                    release_input(store, release_id)
                with self.assertRaisesRegex(ValueError, "already attempted"):
                    store.mark_unconfirmed(release_id, "threads")

    def test_scheduled_threads_takes_a_slot_before_any_phone_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "clip.mp4"
            source.write_bytes(b"finished source")
            with Store(Path(folder) / "release.sqlite") as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "instagram", "@examplechannel", "", "Caption", "")
                store.save_text(release_id, "threads", "@examplechannel", "", "", "")
                store.authorize(release_id, "threads")
                self.assertEqual(store.release(release_id)["delivery_mode"], "schedule")
                with self.assertRaisesRegex(ValueError, "No platform action"):
                    store.mark_unconfirmed(release_id, "threads")
                planned = store.reserve_slot(release_id)
                self.assertIsNotNone(planned["scheduled_at"])
                claimed = store.mark_unconfirmed(release_id, "threads")
                self.assertEqual(next(d for d in claimed["destinations"] if d["platform"] == "threads")["status"],
                                 "unconfirmed")

    def test_parallel_threads_claims_allow_one_final_tap(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "release.sqlite"
            source = Path(folder) / "clip.mp4"
            source.write_bytes(b"finished source")
            with Store(path) as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "instagram", "@examplechannel", "", "Caption", "")
                store.save_text(release_id, "threads", "@examplechannel", "", "", "")
                store.set_delivery_mode(release_id, "post_now")
                revision = next(d for d in store.authorize(release_id, "threads")["destinations"]
                                if d["platform"] == "threads")["revision_hash"]

            def claim():
                with Store(path) as store:
                    try:
                        store.mark_unconfirmed(release_id, "threads", expected_revision=revision)
                        return True
                    except ValueError:
                        return False

            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(lambda _: claim(), range(2))), [False, True])


if __name__ == "__main__":
    unittest.main()
