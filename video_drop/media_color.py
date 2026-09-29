"""Choose Edits SDR/HDR export mode from the source video's metadata."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
SDR_TRANSFERS = {"bt709", "smpte170m", "bt470bg", "iec61966-2-1"}


def edits_color_mode(path: Path) -> str:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=color_transfer,color_primaries,color_space", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1:
        raise ValueError(f"Expected one video stream in {path}")
    metadata = streams[0]
    transfer = metadata.get("color_transfer", "").casefold()
    primaries = metadata.get("color_primaries", "").casefold()
    space = metadata.get("color_space", "").casefold()
    if transfer in HDR_TRANSFERS:
        if primaries not in ("", "unknown", "bt2020"):
            raise ValueError(f"Conflicting HDR color metadata: {metadata}")
        return "HDR"
    if transfer in SDR_TRANSFERS:
        if primaries == "bt2020" or space in ("bt2020nc", "bt2020c"):
            raise ValueError(f"Conflicting SDR color metadata: {metadata}")
        return "SDR"
    raise ValueError(f"Unknown source color transfer {transfer!r}; choose Edits mode manually")
