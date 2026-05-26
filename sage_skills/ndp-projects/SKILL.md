---
name: ndp-projects
description: Find, inspect, or download an NDP project. A project is a container that groups multiple workspaces. Use when the user mentions a project, wants to list the projects they have access to, summarize the workspaces in a project, or download all files in the workspaces of a project.
---

# NDP Projects Skill

## Description

This skill inspects **projects** on the NDP Workspace API. A **project** is a
container that groups multiple **workspaces** under a single `entity_id`.
One project has many workspaces; a workspace always belongs to exactly one
project.

Use this skill when the user asks about a *project* — e.g. *"list workspaces
in the Fire Risk Modeling project"*, *"show me the datasets attached to
project X"*, *"what reports are in the Deep Review project"*. For a
single-user workspace listing without project context, use the
`ndp-workspaces` skill instead.

The Swagger UI for the workspace API is at
<https://nationaldataplatform.org/workspaces-api/v1/openapi.json>.

## Names vs. IDs

**Users only know names.** They will say *"the Deep Review project"* or
*"the FiSci workspace"*. Project IDs and workspace IDs are internal UUIDs
the user almost never sees. **Always start from the name** and look the ID
up if needed.

### Private projects and access

The NDP Workspace API does not expose a public "find project by name"
endpoint. Project name → ID resolution goes through
`/read_project_by_user`, which **only returns projects the caller is a
member of**. Most NDP projects are private — created and shared explicitly
among team members.

Consequences:

- If the user is a member of the project they asked about, it will appear
  in the `read_project_by_user` response and the name-substring filter on
  `.title` will find it.
- If the user is *not* a member, no name-based discovery is possible. The
  request will look identical to a typo. In that case, report the project
  titles you *did* see and tell the user: *"if this is a private project
  you're not a member of, ask the project owner to add your NDP account."*
- Never invent a `project_id`. Never call `read_project/{id}` with a UUID
  the user did not explicitly provide.

## Prerequisites

The following environment variables must be set (same as `ndp-workspaces`):

- `WORKSPACE_API_URL` — base URL for the NDP Workspace API,
  e.g. `https://nationaldataplatform.org/workspaces-api`
- `ACCESS_TOKEN` — Bearer token for authentication

## Critical: workspace creation context (`where_wkspc_created`)

Projects and workspaces on NDP are partitioned by **context**:

- `NDP` — the default National Data Platform context
- `WSTC` — Workspace Technology and Computing context

**Every API call below takes a `where_wkspc_created` query parameter.** Omit
it and the API returns `{"error": "Project not found"}` even for projects
that exist. If the user doesn't say which context, try both and merge — see
Example 4.

## API Endpoints

| Purpose                                         | Method | Path                                                                  |
|-------------------------------------------------|--------|-----------------------------------------------------------------------|
| List projects the caller is a member of         | GET    | `/read_project_by_user?where_wkspc_created={context}`                 |
| Fetch one project's metadata + workspace list   | GET    | `/read_project/{project_id}?where_wkspc_created={context}`            |
| Fetch full workspace payloads (datasets, etc.)  | GET    | `/workspace/read_workspaces_by_user?where_wkspc_created={context}`    |

**Authentication header (every request):**

```
Authorization: Bearer {ACCESS_TOKEN}
```

**Do not use** `GET /workspace/{workspace_id}` — it returns empty `datasets`
arrays. The correct field is `parent_datasets`, and it only appears in the
`read_workspaces_by_user` response.

## Three-step resolution flow

A typical "tell me about project X" request follows these three calls.
None of the responses can substitute for another — each provides data the
others lack.

### Progress messages — what to log during resolution

The three resolution calls (project_by_user → read_project → read_workspaces_by_user) typically take a few seconds total. If a download follows, emit one progress line per call so the reviewer sees the work isn't stalled. **Log only what is meaningful to a reviewer**, in domain terms.

#### How to derive `N`, the number of workspaces

There is exactly ONE workspace count to report to the user — the number of **child workspace entries** in the filtered `read_workspaces_by_user` response (entries with non-null `parent_workspace_id`). This equals the number of submissions the reviewer sees. Use this single number consistently in every progress line and in any summary.

NOTE: do NOT additionally require `parent_datasets` to be non-empty. A submission may legitimately have no linked catalog datasets while still carrying significant content in `additional_resources` and `repository_links` — those submissions are real and must be downloaded. Filtering by `parent_datasets` silently drops them.

Do **NOT** invent alternative counts. Specifically:

- Do NOT report the size of the `workspace_id` set built from `entity.subgroups[].workspaces[]`. That set contains both parent and child IDs (2 per submission) — reporting it as the "number of workspaces" is misleading (e.g. *"Found 12 workspace IDs"* for 6 actual workspaces).
- Do NOT divide any count by 2 to "de-dupe pairs". `read_workspaces_by_user` already returns one entry per workspace; dividing produces a wrong number (e.g. 6 / 2 = 3 ≠ 6).
- Do NOT report the count before filtering, or before `download_project` derives `children`.

The progress messages are exactly:

```python
_sage_progress(f"Resolving project: {PROJECT_NAME}")
# call read_project_by_user, find the project_id

_sage_progress(f"Found project: {project_title}")
# call read_project/{project_id}, then call read_workspaces_by_user,
# then filter children = [ws for ws in project_workspaces if
# ws.get("parent_workspace_id")]

_sage_progress(f"Loading {len(children)} workspaces…")
# now call download_project(...), which emits its own final summary line.
```

`download_project()` already emits a final `"Project download complete: …"` line when it finishes. Do NOT write an additional "Download Summary" block in `main()` — it just duplicates information and risks introducing a second, inconsistent count.

### Step 1: Resolve project name → project_id

```bash
CONTEXT="WSTC"   # ask the user if unknown; try both per Example 4
PROJECT_NAME="Deep Review"

curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
  | jq --arg n "$PROJECT_NAME" '
      [.[] | select(.title | ascii_downcase | contains($n | ascii_downcase))]
    '
```

The response is an array of `{project_id, title, entity_id, ...}` objects.
Filter by case-insensitive substring on **`.title`** — there is no
`project_name` field on this endpoint.

If the filter returns zero, list every `.title` available so the user can
correct their query:

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
  | jq -r '.[].title'
```

### Step 2: Fetch project metadata + workspace list

`read_project/{project_id}` returns the project's metadata plus its
workspaces under `.entity.subgroups[].workspaces[]`. Each workspace there
has **two IDs** — `workspace_id` (the parent) and `child_workspace_id`
(the user-facing child).

```bash
PROJECT_ID="<from step 1>"

curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project/$PROJECT_ID?where_wkspc_created=$CONTEXT" \
  -o project.json

# Project metadata: title, description, members
jq '{title, description, status, members: [.entity.subgroups[].users[]?.email]}' project.json

# Workspaces in this project (just names + IDs — no contents yet)
jq '[.entity.subgroups[].workspaces[] | {workspace_name, workspace_id, child_workspace_id}]' project.json
```

### Step 3: Load full workspace contents (datasets, additional_resources)

`read_project/{project_id}` does **not** include workspace contents — only
their names and IDs. To get `parent_datasets` and `additional_resources`,
call `read_workspaces_by_user` and filter by the workspace IDs from Step 2.

The reliable filter is membership in the project's `(parent + child)` ID
set, **not** name matching:

```bash
# Collect every workspace ID (parent + child) that belongs to this project
PROJECT_WS_IDS=$(jq -r '
  [.entity.subgroups[].workspaces[]
   | (.workspace_id, .child_workspace_id)
   | select(. != null)]
  | unique | .[]
' project.json)

curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_by_user?where_wkspc_created=$CONTEXT" \
  -o all_workspaces.json

# Filter to just this project's workspaces, with full payloads
jq --argjson ids "$(printf '%s\n' $PROJECT_WS_IDS | jq -R . | jq -s .)" '
  [.[] | select(.workspace_id as $id | $ids | index($id))
       | {workspace_name,
          workspace_id,
          datasets: (.parent_datasets // [] | length),
          resources: (.additional_resources // [] | length)}]
' all_workspaces.json
```

## Workspace Structure (parent/child pairs)

Each workspace is stored as a parent/child pair. The project's
`entity.subgroups[].workspaces[]` list gives you the parent ID
(`workspace_id`) and the user-facing child ID (`child_workspace_id`) for
each. The full payload in `read_workspaces_by_user` will appear under
**both** IDs (one entry for the parent, one for the child).

| Field                   | Where it lives             | Notes                                |
|-------------------------|----------------------------|--------------------------------------|
| `datasets`              | child workspace            | Always empty `[]` — do not use       |
| `parent_datasets`       | child workspace            | The actual NDP dataset records       |
| `additional_resources`  | child workspace            | Google Drive / GitHub / other links  |
| `repository_links`      | child workspace            | GitHub repo links (if any)           |
| `entity_id`             | both                       | Same value for every workspace in a project |
| `parent_workspace_id`   | child only                 | Points back to the parent            |
| `child_workspace_id`    | parent only                | Points forward to the child          |

To dedupe parent/child duplicates when showing results, prefer the child
entry (the one with non-null `parent_workspace_id`).

## Examples

### Example 1: List the workspaces in a named project

**User Request:** *"List the workspaces in the Deep Review project on WSTC."*

```bash
CONTEXT="WSTC"
PROJECT_NAME="Deep Review"

# Step 1: name → project_id
PROJECT_ID=$(curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
  | jq -r --arg n "$PROJECT_NAME" '
      [.[] | select(.title | ascii_downcase | contains($n | ascii_downcase))]
      | .[0].project_id
    ')

# Step 2: list workspaces from the project entity
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project/$PROJECT_ID?where_wkspc_created=$CONTEXT" \
  | jq '[.entity.subgroups[].workspaces[] | .workspace_name]'
```

### Example 2: Inspect resources in a named workspace inside a project

**User Request:** *"Show me the Google Drive resources in the FiSci
workspace of the Deep Review project."*

```bash
CONTEXT="WSTC"
PROJECT_NAME="Deep Review"
WORKSPACE_NAME="FiSci"

# Step 1: project_id
PROJECT_ID=$(curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
  | jq -r --arg n "$PROJECT_NAME" '
      [.[] | select(.title | ascii_downcase | contains($n | ascii_downcase))]
      | .[0].project_id
    ')

# Step 2: gather the project's workspace IDs (parent + child)
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project/$PROJECT_ID?where_wkspc_created=$CONTEXT" \
  -o project.json

# Step 3: full workspace data + filter to the named one
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_by_user?where_wkspc_created=$CONTEXT" \
  | jq --slurpfile p project.json --arg w "$WORKSPACE_NAME" '
      ($p[0].entity.subgroups[].workspaces[]
        | (.workspace_id, .child_workspace_id) | select(. != null)) as $pid
      | [.[] | select(.workspace_id == $pid)]
      | map(select(.workspace_name | ascii_downcase | contains($w | ascii_downcase)))
      | .[]
      | (.additional_resources // [])
      | map(select(.resource_url | test("drive.google.com|docs.google.com")))
      | .[] | {label: .information, url: .resource_url}
    '
```

### Example 3: Summarize all projects the user has access to

**User Request:** *"What projects can I see on NDP?"* (no specific name)

```bash
for CONTEXT in NDP WSTC; do
  echo "=== $CONTEXT ==="
  curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
    "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
    | jq -r '.[] | "\(.title)   [\(.project_id)]"'
done
```

### Example 4: Project name given, context unknown

```bash
PROJECT_NAME="Fire Risk Modeling"

for CONTEXT in NDP WSTC; do
  count=$(curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
    "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
    | jq --arg n "$PROJECT_NAME" '
        [.[] | select(.title | ascii_downcase | contains($n | ascii_downcase))] | length
      ')
  echo "$CONTEXT: $count matching project(s)"
done
```

### Example 5: Detailed report — workspaces + dataset counts + resource counts

**User Request:** *"Give me a summary of the Deep Review project — what's
inside each workspace?"*

```bash
CONTEXT="WSTC"
PROJECT_NAME="Deep Review"

PROJECT_ID=$(curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
  | jq -r --arg n "$PROJECT_NAME" '
      [.[] | select(.title | ascii_downcase | contains($n | ascii_downcase))]
      | .[0].project_id
    ')

curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/read_project/$PROJECT_ID?where_wkspc_created=$CONTEXT" \
  -o project.json

curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_by_user?where_wkspc_created=$CONTEXT" \
  | jq --slurpfile p project.json '
      ([$p[0].entity.subgroups[].workspaces[]
        | (.workspace_id, .child_workspace_id)
        | select(. != null)] | unique) as $ids
      | [.[] | select(.workspace_id as $id | $ids | index($id))
             | select((.parent_datasets // []) | length > 0
                      or (.additional_resources // []) | length > 0)
             | {name: .workspace_name,
                catalog_assets: (.parent_datasets // [] | length),
                additional_resources: (.additional_resources // [] | length),
                repositories: ((.repository_links // []) + (.parent_repository_links // []) | length)}]
    '
```

## Downloading Project Resources

This section covers requests like *"download all files in all workspaces in
this project"* or *"save the entire Deep Review project to disk"*. The
project skill iterates over every workspace in the project and applies the
per-workspace download logic.

### Folder layout

`SAGE_OUTPUT_DIR` is the root. The **top folder is the sanitized project
title**, and each workspace is a subfolder underneath it.

```
{SAGE_OUTPUT_DIR}/
  <sanitized_project_title>/
    <sanitized_workspace_name_1>/
      catalog_assets/<sanitized_dataset_title>/<filename>      # CKAN dataset files
      additional_resources/<filename>                          # extras (PDFs, READMEs, etc.)
      repositories/<sanitized_repo_name>/                      # cloned git repos
      _drive_urls_to_download_later.txt
      _arcgis_endpoints.txt
      _unhandled_urls.txt
      _manifest.json
    <sanitized_workspace_name_2>/
      ...
    _project_manifest.json
```

**Sanitization rule**: replace any of `\/*?:"<>|` and consecutive whitespace
with a single underscore; strip leading/trailing whitespace and `._-`. Do
not lowercase — preserve the human label.

### Where URLs live in each workspace

A workspace has three independent places URLs can live; each maps to a
different destination folder under the workspace folder:

```
workspace.parent_datasets[]                       → catalog_assets/
  .dataset_title                                  # human label
  .dataset_resources[]
    .name                                         # human label
    .url                                          # direct download URL
    .format                                       # TIFF, CSV, GeoJSON, etc.

workspace.additional_resources[]                  → additional_resources/
  .information                                    # human label
  .resource_url                                   # direct file URL, or Drive / ArcGIS link

workspace.repository_links[]                      → repositories/   (clone)
workspace.parent_repository_links[]               → repositories/   (clone)
  .url                                            # top-level repo URL, always cloneable
  .type_of_repository                             # "git"
```

### Download filtering — let the user opt out of specific file types

By default the download skill fetches everything. Sage is general-purpose
scientific infrastructure, not a review-only tool — a typical user wants
the full dataset on disk so they can analyze it in subsequent cells.

For workflows where the user explicitly wants to exclude certain file
types (e.g., a rubric reviewer who doesn't need gigabytes of output
rasters, or a notebook that only needs documentation), the user can
declare a filter in their natural-language prompt. The wrapper script's
`DOWNLOAD_FILTER` dict is populated by the agent based on the prompt:

| Prompt language                                          | `DOWNLOAD_FILTER["skip_extensions"]` |
|----------------------------------------------------------|--------------------------------------|
| *"Download all workspaces, but skip .tif files"*         | `[".tif"]`                           |
| *"…skip TIFF files"* (includes the alternate extension)  | `[".tif", ".tiff"]`                  |
| *"…skip raster files"*                                   | `[".tif", ".tiff"]`                  |
| *"…skip TIFF, LAZ, and zip files"*                       | `[".tif", ".tiff", ".laz", ".zip"]`  |
| *"…skip large datasets (.tif, .nc, .h5)"*                | `[".tif", ".tiff", ".nc", ".h5"]`    |
| (no filter language in the prompt)                       | `[]` — unfiltered default            |

The agent should:
- Interpret the user's intent naturally — *"skip TIFFs"* normally implies
  both `.tif` and `.tiff`; *"skip the .tif files"* is more specific.
- Set values via `DOWNLOAD_FILTER["skip_extensions"] = [...]` near the
  top of the wrapper script. Extensions can be given with or without
  leading dots; `_normalize_skip_extensions()` handles both.
- Do NOT add filter language the user didn't ask for. The default is
  always "download everything" unless the user explicitly opts out.

Filtered files appear in each workspace's `_manifest.json` under
`skipped[]` with `reason: "filtered"` so the user can see exactly what
was excluded and why. They're never silently dropped.

### What to fetch / skip in this first pass

| Source                                                          | Action                                                                                                   |
|-----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| `parent_datasets[].dataset_resources[].url`                     | **Download** every entry via `download_file(url, dest)`. Treat every URL uniformly. GitHub URLs are handled specifically by `_normalize_github_url()`: `blob/` URLs (web viewer) are rewritten to `raw.githubusercontent.com` so the actual file content is fetched, not the HTML viewer page; `tree/` URLs (folder browsers) raise an error and should be handled as repository clones instead. |
| `additional_resources[].resource_url` (Drive)                   | **Skip**; append URL + label to `_drive_urls_to_download_later.txt` in the workspace folder.             |
| `additional_resources[].resource_url` (ArcGIS — `arcgis.com`, `arcg.is`, `FeatureServer`, `MapServer`) | **Skip**; append to `_arcgis_endpoints.txt` in the workspace folder.    |
| `additional_resources[].resource_url` (any other `http(s)://`)  | **Download** via `download_file(url, dest)` (GitHub URL normalization applies here too, same as catalog assets). |
| `additional_resources[].resource_url` (non-http schemes / empty)| **Skip**; append to `_unhandled_urls.txt`.                                                               |
| `repository_links[].url` and `parent_repository_links[].url`    | **Always clone** with `git clone --depth 1 {url} repositories/{sanitized_repo_name}`. These are top-level repo URLs, always cloneable. Skip the clone if the destination already exists (idempotency). |

Never skip silently — always record skipped URLs to one of the `_*.txt`
lists per workspace so the user can see what was deferred and why.

### Resolution + iteration flow

```
1. read_project_by_user      # resolve project name → project_id
2. read_project/{project_id} # get the project's workspace ID set (parent + child IDs)
3. read_workspaces_by_user   # full workspace payloads, filter by ID set
4. For each child workspace (the entry with .parent_workspace_id set):
       create  <sanitized_project_title>/<sanitized_workspace_name>/
       download per the rules above
       write   _manifest.json inside the workspace folder
5. Write _project_manifest.json at the project root summarizing all workspaces.
```

### Idempotency

Before each download, check `if dest.exists(): skip`. Re-running the prompt
should only fetch new or missing files. The per-workspace `_manifest.json`
plus the project-level `_project_manifest.json` give the agent a fast way
to report "X workspaces already fully downloaded, Y new files this run."

### `_manifest.json` (per workspace) and `_project_manifest.json` schemas

Per-workspace manifest:

```json
{
  "workspace_name": "Deep Review: FiSci (Forest)",
  "workspace_id": "8b908a98-0862-4258-bc23-19527dede3a6",
  "downloaded_at": "2026-05-14T17:25:00Z",
  "downloaded": [
    {"category": "catalog_assets", "dataset": "Forest Surface Fuels and Surface Data",
     "filename": "Forest_depth.tif", "url": "https://scil-data.../Forest_depth.tif",
     "bytes": 12345678},
    {"category": "additional_resources", "label": "README",
     "filename": "README.txt", "url": "https://scil-data.../README.txt",
     "bytes": 1234},
    {"category": "repository", "repo_name": "WUI_fire_risk_exercise",
     "url": "https://github.com/Chenzhi-Ma/WUI_fire_risk_exercise",
     "cloned_to": "repositories/WUI_fire_risk_exercise"}
  ],
  "skipped": [
    {"reason": "drive", "label": "Final Report",
     "url": "https://drive.google.com/file/d/..."}
  ],
  "errors": []
}
```

Project-level manifest:

```json
{
  "project_title": "Fire Risk Modeling Exercise - Deep Review",
  "project_id": "47a65945-ea63-404a-a02a-31fdb6189d8f",
  "context": "WSTC",
  "downloaded_at": "2026-05-14T17:25:00Z",
  "workspaces": [
    {"workspace_name": "Deep Review: FiSci (Forest)",
     "folder": "Deep_Review_FiSci_Forest",
     "downloaded_files": 7, "skipped": 5, "errors": 0}
  ],
  "totals": {"downloaded_files": 42, "skipped": 30, "errors": 0}
}
```

### Reference script pattern

```python
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests

OUTPUT_DIR = Path(os.environ["SAGE_OUTPUT_DIR"])

# Optional per-run filter declared by the user via natural-language prompt.
# Default is unfiltered (download everything) — matches Sage's general-purpose
# philosophy. The agent populates this dict when the user adds filter language
# to the prompt (e.g., "skip .tif files", "skip TIFF and zip files"). See
# the "Download filtering" subsection of this SKILL.md for the full list of
# supported keys and prompt patterns.
DOWNLOAD_FILTER = {
    # Files whose extension (case-insensitive, with or without leading dot)
    # matches an entry here are skipped — not downloaded, no API call wasted
    # in the Drive case beyond the necessary metadata fetch. Empty list = no
    # filter. Examples: [".tif"], [".tif", ".tiff", ".zip"], [".las", ".laz"].
    "skip_extensions": [],
}

def _normalize_skip_extensions(exts):
    """Return a set of lowercase extensions with leading dots — robust to
    user/agent variation (e.g., "tif" vs ".tif" vs ".TIF" all match the same
    files)."""
    out = set()
    for e in exts or []:
        e = e.strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        out.add(e)
    return out

def _should_skip_by_extension(filename: str) -> bool:
    """True if filename's extension matches DOWNLOAD_FILTER['skip_extensions']."""
    skip_set = _normalize_skip_extensions(DOWNLOAD_FILTER.get("skip_extensions"))
    if not skip_set:
        return False
    return Path(filename).suffix.lower() in skip_set

# Live progress is handled by Sage's `_sage_progress(msg)`, which is
# already in the kernel namespace — no local definition or import needed.
# It bypasses execute()'s stdout capture and streams one line to the cell.

def sanitize(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name or "untitled")
    name = re.sub(r"\s+", "_", name).strip("._-")
    return name or "untitled"

def is_drive(url):
    return "drive.google.com" in url or "docs.google.com" in url

def is_arcgis(url):
    return ("arcgis.com" in url or "arcg.is" in url
            or "FeatureServer" in url or "MapServer" in url)

_GH_TREE_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+?)/?$")
_GH_BLOB_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")

def _normalize_github_url(url):
    """Normalize a GitHub URL for direct file download.

    GitHub serves an HTML viewer page (not the file content) when the URL
    is the `blob/<ref>/<path>` form. Convert to `raw.githubusercontent.com`
    so requests.get() returns the actual file bytes. `tree/` URLs point
    at folders and cannot be downloaded as a single file; return None so
    the caller can route them to ``_download_github_tree()``.

    Returns the rewritten URL, or None for tree/ URLs.
    """
    m = _GH_BLOB_RE.match(url)
    if m:
        org, repo, ref, path = m.groups()
        return f"https://raw.githubusercontent.com/{org}/{repo}/{ref}/{path}"
    if _GH_TREE_RE.match(url):
        return None
    return url

def _download_github_tree(url, dest_dir):
    """Handle a GitHub `tree/<ref>/<subpath>` URL: clone the repo at the
    specified ref and copy the named subpath into ``dest_dir``.

    Returns the total bytes copied (for manifest accounting). The dest_dir
    is the FINAL destination for the subfolder's contents (i.e., a tree URL
    pointing at `repo/tree/main/data` will create dest_dir/<contents_of_data>).
    """
    import subprocess, shutil, tempfile
    m = _GH_TREE_RE.match(url)
    if not m:
        raise ValueError(f"Could not parse GitHub tree URL: {url}")
    org, repo, ref, subpath = m.groups()

    if dest_dir.exists():
        return _dir_size(dest_dir)

    with tempfile.TemporaryDirectory() as td:
        repo_clone = Path(td) / "repo"
        clone_url = f"https://github.com/{org}/{repo}.git"
        # Try shallow clone of the ref first (works for branches/tags).
        # Fall back to full clone + checkout (works for commit hashes too).
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", ref, clone_url, str(repo_clone)],
                check=True, capture_output=True, timeout=300,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                ["git", "clone", clone_url, str(repo_clone)],
                check=True, capture_output=True, timeout=600,
            )
            subprocess.run(
                ["git", "-C", str(repo_clone), "checkout", ref],
                check=True, capture_output=True, timeout=30,
            )

        src = repo_clone / subpath
        if not src.exists():
            raise FileNotFoundError(
                f"Subpath {subpath!r} not found in {org}/{repo} @ {ref}"
            )
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest_dir)
        else:
            shutil.copy2(src, dest_dir)
    return _dir_size(dest_dir) if dest_dir.is_dir() else dest_dir.stat().st_size

def _dir_size(path):
    total = 0
    for p in Path(path).rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total

class _FilteredOut(Exception):
    """Raised when a file is skipped by DOWNLOAD_FILTER. Caller logs to
    skipped[] with reason='filtered' rather than errors[]."""
    pass

def download_file(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Honor the user's DOWNLOAD_FILTER. Check extension first — cheap and
    # avoids any network work for filtered files.
    if _should_skip_by_extension(dest.name):
        raise _FilteredOut(
            f"{dest.name}: extension matches DOWNLOAD_FILTER['skip_extensions']"
        )

    # Handle GitHub tree URLs (folder URLs) — clone and extract subpath.
    # dest is the target destination *directory* (or file, for a single-file
    # subpath); the caller's derived ``fname`` (last component of the URL
    # path) becomes the directory name.
    if _GH_TREE_RE.match(url):
        if dest.exists():
            return (_dir_size(dest) if dest.is_dir() else dest.stat().st_size), False
        size = _download_github_tree(url, dest)
        return size, True

    if dest.exists():
        return dest.stat().st_size, False
    real_url = _normalize_github_url(url)
    if real_url is None:
        # Shouldn't happen — _GH_TREE_RE check above already handles tree URLs.
        raise ValueError(f"Unhandled GitHub URL: {url}")
    r = requests.get(real_url, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    return dest.stat().st_size, True

def download_workspace(ws, ws_root):
    ws_root.mkdir(parents=True, exist_ok=True)
    downloaded, skipped, errors = [], [], []
    drive_log, arcgis_log, other_log = [], [], []

    # 1. catalog_assets/ — CKAN dataset files. Treat every URL uniformly.
    for ds in (ws.get("parent_datasets") or []):
        ds_dir = ws_root / "catalog_assets" / sanitize(ds.get("dataset_title", "dataset"))
        for res in (ds.get("dataset_resources") or []):
            url = res.get("url")
            if not url:
                continue
            fname = Path(urlparse(url).path).name or sanitize(res.get("name", "file"))
            try:
                size, _ = download_file(url, ds_dir / fname)
                downloaded.append({"category": "catalog_assets",
                                   "dataset": ds.get("dataset_title"),
                                   "filename": fname, "url": url, "bytes": size})
            except _FilteredOut as e:
                skipped.append({"reason": "filtered", "label": fname,
                                "url": url, "filter": str(e)})
            except Exception as e:
                errors.append({"url": url, "error": str(e)})

    # 2. additional_resources/ — Drive and ArcGIS skipped; everything else
    #    is a plain HTTP download with no hostname-based branching.
    add_dir = ws_root / "additional_resources"
    for r in (ws.get("additional_resources") or []):
        url, label = r.get("resource_url", ""), r.get("information", "unknown")
        if not url:
            continue
        if is_drive(url):
            drive_log.append(f"{label}\t{url}")
            skipped.append({"reason": "drive", "label": label, "url": url})
        elif is_arcgis(url):
            arcgis_log.append(f"{label}\t{url}")
            skipped.append({"reason": "arcgis", "label": label, "url": url})
        elif url.startswith("http"):
            fname = Path(urlparse(url).path).name or sanitize(label)
            try:
                size, _ = download_file(url, add_dir / fname)
                downloaded.append({"category": "additional_resources",
                                   "label": label, "filename": fname,
                                   "url": url, "bytes": size})
            except _FilteredOut as e:
                skipped.append({"reason": "filtered", "label": label,
                                "url": url, "filter": str(e)})
            except Exception as e:
                errors.append({"url": url, "error": str(e)})
        else:
            other_log.append(f"{label}\t{url}")
            skipped.append({"reason": "unhandled", "label": label, "url": url})

    # 3. repositories/ — every entry in repository_links and
    #    parent_repository_links is a top-level repo URL. Always clone.
    repo_dir = ws_root / "repositories"
    for r in ((ws.get("repository_links") or []) + (ws.get("parent_repository_links") or [])):
        url = r.get("url", "")
        if not url:
            continue
        repo_name = sanitize(urlparse(url).path.strip("/").split("/")[-1].removesuffix(".git"))
        repo_dest = repo_dir / repo_name
        if repo_dest.exists():
            downloaded.append({"category": "repository", "repo_name": repo_name,
                               "url": url, "cloned_to": f"repositories/{repo_name}",
                               "note": "already present, skipped clone"})
            continue
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(repo_dest)],
                check=True, capture_output=True, timeout=300)
            downloaded.append({"category": "repository", "repo_name": repo_name,
                               "url": url, "cloned_to": f"repositories/{repo_name}"})
        except Exception as e:
            errors.append({"url": url, "error": str(e)})

    if drive_log:  (ws_root / "_drive_urls_to_download_later.txt").write_text("\n".join(drive_log))
    if arcgis_log: (ws_root / "_arcgis_endpoints.txt").write_text("\n".join(arcgis_log))
    if other_log:  (ws_root / "_unhandled_urls.txt").write_text("\n".join(other_log))

    (ws_root / "_manifest.json").write_text(json.dumps({
        "workspace_name": ws["workspace_name"],
        "workspace_id": ws.get("workspace_id"),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "downloaded": downloaded, "skipped": skipped, "errors": errors,
    }, indent=2))

    return {"workspace_name": ws["workspace_name"],
            "folder": ws_root.name,
            "downloaded_files": len(downloaded),
            "skipped": len(skipped), "errors": len(errors)}

def download_project(project_title, project_id, context, project_workspaces):
    project_root = OUTPUT_DIR / sanitize(project_title)
    project_root.mkdir(parents=True, exist_ok=True)

    # Filter to child workspaces (the half that carries datasets/resources).
    children = [
        ws for ws in project_workspaces
        if ws.get("parent_workspace_id")
    ]
    total = len(children)
    _sage_progress(f"Project: {project_title} ({total} workspaces)")

    per_ws = []
    for i, ws in enumerate(children, 1):
        _sage_progress(f"[{i}/{total}] {ws['workspace_name']} — downloading…")
        ws_root = project_root / sanitize(ws["workspace_name"])
        per_ws.append(download_workspace(ws, ws_root))

    # Phase 4: Drive download. ALWAYS attempts to run. _drive_service()
    # returns None if no shared review token is present, in which case
    # Phase 4 is a no-op and pending Drive URLs stay in each workspace's
    # manifest skipped[] list. If the token IS present (the normal case
    # for the Fire Risk Modeling Exercise and similar projects), this
    # phase downloads every Drive URL in every workspace's manifest.
    # download_drive_for_workspace is manifest-driven: it can retry on
    # subsequent runs even after a JupyterHub restart.
    service = _drive_service()
    if service is not None:
        _sage_progress(f"Drive phase ({len(per_ws)} workspaces)…")
        for i, ws_summary in enumerate(per_ws, 1):
            _sage_progress(f"[{i}/{len(per_ws)}] {ws_summary['workspace_name']} — Drive files…")
            ws_root = project_root / ws_summary["folder"]
            drv_dl, drv_err, drv_skip = download_drive_for_workspace(ws_root, service)
            if drv_dl or drv_err or drv_skip:
                mf_path = ws_root / "_manifest.json"
                mf = json.loads(mf_path.read_text())
                mf["downloaded"].extend(drv_dl)
                mf["errors"].extend(drv_err)
                # Promote per-file filter skips into the manifest's skipped[]
                # so the reviewer can see what was filtered.
                mf["skipped"].extend(drv_skip)
                # download_drive_for_workspace processed every entry with
                # reason="drive" (a Drive URL is either downloaded, errored,
                # or filtered — never left pending). Drop the original
                # folder-/file-URL "drive" placeholders; URL-level matching
                # doesn't work because folder expansions produce per-file
                # download URLs that differ from the original folder URL.
                mf["skipped"] = [s for s in mf["skipped"]
                                 if s.get("reason") != "drive"]
                mf_path.write_text(json.dumps(mf, indent=2))
                # Recompute counts from the updated manifest rather than
                # apply a delta — folder→file expansion makes delta math
                # error-prone (a single folder skipped entry can resolve
                # into many downloaded file entries).
                ws_summary["downloaded_files"] = len(mf["downloaded"])
                ws_summary["skipped"]          = len(mf["skipped"])
                ws_summary["errors"]           = len(mf["errors"])
    else:
        pending = sum(
            sum(1 for s in
                json.loads((project_root / ws_summary["folder"] / "_manifest.json").read_text())
                .get("skipped", [])
                if s.get("reason") == "drive")
            for ws_summary in per_ws
        )
        if pending:
            _sage_progress(
                f"Drive phase skipped — {pending} Drive resources pending across "
                f"all workspaces. Shared Drive token is not present at "
                f"~/work/_User-Persistent-Storage_CephBlock_/.gdrive_token.json. "
                f"Ask the admin to distribute it, then re-run this download "
                f"cell — Phase 4 is idempotent and will pick up where it left off."
            )

    # Compute project totals AFTER Phase 4 so they reflect Drive downloads.
    totals = {
        "downloaded_files": sum(w["downloaded_files"] for w in per_ws),
        "skipped":          sum(w["skipped"]          for w in per_ws),
        "errors":           sum(w["errors"]           for w in per_ws),
    }
    (project_root / "_project_manifest.json").write_text(json.dumps({
        "project_title": project_title,
        "project_id":    project_id,
        "context":       context,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "workspaces":    per_ws,
        "totals":        totals,
    }, indent=2))
    _sage_progress(
        f"Project download complete: {totals['downloaded_files']} files, "
        f"{totals['skipped']} skipped, {totals['errors']} errors."
    )
    return project_root, totals
```

### Canonical `main()` — copy this, do not invent your own counts

```python
def main():
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}

    # --- Step 1: resolve project name → project_id ---
    _sage_progress(f"Resolving project: {PROJECT_NAME}")
    resp = requests.get(
        f"{WORKSPACE_API_URL}/read_project_by_user?where_wkspc_created={CONTEXT}",
        headers=headers, timeout=30,
    )
    resp.raise_for_status()
    candidates = [
        p for p in resp.json()
        if PROJECT_NAME.lower() in (p.get("title") or "").lower()
    ]
    if not candidates:
        print(f"No project matched: {PROJECT_NAME!r}")
        return
    project = candidates[0]
    project_id = project["project_id"]
    project_title = project["title"]
    _sage_progress(f"Found project: {project_title}")

    # --- Step 2: fetch project metadata + collect workspace IDs ---
    resp = requests.get(
        f"{WORKSPACE_API_URL}/read_project/{project_id}?where_wkspc_created={CONTEXT}",
        headers=headers, timeout=30,
    )
    resp.raise_for_status()
    project_data = resp.json()
    ws_id_set = set()
    for sg in project_data.get("entity", {}).get("subgroups", []):
        for ws in sg.get("workspaces", []):
            if ws.get("workspace_id"):
                ws_id_set.add(ws["workspace_id"])
            if ws.get("child_workspace_id"):
                ws_id_set.add(ws["child_workspace_id"])

    # --- Step 3: full workspace payloads, filter to this project ---
    resp = requests.get(
        f"{WORKSPACE_API_URL}/workspace/read_workspaces_by_user?where_wkspc_created={CONTEXT}",
        headers=headers, timeout=60,
    )
    resp.raise_for_status()
    project_workspaces = [
        ws for ws in resp.json() if ws.get("workspace_id") in ws_id_set
    ]

    # The single workspace count for user display = number of CHILD entries
    # in the filtered list. Do NOT divide len(project_workspaces) by 2; do
    # NOT report len(ws_id_set); do NOT invent any other count.
    children = [
        ws for ws in project_workspaces
        if ws.get("parent_workspace_id")
    ]
    _sage_progress(f"Loading {len(children)} workspaces…")

    # download_project() prints its own final progress line; do not add
    # a separate "Download Summary" block here.
    download_project(project_title, project_id, CONTEXT, project_workspaces)


if __name__ == "__main__":
    main()
```

### Drive download phase — helpers referenced by `download_project()`

The Drive download phase (Phase 4) is built into `download_project()`
above — it runs automatically after the three main phases complete for
every workspace. **No separate invocation is needed.** The helpers
below are *referenced by* `download_project()` and must be defined in
the same wrapper script you copy. The convention in this SKILL.md
documents them after the canonical `main()` so the reader sees the main
flow first, but in the actual wrapper script you write, **place these
helper definitions ABOVE the `download_workspace` / `download_project`
functions** so Python sees them before any call:

```
# In your wrapper script, the order should be:
#   1. imports
#   2. Drive helpers (TOKEN_PATH, _drive_service, _extract_drive_file_id,
#      _EXPORT, _sanitize_for_filename, download_drive_for_workspace)
#   3. download_file(), download_workspace(), download_project()
#   4. main()
#   5. if __name__ == "__main__": main()
```

The shared Drive token lives at:

```
/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.gdrive_token.json
```

This token belongs to a dedicated review account (e.g.
`fire.risk.review@gmail.com`) that an admin has set up — file owners
share Drive resources with that one account, and the same token JSON is
distributed to every reviewer. The token is **not** any reviewer's
personal Google credentials.

`_drive_service()` returns `None` if the token file doesn't exist. In
that case, `download_project()`'s Phase 4 is a no-op and the pending
Drive URLs remain in each workspace's manifest `skipped[]` list — the
next reviewer to run the download cell with the token in place will
pick them up automatically (Phase 4 is manifest-driven and idempotent).

Do NOT prompt the reviewer to run OAuth themselves. There is no
"gdrive-setup" skill. Token distribution is an admin task done outside
Sage.

```python
TOKEN_PATH = Path("/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.gdrive_token.json")

def _drive_service():
    """Return an authenticated Drive service, or None if no shared token."""
    if not TOKEN_PATH.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--user",
                               "google-api-python-client", "google-auth-oauthlib"])
        import site; site.main()
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(
        str(TOKEN_PATH),
        ["https://www.googleapis.com/auth/drive.readonly"],
    )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Don't write back — the token file is shared across reviewers;
            # let each kernel hold its own refreshed access_token in memory only.
        else:
            return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)

_DRIVE_FILE_ID_RE = re.compile(r"/d/([a-zA-Z0-9_\-]{20,})")
_DRIVE_FOLDER_ID_RE = re.compile(r"/drive/folders/([a-zA-Z0-9_\-]{20,})")

def _extract_drive_file_id(url: str):
    m = _DRIVE_FILE_ID_RE.search(url)
    return m.group(1) if m else None

def _extract_drive_folder_id(url: str):
    m = _DRIVE_FOLDER_ID_RE.search(url)
    return m.group(1) if m else None

_EXPORT = {
    "application/vnd.google-apps.spreadsheet":
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
}

_DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

def _sanitize_for_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name or "untitled").strip() or "untitled"

def _drive_list_folder_children(service, folder_id):
    """Return [{"id", "name", "mimeType"}, ...] for direct children of the
    Drive folder. supportsAllDrives + includeItemsFromAllDrives are required
    for shared-drive content."""
    children, page_token = [], None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token,
            pageSize=200,
        ).execute()
        children.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return children

def _download_drive_folder_recursive(service, folder_id, dest_dir, label_prefix,
                                      depth=0, max_depth=10):
    """Recursively download a Drive folder's contents into dest_dir, preserving
    the source folder structure on disk. Each subfolder becomes its own
    subdirectory under dest_dir.

    Returns ``(downloaded_list, errors_list, skipped_list)``. The third slot
    holds entries for files that were deliberately skipped (e.g., filtered by
    `DOWNLOAD_FILTER['skip_extensions']`).

    A max_depth guard prevents infinite recursion (defensive — 10 levels is
    deep enough for any realistic team submission).
    """
    downloaded, errors, skipped = [], [], []
    if depth >= max_depth:
        errors.append({
            "url": f"folder_id={folder_id}",
            "error": f"Max recursion depth ({max_depth}) reached at {dest_dir}; "
                     "subfolders below this point were not traversed.",
            "label": label_prefix,
        })
        return downloaded, errors, skipped

    try:
        children = _drive_list_folder_children(service, folder_id)
    except Exception as e:
        errors.append({
            "url": f"folder_id={folder_id}",
            "error": f"Could not list folder: {e}",
            "label": label_prefix,
        })
        return downloaded, errors, skipped

    # Heartbeat at folder boundary so users can see something is happening
    # inside a long Drive run (e.g., SIG/Forest with 1,600+ files).
    n_children = len(children)
    if n_children > 0:
        _sage_progress(f"    {label_prefix} — {n_children} item(s)")

    dest_dir.mkdir(parents=True, exist_ok=True)
    files_done = 0
    for child in children:
        child_name = child.get("name", child["id"])
        child_label = f"{label_prefix} / {child_name}"
        if child.get("mimeType") == _DRIVE_FOLDER_MIME:
            sub_dest = dest_dir / _sanitize_for_filename(child_name)
            sub_dl, sub_err, sub_skip = _download_drive_folder_recursive(
                service, child["id"], sub_dest, child_label,
                depth=depth + 1, max_depth=max_depth,
            )
            downloaded.extend(sub_dl)
            errors.extend(sub_err)
            skipped.extend(sub_skip)
        else:
            dl, err, sk = _download_one_drive_file(
                service, child["id"], child_label, dest_dir,
                url_hint=f"https://drive.google.com/file/d/{child['id']}/view (from folder {folder_id})",
            )
            if dl: downloaded.append(dl)
            if err: errors.append(err)
            if sk: skipped.append(sk)
            files_done += 1
            # Within-folder heartbeat every 50 files so users see progress
            # in huge submissions instead of waiting in silence.
            if files_done % 50 == 0:
                _sage_progress(f"      …{files_done}/{n_children} files in {label_prefix}")
    return downloaded, errors, skipped

def _download_one_drive_file(service, file_id, label, dest_dir, url_hint=""):
    """Download a single Drive file by ID into dest_dir. Returns a 3-tuple
    ``(downloaded_entry, error_entry, skipped_entry)`` — exactly one is
    non-None. The ``skipped_entry`` slot is populated when the file's name
    matches ``DOWNLOAD_FILTER['skip_extensions']``."""
    from googleapiclient.http import MediaIoBaseDownload
    try:
        meta = service.files().get(
            fileId=file_id, fields="name,mimeType",
            supportsAllDrives=True,
        ).execute()
        fname = _sanitize_for_filename(meta["name"])

        # Honor DOWNLOAD_FILTER. The metadata fetch is necessary (Drive file
        # IDs don't carry an extension), but at least no download bandwidth
        # is wasted on filtered files.
        if _should_skip_by_extension(fname):
            return None, None, {
                "reason": "filtered", "label": label, "filename": fname,
                "url": url_hint or f"https://drive.google.com/file/d/{file_id}/view",
                "filter": f"extension {Path(fname).suffix.lower()!r} matches DOWNLOAD_FILTER['skip_extensions']",
            }

        mime = meta["mimeType"]
        if mime in _EXPORT:
            export_mime, ext = _EXPORT[mime]
            if not fname.lower().endswith(ext):
                fname += ext
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        elif mime.startswith("application/vnd.google-apps."):
            request = service.files().export_media(fileId=file_id, mimeType="application/pdf")
            if not fname.lower().endswith(".pdf"):
                fname += ".pdf"
        else:
            request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

        dest = dest_dir / fname
        if dest.exists():
            return ({"category": "additional_resources_drive",
                     "label": label, "filename": fname,
                     "url": url_hint or f"https://drive.google.com/file/d/{file_id}/view",
                     "bytes": dest.stat().st_size,
                     "note": "already present, skipped download"}, None, None)

        with open(dest, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return ({"category": "additional_resources_drive",
                 "label": label, "filename": fname,
                 "url": url_hint or f"https://drive.google.com/file/d/{file_id}/view",
                 "bytes": dest.stat().st_size}, None, None)
    except Exception as e:
        return (None,
                {"url": url_hint or f"file_id={file_id}",
                 "error": str(e), "label": label},
                None)

def download_drive_for_workspace(ws_root, service):
    """Fetch every Drive URL listed in this workspace's manifest as
    ``skipped`` with ``reason == "drive"``.

    The manifest is the authoritative source of pending Drive resources
    (not the sidecar ``_drive_urls_to_download_later.txt``, which can
    drift out of sync across JupyterHub sessions). This means Phase 4 is
    idempotent and can always retry: if the manifest still has Drive
    entries in ``skipped[]``, Phase 4 will pick them up regardless of
    whether the sidecar text file exists.

    Returns (downloaded_list, errors_list).
    """
    mf_path = ws_root / "_manifest.json"
    if not mf_path.exists():
        return [], []

    mf = json.loads(mf_path.read_text())
    drive_entries = [s for s in mf.get("skipped", [])
                     if s.get("reason") == "drive"]
    if not drive_entries:
        return [], []

    add_dir = ws_root / "additional_resources"
    add_dir.mkdir(parents=True, exist_ok=True)

    downloaded, errors, filtered = [], [], []
    n_entries = len(drive_entries)
    _sage_progress(f"  {n_entries} Drive resource(s) to fetch for this workspace")

    for idx, entry in enumerate(drive_entries, 1):
        url = entry.get("url", "")
        label = entry.get("label", "unknown")
        _sage_progress(f"  [{idx}/{n_entries}] {label}")

        # Branch on URL form: file vs folder. Drive folder URLs
        # (drive.google.com/drive/folders/<id>) cannot be downloaded as
        # a single file — recurse into the folder, preserving the team's
        # source folder structure under add_dir/<label>/. Subfolders are
        # downloaded into their own subdirectories (e.g., ELMFIRE typically
        # has ENSEMBLES/, STATISTICS/, MEDIAN/, etc.).
        folder_id = _extract_drive_folder_id(url)
        if folder_id:
            folder_dest = add_dir / _sanitize_for_filename(label)
            dl_list, err_list, skip_list = _download_drive_folder_recursive(
                service, folder_id, folder_dest, label,
            )
            downloaded.extend(dl_list)
            errors.extend(err_list)
            filtered.extend(skip_list)
            continue

        file_id = _extract_drive_file_id(url)
        if not file_id:
            errors.append({"url": url, "error": "Could not parse Drive file ID or folder ID", "label": label})
            continue

        dl, err, sk = _download_one_drive_file(service, file_id, label, add_dir, url_hint=url)
        if dl: downloaded.append(dl)
        if err: errors.append(err)
        if sk: filtered.append(sk)

    # Sidecar text file is informational only and now stale; remove it.
    sidecar = ws_root / "_drive_urls_to_download_later.txt"
    if sidecar.exists():
        sidecar.unlink()

    return downloaded, errors, filtered
```

### Integration is built into `download_project()`

The project-level integration of Phase 4 lives inside the
`download_project()` function shown earlier. You do not need a separate
`service = _drive_service()` block — it's already wired in. Calling
`download_project(...)` runs all four phases (catalog assets,
additional_resources, repository clones, Drive) and writes the
authoritative `_manifest.json` per workspace plus
`_project_manifest.json` at the project root.

### Reporting back to the user

After the run, print a one-line summary per workspace (downloaded /
skipped / errors) plus a totals row, then call out anything that needs the
user's attention:

- If `_drive_urls_to_download_later.txt` files still exist after the
  run, the shared review-account token was not present at
  `/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.gdrive_token.json`.
  Tell the reviewer how many Drive URLs are pending and that the
  shared Drive token has not been distributed yet — ask the admin. Do
  NOT instruct the reviewer to run OAuth themselves.
- If `_arcgis_endpoints.txt` files exist, mention that ArcGIS feature
  services need a separate skill to query and serialize.
- If `errors` > 0 in any workspace, list the first few failed URLs so the
  user can diagnose.

**Terminology to use in user-facing output** (applies to download summaries
*and* any earlier inspection / summary tables in the same conversation —
do not invent alternative labels):

- `parent_datasets` → **"catalog assets"** (matches the folder name; do NOT
  say "NDP datasets", "datasets", or "data files")
- `additional_resources` → **"additional resources"** (matches the folder
  and the API field name; treat as one umbrella — do NOT sub-label a count
  as "Google Drive resources" unless the user explicitly asked for the
  Drive-only subset)
- `repository_links` + `parent_repository_links` → **"repositories"**

Example column headers for a per-workspace summary inside a project:

| Workspace Name | Catalog Assets | Additional Resources | Repositories |

### Parent/child duplicates

`read_workspaces_by_user` may return each workspace as **two entries**: a
parent (no `parent_workspace_id`) and a child (non-null
`parent_workspace_id`). The download flow must skip the parent entry and
operate only on the child — that's where the dataset records and additional
resources live. Note: a child workspace may have an empty `parent_datasets`
field — this still represents a real submission (its content lives in
`additional_resources` and `repository_links`) and must be downloaded.
Do not filter on `parent_datasets` when selecting children.

## Troubleshooting

### `{"error": "Project not found"}`

Either `where_wkspc_created` is wrong (try the other context — NDP ↔ WSTC),
or the `project_id` is from a different context than the one passed. If you
got the `project_id` from `read_project_by_user`, you also know which
context produced it — pass that same context to `read_project/{id}`.

### Project name doesn't match anything

Don't silently invent results. List every `.title` available across both
contexts and ask the user to pick:

```bash
for CONTEXT in NDP WSTC; do
  curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
    "$WORKSPACE_API_URL/read_project_by_user?where_wkspc_created=$CONTEXT" \
    | jq -r --arg c "$CONTEXT" '.[] | "\($c) — \(.title)"'
done
```

If the result is empty even across both contexts, the caller has no
project memberships. The project the user asked about may be private and
they may not be a member — suggest they ask the project owner to add
their NDP account.

### Empty `datasets` array on a workspace

Expected — the `datasets` field is always empty. Read `parent_datasets`
instead, and make sure you fetched the workspace via
`/workspace/read_workspaces_by_user` (the per-workspace endpoint
`/workspace/{workspace_id}` returns shallow data).

### Workspace appears twice in `read_workspaces_by_user` output

A project's workspace is a parent/child pair, and both halves appear in
the user's workspace list — same `entity_id`, different `workspace_id`s.
The child entry is the one with `parent_workspace_id` set; prefer it
because that's where `parent_datasets` lives.

### `401 Not authenticated`

The `ACCESS_TOKEN` is expired or invalid. NDP Bearer tokens last several
hours. Refresh from <https://nationaldataplatform.org> via browser DevTools
→ Network tab → any `workspaces-api` request → copy the
`Authorization: Bearer` value.

### `403` on Google Drive resources

The `additional_resources` URL list is correct, but the Drive file is
private to the project members' Google accounts. Downloading needs separate
Google OAuth — this skill only inspects the URLs, it does not download
Drive content.

## Related Skills

- `ndp-workspaces` — list the current user's workspaces (no project grouping)
- `ndp-search` — search the NDP CKAN catalog for public datasets

## Notes

- Project field is `.title` (not `project_name`, not `name`). The
  `read_workspaces_by_user` response has no project-name field at all —
  only the shared `entity_id` ties workspaces to their project.
- `read_project/{project_id}` returns the workspace **list** (names + IDs)
  but not the workspace **contents**. The two-call pattern (Step 2 + Step
  3) is required for full reports.
- Workspace IDs and project IDs are stable; titles can change. When
  persisting references across sessions, store IDs. When showing results
  to the user, show titles.
- `read_project_by_user` is not paginated in observed responses; typical
  user memberships fit in one call.
