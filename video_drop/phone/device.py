"""go-ios wrapper: device discovery, tunnel/DDI probes, process bookkeeping.

Vendored from SideTap (MIT, (c) 2026 Wes Sander) `src/phone_harness/device.py`
at upstream 0c75c53 plus its uncommitted local patch (the socket + `image list`
route probe in tunnel_running); see VENDORED.md. Trimmed: the LAN/firewall
checks, the doctor's per-run memoization, `ios ps`, syslog, and every function
that STARTS a go-ios process (start_tunnel, start_wda, start_forwards, _spawn)
are gone. In this app only video_drop/link_supervisor.py starts or stops the
tunnel, the WDA runner and the port forwards; it records their pids and logs
here (`_pid_file`, `_log_file`) so `proc_status`, `log_tail` and `stop_all`
describe what it started.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import config

# CREATE_NO_WINDOW exists only on Windows; 0 keeps the same calls importable and testable elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DeviceError(RuntimeError):
    pass


PROCS = ("tunnel", "runwda", "forward8100", "forward9100")

GO_IOS_MISSING = (
    "go-ios (ios.exe) was not found. Install it with `npm install -g go-ios`, "
    "or set GO_IOS_PATH in .env to the full path of ios.exe."
)


def ios_path() -> str | None:
    """The go-ios binary, or None. GO_IOS_PATH pins it; else PATH, then the npm
    global dir. A pinned path that does not exist raises so a typo cannot fall
    back to some other ios.exe in silence."""
    pinned = config.GO_IOS_PATH
    if pinned:
        if Path(pinned).is_file():
            return pinned
        raise DeviceError(f"GO_IOS_PATH is set to {pinned!r}, but no file exists there. {GO_IOS_MISSING}")
    found = shutil.which("ios")
    if found:
        return found
    # Windows truncates a registry PATH past ~4095 chars when it builds the
    # logon environment, so shortcut/Startup launches can miss the npm global
    # dir even though terminals (which rebuild PATH in shell profiles) see it.
    npm_exe = Path(os.environ.get("APPDATA", "")) / "npm" / "ios.exe"
    if npm_exe.is_file():
        return str(npm_exe)
    return None


def pin_udid(args: list[str]) -> list[str]:
    """Append --udid when this instance is pinned to one phone (SIDETAP_UDID).

    The multi-device seam: unset (the default, every single-phone install)
    changes nothing. `list` stays global — the doctor and the fleet roster
    must see every connected phone — and `tunnel` is go-ios's all-devices
    daemon, owned by whichever instance starts it first (design doc §2.3;
    per-command --udid scoping is UNVERIFIED until a second phone exists).
    Read from config at CALL time so one process can never cache another
    instance's pin.
    """
    udid = config.SIDETAP_UDID
    out = list(args)
    if udid and args and args[0] not in ("list", "tunnel"):
        out.append(f"--udid={udid}")
    # An external RSD tunnel (pymobiledevice3's): only the commands go-ios itself routes
    # through RSD get the address, so `image list` stays a plain lockdown probe.
    if config.GO_IOS_RSD_ADDRESS and config.GO_IOS_RSD_PORT and args and args[0] in RSD_COMMANDS:
        out += [f"--address={config.GO_IOS_RSD_ADDRESS}", f"--rsd-port={config.GO_IOS_RSD_PORT}"]
    return out


# go-ios commands that look the tunnel up before running (needsAutomaticTunnelInfo in
# go-ios 1.3.2's cli_device_resolution.go); the rest use lockdown over usbmux.
RSD_COMMANDS = ("runwda", "launch", "kill", "screenshot", "syslog", "ps", "instruments", "runxctest",
                "runtest", "devicestate", "pasteboard", "sysmontap", "ostrace", "debug", "memlimitoff",
                "setlocation", "setlocationgpx", "resetlocation")


def _run(args: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess:
    exe = ios_path()
    if not exe:
        raise DeviceError(GO_IOS_MISSING)
    return subprocess.run(
        [exe, *pin_udid(args)],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def _json_lines(text: str) -> list[dict]:
    """go-ios prints one JSON object per line (or a single object)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                parsed = json.loads(line)
                out.extend(parsed if isinstance(parsed, list) else [parsed])
            except json.JSONDecodeError:
                pass
    return out


def list_devices() -> list[str]:
    """UDIDs of USB-connected iPhones."""
    proc = _run(["list"])
    result = []
    for obj in _json_lines(proc.stdout + proc.stderr):
        if "deviceList" in obj:
            result = list(obj["deviceList"])
            break
    return result


def _apps_cache_file() -> Path:
    return config.STATE_DIR / "apps_cache.json"


def list_apps() -> list[dict]:
    """Installed apps as [{bundle_id, name}]. Parses go-ios output tolerantly.

    Deep sleep EMPTIES `ios apps --list` while the apps stay installed (same
    failure mode as detect_wda_bundle's own cache, docs/ERRORS.md) — a live
    empty result never overwrites a good persisted list, and a live non-empty
    result always does (so a newly installed app is still findable).
    """
    proc = _run(["apps", "--list"], timeout=15)
    apps = []
    for obj in _json_lines(proc.stdout):
        # newer go-ios: JSON objects with CFBundleIdentifier / CFBundleName
        bid = (
            obj.get("CFBundleIdentifier") or obj.get("bundleId") or obj.get("bundle_id")
        )
        if bid:
            apps.append(
                {
                    "bundle_id": bid,
                    "name": obj.get("CFBundleName") or obj.get("name", ""),
                }
            )
    if not apps:
        # fallback: plain "bundleid name" lines
        for line in proc.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) >= 1 and "." in parts[0]:
                apps.append(
                    {"bundle_id": parts[0], "name": parts[1] if len(parts) > 1 else ""}
                )
    if apps:
        try:
            config.STATE_DIR.mkdir(exist_ok=True)
            _apps_cache_file().write_text(json.dumps(apps), encoding="utf-8")
        except OSError:
            pass
    else:
        try:
            apps = json.loads(_apps_cache_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            apps = []
    return apps


def kill_app(bundle_id: str) -> bool:
    """Force-quit an app by bundle id (`ios kill`): the switcher's swipe-up.
    False when go-ios reports nothing to kill (already gone)."""
    proc = _run(["kill", bundle_id], timeout=15)
    return proc.returncode == 0


def _wda_cache_file() -> Path:
    return config.STATE_DIR / "wda_bundle"


def detect_wda_bundle() -> str | None:
    """Find the installed WebDriverAgent runner. .env WDA_BUNDLE_ID wins.

    Deep sleep gates the app list (`ios apps --list` comes back EMPTY while
    the app is still installed), so a successful live
    detection is cached in .state/wda_bundle and an empty list falls back to
    that cache. A NON-empty list without WDA means genuinely uninstalled and
    ignores the cache.
    """
    if config.WDA_BUNDLE_ID:
        return config.WDA_BUNDLE_ID
    try:
        apps = list_apps()
    except (DeviceError, subprocess.TimeoutExpired):
        apps = []
    for app in apps:
        bid = app["bundle_id"].lower()
        if "webdriveragent" in bid or bid.endswith(".xctrunner"):
            try:
                config.STATE_DIR.mkdir(exist_ok=True)
                _wda_cache_file().write_text(app["bundle_id"], encoding="utf-8")
            except OSError:
                pass
            return app["bundle_id"]
    if not apps:
        try:
            return _wda_cache_file().read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def tunnel_running(timeout: float = 10.0) -> bool:
    """Is the userspace tunnel up AND is its route to the phone answering?

    `timeout` bounds the whole probe (upstream: so start_tunnel()'s readiness
    poll could hand each probe only the budget it had left). Carries SideTap's
    uncommitted patch: a `tunnel ls` entry alone is not enough —
    the listener is dialled and `image list` exercises the RSD route."""
    started = time.monotonic()
    try:
        proc = _run(["tunnel", "ls"], timeout=timeout)
    except (DeviceError, subprocess.TimeoutExpired):
        return False
    for obj in _json_lines(proc.stdout + proc.stderr):
        # skip go-ios log lines ({"level": ..., "msg": ...}); a real tunnel
        # entry carries an address + RSD port
        if obj.get("level"):
            continue
        if obj.get("address") and obj.get("rsdPort"):
            # go-ios can retain a tunnel record after its userspace listener
            # dies. In that state every RSD request is refused, even though
            # `tunnel ls` still claims the tunnel is up.
            port = obj.get("userspaceTunPort")
            if port:
                try:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        return False
                    with socket.create_connection(("127.0.0.1", int(port)), timeout=min(remaining, 0.5)):
                        pass
                except (OSError, ValueError):
                    continue
            # A local userspace listener can accept TCP while its route to the
            # phone's RSD service is dead. `image list` exercises that route
            # without requiring the developer image to be mounted.
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                return False
            try:
                route = _run(["image", "list"], timeout=remaining)
            except (DeviceError, subprocess.TimeoutExpired):
                continue
            if route.returncode == 0:
                return True
    return False


def ddi_mounted() -> bool:
    """Is the personalized Developer Disk Image mounted? An iOS UPDATE silently
    unmounts it, and without it testmanagerd refuses every test session — runwda
    dies in dtx channel timeouts that look like a broken tunnel (seen with
    the 26.5→26.6 update). Mounted: `image list` prints a line with
    a "signature" key; unmounted: msg "none"."""
    try:
        proc = _run(["image", "list"], timeout=15)
    except (DeviceError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return any(obj.get("signature") for obj in _json_lines(proc.stdout + proc.stderr))


def mount_ddi() -> tuple[bool, str]:
    """Mount the developer image (`ios image auto`). The phone must be UNLOCKED
    (iOS answers DeviceLocked otherwise); the first mount after an iOS update
    also needs internet (Apple TSS signs the image). Success is verified by
    re-probing, not by parsing the mount log."""
    try:
        proc = _run(["image", "auto"], timeout=180)
    except (DeviceError, subprocess.TimeoutExpired) as exc:
        return False, f"`ios image auto` failed: {exc}"
    if ddi_mounted():
        return True, "developer image mounted"
    out = proc.stdout + proc.stderr
    if "DeviceLocked" in out:
        return False, "phone is locked — unlock it, then retry"
    tail = out.strip().splitlines()[-1] if out.strip() else "no output"
    return False, f"`ios image auto` did not mount: {tail}"


# ---- detached process bookkeeping ----------------------------------------
# The supervisor spawns the processes (link_supervisor.Runner._spawn) and
# records them here; nothing in this module starts one.


def _pid_file(name: str) -> Path:
    return config.STATE_DIR / f"{name}.pid"


def _log_file(name: str) -> Path:
    return config.STATE_DIR / f"{name}.log"


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
        )
        return str(pid) in proc.stdout
    try:
        import os

        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_image(pid: int) -> str:
    """Executable name for a live PID, lowercased ('' if dead or unknown)."""
    if sys.platform == "win32":
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
        )
        first = proc.stdout.strip().splitlines()
        if first and first[0].startswith('"'):
            return first[0].split('","')[0].strip('"').lower()
        return ""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip().lower()
    except OSError:
        return ""


def _safe_kill(pid: int, expected_prefix: str, tree: bool = True) -> bool:
    """Force-kill `pid` only if its executable name starts with
    `expected_prefix`. Pid files outlive their process and Windows reuses
    pids, so an unchecked kill could hit an innocent process. Returns True
    if a kill was issued.

    `tree=False` spares the children. The tunnel and the forwards are children
    of whatever launched them, so killing a viewer's tree takes the phone link
    down with it — see _kill_stale_viewer."""
    if not expected_prefix or not _pid_image(pid).startswith(expected_prefix.lower()):
        return False
    if sys.platform == "win32":
        cmd = ["taskkill", "/PID", str(pid), "/F"] + (["/T"] if tree else [])
        subprocess.run(
            cmd,
            capture_output=True,
            creationflags=_NO_WINDOW,
        )
    else:
        import os
        import signal

        os.kill(pid, signal.SIGTERM)
    return True


def proc_status(name: str) -> str:
    """'running', 'dead', or 'not started'."""
    pf = _pid_file(name)
    if not pf.exists():
        return "not started"
    try:
        pid = int(pf.read_text().strip())
    except ValueError:
        return "not started"
    return "running" if _pid_alive(pid) else "dead"


def log_tail(name: str, lines: int = 5) -> str:
    lf = _log_file(name)
    if not lf.exists():
        return ""
    return "\n".join(
        lf.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    )


def stop_all(names: tuple[str, ...] = PROCS) -> list[str]:
    """Kill every process the supervisor started. Returns names of processes stopped.

    `names` narrows it to a subset so one process can be cleared without
    taking the phone link down with it. Only the supervisor calls this.
    """
    exe = ios_path()
    expected = Path(exe).name.lower() if exe else "ios"
    stopped = []
    for name in names:
        pf = _pid_file(name)
        if not pf.exists():
            continue
        try:
            pid = int(pf.read_text().strip())
        except ValueError:
            pf.unlink()
            continue
        if _safe_kill(pid, expected):
            stopped.append(name)
        pf.unlink()
    return stopped


# ---- link repair -------------------------------------------------------------
# Bring-up (start_tunnel with its 12s readiness cap, start_wda, start_forwards)
# is not vendored: the supervisor's Decider owns those waits (Policy.entry_grace
# and the tunnel backoff), and a second starter beside it is the two-healers
# fight that takes a healthy tunnel down.


def foreground_springboard() -> bool:
    """Put the Home Screen in front over USB, with no WDA involvement.

    The only escape from a WEDGED WebDriverAgent. An app whose accessibility
    server never answers blocks every WDA call that resolves the active
    application - gestures included - and WDA serves requests one at a time, so
    the whole agent stops: /status, /screenshot and the viewer all queue behind
    the blocked call. WDA's own /wda/homescreen queues there too, and restarting
    the runner on top of the stuck one fails with XCTest error 103.
    Foregrounding another app releases the AX wait: measured against
    TikTok's For You feed, WDA answered again ~20s later, no restart needed.
    """
    ios = ios_path()
    if not ios:
        return False
    try:
        return _run(["launch", "com.apple.springboard"], timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError, DeviceError):
        return False


def _free_port(port: int) -> None:
    """Kill our own leftover `ios forward` bound to `port`.

    Repeated bring-ups spawn a new forwarder and overwrite its pid file,
    orphaning the previous one - which keeps the port and blocks WDA from ever
    being reachable. Only ios.exe listeners are killed; unrelated ports are left
    alone.
    """
    if sys.platform != "win32":
        return
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
        )
    except OSError:
        return
    pids = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if (
            len(parts) >= 5
            and parts[3] == "LISTENING"
            and parts[1].endswith(f":{port}")
        ):
            pids.add(parts[4])
    for pid in pids:
        info = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
        )
        if "ios.exe" in info.stdout.lower():
            subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                capture_output=True,
                creationflags=_NO_WINDOW,
            )


def current_udid() -> str | None:
    """This instance's phone: the PHONE_UDID pin, else the first connected."""
    if config.SIDETAP_UDID:
        return config.SIDETAP_UDID
    try:
        udids = list_devices()
    except (DeviceError, subprocess.TimeoutExpired):
        return None
    return udids[0] if udids else None


def sign_app(
    ipa: Path,
    p12: Path,
    profile: Path,
    p12password: str = "",
    bundleid: str | None = None,
    install: bool = True,
) -> str:
    """Sign an IPA (nested .xctest included) with `ios sign app` and install it.

    This is the step Sideloadly skips: go-ios re-signs the nested
    WebDriverAgentRunner.xctest with the same Team ID as the host app, which is
    what iOS Library Validation requires. `bundleid` overrides the app's bundle
    id so it matches the provisioning profile. Returns the combined go-ios output.
    """
    args = [
        "sign",
        "app",
        f"--path={ipa}",
        f"--p12file={p12}",
        f"--profile={profile}",
    ]
    if p12password:
        args.append(f"--p12password={p12password}")
    if bundleid:
        args.append(f"--bundleid={bundleid}")
    if install:
        args.append("--install")
    proc = _run(args, timeout=300)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise DeviceError(f"`ios sign app` failed:\n{out[-1200:]}")
    return out
