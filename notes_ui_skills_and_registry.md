# Notes: UI–Data Skill Separation and Kernel Variable Registry

Source material for updating the Sage paper. Implemented 2026-04-25, shipped in
`kaiucsd/sage-dev:kernel-0.1.33` through `kernel-0.1.35`. Estimated paper
contribution: 1–2 pages, to be inserted after the §6 Case Study (LiDAR) and
before Limitations.

---

## 1. Motivation

In the original Sage design, every data skill that needed an interactive
selector embedded its own UI code. The `usgs-lidar` skill built a coverage map
with bbox draw + filtered dropdown. The `exoplanet-transits` skill (in earlier
iterations) included its own ipywidgets Dropdown plus reopen-notice JS plus
deferred-display logic. Three problems followed:

1. **Code duplication.** Each new data skill that wanted a picker copy-pasted
   ~100 lines of widget wiring, change handlers, and reopen-notice HTML/JS.
2. **Inconsistent UX.** Two dropdowns from two skills could look or behave
   differently, and bug fixes had to be applied per-skill.
3. **Mixed concerns.** A skill called `exoplanet-transits` should describe how
   to fetch exoplanet data — not how to render an ipywidgets Dropdown. As Kai
   put it: *"a data skill should only talk about data rather than GUI."*

Even worse, the embedded UI baked **kernel variable names** into the data
skill (`TARGET_PLANET`, `USER_BBOX`). This made variable naming a property of
the data source rather than the user's task. A user asking *"create a dropdown
for Kanawha river reaches"* and another asking *"create a dropdown of GPS
stations"* should receive structurally identical UIs but with different,
meaningful kernel variable names. With UI logic embedded in data skills,
this required separately implementing the picker in each data skill.

## 2. Three-Role Architecture

The refactor establishes three distinct roles:

| Role         | Responsibility                                     | Example                                     |
|--------------|----------------------------------------------------|---------------------------------------------|
| Data skill   | Fetch / parse / structure data; output Python objects | `exoplanet-transits` produces `planets` list-of-dicts |
| UI skill     | Render an interactive widget; mechanism only       | `sage-dropdown` renders ipywidgets.Dropdown |
| Agent        | Compose data + UI; choose kernel variable names    | Picks `TARGET_PLANET`, `TARGET_STAR` based on cell intent |

The agent reads multiple SKILL.md files for one cell. It runs the data skill's
fetch step, captures the resulting Python list, and then calls the UI skill's
helper passing the list plus a `kernel_vars` mapping that **the agent itself
defines**. No data skill ever names a kernel variable; no UI skill ever knows
what its items represent. The agent is the only place where domain semantics
and UI mechanics meet.

This is the same kind of separation as Backend / Frontend / Application Logic
in conventional software, applied to skills inside a notebook agent.

## 3. The Kernel Variable Registry

### Problem

A single notebook may have a dozen `%%ask` cells. Cell 1 sets `TARGET_PLANET`
via the exoplanet skill; Cell 7 wants to overlay the user-selected GPS station
on a map and needs to know that `SELECTED_GPS_STATION` exists. Without help,
Cell 7's agent only reads its own active skill's SKILL.md — it cannot discover
variables produced by skills active in other cells. This is a fundamental
*cross-cell, cross-skill discovery gap*.

### Solution

A sidecar registry, `.sage_kernel_vars.json`, stored in `SAGE_OUTPUT_DIR`
alongside the existing `.sage_colors.json` (color registry) and
`.sage_cells.json` (file registry). The registry maps each cell's `cellId`
(from Jupyter's `parent_header.metadata.cellId`, which is stable across edits)
to the set of kernel variables it registered:

```json
{
  "<cell-uuid-1>": {
    "TARGET_PLANET": {
      "description": "Name of the user-selected exoplanet",
      "type": "str",
      "set_by": "exoplanet-transits via sage-dropdown"
    },
    "TARGET_STAR": { ... }
  },
  "<cell-uuid-2>": {
    "FIRE_BBOX": {
      "description": "Bounding box drawn by user on the SoCal GOES fires map (EPSG:4326)",
      "type": "tuple",
      "set_by": "sdge-goes-fire via sage-bbox-map"
    }
  }
}
```

### Three integration points in `sage_magic.py`

1. **Helper functions** (`_load_kernel_vars_registry`, `_save_kernel_vars_registry`,
   `_kernel_vars_registry_prompt`) — modeled directly on the existing
   `_color_registry_prompt` pattern.

2. **Prompt injection.** Every `%%ask` system prompt now includes an
   `EXISTING KERNEL VARIABLES (set by previous cells, available now)` block,
   filtered to entries whose names are still in `user_ns` (staleness filter).
   The block lists each variable with its current value, type, description, and
   originating skill:

   ```
   - `TARGET_PLANET` (str) = 'K2-132 b' — Name of the user-selected exoplanet
       [set by exoplanet-transits via sage-dropdown]
   - `ORBITAL_PERIOD_DAYS` (float) = 9.1729 — Orbital period in days
       [set by exoplanet-transits via sage-dropdown]
   ```

   Including the **current value** (not just the type) was a key correction:
   without values the agent narrated default placeholders ("You've selected 55
   Cnc e") even when the user had picked a different planet. With values, the
   agent narrates accurately on the first try.

3. **Cell-rerun cleanup.** When a cell is re-executed (whether from edit or
   from a manual rerun), Sage looks up that cell's `cellId` in the registry,
   deletes those variables from `user_ns`, and removes the registry entry —
   *before* running the cell. This mirrors the existing file-cleanup behavior
   (`.sage_cells.json` already tracks files-per-cell for the same purpose).
   Without this, a cell that previously set `TARGET_PLANET=K2-132 b` would
   leave that value in the kernel after the user edits the cell to a new task,
   silently poisoning later cells.

### Phase 1 vs Phase 2

The current implementation is metadata-only: the registry stores description,
type, and provenance, while the values themselves live in `user_ns` and
disappear on kernel restart. This matches the lifecycle of the variables they
describe.

A future Phase 2 could persist serializable values (tuples, strings, numbers)
to disk, enabling cross-restart recovery. Non-serializable values (numpy
arrays, ipyleaflet Maps, lightkurve LightCurve objects) would remain
metadata-only.

## 4. Generic UI Skills

Two reference UI skills implement the new pattern. Both are skill-agnostic —
they know nothing about the data being displayed.

### `sage-dropdown`

```python
show_dropdown(
    items,                # list of dicts, supplied by data skill
    label_template,       # f-string-style format applied to each item
    kernel_vars={         # caller-defined mapping
        "VAR_NAME": {"field": "<field>" or "@self",
                     "description": "<human-readable>"}
    },
    sort_by="...",
    info_template="...",  # optional info pane
)
```

The helper writes selected fields to caller's namespace and registers them.
Frame inspection (`inspect.currentframe().f_back.f_globals`) finds the caller's
namespace, which under Sage's KernelShellBackend equals `user_ns` because
scripts run via `exec(code, user_ns)`.

### `sage-bbox-map`

```python
show_bbox_map(
    bbox_var={"name": "VAR_NAME",
              "description": "<human-readable>"},
    center=(lat, lon),
    zoom=4,
    overlay_geojson="<path>",   # optional context layer
    overlay_color="#3388ff",
)
```

Same pattern — caller specifies the variable name; helper renders an
ipyleaflet map with rectangle-draw, writes a 4-tuple `(minx, miny, maxx, maxy)`
in EPSG:4326 to the named variable, and registers it.

### What's *not* in the UI skills

- No knowledge of exoplanets, river reaches, fires, or any data domain.
- No fixed kernel variable names. Every name comes from the caller.
- No data fetching. The caller (data skill or agent script) supplies items.

## 5. Composition Example

User: *"Show me transiting exoplanets and let me pick one."*

The agent's single-cell composition:

```python
# === Data skill block: exoplanet-transits Step 1 ===
import requests
# (fetches NASA Exoplanet Archive or uses fallback list)
planets = sorted(rows, key=lambda p: p["pl_name"])

# === UI skill block: sage-dropdown ===
import sys
sys.path.insert(0, "/home/jovyan/.deepagents/agent/skills/sage-dropdown")
from sage_dropdown import show_dropdown

show_dropdown(
    items=planets,
    label_template="{pl_name}  (P={pl_orbper:.3f}d, V={sy_vmag:.1f})",
    sort_by="pl_name",
    header="Step 1: Select a Transiting Exoplanet",
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
    set_by="exoplanet-transits via sage-dropdown",
)
```

The kernel variable names (`TARGET_*`) are chosen by the agent and reflect the
domain. A different cell asking for a Kanawha river-reach picker would produce
the same call shape with `kernel_vars={"SELECTED_KANAWHA_REACH_ID": ...}`. The
`sage-dropdown` helper itself never changes.

After the cell runs, Cell 2's system prompt automatically receives:

```
EXISTING KERNEL VARIABLES (set by previous cells, available now):
- `TARGET_PLANET` (str) = 'WASP-39 b' — Name of the user-selected exoplanet [set by exoplanet-transits via sage-dropdown]
- `TARGET_STAR` (str) = 'WASP-39' — Host star name of the selected exoplanet [set by exoplanet-transits via sage-dropdown]
- `ORBITAL_PERIOD_DAYS` (float) = 4.0553 — Orbital period in days of the selected exoplanet [set by exoplanet-transits via sage-dropdown]
- `PLANET_DATA` (dict, 11 keys) — Full NASA Exoplanet Archive record [set by exoplanet-transits via sage-dropdown]
```

Cell 2's agent reads this block, knows what's available, and produces a script
that uses `globals().get("TARGET_STAR")` to download the corresponding light
curve — without any cross-cell coordination from the user.

## 6. Implementation Lessons

Three iteration points worth mentioning briefly in the paper:

1. **Default-vs-selected narration.** When a dropdown first renders, the
   ipywidgets `Dropdown.value` defaults to the first option, so the kernel
   variables are technically populated even before the user clicks. The agent
   then narrated *"You've selected 55 Cnc e"* on the very first cell, which
   was misleading. Fix: the helper now prints `Initial / default selection
   (the user may change this in the dropdown widget)` to stdout, and the
   `sage-dropdown` SKILL.md gives the agent explicit ✅/❌ examples for first
   vs subsequent cells.

2. **Reopen-notice timing threshold.** Sage shows a yellow banner *"Re-run
   this cell to restore widget interactivity"* when the elapsed time since
   cell execution exceeds a threshold, signaling a notebook reopen rather
   than a fresh run. The original threshold of 30 s was too tight for slow
   scripts (NASA Exoplanet Archive query + `lightkurve` install can exceed
   30 s on the very first run), causing the banner to display spuriously
   on completion. Bumped to 90 s, which still distinguishes reopened
   notebooks (typically hours / days old) cleanly.

3. **Cleanup-before-prompt ordering.** The cell-rerun cleanup (delete this
   cell's previously-registered kernel variables) was initially placed *after*
   the system prompt was built. Result: on rerun of a dropdown cell, the
   prompt's `EXISTING KERNEL VARIABLES` block still showed the previous run's
   `TARGET_PLANET` etc., so the agent would see "the user already selected
   55 Cnc e" and narrate accordingly — even jumping ahead to download the
   light curve instead of just re-rendering the picker. Fix: the cleanup
   block must run *before* prompt construction so `_kernel_vars_registry_prompt()`
   reads a clean `user_ns`. The general principle for prompt-injected state:
   **invalidate first, then build the prompt.** Any state that the prompt
   reads must reflect the user's current intent, not the previous run's.

## 7. Architectural Significance

The kernel variable registry transforms cross-cell state from a *mechanism*
into a *protocol*. Before the registry, cross-cell state existed implicitly
(any top-level assignment in a script persists in `user_ns`), but no cell
could discover what other cells had created. With the registry, every kernel
variable produced by a UI skill carries a self-describing record:

- Its name (chosen by the agent based on intent)
- Its current value (visible in every later cell's system prompt)
- Its type
- A free-text description
- The skill (or skill chain) that created it
- The cell that owns its lifecycle (for rerun cleanup)

This protocol-level view of cross-cell state — combined with the data /
mechanism / agent separation of concerns — is what makes Sage's skill
ecosystem genuinely composable. Adding a new data source (e.g., a buoy
observation feed) requires only a data skill that produces a typed Python
list. Any existing UI skill renders it without modification. Any subsequent
cell discovers and uses the resulting kernel variables without explicit
references to the producing skill.

## 8. Suggested paper insertion location

This work fits naturally as a new subsection between §5 (System Capabilities)
and the §6 LiDAR Case Study, OR as an extension of §6 with a second case study
(the exoplanet notebook). One concrete ordering:

- §5.5 — Composable skill ecosystem (the three-role architecture)
- §5.6 — Cross-cell discovery via the kernel variable registry
- §6.1 — LiDAR case study (existing)
- §6.2 — Exoplanet case study, demonstrating composition of data + UI skills

Each section is roughly half a page.

## 9. Open work

- ~~`usgs-lidar` is not yet decomposed into data + UI parts.~~ **Done** in
  kernel-0.1.38 (2026-04-25). The data primitives (`fetch_coverage`,
  `filter_by_bbox`) live in a `usgs_lidar.py` helper module; the SKILL.md is
  GUI-free with a "Composing with sage-bbox-map and sage-dropdown" section
  describing the two-cell pattern. `sage-bbox-map` was extended with
  per-feature `_color` support so USGS's 27-color categorical coverage palette
  renders naturally as an overlay.

- ~~**Composite UI components** — linked widgets in a single cell.~~ **Done**
  in kernel-0.1.40 (2026-04-25). Mechanism: `_sage_var_subscribers` dict in
  `user_ns` maps each kernel variable name to a list of subscriber callbacks
  (each tagged with the registering cell's `cell_id`). `sage_bbox_map`
  notifies subscribers when the user draws a bbox or clicks Clear.
  `sage_dropdown` accepts a reactive triple: `items_fn` (callable returning
  the current items), `observes` (a kernel variable name to watch), and
  `placeholder` (text shown while empty). When `items_fn()` returns an empty
  list, the dropdown is hidden and the placeholder is displayed; when the
  watched variable changes, the dropdown re-evaluates `items_fn()`,
  refreshes its options, and selects the first new item.

  This is the canonical *publish/subscribe* pattern adapted to the kernel
  variable namespace. The mechanism is fully general — any future helper
  that wants to be reactive subscribes to the kernel variable it cares
  about; any helper that produces a value notifies. The same path supports
  bbox→dropdown (USGS lidar), dropdown→dropdown (state→county), or any
  A→B chain.

  In the paper, this is worth describing alongside the registry: together
  they elevate the kernel namespace from a passive store to an active,
  observable communication channel between widgets.

- **Phase 2 of the registry** (value persistence for serializable variables
  across kernel restarts) is designed but not implemented. Briefly mention as
  future work.
