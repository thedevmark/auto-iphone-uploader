"""First-run setup checklist: what this PC and iPhone still need before posting.

Every probe is read-only. It never starts or stops the phone link, never taps
the iPhone, and never installs or downloads anything. Each item is ``ok``,
``action`` (the user can fix it now) or ``blocked`` (waiting on an earlier
item). Probes are injected so the checklist logic runs without a phone,
network, or real folders.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.request import urlopen

from .analyze import OLLAMA, TEXT_MODEL, VISION_MODEL
from .phone_space import GB

# A 2 GB video needs its download copy, its Photos copy and a margin.
COMFORTABLE_FREE = 5 * GB
APP_NAMES = {"onedrive": "OneDrive", "youtube": "YouTube", "edits": "Edits", "instagram": "Instagram",
             "facebook": "Facebook", "threads": "Threads", "tiktok": "TikTok"}
SOCIAL_APPS = ("youtube", "instagram", "facebook", "threads", "tiktok")
# What a local model adds; setup finishes without one.
LLM_ADDS = ("Recommended, not required: setup finishes without it. A local model drafts titles and captions "
            "from each video; without one, you write them yourself.")
# The first-run flow groups the rows into these screens, in this order. ``why`` is the one line
# the screen shows under its title; every row carries the ``step`` key it belongs to.
STEPS = (
    {"key": "connect", "title": "Connect your iPhone",
     "why": "A USB cable to a port on the PC itself. Keep the iPhone unlocked while you set up."},
    {"key": "control", "title": "Let your PC control it",
     "why": "A one-time signing with your Apple ID, then the passcode so posts can run while you are away."},
    {"key": "folder", "title": "Pick your video folder",
     "why": "A folder that syncs to the iPhone. Every finished export in it becomes a draft here."},
    {"key": "apps", "title": "Check your apps",
     "why": "One read of the iPhone's apps and your YouTube channel, so posts go to the right place."},
)
INSTALL_COMMAND = "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\install_windows.ps1"
# OneDrive videos open in the iPhone's OneDrive app; every other provider's open through Apple's
# Files app, where the provider's app appears as a location once it is installed and turned on.
PROVIDERS = "OneDrive, Google Drive, Dropbox or iCloud Drive"


def files_route_note(provider: str) -> str:
    return (f"The {provider} app must be installed and signed in on the iPhone, and turned on in Files "
            "(Files → Browse → More (…) → Edit).")
VISION_HINT = re.compile(r"vl\b|vision|llava|clip|mllama|moondream|minicpm-v|gemma3")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass(frozen=True)
class SetupProbes:
    driver: Callable[[], dict]  # {"importError": str, "goIos": str | None, "legacyKeys": [str]}
    ios_devices: Callable[[], dict]
    wda_status: Callable[[], dict | None]
    wda_bundle: Callable[[], str | None]
    ollama_tags: Callable[[], dict | None]
    ollama_installed: Callable[[], bool]
    cloud_folders: Callable[[], list[tuple[str, Path]]]
    watch: Callable[[], dict]
    inspection: Callable[[], dict]
    phone_space: Callable[[], dict | None]
    usb_power: Callable[[], dict | None] = lambda: None
    # {"expires": iso | None, "source": "phone" | "cache" | "", "error": str}; only asked once the phone is there.
    wda_signature: Callable[[], dict | None] = lambda: None
    # {"set": bool, "source": "env" | "dotenv" | "legacy" | "", "envPath": str}; never the value.
    passcode: Callable[[], dict] = lambda: {"set": False, "source": "", "envPath": ".env"}
    # {"installed": bool, "running": bool}, or None when Windows services cannot be read.
    apple_service: Callable[[], dict | None] = lambda: None
    # usb_helper.status(): {"supported", "installed", "script", "task", "version", "detail"}; None = unreadable.
    usb_helper: Callable[[], dict | None] = lambda: None
    # usb_path(): {"found": bool, "chain": [{"instanceId", "name", "class"}]}; None = unreadable.
    usb_path: Callable[[], dict | None] = lambda: None
    # link_probe.battery_snapshot(): IOPMPowerSource fields (CurrentCapacity, IsCharging, InstantAmperage,
    # Voltage, Temperature) or {"error": ...}; None = not asked (no phone).
    battery: Callable[[], dict | None] = lambda: None
    # ocr.probe(): {"available": bool, "language": str, "error": str}; None = unreadable.
    screen_text: Callable[[], dict | None] = lambda: None
    vision_model: str = VISION_MODEL
    text_model: str = TEXT_MODEL


def item(key: str, status: str, title: str, detail: str, fix: str = "", *,
         required: bool = True, step: str = "connect", steps: tuple[str, ...] | list[str] = (),
         commands: tuple[str, ...] | list[str] = (), run_in: str = "", commands_first: bool = True,
         more: str = "", gives: str = "", **extra: object) -> dict:
    """One checklist row, calm by construction. ``title`` is at most five words; ``detail`` says what is
    wrong in one line; ``fix`` is the one plain sentence a first-run screen shows (what to do, no
    jargon, no warnings). Everything technical sits behind "Show me how": ``more`` (why, consequences,
    alternatives), ``steps`` (numbered sentences) and ``commands`` (a console block, one per line,
    under the ``run_in`` label). ``gives`` is the one line an optional row says about what it adds.
    The UI never parses prose for any of it."""
    row = {"key": key, "status": status, "title": title, "detail": detail, "fix": fix,
           "required": required, "step": step, **extra}
    if more:
        row["more"] = more
    if gives:
        row["gives"] = gives
    if steps:
        row["steps"] = list(steps)
    if commands:
        row["commands"] = list(commands)
        row["runIn"] = run_in or IN_APP_FOLDER
        if steps:
            row["commandsFirst"] = commands_first  # False: the numbered steps lead up to the command
    return row


IN_APP_FOLDER = "PowerShell, in the app folder"
AS_ADMIN = "PowerShell as administrator"


LEGACY_ENV_NOTE = ("still read from SideTap's .env; copy {keys} into this app's .env to finish the move away from "
                   "SideTap")


def driver_item(driver: dict) -> dict:
    """The app's own phone driver (video_drop/phone): it must import, and go-ios must be on this PC."""
    title = "App files in place"
    error = str(driver.get("importError") or "")
    if error:
        return item("driver", "action", title, f"Part of the app could not load: {error[:160]}",
                    "Run the install command once more.", commands=[INSTALL_COMMAND],
                    more="It reinstalls the app's packages; finished steps are skipped.")
    go_ios = driver.get("goIos")
    if not go_ios:
        return item("driver", "action", title, "The iPhone connector is missing from the app folder.",
                    "Run the install command once more.", commands=[INSTALL_COMMAND],
                    more="It downloads the connector and checks it. Or set GO_IOS_PATH in .env to an ios.exe you "
                         "already have.")
    detail = f"Installed · iPhone connector at {go_ios}"
    legacy = [str(key) for key in driver.get("legacyKeys") or []]
    if legacy:
        detail += " · " + LEGACY_ENV_NOTE.format(keys=", ".join(legacy))
    return item("driver", "ok", title, detail)


# Supported: Face ID iPhones (no Home button: Control Center opens from the top-right corner)
# on iOS 17.4 or later (go-ios's userspace tunnel). Tested live on iPhone 16 Pro Max, iOS 26.7.
MIN_IOS = (17, 4)
HOME_BUTTON_MODELS = {"iPhone10,1", "iPhone10,2", "iPhone10,4", "iPhone10,5",  # iPhone 8, 8 Plus
                      "iPhone12,8", "iPhone14,6"}  # iPhone SE (2nd, 3rd generation)


def unsupported_reason(model: str, ios: str) -> str:
    """Why this iPhone is outside what the app supports, or "" when it is supported or unknown."""
    match = re.fullmatch(r"iPhone(\d+),\d+", model or "")
    if match and (int(match[1]) < 10 or model in HOME_BUTTON_MODELS):
        return "This iPhone has a Home button; the app supports Face ID iPhones (iPhone X and newer)"
    parts = re.findall(r"\d+", ios or "")
    if len(parts) >= 2 and (int(parts[0]), int(parts[1])) < MIN_IOS:
        return f"This iPhone runs iOS {ios}; the app needs iOS 17.4 or newer"
    if len(parts) == 1 and (int(parts[0]), 0) < MIN_IOS:
        return f"This iPhone runs iOS {ios}; the app needs iOS 17.4 or newer"
    return ""


def phone_item(ios: dict, driver_ok: bool) -> dict:
    title = "iPhone connected"
    if not ios.get("found"):
        return item("phone", "blocked", title, "Waiting for the app files.", "Finish the app files step first.")
    error = str(ios.get("error", ""))
    if error:
        if re.search(r"27015|usbmux", error, re.IGNORECASE):
            return item("phone", "action", title, "Windows cannot see iPhones yet.",
                        "Install Apple Devices from the Microsoft Store, then plug the iPhone in again.")
        return item("phone", "action", title, f"The iPhone check failed: {error[:160]}",
                    "Unplug the iPhone, plug it back in and unlock it.")
    count = int(ios.get("count", 0))
    if count == 0:
        return item("phone", "action", title, "No iPhone found over USB.",
                    "Plug the iPhone in with a USB cable, unlock it, and tap Trust if it asks.")
    if count > 1:
        return item("phone", "action", title, f"{count} iPhones are connected.",
                    "Unplug the others so only the iPhone you post from stays connected.")
    reason = unsupported_reason(str(ios.get("model", "")), str(ios.get("ios", "")))
    if reason:
        return item("phone", "action", title, reason + ".",
                    "Use a Face ID iPhone on iOS 17.4 or newer (Settings > General > Software Update).")
    return item("phone", "ok", title, "One iPhone is connected over USB.")


def link_item(wda: dict | None, phone_ok: bool, wda_bundle: str | None = "") -> dict:
    """``wda_bundle`` is the WebDriverAgent runner found on the phone (None = not installed)."""
    title = "PC controls the iPhone"
    if not phone_ok:
        return item("link", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.", step="control")
    value = wda.get("value") if isinstance(wda, dict) else None
    if not isinstance(value, dict) or value.get("ready") is False:
        if wda_bundle is None:
            return item("link", "blocked", title, "Waiting for the one-time signing.",
                        "It answers once the signing is done.", step="control")
        return item("link", "action", title, "The iPhone is connected, but it is not answering taps yet.",
                    "Unlock the iPhone and tap Trust if it asks.", step="control",
                    more="The app starts the control app by itself. If it stays quiet, unplug and replug the iPhone.")
    version = value.get("os", {}).get("version", "") if isinstance(value.get("os"), dict) else ""
    return item("link", "ok", title, f"Answering taps{' on iOS ' + version if version else ''}.", step="control")


def inventory_item(inspection: dict, link_ok: bool) -> dict:
    title = "Apps on the iPhone"
    status = inspection.get("status", "idle")
    if status == "ready" and isinstance(inspection.get("screenPoints"), dict):
        screen = inspection["screenPoints"]
        installed = [name for name in inspection.get("installed", []) if name in APP_NAMES]
        missing = [APP_NAMES[name] for name in SOCIAL_APPS if name not in installed]
        detail = (f"Screen {float(screen.get('width', 0)):g} × {float(screen.get('height', 0)):g} points · "
                  f"apps: {', '.join(APP_NAMES[name] for name in installed) or 'none found'}")
        if missing:
            detail += f" · not installed: {', '.join(missing)}"
        if not any(name in installed for name in SOCIAL_APPS):
            return item("inventory", "action", title, detail,
                        "Install the apps you post to from the App Store.", action="inspect", step="apps")
        return item("inventory", "ok", title, detail, action="inspect", step="apps")
    if status == "inspecting":
        return item("inventory", "blocked", title, "Reading the iPhone now…",
                    "Leave the iPhone alone until the check finishes.", step="apps")
    if not link_ok:
        return item("inventory", "blocked", title, "Not read yet.", "Let your PC control the iPhone first.",
                    step="apps")
    error = str(inspection.get("error", "")).strip()
    return item("inventory", "action", title,
                f"The last read did not finish: {error[:160]}" if error else "Not read yet.",
                "Let the app read which apps are on the iPhone.", action="inspect", step="apps",
                more="Check phone opens YouTube on the iPhone to read the screen size and channel. Leave the phone "
                     "alone while it runs.")


def space_item(space: dict | None) -> dict:
    title = "iPhone free space"
    free = space.get("freeBytes") if isinstance(space, dict) else None
    if not isinstance(free, int):
        return item("space", "blocked", title, "Not measured yet.",
                    "Nothing to do now. The app measures free space before every upload.", required=False,
                    step="apps", gives=SPACE_GIVES)
    detail = f"{free / GB:.1f} GB free"
    if space.get("checkedAt"):
        detail += f" when last measured ({str(space['checkedAt'])[:10]})"
    if free < COMFORTABLE_FREE:
        return item("space", "action", title, detail, "Free up some space on the iPhone.", required=False,
                    step="apps", gives=SPACE_GIVES,
                    more=f"Keep at least {COMFORTABLE_FREE / GB:.0f} GB free so a video and its Photos copy both fit.")
    return item("space", "ok", title, detail, required=False, step="apps", gives=SPACE_GIVES)


SPACE_GIVES = "Room on the iPhone for the next video."


USB_SUSPEND_FIX = "Keep the iPhone's USB port powered while it uploads."
USB_SUSPEND_MORE = ("Windows is allowed to switch USB ports off to save power, which can interrupt a long upload; "
                    "this turns that off for the port the iPhone uses.")
USB_SUSPEND_COMMANDS = ("powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 "
                        "48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0",
                        "powercfg /setactive SCHEME_CURRENT")
USB_SUSPEND_STEPS = ("Open Device Manager and expand Universal Serial Bus controllers.",
                     "Open each USB Root Hub, then its Power Management tab.",
                     "Untick \"Allow the computer to turn off this device to save power\".")


def usb_power_item(power: dict | None) -> dict:
    """Windows USB power saving drops a busy iPhone off the bus (Kernel-PnP 1010, 2026-09-30)."""
    title = "USB power saving off"
    if not isinstance(power, dict):
        return item("usbPower", "blocked", title, "Could not read Windows power settings.", USB_SUSPEND_FIX,
                    commands=USB_SUSPEND_COMMANDS, run_in=AS_ADMIN, steps=USB_SUSPEND_STEPS, more=USB_SUSPEND_MORE)
    problems = []
    if power.get("selectiveSuspend"):
        problems.append("USB selective suspend is on")
    hubs = int(power.get("hubsAllowedOff") or 0)
    if hubs:
        problems.append(f"{hubs} USB hub{'s' if hubs != 1 else ''} may be switched off to save power")
    if problems:
        return item("usbPower", "action", title, "; ".join(problems) + ".", USB_SUSPEND_FIX,
                    commands=USB_SUSPEND_COMMANDS, run_in=AS_ADMIN, steps=USB_SUSPEND_STEPS, more=USB_SUSPEND_MORE)
    return item("usbPower", "ok", title, "Windows keeps the iPhone's USB port powered.")


# WebDriverAgent is signed by the user with their own Apple ID; a free ID's signature lasts 7 days.
SIGNING_FIX = "Sign it with your Apple ID, once."
SIGNING_MORE = ("Apple requires the control app to be signed with your own Apple ID (free, about five minutes). "
                "A free Apple ID's signing lasts 7 days; the same command renews it.")
SIDELOADLY_STEPS = (
    "Install Sideloadly from sideloadly.io.",
    "Plug in the iPhone and unlock it.",
    "In Sideloadly, drag wda\\WebDriverAgent.ipa from the app folder onto the window, pick the iPhone, type your "
    "Apple ID and click Start. Apple asks for your password and a code inside Sideloadly, never in this app.",
    "On the iPhone: Settings > General > VPN & Device Management > tap your Apple ID > Trust.",
    "Run the command below; it finishes the signing so taps work.")
RESIGN_FIX = "Sign it again with your Apple ID."
RESIGN_MORE = ("Run the command, and click Start in Sideloadly when it asks (wda\\WebDriverAgent.ipa, your iPhone, "
               "your Apple ID).")
RESIGN_COMMAND = "python scripts\\phone_resign.py"
RESIGN_IN = "PowerShell, in the app folder, once Sideloadly has finished"
RESIGN_SOON_DAYS = 2


def _parse_expiry(value: object) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        try:
            stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def signature_item(signature: dict | None, phone_ok: bool, installed: bool, now: datetime | None = None) -> dict:
    """Days left on the WebDriverAgent signature, read off the phone; never a guess from a local file alone."""
    title = "Sign the control app"
    if not phone_ok:
        return item("signature", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.",
                    step="control")
    if not installed:
        return item("signature", "action", title, "The control app is not on the iPhone yet.", SIGNING_FIX,
                    step="control", steps=SIDELOADLY_STEPS, commands=[RESIGN_COMMAND], run_in=RESIGN_IN,
                    commands_first=False, more=SIGNING_MORE)
    signature = signature if isinstance(signature, dict) else {}
    expires = _parse_expiry(signature.get("expires"))
    if expires is None:
        error = str(signature.get("error") or "").strip()
        why = ("install the re-sign tool with pip install -r requirements-resign.txt so the app can read the "
               "signing date off the phone" if "pymobiledevice3" in error else
               (error[:160] if error else "the phone did not hand over its signing profile"))
        return item("signature", "ok", title, f"Signed · days left unknown: {why}.", step="control")
    now = now or datetime.now(timezone.utc)
    left = (expires - now).total_seconds() / 86400
    when = expires.astimezone().strftime("%Y-%m-%d %H:%M")
    if left <= 0:
        return item("signature", "action", title, f"The signing ran out on {when}.", RESIGN_FIX,
                    step="control", commands=[RESIGN_COMMAND], run_in=RESIGN_IN, more=RESIGN_MORE)
    if left < 1:
        return item("signature", "action", title, f"The signing runs out today ({when}).", RESIGN_FIX, step="control",
                    commands=[RESIGN_COMMAND], run_in=RESIGN_IN, more=RESIGN_MORE)
    days = int(left)
    detail = f"Signed · {days} day{'s' if days != 1 else ''} left (until {when})"
    if left <= RESIGN_SOON_DAYS:
        return item("signature", "ok", title, detail + " · re-sign soon", RESIGN_FIX, step="control",
                    commands=[RESIGN_COMMAND], run_in=RESIGN_IN, more=RESIGN_MORE)
    return item("signature", "ok", title, detail, step="control")


def passcode_item(passcode: dict | None) -> dict:
    """Whether PHONE_PASSCODE is saved so the app can unlock the phone; the value itself is never read here."""
    title = "Passcode saved"
    passcode = passcode if isinstance(passcode, dict) else {}
    env_path = str(passcode.get("envPath") or ".env")
    if not passcode.get("set"):
        return item("passcode", "action", title, "No passcode is saved yet.",
                    "Save your iPhone passcode so posts can run while you're away.", step="control",
                    commands=["python scripts\\set_passcode.py"],
                    more=f"You type it at a hidden prompt; it is saved to {env_path}, typed only on the lock screen, "
                         "and never shown or logged.")
    if passcode.get("source") == "legacy":
        return item("passcode", "ok", title,
                    "Read from the installed SideTap's .env for now · " + LEGACY_ENV_NOTE.format(keys="PHONE_PASSCODE")
                    + f" ({env_path}).", step="control")
    return item("passcode", "ok", title, f"Saved in {env_path} (never shown).", step="control")


def apple_service_item(service: dict | None) -> dict:
    """Apple Mobile Device Service is the Windows USB driver go-ios talks to (usbmuxd on port 27015)."""
    title = "Apple's iPhone driver"
    if not isinstance(service, dict):
        return item("appleService", "blocked", title, "Could not read Windows services.",
                    "Open Services (services.msc) and look for Apple Mobile Device Service; it must be Running.")
    if not service.get("installed"):
        return item("appleService", "action", title, "Windows has no Apple driver for iPhones yet.",
                    "Install Apple Devices from the Microsoft Store.",
                    more="It is the driver Windows needs to see an iPhone.",
                    steps=("Install Apple Devices from the Microsoft Store (or iTunes from apple.com).",
                           "Plug the iPhone in again, unlock it and tap Trust."))
    if not service.get("running"):
        return item("appleService", "action", title, "Apple Mobile Device Service is installed but not running.",
                    "Start Apple Mobile Device Service in Windows Services.",
                    more="It is the driver Windows needs to see an iPhone.",
                    steps=("Open Services (services.msc).",
                           "Start Apple Mobile Device Service and set its Startup type to Automatic.",
                           "If it will not start, reinstall Apple Devices from the Microsoft Store."))
    return item("appleService", "ok", title, "Apple Mobile Device Service is running.")


USB_HELPER_FIX = "Install it once with the command below."
USB_HELPER_GIVES = "A stalled USB connection fixes itself instead of asking you to replug."
USB_HELPER_MORE = ("The helper can do exactly two things: restart Apple's iPhone driver and reset the iPhone's USB "
                   "port. The app itself never runs with administrator rights (docs/usb-recovery-helper.md).")
USB_HELPER_COMMAND = f"{INSTALL_COMMAND} -InstallUsbHelper"
USB_HELPER_IN = "PowerShell, in the app folder; accept the administrator prompt once"


def usb_helper_item(helper: dict | None) -> dict:
    """The elevated USB recovery helper: without it a stalled USB link ends in 'unplug and replug'.

    Recommended, not required: posting works without it, and it asks for one administrator prompt.
    """
    title = "USB recovery helper"
    if not isinstance(helper, dict):
        return item("usbHelper", "blocked", title, "Could not read the helper's state.", USB_HELPER_FIX, required=False,
                    commands=[USB_HELPER_COMMAND], run_in=USB_HELPER_IN, more=USB_HELPER_MORE, gives=USB_HELPER_GIVES)
    if not helper.get("supported", True):
        return item("usbHelper", "ok", title, "Not needed on this system (Windows only).", required=False,
                    gives=USB_HELPER_GIVES)
    if helper.get("installed"):
        stale = str(helper.get("detail") or "")
        if "version" in stale:
            return item("usbHelper", "action", title, f"Installed, but {stale}.",
                        "Run the install command again to update it.", required=False,
                        commands=[USB_HELPER_COMMAND], run_in=USB_HELPER_IN, more=USB_HELPER_MORE, gives=USB_HELPER_GIVES)
        return item("usbHelper", "ok", title,
                    "Installed. When the iPhone's USB connection stalls, the app restarts Apple's driver and resets "
                    "the USB port by itself before ever asking for a replug.", required=False, gives=USB_HELPER_GIVES)
    partial = str(helper.get("detail") or "")
    if helper.get("script") or helper.get("task"):
        return item("usbHelper", "action", title, f"Installed incompletely ({partial}).", USB_HELPER_FIX, required=False,
                    commands=[USB_HELPER_COMMAND], run_in=USB_HELPER_IN, more=USB_HELPER_MORE, gives=USB_HELPER_GIVES)
    return item("usbHelper", "action", title,
                "Not installed: a stalled USB connection can only be fixed by unplugging and replugging the iPhone "
                "by hand.", USB_HELPER_FIX, required=False, commands=[USB_HELPER_COMMAND], run_in=USB_HELPER_IN,
                more=USB_HELPER_MORE, gives=USB_HELPER_GIVES)


# Host controllers by PCI vendor/device id: what the phone's USB path ends in and what that means.
# DEV_43D5 is the AMD 500-series chipset USB 3.1 controller with the documented dropout bug
# (AGESA 1.2.0.2 era; docs/link-literature.md U1); the CPU's own controller is the usual fix.
USB_CONTROLLERS = {
    ("1022", "43D5"): ("AMD 500-series chipset USB controller", "warn"),
    ("1022", "43EE"): ("AMD 600-series chipset USB controller", "warn"),
    ("1022", "149C"): ("AMD CPU USB controller", "ok"),
    ("1022", "15E0"): ("AMD CPU USB controller", "ok"),
    ("1022", "15E1"): ("AMD CPU USB controller", "ok"),
    ("1022", "1639"): ("AMD CPU USB controller", "ok"),
    ("1022", "161F"): ("AMD CPU USB controller", "ok"),
    ("8086", None): ("Intel USB controller", "ok"),
}
USB_HUB_VENDORS = {"2109": "VIA", "0BDA": "Realtek", "05E3": "Genesys", "1A40": "Terminus", "0424": "Microchip",
                   "2188": "CalDigit", "8087": "Intel"}
PCI_ID = re.compile(r"PCI\\VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", re.IGNORECASE)
USB_ID = re.compile(r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.IGNORECASE)
USB_PATH_FIX = "Plug the iPhone straight into a rear port on the PC, with no hub or dock."
USB_PATH_MORE = ("Use a port on the PC's own (CPU) USB controller rather than a front-panel port or a dock, with the "
                 "cable that came with the phone or a USB-A-to-C cable; this row then reads 'CPU USB controller, "
                 "no hub'.")
USB_PATH_GIVES = "Fewer dropped connections during long uploads."
USB_PATH_TITLE = "iPhone on a direct port"


def describe_usb_path(path: dict) -> dict:
    """Pure: the phone's chain (device -> hubs -> root hub -> controller) as names plus a verdict."""
    chain = [node for node in path.get("chain", []) if isinstance(node, dict)]
    hubs, controller, controller_id, verdict = [], "", "", "unknown"
    for node in chain[1:]:
        instance = str(node.get("instanceId") or "")
        pci = PCI_ID.search(instance)
        if pci:
            vendor, device = pci.group(1).upper(), pci.group(2).upper()
            known = USB_CONTROLLERS.get((vendor, device)) or USB_CONTROLLERS.get((vendor, None))
            controller = f"{known[0] if known else (str(node.get('name') or 'USB controller'))} (DEV_{device})"
            controller_id = device
            verdict = known[1] if known else "unknown"
            break
        usb = USB_ID.search(instance)
        if usb and "ROOT_HUB" not in instance.upper():
            vendor = usb.group(1).upper()
            hubs.append(f"{USB_HUB_VENDORS.get(vendor, 'USB')} hub (VID_{vendor})")
    return {"hubs": hubs, "controller": controller, "controllerId": controller_id, "verdict": verdict}


def usb_path_item(path: dict | None, phone_ok: bool) -> dict:
    """Which controller and hubs the iPhone hangs off: the chipset controller and any hub are known drop causes."""
    title = USB_PATH_TITLE
    if not phone_ok:
        return item("usbPath", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.", required=False, gives=USB_PATH_GIVES, more=USB_PATH_MORE)
    if not isinstance(path, dict):
        return item("usbPath", "blocked", title, "Could not read the phone's USB path from Windows.", USB_PATH_FIX,
                    required=False, gives=USB_PATH_GIVES, more=USB_PATH_MORE)
    if not path.get("found"):
        return item("usbPath", "blocked", title, "Windows does not list an iPhone USB device right now.",
                    "Reconnect the iPhone.", required=False, gives=USB_PATH_GIVES, more=USB_PATH_MORE)
    described = describe_usb_path(path)
    hops = ["iPhone", *described["hubs"], "root hub", described["controller"] or "unknown controller"]
    detail = " -> ".join(hops)
    problems = []
    if described["verdict"] == "warn":
        problems.append("this chipset controller is known to drop busy USB devices")
    if described["hubs"]:
        count = len(described["hubs"])
        problems.append(f"the phone goes through {count} hub{'s' if count != 1 else ''}")
    if problems:
        return item("usbPath", "action", title, f"{detail} · {'; '.join(problems)}.", USB_PATH_FIX, required=False,
                    controller=described["controllerId"], hubs=len(described["hubs"]), gives=USB_PATH_GIVES, more=USB_PATH_MORE)
    note = "CPU USB controller, no hub" if described["verdict"] == "ok" else "no hub; controller not in the known list"
    return item("usbPath", "ok", title, f"{detail} · {note}.", required=False, controller=described["controllerId"],
                hubs=0, gives=USB_PATH_GIVES, more=USB_PATH_MORE)


def vision_capable(model: dict) -> bool:
    details = model.get("details") if isinstance(model.get("details"), dict) else {}
    families = details.get("families") if isinstance(details.get("families"), list) else []
    text = " ".join([str(model.get("name", "")), str(details.get("family", "")), *map(str, families)]).casefold()
    return bool(VISION_HINT.search(text))


def llm_item(tags: dict | None, installed: bool, vision_model: str, text_model: str) -> dict:
    """Local AI is recommended, never required: its absence never holds setup back."""
    title = "Local AI (Ollama)"
    def entry(status: str, detail: str, fix: str = "", **extra: object) -> dict:
        if status != "ok":
            extra["more"] = f"{LLM_ADDS} {extra.get('more', '')}".strip()
        return item("llm", status, title, detail, fix, required=False, recommended=True, step="apps",
                    gives="Titles and captions drafted from each video.", **extra)

    if not isinstance(tags, dict):
        if installed:
            return entry("action", "Ollama is installed but not running.", "Open Ollama from the Start menu.")
        return entry("action", "No local AI was found on this PC.", "Install Ollama and pull the two models.",
                     commands=["winget install Ollama.Ollama", f"ollama pull {vision_model}",
                               f"ollama pull {text_model}"], run_in="PowerShell",
                     more="Download Ollama from ollama.com if winget is not available.")
    listed = [model for model in tags.get("models", []) if isinstance(model, dict) and model.get("name")]
    names = {str(model["name"]) for model in listed}
    names |= {name.removesuffix(":latest") for name in names}
    models = [{"name": str(model["name"]), "vision": str(model["name"]) == vision_model or vision_capable(model)}
              for model in listed]
    summary = ", ".join(model["name"] + (" (vision)" if model["vision"] else "") for model in models)
    missing = [name for name in (vision_model, text_model) if name not in names]
    if missing:
        return entry("action",
                     f"Ollama is running. Installed models: {summary}." if models else "Ollama is running with no models.",
                     "Pull what is missing.", models=models,
                     commands=["ollama pull " + name for name in missing], run_in="PowerShell")
    return entry("ok", f"Ollama is running · vision: {vision_model} · text: {text_model}", models=models)


def cloud_folders(env: Mapping[str, str], home: Path, drives: list[str], is_dir: Callable[[Path], bool],
                  read_text: Callable[[Path], str]) -> list[tuple[str, Path]]:
    """Local sync folders for the cloud apps the iPhone can open through Files."""
    found: list[tuple[str, Path]] = []

    def add(provider: str, path: Path) -> None:
        if is_dir(path) and all(os.path.normcase(str(path)) != os.path.normcase(str(known)) for _, known in found):
            found.append((provider, path))

    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if env.get(key):
            add("OneDrive", Path(env[key]))
    for drive in drives:
        add("Google Drive", Path(drive) / "My Drive")
    add("Google Drive", home / "Google Drive")
    add("Google Drive", home / "My Drive")
    for base in (env.get("APPDATA"), env.get("LOCALAPPDATA")):
        if not base:
            continue
        try:
            accounts = json.loads(read_text(Path(base) / "Dropbox" / "info.json"))
        except (OSError, ValueError):
            continue
        for account in accounts.values() if isinstance(accounts, dict) else []:
            if isinstance(account, dict) and isinstance(account.get("path"), str):
                add("Dropbox", Path(account["path"]))
    add("Dropbox", home / "Dropbox")
    add("iCloud Drive", home / "iCloudDrive")
    return found


def detected_cloud_folders() -> list[tuple[str, Path]]:
    """This PC's cloud sync folders, read the same way as the setup checklist's probe."""
    return cloud_folders(os.environ, Path.home(), local_drives(), Path.is_dir,
                         lambda path: path.read_text(encoding="utf-8"))


def _real(path: Path) -> str:
    # Resolve junctions and 8.3 short names (RUNNER~1 vs the long form) wherever the path exists.
    try:
        path = Path(path).resolve(strict=False)
    except OSError:
        pass
    return os.path.normcase(os.path.normpath(str(path)))


def inside(folder: Path, root: Path) -> bool:
    folder_text = _real(folder)
    root_text = _real(root).rstrip("\\/")
    return folder_text == root_text or folder_text.startswith(root_text + os.sep)


def cloud_for(folder: Path, clouds: list[tuple[str, Path]]) -> tuple[str, Path] | None:
    containing = [(provider, root) for provider, root in clouds if inside(folder, root)]
    return max(containing, key=lambda cloud: len(str(cloud[1])), default=None)


def folder_item(watch: dict, clouds: list[tuple[str, Path]]) -> dict:
    """The export folder must sit inside a cloud folder the iPhone can open videos from.

    OneDrive videos open in the OneDrive app; Google Drive, Dropbox and iCloud Drive videos open
    through the Files app. The most specific provider owns a nested folder, as posting routes it.
    """
    title = "Video folder"
    available = "; ".join(f"{provider} ({root})" for provider, root in clouds)
    pick_inside = (f"Pick one inside {PROVIDERS} so the iPhone can open new videos." if clouds else
                   f"No {PROVIDERS} folder was found on this PC. Install one of them, sign in on this PC and the "
                   "iPhone, let it sync, then choose a folder inside it.")
    path = str(watch.get("path") or "")
    if not path:
        return item("folder", "action", title, "No folder chosen yet.", "Choose the folder your editor exports to.",
                    action="folder", step="folder",
                    more=f"{pick_inside}{' Cloud folders on this PC: ' + available + '.' if clouds else ''}")
    cloud = cloud_for(Path(path), clouds)
    provider = cloud[0] if cloud else ""
    if watch.get("problem"):
        return item("folder", "action", title, f"{path} · {watch['problem']}",
                    str(watch.get("fix") or "Choose the export folder again."), action="folder", provider=provider,
                    step="folder")
    if not cloud:
        return item("folder", "action", title, f"{path} is not inside a cloud folder the iPhone can reach.",
                    "Choose a different folder.", action="folder", provider="", step="folder", more=pick_inside)
    if provider == "OneDrive":
        route, note = "the iPhone opens videos in the OneDrive app", ""
    else:
        route, note = "the iPhone opens videos through the Files app", " " + files_route_note(provider)
    if not watch.get("enabled"):
        return item("folder", "action", title, f"{path} · inside {provider} · Watch folder is off.{note}",
                    "Turn on Watch folder so new exports are picked up by themselves.", action="watch",
                    provider=provider, step="folder")
    return item("folder", "ok", title, f"Watching {path} · inside {provider} · {route}.{note}",
                action="folder", provider=provider, step="folder")


CHARGING_FIX = "Charge the iPhone on a wall charger before posting, and use a USB port that can power it."
CHARGING_MORE = ("A rear port on the CPU's own controller, a powered hub or a USB-C PD port can power it under load. "
                 "Keep it above 20%.")
CHARGING_GIVES = "The iPhone stays charged through long uploads."


def charging_item(battery: dict | None, phone_ok: bool) -> dict:
    """Is the port actually charging the phone? On 2026-09-30 the chipset port read IsCharging while the
    phone drained at 2.4 A under video load at 1-2% capacity; every link stall that day happened with the
    battery low. One sample here: a negative current while 'charging' or a low capacity is the warning."""
    title = "iPhone charging"
    if not phone_ok:
        return item("charging", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.", required=False, gives=CHARGING_GIVES, more=CHARGING_MORE)
    if not isinstance(battery, dict) or "error" in battery or "CurrentCapacity" not in battery:
        return item("charging", "blocked", title, "Could not read the phone's battery over USB.", CHARGING_FIX,
                    required=False, gives=CHARGING_GIVES, more=CHARGING_MORE)
    capacity = int(battery.get("CurrentCapacity") or 0)
    current = battery.get("InstantAmperage")
    charging = bool(battery.get("IsCharging"))
    current_text = f"{current:+d} mA" if isinstance(current, (int, float)) else "current unknown"
    detail = f"{capacity}% · {'charging' if charging else 'not charging'} · {current_text}."
    problems = []
    if not charging:
        problems.append("the phone is not charging on this port")
    elif isinstance(current, (int, float)) and current < 0:
        problems.append(f"it is draining at {abs(int(current))} mA while plugged in (the port cannot power it)")
    if capacity < 20:
        problems.append(f"battery at {capacity}%")
    if problems:
        joined = "; ".join(problems)
        return item("charging", "action", title, detail + " " + joined[0].upper() + joined[1:] + ".",
                    CHARGING_FIX, required=False, capacity=capacity, current=current, gives=CHARGING_GIVES, more=CHARGING_MORE)
    return item("charging", "ok", title, detail, required=False, capacity=capacity, current=current, gives=CHARGING_GIVES, more=CHARGING_MORE)


OCR_FIX = "Add English (United States) with its text reader in Windows Settings."
OCR_MORE = "The YouTube upload screen is read from a screenshot, so Windows needs its English text reader."
OCR_STEPS = ("Open Settings > Time & language > Language & region.",
             "Add English (United States) if it is not listed.",
             "Open its Language options and install the Optical character recognition feature.")


def screen_text_item(screen_text: dict | None) -> dict:
    """Windows' built-in OCR engine: YouTube 21.38 hides its Description, Paid promotion and
    "AI use, Tags" rows from accessibility, so the YouTube flow reads them from a screenshot."""
    title = "Windows reads screen text"
    if not isinstance(screen_text, dict):
        return item("screenText", "blocked", title, "Could not ask Windows for its text reader.", OCR_FIX, step="control",
                    steps=OCR_STEPS, more=OCR_MORE)
    if not screen_text.get("available"):
        detail = "Windows has no English text reader yet."
        if screen_text.get("error"):
            detail += f" ({screen_text['error']})"
        return item("screenText", "action", title, detail, OCR_FIX, step="control", steps=OCR_STEPS, more=OCR_MORE)
    language = str(screen_text.get("language") or "")
    if not language.casefold().startswith("en"):
        return item("screenText", "action", title,
                    f"Windows reads screen text in {language} only; the apps are matched by their English labels.",
                    OCR_FIX, step="control", steps=OCR_STEPS, more=OCR_MORE)
    return item("screenText", "ok", title, f"Offline, {language}.", step="control")


def checklist(probes: SetupProbes) -> dict:
    driver = driver_item(probes.driver())
    phone = phone_item(probes.ios_devices(), driver["status"] == "ok")
    # The WDA-on-phone probe lists the phone's apps, so it only runs once the phone is there and WDA is silent.
    wda = probes.wda_status()
    phone_ok = phone["status"] == "ok"
    bundle = probes.wda_bundle() if phone_ok and not wda_ready(wda) else ""
    link = link_item(wda, phone_ok, bundle)
    # An answering WDA proves the runner is installed; a silent one is looked up in the phone's app list.
    installed = phone_ok and (wda_ready(wda) or bundle is not None)
    # Rows in first-run order: each step's required rows first, then what is recommended for it.
    items = [apple_service_item(probes.apple_service()),
             driver, phone,
             usb_power_item(probes.usb_power()),
             usb_path_item(probes.usb_path() if phone_ok else None, phone_ok),
             charging_item(probes.battery() if phone_ok else None, phone_ok),
             usb_helper_item(probes.usb_helper()),
             signature_item(probes.wda_signature() if installed else None, phone_ok, installed),
             link,
             passcode_item(probes.passcode()),
             screen_text_item(probes.screen_text()),
             folder_item(probes.watch(), probes.cloud_folders()),
             inventory_item(probes.inspection(), link["status"] == "ok"),
             space_item(probes.phone_space()),
             llm_item(probes.ollama_tags(), probes.ollama_installed(), probes.vision_model, probes.text_model)]
    return {"items": items, "steps": step_summary(items),
            "ready": all(entry["status"] == "ok" for entry in items if entry["required"])}


def step_summary(items: list[dict]) -> list[dict]:
    """One entry per first-run screen: what it is for, how many required rows it has and whether they all pass."""
    summary = []
    for step in STEPS:
        mine = [entry for entry in items if entry["step"] == step["key"]]
        required = [entry for entry in mine if entry["required"]]
        summary.append({**step, "required": len(required),
                        "done": sum(entry["status"] == "ok" for entry in required),
                        "ready": all(entry["status"] == "ok" for entry in required)})
    return summary


def wda_ready(wda: dict | None) -> bool:
    value = wda.get("value") if isinstance(wda, dict) else None
    return isinstance(value, dict) and value.get("ready") is not False


# Real, read-only probes for this PC.

def driver_import_error() -> str:
    """Import the vendored driver in a child process so a missing dependency cannot affect this server."""
    code = "import video_drop.phone.device, video_drop.phone.helpers"
    try:
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                                   creationflags=NO_WINDOW, cwd=str(Path(__file__).resolve().parent.parent))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if completed.returncode:
        return (completed.stderr.strip().splitlines() or ["import failed"])[-1]
    return ""


def driver_probe() -> dict:
    """The phone driver as this PC sees it: imports, go-ios path, settings still borrowed from SideTap."""
    error = driver_import_error()
    if error:
        return {"importError": error, "goIos": None, "legacyKeys": []}
    from .phone import config, device

    try:
        go_ios = device.ios_path()
    except device.DeviceError as exc:
        return {"importError": str(exc), "goIos": None, "legacyKeys": []}
    return {"importError": "", "goIos": go_ios, "legacyKeys": config.legacy_keys()}


def go_ios() -> str | None:
    try:
        from .phone import device

        return device.ios_path()
    except Exception:
        return None


def wda_bundle() -> str | None:
    """The WebDriverAgent runner installed on the phone, read-only (`ios apps --list`)."""
    try:
        from .phone import device

        return device.detect_wda_bundle()
    except Exception:
        return None


def wda_signature() -> dict:
    """The WebDriverAgent signing profile's expiry, read off the phone (misagent, in a child process).

    Sideloadly signs in memory and writes no profile to this PC, so the phone is
    the only place the date exists; the app's own copy (``signing.PROFILE_PATH``)
    is a fallback from its last re-sign. Read-only. Never taps the phone.
    """
    result = {"expires": None, "source": "", "error": ""}
    try:
        from .phone import device, signing
    except Exception as exc:  # the driver item already explains an import failure
        result["error"] = str(exc)
        return result
    try:
        udid = device.current_udid()
    except Exception:
        udid = None
    profiles: list[bytes] = []
    source = "phone"
    try:
        profiles = signing._device_profiles(udid)
    except signing.SigningError as exc:
        result["error"] = str(exc)
    if not profiles:
        try:
            profiles = [signing.PROFILE_PATH.read_bytes()]
            source = "cache"
        except OSError:
            return result
    best: datetime | None = None
    for data in profiles:
        try:
            info = signing.parse_profile(data)
        except signing.SigningError:
            continue
        if signing._WDA_MARKER not in str(info.get("app_id", "")).lower():
            continue
        expires = _parse_expiry(info.get("expires"))
        if expires and (best is None or expires > best):
            best = expires
    if best is not None:
        result.update(expires=best.isoformat(), source=source, error="")
    return result


def passcode_probe() -> dict:
    """Is PHONE_PASSCODE saved, and where; the value never leaves the config module."""
    from .phone import config

    env_path = str(config.ENV_FILE)
    if os.environ.get("PHONE_PASSCODE"):
        return {"set": True, "source": "env", "envPath": env_path}
    saved = config._load_env(config.ENV_FILE)  # re-read: the user may have just saved .env
    if saved.get("PHONE_PASSCODE_DPAPI"):
        return {"set": True, "source": "encrypted", "envPath": env_path}
    if saved.get("PHONE_PASSCODE"):
        return {"set": True, "source": "dotenv", "envPath": env_path}
    if config._legacy.get("PHONE_PASSCODE"):
        return {"set": True, "source": "legacy", "envPath": env_path}
    return {"set": False, "source": "", "envPath": env_path}


APPLE_SERVICE = "Apple Mobile Device Service"


def apple_service() -> dict | None:
    """Read-only: is Apple's USB service installed and running (``sc query``)."""
    if os.name != "nt":
        return None
    try:
        completed = subprocess.run(["sc", "query", APPLE_SERVICE], capture_output=True, text=True, timeout=10,
                                   creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_service_query(completed.returncode, completed.stdout)


def parse_service_query(returncode: int, stdout: str) -> dict | None:
    if returncode == 1060 or "1060" in stdout:  # ERROR_SERVICE_DOES_NOT_EXIST
        return {"installed": False, "running": False}
    match = re.search(r"STATE\s*:\s*\d+\s+([A-Z_]+)", stdout)
    if not match:
        return None
    return {"installed": True, "running": match.group(1) == "RUNNING"}


def ios_devices(executable: str | None) -> dict:
    """Count USB iPhones with ``ios list``; device IDs stay out of the result."""
    if not executable:
        return {"found": False, "count": 0, "error": ""}
    try:
        completed = subprocess.run([executable, "list", "--details"], capture_output=True, text=True, timeout=15,
                                   creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"found": True, "count": 0, "error": str(exc)}
    last = ""
    for line in (completed.stdout + "\n" + completed.stderr).splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            last = line.strip() or last
            continue
        if isinstance(data, dict) and isinstance(data.get("deviceList"), list):
            devices = data["deviceList"]
            first = devices[0] if devices and isinstance(devices[0], dict) else {}
            return {"found": True, "count": len(devices), "error": "",
                    "model": str(first.get("ProductType") or ""), "ios": str(first.get("ProductVersion") or "")}
        if isinstance(data, dict):
            last = str(data.get("msg") or data.get("err") or data.get("error") or last)
    return {"found": True, "count": 0, "error": last or f"ios list exited with {completed.returncode}"}


def get_json(url: str, timeout: float) -> dict | None:
    try:
        with urlopen(url, timeout=timeout) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def ollama_installed() -> bool:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return bool(shutil.which("ollama")) or (local / "Programs" / "Ollama" / "ollama.exe").is_file()


def local_drives() -> list[str]:
    if os.name != "nt":
        return []
    import ctypes

    kernel = ctypes.windll.kernel32
    mask = kernel.GetLogicalDrives()
    # Skip network shares: an offline mapped drive can stall a folder check.
    return [f"{letter}:\\" for index, letter in enumerate(string.ascii_uppercase)
            if mask >> index & 1 and kernel.GetDriveTypeW(f"{letter}:\\") != 4]


def read_json_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def local_probes(state: Path, watch: Callable[[], dict], inspection: Callable[[], dict]) -> SetupProbes:
    wda_port = os.environ.get("WDA_PORT", "8100").strip() or "8100"
    return SetupProbes(
        driver=driver_probe,
        ios_devices=lambda: ios_devices(go_ios()),
        wda_status=lambda: get_json(f"http://127.0.0.1:{wda_port}/status", 3),
        wda_bundle=wda_bundle,
        ollama_tags=lambda: get_json(f"{OLLAMA}/api/tags", 3),
        ollama_installed=ollama_installed,
        cloud_folders=lambda: cloud_folders(os.environ, Path.home(), local_drives(), Path.is_dir,
                                            lambda path: path.read_text(encoding="utf-8")),
        watch=watch,
        inspection=inspection,
        phone_space=lambda: read_json_file(state / "phone-space.json"),
        usb_power=usb_power,
        wda_signature=wda_signature,
        passcode=passcode_probe,
        apple_service=apple_service,
        usb_helper=usb_helper_status,
        usb_path=usb_path,
        battery=battery_probe,
        screen_text=screen_text_probe,
    )


def screen_text_probe() -> dict | None:
    try:
        from . import ocr

        return ocr.probe()
    except Exception:  # a diagnostics row must never take the checklist down
        return None


def battery_probe() -> dict | None:
    """Read-only: one `ios batteryregistry` over lockdown (link_probe.battery_snapshot)."""
    from . import link_probe

    try:
        return link_probe.battery_snapshot()
    except Exception as exc:  # a diagnostics row must never take the checklist down
        return {"error": type(exc).__name__}


def usb_helper_status() -> dict | None:
    """Read-only: the USB recovery helper's files and scheduled task (usb_helper.status)."""
    try:
        from . import usb_helper

        return usb_helper.status()
    except Exception:
        return None


# Walks DEVPKEY_Device_Parent from the iPhone's USB node (never an interface, never a caller value)
# up to the PCI controller. Read-only: Get-PnpDevice / Get-PnpDeviceProperty only.
USB_PATH_SCRIPT = (
    "$ErrorActionPreference = 'SilentlyContinue'; "
    "$phone = Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\\VID_05AC&PID_12A8\\*' -and "
    "$_.InstanceId -notmatch '&MI_' } | Select-Object -First 1; "
    "if (-not $phone) { '{\"found\":false}'; exit 0 }; "
    "$chain = @(); $id = [string]$phone.InstanceId; "
    "for ($i = 0; $i -lt 12 -and $id; $i++) { "
    "$dev = Get-PnpDevice -InstanceId $id; "
    "$chain += @{ instanceId = $id; name = [string]$dev.FriendlyName; class = [string]$dev.Class }; "
    "if ($id -like 'PCI\\*') { break }; "
    "$id = [string](Get-PnpDeviceProperty -InstanceId $id -KeyName 'DEVPKEY_Device_Parent').Data }; "
    "@{ found = $true; chain = $chain } | ConvertTo-Json -Compress -Depth 4")


def usb_path() -> dict | None:
    """Read-only: the iPhone's USB chain up to its host controller, from Windows' PnP tree."""
    if os.name != "nt":
        return None
    try:
        completed = subprocess.run(["powershell", "-NoProfile", "-Command", USB_PATH_SCRIPT], capture_output=True,
                                   text=True, timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_usb_path(completed.stdout)


def parse_usb_path(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                return None
            if isinstance(data, dict) and isinstance(data.get("chain", []), list):
                return data
            return None
    return None


USB_SUBGROUP = "2a737441-1930-4402-8d77-b2bebba308a3"
USB_SELECTIVE_SUSPEND = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"


def usb_power() -> dict | None:
    """Read-only: selective suspend on AC power, and hubs Windows may power down."""
    if os.name != "nt":
        return {"selectiveSuspend": False, "hubsAllowedOff": 0}
    try:
        query = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", USB_SUBGROUP, USB_SELECTIVE_SUSPEND],
                               capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
        match = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", query.stdout)
        hubs = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance -Namespace root/wmi -ClassName MSPower_DeviceEnable | "
             "Where-Object { $_.InstanceName -match 'ROOT_HUB' -and $_.Enable }).Count"],
            capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if not match:
        return None
    count = hubs.stdout.strip()
    return {"selectiveSuspend": int(match.group(1), 16) != 0, "hubsAllowedOff": int(count) if count.isdigit() else 0}
