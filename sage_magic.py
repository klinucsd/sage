"""
Sage IPython magic commands.

Auto-loaded at kernel startup via:
  ~/.ipython/profile_default/startup/00-sage-magic.py

Registers:
  %ask  <prompt>   — line magic (single-line prompt)
  %%ask            — cell magic (multi-line prompt in cell body)

After each run, new/modified files in the output folder are auto-displayed:
  .geojson → interactive Folium map
  .csv     → pandas DataFrame table
  .png     → inline image

NRP_API_KEY lookup order:
  1. /home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env
  2. .env in the current working directory (re-checked on each call)
  3. Already set in the environment (ENV)
"""

import asyncio
import json
import os
import warnings
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Per-kernel state — unique per notebook, persists for the session
# ---------------------------------------------------------------------------

# Single thread ID for the whole kernel session — gives the agent memory
# across all %%ask cells in the same notebook.
from deepagents_cli.sessions import generate_thread_id  # noqa: E402
SAGE_THREAD_ID = generate_thread_id()[:8]

# Conversation history for cross-cell memory — maintained in Python, no SQLite checkpointer.
# Each entry is {"role": "user"|"assistant", "content": "..."}.
SAGE_MESSAGES: list = []
SAGE_SHOW_TOOL_OUTPUT: bool = False  # toggled by %tool_output_on / %tool_output_off

# ---------------------------------------------------------------------------
# Output directory — persistent, next to the notebook
# ---------------------------------------------------------------------------
# JPY_SESSION_NAME is set per-kernel by JupyterHub/JupyterLab to the notebook
# path (e.g. /home/jovyan/work/Sage/earthquake_gnss.ipynb).
# We derive a fixed folder name from it so files persist across sessions.
# The folder is never auto-cleared — users manage its contents themselves.
# Fallback: /tmp/sage/{thread_id}/ for terminal/console kernels (ephemeral is fine).

def _init_output_dir() -> str:
    session = os.environ.get("JPY_SESSION_NAME", "")
    if session:
        nb_path = Path(session)
        nb_stem = nb_path.stem  # e.g. "earthquake_gnss"
        nb_dir = nb_path.parent
        # If path is relative, resolve against home dir
        if not nb_dir.is_absolute():
            nb_dir = Path.home() / nb_dir
        out = nb_dir / f"_{nb_stem}_sage_"
    else:
        out = Path(f"/tmp/sage/{SAGE_THREAD_ID}")

    out.mkdir(parents=True, exist_ok=True)
    return str(out)

SAGE_OUTPUT_DIR = _init_output_dir()

# Expose both in IPython namespace so users can reference them
try:
    ip = get_ipython()  # noqa: F821
    ip.user_ns["SAGE_OUTPUT_DIR"] = SAGE_OUTPUT_DIR
    ip.user_ns["SAGE_THREAD_ID"] = SAGE_THREAD_ID
except Exception:
    pass

# ---------------------------------------------------------------------------
# NRP_API_KEY — load from known locations at startup
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    _PERSISTENT_ENV = Path(
        "/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env"
    )
    if _PERSISTENT_ENV.exists():
        load_dotenv(dotenv_path=_PERSISTENT_ENV, override=False)

    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)

except ImportError:
    pass

# ---------------------------------------------------------------------------
# nest_asyncio — required for asyncio.run() inside Jupyter's event loop
# ---------------------------------------------------------------------------
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    warnings.warn(
        "nest_asyncio is not installed — Sage magic may not work in notebooks. "
        "Fix: pip install nest_asyncio",
        stacklevel=1,
    )

# ---------------------------------------------------------------------------
# Python 3.13 + nest_asyncio + ipykernel produce unavoidable cleanup noise:
#   "Exception in callback Task.__step() / RuntimeError: cannot enter context"
#   "Task was destroyed but it is pending!"
#   "RuntimeWarning: coroutine 'Kernel.shell_main' was never awaited"
# These are cosmetic artifacts of re-entrant loop cleanup. Suppress them.
# ---------------------------------------------------------------------------
import sys as _sys

class _AsyncioNoiseFilter:
    _SUPPRESSED = (
        "RuntimeError: cannot enter context",
        "Task was destroyed but it is pending!",
        "task: <Task pending",
        "handle: <Handle Task.__step",
        "Exception in callback Task.__step",
        "RuntimeWarning: coroutine 'Kernel.shell_main' was never awaited",
        "RuntimeWarning: Enable tracemalloc to get the object allocation traceback",
        "<frozen os>:",
    )
    def __init__(self, stream):
        self._stream = stream
    def write(self, text):
        if not any(frag in text for frag in self._SUPPRESSED):
            self._stream.write(text)
        return len(text)
    def flush(self):
        self._stream.flush()
    def __getattr__(self, name):
        return getattr(self._stream, name)

if not isinstance(_sys.stderr, _AsyncioNoiseFilter):
    _sys.stderr = _AsyncioNoiseFilter(_sys.stderr)

warnings.filterwarnings("ignore", message="coroutine '.*' was never awaited")
warnings.filterwarnings("ignore", message="Enable tracemalloc")

del _sys


# ---------------------------------------------------------------------------
# Tool display
# ---------------------------------------------------------------------------

TOOL_ICONS = {
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "✂️",
    "ls": "📁",
    "glob": "📁",
    "grep": "🔍",
    "execute": "⚙️",
    "web_search": "🌐",
    "http_request": "🌐",
    "fetch_url": "🌐",
    "task": "🤖",
    "write_todos": "📋",
}


def _format_tool_summary(tool_name: str, args: dict) -> str:
    """One-line summary of a tool call."""
    if tool_name == "execute":
        command = args.get("command", "")
        if len(command) > 80:
            command = command[:77] + "..."
        return f"Executing: <code>{command}</code>"
    if tool_name == "read_file":
        return f"Reading: <code>{args.get('file_path', '?')}</code>"
    if tool_name == "write_file":
        return f"Writing: <code>{args.get('file_path', '?')}</code>"
    if tool_name == "edit_file":
        return f"Editing: <code>{args.get('file_path', '?')}</code>"
    if tool_name == "http_request":
        method = args.get("method", "GET")
        url = args.get("url", "?")
        if len(url) > 80:
            url = url[:77] + "..."
        return f"{method} <code>{url}</code>"
    if tool_name == "fetch_url":
        url = args.get("url", "?")
        if len(url) > 80:
            url = url[:77] + "..."
        return f"Fetching: <code>{url}</code>"
    if tool_name == "web_search":
        return f"Searching: <code>{args.get('query', '?')}</code>"
    if tool_name == "write_todos":
        todos = args.get("todos", [])
        return f"{len(todos)} todo(s)"
    if tool_name == "task":
        subagent = args.get("subagent_type", "?")
        return f"Subagent: {subagent}"
    args_str = json.dumps(args, ensure_ascii=False)
    if len(args_str) > 100:
        args_str = args_str[:97] + "..."
    return f"<code>{args_str}</code>"


def _format_tool_details(tool_name: str, args: dict) -> str:
    """Full detail block for inside the <details> element."""
    if tool_name == "execute":
        command = args.get("command", "")
        escaped = command.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre style='margin:0'>{escaped}</pre>"
    if tool_name == "write_todos":
        todos = args.get("todos", [])
        status_icon = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}
        lines = [
            f"{status_icon.get(t.get('status', ''), '•')} {t.get('content', '')}"
            for t in todos
        ]
        return "<br>".join(lines)
    pretty = json.dumps(args, indent=2, ensure_ascii=False)
    escaped = pretty.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<pre style='margin:0'>{escaped}</pre>"


def _display_tool_call(tool_name: str, args: dict) -> None:
    """Render a tool call with summary + collapsible full details."""
    from IPython.display import display, HTML

    icon = TOOL_ICONS.get(tool_name, "🔧")
    summary = _format_tool_summary(tool_name, args)
    details = _format_tool_details(tool_name, args)

    # write_todos expanded by default (useful progress indicator)
    open_attr = " open" if tool_name == "write_todos" else ""

    html = f"""
<div style="background:#f5f7ff; border-left:3px solid #4a7fd4;
            padding:5px 10px; margin:3px 0 12px 0; font-size:0.85em;">
  {icon} <b>{tool_name}</b> — {summary}
  <details{open_attr}>
    <summary style="cursor:pointer; color:#888; font-size:0.9em;">details</summary>
    <div style="margin-top:4px; font-family:monospace; font-size:0.9em;">
      {details}
    </div>
  </details>
</div>"""
    display(HTML(html))


def _display_tool_result(tool_name: str, content: str) -> None:
    """Render a tool result as a collapsible output block, capped at 100 lines / 3000 chars."""
    if not SAGE_SHOW_TOOL_OUTPUT:
        return

    from IPython.display import display, HTML

    def _esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    _CHAR_LIMIT = 3000
    _LINE_LIMIT = 100

    lines = content.splitlines()
    total_lines = len(lines)
    shown_lines = lines[:_LINE_LIMIT]
    shown = "\n".join(shown_lines)

    # Apply char limit after line limit
    char_truncated = len(shown) > _CHAR_LIMIT
    if char_truncated:
        shown = shown[:_CHAR_LIMIT]

    line_truncated = total_lines > _LINE_LIMIT
    if line_truncated:
        shown += f"\n… ({total_lines - _LINE_LIMIT} more lines)"
    elif char_truncated:
        shown += f"\n… (truncated at {_CHAR_LIMIT} chars)"

    escaped = _esc(shown)

    html = (
        '<div style="background:#f0fff4; border-left:3px solid #4caf50;'
        '            padding:5px 10px; margin:4px 0 12px 0; font-size:0.85em;">'
        f'  🔍 <b>{tool_name}</b> output ({total_lines} lines)'
        '  <details>'
        '    <summary style="cursor:pointer; color:#888; font-size:0.9em;">show output</summary>'
        '    <div style="margin-top:4px; font-family:monospace; font-size:0.9em; white-space:pre-wrap;">'
        f'{escaped}'
        '    </div>'
        '  </details>'
        '</div>'
    )
    display(HTML(html))


# ---------------------------------------------------------------------------
# File change detection
# ---------------------------------------------------------------------------

def _snapshot(folder: str) -> dict:
    """Return {filepath: mtime} for all files currently in folder."""
    result = {}
    for f in Path(folder).rglob("*"):
        if f.is_file():
            result[str(f)] = f.stat().st_mtime
    return result


def _new_files(before: dict, after: dict) -> list:
    """Return files that are new or modified since the snapshot."""
    return [
        f for f, mtime in after.items()
        if f not in before or before[f] != mtime
    ]


# Internal files that should never be tracked as cell outputs
_SAGE_INTERNAL_FILES = {".sage_cells.json", ".sage_run.jsonl", ".sage_colors.json"}


def _get_cell_id() -> str | None:
    """Return the current cell's unique ID from IPython kernel metadata, or None."""
    try:
        return get_ipython().parent_header.get('metadata', {}).get('cellId')  # noqa: F821
    except Exception:
        return None


def _load_cell_registry() -> dict:
    """Load .sage_cells.json — maps cell_id → list of files it created."""
    p = Path(SAGE_OUTPUT_DIR) / ".sage_cells.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_cell_registry(registry: dict) -> None:
    """Persist .sage_cells.json."""
    (Path(SAGE_OUTPUT_DIR) / ".sage_cells.json").write_text(
        json.dumps(registry, indent=2)
    )


def _load_color_registry() -> dict:
    """Load .sage_colors.json — maps field → {title, palette} for all classification
    schemes established in this notebook (persists across kernel restarts)."""
    p = Path(SAGE_OUTPUT_DIR) / ".sage_colors.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_color_registry(registry: dict) -> None:
    """Persist .sage_colors.json."""
    (Path(SAGE_OUTPUT_DIR) / ".sage_colors.json").write_text(
        json.dumps(registry, indent=2)
    )


def _update_color_registry(new_files: list[str]) -> None:
    """Scan newly created .colormap.json files and merge into the registry.

    Only adds new fields — never overwrites an existing field's scheme,
    since established classifications must remain stable.
    """
    registry = _load_color_registry()
    changed = False
    for f in new_files:
        if not f.endswith(".colormap.json"):
            continue
        try:
            cm = json.loads(Path(f).read_text())
            field = cm.get("field")
            palette = cm.get("palette")
            if field and palette and field not in registry:
                registry[field] = {
                    "title": cm.get("title", field),
                    "palette": palette,
                }
                changed = True
        except Exception:
            continue
    if changed:
        _save_color_registry(registry)


def _color_registry_prompt() -> str:
    """Build the EXISTING CLASSIFICATIONS prompt block from the color registry.

    Returns an empty string when the registry is empty.
    """
    registry = _load_color_registry()
    if not registry:
        return ""

    # Build per-scheme lines and collect all assigned color→category mappings
    scheme_lines = []
    color_owners = {}  # hex → "category (field)" for the forbidden list
    for field, entry in registry.items():
        palette = entry.get("palette", {})
        title = entry.get("title", field)
        items = ", ".join(f"{cat} → {color}" for cat, color in palette.items())
        scheme_lines.append(f"  {field} ({title}): {items}")
        for cat, color in palette.items():
            if color not in color_owners:
                color_owners[color] = f"{cat} in {field}"

    forbidden_lines = "\n".join(
        f"  {color} — already means '{owner}'"
        for color, owner in sorted(color_owners.items())
    )

    block = (
        "EXISTING CLASSIFICATION SCHEMES — earlier cells (or a previous session) of "
        "this notebook have established these schemes. "
        "If your data fits naturally into one of them, reuse it exactly (same category "
        "labels and same colors). "
        "If your data needs a different classification, you may define a new scheme — "
        "but you MUST choose colors that do not appear in the FORBIDDEN list below.\n"
        + "\n".join(scheme_lines)
        + "\n"
        "FORBIDDEN COLORS — these hex values are already assigned to specific categories "
        "in this notebook. Do NOT use any of them for any new category. "
        "Reusing a forbidden color for a different meaning will make the map legend "
        "wrong and confuse the user:\n"
        + forbidden_lines
        + "\n"
    )
    return block


# ---------------------------------------------------------------------------
# Output file display
# ---------------------------------------------------------------------------

_LAYER_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12",
    "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
]


def _build_legend_panel_html(legend_entries: list) -> str:
    """Build a single scrollable legend panel containing all layer legends.

    legend_entries: list of (title, palette) tuples.
    Rendered as a fixed bottom-right panel, ArcGIS-style.
    """
    sections = ""
    for title, palette in legend_entries:
        items = "".join(
            f'<div style="display:flex;align-items:center;margin:2px 0">'
            f'<div style="width:12px;height:12px;background:{color};border-radius:50%;'
            f'flex-shrink:0;margin-right:7px;border:1px solid rgba(0,0,0,0.2)"></div>'
            f'<span style="font-size:11px">{label}</span></div>'
            for label, color in palette.items()
        )
        sections += (
            f'<div style="margin-bottom:8px">'
            f'<div style="font-weight:600;font-size:11px;color:#333;margin-bottom:3px;'
            f'padding-bottom:3px;border-bottom:1px solid #e8e8e8">{title}</div>'
            f'{items}</div>'
        )
    return (
        f'<div style="position:fixed;bottom:30px;left:10px;z-index:9999;'
        f'background:white;padding:8px 12px;border-radius:6px;'
        f'box-shadow:0 2px 8px rgba(0,0,0,0.3);font-family:sans-serif;'
        f'min-width:150px;max-width:200px;max-height:320px;overflow-y:auto">'
        f'<div style="font-weight:700;font-size:12px;margin-bottom:6px;'
        f'padding-bottom:4px;border-bottom:2px solid #ddd;color:#222">Legend</div>'
        f'{sections}</div>'
    )


def _display_combined_map(
    geojson_files: list[Path],
    wms_files: list[Path],
    show_header: bool = True,
    caption: str = "",
) -> None:
    """Render all GeoJSON and WMS layers on a single Folium map."""
    from IPython.display import display, HTML
    try:
        import folium
        import geopandas as gpd
        import json as _json

        geojson_layers = []  # (name, gdf, colormap_or_None)
        for path in geojson_files:
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Non closed ring")
                    warnings.filterwarnings("ignore", message="Could not parse column")
                    gdf = gpd.read_file(path)
                import numpy as _np
                for col in gdf.columns:
                    if col == "geometry":
                        continue
                    if str(gdf[col].dtype).startswith("datetime"):
                        gdf[col] = gdf[col].astype(str)
                    elif gdf[col].dtype == object:
                        # Array-valued properties (e.g. "tags", "resource_formats")
                        # are read by geopandas as numpy arrays, which Folium cannot
                        # serialize to JSON. Convert them to comma-joined strings.
                        try:
                            gdf[col] = gdf[col].apply(
                                lambda v: ", ".join(str(x) for x in v)
                                if isinstance(v, (_np.ndarray, list))
                                else v
                            )
                        except Exception:
                            pass
                # Load colormap sidecar if present (same base name, .colormap.json)
                colormap = None
                cm_path = path.parent / (path.stem + ".colormap.json")
                if cm_path.exists():
                    try:
                        colormap = _json.loads(cm_path.read_text())
                    except Exception:
                        pass
                geojson_layers.append((path.stem, gdf, colormap))
            except Exception:
                continue

        wms_layers = []
        for path in wms_files:
            try:
                wms = _json.loads(path.read_text())
                wms_layers.append(wms)
            except Exception:
                continue

        if not geojson_layers and not wms_layers:
            return

        # Determine map center from GeoJSON bounds or WMS bbox
        fit_bounds = None  # [[south, west], [north, east]] for auto-zoom
        if geojson_layers:
            all_bounds = [gdf.total_bounds for _, gdf, _ in geojson_layers]
            minx = min(b[0] for b in all_bounds)
            miny = min(b[1] for b in all_bounds)
            maxx = max(b[2] for b in all_bounds)
            maxy = max(b[3] for b in all_bounds)
            center = [(miny + maxy) / 2, (minx + maxx) / 2]
            fit_bounds = [[miny, minx], [maxy, maxx]]
        else:
            # bbox format: [min_lat, min_lon, max_lat, max_lon]
            bbox = wms_layers[0].get("bbox")
            if bbox:
                center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
                fit_bounds = [[bbox[0], bbox[1]], [bbox[2], bbox[3]]]
            else:
                center = [39.5, -98.5]  # continental US fallback

        m = folium.Map(location=center, zoom_start=4, tiles=None)

        # Background tile layers (first = default)
        folium.TileLayer("OpenStreetMap", name="Street Map").add_to(m)
        folium.TileLayer("CartoDB.Positron", name="Light", show=False).add_to(m)
        folium.TileLayer("Esri.WorldTopoMap", name="Topographic", show=False).add_to(m)
        folium.TileLayer("Esri.WorldImagery", name="Satellite", show=False).add_to(m)

        # Add GeoJSON layers
        legend_entries = []  # (title, palette, n_items) for legend stacking
        legend_fields_seen = set()  # deduplicate: one legend entry per field name
        for i, (name, gdf, colormap) in enumerate(geojson_layers):
            color = _LAYER_COLORS[i % len(_LAYER_COLORS)]
            popup_fields = [c for c in gdf.columns if c not in ("geometry", "_color")][:5]
            fg = folium.FeatureGroup(name=name, show=True)

            # Apply colormap sidecar if present
            if colormap:
                field = colormap.get("field")
                palette = colormap.get("palette", {})
                if field and field in gdf.columns and palette:
                    gdf = gdf.copy()
                    gdf["_color"] = gdf[field].map(palette).fillna("#999999")
                    # Add legend only once per field — multiple layers sharing the
                    # same classification field (e.g. two earthquake GeoJSONs both
                    # using magnitude_class) should not duplicate the legend entry.
                    if field not in legend_fields_seen:
                        present = set(gdf[field].dropna().unique())
                        visible_palette = {k: v for k, v in palette.items() if k in present}
                        # Fallback: if no palette keys match the data values (e.g. label
                        # mismatch between colormap and classification code), show the
                        # full palette rather than silently dropping the legend.
                        if not visible_palette:
                            visible_palette = palette
                        legend_entries.append((
                            colormap.get("title", name),
                            visible_palette,
                            len(visible_palette),
                        ))
                        legend_fields_seen.add(field)

            has_color_col = "_color" in gdf.columns
            folium.GeoJson(
                gdf,
                marker=folium.CircleMarker(radius=5, fill=True),
                style_function=(
                    lambda x: {
                        "fillColor": x["properties"].get("_color") or "#3388ff",
                        "color": "#333333",
                        "weight": 0.5, "fillOpacity": 0.85,
                    }
                ) if has_color_col else (
                    lambda x, c=color: {
                        "fillColor": c, "color": "#333333",
                        "weight": 0.5, "fillOpacity": 0.8,
                    }
                ),
                popup=folium.GeoJsonPopup(fields=popup_fields) if popup_fields else None,
            ).add_to(fg)
            fg.add_to(m)

        # Add WMS layers
        for wms in wms_layers:
            # layers must be a comma-separated string; accept list too
            layers_val = wms["layers"]
            if isinstance(layers_val, list):
                layers_val = ",".join(layers_val)
            folium.raster_layers.WmsTileLayer(
                url=wms["url"],
                layers=layers_val,
                name=wms.get("name", "WMS Layer"),
                fmt=wms.get("fmt", "image/png"),
                transparent=True,
                opacity=wms.get("opacity", 0.7),
            ).add_to(m)

        # Fit map to data extent; max_zoom prevents over-zooming on single points
        if fit_bounds:
            m.fit_bounds(fit_bounds, max_zoom=10)

        # Add single scrollable legend panel containing all colormap layers
        if legend_entries:
            panel_data = [(title, palette) for title, palette, _ in legend_entries]
            m.get_root().html.add_child(
                folium.Element(_build_legend_panel_html(panel_data))
            )

        folium.LayerControl(collapsed=False).add_to(m)

        # Fix: on notebook reopen, two problems occur:
        # 1. Maps render as a single tile in the top-left corner (container has
        #    zero size when Leaflet initializes → invalidateSize() fixes this).
        # 2. fitBounds runs at page load before the container has real dimensions,
        #    so Leaflet calculates the wrong zoom; after invalidateSize() the tiles
        #    render but the extent stays at the wrong zoom.
        # Both are fixed by calling invalidateSize() + fitBounds() together when
        # the ResizeObserver fires (i.e. exactly when the container gets real size).
        # The bounds are baked into the script at render time so they are always
        # available, even after a kernel restart.
        map_var = m.get_name()
        if fit_bounds:
            _s, _w = fit_bounds[0]   # [south, west]
            _n, _e = fit_bounds[1]   # [north, east]
            fit_js = (
                f"{map_var}.fitBounds([[{_s},{_w}],[{_n},{_e}]],"
                f"{{maxZoom:10}});"
            )
        else:
            fit_js = ""
        # Append to get_root().script (runs AFTER folium's own init +
        # fit_bounds) rather than .html (which places the script BEFORE
        # L.map(...) and fitBounds in the rendered output — at which point
        # the map variable is undefined and the IIFE is racing Leaflet's
        # own init).
        m.get_root().script.add_child(folium.Element(f"""
(function() {{
    // Scroll-away-and-back corruption:
    //   JupyterLab's cell virtualization scrolls the map offscreen and back
    //   without changing its container's dimensions. Leaflet's internal tile
    //   state gets corrupted (map reverts to a single top-left tile and zoom
    //   controls stop responding) but invalidateSize() alone cannot recover
    //   — a fitBounds() is required.
    //   ResizeObserver doesn't fire because the container size never changes,
    //   so we use IntersectionObserver to detect viewport re-entry. Maps used
    //   to render inside a Folium iframe, where IntersectionObserver fires
    //   immediately regardless of parent scroll (v1.0.66 history); now the
    //   map is rendered directly into the notebook DOM, so it works.
    function _fit() {{
        if (typeof {map_var} === 'undefined') return;
        try {{
            var _el = document.getElementById('{map_var}');
            if (!_el || _el.offsetWidth === 0 || _el.offsetHeight === 0) return;
            {map_var}.invalidateSize();
            {fit_js}
        }} catch (e) {{ /* ignore */ }}
    }}
    [50, 150, 400, 800, 1500, 3000, 6000, 10000, 15000].forEach(function(ms) {{
        setTimeout(_fit, ms);
    }});
    if (typeof window !== 'undefined' && window.addEventListener) {{
        window.addEventListener('load', _fit);
        window.addEventListener('resize', _fit);
    }}
    var _el0 = document.getElementById('{map_var}');
    if (_el0 && window.IntersectionObserver) {{
        // Re-fit every time the map scrolls back into the viewport. Trade-off:
        // user's pan/zoom is reset on scroll-away-and-back. Acceptable for
        // narrative notebooks where the fit-to-data view is what the reader
        // expects; recovery from the corruption is the priority.
        new IntersectionObserver(function(entries) {{
            entries.forEach(function(e) {{ if (e.isIntersecting) _fit(); }});
        }}, {{ threshold: 0.01 }}).observe(_el0);
    }}
    if (_el0 && window.ResizeObserver) {{
        new ResizeObserver(_fit).observe(_el0);
    }}
}})();
"""))

        # Build header HTML and combine with map in a single display() call
        # to avoid extra inter-output gaps in nbviewer
        if caption:
            header = (
                f'<div style="font-weight:bold; font-size:0.95em; '
                f'margin:10px 0 4px 0;">{caption}</div>'
            )
        elif show_header:
            all_names = [name for name, _, _ in geojson_layers] + [w.get("name", "WMS") for w in wms_layers]
            header = f"<b>Map:</b> {', '.join(all_names)}<br>"
        else:
            header = ""
        display(HTML(header + m._repr_html_()))

    except ImportError as e:
        display(HTML(f"Map — install folium+geopandas to render: {e}"))
    except Exception as e:
        import traceback as _tb
        display(HTML(
            f'<div style="color:#c00;font-family:monospace;font-size:0.85em;'
            f'white-space:pre-wrap">Map — error rendering: {e}\n\n{_tb.format_exc()}</div>'
        ))


def _display_csv(path: Path) -> None:
    from IPython.display import display, HTML
    try:
        import pandas as pd
        df = pd.read_csv(path)
        display(HTML(
            f"<b>{path.name}</b> — {len(df):,} rows × {len(df.columns)} columns"
        ))
        display(df)
    except Exception as e:
        display(HTML(f"<b>{path.name}</b> — error reading CSV: {e}"))


def _display_png(path: Path, caption: str = None) -> None:
    from IPython.display import display, HTML
    import base64
    try:
        data = base64.b64encode(path.read_bytes()).decode()
        label = caption if caption else path.name
        display(HTML(
            f'<div style="margin:6px 0;">'
            f'<div style="font-size:0.85em; margin-bottom:4px;"><b>{label}</b></div>'
            f'<img src="data:image/png;base64,{data}" '
            f'style="max-width:600px; width:auto; height:auto; display:block;"/>'
            f'</div>'
        ))
    except Exception as e:
        display(HTML(f"<b>{path.name}</b> — error displaying image: {e}"))


def _display_new_outputs(new: list) -> None:
    new_paths = [Path(f) for f in new]

    # If any new GeoJSON or WMS layers: re-render combined map with ALL layers in output dir
    has_new_map_layer = any(
        p.suffix == ".geojson" or p.name.endswith(".wms.json")
        for p in new_paths
    )
    if has_new_map_layer:
        all_geojsons = sorted(Path(SAGE_OUTPUT_DIR).rglob("*.geojson"))
        all_wms = sorted(Path(SAGE_OUTPUT_DIR).rglob("*.wms.json"))
        if all_geojsons or all_wms:
            _display_combined_map(all_geojsons, all_wms)

    # Show new CSVs and PNGs (per-run)
    for path in sorted(new_paths, key=lambda p: (p.suffix, str(p))):
        if path.suffix == ".csv":
            _display_csv(path)
        elif path.suffix == ".png":
            _display_png(path)


# ---------------------------------------------------------------------------
# Markdown post-processing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GLM markdown post-processing
# ---------------------------------------------------------------------------

def _fix_glm_markdown(text: str) -> str:
    """Fix GLM-specific markdown formatting quirks before rendering."""
    import re
    lines = text.split("\n")
    out = []
    for line in lines:
        # Fix broken table separator rows: |:Something:|...|  →  |---|...|
        # GLM sometimes writes |:Label:| instead of |---|
        if line.startswith("|") and line.rstrip().endswith("|"):
            cells = line.split("|")
            inner = [c.strip() for c in cells[1:-1]]
            # Single-column rows (| content |) aren't valid markdown tables — strip pipes.
            # Skip separator rows (only dashes/colons/spaces).
            if len(inner) == 1 and inner[0] and not all(c in "-: " for c in inner[0]):
                line = inner[0]
            # Fix broken table separator rows: |:Something:|...|  →  |---|...|
            # GLM sometimes writes |:Label:| instead of |---|
            elif inner and all(
                (c.startswith(":") and c.endswith(":") and len(c) > 2)
                or c == "---"
                or c == ":---"
                or c == "---:"
                or c == ":---:"
                for c in inner
            ) and any(c.startswith(":") and c.endswith(":") and not set(c.strip(":")) <= {"-"} for c in inner):
                line = "|" + "|".join(" --- " for _ in inner) + "|"
        out.append(line)
    text = "\n".join(out)
    # Fix collapsed table rows: GLM sometimes writes multiple rows on one line.
    # Also fixes lines where a heading/prefix is concatenated with table content.
    def _split_table_rows(table_str):
        """Split a collapsed table string (all rows on one line) into separate lines."""
        parts = re.split(r'\|\s*\|', table_str)
        rows = []
        for i, part in enumerate(parts):
            if i == 0:
                rows.append(part + "|")
            elif i == len(parts) - 1:
                rows.append("|" + part)
            else:
                rows.append("|" + part + "|")
        return rows

    fixed_lines = []
    for line in text.split("\n"):
        if line.startswith("|") and len(line) > 160 and "| |" in line:
            # Line is a pure table line with collapsed rows
            fixed_lines.extend(_split_table_rows(line))
        elif not line.startswith("|") and "| |" in line and "|" in line and line.rstrip().endswith("|"):
            # Line has a non-table prefix followed by collapsed table content
            # e.g. "[Gate 1] heading | Q | A | | row1 | | row2 |"
            # e.g. "### heading | Q | A | |---| | row1 |"
            idx = line.index("|")
            prefix = line[:idx].rstrip()
            table = line[idx:]
            if prefix:
                fixed_lines.append(prefix)
            if len(table) > 100 and "| |" in table:
                fixed_lines.extend(_split_table_rows(table))
            else:
                fixed_lines.append(table)
        else:
            fixed_lines.append(line)
    text = "\n".join(fixed_lines)
    # Fix: closing code fence ``` immediately followed by markdown content on the same line
    # e.g. "```**Response Priorities**" → "```\n**Response Priorities**"
    # Only trigger on non-language characters (* # | > [ _) not alphanumeric language names
    text = re.sub(r'^```([*#|>\[_\-])', r'```\n\1', text, flags=re.MULTILINE)
    # Fix: markdown heading (##) embedded in a line without a preceding newline
    # e.g. "...security incidents. ## Role Identity" → "...security incidents.\n## Role Identity"
    # e.g. "Key Frameworks## Next Section" → "Key Frameworks\n## Next Section"
    # Exclude | # and whitespace as preceding char so table cells like "| # Tag |" are not broken.
    text = re.sub(r'([^\n|#\s])(#{1,6} )', r'\1\n\2', text)
    # Fix: numbered list items missing space after period: "2.INCIDENT" → "2. INCIDENT"
    text = re.sub(r'^(\d+)\.([^\s\d])', r'\1. \2', text, flags=re.MULTILINE)
    # Escape ALL dollar signs to prevent JupyterLab MathJax from treating $...$ as LaTeX math.
    # Any $ can start/end a math expression and consume large spans of text.
    text = text.replace('$', '&#36;')
    # Bold marker fixes — order matters!
    # Phase 1: Fix INTERNAL spacing (strip stray spaces inside ** markers)
    # Uses paired **...**  regex so it naturally knows opening from closing **.
    # Must run BEFORE external spacing fixes, so patterns like word** Bold** become
    # word**Bold** first, then the next step can add the space before **.
    # Uses [^\S\n]* (any horizontal whitespace — covers all Unicode spaces, not just ASCII).
    text = re.sub(r'\*\*[^\S\n]*([^*\n]+?)[^\S\n]*\*\*', r'**\1**', text)
    # Phase 2: Fix EXTERNAL spacing (add spaces around ** where missing)
    # Fix missing space before opening **: word**Bold** → word **Bold**
    # Only when BOTH sides are word chars — avoids firing on closing ** like **Word**:
    text = re.sub(r'(\w)\*\*(\w)', r'\1 **\2', text)
    # Fix missing space after closing **: **Word**X → **Word** X
    # Require at least one \w in the bold content to prevent matching across two
    # adjacent bold pairs (e.g. **A** | **B** — the " | " has no \w, so it won't
    # be mistaken for bold content).
    # Use [^*\n] (not [^*]) to prevent matching across line boundaries, which would
    # span from one bold pair's closing ** to the next pair's opening ** on another line.
    text = re.sub(r'\*\*([^*\n]*\w[^*\n]*)\*\*([^\s*])', r'**\1** \2', text)
    return text


# ---------------------------------------------------------------------------
# Integrated markdown+file renderer for final agent report
# ---------------------------------------------------------------------------

def _render_markdown_with_files(text: str) -> tuple:
    """Render markdown that embeds file references inline.

    The agent writes standard markdown image syntax to reference output files:
      ![caption](full_path/to/file.geojson)        → Folium map
      ![caption](file1.geojson,file2.geojson)       → multi-layer Folium map
      ![caption](full_path/to/file.png)             → inline image

    Text segments between file references are rendered as Markdown.
    Returns (found_any, map_rendered):
      found_any   — True if at least one file reference was found and rendered
      map_rendered — True if a GeoJSON/WMS map was actually rendered inline
    """
    import re
    from IPython.display import display, Markdown, HTML

    if not text.strip():
        return False, False

    # Standard markdown image: ![alt text](src)
    pattern = re.compile(r'!\[([^\]]*)\]\(([^)\n]+)\)')

    last_end = 0
    found_any = False
    map_rendered = False

    for m in pattern.finditer(text):
        # Render prose before this file reference
        before = text[last_end:m.start()].strip()
        if before:
            display(Markdown(before))

        alt = m.group(1).strip()
        src = m.group(2).strip()

        # Support comma-separated paths for multi-layer maps
        file_refs = [f.strip() for f in src.split(',')]

        # Resolve paths — support both absolute and relative (to SAGE_OUTPUT_DIR)
        resolved = []
        for ref in file_refs:
            p = Path(ref)
            if not p.is_absolute():
                p = Path(SAGE_OUTPUT_DIR) / ref
            if p.exists():
                resolved.append(p)

        if not resolved:
            # File not found — skip silently (avoids broken image alt text appearing as "Image")
            last_end = m.end()
            continue

        geojsons = [p for p in resolved if p.suffix == '.geojson']
        wms_files = [p for p in resolved if p.name.endswith('.wms.json')]
        pngs = [p for p in resolved if p.suffix == '.png']

        if geojsons or wms_files:
            found_any = True
            map_rendered = True
            _display_combined_map(
                geojsons, wms_files,
                show_header=not alt,
                caption=alt,
            )
        elif pngs:
            found_any = True
            for png in pngs:
                _display_png(png, caption=alt if alt else None)

        last_end = m.end()

    # Render any remaining prose after the last file reference
    if not found_any:
        return False, False
    remaining = text[last_end:].strip()
    if remaining:
        display(Markdown(remaining))

    return True, map_rendered


# ---------------------------------------------------------------------------
# Agent streaming with tool detail display
# ---------------------------------------------------------------------------

async def _run_agent_async(prompt: str) -> tuple[str, dict]:
    """Create and stream the agent, displaying tool calls with details.

    Returns (final_text, tool_counts) where tool_counts is a dict mapping
    tool name → number of times it was invoked in this cell.
    """
    from IPython.display import display, Markdown

    from deepagents import create_deep_agent
    from deepagents.backends.local_shell import LocalShellBackend
    from deepagents_cli.config import create_model
    from deepagents_cli.model_config import ModelConfigError
    from langchain_core.messages import AIMessage, ToolMessage

    try:
        result = create_model(None)
    except ModelConfigError as e:
        print(f"Error: {e}")
        return ""
    model = result.model
    result.apply_to_settings()

    # Discover installed skills
    skills_dir = Path.home() / ".deepagents" / "agent" / "skills"
    skills_paths = sorted([str(d) for d in skills_dir.iterdir() if d.is_dir()]) if skills_dir.exists() else []

    # No checkpointer — cross-cell memory is carried via SAGE_MESSAGES.
    agent = create_deep_agent(
        model,
        skills=skills_paths,
        backend=LocalShellBackend(virtual_mode=False),
        checkpointer=None,
    )

    config = {"metadata": {"assistant_id": "sage"}}

    # --- streaming loop ---
    tool_call_buffers: dict = {}
    displayed_tool_ids: set = set()
    text_buffer: list[str] = []
    tool_counts: dict = {}  # tool_name → invocation count for this cell

    # Dedup state: track msg_id transitions to catch text→text (no tool call) duplicates.
    _cur_text_msg_id: list = [None]
    _had_tool_after_text: list = [True]
    _skip_msg_id: list = [None]

    def _flush_text():
        if text_buffer:
            display(Markdown(_fix_glm_markdown("".join(text_buffer))))
            text_buffer.clear()
        _had_tool_after_text[0] = True
        _skip_msg_id[0] = None

    # Pass full conversation history so the agent has cross-cell memory.
    initial_messages = list(SAGE_MESSAGES) + [{"role": "user", "content": prompt}]
    async for chunk in agent.astream(
        {"messages": initial_messages},
        stream_mode="messages",
        config=config,
    ):
        if not isinstance(chunk, tuple) or len(chunk) < 2:
            continue
        message_obj, metadata = chunk[0], chunk[1]

        if isinstance(message_obj, ToolMessage):
            _flush_text()
            tool_content = getattr(message_obj, "content", "") or ""
            tool_name = getattr(message_obj, "name", "tool")
            if isinstance(tool_content, list):
                tool_content = "\n".join(
                    (c.get("text", "") if isinstance(c, dict) else str(c))
                    for c in tool_content
                )
            _display_tool_result(tool_name, str(tool_content))
            continue

        if not isinstance(message_obj, AIMessage):
            continue
        if metadata and metadata.get("lc_source") == "summarization":
            continue

        # --- Text content ---
        content = getattr(message_obj, "content", "")
        if content and isinstance(content, str):
            msg_id = getattr(message_obj, "id", None)
            if msg_id is not None:
                if msg_id == _skip_msg_id[0]:
                    content = ""  # skip duplicate message
                elif msg_id != _cur_text_msg_id[0]:
                    # New message ID starting to contribute text
                    if not _had_tool_after_text[0]:
                        # text → text with no tool call = duplicate; discard
                        _skip_msg_id[0] = msg_id
                        content = ""
                    else:
                        _cur_text_msg_id[0] = msg_id
                        _had_tool_after_text[0] = False
            if content:
                text_buffer.append(content)

        # --- Tool calls (streaming chunks or complete calls) ---
        tool_call_chunks = list(getattr(message_obj, "tool_call_chunks", None) or [])
        if not tool_call_chunks:
            raw_calls = getattr(message_obj, "tool_calls", None) or []
            tool_call_chunks = [
                {"id": tc.get("id"), "name": tc.get("name"),
                 "args": tc.get("args", {}), "index": i}
                for i, tc in enumerate(raw_calls)
            ]

        for tc_chunk in tool_call_chunks:
            chunk_name = tc_chunk.get("name")
            chunk_id = tc_chunk.get("id")
            chunk_index = tc_chunk.get("index")
            chunk_args = tc_chunk.get("args")

            buf_key = chunk_index if chunk_index is not None else chunk_id
            if buf_key is None:
                buf_key = f"unknown-{len(tool_call_buffers)}"

            buf = tool_call_buffers.setdefault(buf_key, {
                "name": None, "id": None, "args": None, "args_parts": [],
            })
            if chunk_name:
                buf["name"] = chunk_name
            if chunk_id:
                buf["id"] = chunk_id

            if isinstance(chunk_args, dict):
                buf["args"] = chunk_args
                buf["args_parts"] = []
            elif isinstance(chunk_args, str) and chunk_args:
                buf["args_parts"].append(chunk_args)
                buf["args"] = "".join(buf["args_parts"])

            if not buf["name"]:
                continue

            parsed_args = buf["args"]
            if isinstance(parsed_args, str):
                if not parsed_args:
                    continue
                try:
                    parsed_args = json.loads(parsed_args)
                except json.JSONDecodeError:
                    continue
            elif parsed_args is None:
                continue

            if not isinstance(parsed_args, dict):
                parsed_args = {"value": parsed_args}

            display_key = buf["id"] or buf_key
            if display_key in displayed_tool_ids:
                tool_call_buffers.pop(buf_key, None)
                continue
            displayed_tool_ids.add(display_key)
            tool_call_buffers.pop(buf_key, None)

            # Flush any pending narration before showing the tool call
            _flush_text()
            _display_tool_call(buf["name"], parsed_args)
            tool_counts[buf["name"]] = tool_counts.get(buf["name"], 0) + 1

    # Content-based fallback: if the same message was emitted twice with the same
    # msg_id (msg_id dedup can't catch it), the buffer contains identical duplicated
    # text. Detect by finding the first ~30 chars of the accumulated text appearing
    # again starting from the midpoint.
    final = "".join(text_buffer).strip()
    if len(final) > 60:
        half = len(final) // 2
        marker = final[:30]
        repeat_pos = final.find(marker, half - 5)
        if repeat_pos > 0:
            final = final[:repeat_pos].strip()
    return final, tool_counts


# ---------------------------------------------------------------------------
# API key check
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str | None:
    return os.environ.get("NRP_API_KEY")


# ---------------------------------------------------------------------------
# Magic command registration
# ---------------------------------------------------------------------------

try:
    from IPython.core.magic import register_line_cell_magic

    @register_line_cell_magic
    def ask(line, cell=None):
        """Run a Sage agent task non-interactively.

        Usage:
            %ask search for earthquake datasets near California

            %%ask
            Search for wildfire datasets in California from 2020 to 2024

        Note: use %%ask (cell magic) for prompts containing '?'
        Output files are saved to SAGE_OUTPUT_DIR and displayed automatically.
        """
        prompt = cell.strip() if cell else line.strip()
        if not prompt:
            print("Usage: %ask <prompt>  or  %%ask in a cell")
            return

        # Re-check CWD .env at call time (user may have changed directory)
        try:
            from dotenv import load_dotenv as _load
            _load(dotenv_path=Path.cwd() / ".env", override=False)
        except ImportError:
            pass

        if not _resolve_api_key():
            print(
                "Error: NRP_API_KEY not found.\n"
                "Set it in one of these locations:\n"
                "  1. /home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env\n"
                "  2. .env in the current working directory\n"
                "  3. os.environ['NRP_API_KEY'] = 'your_key'"
            )
            return

        # Inject output directory and thinking requirement into prompt
        import sys as _sys
        full_prompt = (
            f"The Python interpreter is at: {_sys.executable} — always use this exact path "
            f"when running Python scripts (do not use 'python' or 'python3').\n"
            f"Use {SAGE_OUTPUT_DIR} as your working directory for ALL files — "
            f"including intermediate files, scripts, and final outputs (GeoJSON, CSV, PNG). "
            f"Do not write any files to /tmp directly.\n"
            f"FILE ACCESS RULE — you may only read or search files in two locations: "
            f"(1) {SAGE_OUTPUT_DIR} — your working directory for this notebook; "
            f"(2) /opt/sage_scripts — pre-deployed helper scripts. "
            f"Never read, list, search, or browse any other directory on the filesystem "
            f"(e.g. /home, /data, /tmp, /root, or any path outside these two). "
            f"All input data must come from external APIs or services, not from the local filesystem.\n"
            f"Never use read_file on binary files such as PNG, GeoTIFF, or other image files — "
            f"they will crash.\n\n"
            f"As you work, narrate your thought process naturally. Before each tool use, "
            f"briefly explain your intent. After each result, explain what you learned. "
            f"Keep it concise and conversational.\n\n"
            f"When writing your final report, organize it as well-structured markdown. "
            f"IMPORTANT — markdown bold syntax: always write **Label**: value with no space "
            f"inside the ** markers and always a space after the closing ** before any text or punctuation. "
            f"Correct: **Total schools**: 183. Wrong: ** Total schools**: 183 or **Total schools**:183.\n"
            f"IMPORTANT — markdown table syntax: always place EACH TABLE ROW on its own separate line. "
            f"Never write multiple table rows on a single line.\n"
            f"IMPORTANT — when generating charts or visualizations with matplotlib, always use "
            f"figsize=(14, 8) or larger, dpi=150, and call plt.tight_layout() before saving. "
            f"This ensures images are large enough to read clearly in the notebook.\n"
            f"IMPORTANT — when a skill provides a complete script example marked "
            f"'copy this script verbatim', you MUST copy that script exactly and only fill in "
            f"the variable values. Do not rewrite or replace any part of the script logic.\n"
            f"Embed output files inline where most relevant using standard markdown image syntax:\n"
            f"  ![Image caption](full_path_to_file.png)                — for a PNG chart or image\n"
            f"  ![Map caption](full_path_to_file.geojson)              — for a single-layer map\n"
            f"  ![Map caption](file1.geojson,file2.geojson)            — MULTIPLE layers on ONE map (comma-separated)\n"
            f"  ![Map caption](file.geojson,layer.wms.json)            — GeoJSON + WMS together on ONE map\n"
            f"WMS RULE — to display a WMS layer, save a file whose name ends in exactly '.wms.json' "
            f"(e.g. 'burn_probability.wms.json', NOT 'burn_probability_wms.json'). "
            f"Required fields: url (string), layers (string, comma-separated if multiple), "
            f"name (string), bbox ([min_lat, min_lon, max_lat, max_lon]). "
            f"Optional: opacity (0–1, default 0.7). No other fields are needed.\n"
            f"MAP RULE — one map per report, all layers combined: your entire report must contain "
            f"exactly ONE map tag. Put every GeoJSON and WMS layer produced by this cell into that "
            f"single tag as comma-separated paths. Never create separate maps for different layers — "
            f"always combine them. "
            f"Also include files from PREVIOUS cells ONLY when the current task explicitly uses or "
            f"references that data — for example, if the user asked to find GPS stations near a "
            f"specific earthquake, include the earthquake GeoJSON from the previous cell because the "
            f"task directly references it. Do NOT include files from previous cells just because they "
            f"exist in the output folder — only include them when the current question explicitly "
            f"connects to them.\n"
            + (_color_registry_prompt())
            + f"COLOR RULE — to color a GeoJSON map layer by category, save a colormap "
            f"sidecar file with the same base name as the GeoJSON but ending in "
            f"'.colormap.json'. Example: if your data is 'earthquakes.geojson', also save "
            f"'earthquakes.colormap.json':\n"
            f'  {{"field": "magnitude_class", "title": "Earthquake Magnitude", '
            f'"palette": {{"M2-3": "#fee8c8", "M3-4": "#fdd49e", "M4-5": "#fc8d59", '
            f'"M5-6": "#e34a33", "M6+": "#b30000"}}}}\n'
            f"Sage will automatically color each feature and add a legend to the map. "
            f"LABEL RULE — when category labels represent numeric thresholds or ranges "
            f"(flood depth, magnitude, distance, risk levels, etc.), always embed the "
            f"numeric definition in the label in parentheses so the legend is self-explanatory. "
            f"Examples: 'Minor (1-3 ft)' not 'Minor', 'Moderate (3-6 ft)' not 'Moderate', "
            f"'M4-5 (mag 4.0-5.0)' not 'M4-5', 'Near (< 50 mi)' not 'Near'. "
            f"Pure descriptive labels with no numeric meaning (e.g. 'critically dry', 'unknown') "
            f"do not need a parenthetical. "
            f"CRITICAL: the label strings in your palette keys MUST exactly match the values "
            f"your classification function returns — if the palette key is 'Minor (1-3 ft)' "
            f"then the function must also return 'Minor (1-3 ft)', not 'Minor'.\n"
            f"NEVER describe color meanings in your report text in any form — "
            f"no legend, no color key, no bullet list, no 'blue = X' sentences, "
            f"no 'the map displays' color explanations. The map legend is the only "
            f"place color meanings appear. Only report data findings: counts, "
            f"statistics, and insights.\n"
            f"Use distinct color families for different layers to avoid conflicts:\n"
            f"  Reds/oranges (#b30000→#fee8c8) — severity, risk, danger, magnitude\n"
            f"  Blues (#08306b→#deebf7) — water, flood depth, coverage\n"
            f"  Greens (#006837→#d9f0a3) — safe, low-risk, healthy, vegetation\n"
            f"  Purples (#3f007d→#dadaeb) — density, count, intensity\n\n"
            f"MAP RULE — never plot GeoJSON data as a static matplotlib/PNG map. If you have a GeoJSON "
            f"file, reference it with the map tag above — Sage will render it as an interactive Folium "
            f"map automatically. Only use matplotlib/PNG for charts (bar, line, scatter, histogram, etc.).\n"
            f"Correct (one map, two layers):  "
            f"![Earthquake and GNSS Stations]({SAGE_OUTPUT_DIR}/earthquakes.geojson,{SAGE_OUTPUT_DIR}/gnss_stations.geojson)\n"
            f"Wrong (two separate maps):  "
            f"![Earthquake]({SAGE_OUTPUT_DIR}/earthquakes.geojson) ... ![GNSS Stations]({SAGE_OUTPUT_DIR}/gnss_stations.geojson)\n"
            f"Always use full absolute paths from {SAGE_OUTPUT_DIR}. "
            f"Do not list files separately at the end — embed them inline in the report.\n\n"
            f"{prompt}"
        )

        # True rerun: delete this cell's previous output files, then also delete
        # any orphaned files (not registered to any cell) left by interrupted runs.
        cell_id = _get_cell_id()
        if cell_id:
            _reg = _load_cell_registry()
            for _f in _reg.get(cell_id, []):
                try:
                    Path(_f).unlink(missing_ok=True)
                except Exception:
                    pass
            if cell_id in _reg:
                del _reg[cell_id]
                _save_cell_registry(_reg)

            # Delete orphaned files: in output dir but not in any registry entry.
            # These are generated by cells that were stopped before finishing.
            _all_registered = {f for files in _reg.values() for f in files}
            for _orphan in Path(SAGE_OUTPUT_DIR).rglob("*"):
                if (
                    _orphan.is_file()
                    and _orphan.name not in _SAGE_INTERNAL_FILES
                    and str(_orphan) not in _all_registered
                ):
                    try:
                        _orphan.unlink(missing_ok=True)
                    except Exception:
                        pass

        # Snapshot output folder before run
        before = _snapshot(SAGE_OUTPUT_DIR)

        # Run agent with streaming tool display; get back final report + tool counts.
        # Use run_until_complete on the existing loop (patched by nest_asyncio)
        # instead of asyncio.run(), which conflicts with Python 3.13's task cleanup.
        import time as _time
        from IPython.display import display, HTML
        _t_start = _time.time()
        _loop = asyncio.get_event_loop()
        _orig_exc_handler = _loop.get_exception_handler()
        def _suppress_context_errors(loop, context):
            msg = str(context.get("message", "")) + str(context.get("exception", ""))
            if "cannot enter context" in msg:
                return
            (_orig_exc_handler or loop.default_exception_handler)(loop, context)
        _loop.set_exception_handler(_suppress_context_errors)
        try:
            final_text, tool_counts = _loop.run_until_complete(
                _run_agent_async(full_prompt)
            )
        except Exception as _err:
            _loop.set_exception_handler(_orig_exc_handler)
            _err_str = str(_err)
            _err_type = type(_err).__name__
            # Classify common API errors for a user-friendly message
            if "429" in _err_str or "RateLimitError" in _err_type or "limit" in _err_str.lower():
                import re as _re
                _reset = _re.search(r"reset at ([^'\"}\s]+\s+[^'\"}\s]+)", _err_str)
                _reset_msg = f"<br>Limit resets at: <b>{_reset.group(1)}</b>" if _reset else ""
                _msg = f"⏳ <b>Rate limit reached.</b>{_reset_msg}<br>Please wait and try again."
            elif "401" in _err_str or "AuthenticationError" in _err_type or "api key" in _err_str.lower():
                _msg = "🔑 <b>Authentication failed.</b> Check that your API key is set correctly."
            elif "ConnectionError" in _err_type or "connect" in _err_str.lower():
                _msg = "🔌 <b>Connection error.</b> Check your network and try again."
            else:
                _msg = f"❌ <b>{_err_type}:</b> {_err_str[:300]}"
            display(HTML(
                f'<div style="background:#fff3cd; border-left:4px solid #f0ad4e; '
                f'padding:10px 14px; margin:6px 0; font-size:0.95em;">{_msg}</div>'
            ))
            return
        _loop.set_exception_handler(_orig_exc_handler)
        _elapsed = round(_time.time() - _t_start, 1)

        # Append run entry to .sage_run.jsonl (hidden file, cleared by %reset)
        _log_path = Path(SAGE_OUTPUT_DIR) / ".sage_run.jsonl"
        _log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "prompt": prompt[:200] + ("…" if len(prompt) > 200 else ""),
            "elapsed_sec": _elapsed,
            "tool_calls": tool_counts,
            "total_tool_calls": sum(tool_counts.values()),
        }
        with open(_log_path, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(_log_entry) + "\n")

        # Update conversation history for cross-cell memory
        SAGE_MESSAGES.append({"role": "user", "content": prompt})
        SAGE_MESSAGES.append({"role": "assistant", "content": final_text})

        # Auto-trust the notebook so HTML/JS outputs (maps, tool panels) are
        # not flagged as untrusted when the notebook is reopened.
        _nb_session = os.environ.get("JPY_SESSION_NAME", "")
        if _nb_session:
            try:
                import subprocess as _sp
                _sp.run(
                    ["jupyter", "trust", _nb_session],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass  # Non-fatal: trust can be applied manually if needed

        # Fix GLM markdown quirks before rendering
        final_text = _fix_glm_markdown(final_text)

        # Compute new/modified files and update the cell registry so reruns
        # only delete this cell's outputs, not other cells' files.
        after = _snapshot(SAGE_OUTPUT_DIR)
        new = _new_files(before, after)

        # Merge any new colormap sidecars into the persistent color registry
        _update_color_registry(new)

        if cell_id:
            _trackable = [f for f in new if Path(f).name not in _SAGE_INTERNAL_FILES]
            if _trackable:
                _reg = _load_cell_registry()
                _reg[cell_id] = _trackable
                _save_cell_registry(_reg)

        # Render the final report — file references become maps/images inline
        found_any, map_rendered = _render_markdown_with_files(final_text)

        if not found_any:
            # Fallback: plain markdown + auto-display new files separately
            if final_text.strip():
                from IPython.display import display, Markdown
                display(Markdown(final_text))
            if new:
                _display_new_outputs(new)
        elif new:
            # Inline rendering found file refs. Auto-display new GeoJSON/WMS only
            # if no map was actually rendered (e.g. agent referenced a PNG but its
            # GeoJSON path was wrong or omitted entirely).
            if not map_rendered:
                new_maps = [
                    f for f in new
                    if f.endswith('.geojson') or f.endswith('.wms.json')
                ]
                if new_maps:
                    all_geojsons = sorted(Path(SAGE_OUTPUT_DIR).rglob("*.geojson"))
                    all_wms = sorted(Path(SAGE_OUTPUT_DIR).rglob("*.wms.json"))
                    if all_geojsons or all_wms:
                        _display_combined_map(all_geojsons, all_wms)

    del ask  # keep IPython namespace clean

    from IPython.core.magic import register_line_magic

    @register_line_magic
    def tool_output_on(line):
        """Show tool outputs after each tool call."""
        global SAGE_SHOW_TOOL_OUTPUT
        SAGE_SHOW_TOOL_OUTPUT = True
        from IPython.display import display, HTML
        display(HTML('<div style="color:#4caf50; font-size:0.9em;">Tool output display: <b>on</b></div>'))
    del tool_output_on

    @register_line_magic
    def tool_output_off(line):
        """Hide tool outputs (default)."""
        global SAGE_SHOW_TOOL_OUTPUT
        SAGE_SHOW_TOOL_OUTPUT = False
        from IPython.display import display, HTML
        display(HTML('<div style="color:#888; font-size:0.9em;">Tool output display: <b>off</b></div>'))
    del tool_output_off

    @register_line_magic
    def reset(line):
        """Reset Sage: clear output files and conversation history.

        Usage:
            %reset
        """
        import shutil
        from IPython.display import display, Markdown

        # Clear output files including .sage_run.jsonl (reset = start fresh)
        output_path = Path(SAGE_OUTPUT_DIR)
        files_deleted = 0
        if output_path.exists():
            for f in output_path.iterdir():
                if f.is_file():
                    f.unlink()
                    files_deleted += 1
                elif f.is_dir():
                    shutil.rmtree(f)
                    files_deleted += 1

        # Clear conversation history and cell registry
        global SAGE_MESSAGES
        SAGE_MESSAGES.clear()

        from IPython.display import display, Markdown, HTML
        display(Markdown("**Sage reset.** Output folder cleared, history cleared."))
        display(HTML("""
<script>
(function() {
    setTimeout(function() {
        var content = document.querySelector('.jp-scrollbar-tiny > .lm-MenuBar-content');
        if (!content) return;
        var editItem = null;
        Array.from(content.children).forEach(function(c) {
            if (c.textContent.trim() === 'Edit') editItem = c;
        });
        if (!editItem) return;
        var rect = editItem.getBoundingClientRect();
        editItem.dispatchEvent(new MouseEvent('mousedown', {
            bubbles: true, cancelable: true,
            clientX: rect.left + rect.width/2, clientY: rect.top + rect.height/2
        }));
        setTimeout(function() {
            var labels = document.querySelectorAll('.lm-Menu-itemLabel');
            labels.forEach(function(lbl) {
                if (lbl.textContent.trim() === 'Clear Outputs of All Cells') {
                    var menuItem = lbl.closest('.lm-Menu-item');
                    var menuNode = menuItem.closest('.lm-Menu');
                    var r = menuItem.getBoundingClientRect();
                    var opts = {
                        bubbles: true, cancelable: true,
                        clientX: r.left + r.width/2, clientY: r.top + r.height/2
                    };
                    menuNode.dispatchEvent(new MouseEvent('mousemove', opts));
                    menuNode.dispatchEvent(new MouseEvent('mouseup', opts));
                }
            });
        }, 80);
    }, 1500);
})();
</script>
"""))

    del reset  # keep IPython namespace clean

except Exception as exc:
    warnings.warn(
        f"Sage magic commands could not be registered: {exc}", stacklevel=1
    )
