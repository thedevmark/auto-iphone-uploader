"""Save the iPhone passcode for automation into this app's .env (PHONE_PASSCODE).

You type it at a hidden prompt; it is never printed. Run from the app folder:

    python scripts/set_passcode.py
"""

from __future__ import annotations

import getpass
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"


def save(passcode: str, env: Path = ENV) -> None:
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    lines = [line for line in lines if not line.startswith("PHONE_PASSCODE=")]
    lines.append(f"PHONE_PASSCODE={passcode}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    first = getpass.getpass("iPhone passcode (hidden): ").strip()
    if not first.isdigit() or not 4 <= len(first) <= 8:
        raise SystemExit("The passcode should be 4-8 digits; nothing was saved.")
    if getpass.getpass("Type it again: ").strip() != first:
        raise SystemExit("They did not match; nothing was saved.")
    save(first)
    print(f"Saved PHONE_PASSCODE to {ENV}")


if __name__ == "__main__":
    main()
