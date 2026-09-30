"""Run one named phone-link experiment and print a verdict (docs/link-root-cause.md).

    python scripts/link_experiment.py baseline --minutes 20
    python scripts/link_experiment.py kernel   --restart-supervisor      # after `ios tunnel start` in an ADMIN terminal
    python scripts/link_experiment.py pmd3     --restart-supervisor      # after `pymobiledevice3 remote tunneld` (admin)
    python scripts/link_experiment.py no-mjpeg --restart-supervisor
    python scripts/link_experiment.py wda-lean
    python scripts/link_experiment.py idle --minutes 60 --ladder         # record only; walk the recovery ladder on a stall

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

WORKLOADS = ("none", "safe", "tiktok", "control-center", "youtube-create")

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
    """Read-only phone activity through the vendored driver; never posts, deletes or sends."""

    def __init__(self, kinds: list[str]):
        from scripts import phone_youtube as share  # imported late: the driver reads WDA_* env at import

        share.connect_sidetap()
        self.share = share
        self.phone = share.phone
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
                # The 12:20 reproduction on 2026-09-30: the For You feed plus tree reads.
                if self._step("open TikTok feed", lambda: phone.open_app("com.zhiliaoapp.musically")):
                    for i in range(4):
                        time.sleep(8)
                        if not self._step(f"read TikTok {i}", lambda i=i: self._read(f"read TikTok {i}")):
                            break
                self._step("home", phone.press_home)
                time.sleep(2)
            elif kind == "control-center":
                # Preceded three of the failures on 2026-09-30 (12:46, 12:47, 12:56).
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


def verdict(name: str, incidents: list[dict], minutes: float, ladder: list[dict], workload: str) -> str:
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
    parser.add_argument("--stop-on-stall", action="store_true", default=True)
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
                                       tunnel_every=args.tunnel_every).start()
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
        deadline = time.monotonic() + args.minutes * 60
        seen = 0
        while time.monotonic() < deadline:
            if workload:
                workload.cycle()
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
    summary["verdict"] = verdict(args.experiment, incidents, summary["minutes"], ladder, ",".join(kinds) or "idle")
    (out.with_suffix(".json")).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("\n" + summary["verdict"])
    return 0 if not any(i["kind"] == "pipe-stall" for i in incidents) else 1


if __name__ == "__main__":
    raise SystemExit(main())
