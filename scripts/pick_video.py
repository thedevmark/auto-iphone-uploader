"""Return a local video path selected in a native Windows file dialog."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The installer's bundled Python keeps Tcl/Tk's script libraries next to python.exe.
for _var, _dir in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
    if (Path(sys.executable).parent / _dir).is_dir():
        os.environ.setdefault(_var, str(Path(sys.executable).parent / _dir))
from tkinter import Tk, filedialog


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askopenfilename(
            parent=root,
            title="Choose the finished video",
            initialdir=str(args.initial_dir),
            filetypes=[("Video files", "*.mp4 *.mov *.webm *.m4v"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    print(json.dumps({"path": chosen}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
