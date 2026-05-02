---
name: sage-dropdown
description: "Generic interactive dropdown picker. Use whenever the user asks to: pick, select, or choose one item from a list; show a dropdown menu; pick one row from a dataset, GeoDataFrame, or list; build an interactive selector UI. Works with any list-of-dicts data — exoplanets, river reaches, GPS stations, earthquake events, weather stations, sensors, anything. The agent decides which kernel variables to write the user's selection into based on the cell's task."
---

# sage-dropdown — Generic Interactive Dropdown

This is a **UI skill**. It contains no domain knowledge — it only renders a dropdown
and writes the user's selection to kernel variables that the **agent** chooses.

Use this skill whenever a data skill produces a list of items and the user needs
to pick one. The data skill is responsible for fetching/structuring the items
(typically as a list of dicts). This skill takes that list and shows the picker.

## Importing the helper

The skill ships a Python module `sage_dropdown.py` next to this file. From an
agent-generated script, add the skill directory to `sys.path` and import:

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sage-dropdown")
from sage_dropdown import show_dropdown
```

## API

```python
show_dropdown(
    items=None,           # static list of dicts; OR use items_fn for reactive mode
    label_template,       # f-string-style template for each row's display text
    kernel_vars,          # {VAR_NAME: {"field": <field_or_"@self">, "description": str}}
    header="Select an item",
    description="Item:",
    info_template=None,   # optional multi-line template shown below the dropdown
    sort_by=None,         # optional field name to alphabetize items by
    set_by="sage-dropdown",

    # Reactive mode — for linked widgets in a single cell:
    items_fn=None,        # callable returning a list of dicts; used instead of `items`
    observes=None,        # name of a kernel variable to watch (e.g. "USER_BBOX")
    placeholder=None,     # text shown when items_fn() empty AND observed value is None
    no_items_message=None,# text shown when items_fn() empty BUT observed value is set
)
```

### Static vs reactive mode

- **Static mode** — pass `items=[...]`. The dropdown renders immediately
  with that fixed list. Use when the items are already known at the moment
  the script runs (e.g. an exoplanet catalog you just fetched).

- **Reactive mode** — pass `items_fn=callable` and (usually) `observes="VAR"`.
  The dropdown calls `items_fn()` initially. When `VAR` changes (e.g. the
  user draws a rectangle in `sage-bbox-map`), the dropdown re-evaluates
  `items_fn` and refreshes its options. Three empty-state messages are
  available:
  - `placeholder` — shown when `items_fn()` is empty AND the observed
    variable is unset (e.g. before the user has drawn a bbox).
  - `no_items_message` — shown when `items_fn()` is empty BUT the
    observed variable IS set (e.g. user drew a bbox but no datasets
    intersect). Falls back to `placeholder` if not provided.
  - When items are non-empty, the dropdown unhides and shows the first
    item's info via `info_template`.

`kernel_vars` is the **most important** argument: the agent decides what to call
each kernel variable based on the cell's intent. Pick names that downstream cells
in the notebook will recognise. Always write a clear `description` — that text
appears in Sage's system prompt for every following cell so the agent there can
discover what's available.

## Choosing kernel variable names

When the user asks "let me pick a Kanawha river reach", the agent should choose
something like:

```python
kernel_vars = {
    "SELECTED_KANAWHA_REACH_ID":   {"field": "reach_id",
                                    "description": "NHD reach ID of the user-selected Kanawha river reach"},
    "SELECTED_KANAWHA_REACH_GEOM": {"field": "geometry",
                                    "description": "Shapely geometry of the selected Kanawha reach"},
    "SELECTED_KANAWHA_REACH":      {"field": "@self",
                                    "description": "Full record dict of the selected Kanawha river reach"},
}
```

For "pick a GPS station near a magnitude-5 earthquake":

```python
kernel_vars = {
    "SELECTED_GPS_STATION":       {"field": "station_code",
                                   "description": "USGS station code of the user-selected GPS station"},
    "SELECTED_GPS_STATION_LATLON": {"field": "@self",
                                    "description": "Full station record dict including coords"},
}
```

Variable names should be:
- ALL_CAPS_SNAKE_CASE
- Specific enough that two different dropdowns in the same notebook never collide
- Self-explanatory ("SELECTED_PLANET" beats "ITEM")
- Aligned with the data domain ("EARTHQUAKE", "REACH", "STATION", not "DATA1")

## Field selectors

In each `kernel_vars` entry, `field` controls what value gets stored:
- A string field name (e.g. `"reach_id"`) → stores `item["reach_id"]`
- The literal `"@self"` → stores the entire item dict

## Cell rerun behaviour

Sage tracks each registration under the current cell's `cellId` in
`.sage_kernel_vars.json`. When the user re-executes the cell, Sage automatically
deletes the variables this cell previously registered before running it again.
You don't need to clean up manually.

## Cross-cell discovery

Every variable registered here appears in the system prompt of every later
`%%ask` cell:

```
EXISTING KERNEL VARIABLES (set by previous cells, available now):
- `SELECTED_KANAWHA_REACH_ID` (str) — NHD reach ID of the user-selected Kanawha river reach [set by …]
…
```

Subsequent cells should **read** these via `globals().get("VAR_NAME")` rather
than re-prompting the user or hardcoding values.

## Full example

```python
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sage-dropdown")
from sage_dropdown import show_dropdown

# `planets` was fetched by the exoplanet-transits data skill earlier in this script
show_dropdown(
    items=planets,
    label_template="{pl_name}  (P={pl_orbper:.3f}d, V={sy_vmag:.1f})",
    sort_by="pl_name",
    header="Select a Transiting Exoplanet",
    description="Planet:",
    kernel_vars={
        "TARGET_PLANET":       {"field": "pl_name",
                                "description": "Name of the user-selected exoplanet"},
        "TARGET_STAR":         {"field": "hostname",
                                "description": "Host star name of the selected exoplanet"},
        "ORBITAL_PERIOD_DAYS": {"field": "pl_orbper",
                                "description": "Orbital period in days of the selected exoplanet"},
        "PLANET_DATA":         {"field": "@self",
                                "description": "Full NASA Exoplanet Archive record for the selected planet"},
    },
    info_template=(
        "Planet:          {pl_name}\\n"
        "Host Star:       {hostname}  (V={sy_vmag:.1f})\\n"
        "Orbital Period:  {pl_orbper:.4f} days"
    ),
    set_by="exoplanet-transits via sage-dropdown",
)
```

## Execution rules

- Save your script to a `.py` file with `write_file`, then run it with `python /path/to/script.py`. Never use heredoc (`python << EOF`). Never chain commands with `&&`.
- Do NOT call `display(Image(...))` or `dropdown.value` directly in your script — `show_dropdown` handles all UI. Just call it and let it return.
- Do NOT hardcode kernel variable names. Pick names that fit the data domain (planet, reach, quake, station, etc.).
- Do NOT skip `description` in `kernel_vars` — it's what makes the registry useful for subsequent requests.

## Reactive composition — linking widgets

The reactive mode (`items_fn` + `observes` + `placeholder`) lets a dropdown
populate itself from another widget's output. Example: a `sage-bbox-map`
for area selection plus a dropdown of datasets that intersect the drawn area:

```python
show_bbox_map(
    bbox_var={"name": "USER_BBOX", "description": "..."},
    overlay_geojson=coverage_gdf,
    ...
)
show_dropdown(
    items_fn=lambda: filter_by_bbox(coverage_gdf, globals().get("USER_BBOX"), 20_000_000)
                     if globals().get("USER_BBOX") else [],
    observes="USER_BBOX",
    placeholder="Draw a rectangle on the map above to populate this dropdown.",
    label_template="{name}  (~{est:,} pts)",
    kernel_vars={"USER_EPT_URL": {"field": "url", "description": "..."}, ...},
)
```

The dropdown is hidden initially; when the bbox is drawn, it auto-populates.
The same pattern handles State→County, Year→Hurricane, or any
parent-output → child-options chain.

## Important: don't read what the user must set

`show_dropdown` renders an interactive widget. The user's choice happens
*after* your script returns. Do NOT, in the same script, read the kernel
variable the user must set — you will see the default (the first item),
not their choice. If your task involves something the user picks via the
dropdown plus then doing something with that pick (downloading data,
plotting, analyzing), render the dropdown only and stop. The user's pick
will be available in subsequent requests via the kernel-variables
registry.

## Narration guidance — IMPORTANT

After `show_dropdown` runs, the script's stdout includes a line like:

```
[sage-dropdown] Dropdown rendered. Initial / default selection (the user may change this in the dropdown widget):
  TARGET_PLANET = '55 Cnc e'
  TARGET_STAR = '55 Cnc'
  ...
```

This is the **default** selection (first item in the dropdown), NOT a user-confirmed pick. The user might still change it.

When you narrate after a `%%ask` cell that called `show_dropdown` for the FIRST time:
- ✅ Say: "I've shown a dropdown of 987 planets. The default is 55 Cnc e — pick a different one if you'd like."
- ❌ Do NOT say: "You've selected 55 Cnc e!" — the user hasn't actually picked yet.

In LATER cells, the system prompt's `EXISTING KERNEL VARIABLES` block tells you the current value. If it's different from the dropdown's first option, the user has actively picked. Refer to it as the "selected" planet then.
