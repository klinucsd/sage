---
name: sentinel2-l2a
description: "Download Sentinel-2 L2A multispectral imagery from Microsoft Planetary Computer for a bounding box. Use when the user asks for Sentinel-2 data, Sentinel imagery, multispectral satellite data, NDVI, vegetation index, optical remote sensing, or canopy/forest spectral analysis. Retrieves up to N most-recent scenes filtered by cloud cover, computes a per-band median composite at configurable resolution, includes 10 spectral bands (visible, red-edge, NIR, SWIR) plus NDVI. Saves as multiband GeoTIFF. No authentication needed."
---

# sentinel2-l2a — Sentinel-2 L2A Median Composite

Searches the `sentinel-2-l2a` collection on Microsoft Planetary Computer for
scenes intersecting a bounding box and time period, filters by cloud cover,
takes the N most recent scenes, windows each to the bbox, resamples to a
target resolution, and computes a per-band **nanmedian composite**. Output is
a single multiband GeoTIFF in the first scene's UTM CRS.

The median composite is the right shape for downstream ML (canopy-height
regression, land-cover classification): it suppresses per-scene cloud /
shadow / atmospheric noise and gives a single clean reference image per
band. For visual inspection of a single date you can pass `max_scenes=1`.

## Importing the helper

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sentinel2-l2a")
from sentinel2_l2a import fetch_sentinel2_l2a
```

`pystac-client`, `planetary-computer`, `rasterio`, and `scipy` are
auto-installed on first use if not already present.

## API

```python
fetch_sentinel2_l2a(
    bbox,                    # (minx, miny, maxx, maxy) EPSG:4326 — read from kernel var
    output_path,             # destination GeoTIFF path under SAGE_OUTPUT_DIR
    year=None,               # convenience: full calendar year
    start_date=None,         # "YYYY-MM-DD"  — used together with end_date
    end_date=None,           # "YYYY-MM-DD"
    max_cloud_cover=20.0,    # eo:cloud_cover < this percent
    max_scenes=10,           # cap on most-recent scenes used in composite
    resolution=30,           # target meters: 10, 20, 30, or 60
    bands=None,              # list of band names; default = canonical 10
    add_ndvi=True,           # adds NDVI = (B08 - B04) / (B08 + B04)
    n_workers=None,          # default min(4, cpu_count); ThreadPoolExecutor
)
```

Returns the saved `Path`.

### Default bands (in order)

| Band | Native res | Description           |
|------|------------|-----------------------|
| B02  | 10 m       | Blue                  |
| B03  | 10 m       | Green                 |
| B04  | 10 m       | Red                   |
| B05  | 20 m       | Red-edge 1            |
| B06  | 20 m       | Red-edge 2            |
| B07  | 20 m       | Red-edge 3            |
| B08  | 10 m       | NIR                   |
| B8A  | 20 m       | Red-edge 4            |
| B11  | 20 m       | SWIR 1                |
| B12  | 20 m       | SWIR 2                |
| NDVI | derived    | (B08 − B04)/(B08 + B04), if `add_ndvi=True` |

All bands are resampled to the same `resolution` so the output is a single
aligned 3D stack. The output GeoTIFF's CRS is the first valid scene's
native UTM (not EPSG:4326) — this avoids an extra reprojection step and
keeps pixels on the original sensor grid.

## Resolution and file size

| Resolution | Use case                              | Size for ~1° bbox |
|------------|---------------------------------------|-------------------|
| 10 m       | High-detail mapping, per-tree         | ~9× of 30 m       |
| 20 m       | Match red-edge/SWIR native res        | ~2.25× of 30 m    |
| 30 m       | Recommended for canopy-height ML      | baseline          |
| 60 m       | Continental-scale screening           | ~0.25× of 30 m    |

## Choosing dates

GEDI shots are time-stamped; for a regression that maps Sentinel-2 → GEDI
canopy height, the Sentinel-2 composite should overlap the GEDI acquisition
season. A reasonable default is the same calendar year (`year=2024`). For
finer alignment use `start_date` / `end_date` (e.g. `start_date="2024-06-01"`,
`end_date="2024-09-30"` to capture peak growing season).

## Full example

```python
import sys
from pathlib import Path

sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sentinel2-l2a")
from sentinel2_l2a import fetch_sentinel2_l2a

bbox = globals().get("USER_BBOX")          # (minx, miny, maxx, maxy)
output_path = Path(SAGE_OUTPUT_DIR) / "sentinel2_2024_summer.tif"

fetch_sentinel2_l2a(
    bbox=bbox,
    output_path=output_path,
    start_date="2024-06-01",
    end_date="2024-09-30",
    max_cloud_cover=20,
    max_scenes=10,
    resolution=30,
)
```

## Inspecting the output

```python
import rasterio
with rasterio.open(output_path) as src:
    print(f"CRS:       {src.crs}")
    print(f"Shape:     {src.height} x {src.width}")
    print(f"Bands:     {[src.descriptions[i-1] for i in range(1, src.count+1)]}")
    rgb = src.read([3, 2, 1])              # B04, B03, B02 — true color
```

## Execution rules

- Save your script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc. Never chain commands with `&&`.
- Always read the bbox from the kernel variable (e.g. `globals().get("USER_BBOX")`). Do NOT hardcode coordinates.
- The output GeoTIFF path must be under `SAGE_OUTPUT_DIR`. Use a descriptive filename (e.g. `sentinel2_2024_summer.tif`).
- Do NOT re-implement the STAC search, signing, windowing, or median composite. Call `fetch_sentinel2_l2a` and let it handle everything.
- For canopy-height ML / vegetation analysis, prefer the default 10 bands + NDVI. Smaller band lists are fine when the user asks for "RGB only" or similar.
- The output is in the first scene's UTM CRS, not EPSG:4326. This is intentional — pixels stay aligned to the sensor grid. Reproject downstream if you need a different CRS.
