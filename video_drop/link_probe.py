"""Layered probes of the phone link, one per transport hop, plus a 1 Hz recorder.

Why per layer: every "link failure" looks the same from WDA's
side (silence), but at least two different things can sit underneath:

* a *pipe stall*: WDA over the usbmux forward, lockdown (`ios image list`) and
  the go-ios tunnel (a CoreDeviceProxy stream over the same usbmux) all stop
  answering within one keepalive window while `ios list` (usbmuxd's own device
  table, no device I/O) keeps working; only a physical replug recovers;
* a *tunnel-only close*: the CoreDeviceProxy stream gets EOF while WDA keeps
  answering over usbmux, and a fresh tunnel negotiates seconds later.

`/status` through the forward and `tunnel ls` alone cannot tell those apart.
These probes talk to each hop
directly, in-process, with millisecond timing, and never spawn go-ios:

  usbmux  ListDevices          -> Apple Mobile Device Service's device table
  lockdown QueryType (:62078)  -> lockdownd on the phone, through the USB pipe
  WDA /status (:8100 via mux)  -> WebDriverAgent, same pipe, no forward process
  RSD over the tunnel          -> an HTTP/2 SETTINGS round trip to the RSD port
                                  (userspace port + preamble, or [addr]:port for
                                  a kernel/pymobiledevice3 tunnel)

All of it is read-only on the phone. A tunnel probe is one short-lived TCP
connection through the tunnel; it is rate-limited (``tunnel_every``) to keep connects
off every tick.
"""

from __future__ import annotations

import json
import plistlib
import socket
import struct
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

USBMUX = ("127.0.0.1", 27015)
LOCKDOWN_PORT = 62078
WDA_PORT = 8100
TUNNEL_API = "http://127.0.0.1:60105/tunnels"
PMD3_API = "http://127.0.0.1:49151/"
H2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
H2_EMPTY_SETTINGS = b"\x00\x00\x00\x04\x00\x00\x00\x00\x00"

Result = tuple  # (status: "ok" | "<reason>", milliseconds: int) plus optional detail


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-4]


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


# ---- usbmux -------------------------------------------------------------------


def _mux_send(sock: socket.socket, payload: dict, tag: int = 1) -> None:
    body = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
    sock.sendall(struct.pack("<IIII", 16 + len(body), 1, 8, tag) + body)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("EOF")
        data += chunk
    return data


def _mux_recv(sock: socket.socket) -> dict:
    length, _version, _kind, _tag = struct.unpack("<IIII", _recv_exact(sock, 16))
    return plistlib.loads(_recv_exact(sock, length - 16))


def _client_fields() -> dict:
    return {"ClientVersionString": "video-drop link probe", "ProgName": "video-drop", "kLibUSBMuxVersion": 3}


def mux_list_devices(timeout: float = 1.5, mux=USBMUX) -> tuple[str, int, list[dict]]:
    """('ok', ms, devices) when usbmuxd answers; devices are its USB entries."""
    started = time.monotonic()
    try:
        with socket.create_connection(mux, timeout=timeout) as sock:
            _mux_send(sock, {"MessageType": "ListDevices", **_client_fields()})
            reply = _mux_recv(sock)
        devices = [d for d in reply.get("DeviceList", []) if isinstance(d, dict)]
        return "ok", _ms(started), devices
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        return type(exc).__name__, _ms(started), []


def usb_device_id(devices: list[dict], udid: str | None = None) -> int | None:
    for dev in devices:
        props = dev.get("Properties", {})
        if props.get("ConnectionType", "USB") != "USB":
            continue
        if udid and props.get("SerialNumber") != udid:
            continue
        return int(dev["DeviceID"])
    return None


def mux_connect(device_id: int, port: int, timeout: float, mux=USBMUX) -> socket.socket:
    """A socket that, after the usbmux handshake, is a TCP stream to ``port`` on the phone."""
    sock = socket.create_connection(mux, timeout=timeout)
    try:
        _mux_send(sock, {"MessageType": "Connect", "DeviceID": device_id, "PortNumber": socket.htons(port),
                         **_client_fields()})
        reply = _mux_recv(sock)
        if reply.get("Number") != 0:
            raise ConnectionError(f"usbmux connect to :{port} refused ({reply.get('Number')})")
        return sock
    except BaseException:
        sock.close()
        raise


# ---- per-hop probes -------------------------------------------------------------


def lockdown_query_type(device_id: int, timeout: float = 1.5, mux=USBMUX) -> Result:
    """lockdownd's unauthenticated QueryType: proves the USB pipe carries bytes both ways."""
    started = time.monotonic()
    try:
        with mux_connect(device_id, LOCKDOWN_PORT, timeout, mux) as sock:
            body = plistlib.dumps({"Label": "video-drop", "Request": "QueryType"}, fmt=plistlib.FMT_XML)
            sock.sendall(struct.pack(">I", len(body)) + body)
            (length,) = struct.unpack(">I", _recv_exact(sock, 4))
            reply = plistlib.loads(_recv_exact(sock, length))
        if reply.get("Type") == "com.apple.mobile.lockdown":
            return "ok", _ms(started)
        return f"unexpected {reply.get('Type') or reply.get('Error')}", _ms(started)
    except (OSError, ValueError, plistlib.InvalidFileException) as exc:
        return type(exc).__name__, _ms(started)


def wda_status_direct(device_id: int, timeout: float = 1.5, port: int = WDA_PORT, mux=USBMUX) -> Result:
    """GET /status on the phone's :8100 through a fresh usbmux connection (no `ios forward` in the way)."""
    started = time.monotonic()
    try:
        with mux_connect(device_id, port, timeout, mux) as sock:
            sock.sendall(b"GET /status HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 4096:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                head += chunk
        if head.startswith(b"HTTP/1.1 200") or head.startswith(b"HTTP/1.0 200"):
            return "ok", _ms(started)
        if not head:
            return "empty", _ms(started)
        return head.split(b"\r\n", 1)[0].decode("ascii", "replace")[:40], _ms(started)
    except OSError as exc:
        return type(exc).__name__, _ms(started)


def tunnel_rsd_roundtrip(entry: dict, timeout: float = 2.0) -> Result:
    """Open one TCP connection to the phone's RSD port through the tunnel and
    exchange HTTP/2 SETTINGS. Works for go-ios userspace tunnels (local port +
    20-byte preamble), go-ios kernel tunnels and pymobiledevice3 tunnels (direct
    IPv6 connect over the TUN adapter)."""
    started = time.monotonic()
    address, port = entry.get("address"), int(entry.get("rsdPort") or 0)
    if not address or not port:
        return "no entry", 0
    try:
        userspace_port = entry.get("userspaceTunPort")
        if userspace_port:
            sock = socket.create_connection(("127.0.0.1", int(userspace_port)), timeout=timeout)
            sock.sendall(socket.inet_pton(socket.AF_INET6, address) + struct.pack("<I", port))
        else:
            sock = socket.create_connection((address, port), timeout=timeout)
        with sock:
            sock.sendall(H2_PREFACE + H2_EMPTY_SETTINGS)
            frame = _recv_exact(sock, 9)
        if frame[3] == 4:  # a SETTINGS frame: the RSD service on the phone answered
            return "ok", _ms(started)
        return f"frame type {frame[3]}", _ms(started)
    except OSError as exc:
        return type(exc).__name__, _ms(started)


def tunnel_daemon_entries(timeout: float = 1.5, api: str = TUNNEL_API) -> tuple[str, int, list[dict]]:
    """go-ios tunnel daemon API: its list of tunnels (local call, no device I/O)."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(api, timeout=timeout) as response:
            data = json.load(response)
        entries = data if isinstance(data, list) else [data]
        return "ok", _ms(started), [e for e in entries if isinstance(e, dict) and e.get("address")]
    except (OSError, ValueError) as exc:
        return type(exc).__name__, _ms(started), []


def pmd3_entries(timeout: float = 1.5, api: str = PMD3_API) -> tuple[str, int, list[dict]]:
    """pymobiledevice3 tunneld API, mapped to go-ios's entry shape ({address, rsdPort, udid})."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(api, timeout=timeout) as response:
            data = json.load(response)
    except (OSError, ValueError) as exc:
        return type(exc).__name__, _ms(started), []
    entries = []
    for udid, tunnels in (data.items() if isinstance(data, dict) else []):
        for tun in tunnels if isinstance(tunnels, list) else [tunnels]:
            if isinstance(tun, dict) and tun.get("tunnel-address") and tun.get("tunnel-port"):
                entries.append({"udid": udid, "address": tun["tunnel-address"], "rsdPort": int(tun["tunnel-port"]),
                                "userspaceTun": False, "interface": tun.get("interface")})
    return "ok", _ms(started), entries


# ---- battery / charging (power hypothesis) ------------------------------------------

BATTERY_KEYS = ("CurrentCapacity", "IsCharging", "ExternalConnected", "InstantAmperage", "Voltage",
                "Temperature", "FullyCharged", "AdapterDetails")


def battery_snapshot(udid: str | None = None, timeout: float = 8.0, ios: str | None = None) -> dict:
    """The phone's IOPMPowerSource registry through `ios batteryregistry` (diagnostics relay over
    lockdown, no tunnel; `batterycheck` is the lockdown battery domain and carries no current or
    temperature): capacity %, whether it charges, the instantaneous current (mA; negative =
    draining while plugged in) and the pack temperature (centi-degrees C). Spawns go-ios, so it runs on its own
    cadence (``battery_every``), never per tick. An error is a data point ({"error": ...})."""
    import subprocess
    import sys

    exe = ios
    if exe is None:
        from .phone import device

        exe = device.ios_path()
    if not exe:
        return {"error": "no go-ios"}
    args = [exe, "batteryregistry"] + ([f"--udid={udid}"] if udid else [])
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": type(exc).__name__}
    return parse_battery(proc.stdout + proc.stderr)


def parse_battery(text: str) -> dict:
    """Keep the keys that matter from go-ios's JSON (one object, or one per line)."""
    found: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        for src in (data, data.get("data") if isinstance(data.get("data"), dict) else {}):
            for key in BATTERY_KEYS:
                if key in src:
                    value = src[key]
                    if key == "AdapterDetails" and isinstance(value, dict):
                        value = {k: value.get(k) for k in ("Watts", "Current", "Voltage", "Description") if k in value}
                    found[key] = value
    return found or {"error": "no battery data", "raw": text.strip()[:160]}


def power_warning(reads: list[dict], min_reads: int = 2) -> str | None:
    """One sentence when the port cannot power the phone under load, else None.

    Measured on the chipset port: IsCharging true, capacity 1-2%, and
    InstantAmperage -2340..-2463 mA while TikTok/YouTube played (pack voltage 3.33 V).
    A phone that drains while plugged in is one heavy screen away from a brownout, so this
    fires on the SECOND negative sample (one can be a stale registry read)."""
    good = [r for r in reads if isinstance(r, dict) and "error" not in r]
    draining = [r for r in good if r.get("IsCharging") and isinstance(r.get("InstantAmperage"), (int, float))
                and r["InstantAmperage"] < 0]
    if len(draining) < min_reads:
        return None
    worst = min(r["InstantAmperage"] for r in draining)
    capacity = min((r["CurrentCapacity"] for r in draining if isinstance(r.get("CurrentCapacity"), (int, float))),
                   default=None)
    cap = f" at {capacity}% battery" if capacity is not None else ""
    return (f"The USB port cannot power the phone under load: it drained at {abs(worst)} mA while plugged in"
            f"{cap} ({len(draining)} of {len(good)} reads). Charge it on a wall charger and use a port that "
            "delivers more current (a CPU-attached rear port, a powered hub, or USB-C PD).")


# ---- the recorder -----------------------------------------------------------------


class LogTail:
    """New ERROR/closed/Killing lines of a go-ios log since the last look (like the old recorder)."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = path.stat().st_size if path.exists() else 0

    def new_lines(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0
        if size == self.offset:
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            new = handle.read().decode("utf-8", "ignore").splitlines()
        self.offset = size
        return [line[:240] for line in new
                if ('"level":"ERROR"' in line or '"level":"WARN"' in line or "closed" in line or "Killing" in line)
                and "proxyConns failed: writeto" not in line]  # the recorder's own RSD probe closing its socket


class LinkRecorder:
    """Probe every hop once a second on a background thread and append rows to a JSONL file."""

    def __init__(self, out: Path, *, udid: str | None = None, entry_source: Callable[[], list[dict]] | None = None,
                 logs: dict[str, Path] | None = None, interval: float = 1.0, tunnel_every: float = 5.0,
                 probe_timeout: float = 1.5, wda_port: int = WDA_PORT, mux: tuple = USBMUX,
                 battery_every: float = 0.0, battery_source: Callable[[], dict] | None = None):
        self.out = out
        self.udid = udid
        self.mux = mux
        self.entry_source = entry_source or (lambda: tunnel_daemon_entries()[2])
        self.tails = {name: LogTail(path) for name, path in (logs or {}).items()}
        self.interval = interval
        self.tunnel_every = tunnel_every
        self.probe_timeout = probe_timeout
        self.wda_port = wda_port
        self.rows: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tunnel = 0.0
        self._entry: dict | None = None
        self._entry_checked = 0.0
        # 0 = off. On: one `ios batteryregistry` every battery_every seconds, only while lockdown
        # answers (a stalled pipe would just add a hung go-ios to the picture).
        self.battery_every = battery_every
        self.battery_source = battery_source or (lambda: battery_snapshot(udid))
        self._battery_checked = 0.0

    # one tick, exposed for tests
    def probe_once(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        row: dict = {"t": _now()}
        status, ms, devices = mux_list_devices(self.probe_timeout, self.mux)
        device_id = usb_device_id(devices, self.udid) if status == "ok" else None
        row["mux"] = [status if status != "ok" else ("ok" if device_id is not None else "no device"), ms]
        if device_id is not None:
            row["lockdown"] = list(lockdown_query_type(device_id, self.probe_timeout, self.mux))
            row["wda"] = list(wda_status_direct(device_id, self.probe_timeout, self.wda_port, self.mux))
        else:
            row["lockdown"] = row["wda"] = ["no device", 0]
        if now - self._entry_checked >= self.tunnel_every:
            try:
                entries = self.entry_source()
            except Exception as exc:  # the API being down is a data point, not a crash
                entries = []
                row["daemon"] = type(exc).__name__
            self._entry = entries[0] if entries else None
            self._entry_checked = now
            row["tunnel"] = list(tunnel_rsd_roundtrip(self._entry, self.probe_timeout + 0.5)) if self._entry \
                else ["no entry", 0]
            if self._entry:
                row["entry"] = {k: self._entry.get(k) for k in ("address", "rsdPort", "userspaceTun", "userspaceTunPort")}
        if self.battery_every and now - self._battery_checked >= self.battery_every                 and layer_ok(row, "lockdown"):
            self._battery_checked = now
            try:
                row["battery"] = self.battery_source()
            except Exception as exc:  # a failed read is a data point
                row["battery"] = {"error": type(exc).__name__}
        for name, tail in self.tails.items():
            lines = tail.new_lines()
            if lines:
                # "log:" prefix: a tail named "tunnel" would overwrite the tunnel PROBE result
                # (every RSD round trip makes go-ios log a proxyConns error, so without the prefix
                # the tunnel column is mostly log lines, not probes).
                row[f"log:{name}"] = lines[:6]
        return row

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                row = self.probe_once(started)
            except Exception as exc:  # never let the recorder die mid-experiment
                row = {"t": _now(), "error": repr(exc)}
            self.rows.append(row)
            try:
                with self.out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
            except OSError:
                pass
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def start(self) -> "LinkRecorder":
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._loop, name="link-recorder", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + self.probe_timeout * 4 + 2)
        return self.rows


# ---- reading a recording back ------------------------------------------------------

LAYERS = ("mux", "lockdown", "wda", "tunnel")


def layer_ok(row: dict, layer: str) -> bool | None:
    """True/False for a probed layer, None when the row did not probe it."""
    value = row.get(layer)
    if not isinstance(value, list) or not value:
        return None
    return value[0] == "ok"


def _seconds(row: dict) -> float:
    h, m, s = row["t"].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def classify_incidents(rows: list[dict], *, settle: int = 3, window: float = 60.0) -> list[dict]:
    """Group failures into incidents and say which hop died first and whether it was the pipe.

    An incident opens when lockdown, WDA or the tunnel goes from ok to failed after
    at least ``settle`` consecutive good rows. Within ``window`` seconds it records
    the first failure time of every layer and when everything answered again.

    Classes (the labels the experiment plan reasons about):
      usb-drop      usbmuxd stopped listing the phone (a real USB disconnect)
      pipe-stall    lockdown failed and stayed failed while the phone stayed listed:
                    the USB data pipe is dead, whatever WDA and the tunnel say
      tunnel-only   the tunnel round trip failed while lockdown and WDA kept answering
      wda-only      WDA failed while lockdown and the tunnel kept answering
                    (a genuine WebDriverAgent/accessibility wedge)
      mixed         anything else (report the order and look at the rows)
    """
    incidents: list[dict] = []
    good_streak = 0
    i = 0
    while i < len(rows):
        row = rows[i]
        probed = [layer for layer in ("lockdown", "wda", "tunnel") if layer_ok(row, layer) is not None]
        failed = [layer for layer in probed if layer_ok(row, layer) is False]
        if not failed:
            good_streak += 1 if probed else 0
            i += 1
            continue
        if good_streak < settle:
            good_streak = 0
            i += 1
            continue
        start = _seconds(row)
        first_fail: dict[str, float] = {}
        device_gone = False
        recovered_at: float | None = None
        lockdown_failed_for = 0.0
        last_lockdown_fail: float | None = None
        j = i
        while j < len(rows) and _seconds(rows[j]) - start <= window:
            current = rows[j]
            when = _seconds(current)
            if current.get("mux", ["ok"])[0] in ("no device",):
                device_gone = True
            all_ok = True
            for layer in LAYERS:
                ok = layer_ok(current, layer)
                if ok is False:
                    first_fail.setdefault(layer, when)
                    all_ok = False
                    if layer == "lockdown":
                        last_lockdown_fail = when
                elif ok is None and layer == "tunnel":
                    continue
                elif ok is None:
                    all_ok = False
            if all_ok and (when - start) >= 1.0 and recovered_at is None:
                recovered_at = when
                break
            j += 1
        if last_lockdown_fail is not None and "lockdown" in first_fail:
            lockdown_failed_for = last_lockdown_fail - first_fail["lockdown"]
        order = [layer for layer, _ in sorted(first_fail.items(), key=lambda kv: kv[1])]
        if device_gone or "mux" in first_fail:
            kind = "usb-drop"
        elif "lockdown" in first_fail and (recovered_at is None or lockdown_failed_for >= 10.0):
            kind = "pipe-stall"
        elif set(first_fail) == {"tunnel"}:
            kind = "tunnel-only"
        elif set(first_fail) == {"wda"}:
            kind = "wda-only"
        else:
            kind = "mixed"
        incidents.append({"at": row["t"], "kind": kind, "order": order,
                          "first_failure_offsets": {k: round(v - start, 2) for k, v in first_fail.items()},
                          "recovered_after": None if recovered_at is None else round(recovered_at - start, 1),
                          "device_listed_throughout": not device_gone})
        good_streak = 0
        i = j + 1
    return incidents


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows
