"""Tell a USB *pipe stall* from a WebDriverAgent wedge and from an unplugged phone.

The stall (docs/link-root-cause.md, signature S1): lockdown, WebDriverAgent and
the go-ios tunnel all stop moving bytes at once while Apple Mobile Device
Service still lists the phone. From WDA's side it looks exactly like a video
wedge (the forward accepts, nothing answers), and the supervisor used to press
Home into it, wait five minutes, restart a daemon that could not negotiate
either, and then ask for a replug. Three raw probes from ``link_probe`` settle
it in a few seconds, every one bounded and read-only on the phone:

  usbmux ListDevices        -> is the phone still in the device table
  lockdown QueryType        -> does the USB data pipe carry bytes both ways
  WDA /status via usbmux    -> does the pipe reach WDA without the forward

Classes:
  ok         lockdown answered (or WDA did over the same pipe): the pipe is alive.
             A silent WDA with a live pipe is a WDA-only wedge: press Home.
  stalled    the phone is listed and BOTH lockdown and WDA timed out.
  no-device  usbmuxd does not list the phone (unplugged, or mid re-enumeration).
  unknown    usbmuxd itself did not answer, or a probe failed some other way
             (refused, EOF): not evidence of a stall, so it never counts.

The supervisor only runs this when WDA through the forward is not answering,
so a healthy link pays nothing, and a broken one pays at most ~4 s per tick.
"""

from __future__ import annotations

import time

from . import link_probe

PIPE_OK = "ok"
PIPE_STALLED = "stalled"
PIPE_NO_DEVICE = "no-device"
PIPE_UNKNOWN = "unknown"

# ``link_probe`` reports a probe's failure as the exception's class name. A stall
# is a *timeout* on both hops; a refused connect or an EOF is something else.
TIMEOUT_REASONS = ("TimeoutError", "timeout", "timed out")


def timed_out(reason: str) -> bool:
    return any(needle.lower() in str(reason).lower() for needle in TIMEOUT_REASONS)


def classify_pipe(mux: str, device_listed: bool, lockdown: str | None, wda: str | None) -> str:
    """Pure classification of one round of raw probe results (see the module doc)."""
    if mux != "ok":
        return PIPE_UNKNOWN
    if not device_listed:
        return PIPE_NO_DEVICE
    if lockdown == "ok" or wda == "ok":
        return PIPE_OK
    if lockdown is not None and wda is not None and timed_out(lockdown) and timed_out(wda):
        return PIPE_STALLED
    return PIPE_UNKNOWN


def probe_pipe(*, udid: str | None = None, timeout: float = 2.0, wda_port: int = link_probe.WDA_PORT,
               mux=link_probe.USBMUX) -> dict:
    """One round of the three raw probes, each bounded by ``timeout`` (never above 2 s by policy).

    Returns ``{"pipe": <class>, "mux": [status, ms], "lockdown": [status, ms],
    "wda": [status, ms], "ms": total}`` so the supervisor can log exactly what
    it saw. WDA is only asked when lockdown did not answer: a live lockdown is
    already the whole answer.
    """
    started = time.monotonic()
    timeout = min(float(timeout), 2.0)
    mux_status, mux_ms, devices = link_probe.mux_list_devices(timeout, mux)
    device_id = link_probe.usb_device_id(devices, udid) if mux_status == "ok" else None
    row: dict = {"mux": [mux_status, mux_ms], "lockdown": None, "wda": None}
    lockdown = wda = None
    if device_id is not None:
        lockdown, lockdown_ms = link_probe.lockdown_query_type(device_id, timeout, mux)
        row["lockdown"] = [lockdown, lockdown_ms]
        if lockdown != "ok":
            wda, wda_ms = link_probe.wda_status_direct(device_id, timeout, wda_port, mux)
            row["wda"] = [wda, wda_ms]
    row["pipe"] = classify_pipe(mux_status, device_id is not None, lockdown, wda)
    row["ms"] = int((time.monotonic() - started) * 1000)
    return row
