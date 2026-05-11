---
name: gedi-l2a
description: "Download NASA GEDI L2A canopy height data for a bounding box and time period. Use when the user asks for GEDI data, canopy height, forest height, tree height, or lidar canopy measurements. Requires EARTHDATA_USERNAME and EARTHDATA_PASSWORD credentials. Extracts quality-filtered shots with rh25–rh100, elev_highestreturn, elev_lowestmode, and sensitivity fields."
---

# gedi-l2a — GEDI L2A Canopy Height Download

Downloads NASA GEDI L2A data for a user-specified bounding box and time period.
Uses `earthaccess` for NASA Earthdata login, granule search, and batch download.
HDF5 files are downloaded to a temp directory, canopy-height fields extracted,
then deleted — no streaming (streaming via h5py remote is too slow).

## Quality filter applied per shot

```
quality_flag == 1  AND  degrade_flag == 0  AND  0 < rh98 < 130 m
```

## Importing the helper

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/gedi-l2a")
from gedi_l2a import fetch_gedi_l2a
```

`earthaccess` and `h5py` are auto-installed on first use if not present.

## API

```python
fetch_gedi_l2a(
    bbox,                  # (minx, miny, maxx, maxy) EPSG:4326 — read from kernel var
    output_path,           # destination CSV path (under SAGE_OUTPUT_DIR)
    earthdata_user=None,   # or set EARTHDATA_USERNAME env var
    earthdata_pass=None,   # or set EARTHDATA_PASSWORD env var
    year=None,             # e.g. 2024 — downloads the full calendar year
    start_date=None,       # "YYYY-MM-DD" — use instead of year for custom ranges
    end_date=None,         # "YYYY-MM-DD"
    batch_size=8,          # granules downloaded per batch
)
```

Returns a `pd.DataFrame` and writes a CSV to `output_path`.

### Output columns

| Column | Description |
|---|---|
| `latitude` / `longitude` | Shot ground location (EPSG:4326) |
| `elev_highestreturn` | Canopy top elevation, m above WGS84 ellipsoid |
| `elev_lowestmode` | Ground elevation, m above WGS84 ellipsoid |
| `rh25`, `rh50`, `rh75`, `rh95`, `rh98`, `rh100` | Relative height at percentile, m above ground |
| `sensitivity` | Canopy sensitivity (0–1) |
| `quality_flag` | 1 = good quality (all rows have 1 after filter) |
| `degrade_flag` | 0 = not degraded (all rows have 0 after filter) |
| `beam` | GEDI beam identifier |
| `shot_number` | Unique shot ID |
| `delta_time` | Seconds since 2018-01-01 00:00:00 UTC |

## Loading credentials

Read EARTHDATA_USERNAME and EARTHDATA_PASSWORD from `.env` using dotenv, then
pass them to `fetch_gedi_l2a`. Never print credential values.

```python
import os
from dotenv import load_dotenv
load_dotenv("/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env")
earthdata_user = os.environ.get("EARTHDATA_USERNAME")
earthdata_pass = os.environ.get("EARTHDATA_PASSWORD")
```

## Full example

```python
import sys, os
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/gedi-l2a")
from gedi_l2a import fetch_gedi_l2a

# Load credentials
load_dotenv("/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env")
earthdata_user = os.environ.get("EARTHDATA_USERNAME")
earthdata_pass = os.environ.get("EARTHDATA_PASSWORD")

# Read bbox drawn by user
bbox = globals().get("GEDI_BBOX")      # (minx, miny, maxx, maxy)

output_path = Path(SAGE_OUTPUT_DIR) / "gedi_canopy.csv"

df = fetch_gedi_l2a(
    bbox=bbox,
    output_path=output_path,
    earthdata_user=earthdata_user,
    earthdata_pass=earthdata_pass,
    year=2024,
)
print(df.head())
print(f"Columns: {list(df.columns)}")
```

## Execution rules

- Save your script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc. Never chain commands with `&&`.
- Always read the bbox from the kernel variable (e.g. `globals().get("GEDI_BBOX")`). Do NOT hardcode coordinates.
- Always load credentials via `dotenv.load_dotenv()`. Do NOT hardcode or print credential values.
- The output CSV path must be under `SAGE_OUTPUT_DIR`. Use a descriptive filename like `gedi_canopy_2024.csv`.
- Do NOT re-implement the download/extract logic. Call `fetch_gedi_l2a` and let it handle everything.
- `batch_size` controls memory pressure. For small bboxes the default (8) is fine. If the bbox is very large (hundreds of granules), consider reducing to 4.
- The function prints progress per batch. Do NOT add your own download loops around it.
