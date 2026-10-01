"""Named bundles of WebDriverAgent session settings, chosen with WDA_SETTINGS_PROFILE.

The settings themselves are the WDA_* / MJPEG_* keys ``config`` reads and
``wda_client._create_session`` posts to ``/appium/settings``. A profile only
supplies *defaults* for those keys: a key set explicitly in the process
environment or in .env always wins, so an A/B run can pick a profile and still
pin one knob. Applied once, before ``config`` reads anything (see
``video_drop/phone/__init__.py``), which is why this module reads .env itself.

Profiles (docs/link-literature.md, section 5.2, and docs/link-root-cause.md,
experiment 7):

  default  what the app shipped with: WDA_IDLE_WAIT 2, MJPEG stream keys sent
           (60 fps, quality 70, 50 %), accessibilityDeadline 2.0, WDA's own
           snapshot depth (50).
  lean     the experiment agent's A/B arm: no MJPEG keys at all (WDA keeps its
           defaults and captures nothing while no client streams), a short
           accessibilityDeadline (0.5 s), snapshotMaxDepth 20, no idle wait.
  media    for sessions that will sit on video surfaces: snapshotMaxDepth 12
           (appium/appium discussion #19255 recommends 10-15 on TikTok),
           accessibilityDeadline 1.0, no idle wait, no MJPEG keys.

Set ``WDA_SETTINGS_PROFILE=lean`` (env or .env) before starting the process
that mints the WDA session (the posting script, or scripts/link_experiment.py,
which mints a fresh one when its experiment carries env). Adopted sessions keep
the settings of whoever created them.
"""

from __future__ import annotations

import os
from pathlib import Path

PROFILE_KEY = "WDA_SETTINGS_PROFILE"
DEFAULT_PROFILE = "default"

PROFILES: dict[str, dict[str, str]] = {
    "default": {},
    "lean": {
        "MJPEG_SETTINGS": "0",
        "WDA_ACCESSIBILITY_DEADLINE": "0.5",
        "WDA_SNAPSHOT_MAX_DEPTH": "20",
        "WDA_IDLE_WAIT": "0",
    },
    "media": {
        "MJPEG_SETTINGS": "0",
        "WDA_ACCESSIBILITY_DEADLINE": "1.0",
        "WDA_SNAPSHOT_MAX_DEPTH": "12",
        "WDA_IDLE_WAIT": "0",
    },
}


def _dotenv(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (a copy of config._load_env, which cannot be imported yet)."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def profile_name(environ=os.environ, dotenv: dict[str, str] | None = None) -> str:
    return (environ.get(PROFILE_KEY) or (dotenv or {}).get(PROFILE_KEY) or DEFAULT_PROFILE).strip().lower()


def apply_profile(name: str | None = None, *, environ=os.environ, env_file: Path | None = None) -> dict[str, str]:
    """Put the profile's defaults into ``environ`` for every key not already set there or in .env.

    Returns the keys it set. Unknown profile names raise so a typo in .env cannot
    silently run the default arm of an A/B.
    """
    if env_file is None:
        env_file = Path(__file__).resolve().parents[2] / ".env"
    dotenv = _dotenv(env_file)
    name = (name or profile_name(environ, dotenv)).strip().lower()
    if name not in PROFILES:
        raise ValueError(f"{PROFILE_KEY}={name!r} is not a profile; choose one of {', '.join(PROFILES)}")
    applied: dict[str, str] = {}
    for key, value in PROFILES[name].items():
        if environ.get(key) or dotenv.get(key):
            continue
        environ[key] = value
        applied[key] = value
    return applied
