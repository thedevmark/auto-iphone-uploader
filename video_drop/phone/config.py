"""Ports, paths, and .env loading for the vendored phone driver.

Vendored from SideTap (MIT, (c) 2026 Wes Sander) `src/phone_harness/config.py`
at upstream 0c75c53; see VENDORED.md in this package. Trimmed to the settings
the driver reads. Changed on purpose: the state directory is the app's own
(`.state/phone/` under VIDEO_DROP_STATE), the .env is the app's own, and the
go-ios binary can be pinned with GO_IOS_PATH.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


def _load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse a KEY=VALUE .env file. Missing file is fine."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _legacy_sidetap_env() -> dict[str, str]:
    """Migration only: the passcode still lives in the installed SideTap's .env.

    Read for the two keys a fresh checkout cannot know (PHONE_PASSCODE,
    WDA_BUNDLE_ID) and nothing else, so posting keeps working until the human
    copies them into this repo's .env. Never a runtime dependency on SideTap's
    code: a missing install just means an empty dict. Set SIDETAP_ROOT to point
    at an install elsewhere; set VIDEO_DROP_NO_LEGACY_ENV=1 to switch it off.
    """
    if os.environ.get("VIDEO_DROP_NO_LEGACY_ENV") == "1":
        return {}
    roots = []
    configured = os.environ.get("SIDETAP_ROOT", "").strip()
    if configured:
        roots.append(Path(configured))
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    roots.append(local / "SideTap")
    roots.extend(sorted(local.glob("Packages/*/LocalCache/Local/SideTap")))
    for root in roots:
        env = root / ".env"
        if env.is_file():
            values = _load_env(env)
            return {key: values[key] for key in ("PHONE_PASSCODE", "WDA_BUNDLE_ID") if values.get(key)}
    return {}


_env = _load_env()
_legacy = {key: value for key, value in _legacy_sidetap_env().items() if not _env.get(key)}


def legacy_keys() -> list[str]:
    """Settings still being read from the installed SideTap's .env (setup check)."""
    return [key for key in ("PHONE_PASSCODE", "WDA_BUNDLE_ID")
            if _legacy.get(key) and not os.environ.get(key) and not _env.get(key)]


def get(key: str, default: str | None = None) -> str | None:
    """Read a setting: process env wins, then .env file, then the legacy fallback, then default."""
    return os.environ.get(key) or _env.get(key) or _legacy.get(key) or default


# ---- paths --------------------------------------------------------------------
# One owner for the phone link's files (pid files, logs, the shared WDA session
# id, the activity feed): the app's own state folder, beside link-status.json.
STATE_DIR = Path(os.environ.get("VIDEO_DROP_STATE", REPO_ROOT / ".state")).resolve() / "phone"

# go-ios binary. Empty = resolve it (PATH, npm global dir); see device.ios_path.
GO_IOS_PATH = get("GO_IOS_PATH")

# Unset = the first connected phone. PHONE_UDID is this app's name; SIDETAP_UDID
# is accepted so a copied SideTap .env keeps working.
SIDETAP_UDID = get("PHONE_UDID") or get("SIDETAP_UDID")

WDA_PORT = int(get("WDA_PORT", "8100") or "8100")
MJPEG_PORT = int(get("MJPEG_PORT", "9100") or "9100")

WDA_URL = f"http://127.0.0.1:{WDA_PORT}"


# Optional overrides
# Read by device.detect_wda_bundle; else auto-detected from installed apps.
WDA_BUNDLE_ID = get("WDA_BUNDLE_ID")
PHONE_PASSCODE = get("PHONE_PASSCODE")  # opt-in: lets helpers.unlock() type it

# Post-gesture waits, applied by whichever client creates the shared session.
# Measured on device: WDA's default animationCoolOffTimeout=2 made every swipe
# cost 2-5s under scroll momentum; at 0, idle waits of 0/1/2s all measure
# ~0.7s per swipe. idle=2 keeps settle protection on genuinely busy screens.
WDA_IDLE_WAIT = float(get("WDA_IDLE_WAIT", "2") or "2")
WDA_ANIM_COOLOFF = float(get("WDA_ANIM_COOLOFF", "0") or "0")

# Scripted finger contact for a tap, in ms. 80 is the shipped value and stays
# the default: the hold is pure wait (~20% of a bare tap budget), but a contact
# that is too brief can be DROPPED by iOS and a missed tap is worse than a slow
# one. Never on the passcode pad: a wrong or dropped tap there burns an iOS
# lockout attempt, which is why helpers._enter_passcode passes its own hold
# rather than riding this.
WDA_TAP_HOLD_MS = int(get("WDA_TAP_HOLD_MS", "80") or "80")

# Keystrokes per second WDA synthesises for POST /wda/keys. The Python side is
# ONE request either way; the phone spends len(text)/rate seconds, so this is
# the whole cost of a long paste. 60 is WDA's own default. Raising it fails
# QUIETLY: iOS drops synthesised keystrokes above some device-specific rate.
WDA_TYPING_FREQ = int(get("WDA_TYPING_FREQ", "60") or "60")

# MJPEG stream tuning (measured on device: WDA captures ~34fps max; 50% scale
# halves frame weight with no fps cost). The settings ride with the session,
# so the creator applies them even though this app has no live viewer.
MJPEG_FPS = int(get("MJPEG_FPS", "60") or "60")
MJPEG_QUALITY = int(get("MJPEG_QUALITY", "70") or "70")
MJPEG_SCALE = int(get("MJPEG_SCALE", "50") or "50")

# Accessibility snapshot timeout (seconds). Added upstream in WDA #1214
# (appium/WebDriverAgent#1214) to avoid indefinite hangs on apps with busy main
# event loops (e.g. TikTok video feeds). 0 disables the bound; default is 2.0s.
WDA_ACCESSIBILITY_DEADLINE = float(get("WDA_ACCESSIBILITY_DEADLINE", "2.0") or "2.0")
# WDA's snapshotMaxDepth (its default is 50). 0 leaves WDA's default alone. Lower
# values make /source cheaper on deep media UIs; experiment C in docs/link-root-cause.md.
WDA_SNAPSHOT_MAX_DEPTH = int(get("WDA_SNAPSHOT_MAX_DEPTH", "0") or "0")

# An RSD tunnel that go-ios did not create (pymobiledevice3's tunneld): every go-ios
# command that needs RSD gets --address/--rsd-port from here (device.pin_udid). The
# link supervisor sets these at runtime in LINK_TUNNEL_MODE=pmd3; read at call time.
GO_IOS_RSD_ADDRESS = get("GO_IOS_RSD_ADDRESS")
GO_IOS_RSD_PORT = get("GO_IOS_RSD_PORT")
