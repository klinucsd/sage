#!/usr/bin/env python3
"""Strip metadata.widgets from a notebook to remove accumulated widget-state bloat.

Usage:
    python strip_widget_state.py <notebook.ipynb>      # in-place, with .bak
    python strip_widget_state.py <in.ipynb> <out.ipynb>

Notes:
- Removes only `metadata.widgets`. Cell outputs and source are untouched.
- Live widgets disappear from saved state — re-running the cell restores them.
"""
import json
import shutil
import sys
from pathlib import Path


def strip(in_path: Path, out_path: Path) -> tuple[int, int]:
    nb = json.loads(in_path.read_text())
    before = len(json.dumps(nb))
    if "widgets" in nb.get("metadata", {}):
        del nb["metadata"]["widgets"]
    out_path.write_text(json.dumps(nb))
    after = len(json.dumps(nb))
    return before, after


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: strip_widget_state.py <in.ipynb> [out.ipynb]")
        sys.exit(1)
    inp = Path(sys.argv[1])
    if len(sys.argv) == 3:
        out = Path(sys.argv[2])
    else:
        bak = inp.with_suffix(inp.suffix + ".bak")
        shutil.copy(inp, bak)
        print(f"Backup written to {bak}")
        out = inp
    before, after = strip(inp, out)
    print(f"Before: {before/1e6:6.2f} MB")
    print(f"After:  {after/1e6:6.2f} MB  ({(before-after)/1e6:.2f} MB removed)")
    print(f"Output: {out}")


if __name__ == "__main__":
    main()
