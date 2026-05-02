---
name: sage-bbox-map
description: "Generic bounding-box selection map. Use whenever the user asks to: draw a rectangle, pick an area, select a region on a map, set a spatial filter, or interactively choose a bounding box. Works with any geographic dataset — fires, weather, satellite, sensors, anything. The agent decides the kernel variable name to receive the drawn bbox (e.g., USER_BBOX, FIRE_BBOX, STORM_BBOX) based on the cell's task and the data domain."
---

# sage-bbox-map — Generic Bounding-Box Selection Map

This is a **UI skill**. It contains no domain knowledge — it just renders an
ipyleaflet map with a rectangle-draw tool and writes the drawn bbox to a
kernel variable named by the **agent**.

Use this skill whenever a cell needs the user to interactively select a
geographic area. Optionally overlay a GeoJSON dataset (fires, earthquakes,
station locations, etc.) for visual context while drawing.

## Importing the helper

The skill ships a Python module `sage_bbox_map.py` next to this file. From an
agent-generated script, add the skill directory to `sys.path` and import:

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sage-bbox-map")
from sage_bbox_map import show_bbox_map
```

## API

```python
show_bbox_map(
    bbox_var,                 # {"name": "VAR_NAME", "description": "..."}
    center=(lat, lon),
    zoom=4,
    height="400px",
    header="Draw a bounding box on the map",
    overlay_geojson=None,     # optional path to a context layer
    overlay_color="#3388ff",
    overlay_name="Overlay",
    set_by="sage-bbox-map",
)
```

Until the user draws, the kernel variable is `None`. After drawing, it becomes
a 4-tuple `(minx, miny, maxx, maxy)` in **EPSG:4326**.

## Choosing the bbox variable name

The agent picks a meaningful name based on the data domain:
- Generic area selection → `USER_BBOX`
- Selecting a fire region → `FIRE_BBOX` or `SELECTED_FIRE_BBOX`
- Storm-track region → `STORM_BBOX`
- Lidar coverage area → `LIDAR_BBOX` (note: `usgs-lidar` skill uses its own integrated coverage UI, not this generic one)

Always provide a clear `description` — it appears in Sage's system prompt for
every following cell.

## Cell rerun behaviour

Same as sage-dropdown: Sage tracks the registration under the current cell's
`cellId`. On rerun, the variable is automatically deleted before the cell runs
again.

## Cross-cell discovery

Every drawn bbox appears in subsequent requests' system prompts:

```
EXISTING KERNEL VARIABLES (set by previous cells, available now):
- `FIRE_BBOX` (tuple) — Bounding box drawn by user on the SoCal GOES fires map (EPSG:4326) [set by …]
```

Subsequent cells should read it via `globals().get("FIRE_BBOX")`.

## Full example

User asked: "Create a map with all GOES fires in Southern California with a
bounding box selection tool."

After the data skill (`sdge-goes-fire`) saves `fires.geojson`, the agent calls:

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sage-bbox-map")
from sage_bbox_map import show_bbox_map

show_bbox_map(
    bbox_var={
        "name": "FIRE_BBOX",
        "description": "Bounding box drawn by user on the SoCal GOES fires map (EPSG:4326)",
    },
    center=(34.05, -118.24),
    zoom=8,
    header="GOES Fire Detections — Draw a Box to Select Area of Interest",
    overlay_geojson="/path/to/fires.geojson",
    overlay_color="#e34a33",
    overlay_name="GOES Fires",
    set_by="sdge-goes-fire via sage-bbox-map",
)
```

## Execution rules

- Save your script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc. Never chain commands with `&&`.
- Do NOT create your own `ipyleaflet.Map` — call `show_bbox_map` and let it render.
- Do NOT hardcode the bbox variable name. Pick a name that fits the data domain.
- Do NOT skip `description` in `bbox_var` — it's how downstream cells understand what the bbox represents.
- For `overlay_geojson`, prefer in-memory dicts or GeoDataFrames over file paths. Writing the overlay to `SAGE_OUTPUT_DIR` triggers Sage's auto-Folium fallback, producing a duplicate static map next to the live ipyleaflet widget.

## Important: don't read what the user must set

`show_bbox_map` renders a widget the user must interact with (draw a
rectangle). The drawing happens *after* your script returns. Do NOT, in
the same script, read the bbox kernel variable — you will see `None` (or
whatever stale value it had before).

If your task involves the user drawing a rectangle plus then acting on it
(filtering data, picking from a list of intersecting items), use one of
these patterns:

- **Linked widget in the same script** — render `show_bbox_map` and a
  reactive `sage-dropdown` (with `items_fn` + `observes="USER_BBOX"`).
  The dropdown auto-populates when the user draws.
- **Stop after rendering the bbox map** — the user's bbox lands in the
  kernel namespace and is visible to subsequent requests via the
  kernel-variables registry.

Calling `show_bbox_map` then immediately reading the bbox variable in the
same script is one of the most common mistakes — it always fails.
