"""Select a local export folder without exposing arbitrary path entry in the browser."""

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

parser = argparse.ArgumentParser()
parser.add_argument("--initial-dir", type=Path, required=True)
args = parser.parse_args()

root = Tk()
root.withdraw()
root.attributes("-topmost", True)
try:
    chosen = filedialog.askdirectory(parent=root, title="Choose export folder",
                                     initialdir=str(args.initial_dir))
finally:
    root.destroy()
print(json.dumps({"path": str(Path(chosen).resolve()) if chosen else ""}), flush=True)
