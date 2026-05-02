"""
sage_dropdown — generic dropdown picker for Sage notebooks.

This module is GUI-only. It knows nothing about exoplanets, river reaches, or any
specific data source. The caller (a data skill or an agent-written script) supplies:
  * the items list (list of dicts)
  * a label template for each row's display string
  * a `kernel_vars` mapping that names which kernel variables to write the user's
    selection into, plus a human-readable description for each

Selections are written to the kernel namespace AND registered in the cross-cell
kernel-variables registry (`.sage_kernel_vars.json` in SAGE_OUTPUT_DIR), so future
cells can discover them via Sage's system prompt.

Example call from an agent-generated script:

    import sys
    sys.path.insert(0, '/home/jovyan/.deepagents/agent/skills/sage-dropdown')
    from sage_dropdown import show_dropdown

    show_dropdown(
        items=planets,                                 # list of dicts (from data skill)
        label_template="{pl_name}  (P={pl_orbper:.3f}d, V={sy_vmag:.1f})",
        header="Step 1: Select a Transiting Exoplanet",
        description="Planet:",
        sort_by="pl_name",
        kernel_vars={
            "TARGET_PLANET":       {"field": "pl_name",
                                    "description": "Name of the user-selected exoplanet"},
            "TARGET_STAR":         {"field": "hostname",
                                    "description": "Host star name of the selected planet"},
            "ORBITAL_PERIOD_DAYS": {"field": "pl_orbper",
                                    "description": "Orbital period in days of the selected planet"},
        },
        info_template=(
            "Planet:          {pl_name}\\n"
            "Host Star:       {hostname}  (V={sy_vmag:.1f})\\n"
            "Orbital Period:  {pl_orbper:.4f} days"
        ),
        set_by="exoplanet-transits via sage-dropdown",
    )
"""

import inspect
import json
import time
import uuid
from pathlib import Path

import ipywidgets as widgets
from IPython import get_ipython
from IPython.display import HTML, display


def _format_template(tmpl: str, item: dict) -> str:
    """Apply a Python format-string template to an item dict. Falls back to literal on error."""
    try:
        return tmpl.format(**item)
    except (KeyError, ValueError, AttributeError, TypeError, IndexError):
        return tmpl


def _get_cell_id():
    try:
        return get_ipython().parent_header.get("metadata", {}).get("cellId")
    except Exception:
        return None


def _subscribe_var_change(user_ns, var_name, callback, cell_id):
    """Register `callback` to fire when `var_name`'s value changes via the
    notify mechanism in sage-bbox-map (or any future helper).
    """
    subs = user_ns.setdefault("_sage_var_subscribers", {})
    subs.setdefault(var_name, []).append({
        "cell_id": cell_id,
        "callback": callback,
    })


def _register_kernel_vars(cell_id, user_ns, type_specs, set_by):
    """Persist registered variables to .sage_kernel_vars.json under the current cell_id."""
    output_dir = user_ns.get("SAGE_OUTPUT_DIR")
    if not output_dir or not cell_id:
        return
    p = Path(output_dir) / ".sage_kernel_vars.json"
    try:
        registry = json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        registry = {}
    cell_entry = registry.setdefault(cell_id, {})
    for var_name, spec in type_specs.items():
        cell_entry[var_name] = {
            "description": spec.get("description", f"Set by {set_by}"),
            "type": spec.get("type", "?"),
            "set_by": set_by,
        }
    try:
        p.write_text(json.dumps(registry, indent=2))
    except Exception:
        pass


def show_dropdown(
    items=None,
    label_template="{}",
    kernel_vars=None,
    header="Select an item",
    description="Item:",
    info_template=None,
    sort_by=None,
    set_by="sage-dropdown",
    items_fn=None,
    observes=None,
    placeholder=None,
    no_items_message=None,
):
    """Render an ipywidgets dropdown bound to caller-specified kernel variables.

    Two modes:

    **Static mode** — pass `items` (a list of dicts).

    **Reactive mode** — pass `items_fn` (callable returning a list of dicts)
    and optionally `observes` (the name of a kernel variable to watch). The
    dropdown evaluates `items_fn()` initially; if empty, it hides itself and
    shows `placeholder` text. When the watched variable changes (e.g. the
    user draws a rectangle in `sage-bbox-map`), the dropdown re-evaluates
    `items_fn()` and refreshes its options.

    Args:
        items: static list of dicts. Use this OR `items_fn`, not both.
        label_template: format string applied to each item to build its dropdown row.
        kernel_vars: dict of {VAR_NAME: {"field": <field_or_"@self">, "description": str}}.
            Each VAR_NAME is written to the kernel namespace and registered.
        header: H3 title shown above the dropdown.
        description: dropdown's left-side label.
        info_template: optional multi-line template for the info pane.
        sort_by: optional field name to sort items by (alphabetical) before display.
        set_by: identifier for the registry's set_by metadata.
        items_fn: callable returning a list of dicts. Called initially, and
            again every time `observes` changes. Enables reactive composition
            with sage-bbox-map and other producers.
        observes: name of a kernel variable to subscribe to. When that variable
            is set or cleared (via the producer's notify mechanism), this
            dropdown re-evaluates `items_fn`.
        placeholder: text shown when no items and the observed variable
            (`observes`) is unset / None — e.g. "Draw a rectangle on the
            map above to populate this dropdown."
        no_items_message: text shown when the observed variable IS set but
            `items_fn()` returned an empty list — e.g. "No datasets match
            the selected area. Try a different one." Falls back to
            `placeholder` if unset.

    Returns:
        None. Side effect: kernel variables receive selection values, the
        registry is updated, and a subscription is registered if `observes`
        is provided.
    """
    if not isinstance(kernel_vars, dict) or not kernel_vars:
        raise ValueError("show_dropdown: kernel_vars must be a non-empty dict")
    if items is None and items_fn is None:
        raise ValueError("show_dropdown: must provide either items or items_fn")
    if items is not None and items_fn is not None:
        raise ValueError("show_dropdown: pass only one of items or items_fn")

    caller_ns = inspect.currentframe().f_back.f_globals
    cell_id = _get_cell_id()

    def _resolve_items():
        if items_fn is not None:
            try:
                got = items_fn() or []
            except Exception as e:
                print(f"[sage-dropdown] items_fn error: {e}")
                got = []
        else:
            got = list(items) if items else []
        if sort_by:
            got = sorted(got, key=lambda p: p.get(sort_by, ""))
        return got

    initial_items = _resolve_items()
    initial_options = [(_format_template(label_template, p), p) for p in initial_items]

    dropdown = widgets.Dropdown(
        options=initial_options,
        description=description,
        style={"description_width": "auto"},
        layout=widgets.Layout(
            width="auto", min_width="500px",
            display=("" if initial_options else "none"),
        ),
    )
    info_out = widgets.Output() if (info_template or placeholder or no_items_message) else None

    def _write_kernel_vars(value_source):
        """Write kernel variables based on `value_source` (an item dict or None)."""
        type_specs = {}
        for var_name, spec in kernel_vars.items():
            field = spec.get("field", "@self")
            if value_source is None:
                value = None
            else:
                value = value_source if field == "@self" else value_source.get(field)
            caller_ns[var_name] = value
            type_specs[var_name] = {
                "description": spec.get("description", ""),
                "type": type(value).__name__,
            }
        _register_kernel_vars(cell_id, caller_ns, type_specs, set_by)

    def _show_info_for(item, observed_value=None):
        """Render the info pane.

        Three empty-state branches when `item is None`:
          - observed_value is None → show placeholder ("set the source first")
          - observed_value is not None → show no_items_message if provided,
            else fall back to placeholder
        """
        if info_out is None:
            return
        with info_out:
            info_out.clear_output()
            if item is None:
                if observed_value is None:
                    if placeholder:
                        print(placeholder)
                else:
                    msg = no_items_message or placeholder
                    if msg:
                        print(msg)
                return
            if info_template:
                print(_format_template(info_template, item))
            summary_parts = [
                f"{n}={caller_ns.get(n)}"
                for n, s in kernel_vars.items()
                if s.get("field") != "@self"
            ]
            if summary_parts:
                print("\n✓ " + ", ".join(summary_parts))

    def _select(item):
        _write_kernel_vars(item)
        _show_info_for(item)

    def _on_change(change):
        if change.get("name") == "value" and change.get("new") is not None:
            _select(change["new"])

    # Initial state — usually observed_value is None (e.g. USER_BBOX not yet drawn),
    # but if the caller pre-populated the kernel variable before this call, treat that
    # as already-set and show no_items_message instead of placeholder.
    initial_observed = caller_ns.get(observes) if observes else None
    if initial_items:
        _select(initial_items[0])
    else:
        _write_kernel_vars(None)
        _show_info_for(None, observed_value=initial_observed)

    dropdown.observe(_on_change, names="value")

    # Reactive: when `observes` changes, refresh options
    def _refresh(new_value=None):
        new_items = _resolve_items()
        new_options = [(_format_template(label_template, p), p) for p in new_items]
        # Detach observer to avoid double-fires while options change
        try:
            dropdown.unobserve(_on_change, names="value")
        except Exception:
            pass
        dropdown.options = new_options
        try:
            dropdown.observe(_on_change, names="value")
        except Exception:
            pass

        if new_items:
            dropdown.layout.display = ""
            _select(new_items[0])
        else:
            dropdown.layout.display = "none"
            _write_kernel_vars(None)
            _show_info_for(None, observed_value=new_value)

    if observes is not None:
        _subscribe_var_change(caller_ns, observes, _refresh, cell_id)

    # Print initial state to stdout for agent narration. Phrasing is deliberate.
    print("\n[sage-dropdown] Dropdown rendered.", end=" ")
    if initial_items:
        print("Initial / default selection (the user may change this in the dropdown widget):")
        for _vn in kernel_vars:
            try:
                print(f"  {_vn} = {caller_ns.get(_vn)!r}")
            except Exception:
                print(f"  {_vn} = <unprintable>")
    else:
        if observes:
            print(f"Hidden — waiting for `{observes}` to be set. "
                  f"Will populate automatically when the user provides input.")
        else:
            print("Hidden — items list is empty.")

    # Reopen notice — hidden by default; revealed only if elapsed > 30s (notebook reopen).
    notice_id = f"sage-dropdown-notice-{uuid.uuid4().hex[:8]}"
    cell_run_ms = int(time.time() * 1000)
    reopen_notice = HTML(f'''
<div id="{notice_id}" data-runtime="{cell_run_ms}" style="padding:8px 12px;
     background:#fff8e1;border-left:3px solid #f4b400;font-size:12px;color:#555;
     margin:6px 0;border-radius:2px; display:none;">
    <b>⟳ Interactive widget</b> — re-run this cell to restore dropdown interactivity.
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
    var hideBrokenWidgets = function() {{
        var cell = n.closest(".jp-Cell") || n.closest(".jp-OutputArea") || document;
        // Hide ONLY outputs whose textContent matches "Error displaying widget" /
        // "model not found". Conservative — we don't try to identify broken
        // SVG icons because the same pattern can match valid widget icons
        // (zoom controls, draw tools, etc.).
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

    children = [widgets.HTML(f"<h3>{header}</h3>"), dropdown]
    if info_out is not None:
        children.append(info_out)

    # Build a static HTML fallback so nbviewer / GitHub renderers (which
    # don't load ipywidgets) show a preview of the dropdown instead of a
    # bare yellow re-run banner. Live JupyterLab still renders the
    # interactive VBox via the widget mime type below.
    # To roll back: replace this whole block (down to the final display()
    # calls) with the single original line:
    #   display(reopen_notice, widgets.VBox(children), exclude=["text/plain"])
    def _esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    static_html = ""
    try:
        if initial_options:
            options_html = "".join(
                f"<option>{_esc(lbl)}</option>" for lbl, _ in initial_options
            )
            select_html = (
                f'<div style="display:flex; align-items:center; gap:8px; margin:6px 0;">'
                f'<label style="font-weight:bold;">{_esc(description)}</label>'
                f'<select disabled style="padding:4px; border:1px solid #ccc; '
                f'border-radius:3px; min-width:300px; background:#fafafa;">'
                f"{options_html}</select></div>"
            )
            info_html = ""
            if info_template and initial_items:
                info_text = _format_template(info_template, initial_items[0])
                info_html = (
                    f'<pre style="font-size:0.9em; background:#f7f7f7; padding:8px; '
                    f'border-radius:3px; margin:6px 0 0 0; white-space:pre-wrap;">'
                    f"{_esc(info_text)}</pre>"
                )
        else:
            # Render a disabled dropdown shape even when empty so static
            # viewers (nbviewer / GitHub) see a recognizable dropdown
            # control rather than just a paragraph of italic text.
            msg = placeholder or no_items_message or "Waiting for input."
            select_html = (
                f'<div style="display:flex; align-items:center; gap:8px; margin:6px 0;">'
                f'<label style="font-weight:bold;">{_esc(description)}</label>'
                f'<select disabled style="padding:4px; border:1px solid #ccc; '
                f'border-radius:3px; min-width:300px; background:#fafafa; '
                f'color:#888; font-style:italic;">'
                f"<option>— {_esc(msg)} —</option>"
                f"</select></div>"
            )
            info_html = ""
        static_html = (
            f'<div style="margin:10px 0; padding:10px; border:1px solid #e0e0e0; '
            f'border-radius:4px;">'
            f'<h3 style="margin:0 0 8px 0;">{_esc(header)}</h3>'
            f"{select_html}{info_html}"
            f"</div>"
        )
    except Exception:
        static_html = ""

    display(reopen_notice, exclude=["text/plain"])
    _vbox = widgets.VBox(children)
    if static_html and getattr(_vbox, "_model_id", None):
        display(
            {
                "application/vnd.jupyter.widget-view+json": {
                    "version_major": 2,
                    "version_minor": 0,
                    "model_id": _vbox._model_id,
                },
                "text/html": static_html,
            },
            raw=True,
        )
    else:
        display(_vbox, exclude=["text/plain"])
