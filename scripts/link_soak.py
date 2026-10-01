"""Soak the phone link: realistic read-only automation for N minutes, with the supervisor running.

Cycles through opening apps, reading their accessibility trees, swiping, pressing
Home and idling, and (optionally) opening TikTok's autoplaying feed on purpose
to prove a wedge is released by a Home press and never by a restart. Never taps
anything that posts, deletes or types. Takes .state/phone.lock for the run.

Output: .state/link-soak.log (one line per step) and a JSON summary on stdout
that joins the steps with the supervisor's decisions from .state/link-events.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_drop import link_supervisor  # noqa: E402
from scripts import phone_youtube as share  # noqa: E402

SAFE_APPS = ("Settings", "Weather", "Notes", "Clock", "Calculator")
STATE = link_supervisor.state_dir()
LOCK = STATE / "phone.lock"
LOG = STATE / "link-soak.log"


def log(step: str, **fields) -> None:
    line = {"ts": datetime.now().isoformat(timespec="seconds"), "step": step, **fields}
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")
    print(json.dumps(line), flush=True)


def link_state() -> str:
    return link_supervisor.read_status(STATE).get("state", "unknown")


def timed(step: str, fn):
    started = time.monotonic()
    try:
        result = fn()
        log(step, ok=True, seconds=round(time.monotonic() - started, 2))
        return result
    except Exception as exc:
        log(step, ok=False, seconds=round(time.monotonic() - started, 2), error=str(exc)[:200],
            kind=type(exc).__name__, link=link_state())
        raise


def wait_ready(timeout: float) -> float | None:
    """Seconds until the supervisor reports ready again, or None."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if link_state() == "ready" and share.phone.client().is_up():
            return round(time.monotonic() - started, 1)
        time.sleep(3)
    return None


def cycle(phone, index: int, *, wedge: bool) -> dict:
    counts = {"reads": 0, "errors": 0, "recovered": 0, "unrecovered": 0, "wedge_release_seconds": None}

    def read(step: str):
        tree = phone.ui_tree()
        counts["reads"] += 1
        texts = phone.collect_texts(tree)
        log(step, ok=True, elements=len(texts))

    try:
        for app in SAFE_APPS[:3]:
            timed(f"open {app}", lambda app=app: phone.open_app(app, wait_seconds=8))
            time.sleep(1.5)
            read(f"read {app}")
            if app == "Weather":
                layout = share.layout(refresh=True)
                timed("swipe Weather", lambda: phone.swipe(layout.width * .5, layout.height * .7,
                                                            layout.width * .5, layout.height * .3, .4))
                read("read Weather after swipe")
            timed("home", phone.press_home)
            time.sleep(1.0)
        if wedge:
            # The canonical wedge: TikTok's For You feed never answers accessibility requests.
            timed("open TikTok feed (expected to wedge WDA)", lambda: phone.open_app("com.zhiliaoapp.musically"))
            time.sleep(25)
            started = time.monotonic()
            try:
                read("read TikTok feed")
                log("tiktok did not wedge this time")
            except share.WDAError as exc:
                counts["errors"] += 1
                log("wedged as expected", error=str(exc)[:120])
                # Do NOT recover from here: the supervisor must notice and press Home by itself.
                waited = wait_ready(240)
                if waited is None:
                    counts["unrecovered"] += 1
                    log("wedge NOT released by the supervisor within 240s", link=link_state())
                else:
                    counts["recovered"] += 1
                    counts["wedge_release_seconds"] = round(time.monotonic() - started, 1)
                    log("wedge released by the supervisor", seconds=counts["wedge_release_seconds"])
            timed("home", phone.press_home)
        log("idle 60s")
        time.sleep(60)
        read("read Home Screen after idle")
    except share.WDAError as exc:
        counts["errors"] += 1
        log("link error during cycle", error=str(exc)[:160], link=link_state())
        if share.recover_link():
            counts["recovered"] += 1
            log("recovered via recover_link")
        else:
            counts["unrecovered"] += 1
            log("NOT recovered", link=link_state())
    return counts


def events_since(started_at: str) -> list[dict]:
    path = STATE / link_supervisor.EVENTS_FILE
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("ts", "") >= started_at:
            rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--wedge-cycles", type=int, default=2, help="cycles that open TikTok's feed on purpose")
    args = parser.parse_args()
    if LOCK.exists():
        print(f"phone.lock is held: {LOCK.read_text(encoding='utf-8').strip()}", file=sys.stderr)
        return 2
    if not link_supervisor.supervisor_alive(STATE):
        print("the link supervisor is not running; start it first", file=sys.stderr)
        return 2
    LOCK.write_text(f"link soak {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
    started_at = link_supervisor._now_iso()
    totals = {"cycles": 0, "reads": 0, "errors": 0, "recovered": 0, "unrecovered": 0, "wedge_releases": []}
    try:
        share.connect_sidetap()
        phone = share.phone
        phone.unlock()
        log("soak start", minutes=args.minutes, link=link_state())
        deadline = time.monotonic() + args.minutes * 60
        index = 0
        while time.monotonic() < deadline:
            counts = cycle(phone, index, wedge=index < args.wedge_cycles)
            totals["cycles"] += 1
            for key in ("reads", "errors", "recovered", "unrecovered"):
                totals[key] += counts[key]
            if counts["wedge_release_seconds"] is not None:
                totals["wedge_releases"].append(counts["wedge_release_seconds"])
            index += 1
    finally:
        try:
            share.phone.press_home()
        except Exception:
            pass
        LOCK.unlink(missing_ok=True)
    events = events_since(started_at)
    actions = [a for e in events for a in e.get("actions", [])]
    summary = {**totals, "supervisorActions": actions,
               "tunnelRestarts": sum(1 for a in actions if "tunnel" in a),
               "runnerRestarts": sum(1 for a in actions if "runwda" in a),
               "homePresses": sum(1 for a in actions if "springboard" in a),
               "states": sorted({e.get("state", "") for e in events if e.get("state")}),
               "finalLink": link_state()}
    log("soak end", **summary)
    print(json.dumps(summary, indent=2))
    return 0 if totals["unrecovered"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
