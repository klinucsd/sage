---
name: ndp-workspaces
description: List, inspect, or download a single NDP workspace. Use when the user wants to list the workspaces they have access to, see what is inside one workspace by name, or download every file in one specific workspace to local disk.
---

# NDP Workspaces Skill

## Scope check — read this first

This skill operates on **one** workspace at a time. If the user's request is
project-scoped — phrases such as *"download all files in the workspaces of
this project"*, *"summarize the workspaces in project X"*, *"every
workspace in the Y project"*, or any mention of a named **project** — stop
here and use the `ndp-projects` skill instead. That skill knows how to
resolve a project name → its workspaces and iterate the download with a
`<project_title>/<workspace_name>/` folder layout. Coming back to this
skill after `ndp-projects` is normal for a follow-up about a single
workspace.

## Description

This skill provides functionality to load workspace data from the NDP Workspace API using direct HTTP requests via curl. It's designed to integrate with JupyterHub environments and retrieve workspace configurations for entities. The skill reads authentication credentials from environment variables and makes API calls to fetch workspace information.

The Swagger UI for the workspace API is at https://nationaldataplatform.org/workspaces-api/v1/openapi.json

## When to Use

- When the user needs to retrieve workspace configurations from the NDP API
- When setting up or configuring JupyterHub environments based on workspace data
- When filtering workspaces by specific entity IDs
- When you need to automate workspace data retrieval for downstream processes
- When troubleshooting workspace access or configuration issues

## Prerequisites

The following environment variables must be set:
- `WORKSPACE_API_URL` - Base URL for the NDP Workspace API
- `ACCESS_TOKEN` - Bearer token for authentication

Optional environment variable:
- `ENTITY_ID` - Entity ID to filter workspaces (if not provided, all accessible workspaces are returned)

## How to Use

### Step 1: Verify Environment Variables

Check that the required environment variables are set:
```bash
echo "API URL: $WORKSPACE_API_URL"
echo "Token present: $([ -n "$ACCESS_TOKEN" ] && echo "Yes" || echo "No")"
```

### Step 2: Make the API Call

Use curl to call the NDP Workspace API endpoint. The basic pattern is:

```bash
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub"
```

### Step 3: Process the Results

The API returns JSON data containing workspace information. You can:
- Pipe the output to `jq` for parsing and filtering
- Save it to a file for later processing
- Use it directly in your automation workflow

## API Endpoint

**Endpoint:**
```
GET {WORKSPACE_API_URL}/workspace/read_workspaces_for_jupyterhub
```

**Headers:**
- `Authorization: Bearer {ACCESS_TOKEN}`

**Query Parameters:**
- `entity_id` (optional): Filter workspaces by entity ID

## Best Practices

- **Security**: Never hardcode access tokens. Always use environment variables
- **Error Handling**: Check HTTP response codes. Use `-f` flag with curl to fail on HTTP errors
- **Entity Filtering**: Use the `entity_id` parameter to filter workspaces when you only need data for specific entities
- **Output Management**: Use `-o` flag to save responses to files for batch operations
- **JSON Processing**: Use `jq` for parsing and manipulating JSON responses
- **Debugging**: Add `-v` flag to curl for verbose output when troubleshooting

## Examples

### Example 1: Basic Workspace Retrieval

**User Request:** "Fetch all workspaces from the NDP API"

**Approach:**
1. Verify environment variables are set
2. Make a GET request to the workspaces endpoint
3. Pretty-print the JSON output

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | jq '.'
```

**Expected outcome:** JSON output with all workspaces the token has access to, formatted with jq

### Example 2: Entity-Specific Workspace Retrieval

**User Request:** "Get workspaces for entity ID 'project-alpha' and save to a file"

**Approach:**
1. Add the entity_id query parameter to the request
2. Save the output to a JSON file
3. Verify the file was created successfully

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub?entity_id=project-alpha" \
  -o project_alpha_workspaces.json

cat project_alpha_workspaces.json | jq '.'
```

**Expected result:** A JSON file containing only workspaces associated with 'project-alpha'

### Example 3: Using Environment Variable for Entity ID

**User Request:** "Retrieve workspaces for the entity specified in ENTITY_ID environment variable"

**Approach:**
1. Use the ENTITY_ID environment variable if set
2. Handle the case where ENTITY_ID might not be set
3. Save and display results

```bash
if [ -n "$ENTITY_ID" ]; then
  curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
    "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub?entity_id=$ENTITY_ID" \
    -o workspaces.json
else
  curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
    "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" \
    -o workspaces.json
fi

echo "Workspaces saved to workspaces.json"
cat workspaces.json | jq '.'
```

**Expected result:** Workspaces filtered by ENTITY_ID if set, otherwise all workspaces

### Example 4: Error Handling and Status Checking

**User Request:** "Retrieve workspaces with proper error handling"

**Approach:**
1. Use curl with fail flag and capture HTTP status
2. Check for errors and display appropriate messages
3. Only process response if successful

```bash
HTTP_CODE=$(curl -s -w "%{http_code}" -o workspaces.json \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub")

if [ "$HTTP_CODE" -eq 200 ]; then
  echo "Success! Workspaces retrieved."
  cat workspaces.json | jq '.'
else
  echo "Error: HTTP $HTTP_CODE"
  cat workspaces.json
  exit 1
fi
```

**Expected result:** Graceful error handling with appropriate status messages

### Example 5: Extracting Specific Fields

**User Request:** "Get just the workspace names and IDs"

**Approach:**
1. Fetch the workspaces data
2. Use jq to extract only the required fields
3. Format as a simple list

```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq -r '.workspaces[] | "\(.id): \(.name)"'
```

**Expected result:** A clean list of workspace IDs and names

## Common jq Patterns

**Count workspaces:**
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq '.workspaces | length'
```

**Filter workspaces by a field:**
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq '.workspaces[] | select(.status == "active")'
```

**Extract to CSV:**
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "$WORKSPACE_API_URL/workspace/read_workspaces_for_jupyterhub" | \
  jq -r '.workspaces[] | [.id, .name, .status] | @csv'
```

## Downloading Workspace Resources

This skill supports requests like *"download all files in the FiSci
workspace"* or *"save every file from this workspace to disk"*. The
workspace JSON has all the URLs; this section describes how to walk them,
which to fetch, and where to put them on disk.

### Where URLs live in the response

A workspace has three independent places URLs can live. Each maps to a
different destination folder:

```
workspace.parent_datasets[]                  → catalog_assets/
  .dataset_title                             # human label, e.g. "Forest Weather Data"
  .dataset_resources[]
    .name                                    # human label
    .url                                     # direct download URL
    .format                                  # TIFF, CSV, GeoJSON, etc.

workspace.additional_resources[]             → additional_resources/
  .information                               # human label
  .resource_url                              # direct file URL, or Drive / ArcGIS link

workspace.repository_links[]                 → repositories/   (clone)
workspace.parent_repository_links[]          → repositories/   (clone)
  .url                                       # top-level repo URL, always cloneable
  .type_of_repository                        # "git"
```

### Download filtering — let the user opt out of specific file types

By default the download skill fetches everything. Sage is general-purpose
scientific infrastructure — a typical user wants the full dataset on
disk so they can analyze it in subsequent cells.

For workflows where the user explicitly wants to exclude certain file
types, the user can declare a filter in their natural-language prompt.
The wrapper script's `DOWNLOAD_FILTER` dict is populated by the agent:

| Prompt language                                          | `DOWNLOAD_FILTER["skip_extensions"]` |
|----------------------------------------------------------|--------------------------------------|
| *"Download the workspace, but skip .tif files"*          | `[".tif"]`                           |
| *"…skip TIFF files"* (covers both `.tif` and `.tiff`)    | `[".tif", ".tiff"]`                  |
| *"…skip raster files"*                                   | `[".tif", ".tiff"]`                  |
| *"…skip TIFF, LAZ, and zip files"*                       | `[".tif", ".tiff", ".laz", ".zip"]`  |
| (no filter language in the prompt)                       | `[]` — unfiltered default            |

Interpret the user's intent naturally and set values via
`DOWNLOAD_FILTER["skip_extensions"] = [...]` near the top of the
wrapper script. Filtered files appear in `_manifest.json` under
`skipped[]` with `reason: "filtered"`.

### What to fetch / skip in this first pass

| Source                                                          | Action                                                                                                   |
|-----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| `parent_datasets[].dataset_resources[].url`                     | **Download** every entry via `requests.get(url, stream=True)`. Treat every URL uniformly — no special-casing by hostname. If a publisher puts a GitHub link in a dataset resource, download it as a file (it's the publisher's choice; do not try to clone it). |
| `additional_resources[].resource_url` (Drive)                   | **Skip**; append URL + label to `_drive_urls_to_download_later.txt`.                                     |
| `additional_resources[].resource_url` (ArcGIS — `arcgis.com`, `arcg.is`, `FeatureServer`, `MapServer`) | **Skip**; append to `_arcgis_endpoints.txt`.                          |
| `additional_resources[].resource_url` (any other `http(s)://`)  | **Download** via `requests.get`. Treat uniformly — do not branch on hostname.                            |
| `additional_resources[].resource_url` (non-http schemes / empty)| **Skip**; append to `_unhandled_urls.txt`.                                                               |
| `repository_links[].url` and `parent_repository_links[].url`    | **Always clone** with `git clone --depth 1 {url} repositories/{sanitized_repo_name}`. These are top-level repo URLs, always cloneable. Skip if the destination already exists (idempotency). |

Skipping silently is bad UX — always record skipped URLs to one of the
`_*.txt` lists so the user can see what was deferred and why.

### Folder layout

Use `SAGE_OUTPUT_DIR` (already set in the kernel) as the root. The
top-level folder is the sanitized workspace name.

```
{SAGE_OUTPUT_DIR}/
  <sanitized_workspace_name>/
    catalog_assets/                  # files from parent_datasets[].dataset_resources[].url
      <sanitized_dataset_title>/
        <original_filename>
        ...
    additional_resources/            # files from additional_resources[].resource_url
      <original_filename>
    repositories/                    # cloned git repos from {parent_,}repository_links[]
      <sanitized_repo_name>/
    _drive_urls_to_download_later.txt
    _arcgis_endpoints.txt
    _unhandled_urls.txt
    _manifest.json
```

**Sanitization rule (applies to both workspace and dataset names):**
replace any of `\/*?:"<>|` and consecutive whitespace with a single
underscore; strip leading/trailing whitespace. Do not lowercase — keep the
human label readable.

### Idempotency

Before each download, check `if dest.exists(): skip`. The manifest from a
previous run is authoritative — if the user re-runs the prompt, only new or
missing files should be fetched.

### `_manifest.json` schema

Write this once at the end of the run:

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
     "url": "https://drive.google.com/file/d/..."},
    {"reason": "arcgis", "label": "ESRI MapViewer",
     "url": "https://www.arcgis.com/..."}
  ],
  "errors": [
    {"url": "...", "error": "HTTP 404"}
  ]
}
```

### Reference script pattern

Below is the canonical structure the agent should follow. Treat it as a
template — adjust field accesses if the workspace has missing/null keys.

```python
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
import requests

OUTPUT_DIR = Path(os.environ["SAGE_OUTPUT_DIR"])

# Optional per-run filter declared by the user via natural-language prompt.
# Default = empty (download everything). See the "Download filtering"
# subsection of this SKILL.md for the full list of supported keys and
# prompt patterns.
DOWNLOAD_FILTER = {
    # Files whose extension (case-insensitive, with or without leading dot)
    # matches an entry here are skipped. Examples: [".tif"], [".tif", ".tiff"],
    # [".las", ".laz"]. Empty list = no filter.
    "skip_extensions": [],
}

def _normalize_skip_extensions(exts):
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

def is_drive(url: str) -> bool:
    return "drive.google.com" in url or "docs.google.com" in url

def is_arcgis(url: str) -> bool:
    return ("arcgis.com" in url or "arcg.is" in url
            or "FeatureServer" in url or "MapServer" in url)

_GH_TREE_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+?)/?$")
_GH_BLOB_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$")

def _normalize_github_url(url: str):
    """Normalize a GitHub URL for direct file download.

    GitHub serves an HTML viewer page (not the file content) when the URL
    is the `blob/<ref>/<path>` form. Convert to `raw.githubusercontent.com`
    so requests.get() returns the actual file bytes. `tree/` URLs point
    at folders; return None so the caller can route them to
    `_download_github_tree()` which clones the parent repo and copies the
    named subpath.
    """
    m = _GH_BLOB_RE.match(url)
    if m:
        org, repo, ref, path = m.groups()
        return f"https://raw.githubusercontent.com/{org}/{repo}/{ref}/{path}"
    if _GH_TREE_RE.match(url):
        return None
    return url

def _dir_size(path):
    total = 0
    for p in Path(path).rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total

def _download_github_tree(url, dest_dir):
    """Clone the repo at the named ref and copy the subpath into dest_dir."""
    import subprocess, shutil, tempfile
    m = _GH_TREE_RE.match(url)
    if not m:
        raise ValueError(f"Could not parse GitHub tree URL: {url}")
    org, repo, ref, subpath = m.groups()
    if dest_dir.exists():
        return _dir_size(dest_dir) if dest_dir.is_dir() else dest_dir.stat().st_size

    with tempfile.TemporaryDirectory() as td:
        repo_clone = Path(td) / "repo"
        clone_url = f"https://github.com/{org}/{repo}.git"
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

class _FilteredOut(Exception):
    """Raised when a file is skipped by DOWNLOAD_FILTER."""
    pass

def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)

    if _should_skip_by_extension(dest.name):
        raise _FilteredOut(
            f"{dest.name}: extension matches DOWNLOAD_FILTER['skip_extensions']"
        )

    # GitHub tree URLs: clone-and-copy instead of HTTP GET.
    if _GH_TREE_RE.match(url):
        if dest.exists():
            return (_dir_size(dest) if dest.is_dir() else dest.stat().st_size), False
        size = _download_github_tree(url, dest)
        return size, True

    if dest.exists():
        return dest.stat().st_size, False
    real_url = _normalize_github_url(url)
    if real_url is None:
        raise ValueError(f"Unhandled GitHub URL: {url}")
    r = requests.get(real_url, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    return dest.stat().st_size, True

def download_workspace(ws: dict) -> Path:
    ws_root = OUTPUT_DIR / sanitize(ws["workspace_name"])
    ws_root.mkdir(parents=True, exist_ok=True)
    _sage_progress(f"Workspace: {ws['workspace_name']}")

    downloaded, skipped, errors = [], [], []
    drive_log, arcgis_log, other_log = [], [], []

    # 1. catalog_assets/ — CKAN dataset files. Every URL is downloaded as a
    #    plain file regardless of hostname (publisher's choice).
    _sage_progress("  catalog_assets…")
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

    # 2. additional_resources/ — extras. Skip Drive and ArcGIS; everything
    #    else is a plain HTTP download. No hostname-based branching.
    _sage_progress("  additional_resources…")
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
    repo_entries = (ws.get("repository_links") or []) + (ws.get("parent_repository_links") or [])
    if repo_entries:
        _sage_progress(f"  repositories ({len(repo_entries)})…")
    for r in repo_entries:
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

    if drive_log:
        (ws_root / "_drive_urls_to_download_later.txt").write_text("\n".join(drive_log))
    if arcgis_log:
        (ws_root / "_arcgis_endpoints.txt").write_text("\n".join(arcgis_log))
    if other_log:
        (ws_root / "_unhandled_urls.txt").write_text("\n".join(other_log))

    (ws_root / "_manifest.json").write_text(json.dumps({
        "workspace_name": ws["workspace_name"],
        "workspace_id": ws.get("workspace_id"),
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
    }, indent=2))

    # Phase 4: Drive download. ALWAYS attempts to run. _drive_service()
    # returns None if no shared review token is present, in which case
    # Phase 4 is a no-op and pending Drive URLs stay in the manifest's
    # skipped[] list. If the token IS present, this phase downloads every
    # Drive URL in the manifest and updates the manifest in place.
    # download_drive_for_workspace is manifest-driven, so it can retry on
    # subsequent runs even after a JupyterHub restart.
    service = _drive_service()
    if service is not None:
        _sage_progress(f"  Drive phase…")
        download_drive_for_workspace(ws_root, service)

    return ws_root
```

### Drive download phase — helpers referenced by `download_workspace()`

The Drive download phase (Phase 4) is built into `download_workspace()`
above — it runs automatically after the three main phases complete.
**No separate invocation is needed.** The helpers below are *referenced
by* `download_workspace()` and must be defined in the same wrapper
script you copy. The convention in this SKILL.md documents them after
`download_workspace` so the main flow reads top-down, but in the actual
wrapper script you write, **place these helper definitions ABOVE
`download_workspace`** so Python sees them before any call:

```
# In your wrapper script, the order should be:
#   1. imports
#   2. Drive helpers (TOKEN_PATH, _drive_service, _extract_drive_file_id,
#      _EXPORT, _sanitize_for_filename, download_drive_for_workspace)
#   3. download_file(), download_workspace()
#   4. main()
#   5. if __name__ == "__main__": main()
```

The shared Drive token lives at:

```
/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.gdrive_token.json
```

This token belongs to a dedicated review account (e.g.
`fire.risk.review@gmail.com`) that an admin has set up specifically for
this purpose — file owners share Drive resources with that account, and
the same token JSON is distributed to every reviewer. The token is
**not** the reviewer's personal Google credentials.

`_drive_service()` returns `None` if the token file doesn't exist. In
that case, `download_workspace()`'s Phase 4 is a no-op and the pending
Drive URLs stay in the manifest's `skipped[]` list — the next reviewer
to run the download cell with the token in place will pick them up
automatically (Phase 4 is manifest-driven and idempotent).

For documentation purposes, here is what Phase 4 does when it runs:

- Build a Drive service from the shared token.
- Iterate every entry in the manifest's `skipped[]` list with
  `reason == "drive"`, download each file/sheet into the workspace's
  `additional_resources/` folder, record results in
  `_manifest.json` under `category: "additional_resources_drive"`, and
  remove the corresponding entries from `skipped`. If every Drive URL
  succeeds, delete `_drive_urls_to_download_later.txt`.

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
            # Don't write back — the token file is shared across reviewers; let
            # each kernel hold its own refreshed access_token in memory only.
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

# Google Docs/Sheets/Slides need .export_media with an Office mime type.
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
    """Return [{"id","name","mimeType"}, ...] for direct children of a Drive folder."""
    children, page_token = [], None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            pageToken=page_token, pageSize=200,
        ).execute()
        children.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return children

def _download_drive_folder_recursive(service, folder_id, dest_dir, label_prefix,
                                      depth=0, max_depth=10):
    """Recursively download a Drive folder's contents. Returns
    ``(downloaded_list, errors_list, skipped_list)``."""
    downloaded, errors, skipped = [], [], []
    if depth >= max_depth:
        errors.append({
            "url": f"folder_id={folder_id}",
            "error": f"Max recursion depth ({max_depth}) reached at {dest_dir}.",
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

    # Heartbeat at folder boundary + within-folder every 50 files so the
    # user sees progress on huge Drive folders (e.g., SIG/Forest with
    # 1,600+ files) instead of waiting in silence.
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
            if files_done % 50 == 0:
                _sage_progress(f"      …{files_done}/{n_children} files in {label_prefix}")
    return downloaded, errors, skipped

def _download_one_drive_file(service, file_id, label, dest_dir, url_hint=""):
    """Download a single Drive file by ID. Returns a 3-tuple
    ``(downloaded_entry, error_entry, skipped_entry)`` — exactly one is
    non-None."""
    from googleapiclient.http import MediaIoBaseDownload
    try:
        meta = service.files().get(
            fileId=file_id, fields="name,mimeType", supportsAllDrives=True,
        ).execute()
        fname = _sanitize_for_filename(meta["name"])

        if _should_skip_by_extension(fname):
            return None, None, {
                "reason": "filtered", "label": label, "filename": fname,
                "url": url_hint or f"https://drive.google.com/file/d/{file_id}/view",
                "filter": f"extension {Path(fname).suffix.lower()!r} matches DOWNLOAD_FILTER['skip_extensions']",
            }

        mime = meta["mimeType"]
        if mime in _EXPORT:
            export_mime, ext = _EXPORT[mime]
            if not fname.lower().endswith(ext): fname += ext
            request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        elif mime.startswith("application/vnd.google-apps."):
            request = service.files().export_media(fileId=file_id, mimeType="application/pdf")
            if not fname.lower().endswith(".pdf"): fname += ".pdf"
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

    Returns (downloaded_list, errors_list, filtered_list).
    """
    mf_path = ws_root / "_manifest.json"
    if not mf_path.exists():
        return [], [], []

    mf = json.loads(mf_path.read_text())
    drive_entries = [s for s in mf.get("skipped", [])
                     if s.get("reason") == "drive"]
    if not drive_entries:
        return [], [], []

    add_dir = ws_root / "additional_resources"
    add_dir.mkdir(parents=True, exist_ok=True)

    downloaded, errors, filtered = [], [], []
    n_entries = len(drive_entries)
    _sage_progress(f"  {n_entries} Drive resource(s) to fetch for this workspace")

    for idx, entry in enumerate(drive_entries, 1):
        url = entry.get("url", "")
        label = entry.get("label", "unknown")
        _sage_progress(f"  [{idx}/{n_entries}] {label}")

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

    # Update the workspace manifest in place: extend downloaded[], extend
    # errors[], promote filtered into skipped[] with reason="filtered", and
    # remove every original reason="drive" placeholder. Per-file download
    # URLs from folder expansions don't URL-match the original folder URL,
    # so URL-based filtering would leave stale "drive" entries behind. This
    # function processes every reason="drive" entry it picked up, so it's
    # safe to drop them all in one pass.
    mf["downloaded"].extend(downloaded)
    mf["errors"].extend(errors)
    mf["skipped"].extend(filtered)
    mf["skipped"] = [s for s in mf.get("skipped", [])
                     if s.get("reason") != "drive"]
    mf_path.write_text(json.dumps(mf, indent=2))

    # Sidecar text file is informational only and now stale; remove it.
    sidecar = ws_root / "_drive_urls_to_download_later.txt"
    if sidecar.exists():
        sidecar.unlink()

    return downloaded, errors, filtered
```

### Integration is built into `download_workspace()`

The integration of Phase 4 lives inside the `download_workspace()`
function shown earlier. You do not need a separate `service =
_drive_service()` block — it's already wired in. Calling
`download_workspace(...)` runs all four phases (catalog assets,
additional_resources, repository clones, Drive) and writes the
authoritative `_manifest.json`.

If `service is None` because the token isn't present, Phase 4 no-ops
and the final report should mention: *"N Drive resources still pending —
the shared Drive token has not been placed at
`.../CephBlock_/.gdrive_token.json` yet. Ask your project admin."*

### Reporting back to the user

After the run, print a short summary table per workspace: name, # files
downloaded, # skipped (broken out by reason), # errors.

If `_drive_urls_to_download_later.txt` still exists for this workspace
after the run, the shared review-account token was not present at
`/home/jovyan/work/_User-Persistent-Storage_CephBlock_/.gdrive_token.json`.
Tell the reviewer to ask their admin for the token. Do NOT instruct the
reviewer to run OAuth themselves.

**Terminology to use in user-facing output** (do not invent alternative
labels — these are the names users recognize):

- `parent_datasets` → **"catalog assets"** (matches the folder name; do NOT
  say "NDP datasets", "datasets", or "data files")
- `additional_resources` → **"additional resources"** (matches the folder
  and the API field name; treat as one umbrella — do NOT sub-label a count
  as "Google Drive resources" unless the user explicitly asked for the
  Drive-only subset)
- `repository_links` + `parent_repository_links` → **"repositories"**

Example column headers for a summary table:

| Workspace Name | Catalog Assets | Additional Resources | Repositories |

### Parent/child duplicates

`read_workspaces_for_jupyterhub` returns each workspace as **two entries**:
a parent (no `parent_workspace_id`, no `parent_datasets`) and a child
(non-null `parent_workspace_id`, has `parent_datasets`). Always pick the
child entry — the one where `parent_workspace_id is not None` AND
`parent_datasets` is non-empty — when running the download. The parent has
no resources to fetch.

## Troubleshooting

### Common Issues

**Authentication Errors (401):**
- Verify your ACCESS_TOKEN environment variable is set correctly
- Check that the token is valid and not expired
- Ensure the token has the necessary permissions

**Connection Errors:**
- Verify WORKSPACE_API_URL is correct and accessible
- Check network connectivity
- Test with: `curl -v $WORKSPACE_API_URL`

**Empty or Missing Environment Variables:**
```bash
# Check if variables are set
if [ -z "$WORKSPACE_API_URL" ]; then
  echo "Error: WORKSPACE_API_URL not set"
  exit 1
fi

if [ -z "$ACCESS_TOKEN" ]; then
  echo "Error: ACCESS_TOKEN not set"
  exit 1
fi
```

**No Workspaces Returned:**
- Verify the entity ID is correct (if filtering)
- Check that the token has access to the requested workspaces
- Review API permissions

## Notes

- All authentication is handled via the Authorization header with Bearer token
- The API endpoint is specifically designed for JupyterHub integration
- Response data structure depends on the API implementation; use `jq` to explore the structure
- For automated workflows, consider adding retry logic for transient network failures
- Use `-k` flag with curl to skip SSL verification in development (not recommended for production)