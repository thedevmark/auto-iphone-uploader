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
from pathlib import Path
from typing import Callable, Mapping
from urllib.request import urlopen

from .analyze import OLLAMA, TEXT_MODEL, VISION_MODEL
from .phone_space import GB, ios_path
from .sidetap_root import sidetap_root

# A 2 GB video needs its download copy, its Photos copy and a margin.
COMFORTABLE_FREE = 5 * GB
APP_NAMES = {"onedrive": "OneDrive", "youtube": "YouTube", "edits": "Edits", "instagram": "Instagram",
             "facebook": "Facebook", "threads": "Threads", "tiktok": "TikTok"}
SOCIAL_APPS = ("youtube", "instagram", "facebook", "threads", "tiktok")
VISION_HINT = re.compile(r"vl\b|vision|llava|clip|mllama|moondream|minicpm-v|gemma3")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass(frozen=True)
class SetupProbes:
    sidetap_root: Path
    sidetap_import_error: Callable[[Path], str | None]
    ios_devices: Callable[[], dict]
    wda_status: Callable[[], dict | None]
    ollama_tags: Callable[[], dict | None]
    ollama_installed: Callable[[], bool]
    cloud_folders: Callable[[], list[tuple[str, Path]]]
    watch: Callable[[], dict]
    inspection: Callable[[], dict]
    phone_space: Callable[[], dict | None]
    vision_model: str = VISION_MODEL
    text_model: str = TEXT_MODEL


def item(key: str, status: str, title: str, detail: str, fix: str = "", *,
         required: bool = True, **extra: object) -> dict:
    return {"key": key, "status": status, "title": title, "detail": detail, "fix": fix,
            "required": required, **extra}


def sidetap_item(root: Path, import_error: str | None) -> dict:
    """``import_error`` is None when no SideTap install was found at all."""
    title = "SideTap"
    if import_error is None:
        return item("sidetap", "action", title, "SideTap was not found on this PC.",
                    "Install SideTap. It is the helper that lets this PC control the iPhone. Then check again.")
    if import_error:
        return item("sidetap", "action", title, f"SideTap is installed at {root} but could not load: {import_error[:160]}",
                    "Update or reinstall SideTap, then check again.")
    return item("sidetap", "ok", title, f"Installed at {root}")


def phone_item(ios: dict, sidetap_ok: bool) -> dict:
    title = "iPhone connected"
    if not ios.get("found"):
        if not sidetap_ok:
            return item("phone", "blocked", title, "Waiting for SideTap.", "Install SideTap first.")
        return item("phone", "action", title, "SideTap's iPhone connector (go-ios) is missing.",
                    "Reinstall SideTap so it includes go-ios, then check again.")
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


def link_item(wda: dict | None, phone_ok: bool) -> dict:
    title = "Phone link"
    if not phone_ok:
        return item("link", "blocked", title, "Waiting for the iPhone.", "Connect the iPhone first.")
    value = wda.get("value") if isinstance(wda, dict) else None
    if not isinstance(value, dict) or value.get("ready") is False:
        return item("link", "action", title, "The iPhone is connected, but the app cannot control it yet.",
                    "Unlock the iPhone and tap Trust if it asks. Then start the phone link in SideTap and check again.")
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


def vision_capable(model: dict) -> bool:
    details = model.get("details") if isinstance(model.get("details"), dict) else {}
    families = details.get("families") if isinstance(details.get("families"), list) else []
    text = " ".join([str(model.get("name", "")), str(details.get("family", "")), *map(str, families)]).casefold()
    return bool(VISION_HINT.search(text))


def llm_item(tags: dict | None, installed: bool, vision_model: str, text_model: str) -> dict:
    title = "Local AI (Ollama)"
    pull = f"ollama pull {vision_model} and ollama pull {text_model}"
    if not isinstance(tags, dict):
        if installed:
            return item("llm", "action", title, "Ollama is installed but not running.",
                        "Open Ollama from the Start menu, then check again.")
        return item("llm", "action", title, "No local AI was found on this PC.",
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
        return item("llm", "action", title,
                    f"Ollama is running. Installed models: {summary}." if models else "Ollama is running with no models.",
                    f"Run {' and '.join('ollama pull ' + name for name in missing)}, then check again.", models=models)
    return item("llm", "ok", title, f"Ollama is running · vision: {vision_model} · text: {text_model}", models=models)


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


def inside(folder: Path, root: Path) -> bool:
    folder_text = os.path.normcase(os.path.normpath(str(folder)))
    root_text = os.path.normcase(os.path.normpath(str(root))).rstrip("\\/")
    return folder_text == root_text or folder_text.startswith(root_text + os.sep)


def cloud_for(folder: Path, clouds: list[tuple[str, Path]]) -> tuple[str, Path] | None:
    containing = [(provider, root) for provider, root in clouds if inside(folder, root)]
    return max(containing, key=lambda cloud: len(str(cloud[1])), default=None)


def folder_item(watch: dict, clouds: list[tuple[str, Path]]) -> dict:
    title = "Video folder"
    available = "; ".join(f"{provider} ({root})" for provider, root in clouds)
    pick_inside = (f"Pick one inside {' or '.join(dict.fromkeys(provider for provider, _ in clouds))} so the iPhone "
                   "can open new videos from Files." if clouds else
                   "No OneDrive, Google Drive, Dropbox or iCloud Drive folder was found on this PC. Install one of "
                   "them and let it sync, then choose a folder inside it so the iPhone can open new videos.")
    path = str(watch.get("path") or "")
    if not path:
        return item("folder", "action", title,
                    f"No folder chosen yet.{' Cloud folders on this PC: ' + available + '.' if clouds else ''}",
                    f"Choose the folder your video editor exports to. {pick_inside}", action="folder")
    cloud = cloud_for(Path(path), clouds)
    if watch.get("problem"):
        return item("folder", "action", title, f"{path} · {watch['problem']}",
                    str(watch.get("fix") or "Choose the export folder again."), action="folder",
                    provider=cloud[0] if cloud else "")
    if not cloud:
        return item("folder", "action", title, f"{path} is not inside a cloud folder the iPhone can reach.",
                    f"Choose a different folder. {pick_inside}", action="folder", provider="")
    if not watch.get("enabled"):
        return item("folder", "action", title, f"{path} · inside {cloud[0]} · Watch folder is off.",
                    "Turn on Watch folder so new exports are picked up by themselves.", action="watch",
                    provider=cloud[0])
    return item("folder", "ok", title, f"Watching {path} · inside {cloud[0]}", action="folder", provider=cloud[0])


def checklist(probes: SetupProbes) -> dict:
    root = probes.sidetap_root
    sidetap = sidetap_item(root, probes.sidetap_import_error(root))
    phone = phone_item(probes.ios_devices(), sidetap["status"] == "ok")
    link = link_item(probes.wda_status(), phone["status"] == "ok")
    items = [sidetap, phone, link,
             inventory_item(probes.inspection(), link["status"] == "ok"),
             space_item(probes.phone_space()),
             llm_item(probes.ollama_tags(), probes.ollama_installed(), probes.vision_model, probes.text_model),
             folder_item(probes.watch(), probes.cloud_folders())]
    return {"items": items, "ready": all(entry["status"] == "ok" for entry in items if entry["required"])}


# Real, read-only probes for this PC.

def sidetap_import_error(root: Path) -> str | None:
    """Import SideTap in a child process so a broken install cannot affect this server."""
    if not (root / "src" / "phone_harness").is_dir():
        return None
    code = "import sys; sys.path.insert(0, sys.argv[1]); import phone_harness.device, phone_harness.helpers"
    try:
        completed = subprocess.run([sys.executable, "-c", code, str(root / "src")], capture_output=True,
                                   text=True, timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if completed.returncode:
        return (completed.stderr.strip().splitlines() or ["import failed"])[-1]
    return ""


def go_ios(root: Path) -> str | None:
    found = ios_path()
    if found:
        return found
    for candidate in (root / "bin" / "ios.exe",
                      Path(os.environ.get("LOCALAPPDATA", "")) / "SideTap" / "bin" / "ios.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


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
    root = sidetap_root()
    wda_port = os.environ.get("WDA_PORT", "8100").strip() or "8100"
    return SetupProbes(
        sidetap_root=root,
        sidetap_import_error=sidetap_import_error,
        ios_devices=lambda: ios_devices(go_ios(root)),
        wda_status=lambda: get_json(f"http://127.0.0.1:{wda_port}/status", 3),
        ollama_tags=lambda: get_json(f"{OLLAMA}/api/tags", 3),
        ollama_installed=ollama_installed,
        cloud_folders=lambda: cloud_folders(os.environ, Path.home(), local_drives(), Path.is_dir,
                                            lambda path: path.read_text(encoding="utf-8")),
        watch=watch,
        inspection=inspection,
        phone_space=lambda: read_json_file(state / "phone-space.json"),
    )
