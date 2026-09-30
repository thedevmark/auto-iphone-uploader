"""One long-lived process that owns the phone link: tunnel, WDA runner, port forwards.

Why this exists (evidence gathered 2026-09-29/30 on the reference iPhone 16 Pro Max):

* Windows logged the phone as "surprise removed" from the USB bus 35/137/84/11
  times a day over four days (Kernel-PnP event 1010), and every overnight
  "down" in .state/link-health.log lines up with one of those events to the
  second. Each re-enumeration kills the tunnel route, orphans the WDA runner,
  and forces a full bring-up. go-ios's own tunnel daemon notices the device
  leaving and re-creates the tunnel seconds after it returns, so the daemon must
  be LEFT ALONE across a USB drop; only the runner and forwards need restarting.
* Video surfaces (TikTok feed, Instagram Reels / reel editor, Threads autoplay)
  wedge WebDriverAgent: it accepts the connection and never answers. Putting
  SpringBoard in front releases it; restarting it on top of the stuck runner
  fails with XCTest error 103. So a wedge is never a restart.
* A tunnel whose route to the phone dies while the phone is still on the bus
  (seen 10:47 on 2026-09-30, no USB event) cannot be re-negotiated by a new
  daemon either. Only a replug fixes it, so after one daemon restart the user
  is told to replug instead of the tunnel being killed in a loop.
* SideTap's ``up()`` decided from one 5s probe; a slow answer under load killed
  healthy tunnels. Here every restart needs consecutive failures across ticks,
  and a declared busy window (an upload or export in progress) stretches every
  threshold.

The decision logic (``Decider``) is pure and driven by ``Observation`` values so
tests inject probes. ``Runner`` gathers observations from go-ios/WDA and applies
the actions. ``ensure_running`` spawns the runner fully detached so a posting
script, the app server, or the shell that started it can exit without taking
the link down. Status lives in ``.state/link-status.json`` (plain language for
the UI) and every decision in ``.state/link-events.jsonl``.

This is the ONLY code that starts or stops the go-ios processes. It drives
them through the vendored driver (``video_drop/phone``), whose pid files, logs
and shared WDA session live in ``.state/phone/`` beside these files.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WDA_STATUS = "http://127.0.0.1:8100/status"
TUNNEL_API_PORT = 60105  # go-ios tunnel daemon's local API (GET /tunnels, DELETE /tunnel/{udid})
# SideTap's viewer, when a human runs it, listens here and heals the link on
# its own. It keeps its own state folder, so its pid file is invisible to us;
# the port is the one thing that says it is alive.
SIDETAP_VIEWER_PORT = 8770

STATUS_FILE = "link-status.json"
EVENTS_FILE = "link-events.jsonl"
BUSY_FILE = "phone-busy.json"
REQUEST_FILE = "link-request"
PID_FILE = "link-supervisor.pid"
LOG_DIR = "link-logs"


def state_dir() -> Path:
    return Path(os.environ.get("VIDEO_DROP_STATE", ROOT / ".state")).resolve()


# ---- observations and decisions --------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One tick's view of the link. ``None`` means "not probed this tick"."""

    device_present: bool | None = None  # `ios list` sees the phone over USB
    tunnel_alive: bool = False  # tunnel daemon process is running
    tunnel_entry: bool = False  # `ios tunnel ls` lists an address + rsdPort
    tunnel_listening: bool | None = None  # the listed userspace port accepts connections (None: not probed)
    route_ok: bool | None = None  # an RSD round trip (`ios image list`) answered
    runwda_alive: bool = False  # `ios runwda` process is running
    forwards_alive: bool = False  # both `ios forward` processes are running
    wda: str = "down"  # "up" (answers), "wedged" (accepts, never answers), "down" (refused)
    activity_landed: bool = False  # another process landed a WDA action since the last tick
    busy: bool = False  # a script declared an upload/export in progress
    runner_error: str = ""  # last "Failed running WDA" error from runwda.log, when the runner is dead


# What a dead runner's last error means for the person holding the phone.
RUNNER_HINTS = (
    ("enabling automation mode", "Unlock the phone and allow UI automation if iOS asks (enter the passcode on the phone)"),
    ("error 103", "The previous driver is still stuck on the phone: put it on the Home Screen, the link retries by itself"),
    ("lost connection to testmanagerd", "iOS stopped the input driver (video app pressure or a USB reset); restarting it"),
    ("DeviceLocked", "Unlock the phone; the link retries by itself"),
    ("expired", "The input driver's 7-day signature expired: re-sign WebDriverAgent in Sideloadly"),
)


def runner_hint(error: str) -> str:
    lowered = error.lower()
    for needle, hint in RUNNER_HINTS:
        if needle.lower() in lowered:
            return hint
    return ""


@dataclass(frozen=True)
class Policy:
    # iOS kills the WDA runner ~39s after a video surface wedges it (10:47:40 ->
    # 10:48:18 and 11:24:32 -> 11:25:11 on 2026-09-30, tunnel healthy both times).
    # The Home press has to land well inside that, so the tick is short, WDA is
    # probed first, and two silent probes (~10s) are enough. The old 45s gate
    # (SideTap's viewer) was always too late.
    tick: float = 5.0
    # False when somebody else owns the tunnel daemon (an admin-terminal kernel
    # `ios tunnel start`, or pymobiledevice3's tunneld): the supervisor then reads
    # it, adopts it, and never starts, refreshes or kills it. A dead external
    # tunnel is reported, not repaired (docs/link-root-cause.md, experiment A/B).
    manage_tunnel: bool = True
    wda_timeout: float = 4.0
    route_failures: int = 3  # consecutive dead route probes before the route counts as dead
    # A dead route with the phone still on the bus has followed a heavy transcode
    # (Edits 4K export 10:47, TikTok editor 11:25 on 2026-09-30) every time; the
    # phone-side endpoint may be starved rather than gone, and nobody has yet let
    # it recover on its own. Leave the daemon alone this long before the one restart.
    route_dead_grace: float = 300.0
    wedged_ticks: int = 2  # consecutive silent probes before pressing Home (~10s)
    busy_multiplier: int = 3  # route/entry thresholds are this much longer while busy (never the wedge one)
    entry_grace: float = 60.0  # seconds a live daemon gets to list a tunnel
    entry_ticks: int = 3  # ticks past the grace without an entry before a restart
    wda_start_grace: float = 90.0  # seconds a fresh runner gets before it counts as dead
    release_interval: float = 45.0  # seconds between Home presses for one wedge (WDA answers ~20s after one)
    refresh_interval: float = 20.0  # seconds between per-device tunnel refreshes (go-ios DELETE /tunnel/{udid})
    refreshes_before_restart: int = 3  # refreshes that failed to bring the listener back before a daemon restart
    releases_before_restart: int = 2  # Home presses that failed to clear a wedge before restarting the runner
    tunnel_backoff: tuple[float, ...] = (20.0, 60.0, 180.0, 300.0)  # a daemon needs ~12s to negotiate
    runner_backoff: tuple[float, ...] = (3.0, 10.0, 30.0, 60.0)


@dataclass
class Decision:
    state: str
    message: str
    actions: list[str] = field(default_factory=list)
    detail: str = ""


READY = "ready"
UNPLUGGED = "unplugged"
STARTING = "starting"
WAITING_TUNNEL = "waiting-tunnel"
RECOVERING_TUNNEL = "recovering-tunnel"
RECOVERING_RUNNER = "recovering-runner"
WEDGED = "wedged"
NEEDS_REPLUG = "needs-replug"
EXTERNAL_TUNNEL_DOWN = "external-tunnel-down"

TUNNEL_MODES = ("userspace", "kernel", "pmd3")

MESSAGES = {
    EXTERNAL_TUNNEL_DOWN: "The externally started tunnel is not answering: restart it in its admin terminal "
                          "(or replug the phone). The supervisor never touches an adopted tunnel",
    READY: "Phone link ready",
    UNPLUGGED: "Phone not detected over USB. Check the cable; the link resumes by itself when it returns",
    STARTING: "Phone link starting",
    WAITING_TUNNEL: "Phone link paused: waiting for the phone to answer over USB. Recovering…",
    RECOVERING_TUNNEL: "Phone link paused: restarting the USB tunnel. Recovering…",
    RECOVERING_RUNNER: "Phone link paused: restarting the input driver on the phone. Recovering…",
    WEDGED: "Phone link paused: an app with playing video is holding the driver. Returning to the Home Screen…",
    NEEDS_REPLUG: "Unplug and replug the phone: its USB link stopped answering and only a replug restores it",
}


class Decider:
    """Turns observations into actions. Holds only counters; never touches the phone."""

    def __init__(self, policy: Policy | None = None, clock=time.monotonic):
        self.policy = policy or Policy()
        self.clock = clock
        self.state = STARTING
        self.route_failures = 0
        self.route_dead_since: float | None = None
        self.last_route_ok: bool | None = None
        self.tunnel_refreshes = 0
        self.next_refresh = 0.0
        self.stale_ticks = 0
        self.entry_misses = 0
        self.wedged_ticks = 0
        self.releases = 0
        self.last_release_at: float | None = None
        self.started_at = clock()
        self.tunnel_starts = 0  # drives the backoff; reset when the route answers
        self.outage_restarts = 0  # daemon restarts for the current dead route; one, then "replug"
        self.tunnel_started_at: float | None = None
        self.next_tunnel_start = 0.0
        self.runner_restarts = 0
        self.runner_started_at: float | None = None
        self.next_runner_start = 0.0
        self.device_returned_at: float | None = None
        self.device_seen = True

    # -- bookkeeping the runner reports back ---------------------------------

    def tunnel_started(self) -> None:
        now = self.clock()
        self.tunnel_started_at = now
        self.entry_misses = 0
        self.route_failures = 0
        backoff = self.policy.tunnel_backoff[min(self.tunnel_starts, len(self.policy.tunnel_backoff) - 1)]
        self.tunnel_starts += 1
        self.next_tunnel_start = now + backoff

    def route_failed(self) -> None:
        """A runner start found the route dead: hold further starts until a probe answers."""
        self.last_route_ok = False
        if self.route_dead_since is None:
            self.route_dead_since = self.clock()

    def runner_started(self) -> None:
        now = self.clock()
        self.runner_started_at = now
        self.wedged_ticks = 0
        self.releases = 0
        backoff = self.policy.runner_backoff[min(self.runner_restarts, len(self.policy.runner_backoff) - 1)]
        self.runner_restarts += 1
        self.next_runner_start = now + backoff

    def released(self) -> None:
        self.last_release_at = self.clock()
        self.releases += 1
        self.wedged_ticks = 0

    # -- the decision ----------------------------------------------------------

    def _scale(self, threshold: int, busy: bool) -> int:
        return threshold * self.policy.busy_multiplier if busy else threshold

    def step(self, obs: Observation) -> Decision:
        now = self.clock()
        policy = self.policy

        if obs.device_present is False:
            if self.device_seen:
                self.device_seen = False
            self.route_failures = self.entry_misses = self.wedged_ticks = 0
            self.outage_restarts = self.tunnel_starts = 0
            self.state = UNPLUGGED
            return Decision(UNPLUGGED, MESSAGES[UNPLUGGED])
        if not self.device_seen:
            # The phone just came back. go-ios's daemon re-creates the tunnel on
            # its own; give it the full grace before judging the entry.
            self.device_seen = True
            self.device_returned_at = now
            self.entry_misses = 0
            self.route_failures = 0
            self.outage_restarts = self.tunnel_starts = 0
            self.runner_restarts = 0
            self.state = WAITING_TUNNEL
            # `ios forward` is bound to the USB connection the phone just dropped; a forward from
            # before the drop accepts locally and never reaches the phone (2026-09-30 11:50-12:05).
            return Decision(WAITING_TUNNEL, MESSAGES[WAITING_TUNNEL], ["start_forwards"],
                            detail="phone returned to USB")

        if not obs.tunnel_alive:
            if not policy.manage_tunnel:
                self.state = EXTERNAL_TUNNEL_DOWN
                return Decision(EXTERNAL_TUNNEL_DOWN, MESSAGES[EXTERNAL_TUNNEL_DOWN],
                                detail="external tunnel daemon not running")
            if now >= self.next_tunnel_start:
                self.state = RECOVERING_TUNNEL
                return Decision(RECOVERING_TUNNEL, MESSAGES[RECOVERING_TUNNEL], ["start_tunnel"],
                                detail="tunnel daemon not running")
            self.state = RECOVERING_TUNNEL
            return Decision(RECOVERING_TUNNEL, MESSAGES[RECOVERING_TUNNEL], detail="waiting out tunnel backoff")

        if not obs.tunnel_entry:
            since = max(filter(None, (self.tunnel_started_at, self.device_returned_at)), default=self.started_at)
            if now - since < policy.entry_grace:
                self.state = WAITING_TUNNEL
                return Decision(WAITING_TUNNEL, MESSAGES[WAITING_TUNNEL], detail="daemon still negotiating")
            self.entry_misses += 1
            if self.entry_misses >= self._scale(policy.entry_ticks, obs.busy):
                return self._tunnel_dead(now, "daemon never listed a tunnel")
            self.state = WAITING_TUNNEL
            return Decision(WAITING_TUNNEL, MESSAGES[WAITING_TUNNEL],
                            detail=f"no tunnel entry ({self.entry_misses})")
        self.entry_misses = 0

        # A working WDA means the runner's testmanagerd link through this tunnel is alive: a refused
        # probe then is noise, and refreshing killed a working runner (2026-09-30 12:46:10).
        stale = obs.tunnel_listening is False and obs.wda != "up"
        self.stale_ticks = self.stale_ticks + 1 if stale else 0
        if stale and self.stale_ticks >= 2:
            # go-ios keeps a tunnel record after its userspace listener dies (seen after the
            # 12:37 replug on 2026-09-30: port 60107 listed, nothing listening). The daemon's own
            # per-device refresh rebuilt it in under 5s; killing the daemon never helped.
            if not policy.manage_tunnel:
                return self._tunnel_dead(now, "tunnel listed but its port refuses connections")
            if self.tunnel_refreshes >= policy.refreshes_before_restart:
                return self._tunnel_dead(now, "tunnel listener stayed dead after refreshes")
            if now >= self.next_refresh:
                self.tunnel_refreshes += 1
                self.next_refresh = now + policy.refresh_interval
                self.state = RECOVERING_TUNNEL
                return Decision(RECOVERING_TUNNEL, MESSAGES[RECOVERING_TUNNEL], ["refresh_tunnel"],
                                detail="tunnel listed but its port refuses connections")
            self.state = RECOVERING_TUNNEL
            return Decision(RECOVERING_TUNNEL, MESSAGES[RECOVERING_TUNNEL], detail="waiting for the refreshed tunnel")
        if obs.tunnel_listening is True or obs.wda == "up":
            self.tunnel_refreshes = 0

        if obs.route_ok is False:
            self.route_failures += 1
            self.last_route_ok = False  # holds runner starts; the daemon restart waits for the counters
            if self.route_dead_since is None:
                self.route_dead_since = now
            if self.route_failures >= self._scale(policy.route_failures, obs.busy):
                dead_for = now - self.route_dead_since
                if dead_for >= self._scale(policy.route_dead_grace, obs.busy):
                    return self._tunnel_dead(now, f"route dead for {self.route_failures} probes")
        elif obs.route_ok is True:
            self.route_failures = 0
            self.route_dead_since = None
            self.last_route_ok = True
            self.outage_restarts = self.tunnel_starts = 0
            if self.state in (NEEDS_REPLUG, EXTERNAL_TUNNEL_DOWN):
                self.state = STARTING

        if self.state in (NEEDS_REPLUG, EXTERNAL_TUNNEL_DOWN) and obs.wda != "up":
            return Decision(self.state, MESSAGES[self.state])

        # A forward that accepts the connection with no runner behind it looks
        # "wedged" (accepts, never answers) but is plainly down.
        wda = obs.wda if obs.runwda_alive else "down"

        if wda == "up":
            self.wedged_ticks = 0
            self.releases = 0
            self.runner_restarts = 0
            self.state = READY
            return Decision(READY, MESSAGES[READY])

        if wda == "wedged":
            if obs.activity_landed:
                # Someone else's action just went through: a busy queue, not a wedge.
                self.wedged_ticks = 0
                self.state = READY
                return Decision(READY, MESSAGES[READY], detail="busy, not wedged")
            self.wedged_ticks += 1
            # Busy does NOT stretch this one: the wedge happens inside a busy
            # window by construction, and the runner is lost 39s in either way.
            if self.wedged_ticks < policy.wedged_ticks:
                self.state = WEDGED
                return Decision(WEDGED, MESSAGES[WEDGED], detail=f"wedged ({self.wedged_ticks})")
            if self.releases >= policy.releases_before_restart and obs.runwda_alive:
                if now >= self.next_runner_start:
                    self.state = RECOVERING_RUNNER
                    return Decision(RECOVERING_RUNNER, MESSAGES[RECOVERING_RUNNER], ["restart_runwda"],
                                    detail=f"wedge survived {self.releases} Home presses")
                self.state = RECOVERING_RUNNER
                return Decision(RECOVERING_RUNNER, MESSAGES[RECOVERING_RUNNER], detail="waiting out runner backoff")
            if self.last_release_at is None or now - self.last_release_at >= policy.release_interval:
                self.state = WEDGED
                return Decision(WEDGED, MESSAGES[WEDGED], ["release_springboard"], detail="pressing Home")
            self.state = WEDGED
            return Decision(WEDGED, MESSAGES[WEDGED], detail="waiting for the Home press to release the driver")

        # WDA refused the connection: the runner or a forward is gone.
        actions: list[str] = []
        if not obs.forwards_alive:
            actions.append("start_forwards")
        if not obs.runwda_alive and self.last_route_ok is False:
            # The runner talks to testmanagerd through the tunnel; starting one
            # on a dead route just fails again and burns the backoff.
            self.state = WAITING_TUNNEL
            return Decision(WAITING_TUNNEL, MESSAGES[WAITING_TUNNEL], actions,
                            detail="runner not started: the phone's USB route is not answering")
        if not obs.runwda_alive:
            hint = runner_hint(obs.runner_error)
            message = f"{MESSAGES[RECOVERING_RUNNER]} {hint}".strip() if hint else MESSAGES[RECOVERING_RUNNER]
            if now >= self.next_runner_start:
                actions.append("start_runwda")
                self.state = RECOVERING_RUNNER
                return Decision(RECOVERING_RUNNER, message, actions, detail=obs.runner_error or "runner not running")
            self.state = RECOVERING_RUNNER
            return Decision(RECOVERING_RUNNER, message, actions, detail="waiting out runner backoff")
        started = self.runner_started_at
        if started is not None and now - started < policy.wda_start_grace:
            self.state = RECOVERING_RUNNER
            return Decision(RECOVERING_RUNNER, MESSAGES[RECOVERING_RUNNER], actions, detail="runner still starting")
        if started is None:
            # Adopted a runner we did not start; give it one grace period.
            self.runner_started_at = now
            self.state = RECOVERING_RUNNER
            return Decision(RECOVERING_RUNNER, MESSAGES[RECOVERING_RUNNER], actions, detail="adopted runner, waiting")
        if now >= self.next_runner_start:
            # A live runner that never answers is as often a stale forward as a stuck runner; both are cheap.
            if "start_forwards" not in actions:
                actions.append("start_forwards")
            actions.append("restart_runwda")
            self.state = RECOVERING_RUNNER
            return Decision(RECOVERING_RUNNER, MESSAGES[RECOVERING_RUNNER], actions,
                            detail="runner alive but the driver never answered")
        self.state = RECOVERING_RUNNER
        return Decision(RECOVERING_RUNNER, MESSAGES[RECOVERING_RUNNER], actions, detail="waiting out runner backoff")

    def _tunnel_dead(self, now: float, why: str) -> Decision:
        if not self.policy.manage_tunnel:
            # Adopted, never owned: say what died and leave the daemon to whoever started it.
            self.state = EXTERNAL_TUNNEL_DOWN
            return Decision(EXTERNAL_TUNNEL_DOWN, MESSAGES[EXTERNAL_TUNNEL_DOWN], detail=why)
        if self.outage_restarts >= 1:
            # One fresh daemon already failed to reach the phone while it sat
            # on the bus: killing more of them never helped, a replug does.
            self.state = NEEDS_REPLUG
            return Decision(NEEDS_REPLUG, MESSAGES[NEEDS_REPLUG], detail=why)
        if now < self.next_tunnel_start:
            self.state = RECOVERING_TUNNEL
            return Decision(RECOVERING_TUNNEL, MESSAGES[RECOVERING_TUNNEL], detail="waiting out tunnel backoff")
        self.outage_restarts += 1
        self.route_failures = 0
        self.state = RECOVERING_TUNNEL
        return Decision(RECOVERING_TUNNEL, MESSAGES[RECOVERING_TUNNEL], ["restart_tunnel"], detail=why)


# ---- status, events, busy windows ---------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_status(state: Path | None = None) -> dict:
    path = (state or state_dir()) / STATUS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "unknown", "message": "Phone link supervisor has not reported yet", "alive": False}
    data["alive"] = supervisor_alive(state)
    return data


def write_status(state: Path, decision: Decision, *, since: str, extra: dict | None = None) -> None:
    payload = {"state": decision.state, "message": decision.message, "detail": decision.detail,
               "since": since, "updatedAt": _now_iso(), **(extra or {})}
    tmp = state / (STATUS_FILE + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, state / STATUS_FILE)


def log_event(state: Path, event: dict) -> None:
    with (state / EVENTS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": _now_iso(), **event}) + "\n")


def read_busy(state: Path, now: float | None = None) -> str | None:
    """The reason a script declared the phone busy, or None once its window passed."""
    try:
        data = json.loads((state / BUSY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if float(data.get("until", 0)) < (now if now is not None else time.time()):
        return None
    return str(data.get("reason") or "busy")


def declare_busy(state: Path, reason: str, seconds: float) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / BUSY_FILE).write_text(json.dumps({"reason": reason, "until": time.time() + seconds}),
                                   encoding="utf-8")


def clear_busy(state: Path) -> None:
    try:
        (state / BUSY_FILE).unlink()
    except OSError:
        pass


def request_recovery(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / REQUEST_FILE).write_text("recover", encoding="utf-8")


# ---- process plumbing ----------------------------------------------------------


def _pid_alive(pid: int, image_prefix: str = "") -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        proc = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True,
                              text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        first = proc.stdout.strip().splitlines()
        if not first or not first[0].startswith('"'):
            return False
        image = first[0].split('","')[0].strip('"').lower()
        return image.startswith(image_prefix.lower()) if image_prefix else True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def supervisor_alive(state: Path | None = None) -> bool:
    path = (state or state_dir()) / PID_FILE
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return _pid_alive(pid, "python")


def detached_flags() -> int:
    if sys.platform != "win32":
        return 0
    return subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def spawn_detached(command: list[str], log: Path, *, cwd: Path | None = None, env: dict | None = None) -> int:
    """Start a process that outlives whoever started it (and any Job the caller sits in, when allowed)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "a", encoding="utf-8")
    flags = detached_flags()
    tries = [flags | 0x01000000, flags] if sys.platform == "win32" else [0]  # CREATE_BREAKAWAY_FROM_JOB first
    last: OSError | None = None
    for creationflags in tries:
        try:
            proc = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    creationflags=creationflags, cwd=cwd, env=env, close_fds=True)
            return proc.pid
        except OSError as exc:  # breakaway refused by the Job: fall back to plain detached
            last = exc
    raise last if last else RuntimeError("could not start the process")


def ensure_running(state: Path | None = None, *, python: str | None = None) -> dict:
    """Start the supervisor if it is not already running. Safe to call from every client."""
    state = state or state_dir()
    state.mkdir(parents=True, exist_ok=True)
    if supervisor_alive(state):
        return {"started": False, "alive": True}
    env = os.environ.copy()
    env["VIDEO_DROP_STATE"] = str(state)
    env["PYTHONIOENCODING"] = "utf-8"
    exe = python or sys.executable
    if sys.platform == "win32" and exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    pid = spawn_detached([exe, "-m", "video_drop.link_supervisor"], state / "link-supervisor.log", cwd=ROOT, env=env)
    return {"started": True, "alive": True, "pid": pid}


def stop_supervisor(state: Path | None = None) -> bool:
    """End the supervisor process only. Its go-ios processes keep running (their pid files
    stay), so the next supervisor adopts them; nothing on the phone side changes."""
    state = state or state_dir()
    path = state / PID_FILE
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if not _pid_alive(pid, "python"):
        path.unlink(missing_ok=True)
        return False
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.kill(pid, 15)
    for _ in range(20):
        if not _pid_alive(pid, "python"):
            break
        time.sleep(0.25)
    path.unlink(missing_ok=True)
    return True


# ---- the runner ----------------------------------------------------------------


class Runner:
    """Probes go-ios/WDA, feeds the Decider, applies its actions, keeps the files current."""

    def __init__(self, state: Path, policy: Policy | None = None, *, observe_only: bool = False,
                 tunnel_mode: str | None = None, mjpeg_forward: bool | None = None):
        self.state = state
        from .phone import config, device

        # Experiment knobs (docs/link-root-cause.md). LINK_TUNNEL_MODE: "userspace" (the
        # supervisor owns a go-ios userspace daemon, the default), "kernel" (adopt an
        # admin-started `ios tunnel start` and its wintun tunnel, never start or kill it) or
        # "pmd3" (read pymobiledevice3's tunneld on :49151 and hand its address/rsd port to
        # every go-ios command that needs RSD). LINK_SKIP_MJPEG_FORWARD=1 leaves :9100 alone.
        self.tunnel_mode = (tunnel_mode or config.get("LINK_TUNNEL_MODE", "userspace") or "userspace").lower()
        if self.tunnel_mode not in TUNNEL_MODES:
            raise ValueError(f"LINK_TUNNEL_MODE must be one of {TUNNEL_MODES}, not {self.tunnel_mode!r}")
        if mjpeg_forward is None:
            mjpeg_forward = (config.get("LINK_SKIP_MJPEG_FORWARD", "0") or "0").lower() not in ("1", "true", "yes")
        self.mjpeg_forward = mjpeg_forward
        self.forward_names = ("forward8100", "forward9100") if mjpeg_forward else ("forward8100",)
        self.policy = replace(policy or Policy(), manage_tunnel=self.tunnel_mode == "userspace")
        self.decider = Decider(self.policy)
        self.mode_mismatch = ""
        # Observe-only: probe, decide and log "would ..." but never touch a process or the
        # phone, and never claim to be the live supervisor (own pid/status/event files).
        self.observe_only = observe_only
        self.status_file = "link-observe.json" if observe_only else STATUS_FILE
        self.events_file = "link-observe.jsonl" if observe_only else EVENTS_FILE
        self.pid_file = "link-observer.pid" if observe_only else PID_FILE
        self.since = _now_iso()
        self.last_state = ""
        self.last_route_probe = 0.0
        self.last_activity_mtime = 0.0
        self.viewer_warned = False

        # One owner: the driver's files (pids, logs, wda_session, the activity
        # feed) sit under THIS supervisor's state folder, whatever the process
        # environment said when the module loaded.
        config.STATE_DIR = state / "phone"
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.config = config
        self.ios = device.ios_path()
        (state / LOG_DIR).mkdir(parents=True, exist_ok=True)

    # -- probes --

    def _ios(self, args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
        if not self.ios:
            return None
        try:
            return subprocess.run([self.ios, *self.device.pin_udid(args)], capture_output=True, text=True,
                                  timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def device_present(self) -> bool | None:
        proc = self._ios(["list"], timeout=10)
        if proc is None:
            return None
        for obj in self.device._json_lines(proc.stdout + proc.stderr):
            if "deviceList" in obj:
                return bool(obj["deviceList"])
        return None

    def tunnel_entry(self) -> dict | None:
        if self.tunnel_mode == "pmd3":
            return self._pmd3_entry()
        # The daemon's API answers in ~0.4s; when it stalls (a jammed userspace tunnel) this
        # must not hold the loop that has ~39s to release a wedge.
        proc = self._ios(["tunnel", "ls"], timeout=4)
        if proc is None:
            return None
        for obj in self.device._json_lines(proc.stdout + proc.stderr):
            if obj.get("level"):
                continue
            if obj.get("address") and obj.get("rsdPort"):
                userspace = bool(obj.get("userspaceTun"))
                if userspace != (self.tunnel_mode == "userspace"):
                    # The listed tunnel is not the kind this mode was told to expect: adopting it
                    # would make an A/B run measure the wrong thing, so it counts as no entry.
                    self._note_mismatch(f"tunnel mode {self.tunnel_mode!r} but the listed tunnel is "
                                        f"{'userspace' if userspace else 'kernel'} (udid {obj.get('udid')})")
                    return None
                self.mode_mismatch = ""
                return obj
        return None

    def _pmd3_entry(self) -> dict | None:
        from .link_probe import pmd3_entries

        status, _ms, entries = pmd3_entries()
        if status != "ok":
            return None
        udid = self.config.SIDETAP_UDID
        for entry in entries:
            if udid and entry.get("udid") != udid:
                continue
            # go-ios reaches this tunnel with --address/--rsd-port on every RSD command
            # (device.pin_udid adds them); the address is read at call time.
            self.config.GO_IOS_RSD_ADDRESS = entry["address"]
            self.config.GO_IOS_RSD_PORT = str(entry["rsdPort"])
            return entry
        return None

    def _note_mismatch(self, text: str) -> None:
        if text != self.mode_mismatch:
            self.mode_mismatch = text
            self._event({"event": "warning", "detail": text})

    def pmd3_alive(self) -> bool:
        from .link_probe import pmd3_entries

        return pmd3_entries()[0] == "ok"

    @staticmethod
    def port_listening(port) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                return True
        except (OSError, ValueError):
            return False

    @staticmethod
    def tunnel_listening(entry: dict) -> bool:
        """A TCP connect to the tunnel: the local userspace port for a go-ios userspace
        tunnel, the phone's RSD port through the TUN adapter for a kernel/pmd3 one."""
        if entry.get("userspaceTunPort"):
            return Runner.port_listening(entry["userspaceTunPort"])
        try:
            with socket.create_connection((entry["address"], int(entry["rsdPort"])), timeout=0.5):
                return True
        except (OSError, ValueError, KeyError):
            return False

    def refresh_tunnel(self) -> str:
        """go-ios's per-device refresh: the daemon drops this phone's tunnel and builds a new one."""
        if not self.policy.manage_tunnel:
            return f"tunnel is external ({self.tunnel_mode}); not refreshed"
        entry = getattr(self, "last_entry", None) or {}
        udid = entry.get("udid")
        if not udid:
            return "no tunnel record to refresh"
        request = urllib.request.Request(f"http://127.0.0.1:{TUNNEL_API_PORT}/tunnel/{udid}", method="DELETE")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return f"refreshed tunnel ({response.status})"
        except OSError as exc:
            return f"tunnel refresh failed: {exc}"

    def route_ok(self, timeout: float = 15) -> bool:
        proc = self._ios(["image", "list"], timeout=timeout)
        return proc is not None and proc.returncode == 0

    def viewer_running(self) -> bool:
        """SideTap's viewer runs its own healer (admin.up() after 45s of silence). Two
        healers on one link replace healthy tunnels with dead ones (11:28:13 on
        2026-09-30), so the supervisor stands by while the viewer is alive. Its pid
        file lives in SideTap's own state folder, so its listening port is the probe."""
        try:
            with socket.create_connection(("127.0.0.1", SIDETAP_VIEWER_PORT), timeout=0.3):
                return True
        except OSError:
            return False

    def wda_state(self, timeout: float = 4.0) -> str:
        try:
            with urllib.request.urlopen(WDA_STATUS, timeout=timeout) as response:
                json.load(response)
                return "up"
        except TimeoutError:
            return "wedged"
        except OSError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError) or "timed out" in str(exc).lower():
                return "wedged"
            return "down"
        except ValueError:
            return "wedged"

    def proc_alive(self, name: str) -> bool:
        return self.device.proc_status(name) == "running"

    def runner_error(self) -> str:
        for line in reversed(self.device.log_tail("runwda", 30).splitlines()):
            if '"Failed running WDA"' in line:
                try:
                    return str(json.loads(line).get("error", ""))[:300]
                except ValueError:
                    return line[-300:]
        return ""

    def activity_landed(self) -> bool:
        try:
            mtime = (self.config.STATE_DIR / "agent_activity.log").stat().st_mtime
        except OSError:
            return False
        landed = mtime > self.last_activity_mtime and time.time() - mtime < self.policy.tick * 2
        self.last_activity_mtime = max(self.last_activity_mtime, mtime)
        return landed

    def observe(self) -> Observation:
        busy = read_busy(self.state) is not None
        # WDA first: a wedge has ~39s before iOS kills the runner, and nothing
        # slow may run ahead of the probe that detects it.
        wda = self.wda_state(self.policy.wda_timeout)
        runwda_alive = self.proc_alive("runwda")
        if self.tunnel_mode == "pmd3":
            tunnel_alive = self.pmd3_alive()
        else:
            tunnel_alive = self.proc_alive("tunnel")
            # A daemon someone else started is adopted in every mode (pid file only). In
            # kernel mode that is the whole point; it is never started or killed from here.
            if not tunnel_alive and not self.observe_only and self._adopt_unmanaged_tunnel():
                tunnel_alive = True
        present = self.device_present()
        if present is False:
            wda = "down"
        entry = self.tunnel_entry() if (present is not False and tunnel_alive) else None
        # Every connect to the userspace port is a half-open tunnel client go-ios logs as a
        # failed preamble; probing it every tick coincided with the link cycling every ~40s
        # (2026-09-30 12:45-12:58). Only probe when WDA already says something is wrong.
        listening = self.tunnel_listening(entry) if entry and wda != "up" else None
        self.last_entry = entry
        route: bool | None = None
        # The route probe (`ios image list`) can hang 15s on a starved phone, so it
        # never runs while a wedge is being judged, and only every 60s when ready.
        if entry and present is not False and wda != "wedged":
            interval = 60.0 if wda == "up" else 15.0
            if busy:
                interval *= self.policy.busy_multiplier
            if time.monotonic() - self.last_route_probe >= interval:
                route = self.route_ok()
                self.last_route_probe = time.monotonic()
        return Observation(device_present=present, tunnel_alive=tunnel_alive, tunnel_entry=entry is not None,
                           tunnel_listening=listening,
                           route_ok=route, runwda_alive=runwda_alive,
                           forwards_alive=all(self.proc_alive(name) for name in self.forward_names),
                           wda=wda, activity_landed=self.activity_landed(), busy=busy,
                           runner_error="" if runwda_alive else self.runner_error())

    # -- actions --

    def _rotate_log(self, name: str) -> None:
        src = self.device._log_file(name)
        if not src.is_file() or src.stat().st_size == 0:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copyfile(src, self.state / LOG_DIR / f"{name}-{stamp}.log")
        kept = sorted((self.state / LOG_DIR).glob(f"{name}-*.log"))
        for old in kept[:-20]:
            old.unlink(missing_ok=True)
        try:
            src.write_text("", encoding="utf-8")  # the copy holds the history; the live log starts clean
        except OSError:
            pass

    def _spawn(self, name: str, args: list[str]) -> int:
        self._rotate_log(name)
        pid = spawn_detached([self.ios, *self.device.pin_udid(args)], self.device._log_file(name))
        self.device._pid_file(name).write_text(str(pid), encoding="utf-8")
        return pid

    def _stop(self, names: tuple[str, ...]) -> list[str]:
        for name in names:
            self._rotate_log(name)
        return self.device.stop_all(names)

    def _other_ios(self, name: str, pattern: str) -> list[int]:
        """PIDs of go-ios processes matching `pattern` that are not the one in <name>.pid.

        A human or SideTap's viewer starts these too; a second daemon or runner
        beside theirs fights them, so the supervisor adopts what it finds.
        """
        if sys.platform != "win32":
            return []
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-Command",
                                   "Get-CimInstance Win32_Process -Filter \"Name='ios.exe'\" | "
                                   f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
                                   "ForEach-Object { $_.ProcessId }"],
                                  capture_output=True, text=True, timeout=20,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return []
        known = self.device.proc_status(name) == "running"
        try:
            own = int(self.device._pid_file(name).read_text().strip()) if known else -1
        except (OSError, ValueError):
            own = -1
        return [int(line) for line in proc.stdout.split() if line.strip().isdigit() and int(line) != own]

    def _other_runners(self) -> list[int]:
        return self._other_ios("runwda", " runwda")

    def _adopt_unmanaged_tunnel(self) -> bool:
        """Write tunnel.pid for a daemon someone started by hand; True if adopted."""
        if self.device.proc_status("tunnel") == "running":
            return False
        others = self._other_ios("tunnel", " tunnel start")
        if not others:
            return False
        self.device._pid_file("tunnel").write_text(str(others[0]), encoding="utf-8")
        self._event({"event": "adopted an unmanaged tunnel daemon", "pid": others[0]})
        return True

    def start_runwda(self) -> str:
        others = self._other_runners()
        if others:
            # SideTap's viewer (or a human) already started one; adopt it instead of
            # racing it with a second runner, which fails with XCTest error 103.
            self.device._pid_file("runwda").write_text(str(others[0]), encoding="utf-8")
            self.decider.runner_started()
            return f"adopted runner pid {others[0]}"
        # Everything below (bundle lookup, image mount, the runner itself) goes through the
        # phone's route. On a dead route `ios image auto` blocked this loop 180s (2026-09-30
        # 12:20-12:24), so a short probe decides first and a dead route starts nothing.
        if not self.route_ok(timeout=6):
            self.decider.route_failed()
            return "phone route not answering; runner not started"
        bundle = self.device.detect_wda_bundle()
        if not bundle:
            return "no WebDriverAgent bundle found on the phone"
        if not self.device.ddi_mounted():
            ok, msg = self.device.mount_ddi()
            if not ok:
                return f"developer image: {msg}"
        pid = self._spawn("runwda", ["runwda", f"--bundleid={bundle}", f"--testrunnerbundleid={bundle}",
                                     "--xctestconfig=WebDriverAgentRunner.xctest"])
        self.decider.runner_started()
        return f"started runner pid {pid}"

    def start_forwards(self) -> str:
        self._stop(("forward8100", "forward9100"))
        self.device._free_port(self.config.WDA_PORT)
        pids = [self._spawn("forward8100", ["forward", str(self.config.WDA_PORT), "8100"])]
        if self.mjpeg_forward:
            self.device._free_port(self.config.MJPEG_PORT)
            pids.append(self._spawn("forward9100", ["forward", str(self.config.MJPEG_PORT), "9100"]))
        return "forwards " + "/".join(str(p) for p in pids) + ("" if self.mjpeg_forward else " (no MJPEG forward)")

    def start_tunnel(self) -> str:
        if not self.policy.manage_tunnel:
            return f"tunnel is external ({self.tunnel_mode}); not started"
        pid = self._spawn("tunnel", ["tunnel", "start", "--userspace"])
        self.decider.tunnel_started()
        return f"started tunnel daemon pid {pid}"

    def restart_tunnel(self) -> str:
        if not self.policy.manage_tunnel:
            return f"tunnel is external ({self.tunnel_mode}); not restarted"
        stopped = self._stop(("runwda", "forward8100", "forward9100", "tunnel"))
        return f"stopped {stopped}; " + self.start_tunnel()

    def restart_runwda(self) -> str:
        stopped = self._stop(("runwda",))
        return f"stopped {stopped}; " + self.start_runwda()

    def release_springboard(self) -> str:
        ok = self.device.foreground_springboard()
        self.decider.released()
        return "pressed Home" if ok else "Home press failed"

    def apply(self, action: str) -> str:
        return {
            "start_tunnel": self.start_tunnel, "restart_tunnel": self.restart_tunnel,
            "refresh_tunnel": self.refresh_tunnel,
            "start_runwda": self.start_runwda, "restart_runwda": self.restart_runwda,
            "start_forwards": self.start_forwards, "release_springboard": self.release_springboard,
        }[action]()

    def _viewer_check(self) -> None:
        if self.viewer_warned:
            return
        self._event({"event": "warning", "detail": "SideTap's viewer is running its own link healer; the supervisor "
                                                   "observes but takes no action until the viewer is closed."})
        self.viewer_warned = True

    # -- loop --

    def tick(self) -> Decision:
        requested = (self.state / REQUEST_FILE).is_file()
        if requested:
            (self.state / REQUEST_FILE).unlink(missing_ok=True)
            self.last_route_probe = 0.0
        obs = self.observe()
        decision = self.decider.step(obs)
        results = []
        standby = not self.observe_only and self.viewer_running()
        if standby and decision.actions:
            decision = replace(decision, message="Phone link supervisor is standing by: SideTap's viewer is running "
                                                 "its own link healer. Close the viewer to let the app own the link.",
                               detail=f"viewer running; not doing {decision.actions}")
        for action in decision.actions:
            if self.observe_only or standby:
                results.append(f"would {action}")
                # Keep the counters honest as if the action had run, so the log
                # shows the real cadence (one restart, then replug, etc.).
                {"start_tunnel": self.decider.tunnel_started, "restart_tunnel": self.decider.tunnel_started,
                 "start_runwda": self.decider.runner_started, "restart_runwda": self.decider.runner_started,
                 "release_springboard": self.decider.released}.get(action, lambda: None)()
                continue
            try:
                results.append(f"{action}: {self.apply(action)}")
            except Exception as exc:  # the loop must survive a failed action
                results.append(f"{action}: failed {exc}")
        if decision.state != self.last_state or results or requested:
            if decision.state != self.last_state:
                self.since = _now_iso()
            self._event({"state": decision.state, "message": decision.message, "detail": decision.detail,
                         "actions": results, "requested": requested, "observation": obs.__dict__})
            self.last_state = decision.state
        payload = {"state": decision.state, "message": decision.message, "detail": decision.detail,
                   "since": self.since, "updatedAt": _now_iso(), "busy": read_busy(self.state),
                   "pids": {n: self.device.proc_status(n) for n in ("tunnel", "runwda", *self.forward_names)},
                   "supervisorPid": os.getpid(), "observeOnly": self.observe_only, "standby": standby,
                   "tunnelMode": self.tunnel_mode, "mjpegForward": self.mjpeg_forward,
                   "modeMismatch": self.mode_mismatch}
        tmp = self.state / (self.status_file + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.state / self.status_file)
        if standby:
            self._viewer_check()
        return decision

    def _event(self, event: dict) -> None:
        with (self.state / self.events_file).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": _now_iso(), **event}) + "\n")

    def run(self) -> None:
        (self.state / self.pid_file).write_text(str(os.getpid()), encoding="utf-8")
        self._event({"event": "observer started" if self.observe_only else "supervisor started",
                     "pid": os.getpid(), "ios": self.ios})
        while True:
            try:
                self.tick()
            except Exception as exc:
                self._event({"event": "tick failed", "detail": repr(exc)})
            waited = 0.0
            while waited < self.policy.tick:
                if not self.observe_only and (self.state / REQUEST_FILE).is_file():
                    break
                time.sleep(1.0)
                waited += 1.0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Own the phone link: tunnel, WDA runner, port forwards.")
    parser.add_argument("--observe", action="store_true",
                        help="probe and log decisions only; never start, stop or touch anything")
    parser.add_argument("--tunnel-mode", choices=TUNNEL_MODES, default=None,
                        help="userspace (own a go-ios userspace daemon, default), kernel (adopt an admin-started "
                             "`ios tunnel start`, never start/kill it) or pmd3 (pymobiledevice3 tunneld on :49151). "
                             "Overrides LINK_TUNNEL_MODE")
    parser.add_argument("--no-mjpeg-forward", action="store_true",
                        help="do not forward :9100 (the MJPEG stream); overrides LINK_SKIP_MJPEG_FORWARD")
    args = parser.parse_args(argv)
    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)
    if not args.observe and supervisor_alive(state) and int((state / PID_FILE).read_text().strip()) != os.getpid():
        print("link supervisor already running", flush=True)
        return 0
    Runner(state, observe_only=args.observe, tunnel_mode=args.tunnel_mode,
           mjpeg_forward=False if args.no_mjpeg_forward else None).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
