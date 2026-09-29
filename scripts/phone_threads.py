"""Share one authorized video from OneDrive to Threads on the iPhone.

Threads is an immediate-post destination. A final tap is recorded as
unconfirmed before it happens, so a lost SideTap connection cannot replay it.
Without --commit, stop at the fully verified native composer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_drop.core import Store, digest  # noqa: E402
from video_drop.accounts import load_targets, require_target  # noqa: E402
from scripts import phone_youtube as share  # noqa: E402
from video_drop.phone_focus import upload_focus  # noqa: E402

phone = share.phone


def unique(rows: list[dict], label: str, *, kind: str | None = None) -> dict:
    found = [row for row in rows if row.get("text", "").strip() == label
             and (kind is None or row.get("type") == kind)]
    if len(found) != 1:
        raise share.PhoneUploadError(f"Expected one {label!r} in Threads, found {len(found)}")
    return found[0]


def release_input(store: Store, release_id: int) -> dict:
    release = store.release(release_id)
    destination = next(d for d in release["destinations"] if d["platform"] == "threads")
    if destination["status"] != "pending":
        raise share.PhoneUploadError("Threads is already attempted; check the account before any retry")
    require_target(store.account_targets, "threads", destination["account"])
    if not destination["revision_hash"]:
        raise share.PhoneUploadError("Threads text was not approved in Video Drop")
    revision = store._revision_hash("threads", destination["account"], destination["title"],
                                    destination["description"], destination["tags"], destination["visibility"])
    if revision != destination["revision_hash"]:
        raise share.PhoneUploadError("Threads text changed after approval")
    source = Path(release["source_path"])
    if source.name != release["source_name"] or not source.is_file() or source.stat().st_size != release["file_size"]:
        raise share.PhoneUploadError("Source filename or size changed")
    if digest(source) != release["sha256"]:
        raise share.PhoneUploadError("Source content changed")
    caption = destination["description"] or destination["title"]
    if not caption:
        raise share.PhoneUploadError("Threads caption is empty")
    return {"releaseId": release_id, "filename": source.name, "sizeBytes": release["file_size"],
            "account": destination["account"].lstrip("@"), "caption": caption,
            "revisionHash": destination["revision_hash"]}


def paste_exact_caption(caption: str) -> None:
    # WDA key injection has dropped or reordered letters when Threads opens
    # hashtag-topic suggestions. Clipboard Paste landed the exact text on the
    # observed phone; read back the whole field before any final action.
    phone.set_clipboard(caption)
    rows = share.screen()
    field = unique(rows, "What's new?", kind="TextView")
    phone.long_press(field["x"], field["y"])
    paste = unique(share.screen(), "Paste", kind="MenuItem")
    phone.tap(paste["x"], paste["y"])
    unique(share.screen(), caption, kind="TextView")


def prepare(data: dict) -> None:
    phone.unlock()
    share.layout(refresh=True)
    share.open_onedrive_file(data)
    share.choose_share_app("Threads", expected_bundle="com.burbn.barcelona")
    rows = share.screen()
    unique(rows, "New thread")
    unique(rows, data["account"], kind="Link")
    unique(rows, "Remove video at attached item #1", kind="Button")
    paste_exact_caption(data["caption"])
    rows = share.screen()
    unique(rows, data["caption"], kind="TextView")
    unique(rows, data["account"], kind="Link")
    unique(rows, "Remove video at attached item #1", kind="Button")
    unique(rows, "Post this thread", kind="Button")


def run(release_id: int, db: Path, *, commit: bool = False) -> dict:
    """Use the native Threads composer for one confirmed release."""
    if commit and os.environ.get("VIDEO_DROP_TEST_MODE") == "1":
        raise share.PhoneUploadError("Posting is disabled in this test session")
    with Store(db, load_targets(db.parent)) as store:
        data = release_input(store, release_id)
        share.connect_sidetap()
        global phone
        phone = share.phone
        phone.unlock()
        with upload_focus(phone):
            for attempt in range(3):
                try:
                    prepare(data)
                    break
                except share.WDAError as exc:
                    if attempt == 2 or share.sidetap_admin.up() != 0:
                        raise share.PhoneUploadError("SideTap could not restore Threads preparation; nothing posted") from exc
                    time.sleep(1)
            if not commit:
                return {"kind": "ready", "platform": "threads", "releaseId": release_id}
            # Record uncertainty before the final tap. A WDA timeout after this
            # point can mean a successful post, so the script cannot replay it.
            store.mark_unconfirmed(release_id, "threads", expected_revision=data["revisionHash"])
            rows = share.screen()
            unique(rows, data["caption"], kind="TextView")
            unique(rows, data["account"], kind="Link")
            unique(rows, "Remove video at attached item #1", kind="Button")
            post = unique(rows, "Post this thread", kind="Button")
            phone.tap(post["x"], post["y"])
            return {"kind": "unconfirmed", "platform": "threads", "releaseId": release_id,
                    "message": "Final tap sent; check native Threads receipt before any retry"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_id", type=int)
    parser.add_argument("--db", type=Path, default=ROOT / ".state" / "video-drop.sqlite")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.release_id, args.db, commit=args.commit)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"kind": "error", "message": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
