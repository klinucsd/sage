# Changelog

All notable changes to the Sage Docker image are documented here.

---

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
