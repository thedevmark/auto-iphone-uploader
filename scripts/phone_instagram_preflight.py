"""Check Instagram identity and source color before any Edits export.

Read-only on the phone. No model or platform website is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_drop.media_color import edits_color_mode  # noqa: E402
from video_drop.phone_ui import PhoneLayout  # noqa: E402
from video_drop.core import Store, digest  # noqa: E402
from video_drop.accounts import load_targets, require_target  # noqa: E402

SIDETAP = Path(os.environ.get("SIDETAP_ROOT", "").strip() or Path.home() / "AppData" / "Local" / "SideTap")
phone = None


def connect_sidetap() -> None:
    global phone
    source = SIDETAP / "src"
    if not source.is_dir():
        raise ValueError(f"SideTap source missing: {source}")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    try:
        from phone_harness import helpers
    except ImportError as exc:
        raise ValueError(f"SideTap cannot load: {exc}") from exc
    phone = helpers


def selected_instagram_account(phone_client=None) -> str:
    device = phone_client or phone
    device.open_app("com.burbn.instagram", wait_seconds=2)
    if device.current_app().get("bundleId") != "com.burbn.instagram":
        raise ValueError("Instagram is not foreground")
    rows = device.compact(device.ocr())
    profile = [row for row in rows if row["text"] == "Profile" and row.get("type") == "Button"]
    if len(profile) == 1:
        device.tap(profile[0]["x"], profile[0]["y"])
        rows = device.compact(device.ocr())
    layout = PhoneLayout.from_info(device.screen_info())
    handles = [row["text"] for row in rows if row.get("type") == "Button"
               and layout.relative_band(row, left=0.3, right=0.7, top=0.06, bottom=0.12)
               and re.fullmatch(r"[A-Za-z0-9._]+", row["text"])]
    if len(handles) != 1:
        raise ValueError("Instagram profile header is missing or ambiguous; stop before Edits export")
    return "@" + handles[0].casefold()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("--account")
    parser.add_argument("--release-id", type=int)
    parser.add_argument("--db", type=Path, default=ROOT / ".state" / "video-drop.sqlite")
    args = parser.parse_args()
    if args.release_id is not None:
        if args.source is not None:
            raise ValueError("Use a release ID or a source path, not both")
        with Store(args.db, load_targets(args.db.parent)) as store:
            release = store.release(args.release_id)
            instagram = next(d for d in release["destinations"] if d["platform"] == "instagram")
            require_target(store.account_targets, "instagram", instagram["account"])
            revision = store._revision_hash("instagram", instagram["account"], instagram["title"],
                                            instagram["description"], instagram["tags"], instagram["visibility"])
        if instagram["status"] != "pending":
            raise ValueError("Instagram has already been attempted; check its native receipt")
        if not instagram["revision_hash"]:
            raise ValueError("Instagram text is not confirmed in Automated iPhone Social Media Uploads")
        if instagram["revision_hash"] != revision:
            raise ValueError("Instagram text changed after confirmation")
        source = Path(release["source_path"])
        account = instagram["account"]
        if args.account and args.account.casefold() != account.casefold():
            raise ValueError("Requested account differs from the saved Instagram target")
        if not source.is_file() or source.stat().st_size != release["file_size"] or digest(source) != release["sha256"]:
            raise ValueError("Source video changed or is missing")
    else:
        if args.source is None or not args.account:
            raise ValueError("Give --release-id, or a source path with --account")
        source, account = args.source, args.account
    if not source.is_file():
        raise ValueError(f"Source video missing: {source}")
    color = edits_color_mode(source)
    connect_sidetap()
    actual = selected_instagram_account()
    if actual != account.casefold():
        raise ValueError(f"Instagram has {actual}; expected {account}. Switch before Edits export")
    print(json.dumps({"source": str(source.resolve()), "instagramAccount": actual,
                      "editsColorMode": color, "readyForEditsExport": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"readyForEditsExport": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
