---
name: usgs-lidar
description: "USGS 3DEP LiDAR point cloud data. Use when the user wants to: download LiDAR point clouds from USGS 3DEP / EPT (Entwine Point Tile) services; query the USGS 3DEP coverage catalog; visualize point clouds in 3D; generate digital elevation models (DEMs) from ground-classified LiDAR returns. Pure data skill — Python functions only, no UI."
---

# usgs-lidar — Data Skill

This is a **data-only skill**. It exposes Python functions for fetching the
USGS 3DEP coverage catalog, filtering it by bounding box, downloading point
clouds, and rasterizing them to a DEM. It contains no widgets, maps, or
dropdowns and has no agent-runtime dependencies — usable from any Python
environment.

## ⚠️ Critical rule — NEVER plot 3DEP point cloud data on a Folium map

A single 3DEP LiDAR tile contains millions to billions of points. Rendering
them as point markers on a 2D Folium map will hang the browser, blow up
notebook size, and produce a meaningless dense blob.

- **Do NOT** save point cloud arrays (`pointclouds`, `X/Y/Z` arrays) as a
  GeoJSON of points and reference them with a `![...](...)` map tag.
- **Do NOT** convert decimated points to `Point` geometries for a Folium map.
- **Do NOT** include a 3DEP point file in a multi-layer map tag.

The only acceptable visualization for the point cloud itself is the Plotly
3D scatter in Step 4 (`fig.show()`). For 2D context on a Folium map, use
the **coverage tile footprint** (a polygon) from `fetch_coverage()`, not
the points. For a 2D raster, generate a DEM from the points and reference
the GeoTIFF instead.

## Required Libraries

`pdal`, `python-pdal`, `pyforestscan`, `laspy`, `geopandas`, `pyproj`,
`rasterio` are pre-installed in the Sage image. No install step is needed.

## Helper module

The skill ships `usgs_lidar.py` next to this file. From an agent-generated
script, add the skill directory to `sys.path` and import:

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/usgs-lidar")
from usgs_lidar import fetch_coverage, filter_by_bbox
```

Two functions:

| Function                                       | Purpose                                                                       |
|------------------------------------------------|-------------------------------------------------------------------------------|
| `fetch_coverage(color_features=True)` | Downloads `https://usgs.entwine.io/boundaries/resources.geojson` and returns an in-memory `GeoDataFrame` in EPSG:4326 with `name`, `url`, `count`, `geometry`. Keep it in memory; do not write it to a file. |
| `filter_by_bbox(coverage, bbox, max_points)`   | Given the GeoDataFrame and a bbox tuple `(minx, miny, maxx, maxy)` in EPSG:4326, returns a list of dicts `[{"name", "url", "count", "est"}]` for intersecting datasets whose estimated bbox-clipped point count is below `max_points` (default 20 million). |

## Execution rules — read before writing any code

- Save every script to a `.py` file with `write_file`, then run it with
  `python /path/to/script.py`. Never use heredoc syntax (`python << 'EOF'`).
  Never chain commands with `&&`.
- Read kernel variables via `globals().get("VAR_NAME")`. The system prompt's
  `EXISTING KERNEL VARIABLES` block tells you what's already set by previous cells.

### Reading variables across steps

Variables produced by an earlier step (e.g., `coverage`, `pointclouds`,
`bbox`, `ept_url`) live in the kernel namespace. Read them with
`globals().get("VAR_NAME")`. Commands chained with pipes, `&&`, or
environment-variable prefixes run in a subprocess that cannot see kernel
variables — keep each script self-contained and run with plain
`python /path/to/script.py`.

---

## Step 1 — Fetch the USGS 3DEP coverage catalog

Pure data step. Produces:

| Variable    | Type            | Contents                                              |
|-------------|-----------------|-------------------------------------------------------|
| `coverage`  | GeoDataFrame    | One row per USGS 3DEP dataset; EPSG:4326              |

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/usgs-lidar")
from usgs_lidar import fetch_coverage

coverage = fetch_coverage()
print(f"Loaded {len(coverage)} USGS 3DEP datasets.")
```

Keep `coverage` in memory for downstream steps. Do not write it to a file.

---

## Step 2 — Filter the catalog by a bounding box

Pure data step. Given a bbox in EPSG:4326 (e.g., from a UI widget or a
hardcoded value), return a list of intersecting datasets.

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/usgs-lidar")
from usgs_lidar import filter_by_bbox

bbox = (-122.5, 37.7, -122.3, 37.9)   # (minx, miny, maxx, maxy) in EPSG:4326
datasets = filter_by_bbox(coverage, bbox, max_points=20_000_000)
print(f"{len(datasets)} dataset(s) intersect the bbox with under 20M estimated points.")
for d in datasets[:5]:
    est = f"~{d['est']:,} pts" if d['est'] else "?"
    print(f"  {d['name']}  ({est})")
```

`datasets` is a list of dicts with keys `name`, `url`, `count`, `est`.
Pick one (or let the user pick one via whatever picker your agent provides)
and pass its `url` and the bbox to Step 3.

---

## Step 3 — Download the selected point cloud

Given a bbox and an EPT endpoint URL, downloads the point cloud via
`pyforestscan`.

### pyforestscan imports — use these exact paths, do not guess

```python
from pyforestscan.handlers   import read_lidar, create_geotiff, write_las
from pyforestscan.calculate  import assign_voxels, calculate_pad, calculate_pai, calculate_fhd, calculate_chm
from pyforestscan.process    import process_with_tiles
from pyforestscan.utils      import get_srs_from_ept
```

`get_srs_from_ept` lives in `pyforestscan.utils`, not `pyforestscan.handlers`.
If it ever fails (package version mismatch, etc.), fall back to fetching the
EPT JSON directly:

```python
import requests
ept = requests.get(ept_url, timeout=30).json()
srs = ept.get("srs", {})
ept_srs = f"{srs.get('authority','EPSG')}:{srs.get('horizontal','3857')}"
```

### Step 3 script

Inputs: `bbox` (4-tuple in EPSG:4326) and `ept_url` (string from Step 2's
chosen dataset). Replace the names below with whatever your agent stores
them under.

```python
import numpy as np
from pyproj import Transformer
from pyforestscan.handlers import read_lidar
from pyforestscan.utils import get_srs_from_ept

bbox    = globals().get("bbox")        # or whatever name your agent picked
ept_url = globals().get("ept_url")
if not bbox or not ept_url:
    raise ValueError("bbox and ept_url must be set before downloading point clouds")

ept_srs = get_srs_from_ept(ept_url)
transformer = Transformer.from_crs("EPSG:4326", ept_srs, always_xy=True)
minx, miny = transformer.transform(bbox[0], bbox[1])
maxx, maxy = transformer.transform(bbox[2], bbox[3])
bounds = ([minx, maxx], [miny, maxy])

print(f"Downloading point cloud from {ept_url} ...")
pointclouds = read_lidar(ept_url, ept_srs, bounds, hag=True)
print(f"Downloaded {len(pointclouds)} point arrays.")
globals()["pointclouds"] = pointclouds
```

`hag=True` computes Height Above Ground (normalized elevation relative to
ground). Set `hag=False` for raw absolute elevation.

---

## Step 4 — Interactive 3D visualization

Renders the point cloud as a Plotly 3D scatter plot colored by elevation.
Decimates to ~250,000 points for browser performance.

Call `fig.show()` directly — do NOT use a `![...](...)` map tag for Plotly figures.

```python
import plotly.graph_objects as go
import numpy as np

pointclouds = globals().get("pointclouds")
if pointclouds is None:
    raise ValueError("pointclouds not set — run Step 3 (download) first.")

all_x, all_y, all_z = [], [], []
for pc in pointclouds:
    all_x.append(pc['X'] if 'X' in pc.dtype.names else pc.x)
    all_y.append(pc['Y'] if 'Y' in pc.dtype.names else pc.y)
    all_z.append(pc['Z'] if 'Z' in pc.dtype.names else pc.z)

x = np.concatenate(all_x); y = np.concatenate(all_y); z = np.concatenate(all_z)
x -= np.min(x); y -= np.min(y)

total = len(x)
step = max(1, total // 250_000)
x, y, z = x[::step], y[::step], z[::step]
print(f"Rendering {len(x):,} of {total:,} points (step={step})")

fig = go.Figure(data=[go.Scatter3d(
    x=x, y=y, z=z, mode='markers',
    marker=dict(size=1.5, color=z, colorscale='earth',
                colorbar=dict(title="Elevation", titleside="right"), opacity=1.0)
)])
fig.update_layout(margin=dict(l=0, r=0, b=0, t=0), scene=dict(aspectmode='data'))
fig.show()
```

`pointclouds` is a list of structured numpy arrays. Field names may be
uppercase (`X`, `Y`, `Z`) or lowercase depending on the dataset; the snippet
above handles both.

---

## Step 5 — 1-m DEM with hillshade overlay

Reads `pointclouds` (set by Step 3). Filters ground returns (LAS class 2),
rasterises to a 1-m DEM, writes a georeferenced GeoTIFF, and generates a
local hillshade via `gdaldem`. Also writes a `hillshade.wms.json` sidecar so
the USGS 3DEP WMS service appears as a background context layer on the
combined Folium map.

### Y-axis orientation — the classic rasterisation trap

`rasterio`'s `from_origin(min_x, max_y, dx, dy)` treats row 0 as `max_y`
(north-up). If you index the array with `y_idx = ((y - min_y) / dx).astype(int)`,
row 0 of the array is at `min_y` (south). Writing that array under a north-up
transform produces a **vertically flipped raster**. Use `y_idx` from `max_y`:

```python
y_idx = ((max_y - y) / 1.0).astype(int)   # row 0 = north
```

Verify by comparing the hillshade against Google Earth for the same bbox — a
correct hillshade with `-az 315` (NW lighting) has north-facing slopes in shadow.

### Step 5 script

```python
import numpy as np, json, subprocess, os
from pathlib import Path
import rasterio
from rasterio.transform import from_origin
from pyforestscan.utils import get_srs_from_ept

pointclouds = globals().get("pointclouds")
bbox        = globals().get("bbox")        # 4-tuple in EPSG:4326
ept_url     = globals().get("ept_url")
if pointclouds is None:
    raise ValueError("pointclouds not found — run Step 3 first")
if not bbox or not ept_url:
    raise ValueError("bbox / ept_url not set")

output_dir = Path(globals().get("OUTPUT_DIR", "/tmp"))   # agent's output directory
output_dir.mkdir(parents=True, exist_ok=True)

# Extract ground-classified points (LAS class 2)
ground = []
for pc in pointclouds:
    cls_field = 'Classification' if 'Classification' in pc.dtype.names else 'classification'
    ground.append(pc[pc[cls_field] == 2])
all_ground = np.concatenate(ground)

x_field = 'X' if 'X' in all_ground.dtype.names else 'x'
y_field = 'Y' if 'Y' in all_ground.dtype.names else 'y'
z_field = 'Z' if 'Z' in all_ground.dtype.names else 'z'
x = all_ground[x_field]; y = all_ground[y_field]; z = all_ground[z_field]

min_x, max_x = float(np.min(x)), float(np.max(x))
min_y, max_y = float(np.min(y)), float(np.max(y))
grid_w = int(np.ceil(max_x - min_x)) + 1
grid_h = int(np.ceil(max_y - min_y)) + 1

# Rasterise with Y-flip-correct indexing
x_idx = ((x - min_x) / 1.0).astype(int)
y_idx = ((max_y - y) / 1.0).astype(int)   # row 0 = north
valid = (x_idx >= 0) & (x_idx < grid_w) & (y_idx >= 0) & (y_idx < grid_h)
cell = y_idx[valid] * grid_w + x_idx[valid]
z_ok = z[valid]
sum_z = np.bincount(cell, weights=z_ok, minlength=grid_w * grid_h)
cnt   = np.bincount(cell, minlength=grid_w * grid_h)
with np.errstate(divide='ignore', invalid='ignore'):
    dem = (sum_z / cnt).reshape((grid_h, grid_w))
dem[cnt.reshape((grid_h, grid_w)) == 0] = np.nan

# Write DEM
ept_srs = get_srs_from_ept(ept_url)
dem_tif = str(output_dir / "dem_1m.tif")
with rasterio.open(
    dem_tif, 'w', driver='GTiff',
    height=grid_h, width=grid_w, count=1, dtype=np.float32,
    crs=ept_srs, transform=from_origin(min_x, max_y, 1.0, 1.0),
    nodata=np.nan,
) as dst:
    dst.write(dem.astype(np.float32), 1)

# Local hillshade
hillshade_tif = str(output_dir / "hillshade_1m.tif")
subprocess.run(
    ["gdaldem", "hillshade", "-az", "315", "-alt", "45",
     "-compute_edges", dem_tif, hillshade_tif],
    check=True, capture_output=True, text=True,
)

# Register USGS 3DEP WMS as a wide-area context layer
wms_json = output_dir / "hillshade.wms.json"
wms_json.write_text(json.dumps({
    "url": "https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WMSServer",
    "layers": "3DEPElevation:Hillshade Gray",
    "name": "USGS 3DEP Hillshade (Background)",
    "bbox": [bbox[1], bbox[0], bbox[3], bbox[2]],
    "opacity": 0.5,
}, indent=2))

print(f"Wrote {dem_tif}")
print(f"Wrote {hillshade_tif}")
print(f"Wrote {wms_json}")
```

### USGS 3DEP WMS layers — use these exact layer names

`3DEPElevation:Hillshade` (no qualifier) does NOT exist and renders blank
tiles. Valid names:

| Layer (use verbatim in `"layers"`)         | Visual                                              |
|--------------------------------------------|-----------------------------------------------------|
| `3DEPElevation:Hillshade Gray`             | Traditional single-source grayscale hillshade       |
| `3DEPElevation:Hillshade Gray-Stretch`     | Gray hillshade with contrast stretched              |
| `3DEPElevation:Hillshade Multidirectional` | Dramatic multi-source lighting                      |
| `3DEPElevation:Hillshade Elevation Tinted` | Hillshade colored by elevation                      |
| `3DEPElevation:Aspect Degrees`             | Aspect angle in degrees                             |
| `3DEPElevation:Slope Degrees`              | Slope angle in degrees                              |
| `3DEPElevation:Contour 25`                 | 25-unit contour lines                               |
| `3DEPElevation`                            | The raw DEM elevation grid                          |

URL for all of these: `https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WMSServer`.

---

## Notes

- This skill describes data fetching, filtering, downloading, and processing.
  It contains no widgets, maps, or dropdowns. Pair it with whatever UI
  layer your agent provides for area selection and dataset picking.
- Steps 3–5 use placeholder variable names (`bbox`, `ept_url`, `pointclouds`,
  `OUTPUT_DIR`). The agent may use different names — adapt the
  `globals().get(...)` calls accordingly.
