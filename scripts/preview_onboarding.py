"""Preview the first-run screens without a phone: ``python scripts/preview_onboarding.py``.

Starts a second app server on another port (never the live 4748) against a temp
state folder in test mode, with the setup checklist fed from a fixture instead
of the real probes. Nothing here lists, taps or signs the iPhone, and the
owner's .env and .state are never read. Use it to review or screenshot the
onboarding flow:

    python scripts/preview_onboarding.py --port 4790 --scenario connect

Scenarios: fresh, connect, control, folder, apps, ready. The scenario can be
switched while the server runs by writing its name to <state>/preview-scenario.txt
(the path is printed at start); the page picks it up on its next check.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ONEDRIVE = Path("C:/Users/you/OneDrive")
SCENARIOS = ("fresh", "connect", "control", "folder", "apps", "ready")


def fixture(scenario: str) -> dict:
    """Probe values for one scenario; later scenarios have everything earlier ones fixed."""
    from video_drop.setup_check import TEXT_MODEL, VISION_MODEL

    stage = SCENARIOS.index(scenario if scenario in SCENARIOS else "fresh")
    phone = stage >= 2
    signed = stage >= 3
    folder = stage >= 4
    apps = stage >= 5
    return {
        "driver": lambda: {"importError": "", "goIos": str(ROOT / "tools" / "go-ios" / "ios.exe"), "legacyKeys": []},
        "ios_devices": lambda: {"found": True, "count": 1 if phone else 0, "error": ""},
        "wda_status": lambda: {"value": {"ready": True, "os": {"version": "26.7"}}} if signed else None,
        "wda_bundle": lambda: "com.facebook.WebDriverAgentRunner.xctrunner" if signed else None,
        "ollama_tags": lambda: {"models": [{"name": VISION_MODEL, "details": {"family": "qwen25vl"}},
                                           {"name": TEXT_MODEL, "details": {"family": "qwen3"}}]} if apps else None,
        "ollama_installed": lambda: False,
        "cloud_folders": lambda: [("OneDrive", ONEDRIVE)],
        "watch": lambda: ({"path": str(ONEDRIVE / "_Videos"), "enabled": True, "problem": "", "fix": ""} if folder
                          else {"path": "", "enabled": False, "problem": "", "fix": ""}),
        "inspection": lambda: ({"status": "ready", "screenPoints": {"width": 440.0, "height": 956.0},
                                "installed": ["onedrive", "youtube", "instagram", "facebook", "threads", "tiktok"]}
                               if apps else {"status": "idle"}),
        "phone_space": lambda: {"freeBytes": 41 * 1024 ** 3, "checkedAt": "2026-10-01T09:00:00+00:00"} if apps else None,
        "usb_power": lambda: {"selectiveSuspend": stage < 2, "hubsAllowedOff": 0 if phone else 2},
        "wda_signature": lambda: {"expires": "2026-10-07T18:40:00+00:00", "source": "phone", "error": ""},
        "passcode": lambda: {"set": signed, "source": "dotenv" if signed else "", "envPath": str(ROOT / ".env")},
        "apple_service": lambda: {"installed": True, "running": True},
        "usb_helper": lambda: {"supported": True, "installed": apps, "script": apps, "task": apps, "version": 1,
                               "detail": "installed" if apps else "helper script missing; scheduled task missing"},
        "usb_path": lambda: {"found": True, "chain": [
            {"instanceId": "USB\\VID_05AC&PID_12A8\\X", "name": "Apple iPhone", "class": "USB"},
            {"instanceId": "USB\\ROOT_HUB30\\5&1&0&0", "name": "USB Root Hub (USB 3.0)", "class": "USB"},
            {"instanceId": "PCI\\VEN_1022&DEV_149C\\3&1&0&41", "name": "AMD USB 3.10 eXtensible Host Controller",
             "class": "USB"}]},
        "battery": lambda: {"CurrentCapacity": 64, "IsCharging": True, "InstantAmperage": 812, "Voltage": 3980,
                            "Temperature": 2950},
        "screen_text": lambda: {"available": True, "language": "en-US", "error": ""},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview the first-run screens with a fixture checklist")
    parser.add_argument("--port", type=int, default=4790)
    parser.add_argument("--scenario", choices=SCENARIOS, default="fresh")
    parser.add_argument("--state", default="", help="temp state folder (default: a fresh temp dir)")
    args = parser.parse_args()
    if args.port == 4748:
        raise SystemExit("Port 4748 is the live app; pick another port for the preview.")
    state = Path(args.state or tempfile.mkdtemp(prefix="video-drop-preview-"))
    state.mkdir(parents=True, exist_ok=True)
    scenario_file = state / "preview-scenario.txt"
    scenario_file.write_text(args.scenario, encoding="utf-8")
    # Read by the app modules at import time: the preview never touches the real state or .env.
    os.environ["VIDEO_DROP_STATE"] = str(state)
    os.environ["VIDEO_DROP_TEST_MODE"] = "1"
    os.environ["VIDEO_DROP_ENV_FILE"] = str(state / ".env")
    (state / ".env").write_text("", encoding="utf-8")

    from video_drop import server
    from video_drop.setup_check import SetupProbes

    def probes(_state, _watch, _inspection) -> SetupProbes:
        name = scenario_file.read_text(encoding="utf-8").strip() if scenario_file.is_file() else args.scenario
        return SetupProbes(**fixture(name))

    server.setup_probes = probes
    server.queue_phone_inspection = lambda: {"status": "idle"}
    server.phone_free_bytes = lambda: None
    server.sidetap_status = lambda: {"status": "unavailable", "passed": 0, "total": 0, "firstFailure": "preview",
                                     "firstFailureFix": "", "checks": []}
    print(f"preview state: {state}\nscenario file: {scenario_file}", flush=True)
    sys.argv = [sys.argv[0], "--port", str(args.port)]
    server.main()


if __name__ == "__main__":
    main()
