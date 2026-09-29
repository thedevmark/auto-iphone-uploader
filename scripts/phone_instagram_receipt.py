"""Verify an uncertain Instagram schedule in the native iPhone app.

This reads the active account, device time zone, Scheduled content rows, and
cover screenshot through SideTap. It never taps Share or Schedule and never
retries a possibly submitted Reel.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import phone_instagram_preflight as preflight  # noqa: E402
from video_drop.accounts import load_targets, require_target  # noqa: E402
from video_drop.core import Store, digest  # noqa: E402
from video_drop.instagram_schedule import matching_scheduled_reel, read_device_time_zone  # noqa: E402
from video_drop.phone_ui import PhoneLayout  # noqa: E402


def release_input(store: Store, release_id: int) -> dict:
    release = store.release(release_id)
    destination = next(d for d in release["destinations"] if d["platform"] == "instagram")
    require_target(store.account_targets, "instagram", destination["account"])
    if release["delivery_mode"] != "schedule" or not release["scheduled_at"]:
        raise ValueError("This Instagram release has no planned schedule")
    if destination["status"] != "unconfirmed":
        raise ValueError("Instagram has no uncertain schedule to verify")
    revision = store._revision_hash("instagram", destination["account"], destination["title"],
                                    destination["description"], destination["tags"], destination["visibility"])
    if destination["revision_hash"] != revision:
        raise ValueError("Instagram text changed after confirmation")
    source = Path(release["source_path"])
    if (not source.is_file() or source.name != release["source_name"]
            or source.stat().st_size != release["file_size"] or digest(source) != release["sha256"]):
        raise ValueError("Source video changed or is missing")
    return {"account": destination["account"], "slot": release["scheduled_at"],
            "caption": destination["description"]}


def visible_rows(device) -> list[dict]:
    layout = PhoneLayout.from_info(device.screen_info())
    return [row for row in device.compact(device.ocr(), limit=None) if layout.contains(row)]


def single_usb_udid(connected: list[str], pinned: str) -> str:
    if len(connected) != 1:
        raise ValueError("Instagram receipt needs exactly one USB iPhone; disconnect extra phones")
    if pinned and pinned != connected[0]:
        raise ValueError("SideTap is pinned to a different iPhone than the connected USB phone")
    return connected[0]


def connected_usb_udid() -> str:
    from phone_harness import config, device
    return single_usb_udid(device.list_devices(), config.SIDETAP_UDID)


def open_scheduled_content(device) -> list[dict]:
    layout = PhoneLayout.from_info(device.screen_info())
    def title_visible(rows: list[dict]) -> bool:
        return any(row.get("text") == "Scheduled content" and row.get("type") != "Button"
                   and layout.relative_band(row, left=0.2, right=0.8, top=0.04, bottom=0.2)
                   for row in rows)

    rows = visible_rows(device)
    if title_visible(rows):
        return rows
    menu = [row for row in rows if row.get("type") == "Button"
            and row.get("text") in {"Menu", "Options", "More options"}
            and layout.relative_band(row, left=0.55, top=0.02, bottom=0.22)]
    if len(menu) != 1:
        labels = [row.get("text") for row in rows if row.get("text")]
        raise ValueError(f"Instagram profile menu is missing or ambiguous; visible: {labels[:18]}")
    device.tap(menu[0]["x"], menu[0]["y"])
    for _ in range(5):
        rows = visible_rows(device)
        entry = [row for row in rows if row.get("text") == "Scheduled content"]
        if len(entry) == 1:
            device.tap(entry[0]["x"], entry[0]["y"])
            break
        if len(entry) > 1:
            raise ValueError("Instagram has multiple Scheduled content entries")
        device.scroll("down", 0.35)
    else:
        labels = [row.get("text") for row in rows if row.get("text")]
        raise ValueError(f"Instagram Scheduled content menu item is missing; visible: {labels[:18]}")
    for _ in range(20):
        rows = visible_rows(device)
        if title_visible(rows):
            return rows
        time.sleep(0.5)
    raise ValueError("Instagram Scheduled content screen did not open")


def run(release_id: int, db: Path) -> dict:
    if os.environ.get("VIDEO_DROP_TEST_MODE") == "1":
        raise ValueError("Native receipt recording is disabled in this test session")
    with Store(db, load_targets(db.parent)) as store:
        expected = release_input(store, release_id)
        preflight.connect_sidetap()
        device = preflight.phone
        udid = connected_usb_udid()
        device.unlock()
        device_zone = read_device_time_zone(udid)
        actual_account = preflight.selected_instagram_account(device)
        if actual_account != expected["account"].casefold():
            raise ValueError(f"Instagram has {actual_account}; expected {expected['account']}")
        rows = open_scheduled_content(device)
        if device.current_app().get("bundleId") != "com.burbn.instagram":
            raise ValueError("Instagram left the foreground before receipt capture")
        layout = PhoneLayout.from_info(device.screen_info())
        before_match = matching_scheduled_reel(rows, expected["caption"], expected["slot"],
                                               device_zone, layout)
        screenshot = device.screenshot()
        after = visible_rows(device)
        after_match = matching_scheduled_reel(after, expected["caption"], expected["slot"],
                                              device_zone, layout)
        if abs(before_match["timeRow"]["y"] - after_match["timeRow"]["y"]) > 5:
            raise ValueError("Instagram Scheduled content changed during capture; check again")
        evidence_dir = db.parent / "receipts"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        evidence = evidence_dir / f"instagram-{release_id}-{stamp}.png"
        evidence.write_bytes(screenshot)
        release = store.record_observed_schedule(release_id, "instagram", account=actual_account,
                                                 native_rows=rows, device_time_zone=device_zone,
                                                 screen_info=device.screen_info(), evidence_image=evidence)
    return {"kind": "scheduled", "releaseId": release_id, "account": actual_account,
            "scheduledAt": release["scheduled_at"], "evidence": str(evidence)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_id", type=int)
    parser.add_argument("--db", type=Path, default=ROOT / ".state" / "video-drop.sqlite")
    args = parser.parse_args()
    print(json.dumps(run(args.release_id, args.db)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"kind": "error", "message": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
