import tempfile
import unittest
from pathlib import Path

from scripts.import_homebase import apply, compare, media_issues, merge_copy
from video_drop.core import Store, digest


def source_snapshot(path: Path) -> dict:
    path.write_bytes(b"homebase clip")
    release = {"id": 7, "sourcePath": str(path), "sourceName": path.name,
               "contentHash": digest(path), "fileSize": path.stat().st_size, "status": "draft",
               "scheduledFor": None, "createdAt": "2026-09-01T00:00:00Z",
               "updatedAt": "2026-09-01T00:00:00Z"}
    upload = {"id": 30, "releaseId": 7, "platform": "youtube", "accountKey": "@creator",
              "draftTitle": "Title", "draftDescription": "Description", "draftTags": "game",
              "status": "pending", "externalUrl": "", "textRevisionId": 5,
              "updatedAt": "2026-09-01T00:00:00Z"}
    return {"releases": [release], "uploads": [upload],
            "revisions": {5: {"id": 5, "body": "Approved"}},
            "observations": [{"id": 4, "uploadId": 30, "status": "pending"}],
            "duplicate_destinations": []}


class MigrationDeltaTests(unittest.TestCase):
    def test_read_only_comparison_reports_changed_and_new_homebase_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_snapshot(root / "clip.mp4")
            with Store(root / "snapshot.sqlite") as destination:
                apply(source, destination)
                self.assertTrue(all(not value for value in compare(source, destination.db).values()))
                updated = source_snapshot(root / "clip.mp4")
                updated["releases"][0]["status"] = "reserved"
                updated["uploads"][0]["status"] = "uploading"
                updated["revisions"][5]["body"] = "Changed"
                updated["observations"][0]["status"] = "verified"
                added = dict(updated["releases"][0], id=8, sourceName="next.mp4",
                             sourcePath=str(root / "next.mp4"), contentHash="b" * 64)
                updated["releases"].append(added)
                delta = compare(updated, destination.db)
                self.assertEqual(delta["new_release_ids"], [8])
                self.assertEqual(delta["changed_release_ids"], [7])
                self.assertEqual(delta["changed_uploads"], [[7, "youtube"]])
                self.assertEqual(delta["changed_revisions"], [[7, "youtube"]])
                self.assertEqual(delta["changed_observations"], [[7, "youtube"]])
                self.assertEqual(destination.db.execute("SELECT count(*) FROM release").fetchone()[0], 1)

    def test_merge_copy_preserves_existing_and_imports_homebase_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_video = root / "standalone.mp4"
            source_video.write_bytes(b"standalone clip")
            existing_path = root / "existing.sqlite"
            with Store(existing_path) as existing:
                existing.import_file(source_video)
            homebase = source_snapshot(root / "homebase.mp4")
            merged_path = root / "merged.sqlite"
            result = merge_copy(homebase, existing_path, merged_path)
            self.assertEqual(result["release_count"], 2)
            self.assertEqual(list(root.glob(".video-drop-merge-*")), [])
            with Store(existing_path) as unchanged:
                self.assertEqual(unchanged.db.execute("SELECT count(*) FROM release").fetchone()[0], 1)
            with Store(merged_path) as merged:
                self.assertEqual(merged.db.execute("SELECT count(*) FROM release").fetchone()[0], 2)
                self.assertEqual(merged.db.execute("SELECT homebase_id FROM release WHERE homebase_id=7").fetchone()[0], 7)
                self.assertTrue(all(not value for value in compare(homebase, merged.db).values()))

    def test_merge_copy_rejects_media_overlap_without_touching_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_video = root / "standalone.mp4"
            source_video.write_bytes(b"standalone clip")
            existing_path = root / "existing.sqlite"
            with Store(existing_path) as existing:
                existing.import_file(source_video)
            homebase = source_snapshot(root / "homebase.mp4")
            (root / "homebase.mp4").write_bytes(b"standalone clip")
            homebase["releases"][0]["contentHash"] = digest(source_video)
            homebase["releases"][0]["fileSize"] = source_video.stat().st_size
            candidate = root / "conflict.sqlite"
            with self.assertRaisesRegex(ValueError, "Media already exists"):
                merge_copy(homebase, existing_path, candidate)
            self.assertFalse(candidate.exists())
            self.assertEqual(list(root.glob(".video-drop-merge-*")), [])
            with Store(existing_path) as unchanged:
                self.assertEqual(unchanged.db.execute("SELECT count(*) FROM release").fetchone()[0], 1)

    def test_import_rejects_media_changed_after_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_snapshot(root / "clip.mp4")
            self.assertEqual(media_issues(source["releases"]), ([], []))
            (root / "clip.mp4").write_bytes(b"different clip")
            with Store(root / "snapshot.sqlite") as destination:
                with self.assertRaisesRegex(ValueError, "missing or changed"):
                    apply(source, destination)
                self.assertEqual(destination.db.execute("SELECT count(*) FROM release").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
