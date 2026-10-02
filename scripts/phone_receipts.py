"""Read native receipts back for one release and record the ones the phone proves.

    python scripts/phone_receipts.py RELEASE_ID [--platform instagram|threads|facebook|tiktok|youtube|all]
                                               [--posts-before N] [--no-record]

Only destinations marked unconfirmed (a final tap was sent) are checked, in posting
order. Every read is a static, non-video screen: a profile header, a profile grid, a
Scheduled content list. An app that lands on an autoplaying feed (Instagram after
Share, Threads) is never read there: the Profile tab is tapped blind at its measured
place first, and screenshots come from go-ios (video_drop.phone_link.pixels), never WDA.
Destinations whose screens were never recorded are reported with the exact screens a
live session must capture (video_drop.receipts.ROUTES), and the phone is not touched
for them.

Recording: an Instagram schedule goes through Store.record_observed_schedule. A posted
receipt goes through Store.record_receipt with the evidence JSON's file URI, which core
checks against the release and logs as native_post_observed (it counts toward the
unattended streak).

Instagram Post now needs the profile's post count from before the upload: the
baseline scripts/phone_instagram.py saves at its account check (receipts.save_baseline),
or --posts-before for an older release.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_drop import phone_lock  # noqa: E402

from video_drop import receipts as rc  # noqa: E402
from video_drop.accounts import load_targets  # noqa: E402
from video_drop.core import POST_ORDER, Store, digest  # noqa: E402
from video_drop.instagram_schedule import first_frame  # noqa: E402
from video_drop.phone_link import release_frozen_app  # noqa: E402
from video_drop.screens.snapshot import elements_from_tree  # noqa: E402
from scripts import phone_youtube as share  # noqa: E402

phone = share.phone
INSTAGRAM_BUNDLE = "com.burbn.instagram"
THREADS_BUNDLE = "com.burbn.barcelona"
# Tab centers on the 440 x 956 reference phone; both bars sit on the bottom safe area.
INSTAGRAM_PROFILE_TAB = (370.5, 904.0)  # profile-tab, fixtures instagram/profile-header-*
THREADS_PROFILE_TAB = (372.5, 904.0)  # Profile, fixtures threads/posting-banner-*
LAUNCH_SETTLE = 4.0
TAB_SETTLE = 2.5
SCHEDULED_ROW = "Scheduled content"
MENU_SCROLLS = 4


class Stop(RuntimeError):
    pass


# ---- inputs -----------------------------------------------------------------------


def targets(release: dict, platform: str = "all") -> list[str]:
    """Destinations waiting for a receipt, in posting order."""
    waiting = {d["platform"] for d in release["destinations"] if d["status"] == "unconfirmed"}
    if platform != "all":
        if platform not in POST_ORDER:
            raise Stop(f"Unknown platform {platform!r}")
        return [platform] if platform in waiting else []
    return [p for p in POST_ORDER if p in waiting]


def destination(release: dict, platform: str) -> dict:
    return next(d for d in release["destinations"] if d["platform"] == platform)


def expected_caption(release: dict, platform: str) -> str:
    """A crosspost carries Instagram's caption; a separate post carries its own approved text."""
    carrier = "instagram" if platform in release.get("instagramCrossposts", []) else platform
    source = destination(release, carrier)
    return source["description"] or source["title"]


def source_frame(release: dict) -> Image.Image:
    source = Path(release["source_path"])
    if not source.is_file() or digest(source) != release["sha256"]:
        raise Stop("Source video changed or is missing; its first frame cannot identify the post")
    return first_frame(source)


# ---- phone ------------------------------------------------------------------------


def elements():
    # A profile can autoplay a video post: wait for a still screen; the driver refuses the read
    # while it plays, and a screen that keeps playing ends the check (VideoSurfaceError).
    return elements_from_tree(share.still_tree(driver=phone))


def screenshot() -> tuple[bytes, Image.Image]:
    png = share.screen_pixels()
    return png, Image.open(BytesIO(png)).convert("RGB")


def open_profile(bundle: str, tab: tuple[float, float]) -> None:
    """Launch without asking WDA anything (the app may resume on a playing feed), then tap Profile blind."""
    phone.open_app(bundle)
    time.sleep(LAUNCH_SETTLE)
    point = share.layout().bottom_sheet_point(*tab)
    phone.tap(*point)
    time.sleep(TAB_SETTLE)
    # Instagram reopens the profile where it was last scrolled, hiding the header's post count
    # (measured 2026-10-01: 0 counts found). A second tap on the active tab scrolls to the top;
    # TAB_SETTLE apart, so Instagram never reads it as the double tap that switches accounts.
    phone.tap(*point)
    time.sleep(TAB_SETTLE)


def leave() -> None:
    try:
        from video_drop.phone import device
        release_frozen_app(device.ios_path())
    except Exception:
        pass


def evidence_stem(state: Path, release_id: int, platform: str) -> Path:
    folder = state / "evidence"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"release{release_id}-{platform}-receipt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"


def record_post(store: Store, release_id: int, platform: str, verified: dict, stem: Path, *, record: bool) -> dict:
    """Keep the evidence, then hand core a posted receipt; say so honestly when core cannot take it yet."""
    evidence = stem.with_suffix(".json")
    evidence.write_text(json.dumps({"releaseId": release_id, "platform": platform, "choice": "posted",
                                    "screenshot": stem.with_suffix(".png").name if stem.with_suffix(".png").is_file() else None,
                                    **verified}, indent=2, ensure_ascii=False), encoding="utf-8")
    result = {"platform": platform, "releaseId": release_id, "evidence": str(evidence),
              "verification": verified["verification"]}
    if not record:
        return {"kind": "verified", **result}
    try:
        store.record_receipt(release_id, platform, evidence.as_uri())
    except ValueError as exc:
        return {"kind": "verified_unrecorded", **result,
                "message": f"The phone shows the post, but core did not record it: {exc}"}
    return {"kind": "recorded", **result}


# ---- platforms ----------------------------------------------------------------------


def instagram_post(store: Store, release: dict, state: Path, *, posts_before: int | None, record: bool) -> dict:
    release_id = release["id"]
    if posts_before is None:
        saved = rc.load_baseline(state, release_id, "instagram")
        posts_before = saved.get("posts") if saved else None
    if posts_before is None:
        return {"kind": "unverified", "platform": "instagram", "releaseId": release_id,
                "message": "No pre-upload post count was saved for this release; pass --posts-before"}
    frame = source_frame(release)
    open_profile(INSTAGRAM_BUNDLE, INSTAGRAM_PROFILE_TAB)
    header = elements()
    rc.instagram_profile(header)  # the profile header, or stop before any swipe
    distance = rc.instagram_grid_scroll(header, share.layout())
    if distance:
        layout = share.layout()
        start = layout.height * 0.8
        phone.swipe(layout.width / 2, start, layout.width / 2, start - distance, 1.0)
        time.sleep(1.5)
    grid = elements()
    png, image = screenshot()
    stem = evidence_stem(state, release_id, "instagram")
    stem.with_suffix(".png").write_bytes(png)
    verified = rc.verified_instagram_post(header, grid, image, share.layout(), frame,
                                          account=destination(release, "instagram")["account"],
                                          posts_before=int(posts_before))
    return record_post(store, release_id, "instagram", verified, stem, record=record)


def open_scheduled_content() -> None:
    """Best effort, never recorded: Profile -> settings & activity -> Scheduled content."""
    items = elements()
    menu = [e for e in items if e.type == "Button" and e.name == "profile-more-button"]
    if len(menu) != 1:
        raise Stop("Instagram's settings & activity button is not on the profile")
    phone.tap(menu[0].x, menu[0].y)
    time.sleep(2)
    layout = share.layout()
    for _ in range(MENU_SCROLLS + 1):
        rows = [e for e in elements() if e.label == SCHEDULED_ROW and layout.contains({"x": e.x, "y": e.y})]
        if len(rows) == 1:
            phone.tap(rows[0].x, rows[0].y)
            time.sleep(2.5)
            return
        phone.swipe(layout.width / 2, layout.height * 0.7, layout.width / 2, layout.height * 0.35, 0.8)
        time.sleep(1.2)
    raise Stop("Instagram's menu has no Scheduled content row (navigation not recorded yet)")


def instagram_schedule(store: Store, release: dict, state: Path) -> dict:
    release_id = release["id"]
    open_profile(INSTAGRAM_BUNDLE, INSTAGRAM_PROFILE_TAB)
    account = rc.instagram_profile(elements())["account"]
    open_scheduled_content()
    # Exact rows from the tree for the record, never OCR of a playing screen.
    rows = phone.compact(phone.collect_texts(share.still_tree(driver=phone)))
    png, _ = screenshot()
    stem = evidence_stem(state, release_id, "instagram")
    image = stem.with_suffix(".png")
    image.write_bytes(png)
    stem.with_suffix(".json").write_text(json.dumps({"rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    store.record_observed_schedule(release_id, "instagram", account=account, native_rows=rows,
                                   device_time_zone=store.time_zone().key, screen_info=phone.screen_info(),
                                   evidence_image=image)
    return {"kind": "recorded", "platform": "instagram", "releaseId": release_id, "evidence": str(image),
            "verification": "native_caption_time_first_frame"}


def threads_post(store: Store, release: dict, state: Path, *, record: bool) -> dict:
    release_id = release["id"]
    open_profile(THREADS_BUNDLE, THREADS_PROFILE_TAB)
    items = elements()
    stem = evidence_stem(state, release_id, "threads")
    stem.with_suffix(".png").write_bytes(screenshot()[0])
    verified = rc.threads_newest_post(items, share.layout(), account=destination(release, "threads")["account"],
                                      caption=expected_caption(release, "threads"))
    return record_post(store, release_id, "threads", verified, stem, record=record)


def check(store: Store, release: dict, platform: str, state: Path, *, posts_before: int | None,
          record: bool) -> dict:
    route = rc.receipt_route(platform, release["delivery_mode"])
    if route["status"] != "verify":
        return {"kind": "needs_recording", "platform": platform, "releaseId": release["id"],
                "plan": route["plan"], "record": route["record"]}
    try:
        if platform == "instagram" and release["delivery_mode"] == "schedule":
            if not record:
                return {"kind": "skipped", "platform": platform, "releaseId": release["id"],
                        "message": "Scheduled content is verified inside core; run without --no-record"}
            return instagram_schedule(store, release, state)
        if platform == "instagram":
            return instagram_post(store, release, state, posts_before=posts_before, record=record)
        return threads_post(store, release, state, record=record)
    except (rc.ReceiptError, Stop, ValueError, share.PhoneUploadError, share.WDAError) as exc:
        # Before leave(): the sweep's 13:05 Instagram read failed on 2026-10-02 and left no
        # trace of what the phone showed; the 13:09 read of the same post then verified it.
        from video_drop import failure_capture
        failure_capture.capture(state, f"receipt {platform}", exc)
        return {"kind": "unverified", "platform": platform, "releaseId": release["id"], "message": str(exc)}
    finally:
        leave()


@phone_lock.locked("receipt check")
def run(release_id: int, db: Path, *, platform: str = "all", posts_before: int | None = None,
        record: bool = True) -> dict:
    with Store(db, load_targets(db.parent)) as store:
        release = store.release(release_id)
        waiting = targets(release, platform)
        results = []
        needs_phone = [p for p in waiting if rc.receipt_route(p, release["delivery_mode"])["status"] == "verify"]
        if needs_phone:
            share.connect_sidetap()
            global phone
            phone = share.phone
            phone.unlock()
            share.layout(refresh=True)
        with share.busy("Receipts", 600) if needs_phone else contextlib.nullcontext():
            for name in waiting:
                results.append(check(store, store.release(release_id), name, db.parent,
                                     posts_before=posts_before if name == "instagram" else None, record=record))
        return {"releaseId": release_id, "checked": waiting, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("release_id", type=int)
    parser.add_argument("--platform", default="all", choices=("all", *POST_ORDER))
    parser.add_argument("--db", type=Path, default=ROOT / ".state" / "video-drop.sqlite")
    parser.add_argument("--posts-before", type=int, help="Instagram's post count before the upload")
    parser.add_argument("--no-record", action="store_true", help="verify and keep evidence, but change nothing")
    args = parser.parse_args()
    print(json.dumps(run(args.release_id, args.db, platform=args.platform, posts_before=args.posts_before,
                         record=not args.no_record), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"kind": "error", "message": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
