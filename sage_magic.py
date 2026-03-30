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


# ---------------------------------------------------------------------------
# Output file display
# ---------------------------------------------------------------------------

_LAYER_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12",
    "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
]


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

        geojson_layers = []
        for path in geojson_files:
            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Non closed ring")
                    gdf = gpd.read_file(path)
                for col in gdf.columns:
                    if str(gdf[col].dtype).startswith("datetime"):
                        gdf[col] = gdf[col].astype(str)
                geojson_layers.append((path.stem, gdf))
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
        if geojson_layers:
            all_bounds = [gdf.total_bounds for _, gdf in geojson_layers]
            minx = min(b[0] for b in all_bounds)
            miny = min(b[1] for b in all_bounds)
            maxx = max(b[2] for b in all_bounds)
            maxy = max(b[3] for b in all_bounds)
            center = [(miny + maxy) / 2, (minx + maxx) / 2]
        else:
            # bbox format: [min_lat, min_lon, max_lat, max_lon]
            bbox = wms_layers[0]["bbox"]
            center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]

        m = folium.Map(location=center, zoom_start=6, tiles=None)

        # Background tile layers (first = default)
        folium.TileLayer("OpenStreetMap", name="Street Map").add_to(m)
        folium.TileLayer("CartoDB.Positron", name="Light", show=False).add_to(m)
        folium.TileLayer("Esri.WorldTopoMap", name="Topographic", show=False).add_to(m)
        folium.TileLayer("Esri.WorldImagery", name="Satellite", show=False).add_to(m)

        # Add GeoJSON layers
        for i, (name, gdf) in enumerate(geojson_layers):
            color = _LAYER_COLORS[i % len(_LAYER_COLORS)]
            popup_fields = [c for c in gdf.columns if c != "geometry"][:5]
            fg = folium.FeatureGroup(name=name, show=True)
            folium.GeoJson(
                gdf,
                marker=folium.CircleMarker(radius=3, fill=True),
                style_function=lambda x, c=color: {
                    "fillColor": c, "color": "#333333",
                    "weight": 0.5, "fillOpacity": 0.8,
                },
                popup=folium.GeoJsonPopup(fields=popup_fields) if popup_fields else None,
            ).add_to(fg)
            fg.add_to(m)

        # Add WMS layers
        for wms in wms_layers:
            folium.raster_layers.WmsTileLayer(
                url=wms["url"],
                layers=wms["layers"],
                name=wms.get("name", "WMS Layer"),
                fmt="image/png",
                transparent=True,
                opacity=wms.get("opacity", 0.7),
            ).add_to(m)

        folium.LayerControl(collapsed=False).add_to(m)
        # Build header HTML and combine with map in a single display() call
        # to avoid extra inter-output gaps in nbviewer
        if caption:
            header = (
                f'<div style="font-weight:bold; font-size:0.95em; '
                f'margin:10px 0 4px 0;">{caption}</div>'
            )
        elif show_header:
            all_names = [name for name, _ in geojson_layers] + [w.get("name", "WMS") for w in wms_layers]
            header = f"<b>Map:</b> {', '.join(all_names)}<br>"
        else:
            header = ""
        display(HTML(header + m._repr_html_()))

    except ImportError as e:
        display(HTML(f"Map — install folium+geopandas to render: {e}"))
    except Exception as e:
        display(HTML(f"Map — error rendering: {e}"))


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

def _render_markdown_with_files(text: str) -> bool:
    """Render markdown that embeds file references inline.

    The agent writes standard markdown image syntax to reference output files:
      ![caption](full_path/to/file.geojson)        → Folium map
      ![caption](file1.geojson,file2.geojson)       → multi-layer Folium map
      ![caption](full_path/to/file.png)             → inline image

    Text segments between file references are rendered as Markdown.
    Returns True if at least one file reference was found and rendered.
    """
    import re
    from IPython.display import display, Markdown, HTML

    if not text.strip():
        return False

    # Standard markdown image: ![alt text](src)
    pattern = re.compile(r'!\[([^\]]*)\]\(([^)\n]+)\)')

    last_end = 0
    found_any = False

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
        return False
    remaining = text[last_end:].strip()
    if remaining:
        display(Markdown(remaining))

    return True


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
    from langchain_core.messages import AIMessage

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
            f"Embed output files inline where most relevant using standard markdown image syntax:\n"
            f"  ![Image caption](full_path_to_file.png)                — for a PNG chart or image\n"
            f"  ![Map caption](full_path_to_file.geojson)              — for a single-layer map\n"
            f"  ![Map caption](file1.geojson,file2.geojson)            — MULTIPLE layers on ONE map (comma-separated)\n"
            f"  ![Map caption](file.geojson,layer.wms.json)            — GeoJSON + WMS together on ONE map\n"
            f"MAP RULE — one map per report, all layers combined: your entire report must contain "
            f"exactly ONE map tag. Put every GeoJSON and WMS layer that is relevant to the result "
            f"into that single tag as comma-separated paths. Never create separate maps for different "
            f"layers — always combine them.\n"
            f"Correct (one map, two layers):  "
            f"![Earthquake and GNSS Stations]({SAGE_OUTPUT_DIR}/earthquakes.geojson,{SAGE_OUTPUT_DIR}/gnss_stations.geojson)\n"
            f"Wrong (two separate maps):  "
            f"![Earthquake]({SAGE_OUTPUT_DIR}/earthquakes.geojson) ... ![GNSS Stations]({SAGE_OUTPUT_DIR}/gnss_stations.geojson)\n"
            f"Always use full absolute paths from {SAGE_OUTPUT_DIR}. "
            f"Do not list files separately at the end — embed them inline in the report.\n\n"
            f"{prompt}"
        )

        # Snapshot output folder before run
        before = _snapshot(SAGE_OUTPUT_DIR)

        # Run agent with streaming tool display; get back final report + tool counts.
        # Use run_until_complete on the existing loop (patched by nest_asyncio)
        # instead of asyncio.run(), which conflicts with Python 3.13's task cleanup.
        import time as _time
        _t_start = _time.time()
        final_text, tool_counts = asyncio.get_event_loop().run_until_complete(
            _run_agent_async(full_prompt)
        )
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

        # Fix GLM markdown quirks before rendering
        final_text = _fix_glm_markdown(final_text)

        # Render the final report — file references become maps/images inline
        rendered = _render_markdown_with_files(final_text)

        if not rendered:
            # Fallback: plain markdown + auto-display new files separately
            if final_text.strip():
                from IPython.display import display, Markdown
                display(Markdown(final_text))
            after = _snapshot(SAGE_OUTPUT_DIR)
            new = _new_files(before, after)
            if new:
                _display_new_outputs(new)

    del ask  # keep IPython namespace clean

    from IPython.core.magic import register_line_magic

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

        # Clear conversation history
        global SAGE_MESSAGES
        SAGE_MESSAGES.clear()

        display(Markdown(
            f"**Sage reset.**\n"
            f"- Output folder cleared: `{SAGE_OUTPUT_DIR}` ({files_deleted} items removed)\n"
            f"- Conversation history cleared"
        ))

    del reset  # keep IPython namespace clean

except Exception as exc:
    warnings.warn(
        f"Sage magic commands could not be registered: {exc}", stacklevel=1
    )
