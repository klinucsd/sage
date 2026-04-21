---
name: usgs-lidar
description: USGS 3DEP LiDAR point cloud access and 3D visualization. Use when the user asks to explore, download, or visualize LiDAR point cloud data, 3D terrain, canopy height, or USGS 3DEP datasets for a geographic area of interest.
---

# USGS LiDAR Point Cloud Skill

## Required Libraries

Install on-the-fly at the start of every task — these are not in the base Sage image:

```python
import subprocess, sys

subprocess.run(
    ["conda", "install", "-y", "-c", "conda-forge", "pdal", "python-pdal"],
    check=True, capture_output=True
)
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "pyforestscan", "leafmap", "plotly", "laspy"],
    check=True
)
```

This takes ~2–3 minutes the first time. Subsequent cells in the same session are instant.

---

## Execution rules — read before writing any code

- Save every script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc syntax (`python << 'EOF'`). Never chain commands with `&&`.
- Never call `m.save()` or write an HTML file. The `display(m, out, dropdown)` call at the end of Step 1 is the ONLY correct way to show the map — if you replace it with `m.save()` the interactive widget will not appear.
- Copy each step's code exactly as written. Do not reorganise, summarise, or substitute any part of it.

---

## Step 1 — Coverage Map with Bounding Box Selection

Save the code below to a `.py` file and run it with `python`. It displays the USGS 3DEP
coverage map as a live interactive widget, lets the user draw a rectangle, and populates
a dropdown with datasets that intersect the selected area.
It stores two kernel variables that later steps depend on:
- `USER_BBOX` — `(minx, miny, maxx, maxy)` in EPSG:4326
- `USER_EPT_URL` — EPT endpoint URL for the selected dataset

```python
import subprocess, sys

subprocess.run(
    ["conda", "install", "-y", "-c", "conda-forge", "pdal", "python-pdal"],
    check=True, capture_output=True
)
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "pyforestscan", "leafmap", "plotly", "laspy"],
    check=True
)

import leafmap
import geopandas as gpd
import pyproj
import ipywidgets as widgets
from shapely.geometry import shape
from IPython.display import display

USER_BBOX = None
USER_EPT_URL = None
MAX_POINTS = 20_000_000

# Load coverage GeoJSON
json_url = "https://of4d.sdsc.edu/json/usgs_resources.json"
gdf = gpd.read_file(json_url)

# Build map
m = leafmap.Map(center=[40, -100], zoom=4, toolbar_control=False, measure_control=False)
m.add_basemap("USGS.USTopo")
m.add_gdf(gdf, layer_name="OF4D 3DEP Coverage",
          style={"color": "blue", "weight": 1, "fillOpacity": 0.1})

# Keep only the rectangle drawing tool
for tool in ['marker', 'polyline', 'polygon', 'circlemarker', 'circle']:
    setattr(m.draw_control, tool, {})

out = widgets.Output()
dropdown = widgets.Dropdown(options={}, description='Dataset:', layout={'width': 'max-content'})

def handle_draw(target, action, geo_json):
    global USER_BBOX, USER_EPT_URL
    with out:
        out.clear_output()
        if action != 'created':
            return
        target.data = [geo_json]
        geom = shape(geo_json['geometry'])
        USER_BBOX = geom.bounds
        m.fit_bounds([[USER_BBOX[1], USER_BBOX[0]], [USER_BBOX[3], USER_BBOX[2]]])

        geod = pyproj.Geod(ellps="WGS84")
        area_sq_meters = abs(geod.geometry_area_perimeter(geom)[0])
        intersecting = gdf[gdf.intersects(geom)]

        if intersecting.empty:
            print("No datasets cover this area. Draw a new box.")
            dropdown.options = {}
            USER_EPT_URL = None
            return

        options = {}
        for _, row in intersecting.iterrows():
            density = row.get('density') or 10.0
            est_points = area_sq_meters * density
            display_pts = int(round(est_points, -4))
            if est_points <= MAX_POINTS:
                options[f"{row['name']} (~{display_pts:,} pts)"] = row['url']
            else:
                print(f"Skipped {row['name']}: ~{display_pts:,} pts exceeds {MAX_POINTS:,} limit")

        if not options:
            print("All datasets exceed the point limit. Draw a smaller box.")
            dropdown.options = {}
            USER_EPT_URL = None
        else:
            dropdown.options = options
            USER_EPT_URL = list(options.values())[0]
            print(f"Select a dataset below, then run the next cell.")

def on_dropdown_change(change):
    global USER_EPT_URL
    if change['type'] == 'change' and change['name'] == 'value':
        USER_EPT_URL = change['new']

m.draw_control.on_draw(handle_draw)
dropdown.observe(on_dropdown_change, names='value')

print(f"Draw a rectangle on the map. Max {MAX_POINTS:,} points.")
display(m, out, dropdown)
```

---

## Step 2 — Download the Selected Dataset

Reads `USER_BBOX` and `USER_EPT_URL` from the kernel.
Uses `pyforestscan` to download the point cloud within the selected bounding box.
Saves the result for visualization in Step 3.

```python
import numpy as np
from pyproj import Transformer
from pyforestscan.handlers import read_lidar, get_srs_from_ept

if not USER_BBOX or not USER_EPT_URL:
    raise ValueError("Draw a bounding box and select a dataset in the previous cell first.")

# Get the EPT dataset's native SRS
ept_srs = get_srs_from_ept(USER_EPT_URL)

# Reproject USER_BBOX (EPSG:4326) → EPT SRS
transformer = Transformer.from_crs("EPSG:4326", ept_srs, always_xy=True)
minx, miny = transformer.transform(USER_BBOX[0], USER_BBOX[1])
maxx, maxy = transformer.transform(USER_BBOX[2], USER_BBOX[3])
bounds = ([minx, maxx], [miny, maxy])

print(f"Downloading point cloud from {USER_EPT_URL} ...")
pointclouds = read_lidar(USER_EPT_URL, ept_srs, bounds, hag=True)
print(f"Downloaded {len(pointclouds)} point arrays.")
```

---

## Step 3 — Interactive 3D Visualization

Renders the downloaded point cloud as an interactive Plotly 3D scatter plot colored by
elevation. Decimates to ~250,000 points for browser performance.

Call `fig.show()` directly — do NOT use a `![...](...)` map tag for Plotly figures.

```python
import plotly.graph_objects as go
import numpy as np

# Extract and concatenate x, y, z from all point arrays
all_x, all_y, all_z = [], [], []
for pc in pointclouds:
    all_x.append(pc['X'] if 'X' in pc.dtype.names else pc.x)
    all_y.append(pc['Y'] if 'Y' in pc.dtype.names else pc.y)
    all_z.append(pc['Z'] if 'Z' in pc.dtype.names else pc.z)

x = np.concatenate(all_x)
y = np.concatenate(all_y)
z = np.concatenate(all_z)

# Normalize to avoid floating-point jitter
x -= np.min(x)
y -= np.min(y)

# Decimate to ~250,000 points for performance
total = len(x)
step = max(1, total // 250_000)
x, y, z = x[::step], y[::step], z[::step]
print(f"Rendering {len(x):,} of {total:,} points (step={step})")

fig = go.Figure(data=[go.Scatter3d(
    x=x, y=y, z=z,
    mode='markers',
    marker=dict(
        size=1.5,
        color=z,
        colorscale='earth',
        colorbar=dict(title="Elevation", titleside="right"),
        opacity=1.0,
    )
)])
fig.update_layout(
    margin=dict(l=0, r=0, b=0, t=0),
    scene=dict(aspectmode='data')
)
fig.show()
```

---

## Notes

- `USER_BBOX` and `USER_EPT_URL` are kernel variables set by Step 1. Steps 2 and 3 depend on them — always run Step 1 first and draw a bbox before proceeding.
- `hag=True` in `read_lidar` computes Height Above Ground (normalized elevation relative to ground surface). Set `hag=False` if you want raw absolute elevation.
- The `pointclouds` return value from `read_lidar` is a list of structured numpy arrays. Field names may be uppercase (`X`, `Y`, `Z`) or lowercase depending on the dataset. The Step 3 example handles both.
- For very large areas, reduce `MAX_POINTS` or draw a smaller bounding box to avoid memory issues.
