"""
sage_bbox_map — generic bounding-box selection map for Sage notebooks.

This module is GUI-only. It knows nothing about USGS LiDAR, fires, weather, or
any specific data source. The caller (a data skill or agent-written script)
supplies the kernel variable name to receive the drawn bbox, plus optional
overlay GeoJSON for visual context.

The user draws a single rectangle on an ipyleaflet map; the rectangle's bounds
are written to the kernel namespace as a 4-tuple `(minx, miny, maxx, maxy)` in
EPSG:4326, AND registered in the cross-cell kernel-variables registry.

Example call from an agent-generated script:

    import sys
    sys.path.insert(0, '/home/jovyan/.deepagents/agent/skills/sage-bbox-map')
    from sage_bbox_map import show_bbox_map

    show_bbox_map(
        bbox_var={"name": "USER_BBOX",
                  "description": "Bounding box drawn by user on the SoCal fires map (EPSG:4326)"},
        center=(34.05, -118.24),
        zoom=8,
        header="Draw a rectangle to select an area",
        overlay_geojson="/path/to/fires.geojson",   # optional
        overlay_color="#e34a33",
        set_by="sdge-goes-fire via sage-bbox-map",
    )
"""

import inspect
import json
import time
import uuid
from pathlib import Path

import ipyleaflet as ipyl
import ipywidgets as widgets
from IPython import get_ipython
from IPython.display import HTML, display
from shapely.geometry import shape


def _get_cell_id():
    try:
        return get_ipython().parent_header.get("metadata", {}).get("cellId")
    except Exception:
        return None


def _notify_var_change(user_ns, var_name, value):
    """Fire all subscribers of var_name with the new value.

    Subscribers are registered by `show_dropdown` (or other future helpers)
    when a reactive `observes=...` is supplied. The dict lives in user_ns so
    it survives across script executions within one kernel.
    """
    subs = user_ns.get("_sage_var_subscribers", {})
    for entry in list(subs.get(var_name, [])):
        try:
            entry["callback"](value)
        except Exception as e:
            print(f"[sage-bbox-map] subscriber error for {var_name}: {e}")


def _slim_geojson(geo, ndigits=5, keep_props=("_color",)):
    """Recursively round coordinates and strip non-essential properties.

    Used both for the live ipyleaflet overlay (so JupyterLab's "Save Widget
    State Automatically" doesn't bake the full-precision GeoJSON into the
    notebook) and for the static Folium fallback. Empirically dominant for
    overlays with thousands of polygons (USGS 3DEP coverage, vegetation,
    etc.) where each feature carries large coordinate arrays plus name,
    URL, and count properties not needed for rendering.

    `_color` is the only property the live widget's style_callback reads.
    Callers that need other properties retained can override `keep_props`.
    """
    if isinstance(geo, dict):
        out = {}
        for k, v in geo.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: pv for pk, pv in v.items() if pk in keep_props}
            else:
                out[k] = _slim_geojson(v, ndigits, keep_props)
        return out
    if isinstance(geo, (list, tuple)):
        # GeoDataFrame.__geo_interface__ returns coordinates as nested
        # tuples (shapely convention), so accept both.
        if all(isinstance(x, (int, float)) for x in geo):
            return [round(x, ndigits) if isinstance(x, float) else x for x in geo]
        return [_slim_geojson(x, ndigits, keep_props) for x in geo]
    return geo


def _register_kernel_var(cell_id, user_ns, var_name, type_str, description, set_by):
    """Persist a single registered variable to .sage_kernel_vars.json under cell_id."""
    output_dir = user_ns.get("SAGE_OUTPUT_DIR")
    if not output_dir or not cell_id:
        return
    p = Path(output_dir) / ".sage_kernel_vars.json"
    try:
        registry = json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        registry = {}
    cell_entry = registry.setdefault(cell_id, {})
    cell_entry[var_name] = {
        "description": description,
        "type": type_str,
        "set_by": set_by,
    }
    try:
        p.write_text(json.dumps(registry, indent=2))
    except Exception:
        pass


def show_bbox_map(
    bbox_var,
    center=(40, -100),
    zoom=4,
    height="400px",
    header="Draw a bounding box on the map",
    overlay_geojson=None,
    overlay_color="#3388ff",
    overlay_name="Overlay",
    set_by="sage-bbox-map",
):
    """Render an ipyleaflet map with a draw-rectangle tool that writes bbox to kernel.

    Args:
        bbox_var: dict {"name": str, "description": str}.
            The drawn bbox (minx, miny, maxx, maxy in EPSG:4326) is written to
            kernel namespace under the given name and registered.
        center: (lat, lon) for initial map center.
        zoom: initial zoom level.
        height: CSS height for the map.
        header: H3 title shown above the map.
        overlay_geojson: optional path to a GeoJSON file rendered as a context layer.
        overlay_color: hex color for the overlay layer.
        overlay_name: display name for the overlay layer in the layer control.
        set_by: identifier for registry's set_by metadata.

    Returns:
        None. Side effect: caller's kernel namespace receives the bbox tuple
        (initialised to None until the user draws), and .sage_kernel_vars.json
        is updated under the current cell_id.
    """
    if not isinstance(bbox_var, dict) or "name" not in bbox_var:
        raise ValueError("show_bbox_map: bbox_var must be a dict with at least a 'name' key")

    var_name = bbox_var["name"]
    var_desc = bbox_var.get("description", "Bounding box drawn by user (EPSG:4326)")

    caller_ns = inspect.currentframe().f_back.f_globals
    cell_id = _get_cell_id()

    # Initialise to None so downstream cells see the variable exists but is unset
    caller_ns[var_name] = None
    _register_kernel_var(cell_id, caller_ns, var_name, "tuple|None", var_desc, set_by)

    m = ipyl.Map(center=center, zoom=zoom, scroll_wheel_zoom=True)
    m.layout.height = height

    # `data` is captured here so we can reuse it below for the static
    # Folium fallback (rendered in nbviewer / GitHub where ipywidgets is
    # absent). Stays None when no overlay was supplied.
    data = None

    # Optional context overlay. Accepts:
    #   - dict (GeoJSON object, the canonical in-memory form)
    #   - GeoDataFrame (anything with .to_json())
    #   - str / Path (file path to a GeoJSON file)
    # Prefer in-memory forms — writing the overlay to SAGE_OUTPUT_DIR triggers
    # Sage's auto-fallback Folium display, producing a duplicate map next to
    # the live ipyleaflet widget.
    if overlay_geojson is not None:   # NOT `if overlay_geojson:` — GeoDataFrames raise on truthiness
        try:
            if isinstance(overlay_geojson, dict):
                data = overlay_geojson
            elif hasattr(overlay_geojson, "__geo_interface__"):
                # GeoDataFrames: use __geo_interface__ instead of to_json(), which
                # serializes more permissively. to_json() can raise on array-valued
                # properties (e.g. tags, resource_formats stored as numpy arrays).
                data = overlay_geojson.__geo_interface__
                # Ensure properties are JSON-serializable: coerce numpy arrays / pd
                # types to Python primitives so ipyleaflet can transport them.
                for feat in data.get("features", []):
                    props = feat.get("properties") or {}
                    for k, v in list(props.items()):
                        if v is None or isinstance(v, (str, int, float, bool)):
                            continue
                        if hasattr(v, "tolist"):
                            try:
                                props[k] = v.tolist()
                            except Exception:
                                props[k] = str(v)
                        else:
                            try:
                                json.dumps(v)
                            except Exception:
                                props[k] = str(v)
            else:
                data = json.loads(Path(overlay_geojson).read_text())

            # Slim the GeoJSON before handing it to the live widget — this
            # is the version that JupyterLab serializes into the notebook
            # via "Save Widget State Automatically", and it dominates
            # notebook size for large overlays.
            data = _slim_geojson(data, ndigits=5, keep_props=("_color",))

            def _style_cb(feature):
                c = feature.get("properties", {}).get("_color", overlay_color)
                return {
                    "color": c, "weight": 1, "opacity": 0.8,
                    "fillColor": c, "fillOpacity": 0.3,
                }

            geo_layer = ipyl.GeoJSON(
                data=data,
                style_callback=_style_cb,
                hover_style={"weight": 3, "fillOpacity": 0.5},
                name=overlay_name,
            )
            m.add_layer(geo_layer)
        except Exception as e:
            print(f"[sage-bbox-map] warning: failed to load overlay {overlay_geojson}: {e}")

    # Plain ipyleaflet.Map does not auto-attach a DrawControl (only leafmap.Map
    # does). Add one explicitly with rectangle-only and edit/remove disabled.
    draw_control = ipyl.DrawControl(
        rectangle={"shapeOptions": {"color": "#e74c3c", "fillOpacity": 0.08}},
        marker={}, polyline={}, polygon={}, circlemarker={}, circle={},
        edit=False, remove=False,
    )
    m.add_control(draw_control)

    status_html = widgets.HTML(
        f"<i>Draw a rectangle to set <code>{var_name}</code>.</i>"
    )
    clear_btn = widgets.Button(
        description="Clear selection",
        icon="times",
        layout={"width": "150px", "display": "none"},
    )
    bbox_layer_ref = [None]

    def _reset():
        if bbox_layer_ref[0] is not None:
            try:
                m.remove_layer(bbox_layer_ref[0])
            except Exception:
                pass
            bbox_layer_ref[0] = None
        try:
            draw_control.clear()
        except Exception:
            pass
        caller_ns[var_name] = None
        _register_kernel_var(
            cell_id, caller_ns, var_name, "tuple|None", var_desc, set_by
        )
        _notify_var_change(caller_ns, var_name, None)
        status_html.value = (
            f"<i>Draw a rectangle to set <code>{var_name}</code>.</i>"
        )
        clear_btn.layout.display = "none"

    clear_btn.on_click(lambda _b: _reset())

    def _handle_draw(target, action, geo_json):
        if action != "created":
            return
        # Single-bbox enforcement: drop any prior bbox layer
        if bbox_layer_ref[0] is not None:
            try:
                m.remove_layer(bbox_layer_ref[0])
            except Exception:
                pass
            bbox_layer_ref[0] = None
        try:
            target.clear()
        except Exception:
            pass

        geom = shape(geo_json["geometry"])
        bbox = geom.bounds  # (minx, miny, maxx, maxy) in EPSG:4326
        caller_ns[var_name] = bbox
        _register_kernel_var(
            cell_id, caller_ns, var_name, "tuple", var_desc, set_by
        )
        _notify_var_change(caller_ns, var_name, bbox)

        new_layer = ipyl.GeoJSON(
            data={"type": "FeatureCollection", "features": [geo_json]},
            style={"color": "#e74c3c", "weight": 2,
                   "fillColor": "#e74c3c", "fillOpacity": 0.08},
            name="Bounding box",
        )
        m.add_layer(new_layer)
        bbox_layer_ref[0] = new_layer

        status_html.value = (
            f"<b>{var_name}</b> = ({bbox[0]:.4f}, {bbox[1]:.4f}, "
            f"{bbox[2]:.4f}, {bbox[3]:.4f})"
        )
        clear_btn.layout.display = ""

    draw_control.on_draw(_handle_draw)
    if overlay_geojson is not None:   # same truthiness trap as line 142 — GeoDataFrames raise
        m.add_control(ipyl.LayersControl(position="topright"))

    # Reopen notice — same pattern as sage-dropdown
    notice_id = f"sage-bbox-notice-{uuid.uuid4().hex[:8]}"
    cell_run_ms = int(time.time() * 1000)
    reopen_notice = HTML(f'''
<div id="{notice_id}" data-runtime="{cell_run_ms}" style="padding:8px 12px;
     background:#fff8e1;border-left:3px solid #f4b400;font-size:12px;color:#555;
     margin:6px 0;border-radius:2px; display:none;">
    <b>⟳ Interactive widget</b> — re-run this cell to restore map interactivity.
    Widget callbacks live in the kernel and are lost when the notebook is closed.
</div>
<script>
(function() {{
    var n = document.getElementById("{notice_id}");
    if (!n) return;
    setTimeout(function() {{
        var rt = parseInt(n.getAttribute("data-runtime"), 10);
        if (Date.now() - rt > 180000) n.style.display = "";
    }}, 100);
    // Hide "Error displaying widget: model not found" placeholders that
    // JupyterLab renders after reopen when the widget state isn't saved.
    // Run multiple passes — JupyterLab renders these asynchronously.
    var hideBrokenWidgets = function() {{
        var cell = n.closest(".jp-Cell") || n.closest(".jp-OutputArea") || document;
        // Conservative cleanup — only hide outputs with the literal error text.
        cell.querySelectorAll(".jp-OutputArea-output").forEach(function(out) {{
            var text = (out.textContent || "").trim();
            if (/Error displaying widget|model not found/i.test(text) && text.length < 200) {{
                out.style.display = "none";
            }}
        }});
    }};
    [200, 600, 1500, 3000, 6000].forEach(function(ms) {{ setTimeout(hideBrokenWidgets, ms); }});
}})();
</script>
''')

    # Build a static Folium fallback for nbviewer / GitHub. The dual mime
    # bundle below sends BOTH the live ipyleaflet widget view AND a static
    # text/html representation; renderers pick whichever they support.
    # To roll back: replace this whole block (down to the final display()
    # calls) with the single original line:
    #   display(reopen_notice, widgets.HTML(f"<h3>{header}</h3>"), m,
    #           status_html, clear_btn, exclude=["text/plain"])
    static_html = ""
    try:
        import folium as _folium

        # `data` was already slimmed above before being given to the live
        # widget, so we reuse it directly here without a second pass.
        _fmap = _folium.Map(location=list(center), zoom_start=zoom, height=height)
        if data is not None:
            _folium.GeoJson(
                data,
                name=overlay_name,
                style_function=lambda f: {
                    "color": (f.get("properties") or {}).get("_color", overlay_color),
                    "weight": 1, "opacity": 0.8,
                    "fillColor": (f.get("properties") or {}).get("_color", overlay_color),
                    "fillOpacity": 0.3,
                },
            ).add_to(_fmap)
        # Wrap in a fixed-height container so the static fallback matches the
        # requested widget height regardless of folium's iframe defaults.
        static_html = (
            f'<div style="height:{height}; max-height:{height}; overflow:hidden;">'
            f"{_fmap._repr_html_()}"
            f"</div>"
        )
    except Exception:
        static_html = ""

    display(reopen_notice, widgets.HTML(f"<h3>{header}</h3>"), exclude=["text/plain"])
    if static_html and getattr(m, "_model_id", None):
        display(
            {
                "application/vnd.jupyter.widget-view+json": {
                    "version_major": 2,
                    "version_minor": 0,
                    "model_id": m._model_id,
                },
                "text/html": static_html,
            },
            raw=True,
        )
    else:
        display(m, exclude=["text/plain"])
    display(status_html, clear_btn, exclude=["text/plain"])
