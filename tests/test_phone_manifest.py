import tempfile
import unittest
from pathlib import Path

from video_drop.core import Store
from video_drop.phone_manifest import verify_youtube_manifest, youtube_input


class YouTubeManifestTests(unittest.TestCase):
    def test_manifest_must_match_confirmed_release_and_original_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "finished.mp4"
            source.write_bytes(b"finished source")
            with Store(root / "state.sqlite", {"youtube": "@channel"}) as store:
                release_id = store.import_file(source)["id"]
                with self.assertRaisesRegex(ValueError, "Confirm YouTube details"):
                    youtube_input(store, release_id)
                store.save_text(release_id, "youtube", "@channel", "Exact title", "Description #game", "game, clip")
                store.authorize(release_id, "youtube")
                manifest = youtube_input(store, release_id)
                self.assertEqual(manifest["filename"], "finished.mp4")
                self.assertEqual(manifest["tags"], ["game", "clip"])
                self.assertEqual(verify_youtube_manifest(store, manifest), manifest)
                stale = dict(manifest, title="Other title")
                with self.assertRaisesRegex(ValueError, "title does not match"):
                    verify_youtube_manifest(store, stale)
                with Store(root / "state.sqlite") as no_target:
                    with self.assertRaisesRegex(ValueError, "local accounts.json"):
                        youtube_input(no_target, release_id)
                with Store(root / "state.sqlite", {"youtube": "@other-channel"}) as wrong_target:
                    with self.assertRaisesRegex(ValueError, "differs from local accounts.json"):
                        youtube_input(wrong_target, release_id)
                source.write_bytes(b"altered  source")
                with self.assertRaisesRegex(ValueError, "Source content changed"):
                    verify_youtube_manifest(store, manifest)

    def test_edited_public_copy_invalidates_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "finished.mp4"
            source.write_bytes(b"finished source")
            with Store(root / "state.sqlite", {"youtube": "@channel"}) as store:
                release_id = store.import_file(source)["id"]
                store.save_text(release_id, "youtube", "@channel", "First title", "Description", "game")
                store.authorize(release_id, "youtube")
                manifest = youtube_input(store, release_id)
                store.save_text(release_id, "youtube", "@channel", "Revised title", "Description", "game")
                with self.assertRaisesRegex(ValueError, "Confirm YouTube details"):
                    verify_youtube_manifest(store, manifest)


if __name__ == "__main__":
    unittest.main()
