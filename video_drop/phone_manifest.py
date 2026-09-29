"""Resolve a phone upload from the saved, approved release revision."""

from __future__ import annotations

from pathlib import Path

from .accounts import require_target
from .core import Store, digest


def youtube_input(store: Store, release_id: int) -> dict:
    release = store.release(release_id)
    destination = next(d for d in release["destinations"] if d["platform"] == "youtube")
    if destination["status"] != "pending":
        raise ValueError("YouTube was already attempted; check the channel before any retry")
    if not destination["revision_hash"]:
        raise ValueError("Confirm YouTube details in Automated iPhone Social Media Uploads before phone preparation")
    require_target(store.account_targets, "youtube", destination["account"])
    revision = store._revision_hash("youtube", destination["account"], destination["title"],
                                    destination["description"], destination["tags"], destination["visibility"])
    if revision != destination["revision_hash"]:
        raise ValueError("YouTube details changed after confirmation")
    source = Path(release["source_path"])
    if not source.is_file() or source.name != release["source_name"] or source.stat().st_size != release["file_size"]:
        raise ValueError("Source filename or size changed")
    if digest(source) != release["sha256"]:
        raise ValueError("Source content changed")
    if not destination["account"] or not destination["title"] or not destination["description"]:
        raise ValueError("YouTube account, title, and description are required")
    tags = [tag.strip() for tag in destination["tags"].split(",") if tag.strip()]
    if not tags:
        raise ValueError("YouTube tags are required")
    return {"releaseId": release_id, "filename": source.name, "sizeBytes": release["file_size"],
            "expectedAccount": destination["account"], "title": destination["title"],
            "description": destination["description"], "tags": tags,
            "visibility": destination["visibility"], "revisionHash": revision,
            "sourceSha256": release["sha256"], "deliveryMode": release["delivery_mode"],
            "scheduledAt": release["scheduled_at"]}


def verify_youtube_manifest(store: Store, manifest: dict) -> dict:
    if not isinstance(manifest, dict) or type(manifest.get("releaseId")) is not int:
        raise ValueError("Manifest needs a numeric releaseId")
    expected = youtube_input(store, manifest["releaseId"])
    for key in ("filename", "sizeBytes", "expectedAccount", "title", "description", "tags", "visibility"):
        if manifest.get(key) != expected[key]:
            raise ValueError(f"YouTube manifest {key} does not match the confirmed release")
    if manifest.get("revisionHash", expected["revisionHash"]) != expected["revisionHash"]:
        raise ValueError("YouTube manifest revision does not match the confirmed release")
    return expected
