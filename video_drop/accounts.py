"""Operator account targets kept in ignored local state, outside public code."""

from __future__ import annotations

import json
from pathlib import Path


PLATFORMS = ("youtube", "instagram", "facebook", "threads", "tiktok")


def load_targets(state_dir: Path) -> dict[str, str]:
    path = state_dir / "accounts.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(
        key not in PLATFORMS or not isinstance(value, str) for key, value in data.items()
    ):
        raise ValueError("Local accounts.json must map platform names to account strings")
    return {key: value.strip() for key, value in data.items() if value.strip()}


def require_target(targets: dict[str, str], platform: str, saved_account: str) -> str:
    target = targets.get(platform, "").strip()
    if not target:
        raise ValueError(f"Set the {platform} target in local accounts.json before phone preparation")
    if saved_account.strip().casefold() != target.casefold():
        raise ValueError(f"Saved {platform} account differs from local accounts.json; review the account and confirm again")
    return target
