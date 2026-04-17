#!/usr/bin/env python3
"""
Sage Workflow Diagram Generator v2
Reads the notebook (.ipynb), .sage_cells.json, and per-cell Python scripts.
Outputs workflow_diagram.workflow.html (self-contained cytoscape.js
interactive diagram with compound cell nodes).

Usage: python workflow_generator.py <output_dir>
"""
import ast
import json
import os
import re
import sys
import tokenize
from io import BytesIO
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# stdlib + common utility modules we don't want to show as "uses:" tags
_COMMON_UTILS = {
    'os', 'sys', 'json', 're', 'io', 'time', 'datetime', 'math', 'random',
    'pathlib', 'typing', 'collections', 'functools', 'itertools', 'warnings',
    'copy', 'ast', 'tokenize', 'subprocess', 'shutil', 'glob', 'tempfile',
    'numpy', 'pandas', 'matplotlib', 'seaborn',
}


# ---------------------------------------------------------------------------
# Notebook reading
# ---------------------------------------------------------------------------

def find_notebook(output_dir):
    """Derive the notebook path from SAGE_OUTPUT_DIR convention.

    Pattern: /path/to/_{stem}_sage_/ → /path/to/{stem}.ipynb
    """
    out_path = Path(output_dir)
    name = out_path.name
    if name.startswith('_') and name.endswith('_sage_'):
        stem = name[1:-len('_sage_')]
        candidate = out_path.parent / f"{stem}.ipynb"
        if candidate.exists():
            return candidate
    return None


def read_notebook_cells(notebook_path):
    """Return %%ask/%ask cells in execution order.

    Each cell: {'id', 'prompt', 'outputs_html'}.
    """
    if not notebook_path or not notebook_path.exists():
        return []
    try:
        nb = json.loads(notebook_path.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"Warning: could not parse notebook: {e}", file=sys.stderr)
        return []

    cells = []
    for cell in nb.get('cells', []):
        if cell.get('cell_type') != 'code':
            continue
        src_lines = cell.get('source', [])
        if isinstance(src_lines, str):
            src_lines = [src_lines]
        source = ''.join(src_lines).strip()
        if not source:
            continue

        # Only %%ask (cell magic) or %ask (line magic) cells
        first_line = source.split('\n', 1)[0].strip()
        if first_line.startswith('%%ask'):
            prompt = '\n'.join(source.split('\n')[1:]).strip()
        elif first_line.startswith('%ask'):
            prompt = first_line[len('%ask'):].strip()
        else:
            continue

        # Collect all HTML output fragments
        html_chunks = []
        for out in cell.get('outputs', []):
            otype = out.get('output_type', '')
            if otype in ('display_data', 'execute_result'):
                data = out.get('data', {})
                html = data.get('text/html', '')
                if isinstance(html, list):
                    html = ''.join(html)
                if html:
                    html_chunks.append(html)

        cells.append({
            'id': cell.get('id', ''),
            'prompt': prompt,
            'outputs_html': '\n'.join(html_chunks),
        })

    return cells


# ---------------------------------------------------------------------------
# Cell output parsing (skill refs + execution commands)
# ---------------------------------------------------------------------------

# Matches: <b>read_file</b> — Reading: <code>/.../skills/NAME/SKILL.md</code>
_SKILL_RE = re.compile(
    r'<b>read_file</b>[^<]*?Reading:\s*<code>([^<]*skills/([^/<]+)/SKILL\.md)</code>',
    re.IGNORECASE,
)

# Matches: <b>execute</b> — Executing: <code>COMMAND</code>
_EXEC_RE = re.compile(
    r'<b>execute</b>[^<]*?Executing:\s*<code>([^<]+)</code>',
    re.IGNORECASE,
)


def parse_outputs(outputs_html):
    """Extract skills referenced and execute commands from cell output HTML."""
    skills = []
    seen_skills = set()
    for m in _SKILL_RE.finditer(outputs_html):
        name = m.group(2)
        if name not in seen_skills:
            seen_skills.add(name)
            skills.append(name)

    commands = [m.group(1) for m in _EXEC_RE.finditer(outputs_html)]
    return skills, commands


def detect_runners(commands):
    """Reduce raw execute commands to a small set of runner labels."""
    runners = []
    seen = set()
    for cmd in commands:
        cmd = cmd.strip()
        # Truncated HTML — just look at the start
        if re.match(r'^fdp\s+(run\s+)?python\b', cmd):
            label = 'fdp run python'
        elif re.search(r'\bpython[0-9.]*\s+[^\s]*\.py', cmd):
            label = 'python'
        else:
            # First token only
            first = cmd.split()[0] if cmd else ''
            if not first or first.startswith('/'):
                continue
            label = first
        if label not in seen:
            seen.add(label)
            runners.append(label)
    return runners


# ---------------------------------------------------------------------------
# Python script analysis
# ---------------------------------------------------------------------------

def extract_docstring(script_text):
    try:
        tree = ast.parse(script_text)
        return ast.get_docstring(tree)
    except Exception:
        return None


def extract_imports(script_text):
    """Return non-stdlib, non-common-utility import module names."""
    try:
        tree = ast.parse(script_text)
    except SyntaxError:
        return []
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split('.')[0])
    return sorted(m for m in modules if m and m not in _COMMON_UTILS)


def extract_comments(script_text, max_indent=8, max_lines=6):
    """Extract top-level comments as sub-step descriptions.

    'Top level' = column ≤ max_indent (module scope or inside main()).
    Filters trivial/inline/code-tag comments.
    """
    comments = []
    try:
        tokens = tokenize.tokenize(BytesIO(script_text.encode('utf-8')).readline)
        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            col = tok.start[1]
            if col > max_indent:
                continue  # too deeply nested
            text = tok.string.lstrip('#').strip()
            if len(text) < 6:
                continue  # too short to be descriptive
            low = text.lower()
            if any(skip in low for skip in (
                'noqa', 'type:', 'pylint', 'todo', 'fixme', 'xxx',
                'coding:', 'mypy:', '!/usr',
            )):
                continue
            if len(text) > 70:
                text = text[:67] + '…'
            comments.append(text)
    except (tokenize.TokenizeError, IndentationError):
        pass

    # Dedupe case-insensitively, preserving order
    seen = set()
    unique = []
    for c in comments:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique[:max_lines]


def analyze_scripts(file_paths):
    """Aggregate script info across all .py files for a cell."""
    scripts = [Path(p) for p in file_paths if str(p).endswith('.py')]
    docstring = None
    sub_steps = []
    imports = set()
    for script in scripts:
        try:
            text = script.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        if docstring is None:
            ds = extract_docstring(text)
            if ds:
                docstring = ds.split('\n')[0].strip()
        sub_steps.extend(extract_comments(text))
        imports.update(extract_imports(text))

    # Dedupe sub_steps across all scripts
    seen = set()
    unique_steps = []
    for s in sub_steps:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            unique_steps.append(s)

    return {
        'docstring': docstring,
        'sub_steps': unique_steps[:6],
        'imports': sorted(imports),
        'script_count': len(scripts),
    }


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def wrap_text(text, width, max_lines=3):
    """Word-wrap text to width chars, max_lines lines. Truncate with ellipsis."""
    if not text:
        return ''
    words = text.replace('\n', ' ').split()
    lines, cur, cur_len = [], [], 0
    for w in words:
        added = len(w) + (1 if cur else 0)
        if cur_len + added > width and cur:
            lines.append(' '.join(cur))
            if len(lines) == max_lines:
                lines[-1] = lines[-1][:width - 1] + '…'
                return '\n'.join(lines)
            cur, cur_len = [w], len(w)
        else:
            cur.append(w)
            cur_len += added
    if cur and len(lines) < max_lines:
        lines.append(' '.join(cur))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f1e; font-family: Helvetica, Arial, sans-serif; overflow: hidden; }
  #cy { width: 100%; height: 100vh; }
</style>
</head>
<body>
<div id="cy"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.29.2/cytoscape.min.js"></script>
<script>
var elements = __ELEMENTS__;
var cy = cytoscape({
  container: document.getElementById('cy'),
  elements: elements,
  layout: {
    name: 'cose',
    idealEdgeLength: 100,
    nodeOverlap: 20,
    refresh: 20,
    fit: true,
    padding: 30,
    randomize: false,
    componentSpacing: 120,
    nodeRepulsion: 450000,
    edgeElasticity: 100,
    nestingFactor: 5,
    gravity: 80,
    numIter: 1500,
    initialTemp: 200,
    coolingFactor: 0.95,
    minTemp: 1.0
  },
  style: [
    {
      selector: 'node[type="cell_container"]',
      style: {
        'background-color': '#2d1a4e',
        'background-opacity': 0.25,
        'border-color': '#7F77DD',
        'border-width': 2,
        'label': 'data(label)',
        'color': '#CECBF6',
        'font-size': '11px',
        'font-weight': 'bold',
        'text-wrap': 'wrap',
        'text-max-width': '260px',
        'text-valign': 'top',
        'text-halign': 'center',
        'text-margin-y': -8,
        'padding': '32px',
        'shape': 'round-rectangle',
      }
    },
    {
      selector: 'node[type="substep"]',
      style: {
        'background-color': '#2d1a4e',
        'border-color': '#7F77DD',
        'border-width': 2,
        'label': 'data(label)',
        'color': '#CECBF6',
        'font-size': '10px',
        'text-wrap': 'wrap',
        'text-max-width': '140px',
        'shape': 'round-rectangle',
        'width': 150,
        'height': 48,
        'text-valign': 'center',
        'text-halign': 'center',
      }
    },
    {
      selector: 'node[type="output_file"]',
      style: {
        'background-color': '#1a3a2a',
        'border-color': '#4a9a5a',
        'border-width': 2,
        'label': 'data(label)',
        'color': '#80d090',
        'font-size': '9px',
        'text-wrap': 'wrap',
        'text-max-width': '130px',
        'shape': 'round-rectangle',
        'width': 140,
        'height': 40,
        'text-valign': 'center',
        'text-halign': 'center',
      }
    },
    {
      selector: 'edge',
      style: {
        'line-color': '#534AB7',
        'target-arrow-color': '#7F77DD',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
        'width': 2,
      }
    },
    {
      selector: 'edge[type="cell_flow"]',
      style: {
        'line-color': '#8F87DD',
        'target-arrow-color': '#AFA7EE',
        'width': 3,
      }
    },
  ],
  userZoomingEnabled: true,
  userPanningEnabled: true,
});
setTimeout(function() { cy.fit(40); }, 80);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Element builder
# ---------------------------------------------------------------------------

def build_elements(cell_data):
    """Build a list of cytoscape elements (nodes + edges)."""
    elements = []

    for cell in cell_data:
        ci = cell['index']
        cid = f"cell_{ci}"

        # Container label: prompt (wrapped) + metadata lines
        prompt_display = wrap_text(cell['prompt'], 36, max_lines=3)
        title = f"Cell {ci}: {prompt_display}"

        meta_parts = []
        if cell['skills']:
            meta_parts.append(f"skill: {', '.join(cell['skills'])}")
        if cell['runners']:
            meta_parts.append(f"runner: {', '.join(cell['runners'])}")
        if cell['imports']:
            meta_parts.append(f"uses: {', '.join(cell['imports'][:4])}")
        meta_block = '\n'.join(meta_parts)

        container_label = title
        if meta_block:
            container_label += '\n\n' + meta_block

        elements.append({
            'data': {
                'id': cid,
                'label': container_label,
                'type': 'cell_container',
            }
        })

        # Sub-steps — prefer comments, fall back to docstring, then "Execute"
        sub_steps = cell['sub_steps'][:]
        if not sub_steps and cell['docstring']:
            sub_steps = [cell['docstring'][:60]]
        if not sub_steps:
            if cell['output_files']:
                sub_steps = ['Execute script']
            else:
                sub_steps = ['(no generated files)']

        prev_sid = None
        for j, step in enumerate(sub_steps):
            sid = f"{cid}_s{j+1}"
            elements.append({
                'data': {
                    'id': sid,
                    'label': step,
                    'parent': cid,
                    'type': 'substep',
                }
            })
            if prev_sid:
                elements.append({'data': {'source': prev_sid, 'target': sid}})
            prev_sid = sid

        # Output files as green nodes inside the same container
        last_step_id = prev_sid
        for k, fname in enumerate(cell['output_files'][:6]):
            fid = f"{cid}_f{k+1}"
            elements.append({
                'data': {
                    'id': fid,
                    'label': fname,
                    'parent': cid,
                    'type': 'output_file',
                }
            })
            if last_step_id:
                elements.append({'data': {'source': last_step_id, 'target': fid}})

    # Cell-to-cell execution flow edges (between containers)
    for i in range(len(cell_data) - 1):
        ci1 = cell_data[i]['index']
        ci2 = cell_data[i + 1]['index']
        elements.append({
            'data': {
                'source': f"cell_{ci1}",
                'target': f"cell_{ci2}",
                'type': 'cell_flow',
            }
        })

    return elements


# ---------------------------------------------------------------------------
# Main generate
# ---------------------------------------------------------------------------

def generate(output_dir):
    output_dir = Path(output_dir)
    registry_path = output_dir / '.sage_cells.json'

    # Load cell registry: cell_id → [files]
    cell_registry = {}
    if registry_path.exists():
        try:
            cell_registry = json.loads(registry_path.read_text())
        except Exception:
            pass

    # Read notebook for execution order and prompts
    notebook_path = find_notebook(output_dir)
    notebook_cells = read_notebook_cells(notebook_path)

    # Filter to cells that actually produced files (ignore cells the user asked
    # but which produced no tracked output — e.g. %reset, explanation-only cells)
    cells_with_files = []
    for cell in notebook_cells:
        cid = cell.get('id', '')
        files = cell_registry.get(cid, [])
        if files:
            cells_with_files.append((cell, files))

    # If no notebook or nothing matched, fall back to registry order alone
    if not cells_with_files:
        if cell_registry:
            for i, (rcid, files) in enumerate(cell_registry.items()):
                cells_with_files.append((
                    {'id': rcid, 'prompt': f'Cell output #{i+1}', 'outputs_html': ''},
                    files,
                ))
        else:
            print("No cells found — no notebook and no .sage_cells.json", file=sys.stderr)
            sys.exit(1)

    # Build per-cell data
    cell_data = []
    for i, (cell, files) in enumerate(cells_with_files):
        skills, commands = parse_outputs(cell['outputs_html'])
        runners = detect_runners(commands)
        info = analyze_scripts(files)
        output_files = sorted({
            Path(f).name for f in files
            if not str(f).endswith('.py')
        })

        cell_data.append({
            'index': i + 1,
            'id': cell['id'],
            'prompt': cell['prompt'],
            'skills': skills,
            'runners': runners,
            'imports': info['imports'],
            'sub_steps': info['sub_steps'],
            'docstring': info['docstring'],
            'output_files': output_files,
        })

    elements = build_elements(cell_data)
    elements_json = json.dumps(elements, indent=2)

    html = HTML_TEMPLATE.replace('__ELEMENTS__', elements_json)
    out_path = output_dir / 'workflow_diagram.workflow.html'
    out_path.write_text(html, encoding='utf-8')
    print(f"Saved: {out_path}")
    print(f"Cells: {len(cell_data)}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        output_dir = os.environ.get('SAGE_OUTPUT_DIR', '.')
    else:
        output_dir = sys.argv[1]
    generate(output_dir)
