"""Publish a release zip's VirusTotal result for the README badge.

The README's badge is a shields.io endpoint badge that reads `virustotal.json` from the
repository's `badges` branch, and links to `virustotal.md` there, which links the scan.
The release workflow writes both files to that branch, so a release never needs a push
to `main` (which only accepts merges whose tests passed).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REPO = "thedevmark/auto-iphone-uploader"
BADGE_URL = f"https://raw.githubusercontent.com/{REPO}/badges/virustotal.json"
README_BADGE = (f"[![VirusTotal](https://img.shields.io/endpoint?url={BADGE_URL.replace(':', '%3A').replace('/', '%2F')})]"
                f"(https://github.com/{REPO}/blob/badges/virustotal.md)")


def endpoint(tag: str, sha: str, flagged: int, engines: int) -> dict:
    """shields.io endpoint JSON for one scanned release zip."""
    if not SHA256.match(sha) or engines <= 0 or not 0 <= flagged <= engines:
        raise ValueError("Refusing to write a badge from an incomplete scan")
    return {"schemaVersion": 1, "label": f"VirusTotal {tag}", "message": f"{flagged}/{engines} flagged",
            "color": "brightgreen" if flagged == 0 else "red"}


def report_page(tag: str, sha: str, flagged: int, engines: int) -> str:
    endpoint(tag, sha, flagged, engines)  # same validation
    return (f"# VirusTotal: {tag}\n\n"
            f"`auto-iphone-uploader-{tag}.zip` (SHA-256 `{sha}`): {flagged} of {engines} engines flagged it.\n\n"
            f"[Full report on VirusTotal](https://www.virustotal.com/gui/file/{sha})\n")


def verdict(report: str) -> str:
    """Return "flagged engines" from a VirusTotal file report, or "" if none yet."""
    try:
        stats = json.loads(report)["data"]["attributes"]["last_analysis_stats"]
        flagged = stats["malicious"] + stats["suspicious"]
        return f"{flagged} {flagged + stats['undetected'] + stats['harmless']}"
    except (ValueError, KeyError, TypeError):
        return ""


def main() -> None:
    if sys.argv[1:] == ["stats"]:
        print(verdict(sys.stdin.read()))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("sha256")
    parser.add_argument("flagged", type=int)
    parser.add_argument("engines", type=int)
    parser.add_argument("out", type=Path, help="checkout of the badges branch")
    args = parser.parse_args()
    data = endpoint(args.tag, args.sha256, args.flagged, args.engines)
    (args.out / "virustotal.json").write_text(json.dumps(data) + "\n", encoding="utf-8", newline="")
    (args.out / "virustotal.md").write_text(report_page(args.tag, args.sha256, args.flagged, args.engines),
                                            encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
