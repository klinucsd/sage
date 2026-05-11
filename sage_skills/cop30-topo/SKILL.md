---
name: cop30-topo
description: "Download Copernicus COP30 30 m DEM (or SRTM / AW3D30) from OpenTopography for a bounding box, compute slope and aspect, save as 3-band GeoTIFF (elevation, slope, aspect). Use when the user asks for COP30, DEM, digital elevation model, topography, elevation, slope, aspect, hillshade, terrain features, or topographic features for ML/regression. Requires OPENTOPOGRAPHY_API_KEY in the environment (free key at portal.opentopography.org). Output reprojected to local UTM at 30 m; slope/aspect computed in meters on the square-pixel target grid."
---

# cop30-topo — Copernicus COP30 DEM + Slope + Aspect (OpenTopography)

Fetches a merged GeoTIFF from OpenTopography's GlobalDEM API for the
requested bbox, reprojects it from EPSG:4326 to the local UTM zone at
30 m, then computes slope and aspect from the gradient on the
square-pixel target grid. Produces a 3-band GeoTIFF: elevation, slope,
aspect.

**Source:** https://portal.opentopography.org/API/globaldem

**DEM types** (`demtype` argument):

| Value     | Description                                  | Native res |
|-----------|----------------------------------------------|-----------:|
| `COP30`   | Copernicus 30 m Global DEM (default)         | 30 m       |
| `SRTMGL1` | SRTM 1 arc-second                            | ~30 m      |
| `SRTMGL3` | SRTM 3 arc-second                            | ~90 m      |
| `AW3D30`  | ALOS World 3D 30 m                           | 30 m       |

**Why a topo skill matters for canopy-height ML:**

- **Elevation** sets temperature regime, growing-season length, treeline →
  strongly predicts the *upper bound* of canopy height.
- **Slope** drives soil-water availability and disturbance regime (steep
  slopes shed water, get more landslides, slower-growing forests).
- **Aspect** (north/south facing) controls solar radiation → biggest effect
  in temperate latitudes, modulates forest type and productivity.

These three signals are largely **independent** of what Sentinel-2 spectra
or Sentinel-1 SAR can see. On mixed terrain, adding them typically lifts
canopy-height R² by 0.05–0.10 on top of S2+S1.

## Authentication

OpenTopography's GlobalDEM endpoint requires a free API key. Get one at
https://portal.opentopography.org → My Account → Request API key.

Then either:

1. **Set it in `.env`** (recommended):
   ```env
   OPENTOPOGRAPHY_API_KEY=your_key_here
   ```
   The skill reads `OPENTOPOGRAPHY_API_KEY` automatically.

2. **Pass it explicitly** via the `api_key=` parameter.

## Importing the helper

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/cop30-topo")
from cop30_topo import fetch_cop30_topo
```

`requests` and `rasterio` are auto-installed on first use if not already
present.

## API

```python
fetch_cop30_topo(
    bbox,                # (minx, miny, maxx, maxy) EPSG:4326
    output_path,         # destination GeoTIFF under SAGE_OUTPUT_DIR
    demtype="COP30",     # "COP30" | "SRTMGL1" | "SRTMGL3" | "AW3D30"
    api_key=None,        # falls back to OPENTOPOGRAPHY_API_KEY env var
    target_crs=None,     # output CRS; default = local UTM zone from bbox center
    pixel_size=30.0,     # output pixel size in meters
    add_slope=True,
    add_aspect=True,
)
```

Returns the saved `Path`.

### Output bands

| Band | Description                                     | Units      |
|------|-------------------------------------------------|------------|
| 1    | elevation                                       | meters     |
| 2    | slope                                           | degrees    |
| 3    | aspect (0 = North, clockwise)                   | degrees    |

The output is in **UTM** (or the explicit `target_crs` if supplied), NOT
EPSG:4326. This is intentional — slope and aspect are only correct on a
square-pixel grid in meters. **Never compute slope/aspect on a native
EPSG:4326 DEM** — pixel anisotropy at non-equator latitudes biases the
gradient. This skill always reprojects before differentiating.

## Pixel alignment with Sentinel-2 / Sentinel-1

The output GeoTIFF is in the local UTM zone but may not pixel-align with
your Sentinel-2 / Sentinel-1 GeoTIFFs (those use the *first scene's* UTM
zone, which may differ at zone boundaries; pixel origins may also differ).

At feature-extraction or prediction time, reproject this topo file onto
the S2 grid:

```python
from rasterio.warp import reproject, Resampling
reproject(
    rasterio.band(topo_src, i), dst_array,
    src_transform=topo_src.transform, src_crs=topo_src.crs,
    dst_transform=s2_src.transform, dst_crs=s2_src.crs,
    resampling=Resampling.bilinear,
)
```

`Resampling.bilinear` is the right choice for all three bands (elevation,
slope, aspect are continuous fields).

## Loading credentials

```python
import os
from dotenv import load_dotenv
load_dotenv("/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env")
# fetch_cop30_topo will read OPENTOPOGRAPHY_API_KEY from the environment
```

Never print the API key value.

## Full example

```python
import sys, os
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/cop30-topo")
from cop30_topo import fetch_cop30_topo

load_dotenv("/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env")

bbox = globals().get("USER_BBOX")
output_path = Path(SAGE_OUTPUT_DIR) / "topo_cop30.tif"

fetch_cop30_topo(
    bbox=bbox,
    output_path=output_path,
    demtype="COP30",
    pixel_size=30,
)
```

## Inspecting the output

```python
import rasterio
with rasterio.open(output_path) as src:
    print(f"CRS:    {src.crs}")
    print(f"Shape:  {src.height} x {src.width}")
    print(f"Bands:  {[src.descriptions[i-1] for i in range(1, src.count+1)]}")
    elevation = src.read(1)
    slope     = src.read(2)
    aspect    = src.read(3)
    print(f"Elevation range: {elevation.min():.0f}–{elevation.max():.0f} m")
    print(f"Slope range:     {slope.min():.1f}–{slope.max():.1f}°")
```

## Execution rules

- Save your script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc. Never chain commands with `&&`.
- Always read the bbox from the kernel variable (e.g. `globals().get("USER_BBOX")`). Do NOT hardcode coordinates.
- Always load the OpenTopography key via `dotenv.load_dotenv()`. Do NOT hardcode or print the API key.
- The output GeoTIFF path must be under `SAGE_OUTPUT_DIR`. Use a descriptive filename (e.g. `topo_cop30.tif`).
- Do NOT re-implement the OpenTopography request, reprojection, or slope/aspect math. Call `fetch_cop30_topo` and let it handle everything.
- The output CRS is UTM (not EPSG:4326). When pairing with Sentinel-2 / Sentinel-1, reproject this file to the S2 grid using `rasterio.warp.reproject` with `Resampling.bilinear`.
- Slope is in degrees (0–90), aspect in degrees (0–360, 0=North, clockwise). For ML features, aspect is often more useful when decomposed: `sin(aspect)` and `cos(aspect)` so 0° and 360° are treated as the same direction.
