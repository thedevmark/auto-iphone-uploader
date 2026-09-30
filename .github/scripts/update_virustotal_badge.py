"""Point the README's VirusTotal badge at a release zip's scan result."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"
BADGE_LINE = re.compile(r"^\[!\[VirusTotal[^\n]*$", re.MULTILINE)
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def badge(tag: str, sha: str, flagged: int, engines: int) -> str:
    if not SHA256.match(sha) or engines <= 0 or not 0 <= flagged <= engines:
        raise ValueError("Refusing to write a badge from an incomplete scan")
    color = "brightgreen" if flagged == 0 else "red"
    label = f"VirusTotal {tag}".replace("-", "--").replace(" ", "%20")
    message = f"{flagged}/{engines} flagged".replace(" ", "%20").replace("/", "%2F")
    return (f"[![VirusTotal {tag}: {flagged}/{engines}](https://img.shields.io/badge/{label}-{message}-{color})]"
            f"(https://www.virustotal.com/gui/file/{sha})")


def update(text: str, line: str) -> str:
    if len(BADGE_LINE.findall(text)) != 1:
        raise ValueError("README must contain exactly one VirusTotal badge line")
    return BADGE_LINE.sub(lambda _: line, text)


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
    args = parser.parse_args()
    text = README.read_text(encoding="utf-8")
    README.write_text(update(text, badge(args.tag, args.sha256, args.flagged, args.engines)),
                      encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
