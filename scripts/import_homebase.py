"""Copy Homebase Video Drop records into a separate local SQLite database.

Read only against Homebase. Never starts uploads or marks a publication complete.
Run without --apply to check counts and destination conflicts first.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_drop.core import Store  # noqa: E402


def rows(db: sqlite3.Connection, table: str) -> list[dict]:
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}"')]


def inspect(source: sqlite3.Connection) -> dict:
    releases = rows(source, "VideoRelease")
    uploads = rows(source, "VideoUpload")
    revisions = rows(source, "PublicTextRevision")
    observations = rows(source, "VideoPublicationObservation")
    duplicates = [(key, count) for key, count in Counter((x["releaseId"], x["platform"]) for x in uploads).items() if count > 1]
    return {
        "releases": releases, "uploads": uploads, "revisions": {r["id"]: r for r in revisions},
        "observations": observations, "duplicate_destinations": duplicates,
        "missing_media": [r["id"] for r in releases if not Path(r["sourcePath"]).is_file()],
        "status_counts": dict(Counter(r["status"] for r in releases)),
    }


def apply(source: dict, destination: Store, *, into_copy: bool = False) -> None:
    if source["duplicate_destinations"]:
        raise ValueError("Homebase has multiple destinations for one release/platform")
    if source.get("missing_media"):
        raise ValueError("Homebase has releases with missing source media")
    if not into_copy and destination.db.execute("SELECT 1 FROM release LIMIT 1").fetchone():
        raise ValueError("Destination is not empty; use a new database for a safe import")
    if into_copy:
        if destination.db.execute("SELECT 1 FROM release WHERE homebase_id IS NOT NULL LIMIT 1").fetchone():
            raise ValueError("Destination already contains Homebase releases")
        existing_hashes = {row[0] for row in destination.db.execute("SELECT sha256 FROM release")}
        repeated = sorted(existing_hashes & {row["contentHash"] for row in source["releases"]})
        if repeated:
            raise ValueError(f"Media already exists in both systems ({len(repeated)} matching hashes)")
    releases = source["releases"]
    uploads = source["uploads"]
    revisions = source["revisions"]
    observations = source["observations"]
    with destination.db:
        for old in releases:
            destination.db.execute("""INSERT INTO release(homebase_id,source_path,source_name,sha256,file_size,status,scheduled_at,analysis_json,legacy_json,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                old["id"], old["sourcePath"], old["sourceName"], old["contentHash"], old["fileSize"],
                old["status"], old["scheduledFor"], json.dumps({"transcript": old.get("transcriptText", ""), "summary": old.get("analysisSummary", "")}),
                json.dumps(old, default=str), old["createdAt"], old["updatedAt"],
            ))
            new_id = destination.db.execute("SELECT id FROM release WHERE homebase_id=?", (old["id"],)).fetchone()[0]
            destination._event(new_id, None, "homebase_imported", {"homebase_id": old["id"]})
            for upload in (u for u in uploads if u["releaseId"] == old["id"]):
                revision = revisions.get(upload.get("textRevisionId"))
                legacy = {"upload": upload, "text_revision": revision,
                          "observations": [o for o in observations if o["uploadId"] == upload["id"]]}
                # Exact revision evidence is preserved, but authorizations are not carried
                # into a new publisher. They must be re-reviewed after cutover.
                destination.db.execute("""INSERT INTO destination(release_id,platform,account,title,description,tags,status,external_url,legacy_json,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                    new_id, upload["platform"], upload["accountKey"], upload["draftTitle"],
                    upload["draftDescription"], upload["draftTags"], upload["status"],
                    upload["externalUrl"], json.dumps(legacy, default=str), upload["updatedAt"],
                ))
        for old in releases:
            new_id = destination.db.execute("SELECT id FROM release WHERE homebase_id=?", (old["id"],)).fetchone()[0]
            destination._event(new_id, None, "migration_snapshot", {"homebase_id": old["id"]})


def compare(source: dict, destination: sqlite3.Connection) -> dict:
    """Read-only delta report for a prior snapshot, without overwriting edits."""
    releases = {row["homebase_id"]: row for row in rows(destination, "release")
                if row["homebase_id"] is not None}
    homebase_id_by_release_id = {row["id"]: homebase_id for homebase_id, row in releases.items()}
    current_releases = {row["id"]: row for row in source["releases"]}
    release_ids = set(releases) & set(current_releases)
    destination_uploads = {}
    for row in rows(destination, "destination"):
        homebase_id = homebase_id_by_release_id.get(row["release_id"])
        if homebase_id is not None:
            destination_uploads[(homebase_id, row["platform"])] = row
    current_uploads = {(row["releaseId"], row["platform"]): row for row in source["uploads"]}
    upload_keys = set(destination_uploads) & set(current_uploads)
    observations_by_upload = defaultdict(list)
    for row in source["observations"]:
        observations_by_upload[row["uploadId"]].append(row)
    def old_upload(key: tuple[int, str]) -> dict:
        return json.loads(destination_uploads[key]["legacy_json"])
    return {
        "new_release_ids": sorted(set(current_releases) - set(releases)),
        "missing_source_release_ids": sorted(set(releases) - set(current_releases)),
        "changed_release_ids": sorted(release_id for release_id in release_ids
                                      if json.loads(releases[release_id]["legacy_json"]) != current_releases[release_id]),
        "new_uploads": sorted([list(key) for key in set(current_uploads) - set(destination_uploads)]),
        "missing_source_uploads": sorted([list(key) for key in set(destination_uploads) - set(current_uploads)]),
        "changed_uploads": sorted([list(key) for key in upload_keys if
                                   old_upload(key)["upload"] != current_uploads[key]]),
        "changed_revisions": sorted([list(key) for key in upload_keys if
                                     old_upload(key)["text_revision"] !=
                                     source["revisions"].get(current_uploads[key].get("textRevisionId"))]),
        "changed_observations": sorted([list(key) for key in upload_keys if
                                        sorted(old_upload(key)["observations"], key=lambda row: row["id"]) !=
                                        sorted(observations_by_upload[current_uploads[key]["id"]],
                                               key=lambda row: row["id"])]),
    }


def merge_copy(source: dict, existing_path: Path, destination_path: Path) -> dict:
    """Publish a verified merged copy without exposing an incomplete candidate."""
    existing_path = existing_path.resolve(strict=True)
    destination_path = destination_path.resolve()
    if destination_path.exists():
        raise ValueError("Destination already exists; choose a new path")
    if destination_path == existing_path:
        raise ValueError("Existing and destination databases must differ")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".video-drop-merge-", suffix=".sqlite", dir=destination_path.parent)
    os.close(handle)
    staged_path = Path(temporary_name)
    try:
        existing = sqlite3.connect(f"file:{existing_path.as_posix()}?mode=ro", uri=True)
        copy = sqlite3.connect(staged_path)
        try:
            existing.backup(copy)
        finally:
            copy.close()
            existing.close()
        with Store(staged_path) as destination:
            apply(source, destination, into_copy=True)
            delta = compare(source, destination.db)
            if any(delta.values()):
                raise ValueError(f"Merged copy differs from Homebase snapshot: {delta}")
            if destination.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Merged copy failed SQLite integrity check")
            result = {"release_count": destination.db.execute("SELECT count(*) FROM release").fetchone()[0],
                      "destination_count": destination.db.execute("SELECT count(*) FROM destination").fetchone()[0],
                      "homebase_release_count": len(source["releases"]), "homebase_upload_count": len(source["uploads"])}
            destination.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            destination.db.execute("PRAGMA journal_mode=DELETE")
        # The hard link fails if another process created the target meanwhile.
        # Both paths are in the same directory, so it does not copy media or data.
        os.link(staged_path, destination_path)
        return result
    finally:
        staged_path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(staged_path) + suffix).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Homebase dev.db path")
    parser.add_argument("--dest", type=Path, required=True, help="New Video Drop SQLite path")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="Write the separate destination database")
    action.add_argument("--compare", action="store_true", help="Read-only delta against an existing snapshot")
    action.add_argument("--merge-copy", action="store_true", help="Merge into a new copy of an existing standalone database")
    parser.add_argument("--existing", type=Path, help="Existing standalone database for --merge-copy; never modified")
    args = parser.parse_args()
    source_path = args.source.resolve(strict=True)
    if args.dest.resolve() == source_path:
        parser.error("Source and destination must differ")
    if args.merge_copy and (not args.existing or args.existing.resolve() == source_path):
        parser.error("--merge-copy needs a separate --existing standalone database")
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute("BEGIN")
        report = inspect(source)
        source.commit()
    finally:
        source.close()
    print(json.dumps({
        "releases": len(report["releases"]), "uploads": len(report["uploads"]),
        "observations": len(report["observations"]),
        "duplicate_destinations": report["duplicate_destinations"],
        "missing_media_release_ids": report["missing_media"],
        "status_counts": report["status_counts"], "applied": args.apply,
        "merged_copy": args.merge_copy,
    }, indent=2))
    if args.compare:
        if not args.dest.is_file():
            parser.error("Snapshot database is missing")
        snapshot = sqlite3.connect(f"file:{args.dest.resolve().as_posix()}?mode=ro", uri=True)
        snapshot.row_factory = sqlite3.Row
        try:
            print(json.dumps(compare(report, snapshot), indent=2))
        finally:
            snapshot.close()
    if args.apply:
        if args.dest.exists():
            parser.error("Destination already exists; use a new path to preserve rollback")
        with Store(args.dest) as destination:
            apply(report, destination)
            assert destination.db.execute("SELECT count(*) FROM release").fetchone()[0] == len(report["releases"])
            assert destination.db.execute("SELECT count(*) FROM destination").fetchone()[0] == len(report["uploads"])
    if args.merge_copy:
        print(json.dumps(merge_copy(report, args.existing, args.dest), indent=2))


if __name__ == "__main__":
    main()
