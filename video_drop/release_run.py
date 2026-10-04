"""The two posting modes as one ordered phone run per release, and its per-app progress.

Owner's rules (2026-09-30):

- Exactly two modes. Post now posts everything immediately; Schedule is the default.
- Order: YouTube, then Instagram (Facebook and Threads ride its upload as "Also share on…"
  crossposts, per the crosspostFacebook/crosspostThreads settings), then TikTok, then the rest.
- Post now: YouTube, Instagram, TikTok, then a separate Threads post only when the Threads
  crosspost is off and threadsSeparatePost is on.
- Schedule: YouTube and Instagram are scheduled natively in their apps right away, so the PC
  need not be on at the slot for them. Facebook rides Instagram's scheduled crosspost. TikTok
  has no scheduler on this account, so this app posts it at the slot (slot_posts). Native
  Threads scheduling is not built: Threads stays pending and is never posted immediately.

This module only decides. The server runs the steps on its single phone worker.
"""

from __future__ import annotations

from . import core
from .core import threads_post_refusal

MODES = ("post_now", "schedule")
# Flows the phone worker runs now, in order, per mode. TikTok in Schedule is posted at the slot.
RUN_ORDER = {"post_now": ("youtube", "instagram", "tiktok", "threads"), "schedule": ("youtube", "instagram")}
DISPLAY_ORDER = ("youtube", "instagram", "facebook", "threads", "tiktok")
NAMES = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook", "threads": "Threads",
         "tiktok": "TikTok"}
# Destination statuses that mean the app already made its one attempt: never run again.
DONE = {"unconfirmed": "unconfirmed", "posted": "posted", "scheduled": "scheduled", "published": "posted"}
UNCONFIRMED_NOTE = "Final tap sent; check {apps} for it before any retry."


def name(platform: str) -> str:
    return NAMES.get(platform, platform.title())


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def checked_apps(platform: str, release: dict) -> str:
    """The apps to check after ``platform``'s final tap: Instagram names the crossposts it carried."""
    carried = release.get("instagramCrossposts", []) if platform == "instagram" else []
    return _join([name(platform), *map(name, carried)])


def mode_refusal(release: dict, mode: str) -> str | None:
    """Why this release cannot start ``mode`` now, or None."""
    if mode not in MODES:
        return "Choose Post now or Schedule"
    if release["status"] == "discarded":
        return "This video was discarded"
    if release["delivery_mode"] != mode:
        return ("This video is set to Schedule; switch it to Post now first. Nothing was posted."
                if mode == "post_now" else
                "This video is set to Post now; switch it to Schedule first. Nothing was scheduled.")
    if mode == "schedule" and not release["scheduled_at"]:
        return "Reserve a posting time before scheduling. Nothing was scheduled."
    return None


# Schedule happens only on the platforms' own schedulers. TikTok's phone app cannot schedule,
# and this app does not post it at the slot either.
TIKTOK_SCHEDULE_NOTE = ("TikTok can't schedule from its phone app, so Schedule leaves it out. Post it yourself, "
                        "or press Post now on TikTok when you want it out.")


def plan(release: dict, mode: str) -> list[dict]:
    """One row per approved (or already attempted) destination, in the owner's display order.

    Each row: platform, state, note, run (the phone worker runs its flow now) and, for a
    crosspost, via="instagram". States: queued, unconfirmed, posted, scheduled, needs_you
    and with_instagram (at_slot only for an app-posted destination, of which there are none).
    """
    by_platform = {d["platform"]: d for d in release["destinations"]}
    carried = list(release.get("instagramCrossposts", []))
    rows = []

    def row(platform: str, state: str, note: str = "", *, run: bool = False, via: str | None = None) -> None:
        rows.append({"platform": platform, "state": state, "note": note, "run": run,
                     **({"via": via} if via else {})})

    for platform in DISPLAY_ORDER:
        destination = by_platform.get(platform)
        if destination is None or not (destination["revision_hash"] or destination["status"] != "pending"):
            continue
        if destination["status"] != "pending":
            state = DONE.get(destination["status"], "needs_you")
            # ``carried`` is what Instagram's recorded tap carried once it happened (core keeps it).
            row(platform, state, UNCONFIRMED_NOTE.format(apps=name(platform)) if state == "unconfirmed" else "",
                via="instagram" if platform in carried else None)
            continue
        if platform in carried:
            row(platform, "with_instagram", f"Goes out with Instagram's upload through its “Also share on…” "
                                            f"{name(platform)} switch.", via="instagram")
        elif platform == "facebook":
            row(platform, "needs_you", "The Facebook crosspost is off in Settings, so nothing is sent to "
                                       "Facebook. Turn it on, or post it yourself and confirm it here.")
        elif platform == "threads":
            if mode == "post_now" and release.get("threadsSeparatePost"):
                row(platform, "queued", "Posts on its own in the Threads app after the others.", run=True)
            elif mode == "schedule":
                row(platform, "needs_you", "Native Threads scheduling is not built yet (Instagram's scheduler turns "
                                           "its Threads crosspost off), so Threads stays pending. It is never "
                                           "posted immediately.")
            else:
                refusal = threads_post_refusal({**release, "delivery_mode": mode, "threadsSeparatePost": False})
                row(platform, "needs_you", refusal.replace(" Nothing was posted.", " Threads stays pending."))
        elif platform in core.APP_POSTED_DESTINATIONS and mode == "schedule":
            row(platform, "at_slot", f"{name(platform)} has no scheduler on this account, so this app posts it at "
                                     "the slot. Keep this PC and the phone link on then.")
        elif platform == "tiktok" and mode == "schedule":
            row(platform, "needs_you", TIKTOK_SCHEDULE_NOTE)
        elif platform in RUN_ORDER[mode]:
            row(platform, "queued", "Scheduled in its own app when you press Schedule." if mode == "schedule" else "",
                run=True)
        else:
            row(platform, "needs_you", f"This app has no {name(platform)} route.")
    # Threads rides Instagram when crossposted; on its own it comes last, after TikTok.
    rows.sort(key=lambda r: len(DISPLAY_ORDER) if r["platform"] == "threads" and "via" not in r
              else DISPLAY_ORDER.index(r["platform"]))
    return rows


def runnable(rows: list[dict]) -> list[str]:
    """Platforms whose flow the worker runs now, in run order."""
    return [r["platform"] for r in rows if r["run"]]


def summary(mode: str, steps: list[dict]) -> str:
    """One plain paragraph for a finished run."""
    parts = []
    sent = [name(s["platform"]) for s in steps if s["state"] == "unconfirmed"]
    done = [name(s["platform"]) for s in steps if s["state"] in {"posted", "scheduled"}]
    if done:
        parts.append(f"{_join(done)} {'scheduled' if mode == 'schedule' else 'posted'} with a receipt.")
    if sent:
        parts.append(f"{'Schedule' if mode == 'schedule' else 'Final tap'} sent for {_join(sent)}; check "
                     f"{'them' if len(sent) > 1 else 'it'} in the app before any retry.")
    if any(s["state"] == "at_slot" for s in steps):
        parts.append(f"{_join([name(s['platform']) for s in steps if s['state'] == 'at_slot'])} posts from this app "
                     "at the slot; keep this PC and the phone link on then.")
    if mode == "schedule" and any(s["platform"] == "tiktok" and s["state"] == "needs_you" for s in steps):
        parts.append("TikTok can't schedule from its phone app: post it yourself, or press Post now on TikTok "
                     "when you want it out.")
    if mode == "schedule" and any(s["platform"] == "threads" and s["state"] == "needs_you" for s in steps):
        parts.append("Threads needs you: native Threads scheduling isn't built yet, so it stays pending.")
    others = [name(s["platform"]) for s in steps
              if s["state"] == "needs_you" and not (mode == "schedule" and s["platform"] in {"threads", "tiktok"})]
    if others:
        parts.append(f"{_join(others)} {'need' if len(others) > 1 else 'needs'} you; see the list.")
    return " ".join(parts) or "Nothing was sent."


def slot_row(row: dict, slot: dict) -> None:
    """Show the app-posted slot state (server.with_slot_posts) on a pending TikTok row."""
    state = slot["state"]
    if state in {"due", "queued"}:
        row["state"], row["note"] = "running", "Posting TikTok at its slot now."
    elif state == "missed":
        row["state"], row["note"] = "needs_you", (
            f"Missed its slot ({slot.get('reason') or 'the posting window closed'}), so nothing was posted "
            "late. Post TikTok now, or confirm it if you posted it yourself.")
    elif state == "blocked":
        row["state"], row["note"] = "at_slot", f"{slot.get('reason', 'Waiting')}. " + row["note"]


def progress(release: dict, action: dict | None = None) -> list[dict]:
    """The per-app list the page shows: the plan, live run steps, and the TikTok slot state.

    ``action`` is the phone-action status; its steps count only for this release.
    ``release["slotPosts"]`` (server.with_slot_posts) carries the app-posted slot states.
    """
    mode = release["delivery_mode"] if release["delivery_mode"] in MODES else "schedule"
    rows = [{key: value for key, value in r.items() if key != "run"} for r in plan(release, mode)]
    live = {}
    if action and action.get("releaseId") == release["id"] and action.get("steps"):
        live = {step["platform"]: step for step in action["steps"]}
    status = {d["platform"]: d["status"] for d in release["destinations"]}
    for r in rows:
        pending = status[r["platform"]] == "pending"
        step = live.get(r["platform"])
        if step and pending and step["state"] in {"queued", "running", "needs_you", "stopped"}:
            r["state"], r["note"] = step["state"], step.get("message") or r["note"]
        elif step and not pending and step.get("message"):
            r["note"] = step["message"]
        if pending and r["platform"] in release.get("slotPosts", {}):
            slot_row(r, release["slotPosts"][r["platform"]])
        if r["state"] == "queued" and not live:
            r["state"] = "ready"  # nothing is queued until the button is pressed
    return rows
