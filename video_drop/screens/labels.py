"""Per-language label strings. Maps say "@label:next"; this file says "Next"."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PREFIX = "@label:"


@dataclass(frozen=True)
class Labels:
    locale: str
    strings: dict

    @classmethod
    def load(cls, root: Path, locale: str) -> "Labels":
        path = Path(root) / "labels" / f"{locale}.json"
        if not path.is_file():
            raise ValueError(f"No label file for phone language {locale!r}")
        return cls(locale, json.loads(path.read_text(encoding="utf-8")))

    def text(self, value: str) -> str:
        if not value.startswith(PREFIX):
            return value
        key = value[len(PREFIX):]
        if key not in self.strings:
            raise ValueError(f"Label {key!r} is missing from {self.locale}.json")
        return self.strings[key]
