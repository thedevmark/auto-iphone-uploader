"""Deterministic OneDrive -> YouTube Short upload through SideTap/WDA.

Scheduled runs stop at YouTube's Upload Short button until the native scheduler
is verified. A confirmed Post now run may tap Upload Short once; its result
remains unconfirmed until a native receipt is checked. No model, browser
session, or paid API is used by this script. An unexpected screen fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_drop.phone_ui import (PhoneLayout, filled_radio, first_frame_point, share_app_position, share_rail_y,
                                  size_shown, youtube_identity, youtube_page_account)
from video_drop.screens.labels import Labels
from video_drop.screens.matcher import MatchError, find
from video_drop.screens.model import Locator
from video_drop.screens.snapshot import Element, Snapshot, elements_from_tree
from video_drop.youtube_nav import open_tabs
from video_drop.phone_focus import FocusError, optional_focus
from video_drop.core import Store
from video_drop.phone_manifest import verify_youtube_manifest, youtube_input
from video_drop.accounts import load_targets
from video_drop.sidetap_root import sidetap_root
from video_drop.phone_link import recover, release_frozen_app


def _sidetap_root() -> Path:
    return sidetap_root()


SIDETAP_SRC = _sidetap_root() / "src"
phone = None
sidetap_admin = None


class WDAError(Exception):
    pass


class PhoneUploadError(RuntimeError):
    pass


def connect_sidetap() -> None:
    global phone, sidetap_admin, WDAError
    if not SIDETAP_SRC.is_dir():
        raise PhoneUploadError(f"SideTap source missing: {SIDETAP_SRC}")
    if str(SIDETAP_SRC) not in sys.path:
        sys.path.insert(0, str(SIDETAP_SRC))
    try:
        from phone_harness import helpers, admin
        from phone_harness.wda_client import WDAError as WDAClientError
    except ImportError as exc:
        raise PhoneUploadError(f"SideTap cannot load: {exc}") from exc
    phone, sidetap_admin, WDAError = helpers, admin, WDAClientError


_layout: PhoneLayout | None = None


def layout(*, refresh: bool = False) -> PhoneLayout:
    global _layout
    if _layout is None or refresh:
        _layout = PhoneLayout.from_info(phone.screen_info())
    return _layout


def recover_link() -> bool:
    """Release a frozen app and wait for WDA; restart it only as a last resort."""
    from phone_harness import device
    return recover(sidetap_admin, release=lambda: release_frozen_app(device.ios_path()))


def screen() -> list[dict]:
    return phone.compact(phone.ocr())


def live_element(label: str, kind: str) -> Element:
    """One control with its full frame from the live tree; compact rows keep only centers."""
    snapshot = Snapshot(layout().width, layout().height, elements_from_tree(phone.ui_tree()))
    try:
        found = find(snapshot, Locator(label=label, type=kind), Labels("en", {})).element
    except MatchError as exc:
        raise PhoneUploadError(str(exc)) from exc
    if found is None or not layout().contains({"x": found.x, "y": found.y}):
        raise PhoneUploadError(f"{label!r} is outside the screen")
    return found


def stage(name: str) -> None:
    print(json.dumps({"stage": name}), file=sys.stderr, flush=True)


def matches(label: str, *, exact: bool = True) -> list[dict]:
    needle = label.casefold()
    return [
        row for row in screen()
        if layout().contains(row)
        and (row["text"].casefold() == needle if exact else needle in row["text"].casefold())
    ]


def wait(label: str, *, timeout: float = 20, exact: bool = True) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = matches(label, exact=exact)
        if found:
            return found[0]
        time.sleep(0.5)
    visible = [row["text"] for row in screen() if row.get("text")]
    raise PhoneUploadError(f"Expected {label!r}; visible: {visible[:24]}")


def tap(label: str, *, timeout: float = 20, exact: bool = True) -> None:
    row = wait(label, timeout=timeout, exact=exact)
    phone.tap(row["x"], row["y"])


def assert_visible(label: str, *, exact: bool = True) -> None:
    wait(label, timeout=8, exact=exact)


def youtube_header_account(timeout: float = 10) -> str:
    """The You tab renders its channel handle after the header frame; wait for it."""
    deadline = time.monotonic() + timeout
    while True:
        labels = [row["text"] for row in screen() if row.get("text")]
        try:
            return youtube_page_account(labels)
        except ValueError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def ensure_youtube_channel(expected: str) -> None:
    """Select a known signed-in channel before sending the file to YouTube."""
    stage("youtube_account")
    open_tabs(phone)
    assert_visible("You")
    tap("You")
    assert_visible("Accounts")
    if youtube_header_account() == expected.casefold():
        return
    tap("Accounts")
    options = [row for row in matches(expected) if row["text"].casefold() == expected.casefold()]
    if len(options) != 1:
        raise PhoneUploadError(f"YouTube account {expected} is missing or ambiguous")
    phone.tap(options[0]["x"], options[0]["y"])
    wait("You", timeout=30)
    tap("You")
    assert_visible("Accounts")
    if youtube_header_account() != expected.casefold():
        raise PhoneUploadError(f"YouTube did not switch to {expected}")


def assert_share_sheet(data: dict, *, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        labels = {row["text"] for row in screen()}
        if "shareSheet.activity.contentView" in labels:
            if data["filename"] not in labels:
                raise PhoneUploadError("iOS share sheet has the wrong filename")
            if not size_shown(data["sizeBytes"], labels):
                raise PhoneUploadError("iOS share sheet has the wrong file size")
            return
        if "ShareHVC.AppsAndActions.ScrollView" in labels and "More" in labels:
            tap("More")
        time.sleep(0.5)
    raise PhoneUploadError("iOS share sheet did not appear")


def scroll_to(label: str, *, exact: bool = True) -> None:
    for _ in range(4):
        if matches(label, exact=exact):
            return
        phone.swipe(*layout().reference_point(220, 780), *layout().reference_point(220, 350), 0.55)
    assert_visible(label, exact=exact)


def open_onedrive_file(data: dict) -> None:
    stage("onedrive_search")
    filename = data["filename"]
    onedrive_bundle = "com.microsoft.skydrive"
    # A prior iOS share extension can bounce a direct app launch back to YouTube.
    phone.press_home()
    phone.open_app(onedrive_bundle, wait_seconds=12)
    time.sleep(0.5)
    if phone.current_app().get("bundleId") != onedrive_bundle:
        phone.open_app(onedrive_bundle, wait_seconds=12)
    for _ in range(4):
        if phone.current_app().get("bundleId") != onedrive_bundle:
            raise PhoneUploadError("OneDrive lost the foreground before file selection")
        if matches("Search Your Files"):
            break
        if matches("Close"):
            tap("Close")
        elif matches("Back"):
            tap("Back")
        else:
            raise PhoneUploadError("OneDrive search is not reachable from its current screen")
        time.sleep(0.5)
    else:
        raise PhoneUploadError("OneDrive search did not open")

    stem = Path(filename).stem.casefold()
    result = None
    # Search first by exact filename, then by stem. OneDrive has displayed an
    # extra space before `.mp4` in search results for the verified file. The
    # share panel below still has to show the exact filename and size.
    for query in (filename, Path(filename).stem):
        if matches("Clear search input"):
            tap("Clear search input")
        tap("Search Your Files")
        phone.type_text(query)
        # OneDrive keeps showing the previous results until Search is submitted.
        keyboard_search = [row for row in matches("search") if layout().relative_band(row, top=0.75)]
        if len(keyboard_search) != 1:
            raise PhoneUploadError("OneDrive keyboard Search key is missing or ambiguous")
        phone.tap(keyboard_search[0]["x"], keyboard_search[0]["y"])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            candidates = [row for row in screen() if stem in row["text"].casefold() and ".mp4" in row["text"].casefold()]
            if len(candidates) == 1:
                result = candidates[0]
                break
            if len(candidates) > 1:
                raise PhoneUploadError(f"Multiple OneDrive results matched {filename!r}")
            time.sleep(0.5)
        if result is not None:
            break
    if result is None:
        raise PhoneUploadError(f"OneDrive could not find {filename!r}")
    phone.tap(result["x"], result["y"])
    assert_visible(stem, exact=False)
    tap("Share")
    assert_visible(filename)
    deadline = time.monotonic() + 8
    while not size_shown(data["sizeBytes"], [row["text"] for row in screen()]):
        if time.monotonic() >= deadline:
            raise PhoneUploadError("OneDrive share panel shows a different file size")
        time.sleep(0.5)
    tap("More")
    assert_share_sheet(data)
    stage("ios_share_sheet")


def choose_share_app(name: str, *, expected_bundle: str | None = None) -> None:
    for _ in range(10):
        rows = screen()
        if not any(row["text"] == "shareSheet.activity.contentView" for row in rows):
            raise PhoneUploadError("iOS share sheet disappeared before app selection")
        direction, target = share_app_position(rows, name, layout())
        # Scroll on the requested app's own row when iOS exposes it; the
        # measured 440 x 956 row is only a fallback while it is off-screen.
        rail_y = share_rail_y(rows, name, layout(), layout().reference_point(0, 392)[1])
        if direction == "tap":
            # The share rail keeps scrolling after WDA's swipe returns. Require
            # a second position reading before touching a destination.
            time.sleep(0.9)
            stable_direction, stable = share_app_position(screen(), name, layout())
            if stable_direction != "tap" or stable is None or abs(stable["x"] - target["x"]) > 5:
                continue
            phone.tap(stable["x"], stable["y"])
            if expected_bundle:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if phone.current_app().get("bundleId") == expected_bundle:
                        return
                    time.sleep(0.5)
                raise PhoneUploadError(f"Share selection did not open {name}; stop before composing")
            return
        if direction == "right":
            phone.swipe(layout().width * 0.27, rail_y, layout().width * 0.52, rail_y, 0.5)
        else:
            phone.swipe(layout().width * 0.88, rail_y, layout().width * 0.18, rail_y, 0.45)
        time.sleep(1.2)
    raise PhoneUploadError(f"{name} was not safely visible in the iOS share sheet")


def choose_unlabeled_radio(label: str, *, x: int = 35, exact: bool = True) -> None:
    """Use the accessible row label and verify its non-accessible radio circle."""
    # The row's text does not consistently receive taps; use the circle.
    # Selecting audience can move both rows, so locate the row again after tap.
    y = round(wait(label, exact=exact)["y"])
    # The circle sits a fixed number of points from the leading edge on every
    # width; scaling it by screen width moved the samples off the ring.
    selected_x = float(x)
    image = Image.open(BytesIO(phone.screenshot())).convert("RGB")
    if filled_radio(image, layout(), selected_x, y):
        return
    phone.tap(selected_x, y)
    time.sleep(0.45)
    selected_y = round(wait(label, exact=exact)["y"])
    image = Image.open(BytesIO(phone.screenshot())).convert("RGB")
    if not filled_radio(image, layout(), selected_x, selected_y):
        raise PhoneUploadError(f"YouTube radio {label!r} did not show selected")


def leave_text_editor(expected: str) -> None:
    # First tap may only dismiss the iOS keyboard; second leaves the editor.
    for _ in range(2):
        back = live_element("Back", "Button")
        phone.tap(back.x, back.y)
        if matches(expected):
            return
    assert_visible(expected)


def wait_for_trim_next(timeout: float = 15) -> None:
    """The playing Short can hang WDA's accessibility snapshot; read pixels."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        image = Image.open(BytesIO(phone.screenshot())).convert("RGB")
        sx, sy = image.width / layout().width, image.height / layout().height
        bright = 0
        total = 0
        for x in range(380, 421, 5):
            for y in range(880, 906, 5):
                # Next is pinned to the bottom-right safe area, not scaled.
                px, py = layout().bottom_right_point(x, y)
                r, g, b = image.getpixel((int(px * sx), int(py * sy)))
                bright += min(r, g, b) > 220
                total += 1
        if bright / total > 0.6:
            return
        time.sleep(0.5)
    raise PhoneUploadError("YouTube trim Next button did not appear")


def advance_trim_to_editor() -> None:
    """Wait through processing and retry only if the trim screen clearly remains."""
    for attempt in range(2):
        rows = screen()
        labels = {row["text"] for row in rows if row.get("text")}
        if "Swipe up to edit" in labels:
            return
        if "Crop your video" not in labels or "Next" not in labels:
            raise PhoneUploadError(f"YouTube trim changed before Next: {sorted(labels)[:20]}")
        tap("Next")
        deadline = time.monotonic() + 90
        crop_since = None
        while time.monotonic() < deadline:
            labels = {row["text"] for row in screen() if row.get("text")}
            if "Swipe up to edit" in labels:
                return
            if "Processing" in labels:
                crop_since = None
            elif "Crop your video" in labels and "Next" in labels:
                crop_since = crop_since or time.monotonic()
                if time.monotonic() - crop_since > 5:
                    break
            time.sleep(0.8)
    raise PhoneUploadError("YouTube stayed on the trim screen after two verified Next taps")


def prepare_youtube(data: dict) -> None:
    choose_share_app("YouTube", expected_bundle="com.google.ios.youtube")
    stage("youtube_trim")
    wait_for_trim_next(120 if data["sizeBytes"] > 500_000_000 else 30)
    advance_trim_to_editor()
    tap("Next")
    assert_visible("Add details")
    stage("youtube_details")
    # YouTube exposes the identity as a combined display-name/handle label.
    # Compare the handle token exactly, not a substring of another handle.
    labels = [row["text"] for row in screen() if row.get("text")]
    actual_account = youtube_identity(labels)
    if actual_account != data["expectedAccount"].casefold():
        raise PhoneUploadError(f"Wrong YouTube channel: {actual_account}")
    assert_visible("Edit thumbnail", exact=False)
    tap("Edit thumbnail", exact=False)
    assert_visible("Thumbnail frame selector")
    # The leftmost frame is the first frame of the clip.
    phone.tap(*first_frame_point(live_element("Thumbnail frame selector", "Slider")))
    for _ in range(3):
        if matches("Add details"):
            break
        tap("Done")
        time.sleep(0.4)
    assert_visible("Add details")

    tap("id.elements.components.metadata_editor.title")
    phone.type_text(data["title"])
    assert_visible(data["title"])
    tap("Visibility", exact=False)
    assert_visible("Set visibility")
    choose_unlabeled_radio(data["visibility"].capitalize(), exact=False)
    tap("Back")
    assert_visible(f"Visibility, {data['visibility'].capitalize()}")
    tap("Select audience")
    assert_visible("Select audience")
    choose_unlabeled_radio("No, it's not made for kids")
    tap("Back")
    assert_visible("No, it's not made for kids", exact=False)
    tap("Show more")
    tap("Add description")
    phone.type_text(data["description"])
    assert_visible(data["description"])
    leave_text_editor("Add details")
    tap("Paid promotion & brands", exact=False)
    assert_visible("Paid promotion & brands")
    choose_unlabeled_radio("No", x=28)
    tap("Back")
    assert_visible("Add details")
    assert_visible("No, it doesn", exact=False)
    # Scroll only the details list; the upload button stays fixed below it.
    scroll_to("AI use, Tags", exact=False)
    tap("AI use, Tags", exact=False)
    tap("AI use", exact=False)
    assert_visible("AI use")
    choose_unlabeled_radio("No")
    tap("Back")
    assert_visible("Attributes")
    tap("Add tags")
    phone.type_text(", ".join(data["tags"]) + ",")
    if matches("Tags must have", exact=False):
        raise PhoneUploadError("YouTube rejected one or more tags")
    leave_text_editor("Add details")
    assert_visible("Upload Short")
    stage("ready_to_upload")


def inspect_native_schedule(data: dict, db: Path) -> dict:
    """Read the YouTube Schedule controls without choosing a time or submitting."""
    stage("youtube_schedule_inspection")
    # Preparation ends at the lower metadata fields; return to Visibility.
    for _ in range(5):
        if matches("Visibility", exact=False):
            break
        phone.swipe(*layout().reference_point(220, 300),
                    *layout().reference_point(220, 780), 0.55)
    tap("Visibility", exact=False)
    assert_visible("Set visibility")
    candidates = [row for row in matches("Schedule")
                  if layout().relative_band(row, top=0.3, bottom=0.8)]
    if len(candidates) != 1:
        raise PhoneUploadError("YouTube Schedule control is missing or ambiguous")
    before = tuple(sorted(row["text"] for row in screen() if row.get("text")))
    phone.tap(candidates[0]["x"], candidates[0]["y"])
    if phone.current_app().get("bundleId") != "com.google.ios.youtube":
        raise PhoneUploadError("YouTube left the foreground during schedule inspection")
    deadline = time.monotonic() + 12
    previous = None
    while time.monotonic() < deadline:
        rows = screen()
        visible = tuple(sorted(row["text"] for row in rows if row.get("text")))
        if visible != before and visible == previous:
            break
        previous = visible
        time.sleep(0.4)
    else:
        raise PhoneUploadError("YouTube Schedule did not open; the visibility screen is still showing")
    screenshot = phone.screenshot()
    if tuple(sorted(row["text"] for row in screen() if row.get("text"))) != visible:
        raise PhoneUploadError("YouTube Schedule screen changed during capture; inspect it again")
    evidence_dir = db.parent / "schedule-inspection"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stem = evidence_dir / f"youtube-{data['releaseId']}-{stamp}"
    stem.with_suffix(".png").write_bytes(screenshot)
    stem.with_suffix(".json").write_text(json.dumps({
        "releaseId": data["releaseId"], "account": data["expectedAccount"],
        "plannedSlot": data["scheduledAt"], "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"kind": "schedule_inspection", "releaseId": data["releaseId"],
            "account": data["expectedAccount"], "plannedSlot": data["scheduledAt"],
            "screenshot": str(stem.with_suffix(".png")), "rows": str(stem.with_suffix(".json")),
            "message": "Native Schedule controls captured; no upload or schedule was submitted"}


def run(release: str, db: Path, *, commit: bool = False, resume_share: bool = False,
        inspect_schedule: bool = False) -> dict:
    if commit and inspect_schedule:
        raise PhoneUploadError("Schedule inspection cannot submit an upload")
    if commit and os.environ.get("VIDEO_DROP_TEST_MODE") == "1":
        raise PhoneUploadError("Posting is disabled in this test session")
    with Store(db, load_targets(db.parent)) as store:
        if release.isdecimal():
            data = youtube_input(store, int(release))
        else:
            manifest = json.loads(Path(release).read_text(encoding="utf-8"))
            data = verify_youtube_manifest(store, manifest)
        if commit and data["deliveryMode"] != "post_now":
            raise PhoneUploadError("YouTube scheduling is not connected; choose Post now for an immediate upload")
        if inspect_schedule:
            if data["deliveryMode"] != "schedule" or not data["scheduledAt"]:
                raise PhoneUploadError("Reserve a future slot before inspecting YouTube Schedule")
            slot = datetime.fromisoformat(data["scheduledAt"])
            if slot.tzinfo is None or slot <= datetime.now(timezone.utc):
                raise PhoneUploadError("The planned YouTube slot has passed; reserve a future slot")
        if data["visibility"] not in ("private", "unlisted", "public"):
            raise PhoneUploadError("Invalid YouTube visibility")
        if not isinstance(data["tags"], list) or not all(isinstance(tag, str) and tag for tag in data["tags"]):
            raise PhoneUploadError("Invalid YouTube tags")

        # Only preparation is retryable. Once the final tap is possible, keep
        # the one-shot state in the database and never rebuild the composer.
        connect_sidetap()
        for attempt in range(3):
            try:
                phone.unlock()
                with optional_focus(phone, store.phone_checks()["doNotDisturb"]):
                    if resume_share and attempt == 0:
                        assert_share_sheet(data, timeout=8)
                    else:
                        layout(refresh=True)
                        ensure_youtube_channel(data["expectedAccount"])
                        open_onedrive_file(data)
                    prepare_youtube(data)
                    if inspect_schedule:
                        return inspect_native_schedule(data, db)
                    if not commit:
                        return {"kind": "ready", "releaseId": data["releaseId"],
                                "account": data["expectedAccount"]}
                    # Recheck identity and source revision just before marking
                    # the attempt. A timeout after the tap is never replayed.
                    labels = [row["text"] for row in screen() if row.get("text")]
                    if youtube_identity(labels) != data["expectedAccount"].casefold():
                        raise PhoneUploadError("YouTube channel changed before Upload Short")
                    upload = matches("Upload Short")
                    if len(upload) != 1:
                        raise PhoneUploadError("Upload Short button is missing or ambiguous")
                    store.mark_unconfirmed(data["releaseId"], "youtube",
                                           expected_revision=data["revisionHash"])
                    phone.tap(upload[0]["x"], upload[0]["y"])
                    return {"kind": "unconfirmed", "releaseId": data["releaseId"],
                            "account": data["expectedAccount"],
                            "message": "Final tap sent; check the native YouTube receipt before any retry"}
            except WDAError as exc:
                destination = next(d for d in store.release(data["releaseId"])["destinations"]
                                   if d["platform"] == "youtube")
                if destination["status"] == "unconfirmed":
                    raise PhoneUploadError("YouTube final tap is uncertain; check the channel before any retry") from exc
                if attempt == 2:
                    raise PhoneUploadError(f"Phone link failed after 3 preparation attempts: {exc}") from exc
                stage("recover_phone_link")
                if not recover_link():
                    raise PhoneUploadError("SideTap could not restore the phone link; no upload was submitted") from exc
                time.sleep(1)
    raise PhoneUploadError("YouTube preparation did not finish")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", help="Release ID or an exact legacy manifest path")
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("VIDEO_DROP_STATE", Path(__file__).resolve().parent.parent / ".state")) / "video-drop.sqlite")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--resume-share", action="store_true")
    parser.add_argument("--inspect-schedule", action="store_true",
                        help="Capture the native Schedule screen; never submit")
    args = parser.parse_args()
    print(json.dumps(run(args.release, args.db, commit=args.commit,
                         resume_share=args.resume_share, inspect_schedule=args.inspect_schedule)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PhoneUploadError, FocusError, WDAError, OSError, ValueError) as exc:
        print(json.dumps({"kind": "error", "message": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
