"""Select a local export folder without exposing arbitrary path entry in the browser."""

import argparse
import json
from pathlib import Path
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
