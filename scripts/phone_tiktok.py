"""Share one authorized video from OneDrive to TikTok on the iPhone and post it.

TikTok has no scheduler on the operator's account, so Schedule mode posts it
from this app at the slot (see video_drop/slot_posts.py) and Post now posts it
immediately. The flow is OneDrive -> iOS share sheet -> TikTok -> Video ->
Next -> description -> cover check -> Post.

Two controls are not in the accessibility tree and use measured points moved
by the PhoneLayout safe-area helpers:

- "Video" in TikTok's share sheet. The sheet is a remote view drawn by
  TikTok's extension; WDA sees only the host app behind it. The sheet sits on
  the bottom safe area and splits its width between its buttons, so the point
  keeps its height above that edge and scales across the width
  (``bottom_sheet_point``).
- "Next" in TikTok's editor. The clip plays there and a tree read can hang
  WDA, so the editor is never read. Next is pinned to the bottom-right safe
  area (``bottom_right_point``), like YouTube's trim Next.

Every other control comes from the live tree. Because the Next point is inside
the Post button of the screen that follows, TikTok is force-quit before each
run (no stale composer can be showing) and Next is tapped exactly once.

The cover must be the video's first frame, compared against a screenshot that
is kept as evidence; otherwise the run stops. A final tap is recorded as
unconfirmed before it happens and is never replayed. Without --commit, stop
at the fully checked composer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_drop.core import Store, digest, utc_now  # noqa: E402
from video_drop.accounts import load_targets, require_target  # noqa: E402
from video_drop.instagram_schedule import first_frame  # noqa: E402
from video_drop.phone_focus import optional_focus  # noqa: E402
from video_drop.phone_link import release_frozen_app  # noqa: E402
from video_drop.phone_ui import PhoneLayout  # noqa: E402
from video_drop.screens.snapshot import Element, Snapshot, elements_from_tree  # noqa: E402
from scripts import phone_youtube as share  # noqa: E402

phone = share.phone
TIKTOK_BUNDLE = "com.zhiliaoapp.musically"
DESCRIPTION = "Add description..."
# Measured on the 440 x 956 reference phone (iPhone 16 Pro Max).
VIDEO_POINT = (155.0, 828.0)
NEXT_POINT = (325.0, 896.0)
SHEET_SETTLE = 6.0
EDITOR_SETTLE = 10.0
# Recorded post screen vs its source: first frame 2.7; frames 1-10 s into the same clip 10.5-13.8.
COVER_LIMIT = 8.0


def release_input(store: Store, release_id: int) -> dict:
    release = store.release(release_id)
    destination = next(d for d in release["destinations"] if d["platform"] == "tiktok")
    if destination["status"] != "pending":
        raise share.PhoneUploadError("TikTok is already attempted; check the account before any retry")
    require_target(store.account_targets, "tiktok", destination["account"])
    if not destination["revision_hash"]:
        raise share.PhoneUploadError("TikTok text was not approved in Auto iPhone Uploader")
    revision = store._revision_hash("tiktok", destination["account"], destination["title"],
                                    destination["description"], destination["tags"], destination["visibility"])
    if revision != destination["revision_hash"]:
        raise share.PhoneUploadError("TikTok text changed after approval")
    source = Path(release["source_path"])
    if source.name != release["source_name"] or not source.is_file() or source.stat().st_size != release["file_size"]:
        raise share.PhoneUploadError("Source filename or size changed")
    if digest(source) != release["sha256"]:
        raise share.PhoneUploadError("Source content changed")
    caption = destination["description"]
    if not caption.strip():
        raise share.PhoneUploadError("TikTok caption is empty")
    if not re.search(r"(?<!\w)#fyp(?!\w)", caption, re.IGNORECASE):
        raise share.PhoneUploadError("TikTok caption must keep its #fyp hashtag")
    return {"releaseId": release_id, "filename": source.name, "sizeBytes": release["file_size"],
            "sourcePath": str(source), "account": destination["account"].lstrip("@"), "caption": caption,
            "revisionHash": destination["revision_hash"], "deliveryMode": release["delivery_mode"],
            "scheduledAt": release["scheduled_at"]}


def cover_reference(frame: Image.Image) -> Image.Image:
    """The source's first frame, shrunk once for repeated crops; flat frames cannot prove a cover."""
    frame = frame.convert("RGB")
    frame = frame.resize((360, max(1, round(360 * frame.height / frame.width))))
    if min(ImageStat.Stat(frame).stddev) < 20:
        raise share.PhoneUploadError("The first frame has too little detail to verify TikTok's cover; nothing was posted")
    return frame


def cover_match(screenshot: Image.Image, frame: Image.Image, preview: Element, edit_cover: Element,
                layout: PhoneLayout) -> dict:
    """Compare TikTok's cover card with the first frame, between its "Preview" and "Edit cover" overlays.

    The card fills its box with the centered frame. Its edges are a few points
    outside those two labels, so a small range of card sizes around them is
    searched and the closest one must still be within COVER_LIMIT.
    """
    frame = frame.convert("RGB")
    band = (edit_cover.left + 4, preview.top + preview.height + 4,
            edit_cover.left + edit_cover.width - 4, edit_cover.top - 4)
    if band[2] - band[0] < 40 or band[3] - band[1] < 40:
        raise ValueError("TikTok's cover card is too small to compare")
    sx, sy = screenshot.width / layout.width, screenshot.height / layout.height
    shown = screenshot.convert("RGB").crop((round(band[0] * sx), round(band[1] * sy),
                                            round(band[2] * sx), round(band[3] * sy))).resize((64, 64))
    fw, fh = frame.size
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for card_width in range(round(edit_cover.width), round(edit_cover.width) + 25, 2):
        for top_pad in range(0, 17, 2):
            for bottom_pad in range(0, 15, 2):
                top, bottom = preview.top - top_pad, edit_cover.top + edit_cover.height + bottom_pad
                scale = max(card_width / fw, (bottom - top) / fh)
                cx, cy = edit_cover.x, (top + bottom) / 2
                box = (fw / 2 + (band[0] - cx) / scale, fh / 2 + (band[1] - cy) / scale,
                       fw / 2 + (band[2] - cx) / scale, fh / 2 + (band[3] - cy) / scale)
                if box[0] < 0 or box[1] < 0 or box[2] > fw or box[3] > fh:
                    continue
                expected = frame.crop(tuple(round(value) for value in box)).resize((64, 64))
                score = sum(ImageStat.Stat(ImageChops.difference(shown, expected)).mean) / 3
                if best is None or score < best[0]:
                    best = score, (edit_cover.x - card_width / 2, top, edit_cover.x + card_width / 2, bottom)
    if best is None:
        raise ValueError("TikTok's cover card does not fit the video frame")
    score, card = best
    if score > COVER_LIMIT:
        raise ValueError(f"TikTok's cover is not the first frame (difference {score:.1f})")
    return {"difference": round(score, 1), "card": [round(value, 1) for value in card]}


def post_button(snapshot: Snapshot) -> Element:
    """The one Post button a finger can reach: on screen and not under the keyboard."""
    keyboards = [e for e in snapshot.elements if e.type == "Keyboard" and e.height > 0]
    keyboard_top = min((e.top for e in keyboards), default=snapshot.height)
    found = {(e.left, e.top, e.width, e.height): e for e in snapshot.elements
             if e.type == "Button" and e.label == "Post"
             and 0 <= e.left and e.left + e.width <= snapshot.width
             and 0 <= e.top and e.top + e.height <= keyboard_top}
    if len(found) != 1:
        raise share.PhoneUploadError(f"Expected one reachable TikTok Post button, found {len(found)}; nothing was posted")
    return next(iter(found.values()))


def one(snapshot: Snapshot, label: str, kind: str) -> Element:
    found = {(e.left, e.top, e.width, e.height): e for e in snapshot.elements
             if e.type == kind and e.label == label}
    if len(found) != 1:
        raise share.PhoneUploadError(f"Expected one {label!r} in TikTok, found {len(found)}")
    element = next(iter(found.values()))
    if not (0 <= element.x <= snapshot.width and 0 <= element.y <= snapshot.height):
        raise share.PhoneUploadError(f"TikTok {label!r} is outside the screen")
    return element


def composer_ready(snapshot: Snapshot, caption: str) -> Element:
    """The post screen with exactly the approved caption and one reachable Post button."""
    if snapshot.app != TIKTOK_BUNDLE:
        raise share.PhoneUploadError("TikTok is not in front; nothing was posted")
    field = one(snapshot, DESCRIPTION, "TextView")
    if field.value != caption:
        raise share.PhoneUploadError(f"TikTok description does not match the approved text: {field.value!r}")
    one(snapshot, "Edit cover", "Button")
    return post_button(snapshot)


def live() -> Snapshot:
    layout = share.layout()
    return Snapshot(layout.width, layout.height, elements_from_tree(phone.ui_tree()),
                    app=str(phone.current_app().get("bundleId", "")))


def wait_for_post_screen(timeout: float = 30) -> Snapshot:
    deadline = time.monotonic() + timeout
    while True:
        snapshot = live()
        try:
            one(snapshot, DESCRIPTION, "TextView")
            one(snapshot, "Edit cover", "Button")
            one(snapshot, "Preview", "StaticText")
            return snapshot
        except share.PhoneUploadError:
            if time.monotonic() >= deadline:
                raise share.PhoneUploadError("TikTok's post screen did not appear after Next; nothing was posted")
            time.sleep(1)


def evidence_stem(evidence_dir: Path, release_id: int) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    return evidence_dir / f"tiktok-{release_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"


def open_share_sheet(data: dict) -> None:
    """Retryable part: nothing in TikTok is open yet."""
    phone.unlock()
    share.layout(refresh=True)
    # A composer left open by an earlier stop could sit under the measured Next point.
    phone.close_app(TIKTOK_BUNDLE)
    share.open_onedrive_file(data)


def compose(data: dict, frame: Image.Image, evidence_dir: Path) -> dict:
    """Open TikTok's composer, prove the cover, and enter the caption. Never retried."""
    layout = share.layout()
    share.choose_share_app("TikTok")
    time.sleep(SHEET_SETTLE)
    phone.tap(*layout.bottom_sheet_point(*VIDEO_POINT))
    deadline = time.monotonic() + 30
    while phone.current_app().get("bundleId") != TIKTOK_BUNDLE:
        if time.monotonic() >= deadline:
            raise share.PhoneUploadError("TikTok did not open from its share sheet; nothing was posted")
        time.sleep(0.5)
    share.stage("tiktok_editor")
    time.sleep(EDITOR_SETTLE)
    phone.tap(*layout.bottom_right_point(*NEXT_POINT))
    snapshot = wait_for_post_screen()
    share.stage("tiktok_post_screen")

    # Cover gate: first frame, proven from a stored screenshot, before any text or final tap.
    png = phone.screenshot()
    stem = evidence_stem(evidence_dir, data["releaseId"])
    stem.with_name(stem.name + "-cover.png").write_bytes(png)
    with Image.open(BytesIO(png)) as screenshot:
        if abs(screenshot.width / screenshot.height - layout.width / layout.height) > 0.01:
            raise share.PhoneUploadError("TikTok screenshot does not match the iPhone screen; nothing was posted")
        try:
            match = cover_match(screenshot, frame, one(snapshot, "Preview", "StaticText"),
                                one(snapshot, "Edit cover", "Button"), layout)
        except ValueError as exc:
            raise share.PhoneUploadError(f"{exc}; nothing was posted") from exc
    record = {"releaseId": data["releaseId"], "platform": "tiktok", "revisionHash": data["revisionHash"],
              "cover": match, "screenshot": stem.name + "-cover.png"}
    stem.with_name(stem.name + "-cover.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    field = one(snapshot, DESCRIPTION, "TextView")
    if field.value not in ("", DESCRIPTION):
        raise share.PhoneUploadError("TikTok's description already has text; nothing was posted")
    phone.tap(field.x, field.y)
    time.sleep(1.5)
    phone.type_text(data["caption"])
    time.sleep(2)
    composer_ready(live(), data["caption"])
    return {**record, "evidence": str(stem.with_name(stem.name + "-cover.png"))}


def run(release_id: int, db: Path, *, commit: bool = False, not_after: datetime | None = None) -> dict:
    """Use the native TikTok composer for one confirmed release."""
    if commit and os.environ.get("VIDEO_DROP_TEST_MODE") == "1":
        raise share.PhoneUploadError("Posting is disabled in this test session")
    with Store(db, load_targets(db.parent)) as store:
        data = release_input(store, release_id)
        try:
            frame = cover_reference(first_frame(Path(data["sourcePath"])))
        except ValueError as exc:
            raise share.PhoneUploadError(f"{exc}; nothing was posted") from exc
        share.connect_sidetap()
        global phone
        phone = share.phone
        phone.unlock()
        with optional_focus(phone, store.phone_checks()["doNotDisturb"]):
            for attempt in range(3):
                try:
                    open_share_sheet(data)
                    break
                except share.WDAError as exc:
                    if attempt == 2 or not share.recover_link():
                        raise share.PhoneUploadError("SideTap could not restore TikTok preparation; nothing posted") from exc
                    time.sleep(1)
            try:
                evidence = compose(data, frame, db.parent / "evidence")
            except share.WDAError as exc:
                raise share.PhoneUploadError("The phone link dropped while TikTok was open; nothing was posted") from exc
            if not commit:
                return {"kind": "ready", "platform": "tiktok", "releaseId": release_id, "cover": evidence["cover"]}
            if not_after is not None and utc_now() > not_after:
                raise share.PhoneUploadError("The slot's posting window closed during preparation; nothing was posted")
            composer_ready(live(), data["caption"])
            # Record uncertainty before the final tap. A WDA timeout after this
            # point can mean a successful post, so the script cannot replay it.
            store.mark_unconfirmed(release_id, "tiktok", expected_revision=data["revisionHash"])
            post = composer_ready(live(), data["caption"])
            phone.tap(post.x, post.y)
            try:
                # TikTok opens its playing feed after posting, which can freeze WDA; leave it.
                time.sleep(20)
                from phone_harness import device
                release_frozen_app(device.ios_path())
            except Exception:
                pass
            return {"kind": "unconfirmed", "platform": "tiktok", "releaseId": release_id,
                    "cover": evidence["cover"],
                    "message": "Final tap sent; check the TikTok profile for the post before any retry"}


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
