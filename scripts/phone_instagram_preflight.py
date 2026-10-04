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
from video_drop.phone.helpers import VideoSurfaceError  # noqa: E402
from video_drop.phone_ui import PhoneLayout  # noqa: E402
from scripts import phone_youtube as share  # noqa: E402

phone = None
INSTAGRAM = "com.burbn.instagram"
# Profile tab center on the 440 x 956 reference phone, on the bottom safe area (the same
# measurement as scripts/phone_receipts.py INSTAGRAM_PROFILE_TAB).
PROFILE_TAB = (370.5, 904.0)
# A story's reply bar ("Send message", "Reply to <name>..."): no tab bar under it.
STORY_REPLY = ("send message", "reply to")
# A single Reel opened from the owner's profile: no tab bar, its own
# footer ("Insights on Edits", "Boost") and a back-button at the top left.
REEL_FOOTER = ("insights on edits", "boost")
REEL_BACK = (40.0, 87.0)


def connect_sidetap() -> None:
    """Bind the app's own phone driver (video_drop/phone). The name is historical."""
    global phone
    phone = phone_helpers


def profile_handle(elements) -> str:
    """The active account from the profile header's account-switcher button.

    Instagram names it user-switch-title-button and labels it with the
    handle; its position changes between releases, so position is not trusted.
    """
    found = {e.label for e in elements if e.type == "Button" and e.name == "user-switch-title-button"}
    if len(found) != 1 or not re.fullmatch(r"[A-Za-z0-9._]+", next(iter(found))):
        raise ValueError("Instagram profile header is missing or ambiguous; stop before Edits export")
    return "@" + next(iter(found)).casefold()


def _playing_rows() -> list[dict]:
    """OCR of a go-ios screenshot: the only read allowed while Instagram plays video."""
    from video_drop import ocr
    from video_drop.phone import capture

    try:
        return ocr.screen_rows(capture.pixels_png(), PhoneLayout.from_info(phone.screen_info()).width)
    except ocr.OcrError as exc:
        raise ValueError(f"Instagram is playing video and OCR could not read it: {exc}; "
                         "stop before Edits export") from exc


def _leave_playing_screen() -> None:
    """Get from a playing feed, Reel or story to the still profile without asking WDA anything.

    A story (OCR reads its reply bar) is swiped down closed, since a tap at the Profile tab's
    place would land on its reply or like controls. Anywhere else the Profile tab is tapped
    blind at the place scripts/phone_receipts.py measured for its own receipt reads."""
    layout = PhoneLayout.from_info(phone.screen_info())
    texts = [" ".join(row["text"].split()).casefold() for row in _playing_rows()]
    if any(text.startswith(STORY_REPLY) for text in texts):
        phone.swipe(layout.width / 2, layout.height * 0.3, layout.width / 2, layout.height * 0.85, 0.3)
        time.sleep(1.5)
        return
    if any(text.startswith(REEL_FOOTER) for text in texts):
        phone.tap(*layout.reference_point(*REEL_BACK))  # back to the profile grid
        time.sleep(1.5)
        return
    phone.tap(*layout.bottom_sheet_point(*PROFILE_TAB))
    time.sleep(2.0)


def open_instagram():
    """Instagram in front on a screen whose tree may be read.

    Instagram opens on its feed, a Reel or a story, which autoplay. The driver refuses every
    accessibility request there (activeAppInfo included), so such a screen is left from pixels
    only; the profile header read afterwards is what proves the app and the account."""
    try:
        phone.open_app(INSTAGRAM, wait_seconds=2)
    except VideoSurfaceError:
        pass
    else:
        try:
            if phone.current_app().get("bundleId") != INSTAGRAM:
                raise ValueError("Instagram is not foreground")
            return elements_from_tree(phone.ui_tree())
        except VideoSurfaceError:
            pass
    for _ in range(2):
        _leave_playing_screen()
        try:
            return elements_from_tree(share.still_tree(driver=phone))
        except VideoSurfaceError:
            continue
    raise ValueError("Instagram kept playing video after two tries to reach the profile; "
                     "stop before Edits export")


def discard_composer(elements) -> bool:
    """Close a restored reel composer without saving: Cancel, then "Continue without saving".

    Instagram can reopen on an earlier run's "New reel" composer
    (Buttons Cancel, save-draft-button, share-sheet-share-button); Cancel asks "Changes won't be
    saved" with "Save draft" and "Continue without saving"; discarding lands on the feed.
    Returns True when a composer was discarded. Never taps Share."""
    if not any(e.type == "Button" and e.name == "share-sheet-share-button" for e in elements):
        return False
    cancel = [e for e in elements if e.type == "Button" and e.name == "Cancel"]
    if len(cancel) != 1:
        raise ValueError("A reel composer is open without a single Cancel; stop before Edits export")
    phone.tap(cancel[0].x, cancel[0].y)
    time.sleep(1.5)
    after = elements_from_tree(phone.ui_tree())
    discard = [e for e in after if e.type == "Button" and e.label == "Continue without saving"]
    if len(discard) != 1:
        raise ValueError("Instagram did not offer to discard the open composer; stop before Edits export")
    phone.tap(discard[0].x, discard[0].y)
    time.sleep(2.0)
    return True


def selected_instagram_account() -> str:
    elements = open_instagram()
    # Instagram can reopen inside a story viewer, which has no tab bar.
    # It can also reopen inside a single Reel opened from the profile
    # ("Insights on Edits", "Boost", a back-button and no tab bar): go Back to the grid.
    for _ in range(3):
        if discard_composer(elements):
            elements = elements_from_tree(phone.ui_tree())
            continue
        dismiss = [e for e in elements if e.type == "Button" and e.name == "story-dismiss-button"]
        back = [e for e in elements if e.type == "Button" and e.name == "back-button"]
        has_tabs = any(e.type == "Button" and e.label == "Profile" for e in elements)             or any(e.name == "user-switch-title-button" for e in elements)
        if len(dismiss) == 1:
            target = dismiss[0]
        elif len(back) == 1 and not has_tabs:
            target = back[0]
        else:
            break
        phone.tap(target.x, target.y)
        time.sleep(1.5)
        elements = elements_from_tree(phone.ui_tree())
    profile = [e for e in elements if e.type == "Button" and e.label == "Profile"]
    if len(profile) == 1:
        phone.tap(profile[0].x, profile[0].y)
        time.sleep(1.5)
        elements = elements_from_tree(phone.ui_tree())
    return profile_handle(elements)


def switcher_row(elements, handle: str):
    """The account switcher's row for ``handle``. Each signed-in account is
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
