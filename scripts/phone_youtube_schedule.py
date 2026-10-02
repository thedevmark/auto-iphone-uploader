"""Schedule one approved release on YouTube at its reserved slot, natively on the iPhone.

Same preparation as Post now (scripts/phone_youtube.py: exact OneDrive file, channel,
first-frame thumbnail, title, description, audience, paid promotion, AI use, tags), then
Visibility -> Schedule -> the date field -> the iOS calendar day button -> the time wheels
-> OK. The chosen date and time are read back from the picker twice (before OK, and after
re-opening it) before the release is marked unconfirmed and Upload Short is tapped once.

Recorded on the reference iPhone 16 Pro Max, iOS 26.7, YouTube 21.38.3 (fixtures
youtube/schedule-v2, schedule-picker, time-wheels, schedule-set, details-final). Two things
the recordings show and this script works around:

- The "Sep 30, 2026 at 10:00 AM Local Time" field is drawn but not in the accessibility
  tree. It is placed from the live privacy-settings container and proven by the date alert
  opening; any other result stops the run.
- Back on Add details, the tree still labels the row "Visibility, Public" while the screen
  shows "Visibility · Scheduled". The tree cannot prove the schedule there, so the proof is
  the picker read-back. The final button stays "Upload Short".

YouTube's scheduled state has no recorded receipt screen yet, so a submitted schedule stays
unconfirmed with a clear message; it is never retried.
Without --commit, stop with the slot entered and verified, before Upload Short.
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
from video_drop import phone_lock  # noqa: E402

from video_drop import native_schedule as ns  # noqa: E402
from video_drop.accounts import load_targets  # noqa: E402
from video_drop.core import Store  # noqa: E402
from video_drop.phone_manifest import youtube_input  # noqa: E402
from video_drop.phone_ui import youtube_identity  # noqa: E402
from video_drop.screens.labels import Labels  # noqa: E402
from video_drop.screens.matcher import MatchError, find, identify  # noqa: E402
from video_drop.screens.model import load_maps  # noqa: E402
from video_drop.screens.snapshot import Snapshot, elements_from_tree  # noqa: E402
from scripts import phone_youtube as yt  # noqa: E402

BUNDLE = "com.google.ios.youtube"
MAPS = ROOT / "maps"
MIN_LEAD = ns.MIN_LEAD
SCREEN_TIMEOUT = 12.0


class Stop(yt.PhoneUploadError):
    pass


_maps = None
_labels = None


def youtube_maps():
    global _maps, _labels
    if _maps is None:
        _maps = [m for m in load_maps(MAPS) if m.app == "youtube"]
        _labels = Labels.load(MAPS, "en")
    return _maps, _labels


def elements() -> tuple:
    return elements_from_tree(yt.phone.ui_tree())


def snapshot(items=None) -> Snapshot:
    layout = yt.layout()
    return Snapshot(layout.width, layout.height, elements() if items is None else items)


def screen_name(items) -> str | None:
    maps, labels = youtube_maps()
    try:
        return identify(snapshot(items), maps, labels).screen
    except MatchError:
        return None


def wait_screen(*names: str, timeout: float | None = None, what: str = "") -> tuple:
    deadline = time.monotonic() + (SCREEN_TIMEOUT if timeout is None else timeout)
    while True:
        items = elements()
        if screen_name(items) in names:
            return items
        if time.monotonic() >= deadline:
            shown = screen_name(items) or "an unmapped screen"
            raise Stop(f"YouTube shows {shown}, not {what or ' or '.join(names)}; nothing was scheduled")
        time.sleep(0.5)


def element(items, screen: str, name: str):
    maps, labels = youtube_maps()
    locator = next(m for m in maps if m.screen == screen).elements[name]
    try:
        found = find(snapshot(items), locator, labels).element
    except MatchError as exc:
        raise Stop(f"YouTube {screen}: {name} is missing or ambiguous; nothing was scheduled") from exc
    return found


def tap_at(x: float, y: float) -> None:
    yt.phone.tap(x, y)


def check_phone_date(items, zone, now: datetime) -> None:
    """The calendar's "Today" is the iPhone's date; it must be today in the app's time zone."""
    shown = ns.shown_month(items)
    year = int(shown.split()[-1])
    today = ns.phone_today((e.label for e in items), year)
    if today is not None and today != now.astimezone(zone).date():
        raise Stop("The iPhone's date differs from the app's time zone; set both to the same zone. "
                   "Nothing was scheduled")


def picker_shows(items, moment: datetime) -> bool:
    return (ns.day_selected(items, moment.date())
            and ns.same_text(element(items, "schedule_picker", "time").label, ns.picker_time_label(moment)))


def set_time(items, moment: datetime) -> tuple:
    time_button = element(items, "schedule_picker", "time")
    if ns.same_text(time_button.label, ns.picker_time_label(moment)):
        return items
    # Close the wheels on the "Time" label beside them: a pass-through tap only toggles the time.
    labels = [e for e in items if e.type == "StaticText" and e.label == "Time"]
    if not labels:
        raise Stop("YouTube's date alert has no Time label; nothing was scheduled")
    label = labels[0]
    tap_at(time_button.x, time_button.y)
    wait_screen("time_wheels", what="the time wheels")
    try:
        ns.set_wheels(elements, tap_at, moment, pause=time.sleep)
    except ValueError as exc:
        raise Stop(str(exc)) from exc
    wheels = elements()
    popovers = [ns.frame_of(e) for e in wheels if e.type in ("PickerWheel", "Picker", "DatePicker")]
    try:
        tap_at(*ns.outside_point([(label.x, label.y)], popovers))
    except ValueError as exc:
        raise Stop(f"{exc}; nothing was scheduled") from exc
    return wait_screen("schedule_picker", what="the date alert after the time wheels")


def open_picker(items) -> tuple:
    container = next((e for e in items if e.name == "id.elements.components.privacy_settings"), None)
    if container is None:
        raise Stop("YouTube's schedule settings are not on screen; nothing was scheduled")
    schedule = element(items, "schedule", "schedule")
    try:
        x, y = ns.youtube_date_field_point(ns.frame_of(schedule), ns.frame_of(container))
    except ValueError as exc:
        raise Stop(f"{exc}; nothing was scheduled") from exc
    tap_at(x, y)
    return wait_screen("schedule_picker", what="the date alert (the date field tap did not land)")


def enter_slot(moment: datetime, zone, now: datetime) -> dict:
    """From Add details (after preparation): set Schedule to the slot and prove it twice."""
    yt.stage("youtube_schedule")
    for _ in range(5):
        if yt.matches("Visibility", exact=False):
            break
        yt.phone.swipe(*yt.layout().reference_point(220, 300), *yt.layout().reference_point(220, 780), 0.55)
    yt.tap("Visibility", exact=False)
    items = wait_screen("visibility", "schedule", what="Set visibility")
    if screen_name(items) == "visibility":
        chosen = element(items, "visibility", "schedule")
        tap_at(chosen.x, chosen.y)
        items = wait_screen("schedule", what="YouTube's Schedule option")
    items = open_picker(items)
    check_phone_date(items, zone, now)
    try:
        items = ns.pick_day(elements, tap_at, moment.date(), pause=time.sleep)
    except ValueError as exc:
        raise Stop(str(exc)) from exc
    items = set_time(items, moment)
    if not picker_shows(items, moment):
        raise Stop(f"YouTube's picker does not show {ns.day_button_label(moment.date())} at "
                   f"{ns.picker_time_label(moment)}; nothing was scheduled")
    ok = element(items, "schedule_picker", "ok")
    tap_at(ok.x, ok.y)
    items = wait_screen("schedule", what="Set visibility after OK")
    # Read back what YouTube kept: the field is not in the tree, so re-open the picker.
    items = open_picker(items)
    if not picker_shows(items, moment):
        raise Stop("YouTube did not keep the chosen date and time; nothing was scheduled")
    shown = {"date": ns.day_button_label(moment.date()), "time": ns.picker_time_label(moment),
             "month": ns.shown_month(items)}
    ok = element(items, "schedule_picker", "ok")
    tap_at(ok.x, ok.y)
    items = wait_screen("schedule", what="Set visibility after the read-back")
    back = element(items, "schedule", "back")
    tap_at(back.x, back.y)
    wait_screen("details", what="Add details")
    yt.stage("youtube_schedule_set")
    return shown


def release_input(store: Store, release_id: int, now: datetime) -> tuple[dict, datetime]:
    data = youtube_input(store, release_id)
    if data["deliveryMode"] != "schedule":
        raise Stop("This release is Post now; use scripts/phone_youtube.py")
    if not data["scheduledAt"]:
        raise Stop("Reserve a future slot before scheduling YouTube")
    if data["visibility"] != "public":
        raise Stop("YouTube publishes a scheduled Short as public; approve Public visibility first. "
                   "Nothing was scheduled")
    if not isinstance(data["tags"], list) or not all(isinstance(tag, str) and tag for tag in data["tags"]):
        raise Stop("Invalid YouTube tags")
    try:
        moment = ns.slot_on_phone(data["scheduledAt"], store.time_zone(), now + MIN_LEAD)
    except ValueError as exc:
        raise Stop(f"{exc} (at least {MIN_LEAD.seconds // 60} minutes ahead). Nothing was scheduled") from exc
    return data, moment


@phone_lock.locked("YouTube scheduling")
def run(release: str | int, db: Path, *, commit: bool = False, now=None) -> dict:
    if commit and os.environ.get("VIDEO_DROP_TEST_MODE") == "1":
        raise Stop("Posting is disabled in this test session")
    clock = now or (lambda: datetime.now(timezone.utc))
    yt.set_source_db(db)
    with Store(db, load_targets(db.parent)) as store:
        data, moment = release_input(store, int(release), clock())
        zone = store.time_zone()
        yt.connect_sidetap()
        for attempt in range(3):
            try:
                yt.phone.unlock()
                with yt.busy("YouTube schedule", 900), yt.run_guards(yt.phone, store.phone_checks()):
                    yt.layout(refresh=True)
                    yt.ensure_youtube_channel(data["expectedAccount"])
                    yt.open_source_file(data)
                    yt.prepare_youtube(data)
                    shown = enter_slot(moment, zone, clock())
                    result = {"releaseId": data["releaseId"], "account": data["expectedAccount"],
                              "slot": moment.isoformat(), "shown": shown}
                    if not commit:
                        return {"kind": "ready", **result}
                    if moment - MIN_LEAD <= clock().astimezone(moment.tzinfo):
                        raise Stop("The slot is now too close to submit; reserve a later one. Nothing was scheduled")
                    labels = [row["text"] for row in yt.screen() if row.get("text")]
                    if youtube_identity(labels) != data["expectedAccount"].casefold():
                        raise Stop("YouTube channel changed before Upload Short")
                    if yt.phone.current_app().get("bundleId") != BUNDLE:
                        raise Stop("YouTube left the foreground before Upload Short")
                    upload = yt.matches("Upload Short")
                    if len(upload) != 1:
                        raise Stop("Upload Short button is missing or ambiguous")
                    store.mark_unconfirmed(data["releaseId"], "youtube", expected_revision=data["revisionHash"])
                    with yt.busy("YouTube upload", 30, linger=yt.upload_linger(data.get("sizeBytes", 0))):
                        yt.note_upload(yt.YOUTUBE_BUNDLE, yt.upload_linger(data.get("sizeBytes", 0)) * 3)
                        yt.phone.tap(upload[0]["x"], upload[0]["y"])
                        # Same as Post now: a scheduled Short still has to finish uploading in front.
                        if yt.wait_for_upload(data.get("sizeBytes", 0)):
                            yt.clear_upload(yt.YOUTUBE_BUNDLE)
                    # TODO(receipt): YouTube's scheduled list (You > Your videos, or Studio) is not
                    # recorded yet; until it is, a native read-back cannot mark this "scheduled".
                    return {"kind": "unconfirmed", **result,
                            "message": "Schedule submitted with Upload Short; YouTube's scheduled-video "
                                       "receipt is not mapped yet, so check the channel's scheduled videos "
                                       "before any retry"}
            except Exception as exc:
                if not isinstance(exc, yt.WDAError):
                    raise
                destination = next(d for d in store.release(data["releaseId"])["destinations"]
                                   if d["platform"] == "youtube")
                if destination["status"] == "unconfirmed":
                    raise Stop("YouTube final tap is uncertain; check the channel before any retry") from exc
                if attempt == 2:
                    raise Stop(f"Phone link failed after 3 preparation attempts: {exc}") from exc
                yt.stage("recover_phone_link")
                if not yt.recover_link():
                    raise Stop("The phone link could not be restored; nothing was scheduled") from exc
                time.sleep(1)
    raise Stop("YouTube schedule preparation did not finish")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("release_id", type=int)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("VIDEO_DROP_STATE", ROOT / ".state"))
                        / "video-drop.sqlite")
    parser.add_argument("--commit", action="store_true", help="Tap Upload Short once the slot is verified")
    args = parser.parse_args()
    print(json.dumps(run(args.release_id, args.db, commit=args.commit)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - one JSON error line for the caller
        print(json.dumps({"kind": "error", "message": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None
