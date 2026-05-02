---
name: usgs-lidar
description: "USGS 3DEP LiDAR point cloud data. Use when the user wants to: download LiDAR point clouds from USGS 3DEP / EPT (Entwine Point Tile) services; query the USGS 3DEP coverage catalog; visualize point clouds in 3D; generate digital elevation models (DEMs) from ground-classified LiDAR returns. Pure data skill — no UI. If the user wants an interactive coverage map, area selector, or dataset picker, the agent should chain this with the generic UI skills sage-bbox-map (for bounding-box selection) and sage-dropdown (for dataset selection)."
---

# usgs-lidar — Data Skill

This is a **data-only skill**. It contains no widgets, maps, or dropdowns.
The data primitives — fetching the USGS 3DEP coverage catalog and filtering
it by bounding box — are exposed as Python functions in `usgs_lidar.py`.
When the user wants an interactive workflow (draw a bbox, pick a dataset),
the agent composes this skill with the generic UI skills `sage-bbox-map`
and `sage-dropdown`.

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
| `fetch_coverage(color_features=True)` | Downloads `https://usgs.entwine.io/boundaries/resources.geojson` and returns an in-memory `GeoDataFrame` in EPSG:4326 with `name`, `url`, `count`, `geometry`. Pass the result directly as `overlay_geojson` to `sage_bbox_map.show_bbox_map`. **Do not write it to a file** — Sage's auto-fallback would then render a duplicate static Folium map. |
| `filter_by_bbox(coverage, bbox, max_points)`   | Given the GeoDataFrame and a bbox tuple `(minx, miny, maxx, maxy)` in EPSG:4326, returns a list of dicts `[{"name", "url", "count", "est"}]` for intersecting datasets whose estimated bbox-clipped point count is below `max_points` (default 20 million). |

## Execution rules — read before writing any code

- Save every script to a `.py` file with `write_file`, then run it with
  `python /path/to/script.py`. Never use heredoc syntax (`python << 'EOF'`).
  Never chain commands with `&&`.
- Read kernel variables via `globals().get("VAR_NAME")`. The system prompt's
  `EXISTING KERNEL VARIABLES` block tells you what's already set by previous cells.

### Kernel variables and subprocess traps

- Variables produced by previous cells (e.g., `USER_BBOX`, `USER_EPT_URL`,
  `pointclouds`) live in the **kernel namespace**. They are visible only to
  commands Sage routes to the kernel:
  - `python script.py` ✅
  - `python -c "..."` ✅
- Commands routed to a subprocess (kernel variables NOT accessible):
  - `HOME=/home/jovyan python script.py` — any environment-variable prefix
  - `python script.py | head` — any pipe
  - `python script.py && echo ok` — any `&&`, `;`, `>`
- If a kernel-routed script fails, **read the printed error and fix the script**.
  Do NOT try to "verify whether `USER_BBOX` exists" by launching a diagnostic
  with an env-prefix — that command runs in a subprocess where kernel variables
  cannot exist by definition.

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

After this, `coverage` (GeoDataFrame) is in the kernel. **Do not call
`coverage.to_file(...)` or otherwise write the catalog to a file** — Sage's
auto-fallback would then render a duplicate static Folium map next to the
live ipyleaflet widget. Pass the in-memory GeoDataFrame directly as the
`overlay_geojson` argument of `show_bbox_map`.

---

## Step 2 — Filter the catalog by a bounding box

Pure data step. Reads a bbox from kernel namespace (set by `sage-bbox-map`),
returns a list of intersecting datasets.

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/usgs-lidar")
from usgs_lidar import filter_by_bbox

bbox = globals().get("USER_BBOX")            # set by sage-bbox-map
coverage = globals().get("coverage")          # set by Step 1
if bbox is None:
    raise RuntimeError("USER_BBOX not set — draw a rectangle on the bbox map first")
if coverage is None:
    raise RuntimeError("coverage not set — run Step 1 first")

datasets = filter_by_bbox(coverage, bbox, max_points=20_000_000)
print(f"{len(datasets)} dataset(s) intersect the bbox with under 20M estimated points.")
for d in datasets[:5]:
    est = f"~{d['est']:,} pts" if d['est'] else "?"
    print(f"  {d['name']}  ({est})")
```

After this, `datasets` (list of dicts) is in the kernel and ready to feed
into `sage-dropdown`.

---

## Composing with sage-bbox-map and sage-dropdown

When the user wants an interactive coverage map plus a dataset picker,
chain this skill with `sage-bbox-map` (area selection) and `sage-dropdown`
(dataset selection). The dropdown uses `sage-dropdown`'s reactive mode
(`items_fn` + `observes`) so it auto-populates the moment the user draws a
rectangle:

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/usgs-lidar")
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sage-bbox-map")
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sage-dropdown")
from usgs_lidar    import fetch_coverage, filter_by_bbox
from sage_bbox_map import show_bbox_map
from sage_dropdown import show_dropdown

coverage = fetch_coverage()        # GeoDataFrame, in-memory
globals()["coverage"] = coverage

show_bbox_map(
    bbox_var={"name": "USER_BBOX",
              "description": "Bounding box drawn by user on the USGS 3DEP coverage map (EPSG:4326)"},
    center=(40, -100),
    zoom=4,
    height="450px",
    header="Draw a rectangle on the USGS 3DEP coverage to select an area",
    overlay_geojson=coverage,
    overlay_name="USGS 3DEP Coverage",
    set_by="usgs-lidar via sage-bbox-map",
)

show_dropdown(
    items_fn=lambda: filter_by_bbox(coverage, globals().get("USER_BBOX"), 20_000_000)
                     if globals().get("USER_BBOX") else [],
    observes="USER_BBOX",
    placeholder="Draw a rectangle on the map above to populate this dropdown.",
    no_items_message="No USGS 3DEP datasets cover the selected area (or all exceed 20 million points). Try a different area.",
    label_template="{name}  (~{est:,} pts)",
    sort_by="name",
    header="Pick a USGS 3DEP dataset that covers the selected area",
    description="Dataset:",
    kernel_vars={
        "USER_EPT_URL":      {"field": "url",
                              "description": "EPT endpoint URL of the user-selected USGS 3DEP dataset"},
        "USER_DATASET_NAME": {"field": "name",
                              "description": "Name of the user-selected USGS 3DEP dataset"},
    },
    info_template="Dataset:        {name}\nEPT endpoint:   {url}\nTotal points:   {count:,}\nIn bbox (est.): {est:,}",
    set_by="usgs-lidar via sage-dropdown",
)
```

The variable names (`USER_BBOX`, `USER_EPT_URL`, `USER_DATASET_NAME`) are
recommendations — pick different names if the data domain suggests them.
Subsequent steps below read whatever names you picked via
`globals().get()`; the kernel-variables registry surfaces them in future
requests automatically.

---

## Step 3 — Download the selected point cloud

Reads `USER_BBOX` and `USER_EPT_URL` from the kernel. Downloads via
`pyforestscan`. Stores `pointclouds` in the kernel.

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
ept = requests.get(USER_EPT_URL, timeout=30).json()
srs = ept.get("srs", {})
ept_srs = f"{srs.get('authority','EPSG')}:{srs.get('horizontal','3857')}"
```

### Step 3 script

```python
import numpy as np
from pyproj import Transformer
from pyforestscan.handlers import read_lidar
from pyforestscan.utils import get_srs_from_ept

USER_BBOX    = globals().get("USER_BBOX")
USER_EPT_URL = globals().get("USER_EPT_URL")
if not USER_BBOX or not USER_EPT_URL:
    raise ValueError("USER_BBOX and USER_EPT_URL must be set — run the bbox / dataset picker cells first.")

ept_srs = get_srs_from_ept(USER_EPT_URL)
transformer = Transformer.from_crs("EPSG:4326", ept_srs, always_xy=True)
minx, miny = transformer.transform(USER_BBOX[0], USER_BBOX[1])
maxx, maxy = transformer.transform(USER_BBOX[2], USER_BBOX[3])
bounds = ([minx, maxx], [miny, maxy])

print(f"Downloading point cloud from {USER_EPT_URL} ...")
pointclouds = read_lidar(USER_EPT_URL, ept_srs, bounds, hag=True)
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

pointclouds  = globals().get("pointclouds")
USER_BBOX    = globals().get("USER_BBOX")
USER_EPT_URL = globals().get("USER_EPT_URL")
if pointclouds is None:
    raise ValueError("pointclouds not found — run Step 3 first")
if not USER_BBOX or not USER_EPT_URL:
    raise ValueError("USER_BBOX / USER_EPT_URL not set")

output_dir = Path(globals().get("SAGE_OUTPUT_DIR", "/tmp"))
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
ept_srs = get_srs_from_ept(USER_EPT_URL)
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
    "bbox": [USER_BBOX[1], USER_BBOX[0], USER_BBOX[3], USER_BBOX[2]],
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
  It contains no widgets, maps, or dropdowns — those come from `sage-bbox-map`
  and `sage-dropdown` when the user asks for them.
- Steps 3–5 read kernel variables `USER_BBOX`, `USER_EPT_URL`, and
  `pointclouds`. The agent may have used different variable names when
  composing with `sage-bbox-map` and `sage-dropdown` — check the
  `EXISTING KERNEL VARIABLES` block in the system prompt and adapt.
