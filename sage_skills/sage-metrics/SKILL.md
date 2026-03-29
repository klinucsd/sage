---
name: sage-metrics
description: Analyze Sage execution logs to generate metrics summaries and charts. Use when the user asks to summarize, analyze, or visualize execution statistics for one or more Sage notebooks. Accepts notebook names (with or without .ipynb), full notebook paths, or a directory. Produces per-cell tables and bar charts of elapsed time and tool call counts saved to the current notebook output folder.
---

# Sage Metrics Skill

## Purpose

Reads `.sage_run.jsonl` execution logs produced by Sage and generates:
- A per-cell summary table (prompt, elapsed time, tool calls)
- Bar charts of elapsed time and execute call counts per cell
- A cross-notebook comparison table when multiple notebooks are analyzed

All outputs are saved to the current notebook's output directory (`SAGE_OUTPUT_DIR`).

## CRITICAL: Do Not Search for Log Files

**Never** use `find`, `ls`, or glob to locate log files. Construct the path directly
from the notebook name using the formula below, then read it. If the file does not
exist at the computed path, report that it was not found and continue.

## Log File Location Formula

For a notebook named `earthquake_gnss` (with or without `.ipynb`), the log file is:
```
{parent_dir}/_{notebook_stem}_sage_/.sage_run.jsonl
```

The log filename is always **`.sage_run.jsonl`** (hidden file, starts with a dot).

Examples:
- `earthquake_gnss` or `earthquake_gnss.ipynb` → `{CWD}/_earthquake_gnss_sage_/.sage_run.jsonl`
- `flood_impacts` or `flood_impacts.ipynb`     → `{CWD}/_flood_impacts_sage_/.sage_run.jsonl`
- `/home/jovyan/work/Sage/my_notebook.ipynb`   → `/home/jovyan/work/Sage/_my_notebook_sage_/.sage_run.jsonl`

## Resolving Notebook Inputs

```python
import os
from pathlib import Path

def resolve_log_path(notebook_input, cwd=None):
    """Convert any notebook input form to its .sage_run.jsonl path."""
    if cwd is None:
        cwd = Path(os.getcwd())
    p = Path(notebook_input)
    # Strip .ipynb extension if present to get the stem
    stem = p.stem if p.suffix == '.ipynb' else p.name
    if p.is_absolute() or p.parent != Path('.'):
        # Full path given — use its parent directory
        parent = p.parent if p.suffix == '.ipynb' else p
        if not p.suffix:
            # It's a directory — glob for all logs inside
            return sorted(parent.glob('_*_sage_/.sage_run.jsonl'))
    else:
        parent = cwd
    return parent / f'_{stem}_sage_' / '.sage_run.jsonl'
```

**Input forms:**
- **Bare stem** (`earthquake_gnss`) → resolved relative to CWD
- **Filename** (`earthquake_gnss.ipynb`) → resolved relative to CWD
- **Full path** (`/home/jovyan/work/Sage/my_notebook.ipynb`) → resolved from its parent
- **Directory** (`/home/jovyan/work/Sage/`) → glob `_*_sage_/.sage_run.jsonl` inside it

## Log File Schema

Each line is a JSON object:
```json
{
  "timestamp": "2026-03-29T16:54:05.238819+00:00",
  "prompt": "Find earthquake events with magnitude > 5...",
  "elapsed_sec": 48.5,
  "tool_calls": {
    "execute": 4,
    "read_file": 3,
    "write_file": 2,
    "http_request": 1
  },
  "total_tool_calls": 10
}
```

Fields:
- `timestamp` — ISO 8601 UTC timestamp of when the cell completed
- `prompt` — first 200 characters of the user's `%%ask` prompt
- `elapsed_sec` — wall-clock seconds from prompt submission to completion
- `tool_calls` — dict mapping tool name to invocation count for that cell
- `total_tool_calls` — sum of all tool_calls values

The `execute` count is the best proxy for self-correction iterations: each code
execution that follows a failure and retry increments this count.

## How to Generate the Metrics

### Step 1: Parse log files

```python
import json

def load_log(log_path):
    entries = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries
```

### Step 2: Per-notebook summary table

For each log entry produce a row:
- **Notebook** — notebook stem name
- **Cell** — sequential cell number (1, 2, 3…)
- **Prompt** — first 60 characters of the prompt, truncated with "…"
- **Elapsed (s)** — `elapsed_sec`
- **Execute calls** — `tool_calls.get("execute", 0)`
- **Total tool calls** — `total_tool_calls`

Save as CSV to `SAGE_OUTPUT_DIR/sage_metrics_table.csv`.

### Step 3: Charts

Produce a single figure with two subplots side by side (figsize=(14, 6), dpi=150):
- **Left**: horizontal bar chart of elapsed time per cell; y-axis labels are
  `{notebook_name} / cell {n}`; use distinct colors per notebook
- **Right**: horizontal bar chart of execute call counts per cell; same y-axis labels

Call `plt.tight_layout()` before saving.
Save as PNG to `SAGE_OUTPUT_DIR/sage_metrics_charts.png`.

### Step 4: Cross-notebook comparison (when multiple notebooks)

Produce a summary table with one row per notebook:
- **Notebook** — notebook stem name
- **Cells** — number of `%%ask` cells
- **Total time (s)** — sum of elapsed_sec
- **Avg time/cell (s)** — mean elapsed_sec, rounded to 1 decimal
- **Total execute calls** — sum of execute counts across all cells
- **Total tool calls** — sum of total_tool_calls across all cells

Save as CSV to `SAGE_OUTPUT_DIR/sage_metrics_summary.csv`.

## Output Files

Embed all outputs inline in the final report:
```
![Sage Metrics Charts](SAGE_OUTPUT_DIR/sage_metrics_charts.png)
```

## Example Prompts This Skill Handles

- `Generate a metrics summary for earthquake_gnss`
- `Generate a metrics summary for earthquake_gnss.ipynb`
- `Generate a metrics summary for /home/jovyan/work/Sage/my_notebook.ipynb`
- `Generate metrics for earthquake_gnss, flood_impacts, and skills_manage`
- `Analyze execution logs for all notebooks under /home/jovyan/work/Sage/`
- `Compare execution statistics across all three demo notebooks`
