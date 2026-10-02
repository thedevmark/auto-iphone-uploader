"""Read-only iPhone inventory; writes only a local, nonsecret profile."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_drop.phone import device as phone_device  # noqa: E402
from video_drop.phone import helpers as phone_helpers  # noqa: E402
device = None
phone = None
from video_drop.phone_onboarding import build_profile  # noqa: E402
from video_drop.accounts import load_targets  # noqa: E402
from video_drop.phone_ui import youtube_page_account  # noqa: E402
from video_drop.youtube_nav import open_tabs, visible_rows  # noqa: E402

HANDLE = re.compile(r"@[A-Za-z0-9._-]+\Z")


def connect_sidetap() -> None:
    """Bind the app's own phone driver (video_drop/phone). The name is historical."""
    global device, phone
    device, phone = phone_device, phone_helpers


def rows() -> list[dict]:
    # The tree, or OCR while YouTube's Home feed plays its previews (the driver refuses that read).
    return visible_rows(phone)


def tap_unique(label: str) -> None:
    found = [row for row in rows() if row.get("text") == label]
    if len(found) != 1:
        raise ValueError(f"Expected one {label!r} control; found {len(found)}")
    phone.tap(found[0]["x"], found[0]["y"])


def settled_labels(previous: list[str], timeout: float = 6) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        labels = [row["text"] for row in rows()]
        if labels != previous:
            return labels
        time.sleep(0.4)
    raise ValueError("YouTube screen did not change")


def upload_quality_from_labels(labels: list[str]) -> str:
    """Accept only an explicit upload-quality value, never playback preferences."""
    for label in labels:
        if label.casefold().startswith("upload quality"):
            text = label.casefold()
            if "full quality" in text:
                return "full"
            if any(value in text for value in ("standard quality", "data saver", "lower quality")):
                return "limited"
    if any("full quality" in label.casefold() and "selected" in label.casefold() for label in labels):
        return "full"
    return "unverified"


def inspect_upload_quality() -> dict:
    """Read YouTube's upload setting when the installed app exposes it."""
    before = [row["text"] for row in rows()]
    tap_unique("Settings")
    labels = settled_labels(before)
    for _ in range(2):
        section = next((name for name in ("Uploads", "Videos and audio preferences") if name in labels), None)
        if not section or upload_quality_from_labels(labels) != "unverified" or "Upload quality" in labels:
            break
        before = labels
        tap_unique(section)
        labels = settled_labels(before)
    quality = upload_quality_from_labels(labels)
    if quality == "unverified" and "Upload quality" in labels:
        before = labels
        tap_unique("Upload quality")
        labels = settled_labels(before)
        # A visible option alone does not prove it is selected.
        quality = upload_quality_from_labels(labels)
    return {"status": quality, "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def inspect_youtube() -> dict:
    open_tabs(phone)
    labels = [row["text"] for row in rows()]
    tap_unique("You")
    selected = ""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        labels = [row["text"] for row in rows()]
        if "Accounts" in labels:
            try:
                selected = youtube_page_account(labels)
                break
            except ValueError:
                pass
        time.sleep(0.4)
    if not selected:
        raise ValueError("YouTube account header did not load")
    tap_unique("Accounts")
    labels = [row["text"] for row in rows()]
    available = list(dict.fromkeys(label.casefold() for label in labels if HANDLE.fullmatch(label)))
    if selected not in available:
        raise ValueError("Selected YouTube channel is missing from its account list")
    tap_unique("Close")
    result = {"selected": selected, "available": available}
    try:
        result["uploadQuality"] = inspect_upload_quality()
    except Exception as exc:
        result["uploadQuality"] = {"status": "unverified", "reason": str(exc)[:160]}
    return result


def main() -> None:
    connect_sidetap()
    devices = device.list_devices()
    if len(devices) != 1:
        raise ValueError(f"Connect exactly one iPhone; found {len(devices)}")
    phone.unlock()
    info = phone.screen_info()
    apps = device.list_apps()
    observed = {}
    probe_error = ""
    if any(app.get("bundle_id") == "com.google.ios.youtube" for app in apps):
        try:
            observed["youtube"] = inspect_youtube()
        except Exception as exc:
            probe_error = str(exc)
    state = Path(os.environ.get("VIDEO_DROP_STATE", ROOT / ".state")).resolve()
    profile = build_profile(info, apps, observed, load_targets(state))
    if probe_error:
        profile["youtubeProbeError"] = probe_error
    identifier = hashlib.sha256(devices[0].encode()).hexdigest()[:12]
    target = state / "phone-profiles" / f"{identifier}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"profile": str(target), "screenPoints": profile["screenPoints"],
                      "installed": [name for name, app in profile["apps"].items() if app["installed"]],
                      "youtube": {**profile["apps"]["youtube"],
                                  "uploadQuality": observed.get("youtube", {}).get("uploadQuality", {"status": "unverified"})},
                      "youtubeProbeError": probe_error}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
