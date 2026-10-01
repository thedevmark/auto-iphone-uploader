"""Save the iPhone passcode for automation into this app's .env, encrypted.

You type it at a hidden prompt; it is never printed. It is stored as
PHONE_PASSCODE_DPAPI: encrypted with Windows DPAPI for your Windows account, so only
you, on this PC, can read it back. A plain PHONE_PASSCODE line is removed. Run from
the app folder:

    python scripts/set_passcode.py
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from video_drop import secret_store  # noqa: E402

ENV = Path(__file__).resolve().parent.parent / ".env"


def save(passcode: str, env: Path = ENV) -> None:
    token = secret_store.protect(passcode)  # DPAPI: only this Windows user on this PC can read it
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [line for line in lines if not line.startswith(("PHONE_PASSCODE=", f"{secret_store.KEY}="))]
    lines.append(f"{secret_store.KEY}={token}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    first = getpass.getpass("iPhone passcode (hidden): ").strip()
    if not first.isdigit() or not 4 <= len(first) <= 8:
        raise SystemExit("The passcode should be 4-8 digits; nothing was saved.")
    if getpass.getpass("Type it again: ").strip() != first:
        raise SystemExit("They did not match; nothing was saved.")
    save(first)
    print(f"Saved the passcode to {ENV}, encrypted for your Windows account")


if __name__ == "__main__":
    main()
