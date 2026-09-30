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


def inside(folder: Path, root: Path) -> bool:
    folder_text = os.path.normcase(os.path.normpath(str(folder)))
    root_text = os.path.normcase(os.path.normpath(str(root))).rstrip("\\/")
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
             usb_power_item(probes.usb_power())]
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
    )


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
