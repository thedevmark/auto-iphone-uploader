"""Run one named phone-link experiment and print a verdict (docs/link-root-cause.md).

    python scripts/link_experiment.py baseline --minutes 20
    python scripts/link_experiment.py kernel   --restart-supervisor      # after `ios tunnel start` in an ADMIN terminal
    python scripts/link_experiment.py pmd3     --restart-supervisor      # after `pymobiledevice3 remote tunneld` (admin)
    python scripts/link_experiment.py no-mjpeg --restart-supervisor
    python scripts/link_experiment.py wda-lean
    python scripts/link_experiment.py idle --minutes 60 --ladder         # record only; walk the recovery ladder on a stall
    python scripts/link_experiment.py pixels --minutes 8                 # video on screen, go-ios pixels only (no WDA)
    python scripts/link_experiment.py ax --minutes 8                     # video on screen, WDA /source (AX snapshot)
    python scripts/link_experiment.py ax-shallow --minutes 8             # ax with snapshotMaxDepth=12
    python scripts/link_experiment.py wda-nosnap --minutes 8             # video on screen, WDA /status then /screenshot
    python scripts/link_experiment.py wda-strip --minutes 8              # ax with no MJPEG settings, 0.5s AX deadline

Every experiment runs the same layered 1 Hz recorder (video_drop/link_probe.py:
usbmux ListDevices, lockdown QueryType, WDA /status straight through usbmux,
one RSD round trip through the tunnel every few seconds) beside a read-only
phone workload, then groups the failures into incidents and says which hop
died first. ``--ladder`` turns a pipe stall into a measurement: it prints the
three recovery steps (AMDS service restart, pnputil device restart, physical
replug) one at a time and records which one brought lockdown back.

The script never starts or kills a go-ios process, a tunnel or WDA. When an
experiment needs the supervisor in another mode it either restarts the
*supervisor* for you (``--restart-supervisor``: the Python process only; its
go-ios children survive and are adopted) or prints the exact command and stops.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_drop import link_probe, link_supervisor  # noqa: E402

STATE = link_supervisor.state_dir()
SIDETAP_STATE = (Path.home() / "AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/SideTap/.state")

WORKLOADS = ("none", "safe", "tiktok", "control-center", "youtube-create",
             # Trigger isolation: the same video surfaces with a different
             # host-side reader on each, so what the host does can be separated from what the
             # phone shows. See docs/link-root-cause.md section 6.
             "tiktok-pixels",        # go-ios only: `ios launch` + `ios screenshot` (tunnel); no WDA request at all
             "tiktok-status",        # WDA HTTP only: GET /status polls (standalone route, no accessibility)
             "tiktok-shot",          # WDA GET /screenshot (testmanagerd capture), no accessibility snapshot
             "youtube-feed",         # a second video app (YouTube's home feed autoplays previews) + /source
             "youtube-feed-pixels",  # same screen, go-ios only
             "photos",               # Photos grid (GPU-heavy, no video) + /source: the 13:0x S1 shape
             )

VIDEO_APPS = {"tiktok": "com.zhiliaoapp.musically", "youtube": "com.google.ios.youtube",
              "photos": "com.apple.mobileslideshow"}

EXPERIMENTS: dict[str, dict] = {
    "baseline": {
        "tunnel_mode": "userspace", "mjpeg_forward": True, "env": {},
        "workload": "safe,tiktok,control-center,youtube-create",
        "why": "reference run: the userspace daemon the supervisor owns, MJPEG forward up, default WDA settings",
    },
    "kernel": {
        "tunnel_mode": "kernel", "mjpeg_forward": True, "env": {},
        "workload": "safe,tiktok,control-center,youtube-create",
        "why": "A/B (a): go-ios kernel tunnel over wintun, started by you in an ADMIN terminal and only adopted here",
        "operator": [
            "Stop the supervisor's userspace daemon first (it would compete for CoreDeviceProxy): "
            "  python -c \"from video_drop.phone import device; print(device.stop_all(('tunnel',)))\"",
            "In an ADMIN PowerShell:  & \"$env:APPDATA\\npm\\ios.exe\" tunnel start   (wintun.dll is already in System32)",
            "Wait for `ios tunnel ls` to show userspaceTun: false, then run this script with --restart-supervisor",
        ],
    },
    "pmd3": {
        "tunnel_mode": "pmd3", "mjpeg_forward": True, "env": {},
        "workload": "safe,tiktok,control-center,youtube-create",
        "why": "A/B (b): pymobiledevice3's tunnel (GPLv3, subprocess only); go-ios commands get --address/--rsd-port",
        "operator": [
            "Stop the go-ios daemon first (same command as the kernel experiment)",
            "In an ADMIN PowerShell:  python -m pymobiledevice3 remote tunneld --usb --no-wifi --protocol tcp",
            "Wait until http://127.0.0.1:49151/ lists the phone, then run this script with --restart-supervisor",
        ],
    },
    "no-mjpeg": {
        "tunnel_mode": "userspace", "mjpeg_forward": False, "env": {},
        "workload": "safe,tiktok,control-center,youtube-create",
        "why": "A/B (d): no :9100 forward, so no MJPEG client (SideTap's viewer) can stream screenshots over usbmux",
        "operator": ["Close SideTap's viewer (it opens :9100 itself and heals the link on its own)"],
    },
    "wda-lean": {
        "tunnel_mode": "userspace", "mjpeg_forward": True,
        "env": {"WDA_SNAPSHOT_MAX_DEPTH": "20", "WDA_ACCESSIBILITY_DEADLINE": "1.0", "WDA_IDLE_WAIT": "0",
                "MJPEG_FPS": "5", "MJPEG_SCALE": "25"},
        "workload": "safe,tiktok,control-center,youtube-create",
        "why": "A/B (c): shallow snapshots, short accessibility deadline, no idle wait, a slow small MJPEG stream",
    },
    "idle": {
        "tunnel_mode": None, "mjpeg_forward": None, "env": {}, "workload": "none",
        "why": "record only, whatever the supervisor is doing; use with --ladder while you reproduce a stall by hand",
    },
    # ---- trigger isolation (each arm <= 10 min; stop on the first stall) ----
    "pixels": {
        "tunnel_mode": "userspace", "mjpeg_forward": True, "env": {},
        "workload": "tiktok-pixels,youtube-feed-pixels",
        "why": "video plays, the host reads pixels through go-ios only (launch + screenshot over the tunnel): "
               "no WDA request of any kind while the runner sits idle",
    },
    "ax": {
        "tunnel_mode": "userspace", "mjpeg_forward": True, "env": {},
        "workload": "tiktok,youtube-feed,photos",
        "why": "video/GPU-heavy screens read with WDA /source (an XCTest accessibility snapshot): the 12:20 and "
               "17:30 reproduction shape, on TikTok, YouTube's feed and the Photos grid",
    },
    "ax-shallow": {
        # AX_VIDEO_APPS="" switches the driver's video-surface guard off for this arm: the point is
        # to let a depth-12 /source reach TikTok's feed and see whether it returns.
        "tunnel_mode": "userspace", "mjpeg_forward": True, "env": {"WDA_SNAPSHOT_MAX_DEPTH": "12", "AX_VIDEO_APPS": ""},
        "workload": "tiktok,youtube-feed",
        "why": "the ax arm with snapshotMaxDepth=12 (appium/appium#19255: TikTok's tree is huge; 10-15 while it is "
               "in front): does a shallow /source return at all on the feed?",
    },
    "wda-nosnap": {
        "tunnel_mode": "userspace", "mjpeg_forward": True, "env": {},
        "workload": "tiktok-status,tiktok-shot",
        "why": "video plays while the host talks to WDA without an accessibility snapshot: /status polls, then "
               "/screenshot (testmanagerd capture)",
    },
    "wda-strip": {
        "tunnel_mode": "userspace", "mjpeg_forward": True,
        "env": {"MJPEG_SETTINGS": "0", "WDA_ACCESSIBILITY_DEADLINE": "0.5", "WDA_IDLE_WAIT": "0"},
        "workload": "tiktok,youtube-feed",
        "why": "the ax arm with the session stripped: no MJPEG settings sent, a 0.5s accessibility deadline, "
               "no idle wait",
    },
}


def log(step: str, **fields) -> None:
    line = {"ts": datetime.now().isoformat(timespec="seconds"), "step": step, **fields}
    print(json.dumps(line), flush=True)


# ---- preconditions ----------------------------------------------------------------


def supervisor_command(exp: dict) -> str:
    parts = [sys.executable, "-m", "video_drop.link_supervisor"]
    if exp.get("tunnel_mode"):
        parts += ["--tunnel-mode", exp["tunnel_mode"]]
    if exp.get("mjpeg_forward") is False:
        parts.append("--no-mjpeg-forward")
    return " ".join(parts)


def supervisor_matches(status: dict, exp: dict) -> list[str]:
    """What the running supervisor would have to change for this experiment (empty = nothing)."""
    problems = []
    if not status.get("alive"):
        problems.append("the link supervisor is not running")
        return problems
    if exp.get("tunnel_mode") and status.get("tunnelMode", "userspace") != exp["tunnel_mode"]:
        problems.append(f"supervisor tunnel mode is {status.get('tunnelMode', 'userspace')!r}, "
                        f"experiment needs {exp['tunnel_mode']!r}")
    if exp.get("mjpeg_forward") is not None and bool(status.get("mjpegForward", True)) != exp["mjpeg_forward"]:
        problems.append(f"supervisor MJPEG forward is {status.get('mjpegForward', True)}, "
                        f"experiment needs {exp['mjpeg_forward']}")
    return problems


def tunnel_preconditions(exp: dict) -> list[str]:
    mode = exp.get("tunnel_mode")
    if mode == "kernel":
        status, _ms, entries = link_probe.tunnel_daemon_entries()
        if status != "ok":
            return ["no go-ios tunnel daemon answers on :60105 (start `ios tunnel start` in an ADMIN terminal)"]
        if not any(not e.get("userspaceTun") for e in entries):
            return ["the go-ios daemon lists no kernel tunnel (userspaceTun false); is it the admin one?"]
    if mode == "pmd3":
        status, _ms, entries = link_probe.pmd3_entries()
        if status != "ok":
            return ["pymobiledevice3 tunneld does not answer on :49151"]
        if not entries:
            return ["pymobiledevice3 tunneld lists no tunnel yet"]
    return []


def restart_supervisor(exp: dict) -> dict:
    link_supervisor.stop_supervisor(STATE)
    env = os.environ
    if exp.get("tunnel_mode"):
        env["LINK_TUNNEL_MODE"] = exp["tunnel_mode"]
    if exp.get("mjpeg_forward") is not None:
        env["LINK_SKIP_MJPEG_FORWARD"] = "0" if exp["mjpeg_forward"] else "1"
    started = link_supervisor.ensure_running(STATE)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = link_supervisor.read_status(STATE)
        if status.get("alive") and status.get("tunnelMode") is not None:
            return status
        time.sleep(1)
    return {**link_supervisor.read_status(STATE), "started": started}


# ---- the workload (read-only on the phone) -----------------------------------------


class Workload:
    """Read-only phone activity; never posts, deletes or sends.

    Two readers: the WDA driver (``phone``) and go-ios alone (``_go_*``: `ios launch`,
    `ios screenshot`, `ios launch com.apple.springboard`), so an arm can put the same video
    on the screen with no WDA request in flight. go-ios actions are written to the activity
    feed too, so a stall can be attributed to what the host was doing at that second.
    """

    def __init__(self, kinds: list[str]):
        from scripts import phone_youtube as share  # imported late: the driver reads WDA_* env at import
        from video_drop.phone import capture, device, wda_client

        share.connect_sidetap()
        self.share = share
        self.phone = share.phone
        self.device = device
        self.capture = capture
        self.log_event = wda_client.log_event
        self.kinds = kinds
        self.errors = 0

    def _step(self, name: str, fn) -> bool:
        started = time.monotonic()
        try:
            fn()
            log(name, ok=True, seconds=round(time.monotonic() - started, 2))
            return True
        except Exception as exc:  # WDAError, OSError: the recorder has the transport truth
            self.errors += 1
            log(name, ok=False, seconds=round(time.monotonic() - started, 2), error=str(exc)[:160],
                kind=type(exc).__name__, link=link_supervisor.read_status(STATE).get("state"))
            return False

    def _read(self, name: str) -> None:
        tree = self.phone.ui_tree()
        log(name, ok=True, elements=len(self.phone.collect_texts(tree)))

    def settings(self) -> dict:
        """What WDA holds for this session (GET /appium/settings): proves which knobs applied."""
        client = self.phone.client()
        try:
            value = client._session_request("GET", "/appium/settings")
        except Exception as exc:  # a driver error here is a note, not a failure
            return {"error": str(exc)[:120]}
        keys = ("accessibilityDeadline", "snapshotMaxDepth", "waitForIdleTimeout", "animationCoolOffTimeout",
                "mjpegServerFramerate", "mjpegScalingFactor", "defaultActiveApplication")
        return {k: value.get(k) for k in keys if isinstance(value, dict) and k in value}

    # -- go-ios only (no WDA) --
    def _go_launch(self, bundle: str) -> None:
        self.log_event(f"go-ios launch: {bundle}")
        proc = self.device._run(["launch", bundle], timeout=20)
        if proc.returncode != 0:
            raise RuntimeError(f"ios launch {bundle} failed: {(proc.stderr or proc.stdout)[-160:]}")

    def _go_shot(self, name: str) -> None:
        self.log_event("go-ios screenshot")
        png = self.capture._go_ios_screenshot()
        log(name, ok=True, png_bytes=len(png))

    def _go_home(self) -> None:
        self.log_event("go-ios launch: com.apple.springboard")
        if not self.device.foreground_springboard():
            raise RuntimeError("ios launch com.apple.springboard failed")

    def _wda_status(self, name: str) -> None:
        value = self.phone.client().status()
        log(name, ok=True, ready=bool(isinstance(value, dict) and value.get("ready", True)))

    def _wda_shot(self, name: str) -> None:
        png = self.phone.client().screenshot()
        log(name, ok=True, png_bytes=len(png))

    def _video_arm(self, app: str, reader: str, reads: int = 4, every: float = 8.0) -> None:
        """Open a video/GPU-heavy app, read it `reads` times with one reader, go Home.

        reader: "source" (WDA /source), "status" (WDA /status), "shot" (WDA /screenshot) or
        "pixels" (go-ios only: launch, screenshot and Home all through go-ios)."""
        bundle = VIDEO_APPS[app]
        phone = self.phone
        if reader == "pixels":
            opened = self._step(f"go-ios open {app}", lambda: self._go_launch(bundle))
        else:
            opened = self._step(f"open {app}", lambda: phone.open_app(bundle))
        if opened:
            for i in range(reads):
                time.sleep(every)
                name = f"{reader} {app} {i}"
                fn = {"source": lambda name=name: self._read(name),
                      "status": lambda name=name: self._wda_status(name),
                      "shot": lambda name=name: self._wda_shot(name),
                      "pixels": lambda name=name: self._go_shot(name)}[reader]
                if not self._step(name, fn):
                    break
        if reader == "pixels":
            self._step("go-ios home", self._go_home)
        else:
            self._step("home", phone.press_home)
        time.sleep(2)

    def cycle(self) -> None:
        phone = self.phone
        for kind in self.kinds:
            if kind == "safe":
                for app in ("Settings", "Weather", "Notes"):
                    if self._step(f"open {app}", lambda app=app: phone.open_app(app, wait_seconds=8)):
                        time.sleep(1.5)
                        self._step(f"read {app}", lambda app=app: self._read(f"read {app}"))
                    self._step("home", phone.press_home)
                    time.sleep(1.0)
            elif kind == "tiktok":
                # The For You feed plus tree reads.
                self._video_arm("tiktok", "source")
            elif kind == "tiktok-pixels":
                self._video_arm("tiktok", "pixels")
            elif kind == "tiktok-status":
                self._video_arm("tiktok", "status")
            elif kind == "tiktok-shot":
                self._video_arm("tiktok", "shot")
            elif kind == "youtube-feed":
                self._video_arm("youtube", "source", reads=3)
            elif kind == "youtube-feed-pixels":
                self._video_arm("youtube", "pixels", reads=3)
            elif kind == "photos":
                self._video_arm("photos", "source", reads=2, every=5.0)
            elif kind == "control-center":
                self._step("home", phone.press_home)
                time.sleep(1)
                layout = self.share.layout(refresh=True)
                self._step("pull Control Center", lambda: phone.swipe(layout.width - 35, 1, layout.width - 35, 290, 0.3))
                time.sleep(1.2)
                self._step("read Control Center", lambda: self._read("read Control Center"))
                time.sleep(10)
                self._step("home", phone.press_home)
                time.sleep(2)
            elif kind == "youtube-create":
                # The 12:39 / 12:47 shape: YouTube, its Create sheet, Home. Taps only a uniquely
                # labelled "Create" button; never goes further into an upload.
                if self._step("open YouTube", lambda: phone.open_app("com.google.ios.youtube", wait_seconds=8)):
                    time.sleep(3)

                    def tap_create():
                        texts = phone.collect_texts(phone.ui_tree())
                        hits = [t for t in texts if (t.get("label") or t.get("name") or "") == "Create"]
                        if len(hits) != 1:
                            raise RuntimeError(f"{len(hits)} elements labelled Create; not tapping")
                        phone.tap(hits[0]["x"], hits[0]["y"])

                    self._step("tap Create", tap_create)
                    time.sleep(4)
                    self._step("read Create sheet", lambda: self._read("read Create sheet"))
                self._step("home", phone.press_home)
                time.sleep(2)


# ---- after a stall: the owner replugs ---------------------------------------------------------

REPLUG_FILE = "NEEDS_REPLUG.txt"


def note_replug_needed(state: Path, incident: dict, now: datetime | None = None) -> Path:
    """One plain line for the person at the cable; the experiment waits for the phone after this."""
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    path = state / REPLUG_FILE
    path.write_text(f"{stamp} {incident['kind']} at {incident['at']} ({' -> '.join(incident['order'])}): "
                    "the phone's USB link stopped answering. Unplug the cable and plug it back in.\n",
                    encoding="utf-8")
    return path


def wait_for_phone(udid: str | None, minutes: float, poll: float = 30.0,
                   listed=None, sleep=time.sleep) -> bool:
    """Poll usbmuxd until it lists the phone again (True) or `minutes` pass (False)."""
    listed = listed or (lambda: link_probe.usb_device_id(link_probe.mux_list_devices()[2], udid) is not None)
    deadline = time.monotonic() + minutes * 60
    while True:
        if listed():
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(poll)


# ---- the recovery ladder ---------------------------------------------------------------

LADDER = (
    ("amds-restart", "In an ADMIN PowerShell run:  Restart-Service 'Apple Mobile Device Service'   "
                     "(host-side software only; the cable and the phone are untouched)"),
    ("pnputil-restart", "In an ADMIN PowerShell run:  pnputil /restart-device \"USB\\VID_05AC&PID_12A8\\{instance}\"   "
                        "(Windows re-enumerates the port; the cable is untouched)"),
    ("replug", "Unplug the cable from the phone and plug it back in"),
)


def lockdown_recovered(recorder: link_probe.LinkRecorder, within: float) -> float | None:
    deadline = time.monotonic() + within
    seen = len(recorder.rows)
    while time.monotonic() < deadline:
        for row in recorder.rows[seen:]:
            if link_probe.layer_ok(row, "lockdown") and link_probe.layer_ok(row, "wda") is not None:
                return round(within - (deadline - time.monotonic()), 1)
        seen = len(recorder.rows)
        time.sleep(1)
    return None


def run_ladder(recorder: link_probe.LinkRecorder, instance: str) -> list[dict]:
    results = []
    if not sys.stdin.isatty():
        log("ladder skipped", reason="no interactive terminal")
        return results
    for name, text in LADDER:
        print(f"\n[ladder] {name}: {text.format(instance=instance)}\nPress Enter here once it is done...", flush=True)
        try:
            input()
        except EOFError:
            break
        after = lockdown_recovered(recorder, 45)
        results.append({"step": name, "lockdown_back_after": after})
        log("ladder step", step_name=name, recovered=after is not None, seconds=after)
        if after is not None:
            break
    return results


# ---- verdict --------------------------------------------------------------------------------


def usb_events(since: datetime) -> list[str]:
    """Kernel-PnP 1010/1011 for the phone since `since` (read-only), as HH:MM:SS.fff strings."""
    if sys.platform != "win32":
        return []
    script = ("Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Kernel-PnP/Device Management'; "
              f"StartTime=[datetime]'{since.strftime('%Y-%m-%d %H:%M:%S')}'}} -ErrorAction SilentlyContinue | "
              "Where-Object { $_.Id -in 1010,1011 -and $_.Message -match 'VID_05AC&PID_12A8' } | "
              "ForEach-Object { $_.TimeCreated.ToString('HH:mm:ss.fff') }")
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                              timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _secs(hms: str) -> float:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def tag_usb(incidents: list[dict], events: list[str], slack: float = 5.0) -> list[dict]:
    """Mark incidents that coincide with a Windows USB removal event."""
    tagged = []
    for inc in incidents:
        at = _secs(inc["at"])
        near = [e for e in events if abs(_secs(e) - at) <= slack]
        item = {**inc, "usb_event": near[0] if near else None}
        if near and item["kind"] != "usb-drop":
            item["kind"] = "usb-drop"
        tagged.append(item)
    return tagged


def battery_envelope(rows: list[dict]) -> dict:
    """Min/max of the charging fields over a recording: was the phone draining while plugged in, how warm."""
    reads = [r["battery"] for r in rows if isinstance(r.get("battery"), dict) and "error" not in r["battery"]]
    if not reads:
        return {"reads": 0}
    out: dict = {"reads": len(reads)}
    for key in ("CurrentCapacity", "InstantAmperage", "Temperature", "Voltage"):
        values = [r[key] for r in reads if isinstance(r.get(key), (int, float))]
        if values:
            out[key] = {"min": min(values), "max": max(values)}
    for key in ("IsCharging", "ExternalConnected"):
        values = [r[key] for r in reads if key in r]
        if values:
            out[key] = {"always": all(values), "ever": any(values)}
    return out


def verdict(name: str, incidents: list[dict], minutes: float, ladder: list[dict], workload: str,
            power: str | None = None) -> str:
    stalls = [i for i in incidents if i["kind"] == "pipe-stall"]
    drops = [i for i in incidents if i["kind"] == "usb-drop"]
    tunnel_only = [i for i in incidents if i["kind"] == "tunnel-only"]
    wda_only = [i for i in incidents if i["kind"] == "wda-only"]
    parts = [f"{name}: {len(stalls)} pipe stall(s), {len(tunnel_only)} tunnel-only close(s), "
             f"{len(wda_only)} WDA-only wedge(s), {len(drops)} USB drop(s) in {minutes:g} min of [{workload}]"]
    for inc in stalls:
        order = " -> ".join(f"{layer}+{inc['first_failure_offsets'][layer]:g}s" for layer in inc["order"])
        parts.append(f"pipe stall at {inc['at']}: {order}; phone stayed listed by usbmuxd: "
                     f"{inc['device_listed_throughout']}; recovered by itself: "
                     f"{'no' if inc['recovered_after'] is None else str(inc['recovered_after']) + 's'}")
    for step in ladder:
        if step["lockdown_back_after"] is not None:
            layer = {"amds-restart": "host software (Apple Mobile Device Service): automatable with admin rights",
                     "pnputil-restart": "Windows USB stack / link: automatable with admin rights, port or cable change is the mitigation",
                     "replug": "phone side or the physical link: only a replug resets it"}[step["step"]]
            parts.append(f"ladder: {step['step']} brought lockdown back after {step['lockdown_back_after']}s -> {layer}")
            break
    else:
        if ladder:
            parts.append("ladder: no step brought lockdown back within 45s")
    if not incidents:
        parts.append("no incident: this configuration survived the workload (keep the run count honest before calling it fixed)")
    if power:
        parts.append("power: " + power)
    return "\n".join(parts)


# ---- main --------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experiment", choices=sorted(EXPERIMENTS))
    parser.add_argument("--minutes", type=float, default=20)
    parser.add_argument("--workload", default=None, help="comma list of " + ",".join(WORKLOADS))
    parser.add_argument("--ladder", action="store_true", help="on a pipe stall, walk the recovery ladder interactively")
    parser.add_argument("--restart-supervisor", action="store_true",
                        help="restart the supervisor (Python only) in the mode this experiment needs")
    parser.add_argument("--tunnel-every", type=float, default=5.0, help="seconds between tunnel round trips")
    parser.add_argument("--stop-on-stall", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--battery-every", type=float, default=15.0,
                        help="seconds between `ios batteryregistry` reads (charging state, current, temperature); 0 = off")
    parser.add_argument("--wait-replug", type=float, default=0.0, metavar="MINUTES",
                        help="after a stall that needs a replug, write .state/NEEDS_REPLUG.txt and wait this long for the phone")
    args = parser.parse_args(argv)
    exp = EXPERIMENTS[args.experiment]
    kinds = [k for k in (args.workload or exp["workload"]).split(",") if k and k != "none"]
    for kind in kinds:
        if kind not in WORKLOADS:
            parser.error(f"unknown workload {kind!r}")

    print(f"# {args.experiment}: {exp['why']}", flush=True)
    for line in exp.get("operator", []):
        print(f"# operator step: {line}", flush=True)
    problems = tunnel_preconditions(exp)
    if problems:
        for p in problems:
            print(f"precondition failed: {p}", file=sys.stderr)
        return 2
    status = link_supervisor.read_status(STATE)
    mismatch = supervisor_matches(status, exp)
    if mismatch and args.restart_supervisor:
        status = restart_supervisor(exp)
        mismatch = supervisor_matches(status, exp)
    if mismatch:
        for p in mismatch:
            print(f"precondition failed: {p}", file=sys.stderr)
        print(f"start it as:  {supervisor_command(exp)}   (or pass --restart-supervisor)", file=sys.stderr)
        return 2
    for key, value in exp["env"].items():
        os.environ[key] = value

    from video_drop.phone import config

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = STATE / f"link-experiment-{args.experiment}-{stamp}.jsonl"
    logs = {"tunnel": config.STATE_DIR / "tunnel.log", "runwda": config.STATE_DIR / "runwda.log"}
    if (SIDETAP_STATE / "tunnel.log").exists():
        logs["sidetap-tunnel"] = SIDETAP_STATE / "tunnel.log"
    entry_source = (lambda: link_probe.pmd3_entries()[2]) if exp.get("tunnel_mode") == "pmd3" \
        else (lambda: link_probe.tunnel_daemon_entries()[2])
    recorder = link_probe.LinkRecorder(out, udid=config.SIDETAP_UDID, entry_source=entry_source, logs=logs,
                                       tunnel_every=args.tunnel_every, battery_every=args.battery_every).start()
    started = datetime.now()
    log("experiment start", experiment=args.experiment, minutes=args.minutes, workload=kinds,
        supervisor=status.get("state"), tunnelMode=status.get("tunnelMode"), mjpegForward=status.get("mjpegForward"),
        recording=str(out))
    ladder: list[dict] = []
    workload = None
    try:
        if kinds:
            workload = Workload(kinds)
            if exp["env"]:
                # Settings ride with the WDA session: mint a fresh one so this run's WDA_* apply.
                client = workload.phone.client()
                client.session_id = None
                client._create_session()
            workload.phone.unlock()
            log("wda settings", **workload.settings())
        deadline = time.monotonic() + args.minutes * 60
        seen = 0
        while time.monotonic() < deadline:
            if workload:
                try:
                    workload.cycle()
                except Exception as exc:  # a dead link is the measurement, not the end of the run
                    print(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "step": "workload error",
                                      "error": f"{type(exc).__name__}: {str(exc)[:160]}"}), flush=True)
                    time.sleep(5)
            else:
                time.sleep(5)
            incidents = link_probe.classify_incidents(recorder.rows)
            fresh = [i for i in incidents[seen:] if i["kind"] == "pipe-stall"]
            seen = len(incidents)
            if fresh:
                log("pipe stall detected", incident=fresh[0])
                if args.ladder:
                    instance = (config.SIDETAP_UDID or "").replace("-", "") or "<udid without dash>"
                    ladder = run_ladder(recorder, instance)
                if args.stop_on_stall:
                    break
    finally:
        rows = recorder.stop()
        if workload:
            try:
                workload.phone.press_home()
            except Exception:
                pass
    incidents = tag_usb(link_probe.classify_incidents(rows), usb_events(started))
    summary = {"experiment": args.experiment, "minutes": round((datetime.now() - started).total_seconds() / 60, 1),
               "workload": kinds, "tunnelMode": status.get("tunnelMode"), "mjpegForward": status.get("mjpegForward"),
               "env": exp["env"], "rows": len(rows), "incidents": incidents, "ladder": ladder,
               "workloadErrors": workload.errors if workload else 0, "recording": str(out)}
    summary["battery"] = battery_envelope(rows)
    summary["power"] = link_probe.power_warning([r["battery"] for r in rows if isinstance(r.get("battery"), dict)])
    summary["verdict"] = verdict(args.experiment, incidents, summary["minutes"], ladder, ",".join(kinds) or "idle",
                                 power=summary["power"])
    (out.with_suffix(".json")).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("\n" + summary["verdict"])
    needs_replug = [i for i in incidents if i["kind"] in ("pipe-stall", "usb-drop") and i["recovered_after"] is None]
    if needs_replug and args.wait_replug > 0:
        note = note_replug_needed(STATE, needs_replug[-1])
        log("needs replug", file=str(note), waiting_minutes=args.wait_replug)
        back = wait_for_phone(config.SIDETAP_UDID, args.wait_replug)
        log("phone back" if back else "phone still absent", listed=back)
        if back:
            note.unlink(missing_ok=True)
    return 0 if not any(i["kind"] == "pipe-stall" for i in incidents) else 1


if __name__ == "__main__":
    raise SystemExit(main())
