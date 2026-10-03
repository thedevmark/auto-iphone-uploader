from __future__ import annotations

import argparse
from functools import lru_cache
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from .core import Store, digest, next_slot, threads_post_refusal, utc_now
from .slot_posts import SlotPost, plan_slot_posts, receipts_due
from .analyze import OLLAMA, TEXT_MODEL, VISION_MODEL, analyze
from .accounts import load_targets
from .watch import WatchFolder, complete_video, eligible
from .runtime_identity import source_fingerprint
from .phone_space import ensure_room, free_bytes
from . import edits_cleanup, link_supervisor, phone_link, phone_lock, receipt_sweep, release_run, setup_check, timezones

ROOT = Path(__file__).resolve().parent.parent
phone_free_bytes = free_bytes
setup_probes = setup_check.local_probes
STATIC_FILES = {
    "/logo.svg": ("logo.svg", "image/svg+xml"),
    "/logo.ico": ("logo.ico", "image/x-icon"),
    "/fonts/inter-variable.woff2": ("fonts/inter-variable.woff2", "font/woff2"),
}
RUNTIME_FINGERPRINT = source_fingerprint(ROOT)
STATE = Path(os.environ.get("VIDEO_DROP_STATE", ROOT / ".state")).resolve()
TEST_MODE = os.environ.get("VIDEO_DROP_TEST_MODE") == "1"
MAX_JSON = 1024 * 1024
ANALYSIS_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-drop-analysis")
ANALYSIS_LOCK = threading.Lock()
ANALYSIS_PENDING: set[int] = set()
PHONE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="video-drop-phone")
PHONE_LOCK = threading.Lock()
PHONE_RUNNING = False
PHONE_ACTION_LOCK = threading.Lock()
PHONE_ACTION_RUNNING = False
# phone-action.json is rewritten while the page polls it; a read mid-write sees an empty file.
PHONE_ACTION_FILE_LOCK = threading.Lock()
SIDETAP_STATUS_LOCK = threading.Lock()
SIDETAP_STATUS_CACHE: tuple[float, dict] | None = None
MODEL_RECOVERY_INTERVAL = 30
MODEL_RETRY_DELAY_SECONDS = 120
MODEL_RETRY_LIMIT = 3
SLOT_POST_INTERVAL = 20
# How long one phone step waits for the link supervisor to report ready before it stops.
LINK_WAIT_SECONDS = 180.0
PLATFORM_NAMES = {"youtube": "YouTube", "tiktok": "TikTok"}
WATCHER = WatchFolder(STATE, lambda release_id: queue_analysis(release_id))

VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}


@lru_cache(maxsize=32)
def verified_source(path: str, size: int, mtime_ns: int, expected_sha: str) -> bool:
    source = Path(path)
    if digest(source) != expected_sha:
        return False
    after = source.stat()
    return (after.st_size, after.st_mtime_ns) == (size, mtime_ns)


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    if header is None:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not any(match.groups()):
        raise ValueError("Invalid video range")
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix == 0:
            raise ValueError("Invalid video range")
        return max(0, size - suffix), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError("Invalid video range")
    return start, end


def import_finished_video(store: Store, path: Path) -> dict:
    if not eligible(path) or not complete_video(path):
        raise ValueError("Video is still exporting or cannot be fully decoded; wait for the finished file")
    return store.import_file(path)


def phone_inspection() -> dict:
    path = STATE / "phone-inspection.json"
    if not path.is_file():
        return {"status": "idle"}
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("status") == "inspecting" and not PHONE_RUNNING:
        return {"status": "failed", "error": "Phone inspection was interrupted. Inspect again."}
    return result


def sidetap_status() -> dict:
    """Summarize SideTap's own doctor without running a second phone probe."""
    global SIDETAP_STATUS_CACHE
    with SIDETAP_STATUS_LOCK:
        now = time.monotonic()
        if SIDETAP_STATUS_CACHE and now - SIDETAP_STATUS_CACHE[0] < 20:
            return SIDETAP_STATUS_CACHE[1]
        try:
            with urlopen("http://127.0.0.1:8770/api/doctor", timeout=10) as response:
                checks = json.load(response)
            if not isinstance(checks, list) or not checks or any(
                    not isinstance(check, dict) or not isinstance(check.get("name"), str)
                    or not isinstance(check.get("ok"), bool) for check in checks):
                raise ValueError("SideTap returned invalid checks")
            passed = sum(check["ok"] for check in checks)
            first_failed_check = next((check for check in checks if not check["ok"]), {})
            result = {"status": "ready" if passed == len(checks) else "attention",
                      "passed": passed, "total": len(checks),
                      "firstFailure": first_failed_check.get("name", ""),
                      "firstFailureDetail": first_failed_check.get("detail", ""),
                      "firstFailureFix": first_failed_check.get("fix", ""),
                      "checks": [{"name": check["name"], "ok": check["ok"]} for check in checks]}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result = {"status": "unavailable", "passed": 0, "total": 0,
                      "firstFailure": str(exc)[:120],
                      "firstFailureFix": "Start SideTap's viewer, then refresh this page.", "checks": []}
        SIDETAP_STATUS_CACHE = (time.monotonic(), result)
        return result


def setup_status(*, complete: bool = False) -> dict:
    """Run the read-only setup checklist; ``complete`` records a fully passing run."""
    result = setup_check.checklist(setup_probes(STATE, WATCHER.status, phone_inspection))
    if complete and not result["ready"]:
        raise ValueError("Finish the remaining setup steps first")
    with Store(STATE / "video-drop.sqlite") as store:
        result["completedAt"] = store.complete_setup() if complete else store.setup_completed_at()
    return result


def measured_phone_space() -> int | None:
    """Read the iPhone's free space and keep the last reading for the setup checklist."""
    free = phone_free_bytes()
    if free is not None:
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "phone-space.json").write_text(json.dumps({"freeBytes": free, "checkedAt": utc_now().isoformat()}),
                                                encoding="utf-8")
    return free


def queue_phone_inspection() -> dict:
    global PHONE_RUNNING
    with PHONE_LOCK:
        if PHONE_RUNNING:
            return phone_inspection()
        PHONE_RUNNING = True
        state = {"status": "inspecting"}
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "phone-inspection.json").write_text(json.dumps(state), encoding="utf-8")

    PHONE_POOL.submit(run_phone_inspection)
    return state


def run_phone_inspection() -> dict:
    """Run the read-only phone inventory now; callers hold the single phone worker."""
    global PHONE_RUNNING
    with PHONE_LOCK:
        PHONE_RUNNING = True
        STATE.mkdir(parents=True, exist_ok=True)
        (STATE / "phone-inspection.json").write_text(json.dumps({"status": "inspecting"}), encoding="utf-8")
    try:
        env = os.environ.copy()
        env["VIDEO_DROP_STATE"] = str(STATE)
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "phone_onboard.py")],
                                   cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
        output = json.loads((completed.stdout if completed.returncode == 0 else completed.stderr).strip().splitlines()[-1])
        if completed.returncode:
            result = {"status": "failed", "error": str(output.get("error", "Phone inspection failed"))[:240]}
        else:
            result = {"status": "ready", "screenPoints": output["screenPoints"],
                      "installed": output["installed"], "youtube": output["youtube"],
                      "youtubeProbeError": output.get("youtubeProbeError", "")}
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)[:240]}
    finally:
        with PHONE_LOCK:
            PHONE_RUNNING = False
            (STATE / "phone-inspection.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def youtube_quality_gate(checks: dict) -> str:
    """Check YouTube's upload quality on the first upload and, if chosen, every upload."""
    quality = phone_inspection().get("youtube", {}).get("uploadQuality", {})
    if "status" in quality and not checks["youtubeQualityEveryUpload"]:
        return quality["status"]
    result = run_phone_inspection()
    if result["status"] != "ready":
        raise ValueError(f"YouTube quality check could not run; nothing was uploaded. {result.get('error', '')}".strip())
    status = result["youtube"].get("uploadQuality", {}).get("status", "unverified")
    if status == "limited":
        raise ValueError("YouTube is set to upload at reduced quality; nothing was uploaded. "
                         "In YouTube Settings set Upload quality to Full quality, then try again.")
    return status


def write_phone_action(result: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with PHONE_ACTION_FILE_LOCK:
        (STATE / "phone-action.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def phone_action_status() -> dict:
    path = STATE / "phone-action.json"
    with PHONE_ACTION_FILE_LOCK:
        if not path.is_file():
            return {"status": "idle"}
        result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("status") == "running" and not PHONE_ACTION_RUNNING:
        with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
            release = store.release(result["releaseId"])
        if result.get("steps"):
            return interrupted_run(result, release)
        platform = result["platform"]
        destination = next(d for d in release["destinations"] if d["platform"] == platform)
        return {**result, "status": "needs_check" if destination["status"] == "unconfirmed" else "interrupted",
                "message": f"Check {checked_apps(platform)} for a matching post before trying again" if destination["status"] == "unconfirmed"
                           else "Phone preparation stopped before the final tap; reconnect SideTap and try again"}
    return result


def platform_name(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.title())


class PhoneBusy(ValueError):
    """The single phone worker is taken; a slot post tries again on the next tick."""


def slot_post_state(store: Store, release_id: int, platform: str, now: datetime | None = None) -> SlotPost | None:
    return next((plan for plan in plan_slot_posts(store, now or utc_now())
                 if plan.release_id == release_id and plan.platform == platform), None)


# The owner's Post now order. Facebook (and Threads, when crossposted) ride Instagram's upload.
POST_NOW_ORDER = ("youtube", "instagram", "tiktok", "threads")


def post_now_blockers(release: dict, platform: str) -> list[str]:
    """Approved destinations that Post now must finish first, in the owner's order."""
    return [d["platform"] for d in release["destinations"]
            if d["platform"] in POST_NOW_ORDER[:POST_NOW_ORDER.index(platform)]
            and d["revision_hash"] and d["status"] == "pending"]


def checked_apps(platform: str) -> str:
    return "Instagram, Facebook and Threads" if platform == "instagram" else platform_name(platform)


def link_brief(status: dict) -> dict:
    return {"state": status.get("state", "unknown"), "message": status.get("message", "")}


def wait_for_link(on_wait=None, timeout: float = LINK_WAIT_SECONDS) -> dict:
    """Block until the link supervisor reports the phone link ready (phone_link.wait_ready).

    ``on_wait`` gets the supervisor's status on every poll while it is not ready, so the
    page can show what the link is doing. Returns the last status; check ``["state"]``.
    """
    def pause(seconds: float) -> None:
        if on_wait is not None:
            on_wait(link_supervisor.read_status(STATE))
        time.sleep(seconds)
    return phone_link.wait_ready(max(0.0, timeout), state=STATE, sleep=pause)


def link_refusal(status: dict, what: str) -> str:
    message = (status.get("message") or "no answer from the phone").rstrip(".")
    return f"The phone link is not ready ({message}). {what}"


def destination_status(release_id: int, platform: str) -> str:
    with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
        release = store.release(release_id)
    return next(d for d in release["destinations"] if d["platform"] == platform)["status"]


def check_inputs(store: Store, release_id: int, platform: str, mode: str) -> None:
    """Every read-only input check a flow makes before it touches the phone."""
    if platform == "tiktok":
        from scripts.phone_tiktok import release_input
        release_input(store, release_id)
    elif platform == "instagram":
        from scripts.phone_instagram import release_input
        release_input(store, release_id)
    elif platform == "threads":
        from scripts.phone_threads import release_input
        release_input(store, release_id)
    elif mode == "schedule":
        from scripts.phone_youtube_schedule import release_input
        release_input(store, release_id, utc_now())
    else:
        from video_drop.phone_manifest import youtube_input
        if youtube_input(store, release_id)["deliveryMode"] != "post_now":
            raise ValueError("A scheduled video's YouTube is scheduled with the Schedule button; "
                             "choose Post now to upload it immediately")


def run_flow(release_id: int, platform: str, mode: str, *, slot: SlotPost | None = None) -> dict:
    """Run one platform's native flow with commit=True. Each flow marks its own final tap unconfirmed."""
    db = STATE / "video-drop.sqlite"
    if platform == "tiktok":
        from scripts.phone_tiktok import run
        return run(release_id, db, commit=True, not_after=slot.deadline if slot else None)
    if platform == "instagram":
        from scripts.phone_instagram import run  # Post now or Schedule, from the release's mode
        return run(release_id, db, commit=True)
    if platform == "threads":
        from scripts.phone_threads import run
        return run(release_id, db, commit=True)
    with Store(db, load_targets(STATE)) as store:
        checks = store.phone_checks()
    youtube_quality_gate(checks)
    if mode == "schedule":
        from scripts.phone_youtube_schedule import run
        return run(release_id, db, commit=True)
    from scripts.phone_youtube import run
    return run(str(release_id), db, commit=True)


def queue_phone_post(release_id: int, platform: str, *, slot: SlotPost | None = None) -> dict:
    """Run one native phone action in this process so a server exit cannot leave a child posting.

    ``slot`` is set only by the slot scheduler. The slot is claimed here, under the
    phone locks and after every input check, so it is attempted at most once.

    Post now goes YouTube, then Instagram, then TikTok (the postNowInOrder setting).
    Instagram's one upload carries Facebook and Threads through its "Also share on…"
    switches when their crosspost settings are on. Threads gets a separate immediate post
    only in Post now with its crosspost off and the threadsSeparatePost setting on; a
    scheduled video's Threads post is a native Threads schedule.
    """
    global PHONE_ACTION_RUNNING
    if platform == "threads":
        with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
            if refusal := threads_post_refusal(store.release(release_id)):
                raise ValueError(refusal)
    if TEST_MODE:
        raise ValueError("Posting is disabled in this test session")
    if platform not in {"youtube", "instagram", "tiktok", "threads"}:
        raise ValueError("This native phone posting path is not connected")
    # The worker reads the same clock after this call returns, so capture it now.
    clock = utc_now
    if slot is not None and (slot.release_id, slot.platform) != (release_id, platform):
        raise ValueError("Slot post does not match this destination")
    with PHONE_ACTION_LOCK:
        if PHONE_ACTION_RUNNING:
            raise PhoneBusy("A phone action is already running")
        if (other := phone_lock.holder(STATE)) is not None:  # a hand-run flow, soak or operator session
            raise PhoneBusy(f"The iPhone is in use by {other}; try again when it finishes")
        if slot is not None:
            with PHONE_LOCK:
                if PHONE_RUNNING:
                    raise PhoneBusy("The phone check is running")
            if clock() >= slot.deadline:
                raise ValueError("The slot's posting window closed; nothing was posted")
        with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
            release = store.release(release_id)
            if slot is not None and (release["delivery_mode"] != "schedule" or release["scheduled_at"] != slot.slot):
                raise ValueError("The posting time changed; nothing was posted")
            missed = False
            if platform == "tiktok" and release["delivery_mode"] == "schedule" and slot is None:
                state = slot_post_state(store, release_id, platform)
                if state is None or state.state != "missed":
                    raise ValueError("This app posts TikTok itself at the slot; Post now opens only after a missed slot")
                missed = True
            elif release["delivery_mode"] != "post_now" and slot is None:
                raise ValueError("This video is set to schedule; choose Post now to post it immediately")
            if (release["delivery_mode"] == "post_now" and store.phone_checks()["postNowInOrder"]
                    and (earlier := post_now_blockers(release, platform))):
                raise ValueError(f"Post now goes YouTube, then Instagram, then TikTok; post "
                                 f"{' and '.join(map(platform_name, earlier))} first. Nothing was posted.")
            ensure_room(release["file_size"], measured_phone_space())
            check_inputs(store, release_id, platform, "post_now")
            if slot is not None and not store.mark_slot_post(release_id, platform, slot.slot, "slot_post_queued"):
                raise ValueError("This slot was already attempted; check the account before any retry")
            if missed:
                store.record_manual_intervention(release_id, platform, "post_now_after_missed_slot")
        result = {"status": "running", "platform": platform, "releaseId": release_id,
                  "message": (f"Posting {platform_name(platform)} at its slot" if slot else
                              f"Preparing the confirmed video in {platform_name(platform)}")}
        started = dict(result)
        write_phone_action(result)
        PHONE_ACTION_RUNNING = True

    def work() -> None:
        global PHONE_ACTION_RUNNING
        result = {"status": "failed", "platform": platform, "releaseId": release_id,
                  "message": "Phone action stopped before a result was recorded"}
        try:
            if slot is not None and clock() >= slot.deadline:
                raise ValueError("The slot's posting window closed before the phone was free; nothing was posted")
            link = wait_for_link(
                lambda status: write_phone_action({**started, "link": link_brief(status),
                                                   "message": f"Waiting for the phone link: {status.get('message', '')}"}),
                timeout=min(LINK_WAIT_SECONDS, (slot.deadline - clock()).total_seconds()) if slot else LINK_WAIT_SECONDS)
            if link.get("state") != "ready":
                raise ValueError(link_refusal(link, "Nothing was posted."))
            write_phone_action({**started, "link": link_brief(link)})
            outcome = run_flow(release_id, platform, "post_now", slot=slot)
            result = {"status": "needs_check", "platform": platform, "releaseId": release_id,
                      "message": outcome["message"]}
        except Exception as exc:
            with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
                release = store.release(release_id)
                destination = next(d for d in release["destinations"] if d["platform"] == platform)
                if slot is not None and destination["status"] == "pending":
                    store.mark_slot_post(release_id, platform, slot.slot, "slot_post_failed", error=str(exc)[:240])
            uncertain = destination["status"] == "unconfirmed"
            result = {"status": "needs_check" if uncertain else "failed", "platform": platform,
                      "releaseId": release_id,
                      "message": f"Check {checked_apps(platform)} for a matching post before any retry" if uncertain else str(exc)[:240]}
        finally:
            write_phone_action(result)
            with PHONE_ACTION_LOCK:
                PHONE_ACTION_RUNNING = False

    PHONE_POOL.submit(work)
    return result


def plain_error(platform: str, mode: str, exc: Exception) -> str:
    """One plain sentence for a step that stopped before its final tap."""
    text = (str(exc).strip() or type(exc).__name__)[:240]
    name = platform_name(platform)
    if not text.startswith(name):
        text = f"{name} stopped: {text}"
    if "nothing" not in text.lower():
        text = f"{text.rstrip('.')}. Nothing was {'scheduled' if mode == 'schedule' else 'posted'}."
    return text


def queue_release_run(release_id: int, mode: str) -> dict:
    """Start the release's whole Post now or Schedule run on the single phone worker.

    Post now: YouTube, Instagram (its one upload carries the Facebook/Threads crossposts
    Settings leave on), TikTok, then a separate Threads post only when Settings ask for it.
    Schedule: YouTube's and Instagram's native schedulers now (Facebook rides Instagram);
    TikTok is left to the slot scheduler and Threads stays pending (not built). Every input
    is checked before the phone is touched. The run stops at the first error, and a platform
    already attempted (unconfirmed, posted or scheduled) is never run again.
    """
    global PHONE_ACTION_RUNNING
    if TEST_MODE:
        raise ValueError("Posting is disabled in this test session" if mode == "post_now"
                         else "Scheduling is disabled in this test session")
    with PHONE_ACTION_LOCK:
        if PHONE_ACTION_RUNNING:
            raise PhoneBusy("A phone action is already running; wait for it to finish")
        if (other := phone_lock.holder(STATE)) is not None:
            raise PhoneBusy(f"The iPhone is in use by {other}; try again when it finishes")
        with PHONE_LOCK:
            if PHONE_RUNNING:
                raise PhoneBusy("The phone check is running; try again when it finishes")
        with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
            release = store.release(release_id)
            if refusal := release_run.mode_refusal(release, mode):
                raise ValueError(refusal)
            rows = release_run.plan(release, mode)
            platforms = release_run.runnable(rows)
            if platforms:
                ensure_room(release["file_size"], measured_phone_space())
                for platform in platforms:
                    check_inputs(store, release_id, platform, mode)
        steps = [{"platform": row["platform"], "state": row["state"], "message": row["note"],
                  **({"via": row["via"]} if "via" in row else {})} for row in rows]
        if not steps:
            raise ValueError("Confirm at least one app's details first. Nothing was posted.")
        result = {"status": "running", "kind": "run", "mode": mode, "releaseId": release_id,
                  "platform": platforms[0] if platforms else None, "steps": steps,
                  "message": "Waiting for the phone link"}
        if not platforms:
            return {**result, "status": "done", "platform": None, "message": release_run.summary(mode, steps)}
        write_phone_action(result)
        PHONE_ACTION_RUNNING = True
    PHONE_POOL.submit(run_release_steps, release_id, mode, platforms, steps)
    return result


def run_release_steps(release_id: int, mode: str, platforms: list[str], steps: list[dict]) -> dict:
    """The phone worker's half of queue_release_run. Callers hold PHONE_ACTION_RUNNING."""
    global PHONE_ACTION_RUNNING
    by_platform = {step["platform"]: step for step in steps}
    state = {"status": "running", "kind": "run", "mode": mode, "releaseId": release_id,
             "platform": platforms[0], "steps": steps, "message": ""}

    def save(**changes) -> None:
        state.update(changes)
        write_phone_action(state)

    def settle(platform: str, message: str = "") -> str:
        """Copy a platform's recorded status (and its crossposts') into the steps."""
        status = destination_status(release_id, platform)
        step = by_platform[platform]
        if status != "pending":
            step.update(state=release_run.DONE.get(status, "needs_you"), message=message or step["message"])
        for follower in (s for s in steps if s.get("via") == platform):
            follower_status = destination_status(release_id, follower["platform"])
            if follower_status != "pending":
                follower.update(state=release_run.DONE.get(follower_status, "needs_you"),
                                message=f"Carried by Instagram's final tap; check {platform_name(follower['platform'])} "
                                        "before any retry.")
            else:
                follower.update(state="stopped", message="Not sent: Instagram stopped before its final tap.")
        return status

    from . import phone_focus
    session = phone_focus.prepared_session()
    session.__enter__()
    try:
        for index, platform in enumerate(platforms):
            name = platform_name(platform)
            step = by_platform[platform]
            with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
                release = store.release(release_id)
            status = next(d for d in release["destinations"] if d["platform"] == platform)["status"]
            if release["delivery_mode"] != mode:
                raise ValueError("The delivery choice changed during the run; nothing more was sent")
            if status != "pending":
                # Never replay a platform whose final tap is recorded, even an uncertain one.
                settle(platform, f"Already attempted ({status}); not run again. Check {name} before any retry.")
                save()
                continue
            step.update(state="running", message="Waiting for the phone link")
            save(platform=platform, message=f"Waiting for the phone link before {name}")
            link = wait_for_link(lambda link_status: save(
                link=link_brief(link_status),
                message=f"Waiting for the phone link before {name}: {link_status.get('message', '')}"))
            try:
                if link.get("state") != "ready":
                    raise ValueError(link_refusal(link, f"Nothing was sent to {name}."))
                step["message"] = f"Scheduling in {name}" if mode == "schedule" else f"Posting in {name}"
                save(link=link_brief(link), message=step["message"])
                outcome = run_flow(release_id, platform, mode)
                if settle(platform, str(outcome.get("message", ""))) == "pending":
                    raise ValueError(f"{name} finished without its final tap")
            except Exception as exc:
                uncertain = settle(platform) == "unconfirmed"
                if uncertain:
                    carried = [s["platform"] for s in steps if s.get("via") == platform and s["state"] != "stopped"]
                    apps = release_run.checked_apps(platform, {"instagramCrossposts": carried})
                    step["message"] = f"The final tap may have gone through; check {apps} before any retry."
                else:
                    step.update(state="needs_you", message=plain_error(platform, mode, exc))
                for later in platforms[index + 1:]:
                    by_platform[later].update(state="stopped", message=f"Not started: {name} stopped first.")
                save(status="needs_check" if uncertain else "failed", message=step["message"])
                return state
        save(status="done", platform=None, message=release_run.summary(mode, steps))
        return state
    except Exception as exc:  # a store or state failure between steps: report it, never retry
        for step in steps:
            if step["state"] in {"queued", "running"}:
                step.update(state="stopped", message="Not started: the run stopped first.")
        save(status="failed", message=f"The run stopped: {str(exc)[:200]}")
        return state
    finally:
        # One restore for the whole run (Low Power Mode, Auto-Lock, Do Not Disturb, rotation).
        session.__exit__(None, None, None)
        if phone_focus.restore_problem:
            save(message=f"{state.get('message') or ''} {phone_focus.restore_problem}".strip())
        with PHONE_ACTION_LOCK:
            PHONE_ACTION_RUNNING = False


def interrupted_run(result: dict, release: dict) -> dict:
    """A run the server lost mid-way (restart or crash): report what the database knows."""
    status = {d["platform"]: d["status"] for d in release["destinations"]}
    steps, uncertain = [], False
    for step in result["steps"]:
        step = dict(step)
        if step["state"] in {"queued", "running"}:
            if status[step["platform"]] == "unconfirmed":
                uncertain = True
                step.update(state="unconfirmed", message=f"The final tap may have gone through; check "
                                                         f"{checked_apps(step['platform'])} before any retry.")
            else:
                step.update(state="stopped", message="Stopped before its final tap when the app closed.")
        steps.append(step)
    return {**result, "steps": steps, "status": "needs_check" if uncertain else "interrupted",
            "message": ("Check the apps marked unconfirmed before any retry" if uncertain else
                        "The run stopped before a final tap; press the button again to continue")}


def slot_post_tick(now: datetime | None = None) -> list[SlotPost]:
    """Arm future slots and start due app-posted destinations. Returns the posts started."""
    if TEST_MODE:
        return []
    now = now or utc_now()
    with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
        plans = plan_slot_posts(store, now)
        for plan in plans:
            if plan.state == "arm":
                store.mark_slot_post(plan.release_id, plan.platform, plan.slot, "slot_armed")
    started = []
    for plan in plans:
        if plan.state != "post":
            continue
        try:
            queue_phone_post(plan.release_id, plan.platform, slot=plan)
            started.append(plan)
        except PhoneBusy:
            continue
        except (ValueError, RuntimeError, OSError) as exc:
            with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
                store.mark_slot_post(plan.release_id, plan.platform, plan.slot, "slot_post_failed", error=str(exc)[:240])
            write_phone_action({
                "status": "failed", "platform": plan.platform, "releaseId": plan.release_id,
                "message": f"{platform_name(plan.platform)} did not post at its slot: {exc}"[:240]})
    return started


def receipt_sweep_tick(now: datetime | None = None) -> list[tuple[int, str]]:
    """Start the receipt reads due now on the phone worker (read-only). Returns what started.

    Skipped, without spending the attempt, while any phone action or check runs or the link
    is not ready; the next tick inside the same window tries again.
    """
    global PHONE_ACTION_RUNNING
    if TEST_MODE:
        return []
    now = now or utc_now()
    with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
        if not store.phone_checks()["readReceipts"]:
            return []
        checks = receipt_sweep.due(receipt_sweep.unconfirmed(store.db), receipt_sweep.load_done(STATE), now)
    # phone.lock: a soak, link experiment or operator session holds the phone; a background read
    # would land in the middle of it. A declared busy window (phone-busy.json) is a posting
    # script's upload or export, hand-run or not: an upload that is still running cannot show a
    # receipt, and a read on top of it would spend the attempt for nothing.
    if (not checks or phone_lock.holder(STATE) is not None or link_supervisor.read_busy(STATE) is not None
            or link_supervisor.read_status(STATE).get("state") != "ready"):
        return []
    with PHONE_ACTION_LOCK:
        if PHONE_ACTION_RUNNING:
            return []
        with PHONE_LOCK:
            if PHONE_RUNNING:
                return []
        PHONE_ACTION_RUNNING = True
    PHONE_POOL.submit(run_receipt_checks, checks, now)
    return [(release_id, platform) for release_id, platform, _ in checks]


def run_receipt_checks(checks: list[tuple[int, str, str]], now: datetime) -> list[dict]:
    """The phone worker's half of receipt_sweep_tick. Callers hold PHONE_ACTION_RUNNING."""
    global PHONE_ACTION_RUNNING
    from scripts import phone_receipts

    results = []
    try:
        done = receipt_sweep.load_done(STATE)
        for release_id, platform, key in checks:
            done.add(key)  # spent before the read: a crash mid-read never repeats it in a burst
            receipt_sweep.save_done(STATE, done, now)
            try:
                results.append(phone_receipts.run(release_id, STATE / "video-drop.sqlite", platform=platform))
            except Exception as exc:  # one release's failure never blocks the next
                results.append({"releaseId": release_id, "platform": platform, "kind": "error",
                                "message": str(exc)[:200]})
        (STATE / "receipt-sweep.json").write_text(json.dumps({"at": now.isoformat(), "results": results},
                                                             ensure_ascii=False), encoding="utf-8")
        return results
    finally:
        with PHONE_ACTION_LOCK:
            PHONE_ACTION_RUNNING = False


CLEANUP_ATTEMPTS_FILE = "edits-cleanup-attempts.json"
CLEANUP_MAX_ATTEMPTS = 3


def cleanup_due() -> list[int]:
    """Releases whose Edits project may go to Trash now: noted before upload, not settled,
    Instagram confirmed, and fewer than CLEANUP_MAX_ATTEMPTS failed tries."""
    try:
        attempts = json.loads((STATE / CLEANUP_ATTEMPTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        attempts = {}
    due = []
    with Store(STATE / "video-drop.sqlite", load_targets(STATE)) as store:
        if not store.phone_checks()["removeAfterPost"]:
            return []
        for note in sorted((STATE / "receipts").glob("release*-edits-before.json")):
            try:
                data = json.loads(note.read_text(encoding="utf-8"))
                release_id = int(data["releaseId"])
                if data.get("cleanup") or attempts.get(str(release_id), 0) >= CLEANUP_MAX_ATTEMPTS:
                    continue
                if edits_cleanup.ready(store.release(release_id)):
                    due.append(release_id)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return due


def cleanup_tick() -> list[int]:
    """Start due Edits cleanups on the phone worker, under the same guards as receipt reads."""
    global PHONE_ACTION_RUNNING
    if TEST_MODE:
        return []
    due = cleanup_due()
    if (not due or phone_lock.holder(STATE) is not None or link_supervisor.read_busy(STATE) is not None
            or link_supervisor.read_status(STATE).get("state") != "ready"):
        return []
    with PHONE_ACTION_LOCK:
        if PHONE_ACTION_RUNNING:
            return []
        with PHONE_LOCK:
            if PHONE_RUNNING:
                return []
        PHONE_ACTION_RUNNING = True
    PHONE_POOL.submit(run_cleanups, due)
    return due


def run_cleanups(release_ids: list[int]) -> list[dict]:
    """The phone worker's half of cleanup_tick. Callers hold PHONE_ACTION_RUNNING."""
    global PHONE_ACTION_RUNNING
    from scripts import phone_cleanup

    results = []
    try:
        for release_id in release_ids:
            try:
                results.append(phone_cleanup.run(release_id, STATE / "video-drop.sqlite"))
            except Exception as exc:  # counted; after CLEANUP_MAX_ATTEMPTS the release is left alone
                path = STATE / CLEANUP_ATTEMPTS_FILE
                try:
                    attempts = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    attempts = {}
                attempts[str(release_id)] = attempts.get(str(release_id), 0) + 1
                path.write_text(json.dumps(attempts), encoding="utf-8")
                results.append({"releaseId": release_id, "kind": "error", "message": str(exc)[:200]})
        return results
    finally:
        with PHONE_ACTION_LOCK:
            PHONE_ACTION_RUNNING = False


def slot_post_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            slot_post_tick()
        except Exception as exc:  # keep the scheduler alive; the next tick reads fresh state
            print(f"Slot scheduler: {exc}", file=sys.stderr, flush=True)
        try:
            receipt_sweep_tick()
        except Exception as exc:
            print(f"Receipt reads: {exc}", file=sys.stderr, flush=True)
        try:
            cleanup_tick()
        except Exception as exc:
            print(f"Edits cleanup: {exc}", file=sys.stderr, flush=True)
        stop.wait(SLOT_POST_INTERVAL)


def with_slot_posts(store: Store, releases: list[dict]) -> list[dict]:
    """Add each release's app-posted slot state for the UI (waiting, due, queued, blocked or missed)
    and which destinations offer the manual phone-check receipt buttons."""
    labels = {"arm": "waiting", "armed": "waiting", "post": "due"}
    now = utc_now()
    plans = plan_slot_posts(store, now)
    try:
        action = phone_action_status()
    except (ValueError, OSError, KeyError, StopIteration):
        action = None
    for release in releases:
        release["slotPosts"] = {plan.platform: {"state": labels.get(plan.state, plan.state), "slot": plan.slot,
                                                "until": plan.deadline.isoformat(), "reason": plan.reason}
                                for plan in plans if plan.release_id == release["id"]}
        release["receiptDue"] = receipts_due(release, plans, now)
        release["progress"] = release_run.progress(release, action)
    return releases


def initial_video_folder() -> Path:
    configured = os.environ.get("VIDEO_DROP_SOURCE_DIR")
    candidates = ([Path(configured)] if configured else [])
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        candidates.append(Path(onedrive) / "_Videos")
    candidates.extend((Path.home() / "OneDrive" / "_Videos", Path.home() / "Videos", Path.home()))
    return next(path for path in candidates if path.is_dir())


def local_video_files() -> dict:
    """List local candidates; final import still decodes and fingerprints the file."""
    folder = initial_video_folder().resolve()
    files = []
    for path in folder.rglob("*"):
        if not path.is_file() or not eligible(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append({"path": str(path), "name": path.name, "size": stat.st_size,
                      "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()})
    files.sort(key=lambda item: item["modified"], reverse=True)
    return {"folder": str(folder), "files": files}


def local_video_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Choose a video from the list")
    folder = initial_video_folder().resolve()
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_relative_to(folder) or not path.is_file() or not eligible(path):
        raise ValueError("Choose a finished video from the video folder")
    return path


def choose_local_video() -> Path | None:
    command = [sys.executable, str(ROOT / "scripts" / "pick_video.py"),
               "--initial-dir", str(initial_video_folder())]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                               timeout=300, creationflags=flags)
    if completed.returncode:
        raise ValueError("The video picker could not open")
    try:
        selected = json.loads(completed.stdout)["path"]
    except (ValueError, KeyError) as exc:
        raise ValueError("The video picker returned no usable file") from exc
    return Path(selected) if selected else None


def choose_watch_folder() -> Path | None:
    saved = WATCHER.configuration()["path"]
    previous = Path(saved) if saved else None
    initial = previous if previous and previous.is_dir() else initial_video_folder()
    command = [sys.executable, str(ROOT / "scripts" / "pick_folder.py"),
               "--initial-dir", str(initial)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                               timeout=300, creationflags=flags)
    if completed.returncode:
        raise ValueError("The folder picker could not open")
    selected = json.loads(completed.stdout)["path"]
    return Path(selected) if selected else None


def queue_analysis(release_id: int) -> None:
    with ANALYSIS_LOCK:
        if release_id in ANALYSIS_PENDING:
            return
        ANALYSIS_PENDING.add(release_id)

    def work() -> None:
        attempts = 0
        try:
            with Store(STATE / "video-drop.sqlite") as store:
                release = store.release(release_id)
                if release["status"] != "draft":
                    return
                previous = json.loads(release["analysis_json"])
                attempts = int(previous.get("attempts", 0)) + 1
                store.save_analysis(release_id, {"status": "processing", "attempts": attempts})
                path = Path(release["source_path"])
                if digest(path) != release["sha256"]:
                    raise ValueError("Source video changed after import")
            result = analyze(path)
            result["attempts"] = attempts
            with Store(STATE / "video-drop.sqlite") as store:
                store.save_analysis(release_id, result)
        except Exception as exc:
            with Store(STATE / "video-drop.sqlite") as store:
                try:
                    store.save_analysis(release_id, {"status": "failed", "attempts": attempts,
                                                     "warnings": [str(exc)[:400]]})
                except ValueError:
                    pass
        finally:
            with ANALYSIS_LOCK:
                ANALYSIS_PENDING.discard(release_id)

    ANALYSIS_POOL.submit(work)


def local_models_ready() -> bool:
    try:
        with urlopen(f"{OLLAMA}/api/tags", timeout=2) as response:
            models = json.load(response).get("models", [])
        names = {item.get("name") for item in models if isinstance(item, dict)}
        return VISION_MODEL in names and TEXT_MODEL in names
    except (OSError, ValueError, KeyError):
        return False


def retry_waiting_draft(*, force: bool = False) -> bool:
    with Store(STATE / "video-drop.sqlite") as store:
        waiting = []
        for release in store.drafts():
            result = json.loads(release["analysis_json"])
            warnings = result.get("warnings", [])
            attempts = int(result.get("attempts", 0))
            age = (utc_now() - datetime.fromisoformat(release["updated_at"])).total_seconds()
            if attempts >= MODEL_RETRY_LIMIT or (not force and age < MODEL_RETRY_DELAY_SECONDS * max(1, attempts)):
                continue
            interrupted = result.get("status") == "processing" and age >= 600
            model_failed = result.get("status") in {"partial", "failed"} and any(
                isinstance(warning, str) and warning.startswith("local ") and "model:" in warning
                for warning in warnings
            )
            if interrupted or model_failed:
                waiting.append(release["id"])
    for release_id in waiting:
        queue_analysis(release_id)
    return bool(waiting)


def model_recovery_loop(stop: threading.Event) -> None:
    previously_ready = False
    while not stop.is_set():
        ready = local_models_ready()
        if ready:
            retry_waiting_draft(force=not previously_ready)
        previously_ready = ready
        stop.wait(MODEL_RECOVERY_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def _trusted_request(self) -> bool:
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if host not in allowed_hosts or (origin is not None and origin.lower() != f"http://{host}"):
            self.send_error(403, "Local app requests must come from this origin")
            return False
        return True

    def _json(self, status: int, data: object) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > MAX_JSON:
            raise ValueError("Invalid request size")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def _store(self) -> Store:
        return Store(STATE / "video-drop.sqlite", load_targets(STATE))

    def do_GET(self) -> None:
        if not self._trusted_request():
            return
        path = urlsplit(self.path).path
        if path == "/":
            body = (ROOT / "web" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path in STATIC_FILES:
            name, content_type = STATIC_FILES[path]
            body = (ROOT / "web" / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/runtime":
            self._json(200, {"root": str(ROOT.resolve()), "stateDir": str(STATE),
                             "sourceFingerprint": RUNTIME_FINGERPRINT})
        elif match := re.fullmatch(r"/api/releases/(\d+)/video", path):
            try:
                with self._store() as store:
                    release = store.release(int(match[1]))
                source = Path(release["source_path"])
                info = source.stat()
                if (not source.is_file() or info.st_size != release["file_size"]
                        or not verified_source(str(source), info.st_size, info.st_mtime_ns, release["sha256"])):
                    raise ValueError("Source video changed or is missing")
                try:
                    requested = byte_range(self.headers.get("Range"), info.st_size)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{info.st_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = requested or (0, info.st_size - 1)
                with source.open("rb") as video:
                    if video.seek(0, 2) != info.st_size or source.stat().st_mtime_ns != info.st_mtime_ns:
                        raise ValueError("Source video changed or is missing")
                    video.seek(start)
                    self.send_response(206 if requested else 200)
                    self.send_header("Content-Type", VIDEO_TYPES.get(source.suffix.lower(), "application/octet-stream"))
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    if requested:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{info.st_size}")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    remaining = end - start + 1
                    try:
                        while remaining:
                            chunk = video.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # Seeking or switching clips closes the previous browser request.
            except (ValueError, OSError):
                self.send_error(404, "Video unavailable")
        elif path == "/api/current":
            with self._store() as store:
                self._json(200, {"release": store.current(), "stateDir": str(STATE), "testMode": TEST_MODE})
        elif match := re.fullmatch(r"/api/releases/(\d+)", path):
            try:
                with self._store() as store:
                    release = with_slot_posts(store, [store.release(int(match[1]))])[0]
                    self._json(200, {"release": release, "testMode": TEST_MODE})
            except ValueError as exc:
                self._json(404, {"error": str(exc)})
        elif path == "/api/queue":
            with self._store() as store:
                rows = store.db.execute("SELECT id FROM release WHERE status IN ('draft','reserved','scheduled','uploading','partial','needs_check') ORDER BY CASE WHEN status='draft' THEN 0 ELSE 1 END, scheduled_at, id").fetchall()
                self._json(200, {"releases": with_slot_posts(store, [store.release(row[0]) for row in rows])})
        elif path == "/api/settings":
            with self._store() as store:
                slots = store.posting_slots()
                zone_info = store.time_zone()
                occupied = {row[0] for row in store.db.execute("SELECT scheduled_at FROM release WHERE scheduled_at IS NOT NULL AND status != 'discarded'")}
                upcoming = []
                # Free times the editor offers when a video is scheduled.
                for _ in range(10):
                    slot = next_slot(utc_now(), occupied, slots, zone_info)
                    upcoming.append(slot.isoformat())
                    occupied.add(slot.astimezone(timezone.utc).isoformat())
                self._json(200, {"watch": WATCHER.status(), "postingSlots": slots,
                                 "timeZone": timezones.zone_key(zone_info),
                                 "timeZoneSetting": store.time_zone_setting(),
                                 "pcTimeZone": timezones.pc_zone_name() or "", "nextSlots": upcoming,
                                 "phoneChecks": store.phone_checks(),
                                 "setupCompletedAt": store.setup_completed_at(),
                                 "unattendedStreak": store.unattended_streak(),
                                 "youtubeQuality": phone_inspection().get("youtube", {}).get("uploadQuality", {})})
        elif path == "/api/phone":
            self._json(200, phone_inspection())
        elif path == "/api/phone-action":
            self._json(200, phone_action_status())
        elif path == "/api/source-files":
            self._json(200, local_video_files())
        elif path == "/api/sidetap":
            self._json(200, sidetap_status())
        elif path == "/api/link":
            self._json(200, link_supervisor.read_status(STATE))
        elif path == "/api/setup":
            self._json(200, setup_status())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if not self._trusted_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/import-path":
            try:
                value = self._body().get("path")
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Enter the full path to a finished video")
                selected = Path(value).expanduser().resolve(strict=True)
                with self._store() as store:
                    release = import_finished_video(store, selected)
                self._json(201, {"release": release})
                queue_analysis(release["id"])
            except (ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/source-files/import":
            try:
                selected = local_video_path(self._body().get("path"))
                with self._store() as store:
                    release = import_finished_video(store, selected)
                self._json(201, {"release": release})
                queue_analysis(release["id"])
            except (ValueError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/pick-file":
            try:
                selected = choose_local_video()
                if selected is None:
                    self._json(200, {"cancelled": True})
                    return
                with self._store() as store:
                    release = import_finished_video(store, selected)
                self._json(201, {"release": release})
                queue_analysis(release["id"])
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/watch/pick-folder":
            try:
                selected = choose_watch_folder()
                self._json(200, {"cancelled": selected is None,
                                 "watch": WATCHER.configure(folder=selected) if selected else WATCHER.configuration()})
            except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path in {"/api/watch/toggle", "/api/settings/slots", "/api/settings/phone-checks", "/api/settings/time-zone"}:
            try:
                data = self._body()
                if path == "/api/watch/toggle":
                    if not isinstance(data.get("enabled"), bool):
                        raise ValueError("Expected an on/off value")
                    self._json(200, {"watch": WATCHER.configure(enabled=data["enabled"])})
                elif path == "/api/settings/phone-checks":
                    with self._store() as store:
                        self._json(200, {"phoneChecks": store.set_phone_checks(data.get("phoneChecks"))})
                elif path == "/api/settings/time-zone":
                    with self._store() as store:
                        self._json(200, {"timeZoneSetting": store.set_time_zone(data.get("timeZone")),
                                         "timeZone": timezones.zone_key(store.time_zone())})
                else:
                    with self._store() as store:
                        self._json(200, {"postingSlots": store.set_posting_slots(data.get("slots"))})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/phone/inspect":
            self._json(200, queue_phone_inspection())
            return
        if path == "/api/setup/complete":
            try:
                self._json(200, setup_status(complete=True))
            except (ValueError, OSError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/queue/plan":
            try:
                if TEST_MODE:
                    raise ValueError("Slot planning is disabled in this test session")
                body = self._body()
                release_ids, at = body.get("releaseIds"), body.get("at")
                if (not isinstance(release_ids, list) or not release_ids
                        or any(type(release_id) is not int or release_id <= 0 for release_id in release_ids)):
                    raise ValueError("Choose finished videos to plan")
                if at is not None and not isinstance(at, str):
                    raise ValueError("Posting time must be an ISO date and time")
                with self._store() as store:
                    planned = store.reserve_batch(sorted(release_ids), at=datetime.fromisoformat(at) if at else None)
                    planned = with_slot_posts(store, planned)
                self._json(200, {"releases": planned, "nativeScheduled": False})
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if match := re.fullmatch(r"/api/releases/(\d+)/(post-now|schedule)", path):
            try:
                self._body()
                run = queue_release_run(int(match[1]), "post_now" if match[2] == "post-now" else "schedule")
                with self._store() as store:
                    release = with_slot_posts(store, [store.release(int(match[1]))])[0]
                self._json(202, {"run": run, "release": release})
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if match := re.fullmatch(r"/api/releases/(\d+)/(threads|youtube|instagram|tiktok)-post", path):
            try:
                self._body()
                self._json(202, queue_phone_post(int(match[1]), match[2]))
            except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        match = re.fullmatch(r"/api/releases/(\d+)/(text|shared-title|shared-copy|authorize|delivery-mode|unconfirmed|receipt|manual-receipt|discard|analyze)", path)
        try:
            data = self._body()
            with self._store() as store:
                if match:
                    release_id, action = int(match[1]), match[2]
                    platform = str(data.get("platform", ""))
                    if action == "text":
                        result = store.save_text(release_id, platform, str(data.get("account", "")), str(data.get("title", "")), str(data.get("description", "")), str(data.get("tags", "")), str(data.get("visibility", "public")))
                    elif action == "shared-title":
                        result = store.apply_shared_title(release_id, str(data.get("title", "")))
                    elif action == "shared-copy":
                        result = store.apply_shared_copy(release_id, str(data.get("title", "")), str(data.get("hashtags", "")))
                    elif action == "authorize":
                        result = store.authorize(release_id, platform)
                    elif action == "delivery-mode":
                        result = store.set_delivery_mode(release_id, data.get("mode"))
                    elif action == "unconfirmed":
                        result = store.mark_unconfirmed(release_id, platform)
                    elif action == "receipt":
                        raise ValueError("Provider receipt verification is not connected yet")
                    elif action == "manual-receipt":
                        result = store.record_manual_receipt(release_id, platform, str(data.get("choice", "")))
                    elif action == "analyze":
                        if store.release(release_id)["status"] != "draft":
                            raise ValueError("Only a draft can be analyzed")
                        result = store.release(release_id)
                    else:
                        result = store.discard(release_id)
                    result = with_slot_posts(store, [result])[0]
                else:
                    self.send_error(404)
                    return
            self._json(200, {"release": result})
            if match and match[2] == "analyze":
                queue_analysis(result["id"])
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Auto iPhone Uploader server")
    parser.add_argument("--port", type=int, default=4748)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    inspection = phone_inspection()
    with Store(STATE / "video-drop.sqlite") as store:
        inspect_on_open = store.phone_checks()["inspectPhoneOnOpen"]
    if inspect_on_open and (inspection["status"] in {"idle", "failed"} or (inspection["status"] == "ready" and
            "uploadQuality" not in inspection.get("youtube", {}))):
        queue_phone_inspection()
    recovery_stop = threading.Event()
    watch_stop = threading.Event()
    with Store(STATE / "video-drop.sqlite") as store:
        for draft in store.drafts():
            status = json.loads(draft["analysis_json"]).get("status", "pending")
            if status in {"pending", "processing"}:
                queue_analysis(draft["id"])
    recovery_thread = threading.Thread(target=model_recovery_loop, args=(recovery_stop,),
                                       name="video-drop-model-recovery", daemon=True)
    recovery_thread.start()
    watch_thread = threading.Thread(target=WATCHER.run, args=(watch_stop,),
                                    name="video-drop-watch-folder", daemon=True)
    watch_thread.start()
    slot_stop = threading.Event()
    slot_thread = threading.Thread(target=slot_post_loop, args=(slot_stop,),
                                   name="video-drop-slot-posts", daemon=True)
    slot_thread.start()
    if not TEST_MODE:
        try:
            # One detached process owns the phone link for every script and every server restart.
            started = link_supervisor.ensure_running(STATE)
            print(f"phone link supervisor: {'started' if started['started'] else 'already running'}", flush=True)
        except OSError as exc:
            print(f"phone link supervisor could not start: {exc}", flush=True)
    print(f"Auto iPhone Uploader: http://127.0.0.1:{args.port}  state: {STATE}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        recovery_stop.set()
        watch_stop.set()
        slot_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
