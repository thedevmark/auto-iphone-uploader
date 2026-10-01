"""Repack appium's unsigned ``WebDriverAgentRunner-Runner.zip`` as an ``.ipa``.

The GitHub release zip holds ``WebDriverAgentRunner-Runner.app/`` at its root.
An .ipa is the same bundle under ``Payload/``, which is what Sideloadly and
``ios sign app`` expect. Entry names keep forward slashes and the original
permissions; ``__MACOSX`` resource forks are dropped. Nothing is signed here.

    python -m video_drop.wda_ipa WebDriverAgentRunner-Runner.zip WebDriverAgent.ipa
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

APP_DIR = "WebDriverAgentRunner-Runner.app/"


def repack(source: Path, dest: Path) -> int:
    """Write ``dest`` from ``source``; returns the number of entries written."""
    with zipfile.ZipFile(source) as zin:
        # Resource forks and the debug symbols (.dSYM) are not part of the app bundle.
        entries = [info for info in zin.infolist()
                   if not info.filename.startswith("__MACOSX/") and ".dSYM/" not in info.filename]
        if not any(info.filename.startswith(APP_DIR) for info in entries):
            raise ValueError(f"{source} does not contain {APP_DIR}")
        if any(info.filename.startswith("Payload/") for info in entries):
            raise ValueError(f"{source} is already an .ipa layout")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in entries:
                target = zipfile.ZipInfo("Payload/" + info.filename, date_time=info.date_time)
                target.external_attr = info.external_attr
                target.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(target, zin.read(info.filename))
    return len(entries)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m video_drop.wda_ipa <WebDriverAgentRunner-Runner.zip> <WebDriverAgent.ipa>",
              file=sys.stderr)
        return 2
    try:
        count = repack(Path(argv[0]), Path(argv[1]))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"wrote {argv[1]} ({count} entries under Payload/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
