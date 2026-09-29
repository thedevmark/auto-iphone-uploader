"""Local, nonsecret inventory for one attached iPhone.

Presence and account identity are distinct. An installed app remains unverified
until its own UI shows the intended account; every upload checks again.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .phone_ui import PhoneLayout


APP_BUNDLES = {
    "onedrive": "com.microsoft.skydrive",
    "youtube": "com.google.ios.youtube",
    "edits": "com.burbn.basel",
    "instagram": "com.burbn.instagram",
    "facebook": "com.facebook.Facebook",
    "threads": "com.burbn.barcelona",
    "tiktok": "com.zhiliaoapp.musically",
}


def build_profile(screen_info: dict, installed_apps: list[dict],
                  observed_accounts: dict[str, dict] | None = None,
                  targets: dict[str, str] | None = None) -> dict:
    layout = PhoneLayout.from_info(screen_info)
    installed = {app.get("bundle_id") for app in installed_apps}
    observed_accounts = observed_accounts or {}
    targets = targets or {}
    return {
        "schema": 1,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "screenPoints": {"width": layout.width, "height": layout.height},
        "apps": {
            name: {
                "bundleId": bundle,
                "installed": bundle in installed,
                "account": observed_accounts.get(name, {}).get("selected", ""),
                "availableAccounts": observed_accounts.get(name, {}).get("available", []),
                **({"uploadQuality": observed_accounts.get(name, {}).get("uploadQuality", {"status": "unverified"})}
                   if name == "youtube" else {}),
                "accountVerified": bool(observed_accounts.get(name, {}).get("selected")),
                "targetAccount": targets.get("instagram" if name == "edits" else name, ""),
                "targetMatched": bool(observed_accounts.get(name, {}).get("selected"))
                    and observed_accounts[name]["selected"].casefold()
                    == targets.get("instagram" if name == "edits" else name, "").casefold(),
            }
            for name, bundle in APP_BUNDLES.items()
        },
    }
