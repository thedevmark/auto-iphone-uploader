"""Decide when the app itself must post a Schedule-mode destination at its slot.

Most apps schedule natively. Those in APP_POSTED_DESTINATIONS (TikTok on this
account) have no scheduler, so the running app posts them at the slot. This
module only decides; the server does the phone work. Every rule fails toward
"missed", which the UI surfaces with a Post now button, never toward a late or
repeated post:

- a slot is armed (persisted) only while it is still in the future, so a server
  that first sees a slot after it passed never posts it: a separate runner may
  already have;
- a post is started only inside SLOT_GRACE after the slot;
- a queued mark is persisted before the phone is touched, so one slot is
  attempted at most once, across restarts;
- a destination that is not pending is never considered;
- YouTube and Instagram go first, so a still-pending one blocks the post.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .core import APP_POSTED_DESTINATIONS, POST_ORDER, SLOT_GRACE


@dataclass(frozen=True)
class SlotPost:
    release_id: int
    platform: str
    slot: str
    state: str  # arm, armed, post, queued, blocked, missed
    reason: str = ""

    @property
    def deadline(self) -> datetime:
        return datetime.fromisoformat(self.slot) + SLOT_GRACE


def plan_slot_posts(store, now: datetime) -> list[SlotPost]:
    """One decision per pending, approved, app-posted destination of a Schedule release.

    ``store`` needs ``slot_post_releases()`` and ``slot_post_marks()`` (see core.Store).
    """
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    now = now.astimezone(timezone.utc)
    marks = store.slot_post_marks()
    plans = []
    for release in store.slot_post_releases():
        if release["delivery_mode"] != "schedule" or not release["scheduled_at"]:
            continue
        slot_text = release["scheduled_at"]
        slot = datetime.fromisoformat(slot_text)
        if slot.tzinfo is None:
            continue
        by_platform = {d["platform"]: d for d in release["destinations"]}
        for platform in sorted(APP_POSTED_DESTINATIONS, key=POST_ORDER.index):
            destination = by_platform.get(platform)
            if not destination or destination["status"] != "pending" or not destination["revision_hash"]:
                continue
            seen = marks.get((release["id"], platform, slot_text), set())

            def plan(state: str, reason: str = "") -> None:
                plans.append(SlotPost(release["id"], platform, slot_text, state, reason))

            if now < slot:
                plan("armed" if "slot_armed" in seen else "arm")
            elif "slot_post_failed" in seen:
                plan("missed", "The slot's post stopped before the final tap")
            elif "slot_post_queued" in seen:
                plan("queued" if now < slot + SLOT_GRACE else "missed",
                     "" if now < slot + SLOT_GRACE else "The slot's post did not finish in its window")
            elif now >= slot + SLOT_GRACE:
                plan("missed", "The posting window closed")
            elif "slot_armed" not in seen:
                plan("missed", "This app was not running before the slot")
            else:
                earlier = [d["platform"] for d in release["destinations"]
                           if d["platform"] in POST_ORDER[:POST_ORDER.index(platform)]
                           and d["revision_hash"] and d["status"] == "pending"]
                if earlier:
                    plan("blocked", f"Waiting for {', '.join(earlier)} first")
                else:
                    plan("post")
    return plans
