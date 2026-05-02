# KernelShellBackend: Bridging LangChain Agents and the Jupyter Kernel

## Abstract

Sage is an AI-agent framework built on DeepAgents (a LangChain-based tool-calling agent)
that runs inside JupyterHub notebooks. In its default configuration, the agent executes
all shell commands — including Python scripts — as **subprocesses**, which means
interactive widgets (ipyleaflet, ipywidgets, plotly), shared kernel state, and inline
display objects are not available to agent-generated code. This document describes
**KernelShellBackend**, a subclass of DeepAgents' `LocalShellBackend` that routes
Python invocations into the live IPython kernel via `exec()`, enabling a fundamentally
richer class of skills: interactive maps, live charts, shared variables, and progress
widgets — all rendered inline in the `%%ask` cell. The implementation required 14
development iterations to solve a series of non-obvious constraints in IPython's
concurrency and display pipeline.

---

## 1. Background

### 1.1 DeepAgents and LocalShellBackend

DeepAgents is a tool-calling agent framework that wraps LangChain. When a skill
instructs the agent to run a script, the agent calls its `execute` tool, which
dispatches to a **backend** — an abstraction over "how do I run a shell command."

The default backend, `LocalShellBackend`, runs each command as a subprocess:

```python
# Simplified LocalShellBackend
result = subprocess.run(command, shell=True, capture_output=True, cwd=self.cwd)
return ExecuteResponse(output=result.stdout + result.stderr, exit_code=result.returncode)
```

This works well for most CLI tasks: fetching data, processing files, running analyses.
The agent sees stdout/stderr as text and decides what to do next.

### 1.2 The Problem: Subprocesses Cannot Produce Notebook Widgets

A Jupyter notebook cell communicates with the browser via IPython's **comm protocol**
(ZeroMQ messages). When a Python object calls `display(widget)`, IPython sends a
`comm_open` message to the frontend, which creates a JavaScript-side widget model and
renders it inside the cell's output area.

A subprocess is a separate process with no connection to the IPython kernel's ZeroMQ
sockets. Any `display()` calls inside a subprocess are no-ops; any `print()` output
is captured in a pipe, not routed to the cell. The subprocess also has no access to
the kernel's `user_ns` (the namespace where notebook variables like `USER_BBOX` live).

This means that agent-generated skills that use leafmap, ipyleaflet, ipywidgets, plotly,
or shared kernel state simply cannot work with the subprocess backend.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  %%ask cell                                                      │
│                                                                  │
│  sage_magic.py                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  _run_agent_async()                                       │   │
│  │  loop.run_until_complete(...)                             │   │
│  │  │                                                        │   │
│  │  │  LangChain agent streams tool calls                   │   │
│  │  │  ┌─────────────────────────────────────────────────┐  │   │
│  │  │  │  KernelShellBackend.execute(command)             │  │   │
│  │  │  │  ┌───────────────────────────────────────────┐  │  │   │
│  │  │  │  │  _parse_python_invocation(command)         │  │  │   │
│  │  │  │  │   ↓ matches "python script.py"             │  │  │   │
│  │  │  │  │  _run_in_kernel(code, argv, file_path)     │  │  │   │
│  │  │  │  │   ↓ exec(compiled, ip.user_ns)             │  │  │   │
│  │  │  │  │   ↓ capture stdout/stderr (StringIO)       │  │  │   │
│  │  │  │  │   ↓ intercept display() calls              │  │  │   │
│  │  │  │  │   ↓ queue widgets → _sage_pending_displays  │  │  │   │
│  │  │  │  │  ExecuteResponse(stdout text)              │  │  │   │
│  │  │  │  └───────────────────────────────────────────┘  │  │   │
│  │  │  └─────────────────────────────────────────────────┘  │   │
│  │  │                                                        │   │
│  │  └──────────────────────────────────────────────────────┘   │
│  │                                                              │
│  │  ← run_until_complete() returns ←                           │
│  │                                                              │
│  │  _pending = user_ns.pop("_sage_pending_displays", [])        │
│  │  for widget in _pending:                                     │
│  │      display(widget)   ← NOW on main thread, zmq works ←    │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 Key Components

| Component | File | Role |
|---|---|---|
| `KernelShellBackend` | `sage_kernel_backend.py` | Subclass of `LocalShellBackend`; intercepts Python invocations |
| `_parse_python_invocation()` | `sage_kernel_backend.py` | Tokenizes commands to detect `python[3] [flags] script.py` or `python -c "..."` |
| `_run_in_kernel()` | `sage_kernel_backend.py` | Compiles and execs code in `ip.user_ns`; captures I/O; defers widget display |
| `%%ask` magic | `sage_magic.py` | Runs the agent loop via `loop.run_until_complete()`; flushes `_sage_pending_displays` afterward |
| Dockerfile | `Dockerfile` | Pre-installs `ipywidgets`, `ipyleaflet`, `leafmap`, `plotly` so frontend extensions are registered at Lab startup |

---

## 3. Implementation Details

### 3.1 Shell Command Parsing

The entry point is `execute(command: str) -> ExecuteResponse`. The first task is
determining whether the command is a Python invocation that should be routed to the
kernel, or a non-Python command (pip, conda, curl, etc.) that should fall through to
the parent subprocess implementation.

#### 3.1.1 Tokenization

Standard `shlex.split()` is insufficient because shell operators (`|`, `>`, `&&`, `;`)
outside quotes need to be identified as separate tokens — not stripped as whitespace —
so the parser can detect piped commands like `python foo.py | head`.

The tokenizer uses `shlex.shlex` with `punctuation_chars=True`, which makes shell
operators into their own tokens:

```python
def _tokenize(command: str) -> list[str] | None:
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return None  # unclosed quotes etc.
```

A command containing any shell operator is NOT routed to the kernel — it falls through
to the subprocess backend:

```python
_SHELL_OPERATORS = {"|", "||", "&", "&&", ";", ";;", ">", ">>", "<", "<<", "<<<"}

if any(tok in _SHELL_OPERATORS for tok in tokens):
    return None  # let subprocess handle it
```

This is critical: a command like `python gen_data.py > output.txt` must run as a
subprocess (shell redirection), not via `exec()`.

#### 3.1.2 Python Invocation Detection

After confirming there are no shell operators, the parser checks:
1. `tokens[0]` basename must be `python` or `python3`
2. Recognized flags (`-u`, `-B`, `-O`, `-OO`) are skipped
3. `-c <source>` → source code is the next token
4. `-m` → fall through (module invocation is not intercepted)
5. Otherwise → next token is a script path

```python
def _parse_python_invocation(command: str) -> tuple[str, list[str]] | None:
    tokens = _tokenize(command)
    if not tokens:
        return None
    if any(tok in _SHELL_OPERATORS for tok in tokens):
        return None
    exe = os.path.basename(tokens[0])
    if exe not in {"python", "python3"}:
        return None
    # ... parse flags, return (source_or___FILE__:path, argv)
```

Return value is a `(source, argv)` tuple where `source` is either the raw Python
code (for `-c` invocations) or the sentinel string `"__FILE__:<path>"` for script
files. The caller reads the file if the sentinel is present.

### 3.2 The `execute()` / `aexecute()` Dispatch

```python
def execute(self, command: str) -> ExecuteResponse:
    if self._ipython is None:
        return super().execute(command)      # not in a kernel

    parsed = _parse_python_invocation(command)
    if parsed is None:
        return super().execute(command)      # non-Python or complex shell

    source, argv = parsed
    if source.startswith("__FILE__:"):
        code = Path(source[9:]).read_text()
    else:
        code = source

    return self._run_in_kernel(code, argv, file_path)
```

#### Thread Safety: `aexecute()` Must Not Delegate to a Thread

The default `LocalShellBackend.aexecute()` dispatches via `asyncio.to_thread()`,
which runs `execute()` in a worker thread. Running `exec()` on a worker thread is
unsafe for IPython: `user_ns` accesses are not thread-safe, and IPython's display
hooks are designed for the main kernel thread.

Sage's agent runs entirely on the main kernel thread inside a re-entrant asyncio loop
(patched by `nest_asyncio`). The `aexecute()` override calls `execute()` directly,
keeping everything on the main thread:

```python
async def aexecute(self, command: str) -> ExecuteResponse:
    return self.execute(command)   # stay on main thread; no to_thread()
```

### 3.3 `_run_in_kernel()`: The Core

This method is where agent-issued Python code actually runs. It must:

1. Set up `sys.argv` and `__file__` so scripts see the right context
2. Redirect `sys.stdout` and `sys.stderr` so the agent can read `print()` output
3. Intercept `display()` calls to capture widget objects
4. Execute the code via `exec()`
5. Restore all patched state
6. Return text output to the agent via `ExecuteResponse`
7. Defer widget display to after the asyncio loop returns

#### 3.3.1 Execution Context Setup

```python
prev_argv = sys.argv
prev_file = user_ns.get("__file__", None)
sys.argv = argv if argv else [file_path]
user_ns["__file__"] = file_path
os.chdir(str(self.cwd))
```

The `finally` block restores all of these unconditionally.

#### 3.3.2 stdout/stderr Capture

`sys.stdout` and `sys.stderr` are replaced with `io.StringIO` buffers. This captures
all `print()` output that the agent needs to see as text feedback, while preventing
it from appearing as raw cell output during agent execution.

```python
_stdout_buf = io.StringIO()
_stderr_buf = io.StringIO()
sys.stdout = _stdout_buf
sys.stderr = _stderr_buf
# ... exec ...
sys.stdout = _orig_stdout
sys.stderr = _orig_stderr
```

#### 3.3.3 display() Interception

The critical piece: IPython's `display()` function is monkey-patched to capture widget
objects rather than sending them to the frontend. There are two import paths for
`display` in IPython that must both be patched:

```python
import IPython.display as _ipd_module
import IPython.core.display_functions as _ipcdf_module  # IPython >= 8.x

def _capture_display(*objs, **kwargs):
    for obj in objs:
        _captured_objs.append(obj)

_ipd_module.display = _capture_display
_ipcdf_module.display = _capture_display   # if available
```

After `exec()`, both are restored and the captured objects are queued:

```python
user_ns.setdefault("_sage_pending_displays", []).extend(_captured_objs)
```

#### 3.3.4 matplotlib Auto-close

IPython's matplotlib inline backend automatically calls `plt.show()` (and implicitly
`plt.draw()`) at the end of every cell execution, displaying any open figures. Inside
`exec()`, this double-displays figures: once from `plt.show()` inside the script, and
once from the inline backend cleanup. The fix is to append `plt.close('all')` to the
executed code:

```python
wrapped_code = (
    code
    + "\ntry:\n    import matplotlib.pyplot as _sage_plt; _sage_plt.close('all')\n"
    + "except Exception: pass\n"
)
```

### 3.4 The Deferred Display Pattern

The most subtle aspect of the implementation. Inside `loop.run_until_complete()`,
the asyncio event loop is blocked — it is processing Python tasks synchronously via
`nest_asyncio`'s re-entrant patching. In this state, IPython's ZeroMQ communication
layer cannot reliably send `comm_open` and `display_data` messages to the frontend.

Calling `display(widget)` inside the agent loop either does nothing or sends messages
that the frontend cannot process because it is waiting for the kernel to become idle
(which only happens after `run_until_complete()` returns).

The solution is a two-phase display protocol:

**Phase 1** (inside `loop.run_until_complete()`):
- `_run_in_kernel()` captures widget objects by intercepting `display()`
- Widget objects are stored in `user_ns["_sage_pending_displays"]` — NOT displayed

**Phase 2** (after `loop.run_until_complete()` returns):
- The kernel is back in its normal synchronous cell-execution context
- ZeroMQ comm channels work normally
- `sage_magic.py` flushes the pending list:

```python
# sage_magic.py, after loop.run_until_complete() returns:
_pending = get_ipython().user_ns.pop("_sage_pending_displays", [])
if _pending:
    from IPython.display import display as _disp
    for _w in _pending:
        _disp(_w)
```

This deferred display is the key insight that makes the entire system work. The widgets'
`comm_open` messages were already sent to the frontend when the widget objects were
**created** (not when `display()` is called). The `display()` call only sends the
`display_data` message containing the `application/vnd.jupyter.widget-view+json` mime
type that tells the frontend to render the model it already has. That message CAN be
sent from the normal synchronous context after the loop returns.

### 3.5 Integration with `sage_magic.py`

`sage_magic.py` is the IPython startup file that registers the `%%ask` cell magic.
It has three integration points with `KernelShellBackend`:

**1. Backend selection** (in `_run_agent_async()`):
```python
try:
    from sage_kernel_backend import KernelShellBackend
except ImportError:
    KernelShellBackend = None

backend_cls = KernelShellBackend if KernelShellBackend is not None else LocalShellBackend
agent = create_deep_agent(
    model,
    skills=skills_paths,
    backend=backend_cls(virtual_mode=False),
    ...
)
```

If `sage_kernel_backend` is not importable (e.g., in a stripped environment),
`LocalShellBackend` is used as a fallback — the skill still runs but widgets are
not rendered.

**2. The event loop** (in `ask()` magic):
```python
# Must use run_until_complete on the existing loop (nest_asyncio patched it).
# asyncio.run() creates a new loop, which conflicts with Python 3.13 task cleanup.
_loop = asyncio.get_event_loop()
final_text, tool_counts = _loop.run_until_complete(
    _run_agent_async(full_prompt)
)
```

**3. The deferred display flush** (immediately after `run_until_complete()`):
```python
_pending = get_ipython().user_ns.pop("_sage_pending_displays", [])
if _pending:
    from IPython.display import display as _disp
    for _w in _pending:
        _disp(_w)
```

### 3.6 Dockerfile: Pre-installation Requirement

The most operationally critical requirement — and the hardest to discover — is that
widget packages must be **pre-installed in the Docker image**, not installed at runtime.

JupyterLab's widget system has two parts:
1. The Python package (`ipywidgets`, `ipyleaflet`) — provides the Python `Widget` class
2. The JupyterLab frontend extension (`@jupyter-widgets/jupyterlab-manager`) — the
   JavaScript side that renders widgets in the browser

The frontend extension is a JupyterLab prebuilt extension. It is registered with
JupyterLab **at server startup** from the package's installation location. If the
package is installed after Lab has started (e.g., via `pip install --user` from inside
a running notebook), the Python package is available but the frontend extension is NOT
registered — JupyterLab doesn't hot-reload extensions. The result: all widgets render
as their text repr (`IntSlider(value=5, max=10)`) no matter how correctly the backend
code sends widget-view mime types.

The Dockerfile installs these packages before the JupyterLab server starts:

```dockerfile
RUN pip install --no-cache-dir "deepagents-cli[openai]" nest_asyncio \
    folium geopandas matplotlib rasterio \
    ipywidgets ipyleaflet leafmap plotly

RUN conda install -y -c conda-forge pdal python-pdal && conda clean -afy && \
    pip install --no-cache-dir pyforestscan laspy
```

**Diagnostic test**: If `widgets.IntSlider()` in a fresh cell renders as
`IntSlider(value=0, ...)` (text repr) rather than a draggable slider, the frontend
extension is not registered. No amount of backend code changes will fix this — the
image must be rebuilt.

---

## 4. Development History: 14 Iterations

The implementation required 14 tagged development iterations to reach a working state.
Each iteration exposed a different layer of the IPython/JupyterLab widget pipeline.
The lessons are documented here as they inform future work on Sage and any similar
agent-to-kernel bridge.

### Iteration 0.1.1 — Parser Bug: Shell Operators Inside Quoted Strings

**Problem**: Initial implementation used `shlex.split()` to detect shell operators.
`shlex.split("python -c 'print(1); print(2)'")` returns `["python", "-c", "print(1); print(2)"]`,
stripping the semicolons. The parser would split on the unquoted form of the command
before tokenization, misidentifying the inner `;` as a shell operator and falling
through to subprocess.

**Fix**: Switch to `shlex.shlex(punctuation_chars=True)` which treats shell operators
as tokens only when they appear unquoted, correctly leaving `;` inside `-c` strings
as part of the argument.

### Iteration 0.1.2 — `aexecute()` Ran `execute()` in a Worker Thread

**Problem**: The default `LocalShellBackend.aexecute()` uses `asyncio.to_thread()`.
This dispatched `execute()` to a worker thread, which called `exec()` with `user_ns`.
IPython's display machinery and `user_ns` are not thread-safe; `exec()` from a worker
thread caused silent failures and race conditions.

**Fix**: Override `aexecute()` to call `execute()` directly without thread dispatch.
Since Sage's agent runs inside a `nest_asyncio`-patched re-entrant loop on the main
kernel thread, this is safe and keeps `exec()` on the correct thread.

### Iteration 0.1.3 — IPython's `showtraceback()` Bypassed Capture

**Problem**: `ip.run_cell(code)` (the initial approach) called IPython's full cell
execution pipeline, including error formatting and `showtraceback()`. Tracebacks
appeared directly in the cell output even when `capture_output=True` was set, because
`showtraceback()` writes to the original `sys.stderr` before capture.

**Fix**: Switch from `run_cell()` to direct `exec(compiled, user_ns)` with our own
stderr capture. This bypasses IPython's cell pipeline entirely, giving us full control
over all output.

Also: matplotlib's inline backend auto-display caused figures to appear twice
(once from `plt.show()` in the script, once from IPython's inline hook cleanup).
Fixed by appending `plt.close('all')` to the executed code.

### Iterations 0.1.4–0.1.5 — stdout Capture Interfered with IPython Display

**Problem**: Replacing `sys.stdout` with `StringIO` caused IPython's display system
to detect a non-`OutStream` stdout and fall back to printing widget text reprs to
the buffer instead of sending ZeroMQ messages.

**Fix**: Abandon `run_cell()` with `capture_output=True`. Use raw `exec()` with
`sys.stdout` replaced by `StringIO` only during the `exec()` call, with full
restoration in `finally`. The display pipeline is patched separately (see 0.1.10).

### Iterations 0.1.6–0.1.8 — ipywidgets `Output` Widget as Container

**Problem**: Attempted to use an `ipywidgets.Output()` widget as a display container
(`with cell_out: exec(...)`). The Output widget's `__enter__`/`__exit__` context
manager captures `display()` calls by pushing the widget onto IPython's output stack.
Inside `loop.run_until_complete()`, this was a silent no-op — the stack push fails
because IPython's output capture requires the kernel to be in interactive cell
execution mode, which it is not during `run_until_complete()`.

**Diagnostic**: Log confirmed `cell_out.outputs` had `num outputs: 0` after execution.

**Fix**: Abandon the `Output` widget as a container. Use monkey-patching of `display()`
instead.

### Iterations 0.1.9–0.1.11 — `display_pub.publish` Stripped Widget MIME Types

**Problem**: First attempt at direct capture patched `ip.display_pub.publish`. The
captured outputs had only `text/plain` mime type — `application/vnd.jupyter.widget-view+json`
was absent. Investigation showed that IPython's publish pipeline strips widget-view
mime types when called outside the normal kernel execution context.

**Fix**: Bypass the publish pipeline entirely. Patch `IPython.display.display` (and
`IPython.core.display_functions.display` in IPython ≥ 8) to capture the widget objects
themselves, before they enter the publish pipeline. After `exec()`, queue the raw
Python widget objects into `user_ns["_sage_pending_displays"]`.

### Iteration 0.1.12 — `Output.append_display_data(dict)` Silently Dropped MIME Types

**Problem**: Attempting to use `cell_out.append_display_data(mime_bundle_dict)` to
re-inject captured widgets into an Output container. The method expects an IPython
`DisplayObject` (like `Markdown` or `HTML`), not a raw mime-bundle dict. When given
a dict, it silently discarded all mime types except `text/plain`.

**Fix** (abandoned in favor of 0.1.13 approach): Would have required building proper
`DisplayObject` wrappers. Instead, the Output container approach was dropped entirely.

### Iteration 0.1.13 — Wrapper Output Widget Had No Registered Comm

**Problem**: Wrapped captured widget objects inside a fresh `ipywidgets.Output()` to
batch them. The wrapper was created inside `_run_in_kernel()`, which runs during
`loop.run_until_complete()`. The wrapper's `comm_open` message could not be sent to
the frontend (ZeroMQ blocked by re-entrant loop). The frontend had no model for the
wrapper widget; it rendered as `Output(outputs=(...))` text repr.

**Fix**: Drop the wrapper entirely. Queue the raw widget objects directly in
`_sage_pending_displays` and `display()` them after `run_until_complete()` returns.
The inner widgets (Map, Dropdown, etc.) had their `comm_open` sent when they were
**created** (during `exec()`), not when `display()` is called. Their models are
already registered on the frontend; calling `display(widget)` from the post-loop
context only sends the `display_data` message that says "render model X at this
position."

### Iteration 0.1.14 — Root Cause: ipywidgets Not Pre-installed in Image

**Problem**: After all the backend fixes, widgets still rendered as text. The diagnostic
test (`widgets.IntSlider()` in a fresh cell) showed `IntSlider(value=0, max=10)` as
plain text. The Python package was installed but the JupyterLab frontend extension
(`@jupyter-widgets/jupyterlab-manager`) was not registered.

**Root cause**: `pip install --user ipywidgets` installs to `~/.local`, which Lab
scans at startup. But when installed on a running server (inside a notebook cell), Lab
does not rescan — the extension is never registered for the current session.

**Fix**: Pre-install in the Dockerfile as part of the image build. The extension is
registered when JupyterLab starts from the baked-in installation path.

---

## 5. The Complete Widget Rendering Pipeline

Understanding the full sequence from `exec()` to rendered widget:

```
1. exec(code, user_ns)
   │
   ├── Widget() constructor fires
   │   └── Widget.__init__() calls self.open()
   │       └── comm.open() sends comm_open ZeroMQ message → frontend
   │           (Frontend creates JavaScript model; comm registered)
   │           [This MUST happen during exec(), while loop.run_until_complete()
   │            is running — but comm_open is a fire-and-forget ZeroMQ pub message,
   │            which queues successfully even in re-entrant mode]
   │
   └── display(widget) fires inside skill script
       └── _capture_display(widget) appended to _captured_objs
           (NOT forwarded to IPython's publish pipeline)

2. _run_in_kernel() returns
   ├── _captured_objs queued into user_ns["_sage_pending_displays"]
   └── ExecuteResponse(stdout_text) returned to agent

3. Agent receives text output, continues running...

4. loop.run_until_complete() returns
   └── sage_magic.py:
       _pending = user_ns.pop("_sage_pending_displays", [])
       for widget in _pending:
           display(widget)
           │
           └── IPython.display.display() called on main thread, normal context
               └── display_pub.publish({
                       "application/vnd.jupyter.widget-view+json": {
                           "model_id": widget.model_id,  # already registered!
                           "version_major": 2,
                           "version_minor": 0
                       }
                   })
                   └── Frontend receives display_data message
                       └── Looks up model_id in comm registry
                           └── Renders widget ✓
```

The key insight: **widget creation** (step 1) and **widget display** (step 4) are
decoupled. The comm registration (creating the JS model) happens during `exec()`;
the display instruction (telling the frontend where to render it) happens after the
loop returns. Both halves of the protocol complete correctly.

---

## 6. What This Enables: The New Class of Skills

Before `KernelShellBackend`, all Sage skills were limited to:
- Text output analysis
- File creation (GeoJSON, CSV, PNG) — displayed post-run by `sage_magic.py`
- No interactivity; everything was a snapshot

After `KernelShellBackend`, skills can produce:

| Capability | Example | How |
|---|---|---|
| Interactive maps | leafmap/ipyleaflet map with draw tools | Widget comm + deferred display |
| Live dropdowns | Dataset selection after bbox draw | ipywidgets.Dropdown |
| Output widgets | Log/message panel that updates reactively | ipywidgets.Output |
| Shared kernel state | `USER_BBOX`, `USER_EPT_URL` set by skill, read by next cell | `exec()` writes to `user_ns` |
| Progress bars | tqdm.notebook, ipywidgets.FloatProgress | Widget comm |
| Interactive 3D | Plotly FigureWidget | Widget comm |
| Cross-cell state | Variables set by `%%ask` cells usable in plain code cells | `user_ns` persistence |

The USGS 3DEP LiDAR skill (Step 1) is the first demonstration of this class: an agent
draws a coverage map, installs a draw callback, and the user's bounding-box selection
writes `USER_BBOX` and `USER_EPT_URL` into the kernel namespace for subsequent cells
to use.

---

## 7. Limitations and Known Issues

### 7.1 No Edit/Delete for DrawControl Bbox

`ipyleaflet.DrawControl`'s edit and delete tools operate on shapes in its internal
`FeatureGroup` (managed by Leaflet.Draw on the JavaScript side). After
`target.clear()`, that FeatureGroup is empty, so the tools have nothing to act on.
The current workaround is a Python-side "Clear selection" button that explicitly
removes the bbox GeoJSON layer and resets state. A proper fix would require a way to
re-insert a shape into DrawControl's FeatureGroup from Python, which is not directly
supported by ipyleaflet's current API.

### 7.2 Debug Logging Still Present

`_run_in_kernel()` writes diagnostic entries to `/tmp/sage_debug.log` at every
invocation. This was critical during development to distinguish "code not reached"
from "code failed silently." It should be removed or made conditional on an environment
variable before production deployment.

### 7.3 `asyncio.to_thread()` Safety Boundary

The `aexecute()` override that calls `execute()` directly is safe because Sage's agent
runs on the main kernel thread. If DeepAgents is ever used in a context where the
agent runs on a worker thread (e.g., a different asyncio event loop architecture),
`exec(code, user_ns)` from a worker thread will race with IPython's main thread and
could corrupt notebook state. The current design is tightly coupled to Sage's
single-threaded execution model.

### 7.4 matplotlib Figure Scope

The `plt.close('all')` appended to every executed script prevents double-display but
also destroys any `Figure` objects that the skill code may have stored in `user_ns`
for later use. This is acceptable for skills that display figures as side effects but
would be wrong for skills that need to return a Figure object to subsequent cells.

### 7.5 Nested Imports of `display`

The display interception patches `IPython.display.display` and
`IPython.core.display_functions.display`. Some third-party libraries cache a local
reference to `display` at import time:
```python
from IPython.display import display  # cached at import, not affected by our patch
```
In these cases, widget objects passed to the cached `display` reference bypass our
capture and may trigger display attempts during `run_until_complete()`. Known affected
library: none confirmed, but this is a latent risk.

---

## 8. File Reference

| File | Location | Description |
|---|---|---|
| `sage_kernel_backend.py` | `sage/sage_kernel_backend.py` | `KernelShellBackend` class |
| `sage_magic.py` | `sage/sage_magic.py` | `%%ask` magic; agent loop; deferred display flush |
| `Dockerfile` | `sage/Dockerfile` | Image build; pre-installs widget packages |
| `apply_sage_patch.py` | `sage/apply_sage_patch.py` | Patches `detect_provider()` in deepagents config |
| `sage_skills/usgs-lidar/SKILL.md` | `sage/sage_skills/usgs-lidar/SKILL.md` | First widget-producing skill |
| `CHANGELOG.md` | `sage/CHANGELOG.md` | Per-iteration development log |

---

## 9. Related Work

- **DeepAgents / deepagents-cli**: The LangChain-based agent framework Sage wraps.
  `LocalShellBackend` is the default backend; `KernelShellBackend` extends it.
- **ipywidgets comm protocol**: The ZeroMQ comm protocol specification that underlies
  all Jupyter widget rendering. See ipywidgets documentation §"Low Level Widget
  Explanation."
- **nest_asyncio**: Enables re-entrant `loop.run_until_complete()` calls inside
  Jupyter's existing event loop. Required for running async agent code from a
  synchronous IPython magic command.
- **JupyterHub / JupyterLab**: The deployment platform. JupyterLab's prebuilt
  extension system determines when widget frontend extensions are registered.

---

*Document version: kernel-0.1.18 — 2026-04-22*
