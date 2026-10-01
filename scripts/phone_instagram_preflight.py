"""Check Instagram identity and source color before any Edits export.

Read-only on the phone. No model or platform website is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_drop.media_color import edits_color_mode  # noqa: E402
from video_drop.screens.snapshot import elements_from_tree  # noqa: E402
from video_drop.core import Store, digest  # noqa: E402
from video_drop.accounts import load_targets, require_target  # noqa: E402
from video_drop.phone import helpers as phone_helpers  # noqa: E402

phone = None


def connect_sidetap() -> None:
    """Bind the app's own phone driver (video_drop/phone). The name is historical."""
    global phone
    phone = phone_helpers


def profile_handle(elements) -> str:
    """The active account from the profile header's account-switcher button.

    Instagram 2026-09 names it user-switch-title-button and labels it with the
    handle; its position moved between releases, so position is not trusted.
    """
    found = {e.label for e in elements if e.type == "Button" and e.name == "user-switch-title-button"}
    if len(found) != 1 or not re.fullmatch(r"[A-Za-z0-9._]+", next(iter(found))):
        raise ValueError("Instagram profile header is missing or ambiguous; stop before Edits export")
    return "@" + next(iter(found)).casefold()


def selected_instagram_account() -> str:
    phone.open_app("com.burbn.instagram", wait_seconds=2)
    if phone.current_app().get("bundleId") != "com.burbn.instagram":
        raise ValueError("Instagram is not foreground")
    elements = elements_from_tree(phone.ui_tree())
    profile = [e for e in elements if e.type == "Button" and e.label == "Profile"]
    if len(profile) == 1:
        phone.tap(profile[0].x, profile[0].y)
        time.sleep(1.5)
        elements = elements_from_tree(phone.ui_tree())
    return profile_handle(elements)


def switcher_row(elements, handle: str):
    """The account switcher's row for ``handle``. Recorded 2026-10-01: each signed-in account is
    a Button labelled "INSTAGRAM profile, <handle>[, <activity>]"; the active one has value 1."""
    pattern = re.compile(r"instagram profile, " + re.escape(handle.lstrip("@").casefold()) + r"(,|$)")
    rows = {(e.x, e.y): e for e in elements if e.type == "Button" and pattern.match(e.label.casefold())}
    if len(rows) != 1:
        raise ValueError(f"Instagram account switcher shows {len(rows)} rows for {handle}; "
                         "sign that account in on the phone first")
    return next(iter(rows.values()))


def ensure_instagram_account(expected: str) -> str:
    """Open the profile, and switch to ``expected`` through Instagram's own account switcher
    when another signed-in account is active. The header must then read ``expected``."""
    actual = selected_instagram_account()
    if actual == expected.casefold():
        return actual
    elements = elements_from_tree(phone.ui_tree())
    header = [e for e in elements if e.type == "Button" and e.name == "user-switch-title-button"]
    if len(header) != 1:
        raise ValueError("Instagram profile header is missing or ambiguous; stop before Edits export")
    phone.tap(header[0].x, header[0].y)
    time.sleep(1.5)
    row = switcher_row(elements_from_tree(phone.ui_tree()), expected)
    phone.tap(row.x, row.y)
    deadline = time.monotonic() + 15
    while True:
        time.sleep(1.5)
        try:
            actual = profile_handle(elements_from_tree(phone.ui_tree()))
        except ValueError:
            actual = ""
        if actual == expected.casefold():
            return actual
        if time.monotonic() >= deadline:
            raise ValueError(f"Instagram did not switch to {expected} (shows {actual or 'nothing'}); "
                             "nothing was exported")


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
            raise ValueError("Instagram text is not confirmed in Auto iPhone Uploader")
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
    phone.unlock()
    actual = ensure_instagram_account(account)
    print(json.dumps({"source": str(source.resolve()), "instagramAccount": actual,
                      "editsColorMode": color, "readyForEditsExport": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"readyForEditsExport": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
