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
            "from each video, and the calibration run for a new iPhone will use it. Without one, you write "
            "the copy yourself.")
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
         required: bool = True, **extra: object) -> dict:
    return {"key": key, "status": status, "title": title, "detail": detail, "fix": fix,
            "required": required, **extra}


LEGACY_ENV_NOTE = ("still read from SideTap's .env; copy {keys} into this app's .env to finish the move away from "
                   "SideTap")


def driver_item(driver: dict) -> dict:
    """The app's own phone driver (video_drop/phone): it must import, and go-ios must be on this PC."""
    title = "Phone driver"
    error = str(driver.get("importError") or "")
    if error:
        return item("driver", "action", title, f"The phone driver could not load: {error[:160]}",
                    "Run pip install -r requirements.txt in the app folder, then check again.")
    go_ios = driver.get("goIos")
    if not go_ios:
        return item("driver", "action", title, "go-ios (ios.exe), the USB connector for the iPhone, is not installed.",
                    "Install Node.js, run npm install -g go-ios, then check again. Or set GO_IOS_PATH in .env "
                    "to the full path of ios.exe.")
    detail = f"Built in · go-ios at {go_ios}"
    legacy = [str(key) for key in driver.get("legacyKeys") or []]
    if legacy:
        detail += " · " + LEGACY_ENV_NOTE.format(keys=", ".join(legacy))
    return item("driver", "ok", title, detail)


def phone_item(ios: dict, driver_ok: bool) -> dict:
    title = "iPhone connected"
    if not ios.get("found"):
        return item("phone", "blocked", title, "Waiting for the phone driver.", "Fix the phone driver first.")
    error = str(ios.get("error", ""))
    if error:
        if re.search(r"27015|usbmux", error, re.IGNORECASE):
            return item("phone", "action", title, "Windows cannot talk to iPhones yet.",
                        "Install Apple Devices from the Microsoft Store, then reconnect the iPhone and check again.")
        return item("phone", "action", title, f"The iPhone check failed: {error[:160]}",
                    "Unplug the iPhone, plug it back in, unlock it, then check again.")
    count = int(ios.get("count", 0))
    if count == 0:
        return item("phone", "action", title, "No iPhone found over USB.",
                    "Connect the iPhone with a USB cable, unlock it, and tap Trust if it asks.")
    if count > 1:
        return item("phone", "action", title, f"{count} iPhones are connected.",
                    "Unplug the others so only the iPhone you post from stays connected.")
    return item("phone", "ok", title, "One iPhone is connected over USB.")


def link_item(wda: dict | None, phone_ok: bool, wda_bundle: str | None = "") -> dict:
    """``wda_bundle`` is the WebDriverAgent runner found on the phone (None = not installed)."""
    title = "Phone link"
    if not phone_ok:
        return item("link", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.")
    value = wda.get("value") if isinstance(wda, dict) else None
    if not isinstance(value, dict) or value.get("ready") is False:
        if wda_bundle is None:
            return item("link", "action", title, "WebDriverAgent, the input driver, is not installed on the iPhone.",
                        "Sign and install WebDriverAgent on the iPhone with Sideloadly (a free Apple ID works; "
                        "re-sign every 7 days), then check again.")
        return item("link", "action", title, "The iPhone is connected, but the app cannot control it yet.",
                    "Unlock the iPhone and tap Trust if it asks. The app's link supervisor starts the driver by "
                    "itself; if it stays down, unplug and replug the iPhone, then check again.")
    version = value.get("os", {}).get("version", "") if isinstance(value.get("os"), dict) else ""
    return item("link", "ok", title, f"Touch control is answering{' on iOS ' + version if version else ''}.")


def inventory_item(inspection: dict, link_ok: bool) -> dict:
    title = "Phone details"
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
                        "Install the apps you post to from the App Store, then check the phone again.",
                        action="inspect")
        return item("inventory", "ok", title, detail, action="inspect")
    if status == "inspecting":
        return item("inventory", "blocked", title, "Reading the iPhone now…",
                    "Leave the iPhone alone until the check finishes.")
    if not link_ok:
        return item("inventory", "blocked", title, "Not read yet.", "Get the phone link working first.")
    error = str(inspection.get("error", "")).strip()
    return item("inventory", "action", title, f"The last check failed: {error[:160]}" if error else "Not read yet.",
                "Click Check phone. It opens YouTube on the iPhone to read the screen size and channel; "
                "don't touch the phone while it runs.", action="inspect")


def space_item(space: dict | None) -> dict:
    title = "iPhone free space"
    free = space.get("freeBytes") if isinstance(space, dict) else None
    if not isinstance(free, int):
        return item("space", "blocked", title, "Not measured yet.",
                    "Nothing to do now. The app measures free space before every upload.", required=False)
    detail = f"{free / GB:.1f} GB free"
    if space.get("checkedAt"):
        detail += f" when last measured ({str(space['checkedAt'])[:10]})"
    if free < COMFORTABLE_FREE:
        return item("space", "action", title, detail,
                    f"Free up space on the iPhone. Keep at least {COMFORTABLE_FREE / GB:.0f} GB free so a video "
                    "and its Photos copy both fit.", required=False)
    return item("space", "ok", title, detail, required=False)


USB_SUSPEND_FIX = (
    "Windows is allowed to switch off USB ports to save power, and it cut this iPhone off mid-upload "
    "(Windows logs it as \"surprise removed\"). In an administrator PowerShell run: "
    "powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 "
    "48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0 ; powercfg /setactive SCHEME_CURRENT. "
    "Then in Device Manager, open each USB Root Hub, Power Management, and untick "
    "\"Allow the computer to turn off this device to save power\". Plug the phone into a port on the PC itself, "
    "not a hub.")


def usb_power_item(power: dict | None) -> dict:
    """Windows USB power saving drops a busy iPhone off the bus (Kernel-PnP 1010, 2026-09-30)."""
    title = "USB power saving off"
    if not isinstance(power, dict):
        return item("usbPower", "blocked", title, "Could not read Windows power settings.", USB_SUSPEND_FIX)
    problems = []
    if power.get("selectiveSuspend"):
        problems.append("USB selective suspend is on")
    hubs = int(power.get("hubsAllowedOff") or 0)
    if hubs:
        problems.append(f"{hubs} USB hub{'s' if hubs != 1 else ''} may be switched off to save power")
    if problems:
        return item("usbPower", "action", title, "; ".join(problems) + ".", USB_SUSPEND_FIX)
    return item("usbPower", "ok", title, "Windows keeps the iPhone's USB port powered.")


# WebDriverAgent is signed by the user with their own Apple ID; a free ID's signature lasts 7 days.
SIDELOADLY_STEPS = (
    "1. Install Sideloadly from sideloadly.io. "
    "2. Plug in the iPhone and unlock it. "
    "3. In Sideloadly, drag wda\\WebDriverAgent.ipa from the app folder onto the window, pick the iPhone, type your "
    "Apple ID and click Start. Apple asks for the password and a 2FA code inside Sideloadly, never in this app. "
    "4. On the iPhone: Settings > General > VPN & Device Management > tap your Apple ID > Trust. "
    "5. In the app folder run: python scripts\\phone_resign.py. It re-signs the part Sideloadly leaves unsigned "
    "(the nested test bundle) so taps work, and asks the link supervisor to start the driver. "
    "A free Apple ID's signature lasts 7 days: run step 5 again before it expires (it asks for step 3 only when "
    "the phone holds no valid signature).")
RESIGN_STEPS = ("Run python scripts\\phone_resign.py in the app folder and click Start in Sideloadly when it asks "
                "(Sideloadly: wda\\WebDriverAgent.ipa, your iPhone, your Apple ID). Then check again.")
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
    title = "Signed WebDriverAgent on the iPhone"
    if not phone_ok:
        return item("signature", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.")
    if not installed:
        return item("signature", "action", title, "WebDriverAgent, the input driver, is not on the iPhone yet.",
                    SIDELOADLY_STEPS)
    signature = signature if isinstance(signature, dict) else {}
    expires = _parse_expiry(signature.get("expires"))
    if expires is None:
        error = str(signature.get("error") or "").strip()
        why = ("install the re-sign tool with pip install -r requirements-resign.txt so the app can read the "
               "signature date off the phone" if "pymobiledevice3" in error else
               (error[:160] if error else "the phone did not hand over its signing profile"))
        return item("signature", "ok", title, f"Installed · days left unknown: {why}.")
    now = now or datetime.now(timezone.utc)
    left = (expires - now).total_seconds() / 86400
    when = expires.astimezone().strftime("%Y-%m-%d %H:%M")
    if left <= 0:
        return item("signature", "action", title, f"The signature expired on {when}; taps will not work.", RESIGN_STEPS)
    if left < 1:
        return item("signature", "action", title, f"The signature expires today ({when}).", RESIGN_STEPS)
    days = int(left)
    detail = f"Signed · {days} day{'s' if days != 1 else ''} left (until {when})"
    if left <= RESIGN_SOON_DAYS:
        detail += " · re-sign soon: " + RESIGN_STEPS
    return item("signature", "ok", title, detail)


def passcode_item(passcode: dict | None) -> dict:
    """Whether PHONE_PASSCODE is saved so the app can unlock the phone; the value itself is never read here."""
    title = "Passcode saved for automation"
    passcode = passcode if isinstance(passcode, dict) else {}
    env_path = str(passcode.get("envPath") or ".env")
    if not passcode.get("set"):
        return item("passcode", "action", title,
                    "No passcode is saved, so the app cannot unlock the iPhone by itself before a post.",
                    f"Open (or create) the file {env_path} in a text editor and add one line: "
                    "PHONE_PASSCODE=your iPhone passcode. The app types it only on the lock screen, refuses to type "
                    "it anywhere else, and never shows or logs it. Save the file, then check again.")
    if passcode.get("source") == "legacy":
        return item("passcode", "ok", title,
                    "Read from the installed SideTap's .env for now · " + LEGACY_ENV_NOTE.format(keys="PHONE_PASSCODE")
                    + f" ({env_path}).")
    return item("passcode", "ok", title, f"Saved in {env_path} (never shown).")


def apple_service_item(service: dict | None) -> dict:
    """Apple Mobile Device Service is the Windows USB driver go-ios talks to (usbmuxd on port 27015)."""
    title = "Apple Mobile Device Service running"
    if not isinstance(service, dict):
        return item("appleService", "blocked", title, "Could not read Windows services.",
                    "Open Services (services.msc) and look for Apple Mobile Device Service; it must be Running.")
    if not service.get("installed"):
        return item("appleService", "action", title, "Windows has no Apple USB driver for iPhones.",
                    "Install Apple Devices from the Microsoft Store (or iTunes from apple.com), then reconnect the "
                    "iPhone, unlock it, tap Trust, and check again.")
    if not service.get("running"):
        return item("appleService", "action", title, "The service is installed but not running.",
                    "Open Services (services.msc), start Apple Mobile Device Service and set its Startup type to "
                    "Automatic. If it will not start, reinstall Apple Devices from the Microsoft Store. Then check again.")
    return item("appleService", "ok", title, "Running.")


USB_HELPER_FIX = (
    "In PowerShell in the app folder run: powershell -NoProfile -ExecutionPolicy Bypass -File "
    "scripts\\install_windows.ps1 -InstallUsbHelper and accept the administrator prompt once. It registers a small "
    "on-demand task (runs as SYSTEM) that can do exactly two things for Apple devices: restart Apple Mobile Device "
    "Service and reset the iPhone's USB port. The app itself keeps running without administrator rights "
    "(docs/usb-recovery-helper.md has the details and the security notes).")


def usb_helper_item(helper: dict | None) -> dict:
    """The elevated USB recovery helper: without it a stalled USB link ends in 'unplug and replug'.

    Recommended, not required: posting works without it, and it asks for one administrator prompt.
    """
    title = "USB recovery helper installed"
    if not isinstance(helper, dict):
        return item("usbHelper", "blocked", title, "Could not read the helper's state.", USB_HELPER_FIX, required=False)
    if not helper.get("supported", True):
        return item("usbHelper", "ok", title, "Not needed on this system (Windows only).", required=False)
    if helper.get("installed"):
        stale = str(helper.get("detail") or "")
        if "version" in stale:
            return item("usbHelper", "action", title, f"Installed, but {stale}.",
                        "Run the install command again to update it: " + USB_HELPER_FIX, required=False)
        return item("usbHelper", "ok", title,
                    "Installed. When the phone's USB link stalls, the app restarts Apple's USB service and resets "
                    "the phone's USB port by itself before ever asking for a replug.", required=False)
    partial = str(helper.get("detail") or "")
    if helper.get("script") or helper.get("task"):
        return item("usbHelper", "action", title, f"Installed incompletely ({partial}).", USB_HELPER_FIX, required=False)
    return item("usbHelper", "action", title,
                "Not installed. Without it, a stalled USB link can only be fixed by unplugging and replugging the "
                "phone by hand.", USB_HELPER_FIX, required=False)


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
USB_PATH_FIX = ("Plug the iPhone straight into a rear port on the PC's own (CPU) USB controller: no hub, no "
                "front-panel port, no dock. Use the cable that came with the phone or a USB-A-to-C cable. Then check "
                "again: this row should read 'CPU USB controller, no hub'.")


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
    title = "iPhone USB path"
    if not phone_ok:
        return item("usbPath", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.", required=False)
    if not isinstance(path, dict):
        return item("usbPath", "blocked", title, "Could not read the phone's USB path from Windows.", USB_PATH_FIX,
                    required=False)
    if not path.get("found"):
        return item("usbPath", "blocked", title, "Windows does not list an iPhone USB device right now.",
                    "Reconnect the iPhone, then check again.", required=False)
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
                    controller=described["controllerId"], hubs=len(described["hubs"]))
    note = "CPU USB controller, no hub" if described["verdict"] == "ok" else "no hub; controller not in the known list"
    return item("usbPath", "ok", title, f"{detail} · {note}.", required=False, controller=described["controllerId"],
                hubs=0)


def vision_capable(model: dict) -> bool:
    details = model.get("details") if isinstance(model.get("details"), dict) else {}
    families = details.get("families") if isinstance(details.get("families"), list) else []
    text = " ".join([str(model.get("name", "")), str(details.get("family", "")), *map(str, families)]).casefold()
    return bool(VISION_HINT.search(text))


def llm_item(tags: dict | None, installed: bool, vision_model: str, text_model: str) -> dict:
    """Local AI is recommended, never required: its absence never holds setup back."""
    title = "Local AI (Ollama)"
    pull = f"ollama pull {vision_model} and ollama pull {text_model}"

    def entry(status: str, detail: str, fix: str = "", **extra: object) -> dict:
        if status != "ok":
            detail = f"{detail} {LLM_ADDS}"
        return item("llm", status, title, detail, fix, required=False, recommended=True, **extra)

    if not isinstance(tags, dict):
        if installed:
            return entry("action", "Ollama is installed but not running.",
                         "Open Ollama from the Start menu, then check again.")
        return entry("action", "No local AI was found on this PC.",
                     "Install Ollama with one command in PowerShell: winget install Ollama.Ollama "
                     f"(or download it from ollama.com). Then run {pull}.")
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
                     f"Run {' and '.join('ollama pull ' + name for name in missing)}, then check again.", models=models)
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
        return item("folder", "action", title,
                    f"No folder chosen yet.{' Cloud folders on this PC: ' + available + '.' if clouds else ''}",
                    f"Choose the folder your video editor exports to. {pick_inside}", action="folder")
    cloud = cloud_for(Path(path), clouds)
    provider = cloud[0] if cloud else ""
    if watch.get("problem"):
        return item("folder", "action", title, f"{path} · {watch['problem']}",
                    str(watch.get("fix") or "Choose the export folder again."), action="folder", provider=provider)
    if not cloud:
        return item("folder", "action", title, f"{path} is not inside a cloud folder the iPhone can reach.",
                    f"Choose a different folder. {pick_inside}", action="folder", provider="")
    if provider == "OneDrive":
        route, note = "the iPhone opens videos in the OneDrive app", ""
    else:
        route, note = "the iPhone opens videos through the Files app", " " + files_route_note(provider)
    if not watch.get("enabled"):
        return item("folder", "action", title, f"{path} · inside {provider} · Watch folder is off.{note}",
                    "Turn on Watch folder so new exports are picked up by themselves.", action="watch",
                    provider=provider)
    return item("folder", "ok", title, f"Watching {path} · inside {provider} · {route}.{note}",
                action="folder", provider=provider)


CHARGING_FIX = (
    "Charge the iPhone on a wall charger before posting, and use a USB port that can power it under load: "
    "a rear port on the CPU's own controller, a powered hub, or a USB-C PD port. Keep it above 20%."
)


def charging_item(battery: dict | None, phone_ok: bool) -> dict:
    """Is the port actually charging the phone? On 2026-09-30 the chipset port read IsCharging while the
    phone drained at 2.4 A under video load at 1-2% capacity; every link stall that day happened with the
    battery low. One sample here: a negative current while 'charging' or a low capacity is the warning."""
    title = "iPhone charging over USB"
    if not phone_ok:
        return item("charging", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.", required=False)
    if not isinstance(battery, dict) or "error" in battery or "CurrentCapacity" not in battery:
        return item("charging", "blocked", title, "Could not read the phone's battery over USB.", CHARGING_FIX,
                    required=False)
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
                    CHARGING_FIX, required=False, capacity=capacity, current=current)
    return item("charging", "ok", title, detail, required=False, capacity=capacity, current=current)


OCR_FIX = ("Open Settings > Time & language > Language & region, add English (United States) and make sure its "
           "Optical character recognition feature is installed (Language options), then check again.")


def screen_text_item(screen_text: dict | None) -> dict:
    """Windows' built-in OCR engine: YouTube 21.38 hides its Description, Paid promotion and
    "AI use, Tags" rows from accessibility, so the YouTube flow reads them from a screenshot."""
    title = "Screen text reader (Windows OCR)"
    if not isinstance(screen_text, dict):
        return item("screenText", "blocked", title, "Could not ask Windows for its OCR engine.", OCR_FIX)
    if not screen_text.get("available"):
        detail = "Windows has no OCR language, so YouTube's hidden detail rows cannot be read."
        if screen_text.get("error"):
            detail += f" ({screen_text['error']})"
        return item("screenText", "action", title, detail, OCR_FIX)
    language = str(screen_text.get("language") or "")
    if not language.casefold().startswith("en"):
        return item("screenText", "action", title,
                    f"Windows reads screen text in {language} only; the apps are matched by their English labels.",
                    OCR_FIX)
    return item("screenText", "ok", title, f"Offline, {language}.")


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
    items = [driver, phone, link,
             inventory_item(probes.inspection(), link["status"] == "ok"),
             space_item(probes.phone_space()),
             llm_item(probes.ollama_tags(), probes.ollama_installed(), probes.vision_model, probes.text_model),
             folder_item(probes.watch(), probes.cloud_folders()),
             signature_item(probes.wda_signature() if installed else None, phone_ok, installed),
             passcode_item(probes.passcode()),
             apple_service_item(probes.apple_service()),
             usb_power_item(probes.usb_power()),
             usb_helper_item(probes.usb_helper()),
             usb_path_item(probes.usb_path() if phone_ok else None, phone_ok),
             charging_item(probes.battery() if phone_ok else None, phone_ok),
             screen_text_item(probes.screen_text())]
    return {"items": items, "ready": all(entry["status"] == "ok" for entry in items if entry["required"])}


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
    if config._load_env(config.ENV_FILE).get("PHONE_PASSCODE"):  # re-read: the user may have just saved .env
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
        completed = subprocess.run([executable, "list"], capture_output=True, text=True, timeout=15,
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
            return {"found": True, "count": len(data["deviceList"]), "error": ""}
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
