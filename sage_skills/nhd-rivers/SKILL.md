---
name: nhd-rivers
description: "ALWAYS use this skill to get river or stream geometry in the USA. Never extract rivers from DEM/elevation data — that approach is inaccurate. Use this skill for any request involving a river: find the Carson River, show the river channel, get the river centerline, overlay the river on the map, sample elevation along the river, find the main channel. Returns official vector geometries from the National Hydrography Dataset via pynhd."
---

# NHD Rivers Skill

## CRITICAL — Do NOT extract rivers from DEM data

Never use DEM elevation values, valley detection, local minima, or any
raster-analysis method to find a river channel. Always use the functions
defined below. This applies even when a DEM is already available.

## Required Libraries

```python
import subprocess
subprocess.run(["pip", "install", "-q", "pynhd", "pygeoutils", "rioxarray"], check=True)

import pynhd
import pygeoutils
import geopandas as gpd
import numpy as np
from shapely import ops
import shapely
```

---

## When to Use

- When you need to fetch NHD flowlines for a bbox, use Example 1
- When you need to get the main channel of a river, use Example 2
- When you need both (the usual case), use Example 3

---

## Usage

This skill defines two functions. Copy both function definitions into your
notebook first, then call them as shown in the examples. Do not rewrite the
function bodies.

---

## Function Definitions — copy both verbatim

```python
def fetch_flowlines(dem_bbox, margin=0.5):
    """
    Fetch NHD flowlines using a bbox expanded from the DEM extent.

    Parameters
    ----------
    dem_bbox : tuple  (west, south, east, north) in EPSG:4326
    margin   : float  degrees to expand on each side (default 0.5).
                      Never pass the raw DEM bbox without margin — it is too
                      small and will cause linemerge to return a MultiLineString.

    Returns
    -------
    flw : GeoDataFrame  all NHD flowlines in the expanded bbox, EPSG:4326
    """
    river_bbox = (
        dem_bbox[0] - margin,
        dem_bbox[1] - margin,
        dem_bbox[2] + margin,
        dem_bbox[3] + margin,
    )
    wd = pynhd.WaterData("nhdflowline_network")
    flw = wd.bybox(river_bbox)
    print(f"fetch_flowlines: {len(flw)} segments fetched for bbox {river_bbox}")
    return flw


def get_main_channel(flw, river_name):
    """
    Filter flowlines to the main channel and merge into a single LineString
    sorted in correct flow order (headwaters → outlet).

    Parameters
    ----------
    flw        : GeoDataFrame  output of fetch_flowlines()
    river_name : str  full lowercase official name, e.g. 'carson river'.
                      Partial names (e.g. 'carson') also match forks and
                      tributaries — always use the full official name.

    Returns
    -------
    river_line   : LineString   single merged line in flow order, EPSG:4326
    main_channel : GeoDataFrame filtered segments with all NHD attributes intact
                   (hydroseq, levelpathi, streamorde, gnis_name, crs, etc.)

    Both outputs are required inputs to sample_elevation() and
    plot_river_on_dem() in the py3dep-dem skill.
    """
    # Primary: exact name match
    main_channel = flw[flw['gnis_name'].str.lower() == river_name].copy()

    # Fallback: partial match filtered to highest stream order + levelpathi tiebreak
    if len(main_channel) == 0:
        candidates = flw[flw['gnis_name'].str.lower().str.contains(river_name, na=False)].copy()
        if len(candidates) == 0:
            raise ValueError(
                f"No NHD flowlines found matching '{river_name}'. "
                "Check spelling or increase margin in fetch_flowlines()."
            )
        max_order = candidates['streamorde'].max()
        main_channel = (
            candidates[candidates['streamorde'] == max_order]
            .sort_values('levelpathi')
            .copy()
        )

    assert len(main_channel) > 0, f"main_channel is empty for '{river_name}'"

    # Sort by hydroseq BEFORE merging — arbitrary order causes elevation spikes.
    # This applies even when loading from a saved file: file row order ≠ flow order.
    main_channel = main_channel.sort_values('hydroseq', ascending=True)
    main_channel_lines = main_channel.explode(index_parts=False).reset_index(drop=True)
    river_line = ops.linemerge(main_channel_lines.geometry.tolist())

    if river_line.geom_type != 'LineString':
        raise ValueError(
            f"linemerge returned {river_line.geom_type} — segments not fully connected. "
            "Increase margin in fetch_flowlines() to capture all connecting segments."
        )

    print(f"get_main_channel: {len(main_channel)} segments → "
          f"{river_line.geom_type}, ~{river_line.length * 111:.0f} km")
    return river_line, main_channel
```

---

## Example 1 — Fetch flowlines only

```python
dem_bbox = (-119.59, 39.24, -119.47, 39.30)   # replace with actual DEM bbox
flw = fetch_flowlines(dem_bbox)
```

---

## Example 2 — Get main channel from already-fetched flowlines

```python
river_line, main_channel = get_main_channel(flw, 'carson river')
```

---

## Example 3 — Full pipeline (fetch + main channel)

```python
dem_bbox = (-119.59, 39.24, -119.47, 39.30)   # replace with actual DEM bbox

flw                      = fetch_flowlines(dem_bbox)
river_line, main_channel = get_main_channel(flw, 'carson river')

# river_line and main_channel are now ready to pass to:
#   sample_elevation(river_line, main_channel, dem)    — py3dep-dem skill
#   plot_river_on_dem(dem, main_channel, ...)           — py3dep-dem skill
```

---

## Notes

- `get_main_channel()` always sorts by `hydroseq` before merging. Do not re-sort or re-merge `main_channel` outside the function — you will break the flow order.
- `river_line` is in EPSG:4326 (degrees). Do not extract coordinates from it directly for distance calculations — use `sample_elevation()` in the py3dep-dem skill, which reprojects to UTM automatically.
- `fetch_flowlines()` expands the DEM bbox by 0.5° by default. If `get_main_channel()` raises a `MultiLineString` error, increase `margin` (e.g. `fetch_flowlines(dem_bbox, margin=1.0)`).
- `hydroseq`: lower = closer to outlet, higher = headwaters.
- `levelpathi`: lowest value = main stem. Used as tiebreaker when `streamorde` ties.
- `streamorde`: Strahler stream order — higher = larger river.
