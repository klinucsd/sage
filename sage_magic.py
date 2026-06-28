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
import sys
import warnings
from datetime import UTC as _SAGE_UTC, datetime as _SAGE_DATETIME
from pathlib import Path


# Reference to the kernel's IPython OutStream, captured BEFORE anything in
# this session has a chance to swap sys.stdout. _sage_progress() writes to
# this so progress lines stream live to the cell even when execute() has
# redirected sys.stdout / sys.__stdout__ to the capture buffer.
_SAGE_KERNEL_STDOUT = sys.stdout


def _sage_progress(msg: str) -> None:
    """Print one line of progress live to the cell, bypassing the execute()
    stdout capture. Use ONLY for multi-item loops in long-running skills
    (e.g. downloading many workspaces, batch-processing many submissions)
    where the user needs to see that work is happening. Plain `print()`
    is captured and hidden; this helper is the explicit opt-in for live
    visibility.

    Available in the kernel namespace — no import needed."""
    try:
        print(msg, file=_SAGE_KERNEL_STDOUT, flush=True)
    except Exception:
        pass  # never let progress emission break the script


def _sage_pip_artifact_cleanup():
    """Remove partial pip-install artifacts (~package directories) from
    site-packages. NRP pod startup and failed `pip install` (no --user)
    attempts leave these behind, and every subsequent pip op then prints
    noisy 'Ignoring invalid distribution ~xxx' warnings that pollute cell
    output. Idempotent and silent on systems without matching paths."""
    import glob as _glob
    import os as _os
    import shutil as _shutil
    for entry in _glob.glob("/opt/conda/lib/python*/site-packages/~*"):
        try:
            if _os.path.isdir(entry):
                _shutil.rmtree(entry, ignore_errors=True)
            else:
                _os.remove(entry)
        except Exception:
            pass


_sage_pip_artifact_cleanup()


def _install_pip_subprocess_guard():
    """Monkey-patch subprocess.Popen so that any `pip install` invocation
    from agent-written scripts is transparently rewritten to include
    `--user --quiet --no-warn-script-location` with stdout/stderr suppressed.

    Rationale: GLM sometimes ignores the PACKAGE INSTALL RULE in the system
    prompt and calls `pip install <pkg>` directly via subprocess. Without
    `--user` that fails on the read-only `/opt/conda` site-packages and
    dumps a "Permission denied" error into the cell. Rather than depend on
    instruction-following, intercept the call and make it correct.

    Idempotent (safe to call multiple times). Active for the whole kernel
    session. Plain `subprocess.Popen([...])` calls that aren't pip-install
    are untouched."""
    import subprocess as _sp
    if getattr(_sp, "_sage_pip_guarded", False):
        return

    _real_Popen = _sp.Popen

    def _split_args(args):
        if isinstance(args, str):
            return args.split(), True
        if isinstance(args, (list, tuple)):
            return [str(a) for a in args], False
        return [], False

    def _is_pip_install(parts):
        if not parts:
            return False
        prog = parts[0]
        # `pip install ...` or `pip3 install ...`
        if prog.endswith("pip") or prog.endswith("pip3"):
            return len(parts) >= 2 and parts[1] == "install"
        # `<python> -m pip install ...`
        if (prog.endswith("python") or prog.endswith("python3")
                or "/python" in prog):
            return (len(parts) >= 4 and parts[1] == "-m"
                    and parts[2] in ("pip", "pip3") and parts[3] == "install")
        return False

    def _rewrite(parts):
        try:
            i = parts.index("install")
        except ValueError:
            return parts
        existing = set(parts[i + 1:])
        injected = [f for f in ("--user", "--quiet", "--no-warn-script-location")
                    if f not in existing]
        return parts[:i + 1] + injected + parts[i + 1:]

    class _PatchedPopen(_real_Popen):
        """Subclass of subprocess.Popen that rewrites pip-install invocations.

        Why a subclass (not a function wrapper): some third-party code uses
        ``subprocess.Popen[bytes]`` type annotations evaluated at class-body
        time. A function replacement breaks those with ``'function' object is
        not subscriptable``. A real subclass inherits ``__class_getitem__``
        and stays subscriptable.

        Non-pip calls short-circuit in ``__new__`` by returning a plain
        ``_real_Popen`` instance, so the override has zero impact outside the
        pip-install path.
        """

        def __new__(cls, args, *posargs, **kwargs):
            parts, _ = _split_args(args)
            if not _is_pip_install(parts):
                return _real_Popen(args, *posargs, **kwargs)
            return super().__new__(cls)

        def __init__(self, args, *posargs, **kwargs):
            parts, was_string = _split_args(args)
            parts = _rewrite(parts)
            args = " ".join(parts) if was_string else parts
            # Suppress stdout (pip's success chatter); capture stderr to PIPE
            # so we can re-emit it ONLY if the install fails.
            kwargs["stdout"] = _sp.DEVNULL
            kwargs["stderr"] = _sp.PIPE
            super().__init__(args, *posargs, **kwargs)
            self._sage_pip_err = {"data": None, "emitted": False}

        def _sage_pip_emit_on_fail(self, rc):
            h = self._sage_pip_err
            if rc != 0 and h["data"] and not h["emitted"]:
                try:
                    import sys as _sys
                    _sys.stderr.write(h["data"].decode(errors="replace"))
                    h["emitted"] = True
                except Exception:
                    pass

        def wait(self, *a, **k):
            rc = super().wait(*a, **k)
            h = self._sage_pip_err
            if (h["data"] is None
                    and self.stderr is not None
                    and not self.stderr.closed):
                try:
                    h["data"] = self.stderr.read()
                except Exception:
                    pass
            self._sage_pip_emit_on_fail(rc)
            return rc

        def communicate(self, *a, **k):
            out, err = super().communicate(*a, **k)
            if err is not None:
                self._sage_pip_err["data"] = err
            self._sage_pip_emit_on_fail(self.returncode)
            return out, err

    _sp.Popen = _PatchedPopen
    _sp._sage_pip_guarded = True


_install_pip_subprocess_guard()


def _sage_pip_install(*pkgs: str) -> None:
    """Silent, correct pip install for use inside agent-written scripts.

    Behavior:
      - If every package is already importable, no-op.
      - Otherwise installs with `--user --quiet --no-warn-script-location`,
        stderr suppressed, then sweeps `~*` artifacts from site-packages.
      - Raises only if pip's exit code is nonzero.

    The kernel exposes this in user_ns so agent scripts can do:
        _sage_pip_install("lightkurve")
        import lightkurve
    instead of crafting their own pip command (which historically gets the
    `--user` flag wrong, embeds shell `2>/dev/null` inside subprocess list
    args, or omits `--quiet` and floods the cell with warnings)."""
    import importlib.util as _il_util
    import subprocess as _sp
    import sys as _sys

    def _pkg_name(spec):
        for sep in ("[", "=", ">", "<", "~", "!"):
            spec = spec.split(sep)[0]
        return spec.strip()

    missing = [p for p in pkgs if _il_util.find_spec(_pkg_name(p)) is None]
    if not missing:
        return

    cmd = [_sys.executable, "-m", "pip", "install",
           "--user", "--quiet", "--no-warn-script-location",
           *missing]
    _sp.run(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, check=True)
    _sage_pip_artifact_cleanup()

# ---------------------------------------------------------------------------
# Per-kernel state — unique per notebook, persists for the session
# ---------------------------------------------------------------------------

# Single thread ID for the whole kernel session — gives the agent memory
# across all %%ask cells in the same notebook.
from deepagents_code.sessions import generate_thread_id  # noqa: E402
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


def _init_learnings_dir() -> str:
    """Resolve the per-skill Learnings.md root.

    Order of resolution:
      1. SAGE_LEARNINGS_PATH env var (set explicitly by the user or by the
         Docker image — the NRP image points this at the persistent CephBlock
         mount so learnings survive across pod restarts).
      2. ~/.sage_learnings/ as a portable default.

    The directory is created if missing. Per-skill subdirectories
    (<root>/<skill_name>/Learnings.md) are created on demand by the
    agent the first time it writes a lesson."""
    override = os.environ.get("SAGE_LEARNINGS_PATH", "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        root = Path.home() / ".sage_learnings"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Read-only filesystem or permission error — fall back to /tmp so
        # the agent still has somewhere to write.
        root = Path("/tmp/sage_learnings")
        root.mkdir(parents=True, exist_ok=True)
    return str(root)


SAGE_LEARNINGS_DIR = _init_learnings_dir()


def _sage_build_learnings_skills_set() -> set:
    """Return the set of skill names that currently have a Learnings.md
    file under SAGE_LEARNINGS_DIR.

    Used to short-circuit the agent's read-Learnings.md step: if a skill
    is not in this set, the agent skips the read entirely instead of
    issuing a `read_file` that returns "file not found". Saves ~5-10s
    of LLM round-trip per skill on cells that consult several skills
    whose Learnings.md does not yet exist.

    Called at kernel startup (initial population) AND at the start of
    every `%%ask` cell (right before system-prompt assembly) so any
    Learnings.md created in earlier cells of the session is picked up
    on the next cell. The scan is cheap: one `(child / 'Learnings.md').
    exists()` per child of SAGE_LEARNINGS_DIR."""
    root = Path(SAGE_LEARNINGS_DIR)
    if not root.exists():
        return set()
    out = set()
    try:
        for child in root.iterdir():
            if child.is_dir() and (child / "Learnings.md").exists():
                out.add(child.name)
    except OSError:
        pass
    return out


SAGE_LEARNINGS_SKILLS = _sage_build_learnings_skills_set()

# Expose both in IPython namespace so users can reference them
try:
    ip = get_ipython()  # noqa: F821
    ip.user_ns["SAGE_OUTPUT_DIR"] = SAGE_OUTPUT_DIR
    ip.user_ns["SAGE_THREAD_ID"] = SAGE_THREAD_ID
    ip.user_ns["SAGE_LEARNINGS_DIR"] = SAGE_LEARNINGS_DIR
    ip.user_ns["SAGE_LEARNINGS_SKILLS"] = SAGE_LEARNINGS_SKILLS
    ip.user_ns["_sage_pip_install"] = _sage_pip_install
    ip.user_ns["_sage_pip_artifact_cleanup"] = _sage_pip_artifact_cleanup
    ip.user_ns["_sage_progress"] = _sage_progress
    # _SAGE_RESET_KEEP is populated at the END of this startup script (see
    # bottom of file) — at that point every top-level def/import this script
    # adds to user_ns is already there, so the snapshot is complete.
except Exception:
    pass

# ---------------------------------------------------------------------------
# Cleanup of NRP pod-startup pip artifacts
# ---------------------------------------------------------------------------
# NRP JupyterHub's pod startup runs pip operations that can be interrupted,
# leaving ~-prefixed dist-info directories under
# /opt/conda/lib/python3.*/site-packages/. Every subsequent pip call then
# emits "WARNING: Ignoring invalid distribution ~..." lines, which surface
# in Sage cell output and look like errors to viewers.
# These artifacts are jovyan-writable (pip created them as jovyan during
# pod startup) even though /opt/conda/ is generally read-only — so silent
# rmtree works. Safe no-op if no artifacts exist.
try:
    import shutil as _shutil
    import glob as _glob
    for _bad in _glob.glob("/opt/conda/lib/python3.*/site-packages/~*"):
        try:
            _shutil.rmtree(_bad)
        except (OSError, PermissionError):
            pass
    del _shutil, _glob
    if "_bad" in dir():
        del _bad
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
_SAGE_INTERNAL_FILES = {
    ".sage_cells.json", ".sage_run.jsonl", ".sage_colors.json",
    ".sage_kernel_vars.json", ".sage_cell_runs.json",
}


# MCP server registry — populated by %%mcp, consumed by %%ask.
# Kernel-scoped: persists across cells, wiped on kernel restart.
# Tools are stored per-server so MERGE semantics can replace one server's
# tools without touching others.
_SAGE_MCP_TOOLS_BY_SERVER: dict = {}  # server name → list of BaseTool
_SAGE_MCP_CLIENT = None
_SAGE_MCP_SERVERS: dict = {}          # server name → normalized config


def _sage_mcp_all_tools():
    """Flatten all registered MCP tools across servers into one list."""
    return [t for tools in _SAGE_MCP_TOOLS_BY_SERVER.values() for t in tools]


def _sage_find_notebook_with_mcp_cell():
    """Check the CURRENT notebook for a cell whose source starts with %%mcp.

    Returns the notebook's filename if it has a %%mcp cell, None otherwise.
    Used to surface a soft warning when %%ask runs with an empty MCP
    registry — but only when the user's *own* notebook expects MCP. We
    intentionally do NOT scan other notebooks in the directory (false
    positive: an unrelated mcp_test.ipynb sitting next to a fresh notebook
    that doesn't use MCP).

    Identifies the current notebook via JPY_SESSION_NAME (set by
    JupyterHub/JupyterLab to the notebook path). If unset (terminal
    kernel, non-Jupyter context), returns None — we'd rather miss a true
    positive than show a misleading warning naming the wrong notebook.
    """
    session = os.environ.get("JPY_SESSION_NAME", "")
    if not session:
        return None
    nb_path = Path(session)
    if not nb_path.is_absolute():
        # Resolve relative paths against home dir (matches JupyterLab convention)
        nb_path = Path.home() / nb_path
    if not nb_path.exists():
        return None
    try:
        with open(nb_path) as f:
            nb = json.load(f)
        for cell in nb.get("cells", []):
            if cell.get("cell_type") != "code":
                continue
            src = "".join(cell.get("source", []))
            if src.lstrip().startswith("%%mcp"):
                return nb_path.name
    except Exception:
        pass
    return None


# Persisted cell-run windows — track which %%ask cells started but didn't
# finish, across kernel restarts. Used for orphan-file cleanup.
#
# Schema (.sage_cell_runs.json):
#   {
#     "<cell_id>": {"started_at": <float>, "finished_at": <float|None>},
#     ...
#   }
#
# At cell entry: any record with finished_at=null is from a killed cell
# (interrupted, kernel crashed, cell deleted before finally ran). Files in
# that cell's window which aren't in any cell-output registry are presumed
# partial outputs and get removed with a warning.
#
# Persisting to disk (rather than in-memory) means a kernel restart still
# remembers "the previous cell was killed" and the next cell can clean up.

def _load_cell_runs() -> dict[str, dict[str, float | None]]:
    p = Path(SAGE_OUTPUT_DIR) / ".sage_cell_runs.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_cell_runs(runs: dict[str, dict[str, float | None]]) -> None:
    p = Path(SAGE_OUTPUT_DIR) / ".sage_cell_runs.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(runs, indent=2))
    except Exception:
        pass


def _orphan_cleanup_for_dead_cell(
    prev_start: float,
    now: float,
) -> list[str]:
    """Delete files in SAGE_OUTPUT_DIR whose mtime falls inside the dead
    cell's window, are NOT in any cell's file-registry, and are not
    internal sage files. Returns the list of deleted file paths.

    Caller has determined that some previous cell's run had no
    finished_at (it was killed); we treat unregistered files in its
    window as presumed partial outputs.
    """
    # 1-second buffer on both sides for mtime/clock fuzziness
    window_lo = prev_start - 1.0
    window_hi = now + 1.0
    reg = _load_cell_registry()
    all_registered = {f for files in reg.values() for f in files}
    deleted: list[str] = []
    for orphan in Path(SAGE_OUTPUT_DIR).rglob("*"):
        try:
            if not orphan.is_file():
                continue
            if orphan.name in _SAGE_INTERNAL_FILES:
                continue
            if str(orphan) in all_registered:
                continue
            mtime = orphan.stat().st_mtime
            if not (window_lo <= mtime <= window_hi):
                continue
            orphan.unlink(missing_ok=True)
            deleted.append(str(orphan))
        except Exception:
            pass
    return deleted


def _display_orphan_cleanup_warning(deleted: list[str]) -> None:
    """Show a yellow banner telling the user that orphan files from a
    previous-cell that didn't finish were removed."""
    from IPython.display import display, HTML
    n = len(deleted)
    if n == 0:
        return
    # Show up to first 15 filenames so the banner stays readable.
    shown = deleted[:15]
    file_items = "".join(
        f"<li><code>{Path(f).name}</code></li>"
        for f in shown
    )
    more = (
        f"<li><i>… and {n - len(shown)} more</i></li>"
        if n > len(shown) else ""
    )
    display(HTML(
        '<div style="background:#fff8e1; border-left:4px solid #f0ad4e; '
        'padding:10px 14px; margin:6px 0; font-size:0.92em;">'
        f'<b>⚠️ The previous <code>%%ask</code> cell did not complete normally.</b> '
        f'Removed {n} file(s) from the working directory as presumed partial outputs:'
        f'<ul style="margin:6px 0 0 18px;">{file_items}{more}</ul>'
        '<div style="margin-top:6px; color:#7a5d00;">'
        'If any of these were files you intended to keep, save them outside the '
        'working directory before running cells. Use <code>%reset</code> to '
        'clear all working-directory files.'
        '</div></div>'
    ))


def _get_cell_id() -> str | None:
    """Return the current cell's unique ID from IPython kernel metadata, or None."""
    try:
        return get_ipython().parent_header.get('metadata', {}).get('cellId')  # noqa: F821
    except Exception:
        return None


def _reconstruct_messages_from_notebook(stop_at_cell_id: str | None = None) -> list:
    """Read the on-disk .ipynb and extract %ask conversation history.

    Used to restore SAGE_MESSAGES after a kernel restart so the agent has
    cross-cell memory without forcing the user to re-run every prior cell.

    Walks the notebook top-to-bottom in document order. For each code cell
    that begins with %ask or %%ask:
      - The cell source (after the magic line) becomes a "user" message.
      - All text/markdown display_data outputs are concatenated as the
        "assistant" message (this captures the agent's final report,
        including any inline file rendering).

    **Stops** when it reaches the cell whose id is `stop_at_cell_id` (the
    currently-running cell). Cells after the current one are NOT included —
    they came *after* this one in the conversation and were built on this
    cell's prior output, which we're about to overwrite. Including them
    would feed the agent its own future.

    Returns [] if the notebook can't be read or no prior %ask cells exist.
    Lag note: the .ipynb on disk reflects the last autosave, so cells
    executed in the last ~2 minutes without a save may not appear.
    """
    session = os.environ.get("JPY_SESSION_NAME", "")
    if not session:
        return []
    nb_path = Path(session)
    if not nb_path.is_absolute():
        nb_path = Path.home() / nb_path
    if not nb_path.exists():
        return []

    try:
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    messages: list = []
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        # Stop at the current cell — do NOT include cells after it (they
        # came later in the conversation and built on this cell's prior output).
        if stop_at_cell_id and cell.get("id") == stop_at_cell_id:
            break

        # Skip cells that were never executed. A %%ask cell the user wrote
        # but did not run is NOT part of the prior conversation; including
        # its prompt would feed the agent a request the user hasn't asked
        # yet. (Observed bug: running the last %%ask cell caused the agent
        # to process every unrun %%ask cell above it as a single batch.)
        if cell.get("execution_count") is None:
            continue

        source = cell.get("source", [])
        if isinstance(source, list):
            source = "".join(source)
        source = source.strip()
        if not source:
            continue

        first_line, _, rest = source.partition("\n")
        first_line = first_line.strip()
        if first_line.startswith("%%ask"):
            prompt = rest.strip()
        elif first_line.startswith("%ask "):
            prompt = first_line[len("%ask "):].strip()
        else:
            continue
        if not prompt:
            continue

        # Concatenate all text/markdown blobs from display_data / execute_result outputs
        response_parts = []
        for out in cell.get("outputs", []):
            if out.get("output_type") not in ("display_data", "execute_result"):
                continue
            data = out.get("data", {})
            md = data.get("text/markdown")
            if md is None:
                continue
            if isinstance(md, list):
                md = "".join(md)
            response_parts.append(md)
        response = "\n".join(p.strip() for p in response_parts).strip()

        cid = cell.get("id")
        messages.append({"role": "user", "content": prompt, "cell_id": cid})
        if response:
            messages.append({"role": "assistant", "content": response, "cell_id": cid})

    return messages


def _truncate_messages_for_rerun(messages: list, cell_id: str | None) -> list:
    """Remove all SAGE_MESSAGES entries belonging to `cell_id` and everything
    that came after them. Called before each %ask so a rerun of a cell
    doesn't leave stale entries (its own old answer + all subsequent cells'
    history that was built on it) in the conversation. No-op if cell_id is
    None or not found.
    """
    if not cell_id:
        return messages
    for i, m in enumerate(messages):
        if m.get("cell_id") == cell_id:
            return messages[:i]
    return messages


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


def _load_kernel_vars_registry() -> dict:
    """Load .sage_kernel_vars.json — maps cell_id → {var_name: {description, type, set_by}}.

    Tracks kernel variables registered by UI skills (sage-dropdown, sage-bbox-map, etc.)
    so that (1) future cells discover what variables exist and (2) cell reruns can clean
    up the variables they previously created before re-executing.
    """
    p = Path(SAGE_OUTPUT_DIR) / ".sage_kernel_vars.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_kernel_vars_registry(registry: dict) -> None:
    """Persist .sage_kernel_vars.json."""
    (Path(SAGE_OUTPUT_DIR) / ".sage_kernel_vars.json").write_text(
        json.dumps(registry, indent=2)
    )


def _kernel_vars_registry_prompt() -> str:
    """Build a system-prompt block listing currently-available kernel variables.

    Filters to variables whose name still exists in user_ns (staleness filter).
    Format mirrors _color_registry_prompt — section header + bulleted list.
    """
    registry = _load_kernel_vars_registry()
    if not registry:
        return ""
    try:
        user_ns = get_ipython().user_ns  # noqa: F821
    except Exception:
        return ""

    # Flatten {cell_id: {var_name: meta}} → list of (var_name, meta), skipping stale
    live = []
    for cell_id, vars_dict in registry.items():
        for var_name, meta in vars_dict.items():
            if var_name in user_ns:
                live.append((var_name, meta))
    if not live:
        return ""

    def _short_value(v):
        """Compact, safe repr of a kernel variable for prompt display.
        Without the current value, the agent narrates from guess (e.g., names a
        random planet from the dropdown). Show primitives in full, abbreviate
        containers, hide objects too large or non-trivial to format.
        """
        try:
            if v is None or isinstance(v, (bool, int, float)):
                return repr(v)
            if isinstance(v, str):
                return repr(v) if len(v) <= 120 else repr(v[:117] + "...")
            if isinstance(v, (list, tuple)) and len(v) <= 8:
                inner = ", ".join(_short_value(x) for x in v)
                return f"({inner})" if isinstance(v, tuple) else f"[{inner}]"
            if isinstance(v, (list, tuple)):
                return f"<{type(v).__name__}, len={len(v)}>"
            if isinstance(v, dict) and len(v) <= 8:
                items = ", ".join(f"{k!r}: {_short_value(val)}" for k, val in v.items())
                return "{" + items + "}"
            if isinstance(v, dict):
                return f"<dict, {len(v)} keys>"
            return f"<{type(v).__name__}>"
        except Exception:
            return f"<{type(v).__name__}>"

    lines = ["EXISTING KERNEL VARIABLES (set by previous cells, available now):"]
    for var_name, meta in live:
        desc = meta.get("description", "")
        type_ = meta.get("type", "?")
        set_by = meta.get("set_by", "?")
        cur = _short_value(user_ns.get(var_name))
        lines.append(
            f"- `{var_name}` ({type_}) = {cur} — {desc} [set by {set_by}]"
        )
    lines.append(
        "In agent scripts run as subprocesses (`python /path/to/script.py`), "
        "`globals()` does NOT see kernel state — copy each variable's literal "
        "VALUE from the list above directly into the script. The registry file "
        "`.sage_kernel_vars.json` holds only metadata (descriptions), not "
        "values; do not try to read it from a script. Always copy the exact "
        "value shown above; do not invent, substitute, or use any example "
        "values from this system prompt as a real value."
    )
    return "\n".join(lines) + "\n\n"


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


def _hue_family(hex_color: str) -> str:
    """Map a hex color (e.g. '#e74c3c') to a hue-family name.

    Returns one of: red, orange, yellow, green, teal, blue, purple, magenta,
    or 'neutral' for near-grayscale colors (very low saturation or extreme
    lightness). '?' if the input can't be parsed.
    """
    import colorsys
    try:
        h = hex_color.lstrip("#")
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
    except (ValueError, IndexError):
        return "?"
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    if sat < 0.20 or light < 0.15 or light > 0.92:
        return "neutral"
    deg = hue * 360.0
    if deg < 15 or deg >= 345:
        return "red"
    if deg < 45:
        return "orange"
    if deg < 70:
        return "yellow"
    if deg < 160:
        return "green"
    if deg < 200:
        return "teal"
    if deg < 240:
        return "blue"
    if deg < 290:
        return "purple"
    return "magenta"


# Suggested categorical palettes per hue family — the agent can copy these
# verbatim when defining a new scheme so distinct layers stay visually distinct.
_FAMILY_PALETTES = {
    "red":     ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "orange":  ["#feedde", "#fdbe85", "#fd8d3c", "#e6550d", "#a63603"],
    "yellow":  ["#ffffd4", "#fee391", "#fec44f", "#fe9929", "#cc4c02"],
    "green":   ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "teal":    ["#edf8fb", "#b2e2e2", "#66c2a4", "#2ca25f", "#006d2c"],
    "blue":    ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "purple":  ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
    "magenta": ["#feebe2", "#fbb4b9", "#f768a1", "#c51b8a", "#7a0177"],
}


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

    # Hue-family awareness: identify which families are already in use, and
    # recommend distinct families (with concrete suggested palettes) for new
    # layers. This catches the case where the FORBIDDEN list lets the agent
    # pick a "different red" (#d62728 vs #e74c3c) that looks identical to
    # the user — exact-hex blocking isn't enough for visual distinction.
    families_in_use = sorted({
        f for f in (_hue_family(c) for c in color_owners)
        if f not in ("neutral", "?")
    })
    suggested_families = [
        f for f in ("blue", "green", "purple", "orange", "teal", "red", "yellow", "magenta")
        if f not in families_in_use
    ][:3]
    family_section = ""
    if families_in_use:
        suggestion_lines = []
        for fam in suggested_families:
            palette_str = ", ".join(_FAMILY_PALETTES[fam])
            suggestion_lines.append(f"  {fam}: {palette_str}")
        family_section = (
            "HUE FAMILIES IN USE — these hue families are already assigned to "
            "existing layers: " + ", ".join(families_in_use) + ". "
            "When defining a new categorical scheme, choose colors from a DIFFERENT "
            "hue family so distinct layers are visually distinguishable on a shared map. "
            "Picking a near-shade of an in-use family (e.g. a different red when red is "
            "already used) is NOT acceptable — exact-hex difference does not give visual "
            "distinction. Suggested palettes for unused families (copy verbatim, "
            "lightest→darkest):\n"
            + "\n".join(suggestion_lines)
            + "\n"
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
        + family_section
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

        # Partition GeoJSON layers into those with usable geometry and those
        # without. A layer with empty or null geometry yields
        # total_bounds = [nan, nan, nan, nan]; centering on it would crash
        # folium.Map(). We drop such layers from the map so it still renders,
        # BUT we surface a visible warning naming them rather than hiding the
        # problem silently — an empty-geometry layer usually means an upstream
        # data error (e.g. a boundary fetched with returnGeometry=false), and
        # the user needs to know their layer is missing, not assume it rendered.
        import math as _math

        def _finite_bounds(b):
            try:
                return (b is not None and len(b) == 4
                        and all(_math.isfinite(float(x)) for x in b))
            except (TypeError, ValueError):
                return False

        kept_layers = []
        dropped_layer_names = []
        for name, gdf, colormap in geojson_layers:
            if _finite_bounds(gdf.total_bounds):
                kept_layers.append((name, gdf, colormap))
            else:
                dropped_layer_names.append(name)
        geojson_layers = kept_layers

        # Emit the warning early so it appears even if a later step fails.
        if dropped_layer_names:
            import html as _html
            _names = ", ".join(f"<code>{_html.escape(str(n))}</code>"
                               for n in dropped_layer_names)
            display(HTML(
                '<div style="background:#fff3cd; border-left:3px solid #f0ad4e;'
                ' padding:6px 10px; margin:4px 0; font-size:0.85em;">'
                f'⚠️ Omitted from the map (empty or null geometry): {_names}. '
                'These layers have no valid coordinates — likely an upstream '
                'data error. Verify the source before trusting any analysis '
                'derived from them.</div>'
            ))

        # Determine map center: valid GeoJSON bounds → WMS bbox → US fallback.
        fit_bounds = None  # [[south, west], [north, east]] for auto-zoom
        center = None
        if geojson_layers:
            all_bounds = [gdf.total_bounds for _, gdf, _ in geojson_layers]
            minx = min(b[0] for b in all_bounds)
            miny = min(b[1] for b in all_bounds)
            maxx = max(b[2] for b in all_bounds)
            maxy = max(b[3] for b in all_bounds)
            center = [(miny + maxy) / 2, (minx + maxx) / 2]
            fit_bounds = [[miny, minx], [maxy, maxx]]
        elif wms_layers and wms_layers[0].get("bbox"):
            # bbox format: [min_lat, min_lon, max_lat, max_lon]
            bbox = wms_layers[0]["bbox"]
            center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
            fit_bounds = [[bbox[0], bbox[1]], [bbox[2], bbox[3]]]

        # If every layer was dropped and there's no WMS bbox, there is nothing
        # meaningful to show — say so instead of rendering an empty US map.
        if center is None:
            if dropped_layer_names:
                display(HTML(
                    '<div style="background:#f8d7da; border-left:3px solid #d9534f;'
                    ' padding:6px 10px; margin:4px 0; font-size:0.85em;">'
                    '⚠️ No map rendered: every layer had empty or null '
                    'geometry. Nothing was plotted.</div>'
                ))
                return
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
    """Auto-display for new CSVs / PNGs / map layers — INTENTIONALLY DISABLED.

    Kept as a documented no-op stub so the design can be revisited.

    DESIGN DECISION — 2026-05-14, v1.1.18
    -------------------------------------
    Disabling this completes the "display is agent-driven" rule that v1.1.17
    started for map layers (GeoJSON / WMS), and now extends to CSV / PNG.

    Why disabled (the duplicate-display problem):
      - The agent typically already shows results inside its executed code
        via `plt.show()`, `display(df)`, `display(Image(...))`, etc., and
        those displays are captured by KernelShellBackend's display hook.
      - When the agent ALSO saves the file to disk (e.g.
        `plt.savefig('chart.png'); plt.show()`), the previous behavior of
        auto-displaying every new CSV / PNG produced a **duplicate** render
        — once via the captured display, once via this function.
      - A simple cap (e.g. show first N files) was considered but only bounds
        the volume; it does not eliminate the duplicate-display class of bug.

    Current rule (display is agent-driven):
      • To show a result inline, the agent's code calls `display(...)` /
        `plt.show()` (captured during exec by KernelShellBackend), OR the
        agent's final response includes an inline
        `![caption](file.csv,file.png,file.geojson)` tag, which
        `_render_markdown_with_files` resolves to the right renderer.
      • Files saved to disk WITHOUT either of the above remain on disk
        only — they do NOT auto-render. The user can inspect them
        explicitly in a follow-up cell.

    User-facing benefit:
      • No surprise output when a cell saves many data files (e.g. project
        downloads with hundreds of nested CSV / PNG / GeoJSON files).
      • No duplicate renders when the agent both shows AND saves a chart.
      • Consistent rule across all file types — predictable, "what the
        agent asks for is what the user sees."

    To revisit (e.g. if save-without-show patterns become common):
      1. Restore a per-file `_display_csv` / `_display_png` loop here.
      2. Pair it with a cap (e.g. 3 files) to bound volume.
      3. Pair it with a dedup pass against `_sage_pending_displays` so
         already-captured displays don't render twice.
      Without all three of those guards, re-enabling this function will
      regress to the same duplicate/wall-of-output behavior that motivated
      this change.
    """
    return


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
    # Lookahead `(?=[^\s*])` requires content's first char to be non-whitespace,
    # non-asterisk WITHOUT consuming it. Prevents the regex from matching across
    # two adjacent bold pairs on the same line, where the content between them
    # would otherwise start with whitespace (e.g. on `**A** more **B**X`, the
    # old regex matched `** more **` as a bold pair). See [[feedback_sage_bold_regex]]
    # Rule 3 for the full story.
    text = re.sub(r'\*\*(?=[^\s*])([^*\n]*\w[^*\n]*)\*\*([^\s*])', r'**\1** \2', text)
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

async def _run_agent_async(prompt: str, system_prompt: str | None = None) -> tuple[str, dict]:
    """Create and stream the agent, displaying tool calls with details.

    Returns (final_text, tool_counts) where tool_counts is a dict mapping
    tool name → number of times it was invoked in this cell.
    """
    from IPython.display import display, Markdown

    from deepagents import create_deep_agent
    from deepagents.backends.local_shell import LocalShellBackend
    from deepagents_code.config import create_model
    try:
        from sage_kernel_backend import KernelShellBackend
    except ImportError:
        KernelShellBackend = None
    from deepagents_code.model_config import ModelConfigError
    from langchain_core.messages import AIMessage, ToolMessage

    try:
        result = create_model(None)
    except ModelConfigError as e:
        print(f"Error: {e}")
        return ""
    model = result.model
    result.apply_to_settings()

    # Discover installed skills.
    # IMPORTANT: deepagents 0.6.8's SkillsMiddleware expects `skills=` to be
    # a list of PARENT directories — it calls backend.ls(source) and treats
    # each is_dir entry as a skill. Passing individual skill subdirectories
    # results in ls() finding only SKILL.md (a file, not a dir), so no skills
    # register at all. The fix is to pass the single parent directory; the
    # middleware then iterates its contents.
    #
    # Two skill roots are scanned every cell:
    #   1. ~/.deepagents/agent/skills/ — the global registry, populated by
    #      the Docker image (core skills) and by explicit %%skill cells.
    #   2. SAGE_OUTPUT_DIR/_skills_/ — per-notebook local skills,
    #      populated by %%skill-build and freely editable by the user.
    #      SAGE_OUTPUT_DIR is <notebook-dir>/_<notebook-stem>_sage_/ by
    #      construction, so each notebook gets its own _skills_/ scope
    #      and notebooks in the same directory do NOT share skills.
    skills_dir = Path.home() / ".deepagents" / "agent" / "skills"
    skills_paths = [str(skills_dir)] if skills_dir.exists() else []
    local_skills_dir = Path(SAGE_OUTPUT_DIR) / "_skills_"
    if local_skills_dir.exists() and local_skills_dir.is_dir():
        skills_paths.append(str(local_skills_dir))

    # No checkpointer — cross-cell memory is carried via SAGE_MESSAGES.
    # Pass system_prompt at construction time so the new deepagents 0.6.8
    # delivers our rules as a proper system message rather than as a
    # preamble inside the user message. With GLM's weak instruction-following,
    # system-level rules carry materially more weight than user-message rules.
    backend_cls = KernelShellBackend if KernelShellBackend is not None else LocalShellBackend
    create_kwargs: dict = {
        "skills": skills_paths,
        "backend": backend_cls(virtual_mode=False),
        "checkpointer": None,
    }
    if system_prompt:
        create_kwargs["system_prompt"] = system_prompt
    # Merge in any MCP tools registered via %%mcp earlier in this kernel
    # session. Adds to (does not replace) the built-in skill toolset.
    _mcp_tools = _sage_mcp_all_tools()
    if _mcp_tools:
        create_kwargs["tools"] = _mcp_tools

    # Detect NRP provider by base_url. NRP's vLLM serving (any model: GLM-5,
    # minimax-m2, etc.) emits streaming chunks that langchain_openai's
    # AIMessageChunk parser handles inconsistently — sometimes fine, sometimes
    # hangs waiting indefinitely for chunks that never arrive. The non-stream
    # code path is atomic (single OpenAI response object, no per-chunk parsing)
    # and was verified to handle the same agentic conversations cleanly in
    # direct probes (2026-06-28: glm-5 returned 4 tool calls in 13s, minimax-m2
    # in 3s, with 13K-token context + 6 tools).
    _base_url = str(
        getattr(model, "openai_api_base", None)
        or getattr(model, "base_url", None)
        or ""
    )
    _provider_is_nrp = "nrp-nautilus.io" in _base_url

    # Disable streaming when EITHER condition holds:
    #  - MCP tools registered (the original glm47-parser tool-count bug from
    #    2026-06-09 — still applies)
    #  - NRP provider (the broader langchain-parser hang from 2026-06-28,
    #    applies regardless of tool count or model — affects all NRP models)
    # Both bypass the langchain_openai streaming parser entirely.
    # Trade-off: cell renders the full agent response per LLM-call boundary
    # rather than token-by-token within a call. Tool calls still display
    # individually as they fire. UX is less progressive but doesn't hang.
    if _mcp_tools or _provider_is_nrp:
        try:
            model.streaming = False
        except Exception:
            pass
        try:
            model.disable_streaming = True
        except Exception:
            pass
    agent = create_deep_agent(model, **create_kwargs)

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
        # Route through _render_markdown_with_files so `![](file.png)` and
        # `![](*.geojson)` references in mid-stream text get rendered as
        # inline images / Folium maps. Some models (notably glm-5) emit the
        # final summary BEFORE their last tool call rather than after — if
        # this function just displayed raw Markdown, the browser would see
        # `<img src="/home/jovyan/...">` and show a broken icon because
        # JupyterLab doesn't serve arbitrary filesystem paths.
        if text_buffer:
            text = _fix_glm_markdown("".join(text_buffer))
            found, _ = _render_markdown_with_files(text)
            if not found:
                display(Markdown(text))
            text_buffer.clear()
        _had_tool_after_text[0] = True
        _skip_msg_id[0] = None

    # Pass full conversation history so the agent has cross-cell memory.
    # Strip cell_id from saved messages — it's a SAGE-internal tag, not a
    # field the LLM/LangGraph expects in the message schema.
    initial_messages = (
        [{"role": m["role"], "content": m["content"]} for m in SAGE_MESSAGES]
        + [{"role": "user", "content": prompt}]
    )
    # Diagnostic: capture every chunk so we can introspect what the model
    # actually sent back. Saved to user_ns at end of cell so empty-final
    # failures are inspectable post-hoc. Includes raw AIMessage / ToolMessage
    # objects so tool_calls, additional_kwargs (reasoning content),
    # response_metadata (finish_reason), and usage_metadata are all visible.
    _diag_chunks: list = []
    async for chunk in agent.astream(
        {"messages": initial_messages},
        stream_mode="messages",
        config=config,
    ):
        if not isinstance(chunk, tuple) or len(chunk) < 2:
            continue
        message_obj, metadata = chunk[0], chunk[1]
        _diag_chunks.append((message_obj, metadata))

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
        # Newer OpenAI / Anthropic models stream content as a LIST of typed
        # parts ([{"type": "text", "text": "..."}]) rather than a plain
        # string. Normalize both shapes here so the buffer always sees a str.
        raw_content = getattr(message_obj, "content", "")
        if isinstance(raw_content, str):
            content = raw_content
        elif isinstance(raw_content, list):
            content = "".join(
                p.get("text", "") for p in raw_content
                if isinstance(p, dict) and p.get("type") == "text"
                   and isinstance(p.get("text"), str)
            )
        else:
            content = ""
        if content:
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

    # Expose the raw streamed chunks to user_ns so users can introspect what
    # the model actually returned — especially for empty-final failures where
    # the simplified SAGE_MESSAGES view doesn't show tool_calls,
    # additional_kwargs (reasoning), response_metadata (finish_reason), or
    # usage_metadata (token counts).
    try:
        get_ipython().user_ns["_SAGE_LAST_RUN_CHUNKS"] = _diag_chunks  # noqa: F821
    except Exception:
        pass

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

    # Empty-final-assistant defensive detection. Three failure modes fingerprint
    # as "final assistant text is empty"; each has different actionable advice.
    #
    #   Path A: tools ran but no synthesis.
    #     Model invoked tool(s), received the results, then chose not to
    #     summarize. Usually means the tool itself returned empty/error data
    #     (auth fail, rate limit, query matched nothing). The user should
    #     check the tool inputs shown above in the cell.
    #
    #   Path B: empty MCP registry AND no tools called AND empty content.
    #     If the prompt expects MCP tools but no %%mcp cell ran this session,
    #     register MCP first. Otherwise the model just returned empty for
    #     other reasons.
    #
    #   Path C: MCP registry populated but model called no tool AND empty
    #     content. The model failed to pick a tool from an otherwise relevant
    #     toolset. Observed consistently with NRP GLM-5 (NVFP4 quantized) on
    #     specific prompt phrasings — "call the tool X" mechanical language is
    #     a known trigger. Natural-language rephrasing or a different model
    #     provider usually recovers.
    if not final:
        from IPython.display import display, HTML
        if tool_counts:
            tools_label = ", ".join(f"{n}×{c}" if c > 1 else n
                                    for n, c in tool_counts.items())
            msg = (
                f"<b>Agent ran tool(s) but produced no summary.</b><br>"
                f"Tools called: <code>{tools_label}</code><br>"
                f"The tools executed but the model didn't summarize the "
                f"results. Likely causes: tool returned empty data or an "
                f"error in its payload, missing or invalid credentials, "
                f"server rate-limit. Inspect the tool inputs shown above "
                f"for hints."
            )
        elif _SAGE_MCP_TOOLS_BY_SERVER:
            _n_tools = sum(len(t) for t in _SAGE_MCP_TOOLS_BY_SERVER.values())
            _n_servers = len(_SAGE_MCP_TOOLS_BY_SERVER)
            _servers = ", ".join(_SAGE_MCP_TOOLS_BY_SERVER.keys())
            msg = (
                f"<b>Model returned no output and called no tools.</b><br>"
                f"MCP registry: {_n_tools} tools across {_n_servers} "
                f"server(s) (<code>{_servers}</code>) were available.<br>"
                f"The model produced an empty completion despite having "
                f"relevant tools — a known intermittent failure with "
                f"quantized model endpoints (e.g., NRP GLM-5 NVFP4) on "
                f"specific prompt phrasings. Workarounds:"
                f"<ul style='margin:4px 0 0 18px;padding:0'>"
                f"<li>Rephrase as a natural task (\"show me all Sage "
                f"nodes\"), not as a tool-call directive (\"call the "
                f"tool list_all_nodes\")</li>"
                f"<li>Switch model in <code>~/.deepagents/config.toml</code> "
                f"to OpenAI or Anthropic — they handle tool-calling more "
                f"reliably than NRP's quantized GLM-5</li>"
                f"<li>Inspect <code>SAGE_MESSAGES[-1]</code> in a Python "
                f"cell to confirm the model returned empty content</li>"
                f"</ul>"
            )
        else:
            msg = (
                f"<b>Agent returned no output.</b><br>"
                f"The model produced an empty completion and called no tools. "
                f"Try <code>%reset</code> to clear conversation memory and "
                f"re-run, or rephrase the prompt.<br>"
                f"<span style='color:#888;font-size:12px'>"
                f"(If you intended this prompt to use MCP tools, the "
                f"registry is empty — register a server first with "
                f"<code>%%mcp</code>.)"
                f"</span>"
            )
        display(HTML(
            f"<div style='color:#8a6d00; background:#fff8e1; "
            f"padding:8px 12px; border-left:3px solid #f0b400; "
            f"margin:8px 0; font-family:-apple-system,sans-serif; "
            f"font-size:13px; line-height:1.4'>⚠ {msg}</div>"
        ))

    return final, tool_counts


# ---------------------------------------------------------------------------
# API key check
# ---------------------------------------------------------------------------

def _resolve_required_api_key_env() -> str | None:
    """Determine which env var the user's config.toml requires.

    Reads ``~/.deepagents/config.toml``, finds the default provider, and
    returns that provider's ``api_key_env`` value. Returns ``None`` if the
    config is missing, malformed, or doesn't specify an api_key_env.

    Used by the pre-flight check to validate the user's environment against
    THEIR chosen provider — not a hardcoded list. Works for OpenAI, Anthropic,
    NRP, ZAI, OpenRouter, Nvidia, Mistral, Groq, or anything else the user
    configures.
    """
    try:
        import tomllib  # Python 3.11+ stdlib
    except ImportError:  # pragma: no cover — pre-3.11 fallback
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return None

    config_path = Path.home() / ".deepagents" / "config.toml"
    if not config_path.exists():
        return None
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except Exception:
        return None

    # Default model is formatted "provider:model" — extract the provider half.
    default_model = config.get("models", {}).get("default", "")
    if ":" not in default_model:
        return None
    provider_name = default_model.split(":", 1)[0]

    provider_config = (
        config.get("models", {}).get("providers", {}).get(provider_name, {})
    )
    api_key_env = provider_config.get("api_key_env")
    return api_key_env if isinstance(api_key_env, str) and api_key_env else None


def _resolve_api_key() -> str | None:
    """Return the value of the configured-provider's API key env var, or None.

    Pre-flight check before the agent starts up — gives a fast, clear error
    if the user's chosen provider doesn't have its credential set, instead
    of letting them hit a deeper create_model failure later. Does NOT enforce
    a hardcoded provider list; reads ``config.toml`` so any provider the user
    configures works.
    """
    required_env = _resolve_required_api_key_env()
    if required_env:
        return os.environ.get(required_env)
    # Config missing or malformed — fall back to checking a few common env
    # vars so the user gets a non-empty result if they happen to have any
    # standard key set. Used only when we can't read config.toml.
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NRP_API_KEY"):
        v = os.environ.get(var)
        if v:
            return v
    return None


# ---------------------------------------------------------------------------
# %%skill — install skills from local paths or GitHub URLs
# ---------------------------------------------------------------------------
# Security model (see project_sage_skill_magic.md):
#   1. Local paths: trusted (user controls the filesystem). No prompt.
#   2. GitHub URLs from allowlisted orgs: no prompt, but commit pinning enforced.
#   3. GitHub URLs from unknown orgs: batch trust prompt with SKILL.md preview.
#   4. Branch refs (main/master/develop/...) rejected — pin to a commit SHA.
#
# Two attack surfaces this guards against:
#   - Malicious helper module code that runs when the agent imports the skill.
#   - Prompt injection in SKILL.md that gets followed by the LLM.

_SAGE_SKILLS_DIR    = Path.home() / ".deepagents" / "agent" / "skills"
_SAGE_SKILL_CACHE   = Path.home() / ".deepagents" / "agent" / "_skill_cache"
_SAGE_TRUSTED_ORGS  = Path.home() / ".deepagents" / ".sage_trusted_orgs.json"
_SAGE_TRUSTED_ORGS_DEFAULT = {
    "_comment": "GitHub orgs whose skills install without a trust prompt. "
                "The commit-pinning rule still applies — branch refs are always rejected.",
    "orgs": ["klinucsd"],
}
_SAGE_BRANCH_LIKE = {"main", "master", "develop", "dev", "trunk", "head"}
_SAGE_COMMIT_SHA_RE = __import__("re").compile(r"^[0-9a-f]{40}$")
_SAGE_GITHUB_RE = __import__("re").compile(
    r"^(?:https?://)?github\.com/"
    r"(?P<org>[\w.-]+)/(?P<repo>[\w.-]+)/tree/"
    r"(?P<ref>[\w.-]+)"
    r"(?P<subpath>(?:/[\w.-]+)*)/?$"
)


def _sage_load_trusted_orgs():
    """Load (or create) the trusted-orgs allowlist. Returns a set of org names."""
    if not _SAGE_TRUSTED_ORGS.exists():
        try:
            _SAGE_TRUSTED_ORGS.parent.mkdir(parents=True, exist_ok=True)
            _SAGE_TRUSTED_ORGS.write_text(json.dumps(_SAGE_TRUSTED_ORGS_DEFAULT, indent=2))
        except Exception:
            pass
    try:
        data = json.loads(_SAGE_TRUSTED_ORGS.read_text())
        return set(data.get("orgs", []))
    except Exception:
        return set(_SAGE_TRUSTED_ORGS_DEFAULT["orgs"])


def _sage_parse_skill_entry(raw):
    """Parse one %%skill line. Returns a dict with kind in:
    {'github', 'local', 'error', 'skip'} or None for blank/comment.
    """
    line = (raw or "").strip()
    if not line or line.startswith("#"):
        return None

    m = _SAGE_GITHUB_RE.match(line)
    if m:
        subpath = m.group("subpath").lstrip("/")
        return {
            "kind": "github",
            "raw": raw,
            "org": m.group("org"),
            "repo": m.group("repo"),
            "ref": m.group("ref"),
            "subpath": subpath,
            "skill_name": Path(subpath).name if subpath else m.group("repo"),
            "url": line,
        }

    # Everything that didn't match a GitHub URL is treated as a local path.
    # Supports absolute (/abs/path), home-anchored (~/path), explicit relative
    # (./path or ../path), and bare relative (path/to/skill). Bare relative
    # paths resolve against the notebook's current working directory so a
    # shared notebook with `fire_risk_review_skills/science_rubric` works
    # without modification when the skill folder sits next to the notebook.
    try:
        path = Path(line).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
    except Exception as e:
        return {"kind": "error", "raw": raw,
                "error": f"Failed to resolve path: {e}"}
    return {
        "kind": "local",
        "raw": raw,
        "path": path,
        "skill_name": path.name,
        "url": str(path),
    }


def _sage_classify_ref(ref):
    """Returns one of: 'sha', 'tag', 'branch'.
    SHA = 40-char hex (preferred, tamper-proof).
    Branch = matches a known branch-like name (rejected).
    Tag = anything else (accepted with warning).
    """
    if not ref:
        return "branch"
    if ref.lower() in _SAGE_BRANCH_LIKE:
        return "branch"
    if _SAGE_COMMIT_SHA_RE.match(ref):
        return "sha"
    return "tag"


def _sage_read_skill_md(skill_dir):
    """Best-effort: read description from SKILL.md frontmatter or body."""
    import re as _re
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    try:
        text = skill_md.read_text(errors="replace")
    except Exception:
        return None
    # YAML frontmatter description
    m = _re.search(r'^description:\s*(.+?)$', text, _re.MULTILINE)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    # First non-blank, non-heading line after any frontmatter
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
    for ln in body.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            return ln[:400]
    return None


def _sage_list_skill_files(skill_dir, limit=20):
    """List files in a skill directory with sizes, capped at `limit`."""
    files = []
    truncated = 0
    for f in sorted(skill_dir.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        if len(files) >= limit:
            truncated += 1
            continue
        try:
            size = f.stat().st_size
        except Exception:
            size = 0
        files.append((str(f.relative_to(skill_dir)), size))
    return files, truncated


def _sage_clone_github_subtree(org, repo, ref, subpath):
    """Clone (or reuse cached) GitHub subtree at a specific ref.
    Returns (subtree_dir_or_None, error_or_None).

    Cache key is (org, repo, ref) — but the same cache may be reused across
    many different subpaths over time. When the subpath was not previously
    checked out, we extend the sparse-checkout to include it instead of
    bailing out.
    """
    import subprocess
    cache_key = f"{org}__{repo}__{ref}"
    cache_dir = _SAGE_SKILL_CACHE / cache_key

    # Branch refs advance on the remote, so a cached clone goes stale every
    # time the branch moves. Refresh the cache to the current tip before
    # reusing it. Pinned refs (commit SHA, tag) are immutable — cache reuse
    # is safe as-is.
    if cache_dir.exists() and _sage_classify_ref(ref) == "branch":
        try:
            subprocess.run(
                ["git", "-C", str(cache_dir), "fetch", "--quiet",
                 "origin", ref],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(cache_dir), "reset", "--hard",
                 "--quiet", "FETCH_HEAD"],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError:
            # Refresh failed (network, branch deleted, corrupted clone, …).
            # Don't silently serve stale content — nuke the cache and let
            # the fresh-clone path below re-create it.
            import shutil as _shutil
            _shutil.rmtree(cache_dir, ignore_errors=True)
        except FileNotFoundError:
            return None, "`git` command not found on PATH"

    if cache_dir.exists():
        target = cache_dir / subpath if subpath else cache_dir
        if target.exists():
            return target, None
        # Cache exists but this subpath wasn't checked out before. Extend
        # the sparse-checkout to add it.
        if subpath:
            try:
                subprocess.run(
                    ["git", "-C", str(cache_dir), "sparse-checkout", "add", subpath],
                    check=True, capture_output=True, text=True,
                )
            except subprocess.CalledProcessError as e:
                msg = (e.stderr or "").strip() or str(e)
                return None, f"git sparse-checkout add failed: {msg}"
            if target.exists():
                return target, None
            return None, (f"Subpath {subpath!r} not found in "
                          f"{org}/{repo}@{ref[:12]} after sparse-checkout add")
        return None, f"Cache dir exists but is empty: {cache_dir}"
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    clone_url = f"https://github.com/{org}/{repo}.git"
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout",
             "--filter=blob:none", clone_url, str(cache_dir)],
            check=True, capture_output=True, text=True,
        )
        if subpath:
            subprocess.run(
                ["git", "-C", str(cache_dir), "sparse-checkout", "set", subpath],
                check=True, capture_output=True, text=True,
            )
        subprocess.run(
            ["git", "-C", str(cache_dir), "checkout", "--quiet", ref],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        # Clean up partial clone
        try:
            import shutil
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
        except Exception:
            pass
        msg = (e.stderr or "").strip() or str(e)
        return None, f"git clone/checkout failed: {msg}"
    except FileNotFoundError:
        return None, "`git` command not found on PATH"

    target = cache_dir / subpath if subpath else cache_dir
    if not target.exists():
        return None, f"Subpath {subpath!r} not found in {org}/{repo}@{ref}"
    return target, None


def _sage_install_skill_dir(src_dir, skill_name):
    """Copy src_dir → ~/.deepagents/agent/skills/<skill_name>/ (overwriting).

    Guards against the self-destructive case where src and dest are the same
    directory (e.g. user runs `%%skill` pointing at an already-installed
    skill's path). Without this guard, `shutil.rmtree(dest)` would delete the
    source, and `copytree` would then fail.
    """
    import shutil
    _SAGE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _SAGE_SKILLS_DIR / skill_name
    src_dir = Path(src_dir)
    # No-op when src IS dest. Use samefile() to handle symlinks correctly.
    if dest.exists() and src_dir.exists():
        try:
            if dest.samefile(src_dir):
                return dest
        except OSError:
            pass
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_dir, dest,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".gitignore"))
    return dest


# ---------------------------------------------------------------------------
# Learnings.md frontmatter audit (Phase 4 of the Learnings protocol)
# ---------------------------------------------------------------------------
# Sage owns the `skill_digest` and `last_updated` fields in each
# Learnings.md frontmatter. The agent writes the body sections; Sage keeps
# the metadata accurate. When SKILL.md changes (image upgrade, %%skill
# reload), Sage updates the digest and prepends a one-line warning banner
# so the agent's next read flags pre-existing lessons as possibly stale.

# Placeholder digests written by the agent before Phase 4 shipped. The
# md5 of the empty string and the literal "auto" should be treated as
# "not yet computed" and replaced silently on the first audit (no banner).
_SAGE_LEARNINGS_PLACEHOLDER_DIGESTS = {
    "", "auto", "d41d8cd98f00b204e9800998ecf8427e",
}


def _sage_compute_skill_digest(skill_md_path) -> str:
    """Return md5 of a SKILL.md file. Empty string if unreadable."""
    import hashlib as _hashlib
    try:
        return _hashlib.md5(Path(skill_md_path).read_bytes()).hexdigest()
    except Exception:
        return ""


def _sage_audit_learnings(skill_name: str, skill_md_path=None) -> None:
    """Normalize a single skill's Learnings.md frontmatter against its SKILL.md.

    Behavior:
      - File missing → no-op (agent will create on first lesson; the next
        audit pass normalizes it).
      - Frontmatter present and digest matches → refresh `last_updated`.
      - Frontmatter missing/malformed → rebuild it (no banner).
      - Stored digest was a placeholder → fill in the real digest (no banner).
      - Stored digest mismatches a real prior digest → prepend (or refresh)
        a one-line warning banner so the agent sees that pre-recorded
        lessons may be stale.

    Idempotent. A prior banner is stripped before any new one is added, so
    repeated audits don't stack banners.
    """
    import re as _re
    from datetime import datetime as _dt, timezone as _tz

    learn_path = Path(SAGE_LEARNINGS_DIR) / skill_name / "Learnings.md"
    if not learn_path.exists():
        return

    if skill_md_path is None:
        skill_md_path = _SAGE_SKILLS_DIR / skill_name / "SKILL.md"
    skill_md_path = Path(skill_md_path)
    if not skill_md_path.exists():
        return

    current_digest = _sage_compute_skill_digest(skill_md_path)
    if not current_digest:
        return

    content = learn_path.read_text(encoding="utf-8", errors="replace")
    today = _dt.now(_tz.utc).strftime("%Y-%m-%d")

    fm_re = _re.compile(r"^---\s*\n(.*?)\n---\s*\n", _re.DOTALL)
    m = fm_re.match(content)
    if m:
        fm_text = m.group(1)
        body = content[m.end():]
    else:
        fm_text = ""
        body = content

    # Parse frontmatter (simple `key: value` per line — no nested YAML).
    fm = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()

    stored_digest = fm.get("skill_digest", "")
    real_change = (stored_digest not in _SAGE_LEARNINGS_PLACEHOLDER_DIGESTS
                   and stored_digest != current_digest)

    # Strip any prior banner before (optionally) re-adding a fresh one.
    banner_re = _re.compile(
        r"^> ⚠️ Skill `[^`]+` was updated on \d{4}-\d{2}-\d{2}\.[^\n]*\n+",
        _re.MULTILINE,
    )
    body = banner_re.sub("", body, count=1).lstrip("\n")

    if real_change:
        banner = (
            f"> ⚠️ Skill `{skill_name}` was updated on {today}. "
            f"Lessons recorded before this date may be stale — verify "
            f"before applying.\n\n"
        )
        body = banner + body

    fm["skill"] = skill_name
    fm["skill_digest"] = current_digest
    fm["last_updated"] = today

    new_fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    new_content = f"---\n{new_fm_lines}\n---\n\n{body.lstrip()}"
    if new_content != content:
        learn_path.write_text(new_content, encoding="utf-8")


def _sage_audit_all_learnings() -> None:
    """Walk SAGE_LEARNINGS_DIR and audit every skill's Learnings.md.
    Called at kernel startup so digests reflect any skill changes that
    happened while the kernel was down (image upgrade, %%skill reload
    in a previous session, etc.)."""
    root = Path(SAGE_LEARNINGS_DIR)
    if not root.exists():
        return
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if (child / "Learnings.md").exists():
            try:
                _sage_audit_learnings(child.name)
            except Exception:
                pass  # one corrupt file must not break startup


# Run the startup audit pass.
try:
    _sage_audit_all_learnings()
except Exception:
    pass


# ---------------------------------------------------------------------------
# MCP config helpers (used by %%mcp magic)
# ---------------------------------------------------------------------------

def _sage_interpolate_mcp_env(s):
    """Replace $VARNAME or ${VARNAME} in s with values from os.environ.

    On Colab, falls back to google.colab.userdata (Colab Secrets) when a
    variable isn't in os.environ. Found values are cached into os.environ
    so subsequent interpolations don't re-trigger Colab's secret-access
    dialog.

    Raises ValueError if a referenced variable is unset, so the user gets a
    clear error rather than a silently-empty token in the MCP request.
    """
    if not isinstance(s, str):
        return s
    import re as _re

    def _repl(m):
        var = m.group(1) or m.group(2)
        val = os.environ.get(var)
        on_colab = False
        if val is None:
            try:
                from google.colab import userdata  # type: ignore[import-not-found]
                on_colab = True
                val = userdata.get(var)
                if val:
                    os.environ[var] = val
            except ImportError:
                pass
            except Exception:
                # userdata.get raises SecretNotFoundError / NotebookAccessError
                # when the secret doesn't exist or notebook access is off.
                val = None
        if val is None:
            if on_colab:
                raise ValueError(
                    f"Environment variable ${var} is referenced in %%mcp config but not set.\n"
                    f"Open the 🔑 sidebar in Colab → 'Add new secret' → "
                    f"Name: {var}, Value: your secret, toggle 'Notebook access' "
                    f"on, then re-run this cell."
                )
            raise ValueError(
                f"Environment variable ${var} is referenced in %%mcp config but not set"
            )
        return val

    return _re.sub(r"\$\{(\w+)\}|\$(\w+)", _repl, s)


def _sage_normalize_mcp_config(raw):
    """Normalize Claude Desktop / VS Code / Gemini MCP config to MultiServerMCPClient format.

    Accepts:
      - {"mcpServers": {name: cfg}}    (Claude Desktop wrapper; optional)
      - {name: cfg}                    (bare, langchain-style)

    Per-server cfg can use:
      - {"url": "..."}              — http transport (preferred field name)
      - {"httpUrl": "..."}          — Gemini's alias for url
      - {"command": "...", "args": [...], "env": {...}}  — stdio transport
      - "type" or "transport"       — transport-name override (synonyms)
      - "headers"                   — optional headers for http transports
    """
    if not isinstance(raw, dict):
        raise ValueError("MCP config must be a JSON object")
    if len(raw) == 1 and "mcpServers" in raw and isinstance(raw["mcpServers"], dict):
        raw = raw["mcpServers"]

    normalized = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Server '{name}' config must be a JSON object")

        url = cfg.get("url") or cfg.get("httpUrl")
        command = cfg.get("command")
        transport = cfg.get("transport") or cfg.get("type")

        if transport in ("http", "https", "streamable-http"):
            transport = "streamable_http"

        if not transport:
            if url:
                transport = "streamable_http"
            elif command:
                transport = "stdio"
            else:
                raise ValueError(
                    f"Server '{name}' must specify url, httpUrl, or command"
                )

        if transport == "streamable_http":
            if not url:
                raise ValueError(
                    f"Server '{name}' uses http transport but no url given"
                )
            entry = {
                "transport": "streamable_http",
                "url": _sage_interpolate_mcp_env(url),
            }
            hdrs = cfg.get("headers")
            if hdrs:
                entry["headers"] = {
                    k: _sage_interpolate_mcp_env(v) for k, v in hdrs.items()
                }
        elif transport == "sse":
            if not url:
                raise ValueError(
                    f"Server '{name}' uses sse transport but no url given"
                )
            entry = {"transport": "sse", "url": _sage_interpolate_mcp_env(url)}
        elif transport == "stdio":
            if not command:
                raise ValueError(
                    f"Server '{name}' uses stdio transport but no command given"
                )
            entry = {
                "transport": "stdio",
                "command": _sage_interpolate_mcp_env(command),
            }
            args = cfg.get("args")
            if args:
                entry["args"] = [_sage_interpolate_mcp_env(a) for a in args]
            env = cfg.get("env")
            if env:
                entry["env"] = {
                    k: _sage_interpolate_mcp_env(v) for k, v in env.items()
                }
        else:
            raise ValueError(
                f"Server '{name}' has unsupported transport: {transport}"
            )

        normalized[name] = entry

    return normalized


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
            required_env = _resolve_required_api_key_env()
            if required_env:
                print(
                    f"Error: {required_env} is not set in the environment.\n"
                    f"Your ~/.deepagents/config.toml requires it for the "
                    f"default provider. Set it via .env in CWD, the shell "
                    f"environment, or (on NRP) "
                    f"/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env"
                )
            else:
                print(
                    "Error: no LLM provider API key found and no default "
                    "model configured in ~/.deepagents/config.toml.\n"
                    "Either: (a) write a config.toml with a default model "
                    "and its api_key_env; or (b) set one of the common "
                    "env vars: OPENAI_API_KEY, ANTHROPIC_API_KEY, NRP_API_KEY.\n"
                    "Sources checked: env vars, .env in CWD, and (on NRP) "
                    "/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.env"
                )
            return

        # Orphan-file cleanup for any %%ask cell that didn't finish normally.
        # Uses the persisted .sage_cell_runs.json so kernel restarts don't
        # lose the "killed cell" signal. For each record with finished_at=None,
        # delete files in its window that aren't in any cell-output registry,
        # then remove the record.
        import time as _t_mod
        _cell_entry_now = _t_mod.time()
        _runs = _load_cell_runs()
        _all_deleted: list[str] = []
        _current_cell_id_for_runs = _get_cell_id()
        # Walk every cell's prior run record (INCLUDING this cell's own, in
        # case it was rerun after a kill). For each one whose finished_at
        # is null, the cell was killed (interrupt / kernel crash / cell
        # deleted) and any orphan files in its window should be removed.
        # Cells that finished normally have finished_at set and are skipped.
        for _cid in list(_runs.keys()):
            _rec = _runs.get(_cid) or {}
            _start = _rec.get("started_at")
            _finished = _rec.get("finished_at")
            if _start is None or _finished is not None:
                continue
            # Dead cell — clean orphans in its window
            _all_deleted.extend(
                _orphan_cleanup_for_dead_cell(_start, _cell_entry_now)
            )
            # Remove the dead record so we don't re-clean on a future cell
            del _runs[_cid]
        if _all_deleted:
            _display_orphan_cleanup_warning(_all_deleted)
        # Record the current cell as in-flight
        if _current_cell_id_for_runs:
            _runs[_current_cell_id_for_runs] = {
                "started_at": _cell_entry_now,
                "finished_at": None,
            }
        _save_cell_runs(_runs)

        # Cross-cell conversation history management (must run before the agent):
        #   1. On a fresh kernel (SAGE_MESSAGES empty), restore from notebook —
        #      includes only cells *before* the current cell in document order.
        #   2. On every %ask: if the current cell already has entries in
        #      SAGE_MESSAGES (rerun), drop them along with everything after.
        # Both paths use cell_id tags. SAGE_MESSAGES is therefore always a
        # causally-correct prefix relative to the cell that's about to run.
        global SAGE_MESSAGES
        _current_cell_id = _get_cell_id()
        if not SAGE_MESSAGES:
            _restored = _reconstruct_messages_from_notebook(
                stop_at_cell_id=_current_cell_id
            )
            if _restored:
                SAGE_MESSAGES = _restored
        else:
            SAGE_MESSAGES = _truncate_messages_for_rerun(SAGE_MESSAGES, _current_cell_id)

        # True rerun: delete this cell's previous output files, kernel variables,
        # and registry entries — MUST happen BEFORE building the system prompt below,
        # because _kernel_vars_registry_prompt() reads from user_ns and would otherwise
        # show the stale variables (and the agent would act as if the user had
        # already made a selection on this re-run).
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

            # Same idea for the kernel-variables registry.
            _kvar_reg = _load_kernel_vars_registry()
            for _vname in list(_kvar_reg.get(cell_id, {}).keys()):
                try:
                    get_ipython().user_ns.pop(_vname, None)  # noqa: F821
                except Exception:
                    pass
            if cell_id in _kvar_reg:
                del _kvar_reg[cell_id]
                _save_kernel_vars_registry(_kvar_reg)

        # NOTE: orphan-file cleanup was previously here. It walked the output
        # directory and deleted any file not in any cell's registry, which
        # incorrectly removed files the user had copied in (since user files
        # have no registry entry). The replacement runs at cell entry above
        # and is gated on "previous cell didn't finish normally" — see
        # _orphan_cleanup_for_dead_cell().

        # Refresh the set of skills with Learnings.md so the system prompt
        # below reflects any Learnings.md created (by the agent or out of
        # band) since the last cell. The agent uses this set to skip the
        # `read_file` round-trip for skills that have no Learnings.md yet,
        # eliminating 5-10 s of latency per skipped skill.
        global SAGE_LEARNINGS_SKILLS
        SAGE_LEARNINGS_SKILLS = _sage_build_learnings_skills_set()
        try:
            get_ipython().user_ns["SAGE_LEARNINGS_SKILLS"] = SAGE_LEARNINGS_SKILLS  # noqa: F821
        except Exception:
            pass

        # Inject output directory and thinking requirement into prompt
        import sys as _sys
        system_prompt_text = (
            f"The Python interpreter is at: {_sys.executable} — always use this exact path "
            f"when running Python scripts (do not use 'python' or 'python3').\n"
            f"Use {SAGE_OUTPUT_DIR} as your working directory for ALL files — "
            f"including intermediate files, scripts, and final outputs (GeoJSON, CSV, PNG). "
            f"Do not write any files to /tmp directly.\n"
            f"FILE ACCESS RULE — you may only read or search files in two locations: "
            f"(1) {SAGE_OUTPUT_DIR} — your working directory for this notebook; "
            f"(2) {_SAGE_SKILLS_DIR}/ — read-only access to skill files "
            f"(SKILL.md, helper modules, and any subprocess-runner scripts that skills bundle). "
            f"Never read, list, search, or browse any other directory on the filesystem "
            f"(e.g. /home, /data, /tmp, /root, or any path outside these two). "
            f"All input data must come from external APIs or services, not from the local filesystem.\n"
            f"NEVER MODIFY SKILL FILES — files under {_SAGE_SKILLS_DIR}/ are system files. "
            f"Do not use write_file, edit_file, or any tool that modifies them. If a helper module like "
            f"sage_dropdown.py or sage_bbox_map.py appears broken (raises an error you can't work around by "
            f"changing your own script), STOP and report the issue to the user — describe the error and the "
            f"bug you see in the helper. Do not try to patch the helper yourself.\n"
            f"SKILL CONSULTATION RULE — before writing ANY code, check the available skills and consult any "
            f"that match the user's request. This applies even for tasks you think you know how to do — "
            f"the skill may use community-standard libraries, encode pitfalls, or define output conventions "
            f"that your prior knowledge will miss. Specifically, ALWAYS consult an available skill when the "
            f"user's request involves: LiDAR / point clouds / .laz / .las / .copc files / EPT endpoints; "
            f"DEM / DSM / CHM / hillshade / canopy metrics (PAD, PAI, FHD, canopy cover); "
            f"earthquakes / GNSS / GPS displacement; flood depth / impact analysis; "
            f"satellite fire detections / fuel moisture; vegetation treatments; "
            f"any specialized scientific data format or domain workflow. "
            f"If no skill matches, proceed with your own approach. But never write a multi-step domain "
            f"script (DEM rasterization, CHM calculation, EPT download, etc.) without first reading any "
            f"skill SKILL.md whose description matches the task — the skill exists precisely to prevent "
            f"reinvention.\n"
            f"KERNEL VARIABLE NAME RULE — when `EXISTING KERNEL VARIABLES` lists a variable, your code "
            f"MUST use that exact name with `globals().get(...)`. Do NOT hardcode conventional names "
            f"like `USER_BBOX` if the registry shows a different name (e.g., `CONUS_BBOX`, `STORM_BBOX`). "
            f"The cell that set the variable picked the name; you must use that name verbatim.\n"
            f"VARIABLE NONE RULE — if `EXISTING KERNEL VARIABLES` shows a variable with value `= None`, "
            f"the user has not yet performed the interactive step that sets it (e.g., drawing a bbox, "
            f"picking from a dropdown, completing a manual choice). Tell the user clearly what action "
            f"they need to take; do NOT search files or registries trying to find the value elsewhere — "
            f"it isn't there.\n"
            f"NEVER FABRICATE KERNEL VALUES — when `EXISTING KERNEL VARIABLES` lists a variable with a "
            f"non-None value, you MUST copy that exact literal value directly into your subprocess "
            f"script. This is non-negotiable: the value above IS the answer. Do NOT write fallback "
            f"logic such as `if bbox is None: bbox = (-105.0, ...)` or `bbox = bbox or DEFAULT_BBOX` "
            f"or any other invented default. Do NOT attempt to read the value from "
            f"`.sage_kernel_vars.json` or any other file — that file holds metadata only, not values. "
            f"COPY VERBATIM, INCLUDING ALL DECIMAL DIGITS — `(-121.89949, 43.166273, ...)` must be "
            f"copied as `(-121.89949, 43.166273, ...)`, NEVER as `(-121.90, 43.17, ...)`. Do not "
            f"round, truncate, reformat, or simplify the literal — rounding a bbox by 0.01° shifts "
            f"the area by ~1 km, which is a silent data-correctness failure. "
            f"If a variable you need is missing from `EXISTING KERNEL VARIABLES`, STOP and tell the "
            f"user to run the cell that sets it; never substitute a guess or a 'reasonable default'. "
            f"Inventing or rounding a value for a user-set variable will produce results for the "
            f"WRONG location/dataset/selection and is one of the worst possible failure modes — the "
            f"user will believe they got an answer for their input when they did not.\n"
            f"DO NOT CLOBBER KERNEL VARIABLES — before defining any helper that initializes a kernel "
            f"variable (e.g., `caller_ns[var_name] = None`), check `EXISTING KERNEL VARIABLES`. If a "
            f"variable with that name already exists with a non-None value, do NOT reset it — that "
            f"would silently destroy state the user already produced (a drawn bbox, a picked dataset, "
            f"a long-running download). If you need to extend or wrap an existing widget, leave the "
            f"variable alone or use a different name.\n"
            f"INTERACTIVE WIDGET RULE — show_bbox_map() and show_dropdown() render widgets the user interacts "
            f"with. The user's interaction happens AFTER your script returns. Do NOT, in the same script, "
            f"read the kernel variable the user must set — you will see None or the stale / default value. "
            f"For linked widgets where one feeds another (bbox map → filtered dropdown, dropdown → dependent "
            f"dropdown), render both with sage-dropdown's reactive mode (`items_fn` + `observes`); the "
            f"dependent widget auto-refreshes when the user interacts with the parent. For other cases, "
            f"just render the widget — the user's selection lands in the kernel-variables registry and will "
            f"be visible to subsequent requests automatically.\n"
            f"NEVER use read_file on image or other binary files. This includes PNG, JPG, JPEG, "
            f"GIF, BMP, TIFF, GeoTIFF, PDF, SVG, and any other non-text format. The model is "
            f"text-only — opening a binary file dumps unreadable bytes into context, wastes tokens, "
            f"and risks crashing the run. If you saved a chart or map to a PNG, you already know "
            f"what it contains; embed it in the report with `![Caption](path.png)` and move on. "
            f"To inspect tabular results, read the CSV/JSON sidecar instead, never the rendered image.\n\n"
            f"NARRATION RULE — every tool call must be preceded by one short sentence "
            f"(≤ 25 words) explaining the intent of that specific call. Do NOT chain "
            f"multiple tool calls together without text between them — the reader should "
            f"never see two adjacent tool calls without a one-line explanation between them. "
            f"Examples of correct narration:\n"
            f"  • Before read_file: \"Reading the SKILL.md to find the search endpoint.\"\n"
            f"  • Before write_file: \"Writing the GPS-station distance script.\"\n"
            f"  • Before execute: \"Running the script to compute distances.\"\n"
            f"  • After a read/execute that returns interesting data: \"Found 12 stations "
            f"    within 100 miles — three under 20 miles.\"\n"
            f"Tool calls without narration look like a black-box process and reduce trust "
            f"in the result; this rule applies even when the next call seems obvious to you. "
            f"For mechanical follow-up calls (e.g. fixing a typo in a file you just wrote), "
            f"one short sentence like \"Fixing the column name.\" is enough — but it must be "
            f"present.\n\n"
            f"When writing your final report, organize it as well-structured markdown. "
            f"IMPORTANT — markdown bold syntax: always write **Label**: value with no space "
            f"inside the ** markers and always a space after the closing ** before any text or punctuation. "
            f"Correct: **Total schools**: 183. Wrong: ** Total schools**: 183 or **Total schools**:183.\n"
            f"IMPORTANT — markdown table syntax: always place EACH TABLE ROW on its own separate line. "
            f"Never write multiple table rows on a single line.\n"
            f"IMPORTANT — when generating charts or visualizations with matplotlib, always use "
            f"figsize=(14, 8) or larger, dpi=150, and call plt.tight_layout() before saving. "
            f"This ensures images are large enough to read clearly in the notebook.\n"
            f"PIE CHART RULE — when a pie chart has more than 5 categories, suppress inline "
            f"labels and percentages for slices below 3% (set their label to '' and skip "
            f"autopct for them) and place all category labels in a side legend instead "
            f"(`ax.legend(labels, loc='center left', bbox_to_anchor=(1.0, 0.5))`). "
            f"Prefer a horizontal bar chart over a pie chart when there are more than 7 categories.\n"
            f"COLOR-BY-CLASS RULE — on any map or chart that shows multiple entities, "
            f"color encodes class membership, NOT individual identity. This applies EVEN WHEN "
            f"the entities are the FOCAL subject of the query (e.g., 'find wildfires near each "
            f"powerplant' — the powerplants still all get ONE color, distinguished by their "
            f"names on the marker, not by hue). Per-entity coloring is reserved exclusively "
            f"for cases where the user explicitly asks for it (e.g., 'color each region "
            f"differently').\n"
            f"CORRECT:\n"
            f"  - 12 powerplants → 1 color (e.g., navy); labels say each name\n"
            f"  - 30 wildfires → 1 color (e.g., orange); labels say each fire id\n"
            f"  - 'find wildfires near each powerplant' → exactly 2 colors total: one for ALL "
            f"powerplants, one for ALL wildfires. Distinguish individuals via labels, popups, "
            f"or marker shapes — NEVER hue.\n"
            f"WRONG:\n"
            f"  - 12 powerplants each with a different color (even if they're the focal entity "
            f"the user is iterating on)\n"
            f"  - any categorical field with >5 distinct values getting a palette that maps "
            f"one color per value — use ONE color for the whole layer\n"
            f"A legend with more than ~5–7 colors is a smell — almost always per-entity "
            f"coloring crept in. Regroup by class first.\n"
            f"REGISTRY KEY RULE — when all values of a field share the same color (because they "
            f"all belong to one class), do NOT enumerate every value as its own entry in "
            f"`.sage_colors.json`. Register ONE entry keyed by the class name with a single "
            f"value→color mapping. The legend renders one row per registered entry, so 30 entries "
            f"all sharing one navy color produces 30 redundant legend rows.\n"
            f"CORRECT — one registry entry, one legend row:\n"
            f"  \"layer_type\": {{\"title\": \"Major Rivers\", \"palette\": {{\"river\": \"#1f78b4\"}}}}\n"
            f"WRONG — one entry per individual, all sharing one color:\n"
            f"  \"river_name\": {{\"title\": \"Major Rivers\", \"palette\": {{\"Mississippi\": \"#1f78b4\", "
            f"\"Colorado\": \"#1f78b4\", \"Ohio\": \"#1f78b4\", ...}}}}\n"
            f"CHART COLOR RULE — when a chart (bar, pie, scatter, etc.) shares a categorical "
            f"field with a map layer that has a `.colormap.json` sidecar, the chart MUST use "
            f"the SAME palette colors as the map, keyed by the same field values. Load the "
            f"colormap.json and apply each category's color to the corresponding chart element "
            f"so a reader can cross-reference categories between map and chart at a glance. "
            f"Do NOT use matplotlib's default single color or default palette when a "
            f"colormap.json is available for the same data.\n"
            f"Example: if `fires.colormap.json` has palette "
            f"{{\"FOREST\": \"#006837\", \"SHRB_CHAP\": \"#a1d99b\"}}, a bar chart of acres "
            f"by vegetation type must color the FOREST bar #006837 and the SHRB_CHAP bar "
            f"#a1d99b — pass `color=[palette[v] for v in df['vegetation_type']]` to "
            f"`ax.bar()` / `ax.barh()` (or `colors=` for `ax.pie()`).\n"
            f"IMPORTANT — when a skill provides a complete script example marked "
            f"'copy this script verbatim', you MUST copy that script exactly and only fill in "
            f"the variable values. Do not rewrite or replace any part of the script logic.\n"
            f"TOOL ARGUMENT RULE — when calling any tool (built-in, skill, or MCP), if an "
            f"argument is optional and you don't have a real value for it, OMIT the argument "
            f"entirely. Never pass the literal strings \"null\", \"None\", \"undefined\", or "
            f"\"N/A\" as placeholders — the tool will treat them as real values and the call "
            f"will fail or return wrong data. Same for empty strings when the schema permits "
            f"omission. If you're unsure whether an argument is required, read the tool's "
            f"input schema before guessing.\n"
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
            f"connects to them. "
            f"For example, if the user asked to find the mechanical fuel reduction treatments, do NOT "
            f"include the prescribed fire activities GeoJSON from the previous cell because mechanical "
            f"fuel reduction has no relation to prescribed fire activities.\n"
            f"SUBSET RULE — if the GeoJSON generated by the current request is a subset of a GeoJSON "
            f"from a previous cell, do NOT include the previous (broader) GeoJSON — the new layer "
            f"already covers the relevant features. For example, if the user asks to find all CalTrans "
            f"activities and a previous cell produced a GeoJSON of all activities, include only the "
            f"CalTrans GeoJSON, not the all-activities GeoJSON.\n"
            + (_color_registry_prompt())
            + (_kernel_vars_registry_prompt())
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
            f"PYTHON EXECUTION RULE — when running a Python script, ALWAYS use the form "
            f"`python /abs/path/to/script.py` with no shell prefix. Do NOT use "
            f"`cd <dir> && python script.py` or any compound shell command — those bypass "
            f"Sage's in-kernel execution and variables defined at the script's top level "
            f"won't persist into the kernel namespace. If the script needs a specific "
            f"working directory, set it inside the script via `os.chdir(...)` rather than "
            f"prefixing the invocation with `cd`. All script stdout/stderr (including any "
            f"`print(..., file=sys.__stdout__)` calls) is captured and hidden from the cell "
            f"— it is only visible to you in the tool result. Do not try to stream progress "
            f"to the user via stdout; the cell stays clean while the script runs.\n\n"
            f"LEARNINGS PROTOCOL — Sage maintains a per-skill memory of "
            f"error→fix patterns the agent has learned across past runs. The "
            f"single most-important rule of this protocol, and the one agents "
            f"skip most often, is the WRITE rule below. Read it first.\n"
            f"\n"
            f"### ★ WRITE RULE — DO THIS BEFORE ANYTHING ELSE IN A TASK THAT HIT AN ERROR ★\n"
            f"If, during this task, you call `edit_file` (or `write_file` "
            f"replacing a previous script) and then re-run the same script "
            f"via `execute` — i.e. ANY edit-and-retry cycle — you MUST "
            f"record the lesson in the corresponding skill's Learnings.md. "
            f"You MUST do this BEFORE continuing the task, BEFORE writing "
            f"any further script, and BEFORE composing the final report. "
            f"Skipping this step is the single most common failure mode of "
            f"this protocol — the agent fixes the error, moves on, and the "
            f"next session repeats the same mistake because the lesson was "
            f"never persisted. Do not be that agent.\n"
            f"\n"
            f"Files live at: {SAGE_LEARNINGS_DIR}/<skill_name>/Learnings.md\n"
            f"`<skill_name>` is the directory name of the skill whose SKILL.md "
            f"you just read.\n"
            f"\n"
            f"WRITE the lesson WHEN ALL of these hold:\n"
            f"  1. The script needed at least one edit-and-retry (the first "
            f"`execute` produced an error, OR you called `edit_file` and "
            f"re-ran). A clean one-shot execute means no lesson is needed.\n"
            f"  2. The fix is non-obvious from SKILL.md alone — a reader of "
            f"just the skill would not see it.\n"
            f"  3. The lesson is not already in the on-disk Learnings.md "
            f"file. Your conversation memory does NOT count as 'already "
            f"recorded' — the next session starts with no conversation, "
            f"only the file. If the file has a similar entry, EDIT it to "
            f"clarify rather than adding a duplicate.\n"
            f"DO NOT append for: routine one-shot successes, confirmations "
            f"of patterns already in SKILL.md, per-session journaling, open "
            f"questions, future ideas. If a line in Learnings.md does not "
            f"change what code gets written on the next run, it does not "
            f"belong in the file.\n"
            f"\n"
            f"FILE FORMAT — exactly two body sections:\n"
            f"  ## What Doesn't Work\n"
            f"  - **<short title>**\n"
            f"    <2-3 lines: the pattern, why it fails, what to do instead>\n"
            f"  ## Recurring Errors & Fixes\n"
            f"  - **<error message or short title>**\n"
            f"    Cause: <one line>\n"
            f"    Fix: <one line — minimal code if needed>\n"
            f"YAML frontmatter (`skill`, `skill_digest`, `last_updated`) is "
            f"maintained by Sage; do not edit it. No per-entry dates. No "
            f"confidence ratings. Keep entries terse — 2-3 lines, not "
            f"paragraphs.\n"
            f"\n"
            f"### READ RULE — short-circuited by the list below ###\n"
            f"SKILLS WITH AN EXISTING Learnings.md (as of this cell's start): "
            f"{sorted(SAGE_LEARNINGS_SKILLS) if SAGE_LEARNINGS_SKILLS else '(none)'}\n"
            f"When you read a skill's SKILL.md, check whether `<skill_name>` "
            f"is in the list above. If yes, immediately also read its "
            f"Learnings.md (`read_file`); if no, SKIP the `read_file` — the "
            f"file does not exist yet and the attempt is a wasted round "
            f"trip. When Learnings.md IS present, apply every fix in "
            f"'Recurring Errors & Fixes' pre-emptively and avoid every "
            f"pattern in 'What Doesn't Work'.\n"
            f"The list above SHORT-CIRCUITS READS ONLY. It has NOTHING to "
            f"do with the WRITE RULE above. A skill being absent from the "
            f"list is exactly when you would CREATE its Learnings.md if "
            f"this task discovers a lesson worth recording.\n"
            f"\n"
            f"### ★ CLOSING SELF-AUDIT — BEFORE YOUR FINAL REPORT ★\n"
            f"Before you write your final natural-language report to the "
            f"user, AUDIT your own tool-call history in this task. For each "
            f"`edit_file`/`write_file` that was followed by a re-`execute` "
            f"of the same script, confirm that you have a corresponding "
            f"`write_file` or `edit_file` on a Learnings.md. If any "
            f"edit-and-retry cycle in your trace has no corresponding "
            f"Learnings.md update, record it NOW — this is your last "
            f"chance before the cell ends. Only then proceed to the "
            f"final report. This audit exists because skipping the WRITE "
            f"RULE mid-task is the single most common protocol failure; "
            f"the audit is the safety net.\n"
            f"\n"
            f"PACKAGE INSTALL RULE — `/opt/conda/` is read-only for the kernel user, so any "
            f"`pip install` without `--user` fails with `Permission denied` and floods the cell. "
            f"Sage provides a helper that does the right thing silently:\n"
            f"  `_sage_pip_install('pandas')`            # one package\n"
            f"  `_sage_pip_install('pandas', 'astropy')` # several at once\n"
            f"The helper (a) no-ops if every package is already importable, (b) otherwise runs "
            f"`pip install --user --quiet --no-warn-script-location ...` with stderr suppressed, "
            f"and (c) sweeps `~*` artifacts from site-packages afterwards. Always use it. "
            f"DO NOT call `pip install` directly via `subprocess`, `os.system`, or `!pip` — "
            f"those paths reintroduce the missing-flags / stderr-flood problems. Shell "
            f"redirections like `2>/dev/null` are shell syntax and DO NOT work inside "
            f"`subprocess.run([...])` argument lists; use the helper instead.\n"
            f"The helper is already in the kernel namespace — no import needed. After it "
            f"returns, the new package is importable in the same kernel immediately.\n"
            f"Treat `Ignoring invalid distribution ~...` warnings as harmless noise from NRP's "
            f"pod-startup operations — they are NOT install failures. Verify install success by "
            f"importing the package (`import <pkg>`); if that works the install succeeded.\n\n"
            f"CREDENTIAL ISOLATION RULE — API keys, tokens, and passwords are bound to EXACTLY ONE "
            f"service. NEVER use a credential meant for service A to authenticate against service B, "
            f"even as a 'let me try' fallback. Sending the wrong key to the wrong URL leaks the "
            f"secret to that site's transport logs, even if auth correctly fails. Specific forbidden "
            f"patterns:\n"
            f"  • `NRP_API_KEY` (or any `*_API_KEY` for an LLM provider like ANTHROPIC, OPENAI, GOOGLE) "
            f"→ any non-LLM endpoint\n"
            f"  • `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` → any host outside `*.earthdata.nasa.gov`\n"
            f"  • `OPENTOPOGRAPHY_API_KEY` → any host outside `*.opentopography.org`\n"
            f"  • ANY credential → ANY URL it was not explicitly provisioned for\n"
            f"Do NOT print, echo, or otherwise reveal credential values in tool output, scripts, or "
            f"final reports.\n"
            f"On a missing service-specific credential, you have two options:\n"
            f"  (a) STOP and tell the user which env var to set and where to obtain the key "
            f"(e.g. `portal.opentopography.org` → My Account → Request API key).\n"
            f"  (b) Use an alternative no-auth source for the SAME data product if one exists. "
            f"You MUST include a clearly-headed disclosure section in your final report, for example:\n"
            f"      ### Data source substitution\n"
            f"      Requested OPENTOPOGRAPHY_API_KEY was not set. Downloaded COP30 from AWS Open "
            f"Data (`copernicus-dem-30m.s3.amazonaws.com`) instead. Caveats: <any differences from "
            f"the original source>\n"
            f"Silent substitution is forbidden — the user must always be told which source was "
            f"actually used.\n\n"
            f"SKILL TRANSLATION RULE — skills are framework-portable and use vanilla "
            f"Python idioms in their examples. Translate those idioms to Sage-native "
            f"calls:\n"
            f"  • `pip install <pkg>` (anywhere in a skill body) → "
            f"`_sage_pip_install('<pkg>')` per the PACKAGE INSTALL RULE above.\n"
            f"  • Example output paths like `Path(\"foo.csv\")` → write under "
            f"SAGE_OUTPUT_DIR per the working-directory rule above.\n"
            f"  • Placeholder skill paths like `/path/to/skills/<name>` or "
            f"`/absolute/path/to/this/skill/directory` → substitute the actual path "
            f"`os.path.expanduser(\"~/.deepagents/agent/skills/<skill-name>/\")`.\n"
            f"The system prompt's rules override any specific command, path, or "
            f"env-file location shown in a skill body. The skill's literal text is "
            f"informational; the rules here are authoritative.\n\n"
            f"CRS RULE — whenever you combine, join, overlay, clip, or sample two or more spatial "
            f"datasets (raster + vector, vector + vector, raster + raster), ALWAYS check and align "
            f"their coordinate reference systems before the operation. Misaligned CRSs produce maps "
            f"where features are visibly offset from the basemap (a river shifted off its valley, "
            f"points floating in the ocean, a raster in the wrong hemisphere). Required steps:\n"
            f"  1. Print each dataset's CRS before combining: `print(gdf.crs)`, `print(rio_dataset.crs)`.\n"
            f"  2. If they differ, reproject ONE of them to match the other using "
            f"`gdf.to_crs(other.crs)` for vectors or `rasterio.warp.reproject(...)` for rasters.\n"
            f"  3. For any output displayed on a Folium/ipyleaflet map, the final layer must be in "
            f"EPSG:4326 (lat/lon) — Leaflet requires WGS84. Reproject to 4326 as the last step before writing GeoJSON.\n"
            f"  4. For sampling raster values at vector points (e.g., flood depth at building footprints), "
            f"reproject the vector geometry into the raster's CRS before sampling, not the other way around.\n"
            f"Never assume two datasets share a CRS just because they describe the same geographic area. "
            f"USGS services often return Web Mercator (EPSG:3857), FEMA NSI returns WGS84 (EPSG:4326), "
            f"EPT point clouds are commonly EPSG:3857, state-plane data can be any of hundreds of codes."
        )

        # Snapshot output folder before run
        before = _snapshot(SAGE_OUTPUT_DIR)

        # Reset the "widget rendered a map" flag. Widget skills (sage-bbox-map,
        # potentially others) set this to True when they render their own live
        # map; the auto-Folium fallback below honors it and skips rendering a
        # static map of any GeoJSON written by the cell — preventing duplicate
        # maps when the agent writes intermediate GeoJSON files (e.g. coverage
        # catalog) alongside an interactive widget.
        try:
            ip.user_ns["_sage_widget_map_rendered"] = False  # noqa: F821
        except Exception:
            pass

        # Run agent with streaming tool display; get back final report + tool counts.
        # Use run_until_complete on the existing loop (patched by nest_asyncio)
        # instead of asyncio.run(), which conflicts with Python 3.13's task cleanup.
        import time as _time
        from IPython.display import display, HTML

        # Out-of-order detector: if no MCP tools are registered but a nearby
        # notebook has a %%mcp cell, the user likely jumped to a later cell
        # after a kernel restart without re-running the %%mcp cell first.
        if not _SAGE_MCP_TOOLS_BY_SERVER:
            _nb_with_mcp = _sage_find_notebook_with_mcp_cell()
            if _nb_with_mcp:
                display(HTML(
                    f"<div style='color:#8a6d00; background:#fff8e1; "
                    f"padding:6px 10px; border-left:3px solid #f0b400; "
                    f"margin-bottom:8px; font-family:-apple-system,sans-serif; "
                    f"font-size:13px'>"
                    f"⚠ MCP registry is empty, but <code>{_nb_with_mcp}</code> "
                    f"contains a <code>%%mcp</code> cell. If this question relies "
                    f"on MCP tools, run that cell first — kernel restart wipes "
                    f"the registry."
                    f"</div>"
                ))

        _t_start = _time.time()
        _loop = asyncio.get_event_loop()
        _orig_exc_handler = _loop.get_exception_handler()
        def _suppress_context_errors(loop, context):
            msg = str(context.get("message", "")) + str(context.get("exception", ""))
            if "cannot enter context" in msg:
                return
            if _orig_exc_handler is not None:
                _orig_exc_handler(loop, context)
            else:
                loop.default_exception_handler(context)
        _loop.set_exception_handler(_suppress_context_errors)
        try:
            final_text, tool_counts = _loop.run_until_complete(
                _run_agent_async(prompt, system_prompt_text)
            )
        except Exception as _err:
            _loop.set_exception_handler(_orig_exc_handler)
            # Mark this cell as finished (even on error) in the persisted
            # registry so the next cell's orphan cleanup doesn't treat
            # files written during this cell's window as partial outputs.
            if _current_cell_id_for_runs:
                _runs_err = _load_cell_runs()
                _rec_err = _runs_err.get(_current_cell_id_for_runs) or {}
                _rec_err["finished_at"] = _t_mod.time()
                _runs_err[_current_cell_id_for_runs] = _rec_err
                _save_cell_runs(_runs_err)
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
        # Mark this cell as finished normally in the persisted registry.
        # On the next cell entry, the orphan cleanup will see finished_at
        # is set and skip this window.
        if _current_cell_id_for_runs:
            _runs_ok = _load_cell_runs()
            _rec_ok = _runs_ok.get(_current_cell_id_for_runs) or {}
            _rec_ok["finished_at"] = _t_mod.time()
            _runs_ok[_current_cell_id_for_runs] = _rec_ok
            _save_cell_runs(_runs_ok)

        # Flush any ipywidgets.Output containers collected by KernelShellBackend.
        # display() is called here — after run_until_complete — so we are back
        # in the normal synchronous cell-execution context where zmq comm works.
        _pending = get_ipython().user_ns.pop("_sage_pending_displays", [])
        try:
            with open("/tmp/sage_debug.log", "a") as _dbg:
                _dbg.write(f"\n=== flush _sage_pending_displays ===\n")
                _dbg.write(f"pending count: {len(_pending)}\n")
                for _i, _entry in enumerate(_pending):
                    if isinstance(_entry, tuple) and len(_entry) == 2 and isinstance(_entry[1], dict):
                        _w, _kw = _entry
                        _dbg.write(f"  [{_i}] {type(_w).__name__} kwargs={list(_kw.keys())}\n")
                    else:
                        _dbg.write(f"  [{_i}] (bare) {type(_entry).__name__}\n")
        except Exception:
            pass
        if _pending:
            from IPython.display import display as _disp
            for _entry in _pending:
                # Each entry is (object, kwargs) — see _capture_display in
                # sage_kernel_backend.py. Older versions stored bare objects;
                # accept both shapes for backward compatibility.
                try:
                    if isinstance(_entry, tuple) and len(_entry) == 2 and isinstance(_entry[1], dict):
                        _w, _kw = _entry
                        _disp(_w, **_kw)
                    else:
                        _disp(_entry)
                except Exception as _flush_err:
                    try:
                        with open("/tmp/sage_debug.log", "a") as _dbg:
                            import traceback as _tb_dbg
                            _dbg.write(f"  FLUSH ERROR: {type(_flush_err).__name__}: {_flush_err}\n")
                            _dbg.write(_tb_dbg.format_exc())
                    except Exception:
                        pass

        _elapsed = round(_time.time() - _t_start, 1)

        # Append run entry to .sage_run.jsonl (hidden file, cleared by %reset)
        _log_path = Path(SAGE_OUTPUT_DIR) / ".sage_run.jsonl"
        _log_entry = {
            "timestamp": _SAGE_DATETIME.now(_SAGE_UTC).isoformat(),
            "prompt": prompt[:200] + ("…" if len(prompt) > 200 else ""),
            "elapsed_sec": _elapsed,
            "tool_calls": tool_counts,
            "total_tool_calls": sum(tool_counts.values()),
            "empty_final": not (final_text or "").strip(),
        }
        with open(_log_path, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(_log_entry) + "\n")

        # Update conversation history for cross-cell memory.
        # Tag each entry with cell_id so future reruns can truncate cleanly.
        SAGE_MESSAGES.append({"role": "user", "content": prompt, "cell_id": cell_id})
        SAGE_MESSAGES.append({"role": "assistant", "content": final_text, "cell_id": cell_id})

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

        # When a widget skill (e.g. sage-bbox-map) rendered its own live map,
        # suppress the static-Folium fallback even if intermediate GeoJSON files
        # were written. The widget owns the cell's map output.
        try:
            widget_map_rendered = bool(ip.user_ns.get("_sage_widget_map_rendered", False))  # noqa: F821
        except Exception:
            widget_map_rendered = False

        if not found_any:
            # Fallback: plain markdown + auto-display new files separately
            if final_text.strip():
                from IPython.display import display, Markdown
                display(Markdown(final_text))
            if new and not widget_map_rendered:
                _display_new_outputs(new)
            elif new and widget_map_rendered:
                # Still show non-map outputs (CSV, PNG) but skip auto-Folium.
                _display_new_outputs([f for f in new
                                      if not (f.endswith('.geojson') or f.endswith('.wms.json'))])
        # Map rendering is agent-driven via inline `![](*.geojson)` tags
        # parsed in `_render_markdown_with_files`. There is no fallback that
        # stacks the output directory — if the agent did not specify map
        # layers in its response, none are rendered.

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
        """Reset Sage: clear output files, conversation history, and kernel state.

        Usage:
            %reset

        Resets:
          - SAGE_OUTPUT_DIR contents (sidecar registries, generated files)
          - SAGE_MESSAGES (cross-cell conversation history)
          - User-defined kernel variables and Sage's internal pubsub state
            (_sage_pending_displays, _sage_var_subscribers) — guarantees the
            next %%ask cell starts from a clean kernel namespace
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

        # Clear user-defined kernel variables — like IPython's %reset -f, but
        # in-process so we can do it deterministically without a confirmation
        # prompt. Uses _SAGE_RESET_KEEP, a snapshot of user_ns taken right after
        # Sage's startup script finished bootstrapping. Anything not in that
        # snapshot is user-created state (cell variables, helper-imported
        # modules, dropdown subscribers) and gets deleted.
        try:
            ip = get_ipython()  # noqa: F821
            user_ns = ip.user_ns
            _keep = user_ns.get("_SAGE_RESET_KEEP", frozenset())
            for _k in list(user_ns.keys()):
                if _k not in _keep:
                    user_ns.pop(_k, None)
        except Exception:
            pass

        from IPython.display import display, Markdown, HTML

        # On Colab, the cell-output iframe is at colab.googleusercontent.com
        # and the notebook menubar is at colab.research.google.com. Same-Origin
        # Policy blocks kernel-side JS from reaching the menubar, so we ask the
        # user for one click. On JupyterLab (NRP JupyterHub etc.), Lumino lives
        # in the same document and the DOM-simulation block below clears cell
        # outputs automatically.
        import sys as _sys
        _is_colab = "google.colab" in _sys.modules or os.path.exists("/content")

        if _is_colab:
            display(Markdown(
                "**Sage reset.** Output folder cleared, history cleared.\n\n"
                "_To also clear cell outputs: click **Edit → Clear all outputs** "
                "(in the Colab menu bar)._"
            ))
        else:
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

    # -----------------------------------------------------------------------
    # %%skill — install skills from local paths or GitHub URLs
    # -----------------------------------------------------------------------
    from IPython.core.magic import register_cell_magic

    @register_cell_magic
    def skill(line, cell):
        """Install Sage skills from local paths or GitHub URLs.

        Usage:
            %%skill
            /home/jovyan/private_skills/my-skill
            ~/another_skill
            https://github.com/<org>/<repo>/tree/<COMMIT_SHA>/path/to/skill
            # blank lines and comments are ignored

        Security model:
          - Local paths: installed silently (you control the filesystem).
          - GitHub URLs from allowlisted orgs (~/.deepagents/.sage_trusted_orgs.json):
            installed silently, with any ref. Branch refs (e.g. tree/main/...) work
            but produce a "not reproducible" notice — the same URL may install
            different content on a later run as the branch advances. For
            production / published notebooks, prefer a commit SHA.
          - GitHub URLs from unknown orgs: collected into a single trust prompt
            showing SKILL.md descriptions + helper file lists. Type 'y' to
            install all listed skills, anything else to abort. Branch refs are
            rejected for untrusted orgs — use a commit SHA or tag.
        """
        from IPython.display import display, HTML

        entries = []
        errors = []
        for raw in cell.splitlines():
            parsed = _sage_parse_skill_entry(raw)
            if parsed is None:
                continue
            if parsed["kind"] == "error":
                errors.append(parsed)
            else:
                entries.append(parsed)

        if not entries and not errors:
            display(HTML("<div style='color:#888'>%%skill: nothing to install (empty cell)</div>"))
            return

        # Categorize
        trusted_orgs = _sage_load_trusted_orgs()
        auto_install = []   # local paths + trusted-org URLs
        needs_prompt = []   # unknown-org URLs
        rejected = list(errors)  # parse errors

        for e in entries:
            if e["kind"] == "local":
                if not e["path"].exists():
                    e["error"] = f"path does not exist: {e['path']}"
                    rejected.append(e); continue
                if not e["path"].is_dir():
                    e["error"] = f"path is not a directory: {e['path']}"
                    rejected.append(e); continue
                if not (e["path"] / "SKILL.md").exists():
                    e["error"] = f"no SKILL.md in {e['path']}"
                    rejected.append(e); continue
                auto_install.append(e)
            elif e["kind"] == "github":
                ref_kind = _sage_classify_ref(e["ref"])
                e["ref_kind"] = ref_kind
                is_trusted = e["org"] in trusted_orgs
                if ref_kind == "branch" and not is_trusted:
                    e["error"] = (f"branch ref '{e['ref']}' is not allowed for "
                                  f"untrusted org '{e['org']}' — use a commit SHA "
                                  f"or tag (or add '{e['org']}' to "
                                  f"~/.deepagents/.sage_trusted_orgs.json)")
                    rejected.append(e); continue
                if is_trusted:
                    auto_install.append(e)
                else:
                    needs_prompt.append(e)

        # Clone all GitHub entries up front (clone is side-effect-free until copytree)
        for e in (auto_install + needs_prompt):
            if e["kind"] != "github":
                continue
            subtree, err = _sage_clone_github_subtree(
                e["org"], e["repo"], e["ref"], e["subpath"]
            )
            if err:
                e["error"] = err
                # Move from its current list to rejected
                if e in auto_install: auto_install.remove(e)
                if e in needs_prompt: needs_prompt.remove(e)
                rejected.append(e)
            else:
                e["src_dir"] = subtree

        # Validate cloned skills have SKILL.md
        for lst in (auto_install, needs_prompt):
            for e in list(lst):
                if e["kind"] == "github" and "src_dir" in e:
                    if not (e["src_dir"] / "SKILL.md").exists():
                        e["error"] = f"no SKILL.md in {e['org']}/{e['repo']}@{e['ref'][:12]}/{e['subpath']}"
                        lst.remove(e)
                        rejected.append(e)

        # Build trust prompt for unknown-org entries
        if needs_prompt:
            print()
            print("=" * 70)
            print(f"⚠  {len(needs_prompt)} skill(s) from non-allowlisted GitHub org(s)")
            print("=" * 70)
            print()
            print("Skills are arbitrary Python that runs in this kernel with your")
            print("permissions. Read the previews below before approving.")
            print()
            for e in needs_prompt:
                desc = _sage_read_skill_md(e["src_dir"]) or "(no description in SKILL.md)"
                files, more = _sage_list_skill_files(e["src_dir"], limit=15)
                print(f"  📦 {e['skill_name']}")
                print(f"     source:    https://github.com/{e['org']}/{e['repo']}/tree/{e['ref']}/{e['subpath']}")
                print(f"     ref kind:  {e['ref_kind']}{' ⚠ tags can be moved — SHA preferred' if e['ref_kind']=='tag' else ''}")
                print(f"     org:       {e['org']}  (not in ~/.deepagents/.sage_trusted_orgs.json)")
                print(f"     describes: {desc[:300]}")
                print(f"     files     ({len(files)}{'+'+str(more) if more else ''}):")
                for fname, size in files:
                    print(f"        {fname}  ({size:,} bytes)")
                if more:
                    print(f"        ... and {more} more")
                print()

            try:
                response = input(
                    f"Type 'y' to install all {len(needs_prompt)} unknown-org skill(s), "
                    f"anything else to abort: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                response = ""
            if response not in ("y", "yes"):
                print(f"Aborted: {len(needs_prompt)} unknown-org skill(s) NOT installed.")
                # The auto_install set still proceeds (they're trusted)
                for e in needs_prompt:
                    e["error"] = "user declined trust prompt"
                    rejected.append(e)
                needs_prompt = []

        # Install everything that survived
        installed = []
        for e in (auto_install + needs_prompt):
            src = e["src_dir"] if e["kind"] == "github" else e["path"]
            try:
                dest = _sage_install_skill_dir(src, e["skill_name"])
                e["dest"] = dest
                installed.append(e)
                # Phase 4: refresh the per-skill Learnings.md frontmatter
                # against the newly installed SKILL.md. If the digest
                # changed (skill was updated), prepend a warning banner so
                # the agent flags pre-existing lessons on next read.
                try:
                    _sage_audit_learnings(e["skill_name"])
                except Exception:
                    pass
            except Exception as ex:
                e["error"] = f"install failed: {ex}"
                rejected.append(e)

        # Render summary as tight HTML (Markdown adds visible paragraph and
        # list spacing that we want to avoid).
        def _esc(s):
            return (str(s).replace("&", "&amp;")
                          .replace("<", "&lt;").replace(">", "&gt;"))
        parts = [
            "<hr style='margin:8px 0'/>",
            "<div style='line-height:1.35'>",
            "<b>Skills install summary</b>",
        ]
        if installed:
            parts.append(
                f"<div style='margin-top:4px'>✅ <b>Installed "
                f"({len(installed)})</b> — available in the next "
                f"<code>%%ask</code> cell:</div>"
            )
            parts.append("<ul style='margin:2px 0 0 0; padding-left:1.5em'>")
            any_branch = False
            for e in installed:
                if e["kind"] == "local":
                    src_lbl = f"<code>{_esc(e['path'])}</code>"
                else:
                    # SHA refs are 40 chars — truncate for display. Tags and
                    # branches show their full name.
                    ref_display = (e["ref"][:12] if e.get("ref_kind") == "sha"
                                   else e["ref"])
                    src_lbl = (f"<code>{_esc(e['org'])}/{_esc(e['repo'])}"
                               f"@{_esc(ref_display)}/{_esc(e['subpath'])}</code>")
                    if e.get("ref_kind") == "branch":
                        any_branch = True
                parts.append(f"<li><b>{_esc(e['skill_name'])}</b> ← {src_lbl}</li>")
            parts.append("</ul>")
            if any_branch:
                parts.append(
                    "<div style='margin-top:4px;color:#64748B;font-size:0.9em'>"
                    "Note: one or more skills are pinned to a branch. If a "
                    "future rerun gives different results, check the skill "
                    "repo for recent changes."
                    "</div>"
                )
        if rejected:
            parts.append(
                f"<div style='margin-top:6px'>⛔ <b>Not installed "
                f"({len(rejected)})</b>:</div>"
            )
            parts.append("<ul style='margin:2px 0 0 0; padding-left:1.5em'>")
            for e in rejected:
                label = e.get("skill_name") or e.get("raw", "").strip() or "?"
                err = e.get("error", "unknown error")
                parts.append(f"<li><b>{_esc(label)}</b>: {_esc(err)}</li>")
            parts.append("</ul>")
        if not installed and not rejected:
            parts.append("<div style='color:#888'><i>(no skills processed)</i></div>")
        parts.append("</div>")
        display(HTML("".join(parts)))

    del skill  # keep IPython namespace clean

    # -----------------------------------------------------------------------
    # %%mcp — register MCP (Model Context Protocol) servers for the session
    # -----------------------------------------------------------------------
    @register_cell_magic
    def mcp(line, cell):
        """Register MCP servers for this notebook session.

        Usage:
            %%mcp
            {
              "mcpServers": {
                "wenokn": {"url": "https://wenokn.fastmcp.app/mcp"},
                "filesystem": {
                  "command": "npx",
                  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
                }
              }
            }

        Accepts Claude Desktop / VS Code / Gemini-style JSON config. The
        top-level "mcpServers" wrapper is optional. Use $VARNAME (or
        ${VARNAME}) in any string field to interpolate environment variables.

        Tools loaded here are MERGED into the kernel-scoped registry, so
        multiple %%mcp cells accumulate. If a server name appears in more
        than one cell, the most recent config wins for THAT server only;
        other servers stay registered. On total failure (no server loaded),
        the prior registry is kept untouched.

        Kernel restart wipes the registry. After restart, re-run the
        %%mcp cell(s) before any %%ask cell that depends on MCP tools.
        """
        import html as _html
        from IPython.display import display, HTML
        global _SAGE_MCP_TOOLS_BY_SERVER, _SAGE_MCP_SERVERS, _SAGE_MCP_CLIENT

        def _esc(s):
            return _html.escape(str(s), quote=False)

        body = (cell or "").strip()
        if not body:
            display(HTML("<i style='color:#888'>Empty %%mcp cell — nothing to register.</i>"))
            return

        try:
            raw = json.loads(body)
        except json.JSONDecodeError as e:
            display(HTML(
                f"<b style='color:#c33'>Error:</b> %%mcp body must be valid JSON.<br>"
                f"<code>{_esc(e)}</code>"
            ))
            return

        try:
            servers = _sage_normalize_mcp_config(raw)
        except ValueError as e:
            display(HTML(f"<b style='color:#c33'>Error:</b> {_esc(e)}"))
            return

        if not servers:
            display(HTML("<i style='color:#888'>No servers found in %%mcp body.</i>"))
            return

        from langchain_mcp_adapters.client import MultiServerMCPClient

        loaded_tools_by_server: dict = {}  # server name → list of tools
        loaded_servers: dict = {}
        failures: list = []

        _loop = asyncio.get_event_loop()
        for name, server_cfg in servers.items():
            try:
                _client = MultiServerMCPClient({name: server_cfg})
                _tools = _loop.run_until_complete(_client.get_tools())
                loaded_tools_by_server[name] = _tools
                loaded_servers[name] = server_cfg
            except Exception as e:
                failures.append((name, str(e)))

        # MERGE this cell's loaded servers into the kernel-scoped registry.
        # Per-server replace: if a server name was already registered, the
        # new config (and tools) wins. Servers from prior %%mcp cells that
        # weren't named in this cell are left untouched. On total failure
        # (no loaded_servers), the prior registry is kept intact.
        replaced_servers: list = []
        if loaded_servers:
            for sname in loaded_servers:
                if sname in _SAGE_MCP_SERVERS:
                    replaced_servers.append(sname)
            _SAGE_MCP_SERVERS = {**_SAGE_MCP_SERVERS, **loaded_servers}
            _SAGE_MCP_TOOLS_BY_SERVER = {
                **_SAGE_MCP_TOOLS_BY_SERVER, **loaded_tools_by_server
            }
            _SAGE_MCP_CLIENT = MultiServerMCPClient(_SAGE_MCP_SERVERS)

        loaded_tool_count = sum(len(t) for t in loaded_tools_by_server.values())
        total_servers = len(_SAGE_MCP_SERVERS)
        total_tools = sum(len(t) for t in _SAGE_MCP_TOOLS_BY_SERVER.values())

        parts = ["<div style='font-family: -apple-system, sans-serif; font-size: 13px;'>"]
        if loaded_tool_count:
            parts.append(
                f"<div><b>✓ Loaded {loaded_tool_count} tool(s) from "
                f"{len(loaded_servers)} server(s) this cell</b></div>"
            )
            for sname, tool_list in loaded_tools_by_server.items():
                replaced_tag = (
                    " <span style='color:#a60'>(replaced previous config)</span>"
                    if sname in replaced_servers else ""
                )
                parts.append(
                    f"<div style='margin-top:8px'><b>{_esc(sname)}</b> "
                    f"<span style='color:#888'>({len(tool_list)} tools)</span>"
                    f"{replaced_tag}</div>"
                )
                parts.append("<ul style='margin:2px 0 0 0; padding-left:1.5em'>")
                for t in tool_list:
                    desc_short = (t.description or "").strip().replace("\n", " ")
                    if len(desc_short) > 200:
                        desc_short = desc_short[:200] + "…"
                    parts.append(
                        f"<li><code>{_esc(t.name)}</code> "
                        f"<span style='color:#666'>— {_esc(desc_short)}</span></li>"
                    )
                parts.append("</ul>")
            # Show cumulative registry total if this cell wasn't the first
            if total_servers > len(loaded_servers):
                other_servers = sorted(set(_SAGE_MCP_SERVERS) - set(loaded_servers))
                parts.append(
                    f"<div style='margin-top:10px; color:#555'>"
                    f"<b>Registry total: {total_tools} tools from {total_servers} server(s)</b> "
                    f"<span style='color:#888'>(also active from earlier cells: "
                    f"{', '.join(_esc(s) for s in other_servers)})</span></div>"
                )

        if failures:
            kept_note = (
                " (previous registry kept)"
                if loaded_servers or _SAGE_MCP_SERVERS
                else ""
            )
            parts.append(
                f"<div style='margin-top:10px; color:#c33'>"
                f"<b>⚠ Failed to load {len(failures)} server(s)</b>"
                f"<span style='color:#888'>{_esc(kept_note)}</span></div>"
            )
            parts.append("<ul style='margin:2px 0 0 0; padding-left:1.5em'>")
            for fname, ferr in failures:
                ferr_short = ferr.replace("\n", " ")
                if len(ferr_short) > 300:
                    ferr_short = ferr_short[:300] + "…"
                parts.append(
                    f"<li><b>{_esc(fname)}</b>: "
                    f"<code style='color:#c33'>{_esc(ferr_short)}</code></li>"
                )
            parts.append("</ul>")

        if not loaded_tool_count and not failures:
            parts.append("<i style='color:#888'>No tools loaded.</i>")

        parts.append("</div>")
        display(HTML("".join(parts)))

    del mcp  # keep IPython namespace clean

    # -----------------------------------------------------------------------
    # %%skill-build — build a skill from a data-source URL via the
    # appropriate built-in skill-builder meta-skill
    # -----------------------------------------------------------------------
    @register_cell_magic("skill-build")
    def skill_build(line, cell):
        """Build an ARGUS skill from a data-source URL.

        Usage:
            %%skill-build
            https://services1.arcgis.com/.../FeatureServer/0
            # blank lines and comments are ignored
            # additional URLs on their own lines build additional skills

        Each URL must point at a supported data source. Currently:

          - ArcGIS Feature Service / MapService URLs (path contains
            /FeatureServer/<n> or /MapServer/<n>) are dispatched to the
            built-in ``arcgis-feature-skill-builder`` meta-skill.

        For each URL the agent uses the matching skill-builder meta-skill
        to fetch the source's metadata, generate a complete SKILL.md
        (with field table, code dictionaries, canonical loader, and
        worked examples), save it to ``_skills_/<skill-name>/`` next to
        the notebook, and install it. The generated skill is available
        in the next %%ask cell.

        Internally this magic constructs a structured prompt and
        dispatches it through the standard %%ask agent loop, so it
        inherits the same API-key handling, streaming display, and
        cross-cell memory.
        """
        from IPython.display import display, HTML

        # Parse URLs from the cell body. Same conventions as %%skill:
        # one URL per line, blank lines and # comments ignored.
        urls = []
        for raw in (cell or "").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            urls.append(s)

        if not urls:
            display(HTML(
                "<div style='color:#888'>%%skill-build: nothing to build "
                "(empty cell). List one URL per line.</div>"
            ))
            return

        # Construct a structured prompt asking the agent to dispatch to
        # the appropriate built-in skill-builder meta-skill for each
        # URL. We deliberately do NOT enumerate the URL types or pick
        # the meta-skill here — the meta-skills' own frontmatter
        # descriptions handle routing. When a new skill-builder
        # meta-skill ships (e.g. csv-skill-builder), this magic needs
        # zero changes.
        url_list = "\n".join(urls)
        if len(urls) == 1:
            prompt = (
                "Build an ARGUS skill for the URL below. Use the "
                "appropriate built-in skill-builder meta-skill — match "
                "the URL's type against the available meta-skills' "
                "descriptions. For ArcGIS Feature Service URLs, that is "
                "the `arcgis-feature-skill-builder` skill.\n\n"
                f"{url_list}\n\n"
                "Save the generated SKILL.md to `_skills_/<skill-name>/` "
                "in the current working directory. Do not copy it to "
                "the global skill registry; the next %%ask cell will "
                "pick it up from `_skills_/` automatically. If no "
                "built-in skill-builder meta-skill matches the URL's "
                "type, report the unsupported URL type clearly without "
                "attempting an improvised build."
            )
        else:
            prompt = (
                "Build ARGUS skills for the URLs below, one skill per "
                "URL, using the appropriate built-in skill-builder "
                "meta-skill for each. Match each URL's type against the "
                "available meta-skills' descriptions; for ArcGIS Feature "
                "Service URLs, use the `arcgis-feature-skill-builder` "
                "skill.\n\n"
                f"{url_list}\n\n"
                "Save each generated SKILL.md to "
                "`_skills_/<skill-name>/` in the current working "
                "directory. Do not copy any of the generated skills to "
                "the global skill registry; the next %%ask cell picks "
                "them up from `_skills_/` automatically. If a URL's "
                "type does not match any built-in skill-builder "
                "meta-skill, report it as unsupported and continue with "
                "the remaining URLs."
            )

        # Dispatch through the standard %%ask agent loop. This re-uses
        # the API-key check, orphan-file cleanup, agent invocation,
        # streaming tool display, and SAGE_MESSAGES persistence —
        # nothing here duplicates that logic.
        #
        # Note: the Python-level `ask` name is deleted from the module
        # namespace right after IPython registers it as a magic
        # (`del ask` near the end of that block, mirroring the cleanup
        # this magic and %%skill / %%mcp also do). So we cannot call
        # `ask(...)` directly; we go through IPython's magic dispatch
        # by name.
        ip = get_ipython()
        if ip is None:
            display(HTML(
                "<div style='color:#a00'>%%skill-build: not running in an "
                "IPython kernel; cannot dispatch to %%ask.</div>"
            ))
            return
        return ip.run_cell_magic("ask", "", prompt)

    del skill_build  # keep IPython namespace clean

except Exception as exc:
    warnings.warn(
        f"Sage magic commands could not be registered: {exc}", stacklevel=1
    )


# Snapshot the user_ns keys present right after this startup script finishes.
# IPython runs startup scripts via `exec(code, user_ns)`, so every top-level
# `def`, `import`, and assignment ends up here. `%reset` preserves exactly
# this set — anything else is user-created state (cell variables, dropdown
# subscribers, helper-imported modules) and is safe to wipe.
try:
    _ip = get_ipython()  # noqa: F821
    if _ip is not None:
        _ip.user_ns["_SAGE_RESET_KEEP"] = (
            frozenset(_ip.user_ns.keys()) | {"_SAGE_RESET_KEEP"}
        )
except Exception:
    pass
