"""Post one authorized video to Threads on its own, only when Settings allow it.

Owner's rule: Post now shares Threads through Instagram's "Also share on…" Threads
switch in the same upload (scripts/phone_instagram.py); there is no separate Threads
post. Only when the Threads crosspost is off AND "Post Threads separately" is on does
a Post now release get this path: OneDrive share -> Threads composer -> Post.

Instagram's scheduler turns the Threads crosspost off, so a scheduled video's Threads
post must be scheduled natively in the Threads app (its + composer with the clip in
Photos, then Schedule). That native route is not built yet, and this script never posts
a scheduled video immediately. Every refusal happens before the phone is touched.

A final tap is recorded as unconfirmed before it happens, so a lost SideTap connection
cannot replay it. Without --commit, stop at the fully verified native composer.
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
from video_drop import phone_lock  # noqa: E402

from video_drop.core import Store, digest, threads_post_refusal  # noqa: E402
from video_drop.accounts import load_targets, require_target  # noqa: E402
from scripts import phone_youtube as share  # noqa: E402
from video_drop.phone.helpers import VideoSurfaceError  # noqa: E402
from video_drop.phone_focus import optional_focus  # noqa: E402

phone = share.phone


def _plain(text: str) -> str:
    # Threads swaps straight and curly apostrophes between releases ("What’s new?").
    return text.strip().replace("’", "'")


def composer_rows() -> list[dict]:
    """The composer's controls from the tree, once its attached clip is not playing.

    The caption is read back exactly, which OCR cannot do, and the driver refuses the tree
    while the clip plays: a composer that keeps playing stops the run before any final tap."""
    try:
        return phone.compact(phone.collect_texts(share.still_tree(driver=phone)))
    except VideoSurfaceError as exc:
        raise share.PhoneUploadError("Threads' composer kept playing the clip, so its text cannot be "
                                     "read back; nothing was posted") from exc


def unique(rows: list[dict], label: str, *, kind: str | None = None) -> dict:
    found = [row for row in rows if _plain(row.get("text", "")) == _plain(label)
             and (kind is None or row.get("type") == kind)]
    if len(found) != 1:
        raise share.PhoneUploadError(f"Expected one {label!r} in Threads, found {len(found)}")
    return found[0]


def release_input(store: Store, release_id: int) -> dict:
    release = store.release(release_id)
    if refusal := threads_post_refusal(release):
        raise share.PhoneUploadError(refusal)
    destination = next(d for d in release["destinations"] if d["platform"] == "threads")
    if destination["status"] != "pending":
        raise share.PhoneUploadError("Threads is already attempted; check the account before any retry")
    require_target(store.account_targets, "threads", destination["account"])
    if not destination["revision_hash"]:
        raise share.PhoneUploadError("Threads text was not approved in Auto iPhone Uploader")
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
    rows = composer_rows()
    field = unique(rows, "What's new?", kind="TextView")
    phone.long_press(field["x"], field["y"])
    paste = unique(composer_rows(), "Paste", kind="MenuItem")
    phone.tap(paste["x"], paste["y"])
    unique(composer_rows(), caption, kind="TextView")


def prepare(data: dict) -> None:
    phone.unlock()
    share.layout(refresh=True)
    share.open_source_file(data)
    share.choose_share_app("Threads", expected_bundle="com.burbn.barcelona")
    rows = composer_rows()
    unique(rows, "New thread")
    unique(rows, data["account"], kind="Link")
    unique(rows, "Remove video at attached item #1", kind="Button")
    paste_exact_caption(data["caption"])
    rows = composer_rows()
    unique(rows, data["caption"], kind="TextView")
    unique(rows, data["account"], kind="Link")
    unique(rows, "Remove video at attached item #1", kind="Button")
    unique(rows, "Post this thread", kind="Button")


@phone_lock.locked("Threads")
def run(release_id: int, db: Path, *, commit: bool = False) -> dict:
    """Use the native Threads composer for one confirmed release."""
    if commit and os.environ.get("VIDEO_DROP_TEST_MODE") == "1":
        raise share.PhoneUploadError("Posting is disabled in this test session")
    share.set_source_db(db)
    with Store(db, load_targets(db.parent)) as store:
        data = release_input(store, release_id)
        share.connect_sidetap()
        global phone
        phone = share.phone
        phone.unlock()
        with share.busy("Threads preparation", 600), optional_focus(phone, store.phone_checks()["doNotDisturb"]):
            for attempt in range(3):
                try:
                    prepare(data)
                    break
                except share.WDAError as exc:
                    if attempt == 2 or not share.recover_link():
                        raise share.PhoneUploadError("The phone link could not be restored for Threads; nothing was posted") from exc
                    time.sleep(1)
            if not commit:
                return {"kind": "ready", "platform": "threads", "releaseId": release_id}
            # Record uncertainty before the final tap. A WDA timeout after this
            # point can mean a successful post, so the script cannot replay it.
            store.mark_unconfirmed(release_id, "threads", expected_revision=data["revisionHash"])
            rows = composer_rows()
            unique(rows, data["caption"], kind="TextView")
            unique(rows, data["account"], kind="Link")
            unique(rows, "Remove video at attached item #1", kind="Button")
            post = unique(rows, "Post this thread", kind="Button")
            # Threads uploads after the tap and lands on its autoplaying feed: shield the link, read nothing.
            with share.busy("Threads upload", 30, linger=share.upload_linger(data["sizeBytes"])):
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
