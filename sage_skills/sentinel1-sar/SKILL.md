---
name: sentinel1-sar
description: "Download Sentinel-1 SAR backscatter (VV, VH) from Microsoft Planetary Computer for a bounding box. Use when the user asks for Sentinel-1, SAR, radar, backscatter, or features that complement optical (Sentinel-2) for vegetation, biomass, canopy structure, or wetland analysis. Prefers the sentinel-1-rtc collection (terrain-corrected, proper CRS); falls back to sentinel-1-grd. Computes a per-pixel median composite of the N most-recent scenes (default 5), values clipped to [-50, 10], saved as multi-band GeoTIFF. SAR penetrates clouds — no cloud filter applied. No authentication needed."
---

# sentinel1-sar — Sentinel-1 SAR Median Composite

Downloads Sentinel-1 SAR (Synthetic Aperture Radar) data from Microsoft
Planetary Computer for a bounding box and time period. Produces a 2-band
median composite (VV, VH) saved as a single GeoTIFF.

**Why SAR adds signal beyond Sentinel-2:**

- **VH** (cross-polarized): sensitive to volume scattering inside the canopy
  → correlates with above-ground biomass and canopy structure. Strongest
  added value for canopy-height regression.
- **VV** (co-polarized): sensitive to surface roughness; helps separate
  bare soil from low vegetation.
- **Cloud-penetrating:** unlike optical, SAR works regardless of weather.

Together with Sentinel-2 spectral bands, SAR typically lifts canopy-height
R² from ~0.1 to ~0.3-0.4 for forested regions.

## Importing the helper

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sentinel1-sar")
from sentinel1_sar import fetch_sentinel1_sar
```

`pystac-client`, `planetary-computer`, `rasterio`, and `scipy` are
auto-installed on first use if not already present.

## API

```python
fetch_sentinel1_sar(
    bbox,                        # (minx, miny, maxx, maxy) EPSG:4326
    output_path,                 # destination GeoTIFF under SAGE_OUTPUT_DIR
    year=None,                   # convenience: full calendar year
    start_date=None,             # "YYYY-MM-DD"
    end_date=None,               # "YYYY-MM-DD"
    max_scenes=5,                # cap on most-recent scenes used
    polarizations=("VV", "VH"),  # default both polarizations
    fallback_grd=True,           # if RTC empty, try sentinel-1-grd
    n_workers=None,              # default min(4, cpu_count)
)
```

Returns the saved `Path`.

### Output

| Band | Description                                             |
|------|---------------------------------------------------------|
| 1    | VV (co-polarized backscatter)                           |
| 2    | VH (cross-polarized backscatter)                        |

Pixel values are **linear gamma_naught** (RTC) clipped to `[-50, 10]`. The
output GeoTIFF is in the first scene's native CRS (typically a UTM zone),
NOT EPSG:4326.

If you want SAR features in **dB** for ML (recommended — more linear
relationship with biomass), convert downstream:

```python
import numpy as np
db = 10.0 * np.log10(linear + 1e-10)
```

## Choosing dates

For canopy-height work, pick the same window as your Sentinel-2 composite
(typically growing season of the GEDI year). SAR is less seasonally
sensitive than optical, so a wider window (full year) also works.

## Full example

```python
import sys
from pathlib import Path

sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sentinel1-sar")
from sentinel1_sar import fetch_sentinel1_sar

bbox = globals().get("USER_BBOX")
output_path = Path(SAGE_OUTPUT_DIR) / "sentinel1_2024_summer.tif"

fetch_sentinel1_sar(
    bbox=bbox,
    output_path=output_path,
    start_date="2024-06-01",
    end_date="2024-09-30",
    max_scenes=5,
)
```

## Inspecting the output

```python
import rasterio
with rasterio.open(output_path) as src:
    print(f"CRS:    {src.crs}")
    print(f"Shape:  {src.height} x {src.width}")
    print(f"Bands:  {[src.descriptions[i-1] for i in range(1, src.count+1)]}")
    vv = src.read(1)
    vh = src.read(2)
```

## RTC vs GRD

| Collection            | When used                             | CRS                       |
|-----------------------|---------------------------------------|---------------------------|
| `sentinel-1-rtc`      | Default. Terrain-corrected, has CRS.  | Native UTM, in metadata   |
| `sentinel-1-grd`      | Fallback when RTC has 0 matches.      | Inferred from `proj:epsg` or bbox UTM |

RTC is preferred for terrain analysis (the geometric distortion from radar
foreshortening is corrected). GRD is the raw ground-range product and may
have missing CRS, which the helper fills in from the bbox center's UTM zone.

## Execution rules

- Save your script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc. Never chain commands with `&&`.
- Always read the bbox from the kernel variable (e.g. `globals().get("USER_BBOX")`). Do NOT hardcode coordinates.
- The output GeoTIFF path must be under `SAGE_OUTPUT_DIR`. Use a descriptive filename (e.g. `sentinel1_2024_summer.tif`).
- Do NOT re-implement the STAC search or median composite. Call `fetch_sentinel1_sar` and let it handle everything.
- The output is in UTM (or whichever scene CRS), NOT EPSG:4326. For pairing with Sentinel-2, the natural workflow is to reproject S1 to the S2 grid at feature-extraction or prediction time using `rasterio.warp.reproject` with `Resampling.bilinear`.
- For ML feature engineering, consider converting linear backscatter to dB downstream: `db = 10 * np.log10(linear + 1e-10)`.
