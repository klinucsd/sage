# Changelog

All notable changes to the Sage Docker image are documented here.

---

## kernel-0.1.30 — 2026-04-23 (experimental branch: kernel-shell-backend)
Expanded the reopen-cleanup script in usgs-lidar Step 1 to hide the remaining eyesores:
- **"Error displaying widget: model not found"** — iterate every `.jp-OutputArea-output` and hide any whose textContent matches the error and is under 200 chars.
- **Red broken-link icons** — they live in cell outputs OUTSIDE the `.leaflet-container`, so the old selector missed them. New selector is `cell.querySelectorAll("img")` without the map-scoped filter, plus a fallback that hides any output containing only a tiny SVG with no text.
- Retry cadence tightened to 200/600/1500/3000 ms to catch both immediate and late-rendering fallbacks.

## kernel-0.1.29 — 2026-04-23 (experimental branch: kernel-shell-backend)
- **Timestamp-based reopen detection**: the "map renders → hide notice" heuristic from 0.1.28 misfired when the map partially rendered on reopen (the `.leaflet-container` was present but the surrounding widgets were dead — the notice then hid even though callbacks were broken). Now the HTML bakes in the cell's execution timestamp as `data-runtime="<ms_since_epoch>"`, and the embedded script compares with `Date.now()`. `elapsed < 30s` → fresh run → hide notice. `elapsed > 30s` → reopen → notice stays. 30s absorbs kernel↔browser clock skew on JupyterHub deployments.
- Broken-icon cleanup now runs on three timed retries (400ms, 1.2s, 2.5s) independent of the notice visibility — handles late-loading images.

## kernel-0.1.28 — 2026-04-22 (experimental branch: kernel-shell-backend)
- **Conditional reopen notice + broken-icon cleanup**: the "re-run this cell" warning is now visible only when something is actually wrong. The static HTML notice contains an embedded `<script>` that polls for `.leaflet-container` in the same cell output for up to 3 seconds and then:
  - Hides every control/draw-toolbar `<img>` that failed to load (`naturalWidth === 0`), which on reopen is usually the Leaflet.draw sprite icons showing as broken-image × boxes.
  - If no broken images were found, hides the notice entirely.
  - If broken images WERE found, leaves the notice visible so the user knows to re-run.
- Result:
  - Fresh run → everything works, no broken images, notice hidden.
  - Reopen with valid saveState but broken sprites → broken icons hidden, notice stays visible.
  - Reopen with corrupted saveState → map absent, notice stays visible.
- Each cell gets a unique notice ID via `uuid.uuid4()` so multiple Step-1 cells in one notebook don't fight each other.

## kernel-0.1.27 — 2026-04-22 (experimental branch: kernel-shell-backend)
- **Sharpen usgs-lidar vs py3dep-dem descriptions**: both mentioned "3DEP" so the agent was sometimes picking py3dep-dem (DEM rasters) when the user wanted usgs-lidar (interactive LiDAR coverage map), generating unrelated py3dep availability-check scripts. Each description now explicitly states what it IS and what it is NOT, cross-referencing the other skill. usgs-lidar leads with "Interactive USGS 3DEP LiDAR point cloud coverage map with draw-rectangle bounding-box selection"; py3dep-dem leads with "USGS 3DEP digital elevation model (DEM) rasters and derived terrain products".

## kernel-0.1.26 — 2026-04-22 (experimental branch: kernel-shell-backend)
- **Persistent reopen notice** for the usgs-lidar Step 1 cell: displayed via `IPython.display.HTML` (stored as a `text/html` mime bundle in the cell output, not as a widget) so it survives notebook close/reopen regardless of `saveState`. Tells the user to re-run the cell if the map isn't responding — addresses the "widgets look alive but callbacks are dead" reopen problem observed on 0.1.25. The message appears above the map on every run, so it's discoverable before the user is confused.

## kernel-0.1.25 — 2026-04-22 (experimental branch: kernel-shell-backend)
- **Scrollable popup**: wrapped the multi-dataset HTML in a scrollable container (`max-width:260px`, `max-height:220px`, `overflow-y:auto`) and set `max_width=300` on the Popup so content no longer overflows the popup chrome when several datasets overlap.
- **Drop CSS injection for edit/remove hiding**: replaced `.leaflet-draw-edit-edit`/`.leaflet-draw-edit-remove` CSS hacks with the native ipyleaflet traits `m.draw_control.edit = False` and `m.draw_control.remove = False`. Cleaner, and removes one HTML widget from the display list — possibly reducing broken-asset artifacts on notebook reopen.
- **SKILL.md reopen note**: added a note that reopened notebooks render maps read-only (callbacks aren't re-wired since the kernel is fresh); user must re-execute Step 1 to regain interactivity. This is a fundamental `saveState` limitation — widget VIEWS are restored from notebook metadata, but Python `on_click`/`on_draw`/`observe` handlers live only in the kernel's memory.

## kernel-0.1.24 — 2026-04-22 (experimental branch: kernel-shell-backend)
- **Popup at click point (not polygon centroid)**: switched from `geo_layer.on_click` (gives feature but no coordinates) to `m.on_interaction`, which fires with `kwargs["coordinates"] = [lat, lng]` so the popup arrow points exactly where the user clicked.
- **All overlapping datasets in one popup**: on each click, iterate `gdf` with `gdf.contains(Point(lng, lat))` to find every coverage polygon under the cursor; render each dataset as a block separated by `<hr>` lines. Matches the stacked-dataset appearance of the USGS 3DEP web app screenshot.

## kernel-0.1.23 — 2026-04-22 (experimental branch: kernel-shell-backend)
- **Fix "Widget is not attached" error after first popup click**: The old reuse-one-Popup-across-clicks pattern triggered a JupyterLuminoWidget dispose error once the user closed the popup via ×, and subsequent clicks then silently failed. New approach: create a fresh `ipyleaflet.Popup` widget per click and remove the previous one first.
- **Enable "Save Widget State Automatically" system-wide**: Added `jupyterlab_overrides.json` with `@jupyter-widgets/jupyterlab-manager:plugin.saveState = true`, copied into `$(prefix)/share/jupyter/lab/settings/overrides.json` by the Dockerfile. Widgets are now serialized into notebook metadata on save, so maps/dropdowns/buttons render on reopen (read-only; re-run the cell to regain interactivity).

## kernel-0.1.22 — 2026-04-22 (experimental branch: kernel-shell-backend)
- **Polygon click popup**: `geo_layer.on_click` was updating the status_html panel below the map, but users naturally expect a Leaflet-style popup *on the map* (as in the USGS 3DEP web app). Replaced with an `ipyleaflet.Popup` layer whose location is set to the clicked feature's centroid and whose content is `<b>Name</b><br>Points: N,NNN<br><a href="...">EPT Data</a>`. The popup re-opens on every click (remove + re-add pattern) so users aren't stuck after closing via ×.
- **Hover feedback**: `hover_style={"weight": 3, "fillOpacity": 0.6}` on the coverage layer so users can see which polygon they're about to click.
- **Popup closes on new bbox / clear**: `_process_bbox` and `_reset_state` both call `_close_popup()` so the popup doesn't clutter the bbox analysis view.

## kernel-0.1.21 — 2026-04-22 (experimental branch: kernel-shell-backend)
Re-work the coverage-map widget after 0.1.20 still showed two bboxes and lost the layer control.
- **Bbox lives in a named GeoJSON layer (not DrawControl)** — `ipyleaflet` exposes no reliable Python API to remove a single shape from DrawControl's FeatureGroup (`target.clear()` is all-or-nothing, `target.data = [...]` doesn't sync back to the frontend). So on each `"created"` we remove the previous GeoJSON bbox layer, `target.clear()` the DrawControl, then re-add the new bbox as a named `GeoJSON(name="Bounding box")` layer. Clean single-bbox guarantee.
- **CSS injection hides Leaflet.draw's edit/remove tools** — since the bbox is no longer in DrawControl's FeatureGroup, those tools can't operate on it and would look disabled. CSS selectors `.leaflet-draw-edit-edit`, `.leaflet-draw-edit-remove`, and the empty 2nd section are set to `display:none!important`.
- **Clear button restyled** — gray default styling, `times` icon, 150px wide. Replaces the trash tool; shown only while a bbox exists.
- **Layer control restored** — `m.add_layer_control()` back in, with post-hoc loop that renames any unnamed map layers to `"Draw layer"` so the ipyleaflet-internal FeatureGroup entry no longer appears as an empty-label row.

## kernel-0.1.20 — 2026-04-22 (experimental branch: kernel-shell-backend)
Three fixes to the usgs-lidar coverage-map widget based on UX feedback on 0.1.19:
- **Single-bbox enforcement**: In 0.1.19 `target.data = [geo_json]` didn't actually remove the old shape from the frontend FeatureGroup — `target.clear()` is the only reliable Python-side wipe, but it removes the new shape too. Now when a second shape is drawn, `target.clear()` removes both and the status panel shows "Only one bounding box allowed. The previous one has been cleared — please draw a single rectangle." The user redraws cleanly.
- **Info refresh on delete**: `_reset_state()` is now called unconditionally on `"deleted"` (previously gated on `len(target.data) == 0`, which was racy and sometimes skipped the reset). Removing the bbox via the native trash tool now immediately clears the dataset info and dropdown.
- **Empty-label layer**: Removed `m.add_layer_control()` — ipyleaflet's DrawControl exposes its internal FeatureGroup as an unnamed map layer that showed up in the layers widget with an empty label. Since we only have one real toggleable layer (USGS 3DEP coverage), the layer control isn't worth the confusion.

## kernel-0.1.19 — 2026-04-22 (experimental branch: kernel-shell-backend)
- Remove the ugly "Clear selection" button and restore the native DrawControl edit/trash tools. The bbox now lives inside DrawControl's FeatureGroup (not a separate `ipyl.GeoJSON` layer), so the built-in edit/delete controls light up and work as users expect.
- Style the rectangle via `m.draw_control.rectangle = {"shapeOptions": {...}}` so it keeps the red look of the web app while being natively owned by the draw control.
- Handle `"edited"` draw actions: when the user drags a bbox edge/corner, re-run the spatial analysis on the new geometry automatically.
- Single-bbox enforcement now uses `target.data = [geo_json]` when more than one shape is present after a "created" event — no separate GeoJSON layer needed.
- Status panel updated with the hint: "Use the edit or trash tool to modify or remove it."

## kernel-0.1.18 — 2026-04-22 (experimental branch: kernel-shell-backend)
- Fix bbox edit/delete: DrawControl's built-in edit/delete tools cannot interact with our GeoJSON-layer bbox (they only see shapes in DrawControl's FeatureGroup). Replace them with an explicit "Clear selection" button that removes the bbox GeoJSON layer and resets state. Drawing a new rectangle auto-replaces the old one as before.
- Clean up info display: merge `info_html` + `status_html` into a single `status_html` panel. Polygon-click info (shown when no bbox drawn) and bbox results both write to the same widget, avoiding the previous stacked/duplicate display. `on_feature_click` is now a no-op when a bbox is active.
- Format bbox result as a clean three-line card: `Dataset / Points in dataset / Points in bbox` — no trailing instructions.
- Multi-dataset case: also shows the info card for the auto-selected first entry; info card updates when dropdown changes.
- Move `_matches` and `_bbox_layer` declarations to top of script (alongside USER_BBOX/USER_EPT_URL) so they're clearly in module scope.

## kernel-0.1.17 — 2026-04-22 (experimental branch: kernel-shell-backend)
- Dockerfile: pre-install pdal/python-pdal via conda and pyforestscan/laspy via pip. The previous on-the-fly conda install from the skill script was succeeding, but then `pip install pyforestscan` would try to pip-build pdal from source (no system PDAL library found) and fail with a CMake error. Pre-installing in the image removes the on-the-fly install entirely.
- usgs-lidar SKILL.md: 6 UX improvements to Step 1:
  1. Map height reduced to 400px (`m.layout.height = "400px"`) for laptop screens.
  2. Only one bbox at a time: on each new draw, `target.clear()` removes all DrawControl shapes and the previous bbox GeoJSON layer is removed; new bbox added as a fresh red-outline GeoJSON layer.
  3. Single-dataset auto-select: when only one dataset fits, hide the dropdown and show inline "Dataset: X — run the next cell" message.
  4. Bbox delete updates state: `action == "deleted"` clears `USER_BBOX`, `USER_EPT_URL`, dropdown, and status message.
  5. No-match shows message not empty dropdown: when no datasets fit the point limit, hide dropdown and show an italic status message.
  6. Remove install block from Step 1 (all packages now pre-installed in image).

## kernel-0.1.16 — 2026-04-21 (experimental branch: kernel-shell-backend)
- Fix usgs-lidar Step 1 point-count filter: was comparing `count` (total points in entire dataset, e.g. 31 billion for MT statewide) against MAX_POINTS, which always excluded everything. Now computes `count × (intersection_area / dataset_polygon_area)` — the estimated points within the drawn bbox — before filtering. Restores `pyproj` import and `MAX_POINTS = 20_000_000` that were removed in kernel-0.1.15.

## kernel-0.1.15 — 2026-04-21 (experimental branch: kernel-shell-backend)
- usgs-lidar SKILL.md Step 1: restyle coverage map to match the USGS 3DEP web app.
  - Switch data source from `of4d.sdsc.edu/json/usgs_resources.json` to `usgs.entwine.io/boundaries/resources.geojson` (provides `name`, `count`, `url` per feature).
  - Apply 27-color palette (same palette as the web app) with one random color per dataset polygon via `ipyleaflet.GeoJSON(style_callback=...)`.
  - Replace USGS.USTopo basemap with OpenStreetMap (the default, as in the web app).
  - Add click popup via `geo_layer.on_click()` showing dataset name, point count, and EPT link.
  - Update dropdown population: since `density` is not in the new GeoJSON, list all intersecting datasets by name (no point-count estimate filter).

## kernel-0.1.14 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Critical fix: pre-install `ipywidgets`, `ipyleaflet`, `leafmap`, and `plotly` in the Docker image. In earlier kernel-0.1.x runs, the agent had to pip install these on the fly — and because `/opt/conda` is read-only for jovyan, they went to `~/.local`, which means the JupyterLab frontend extension (`@jupyter-widgets/jupyterlab-manager`) was never registered at Lab startup. Confirmed by running `widgets.IntSlider()` in a fresh cell — it rendered as text `IntSlider(...)` instead of a draggable slider. With these packages baked into the image, the frontend extension loads normally at Lab startup and all our backend capture/display work actually becomes visible.

## kernel-0.1.13 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: kernel-0.1.12 correctly captured widgets with widget-view mime keys (log confirmed it), but the cell still showed `Output(outputs=(...))` as text repr. Root cause: the wrapper `Output` widget we used to hold captured items was created inside the async context where ipykernel's comm machinery is suspended — its comm_open never reached the frontend, so the frontend had no model for the wrapper and fell back to text repr. The inner widgets (Map/Dropdown/inner Output) were fine; only the wrapper was dead.
- Drop the wrapper entirely. Capture widget objects via display() interception as before, but queue the widget objects themselves (not a wrapper) into `_sage_pending_displays`. The post-loop flush step in sage_magic.py calls `display(widget)` for each, which sends a normal display comm for already-registered inner widgets.

## kernel-0.1.12 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: `cell_out.append_display_data()` expects an IPython DisplayObject (Markdown/HTML/Image), NOT a raw mime-bundle dict. When I passed a dict, it silently discarded every mime key except text/plain — which is exactly what the 0.1.10/0.1.11 logs showed (`mime keys: ['text/plain']` even though we built bundles with widget-view).
- Bypass the method entirely: build raw `{"output_type": "display_data", "data": ..., "metadata": ...}` dicts and assign them directly to `cell_out.outputs += (...)`. Widget-view mime keys are now preserved.

## kernel-0.1.11 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Diagnostic: add entry-point log (before any imports) in `_run_in_kernel` so "no log file" can be distinguished from "path not reached". Also log the specific ImportError if the widget-capture path falls through.
- Skill: tighten usgs-lidar SKILL.md to explicitly forbid the "copy the code into a cell yourself" escape-hatch that the agent kept falling into. Add rule: install missing packages and re-run, don't give up.

## kernel-0.1.10 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: kernel-0.1.9 diagnostic showed captured `display_data` had only `text/plain` — `application/vnd.jupyter.widget-view+json` was missing, so the frontend had no widget model id to render. Something in IPython's display→publish pipeline strips widget-view in our async context.
- Bypass the pipeline: also monkey-patch `IPython.display.display` (and `IPython.core.display_functions.display`) to capture widget objects directly. After exec, build widget-view mime bundles manually from each widget's `model_id` and `_view_name`. This avoids the mime-filtering step entirely.
- Non-widget objects passed to display() still get best-effort mimebundles via their `_repr_mimebundle_()` method.

## kernel-0.1.9 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: kernel-0.1.8 diagnostic log showed `num outputs: 0` — `with cell_out:` was a silent no-op in our async context, so nothing was captured. Widget reprs and print output bypassed the Output widget entirely and went to stdout/frontend directly.
- Replace `with cell_out:` with manual capture: swap `sys.stdout`, `sys.stderr`, and `ip.display_pub.publish` with our own capture buffers during `exec()`, then explicitly push captured items into `cell_out.outputs` via `append_stdout` / `append_display_data`. This is guaranteed to work regardless of ipywidgets' internal context-manager logic.
- Agent now only sees stdout/stderr text, never widget repr fallback strings.

## kernel-0.1.8 — 2026-04-20 (experimental branch: kernel-shell-backend, DIAGNOSTIC)
- Diagnostic only: write the structure of `cell_out.outputs` to `/tmp/sage_debug.log` so we can see whether captured widgets appear as `display_data` (correct, should render) or as `stream` text (bug — falling back to repr). No functional change.

## kernel-0.1.7 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: display() called from inside loop.run_until_complete() can't send zmq comm messages — the event loop is blocked and the comm machinery is in a partially suspended state. All display attempts from inside tool calls print repr to stdout instead of rendering. Fix: _run_in_kernel() collects Output widgets into user_ns['_sage_pending_displays'] without calling display(). After run_until_complete() returns in the %%ask magic, Sage flushes the list by calling display() for each widget — now in the normal synchronous cell-execution context where zmq works. Widgets are rendered at the bottom of the cell after the agent's text response.

## kernel-0.1.6 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: widgets still not rendering in kernel-0.1.5. exec() without a display container still causes display() to fall back to printing repr. Root fix: create an ipywidgets.Output() in _run_in_kernel() and call display(output_widget) from there — this runs inside the live %%ask execution context so the widget is anchored to the correct cell output area. Then run exec() inside `with output_widget:`, which captures all display() and print() calls into the widget. Text output is extracted from output_widget.outputs afterward so the agent still sees print() feedback. Exceptions are caught and returned as [stderr] lines without appearing in the cell.

## kernel-0.1.5 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: widgets still not rendering in kernel-0.1.4. `ip.run_cell()` creates a new IPython execution context, so `display()` / comm messages (widget creation, display_data) are sent with a mismatched parent_header and don't route to the %%ask cell's output area. Replaced `run_cell()` with plain `exec(compile(code), ip.user_ns)`. This runs inside the existing %%ask cell execution context — display_pub and comm routing are live and correct — so ipywidgets, leafmap, and plotly render. Exceptions caught with try/except + traceback.format_exc().

## kernel-0.1.4 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: ipywidgets not rendering. `capture_output(stdout=True)` replaces `sys.stdout` with a StringIO — IPython's display system detects this is no longer an ipykernel OutStream and falls back to printing widget repr text to sys.stdout instead of sending a display_data comm to the frontend. Replaced `capture_output` with a `_Tee` wrapper that forwards all writes to the real kernel OutStream (so print output appears in the cell AND widgets render normally via display_pub) while also buffering for the agent. stderr is captured-only (not forwarded) to keep the cell clean.

## kernel-0.1.3 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: silently capture tracebacks. Under KernelShellBackend, exceptions raised in the agent's scripts were printed to the cell via IPython's `showtraceback` (bypassing `capture_output(display=False)`), so every recoverable error the agent fixed still bled into the user's output. Override `ip.showtraceback` and `ip.showsyntaxerror` during `run_cell` — capture the full traceback as text for the agent, suppress display to the cell.
- Fix: matplotlib figure double-display. Kernel-mode execution triggers matplotlib inline's `post_run_cell` hook, which auto-displays open figures — the same chart would render once inline and again when the agent's narrative referenced the saved PNG. Append `plt.close('all')` to every wrapped code string so open figures are closed before the hook runs. Safe for ipywidgets and plotly (different display paths).
- UX: `[sage] backend: <ClassName>` banner now prints on every `%%ask`, not once per kernel. Unambiguous verification.

## kernel-0.1.2 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Fix: parser — use `shlex.shlex(posix=True, punctuation_chars=True)` so `;` / `|` / `&` inside quoted `-c` strings don't trigger the shell-pipeline fallback. Previously `python -c "a=1; b=2"` was passed to the subprocess because the semicolon inside the quoted code matched the shell-metachar regex.
- Fix: async path — override `aexecute` to call `execute` directly on the main thread instead of inheriting the default `asyncio.to_thread(self.execute, ...)` dispatch. `run_cell` from a worker thread corrupts IPython state. Sage runs the agent from the main kernel thread in an asyncio loop, so in-loop execution is correct.
- Feature: one-time `[sage] backend: <ClassName>` stderr print on first `%%ask` per kernel, so the active backend is visible for debugging.

## kernel-0.1.1 — 2026-04-20 (experimental branch: kernel-shell-backend)
- Feature: `KernelShellBackend` — subclass of `LocalShellBackend` that routes `python` and `python -c` commands to the live IPython kernel via `get_ipython().run_cell()`, instead of running them in a subprocess. Non-Python commands (`pip`, `conda`, `curl`, shell pipelines with `|`/`>`/`&&`) still pass through to the parent subprocess implementation unchanged.
- Unlocks interactive skills: ipywidgets, leafmap/ipyleaflet, Plotly `fig.show()`, matplotlib inline, `tqdm` progress bars, and live kernel state sharing with the agent — none of which are possible from a subprocess.
- Captures stdout/stderr as text for the agent (via `IPython.utils.capture.capture_output(display=False)`) while still letting rich displays render to the cell.

## v1.0.161 — 2026-04-19
- Fix: scroll-away/back map corruption (continued from v1.0.159/160). DevTools investigation showed the map is rendered directly in the notebook DOM (not inside an iframe, as was the case historically) and the container keeps its real dimensions the entire time — JupyterLab's cell virtualization corrupts Leaflet's internal tile state without resizing the container, so ResizeObserver never fires. Replaced the size-transition trigger with an IntersectionObserver that re-fits the map every time it scrolls back into the viewport. Works because IntersectionObserver now operates against the parent page scroll instead of a Folium iframe. Trade-off: the user's pan/zoom is reset on scroll-away-and-back; acceptable for narrative notebooks where the fit-to-data view is the expected one.

## v1.0.160 — 2026-04-19
- Fix: Folium scroll-away-and-back map bug (continued from v1.0.159). The earlier fix kept `fitBounds()` from running after the first tick to preserve pan/zoom, but scrolling past the cell and back corrupts Leaflet's view state — `invalidateSize()` alone cannot recover and the map falls back to the global top-left tile. New logic: `_tick()` skips entirely when the container is 0x0 (so Leaflet never caches a bad size), and calls `fitBounds()` on every 0-to-real transition (including re-emergence after JupyterLab virtualization), not just the first one.

## v1.0.159 — 2026-04-19
- Fix: Folium map tile bug when scrolling a map out of view and back. The ResizeObserver was disconnected after 20 s, so if JupyterLab collapsed the container to 0x0 on scroll-away and restored it on scroll-back past that window, nothing re-triggered `invalidateSize()` and tiles stayed stuck in the top-left corner. Observer is now permanent; `fitBounds()` runs only on the first real-dimensions tick so the user's pan/zoom state survives later scrolls.
- Fix: py3dep-dem SKILL.md — stronger inline warnings in Example 4 (elevation profile) and Example 5 (REM); tightened function docstrings with explicit "do NOT reimplement" language; `elevation_profile()` returns `(river_elev, distances)` only, matching the refactored functions.

## v1.0.158 — 2026-04-17
- Fix: py3dep-dem skill redesign — visualization code moved out of functions into examples so repeated cells don't regenerate the same PNGs; `elevation_profile()` and `compute_rem()` now return data only
- Feature: `get_dem_bbox(river_line)` — derives a safe 0.1°×0.1° bbox from the river start instead of requiring hardcoded coordinates; works for any river
- Fix: REM enforcement — WARNING statement added inline to Example 5 naming the specific wrong patterns (`distance_transform_edt`, `griddata`, `matplotlib.imshow`, `~dem_transform`) and scoped to all code in the skill, not just computation
- Fix: elevation profile enforcement — WARNING scoped to "your code is never be correct" (all code, not just REM) to prevent agent from substituting its own visualization

## v1.0.157 — 2026-04-17
- Fix: elevation profile spikes in py3dep-dem skill — root cause was `pygeoutils.smooth_linestring` B-spline smoothing cutting corners in canyon sections and placing sample points on canyon walls. Replaced with unsmoothed 50 m interpolation along the original NHD line. Added 5-point median filter to remove residual single-pixel sampling artifacts. CRITICAL warning updated to name the exact forbidden rasterio patterns (`~dem_transform * (x, y)`, `src.index()`) and explicitly cover the "I already have saved files" case.

## v1.0.149 — 2026-04-16
- Feature: show tool output after each tool call. Results appear in a collapsible green block (🔍) beneath the blue tool call block, showing the first line as preview and full output on expand.

## v1.0.147 — 2026-04-16
- Fix: suppress Python 3.13 + nest_asyncio + ipykernel cleanup noise that was printed to cell output on every `%%ask` cell run. The errors ("Exception in callback Task.__step()", "RuntimeError: cannot enter context", "Task was destroyed but it is pending!") are cosmetic artifacts of re-entrant loop cleanup and do not affect execution. Suppressed via a persistent stderr filter and a per-call event loop exception handler override.
- Feature: on rerun, also delete orphaned output files (generated by cells that were stopped/interrupted before finishing and never registered). Previously only this cell's registered files were deleted; now unregistered files in the output folder are cleaned up too.

## v1.0.146 — 2026-04-15
- Fix: last map in a long notebook still reopened stuck on a single global-extent tile. Root cause: previous retry logic locked out after a premature "success" (dimension check passed but Leaflet's tile render hadn't caught up). New approach: idempotent `invalidateSize()` + `fitBounds()` called unconditionally on a schedule (50 ms … 15 s) plus on `resize` / `load` / `ResizeObserver` callbacks, with no one-shot lock. Observer disconnects after 20 s to avoid interfering with user pan/zoom.

## v1.0.145 — 2026-04-14
- Fix: the last map in a long notebook could still reopen stuck on a single global-extent tile in the top-left corner. Combine scheduled retries (100 ms … 8 s) with the `ResizeObserver`, and only mark the map fixed after the container actually reports non-zero dimensions. Whichever mechanism sees real dimensions first wins; a `_fixed` flag prevents double-firing.

## v1.0.144 — 2026-04-14
- Fix: Folium `ResizeObserver` no longer disconnects after 3 s. Maps below the fold at page load weren't getting real dimensions within that window, so `invalidateSize()` + `fitBounds()` never ran and the last maps in a long notebook stayed zoomed out to global extent on reopen. Observer now disconnects only after it fires once with real dimensions.

## v1.0.72 — 2026-04-01
- Fix: add `jupyter_config.py` alongside `jupyter_server_config.py` so the `jupyter trust` CLI and JupyterLab server both use the same persistent notary paths

## v1.0.71 — 2026-04-01
- Fix: also persist the notary **secret key** on persistent storage; a new secret is generated on every pod restart, invalidating all signatures in the DB even when the DB was persisted

## v1.0.70 — 2026-04-01
- Fix: API errors (rate limit, authentication, connection) now show a clean inline message instead of a 600-line traceback

## v1.0.69 — 2026-04-01
- Fix: extend notary DB persistence to local Docker users (uses mounted work dir `-v ~/workspace:/home/jovyan/work`)

## v1.0.68 — 2026-04-01
- Fix: replace timed retries with `ResizeObserver` for Folium map rendering; fires exactly when JupyterLab adds the cell to the DOM and the map container gets real dimensions — reliably fixes corner-tile bug for all maps on notebook reopen

## v1.0.67 — 2026-04-01
- Fix: redirect notary database to NRP CephBlock persistent storage so notebook trust survives JupyterHub pod restarts

## v1.0.66 — 2026-04-01
- Fix: replace `IntersectionObserver` with multiple timed retries of `invalidateSize()` for Folium maps (IntersectionObserver fires immediately inside iframe context, ignoring parent-page scroll)

## v1.0.65 — 2026-04-01
- Fix: GeoJSON properties containing arrays (e.g. `tags`, `resource_formats`) caused "ndarray is not JSON serializable" map render error; geopandas reads JSON arrays as numpy ndarrays — convert to comma-joined strings before passing to Folium

## v1.0.64 — 2026-03-29
- Fix: inject `IntersectionObserver` into each Folium map to call `invalidateSize()` when scrolled into view; fixes corner-tile bug for maps 2+ on notebook reopen
- Fix: auto-run `jupyter trust` after each `%%ask` run so HTML/JS outputs are not flagged as untrusted on reopen

## v1.0.63 — 2026-03-29
- Change: `%reset` now clears `.sage_run.jsonl` execution log (reset = start completely fresh)

## v1.0.62 — 2026-03-28
- Fix: `sage-metrics` skill — added CRITICAL "do not search for log files" rule; fixed bare stem resolution so `earthquake_gnss` (no `.ipynb`) works as well as `earthquake_gnss.ipynb`

## v1.0.61 — 2026-03-27
- Fix: re-push of v1.0.60 under new tag (NRP had cached v1.0.60 before the final skill was ready)

## v1.0.60 — 2026-03-27
- Feature: rename log file to `.sage_run.jsonl` (hidden, won't clutter output folder)
- Feature: add `sage-metrics` built-in skill for analyzing execution metrics and self-correction counts across notebooks

## v1.0.59 — 2026-03-26
- Feature: per-cell execution logging to `.sage_run.jsonl` — records timestamp, prompt, elapsed time, tool call counts; `%reset` preserves log

## v1.0.58 — 2026-03-25
- Fix: strip single-column table rows (`| content |` → `content`)
- Fix: `asyncio.run()` → `loop.run_until_complete()` for Python 3.13 compatibility
- Fix: suppress pyogrio "Non closed ring" warning on GeoJSON load

## v1.0.57 — 2026-03-24
- Fix: bold marker regex Phase 2b — `[^*]` → `[^*\n]` to prevent cross-line matching that was inserting spaces into unrelated bold pairs

## v1.0.55–v1.0.56 — 2026-03-24
- Debug: added logging to trace bold corruption; discovered cross-line matching bug fixed in v1.0.57

## v1.0.54 — 2026-03-24
- Fix: complete rewrite of bold `**` regex strategy — Phase 1 uses paired regex for internal spacing; Phase 2 uses narrowed `\w**\w` patterns to prevent cross-pair matching

## v1.0.53 — 2026-03-23
- Fix: skillsmp SKILL.md — forceful CRITICAL instruction to always run API key-loading code

## v1.0.52 — 2026-03-23
- Fix: table layout regression — `##` heading regex now excludes `|`, `#`, whitespace as preceding char so table cells like `| # Tag |` are not broken

## v1.0.44–v1.0.51 — 2026-03-22
- Fix/Feature: skillsmp SKILL.md rewrites; `%reset` magic; image dimension caps; code fence fix; heading-newline fix; numbered list fix; bold regex improvements

## v1.0.43 — 2026-03-21
- Fix: GLM writes multiple table rows on one line — split collapsed rows; add prompt rule for table formatting

## v1.0.42 — 2026-03-21
- Fix: skillsmp SKILL.md — use `dotenv.load_dotenv()` for key loading; add skill lookup by name

## v1.0.41 — 2026-03-21
- Fix: skillsmp SKILL.md — correct API key loading order; always use Python for API calls (never curl)

## v1.0.40 — 2026-03-20
- Fix: bold marker regex — use `\s+` to catch non-breaking space (U+00A0) and other Unicode whitespace GLM emits after `**`

## v1.0.39 — 2026-03-20
- Fix: escape all `$` as `&#36;` to prevent JupyterLab MathJax from consuming `$...$` spans
- Fix: skip silently when a referenced file is not found (was showing broken "Image" alt text)

## v1.0.38 — 2026-03-20
- Fix: apply `_fix_glm_markdown` in streaming narration (`_flush_text`) as well as final report — bold markers in mid-stream text were going unpatched

## v1.0.37 — 2026-03-19
- Fix: inject `sys.executable` path into prompt so agent uses the correct Python interpreter without searching

## v1.0.36 — 2026-03-19
- Fix: broken table separator rows from GLM (`|:Label:|` → `|---|`); moved all GLM markdown fixes into shared `_fix_glm_markdown()`

## v1.0.35 — 2026-03-19
- Fix: double-display bug — `_render_markdown_with_files` was displaying remaining text even when no file refs found, then returning False, causing caller to display again

## v1.0.18–v1.0.34 — 2026-03-15 to 2026-03-18
- Rework: agent backend refactored to use `create_deep_agent` + `LocalShellBackend`; `SAGE_MESSAGES` for cross-cell memory; message deduplication in streaming loop

## v1.0.17 — 2026-03-14
- Fix: insert space after closing `**` bold markers when immediately followed by non-space (GLM quirk)

## v1.0.16 — 2026-03-14
- Fix: PNG display — max-width 600px, width:auto (caps large images, no upscaling)

## v1.0.15 — 2026-03-13
- Fix: robust duplicate message detection — detect text→text transition with no tool call in between in streaming loop; discard second message at source

## v1.0.14 — 2026-03-13
- Fix: improved duplicate detection search range (35–65%); superseded by v1.0.15

## v1.0.13 — 2026-03-13
- Fix: prompt rule with correct/wrong example to prevent agent from creating separate maps for layers that should be combined

## v1.0.12 — 2026-03-12
- Fix: duplicate final summary — LangGraph replays final AIMessage with `durability="exit"`; strip exact duplication before returning

## v1.0.11 — 2026-03-12
- Feature: integrated markdown output — agent embeds `![caption](file.geojson)` and `![caption](file.png)` inline in final report; `_render_markdown_with_files()` renders maps/images where referenced

## v1.0.10 — 2026-03-11
- Fix: `bash: n#: command not found` error in terminal — use `printf` instead of `echo` for `.bashrc` newline

## v1.0.9 — 2026-03-10
- Fix: add 12px bottom margin after each tool call panel for cleaner visual separation

## v1.0.8 — 2026-03-10
- Fix: narration now displays in correct order — before each tool call, not all at end

## v1.0.7 — 2026-03-09
- Fix: prevent agent from reading binary files (PNG, GeoTIFF) with `read_file` — they crash the agent

## v1.0.6 — 2026-03-09
- Feature: thinking-out-loud narration before each tool call; PNG display capped at 800px wide

## v1.0.5 — 2026-03-08
- Feature: persistent output directory next to notebook (`_{notebook_stem}_sage_/`); fallback to `/tmp/sage/{thread_id}` for terminal kernels

## v1.0.4 — 2026-03-07
- Feature: add `rasterio`; add 4 Kanawha flood skills (`kanawha-flood-depth`, `kanawha-reach-impact`, `kanawha-cikr-impact`, `kanawha-nsi-impact`)

## v1.0.3 — 2026-03-06
- Feature: WMS layer support — `.wms.json` files auto-displayed on combined Folium map

## v1.0.2 — 2026-03-05
- Fix: short thread ID (8 chars); all generated files directed to output dir instead of `/tmp`

## v1.0.1 — 2026-03-04
- Initial release: Sage image with `%ask`/`%%ask` magic, NRP GLM-4.7 provider, core sage_skills
