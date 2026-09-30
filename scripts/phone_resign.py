"""Re-sign WebDriverAgent after Sideloadly so taps work: ``python scripts/phone_resign.py``.

Sideloadly signs the WebDriverAgent host app with your own Apple ID but leaves
its nested test bundle unsigned, so touch input stays dead. This script reads
the profile Apple minted back off the phone (pymobiledevice3, in a separate
process; ``pip install -r requirements-resign.txt``), re-signs the whole
unsigned ``wda/WebDriverAgent.ipa`` with go-ios, installs it, and asks the link
supervisor to start the driver. No Apple password is ever typed here: when the
phone holds no valid signature it asks you to click Start in Sideloadly.

Optional argument: a ``.mobileprovision`` file to use instead of reading the phone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from video_drop.phone import signing  # noqa: E402


def main(argv: list[str]) -> int:
    profile = Path(argv[0]) if argv else None
    if profile is not None and not profile.is_file():
        print(f"No such profile: {profile}", file=sys.stderr)
        return 2

    def progress(step: str, message: str) -> None:
        print(f"[{step}] {message}", flush=True)

    result = signing.fix_input(profile=profile, progress=progress)
    print(result["message"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
