---
name: ckan-skill-builder
description: >-
  Build one or more ARGUS skills from a CKAN dataset (data.gov,
  data.cnra.ca.gov, National Data Platform, and other CKAN-based
  open-data portals). Use when the user provides a CKAN dataset URL —
  either the `/api/3/action/package_show?id=<slug>` API URL or the
  `/dataset/<slug>` browse URL — and asks to build, create, or
  generate skills from it. Fetcher-only: downloads and classifies the
  dataset's resources (unpacking `.zip` / `.tar.gz` archives), then
  routes to `tabular-skill-builder` (CSV/Excel/…), `array-skill-builder`
  (HDF5/NetCDF), or both. Whichever downstream builder runs, it stops
  for the user's approval before building.
---

# CKAN Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules define the contract this skill fulfills. Read them
before doing anything else.

1. **You are a fetcher shell.** Your only responsibility is to
   download + classify the dataset's resources and hand off to the
   right core builder. Do not write an inventory script. Do not probe
   schemas. Do not propose skills yourself. Once `fetch.py` finishes,
   the rest of the build belongs to `tabular-skill-builder` and/or
   `array-skill-builder`, per the `ROUTE:` line `fetch.py` prints.

2. **Use the bundled `fetch.py`, not a custom script.** The skill
   ships `fetch.py` next to this `SKILL.md`. It handles CKAN URL
   resolution, downloads every resource, classifies each as array /
   tabular / docs, unpacks `.zip` / `.tar.gz` archives and re-classifies
   their contents, captures dataset metadata, and prints a `ROUTE:`
   line. Do not re-implement it. Do not call the CKAN API yourself
   first "to peek at the resources" — `fetch.py`'s stdout summary is
   what you read to know what happened.

3. **Download to `/tmp/ckan-skills/<dataset-slug>/`.** Pod-local
   scratch, never `SAGE_OUTPUT_DIR` or `~/work/` (those have a 10 GB
   quota that a `.tar.gz` of HDF5 can overwhelm). The staged directory
   is what the downstream inventory reads — tabular-skill-builder's
   `inventory.py` on the dir, or array-skill-builder's
   `inventory.py --dir` on it.

4. **The `ROUTE:` line decides the handoff.** After `fetch.py` runs,
   read its `ROUTE:` line and follow the matching branch in Step 3.
   Load the downstream builder's `SKILL.md` fully before continuing —
   the handoff is real, not implicit.

5. **STOP for user approval before building — no exceptions.**
   Whichever downstream builder you hand off to, its proposal gate
   applies unchanged: propose the skill plan and END YOUR TURN; build
   only after the user replies "yes" in the next `%%ask` cell. A
   fetcher handing off does not consume that gate.

## When to Use

Trigger this skill when the user provides a CKAN dataset URL and
asks to build a skill from it. Example URL shapes:

- `https://data.cnra.ca.gov/api/3/action/package_show?id=<slug>`
- `https://data.cnra.ca.gov/dataset/<slug>`
- `https://catalog.data.gov/dataset/<slug>`
- `https://<any-ckan-portal>/api/3/action/package_show?id=<slug>`

Decline (do not use this skill) when:

- The URL is a github.com repo — use `repo-skill-builder`.
- The URL is an ArcGIS Feature/Map Service — use
  `arcgis-feature-skill-builder`.
- The dataset has no buildable data (only PDFs, HTML pages, raster
  imagery, or documentation) — `fetch.py` prints `ROUTE: none` (or
  `ROUTE: raster`); tell the user there is nothing queryable to
  build here.

## What You Need From the User

Just the CKAN dataset URL. Everything else — dataset title,
description, tags, license, per-resource metadata — is captured
automatically from CKAN's `package_show` API and saved to
`_ckan_metadata.json` in the download directory.

If the user has expressed preferences ("only download the annual
files", "skip the monthly ones"), pass those preferences forward
in your handoff message so `tabular-skill-builder`'s Step 3 grouping
respects them — but do not attempt to filter resources yourself in
this skill; the fetcher takes everything tabular by design.

## Steps

### Step 1 — Determine the dataset slug (best guess is fine)

The dataset slug is CKAN's `name` field — typically the string after
`id=` in a `package_show` URL, or the last path segment of a
`/dataset/<slug>` URL. Use whichever you can read directly from the
user's URL as your download-directory name. `fetch.py` prints
CKAN's authoritative slug in its output; if it differs, that's
fine — the directory name is arbitrary and only used for scratch
paths.

### Step 2 — Run `fetch.py`

**Do not read the CKAN API yourself first.** `fetch.py` does one
metadata GET plus one download per resource; anything you learn by
pre-calling the API is redundant with what the script captures.

Under the ARGUS install layout the command is:

```bash
python /home/jovyan/.deepagents/agent/skills/ckan-skill-builder/fetch.py \
       <ckan-url> \
       /tmp/ckan-skills/<dataset-slug>
```

Substitute the actual skills directory prefix for other runtimes
(Claude Code, Codex).

The script downloads + classifies every resource, unpacks archives,
and ends with a classification tally and a `ROUTE:` line:

```
Classification
  array   : 1 file(s)  -> array-skill-builder
  tabular : 2 file(s)  -> tabular-skill-builder
  docs    : 1 file(s)  -> read for semantics (_docs/)

ROUTE: combined
```

It also separates documentation into `_docs/` and writes
`_ckan_metadata.json` (provenance) + `_classification.json`
(per-file class + source URLs). Archives (`.zip`, `.tar.gz`) are
unpacked and their contents re-classified automatically.

### Step 3 — Branch on the `ROUTE:` line

#### `ROUTE: tabular`

Read `tabular-skill-builder`'s `SKILL.md` fully, then follow it from
its **Step 2**, treating the download directory like a cloned repo:

```
read_file /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/SKILL.md
```
```bash
python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
       /tmp/ckan-skills/<dataset-slug>
```

Its **Step 4 hard stop** applies unchanged — propose and wait
(pre-flight rule 5) before building. One CKAN refinement to its
Step 8 (Write each SKILL.md): source the `description`, the `## Data`
`Source:` bullet, and provenance from `_ckan_metadata.json`
(`title`, `notes`, `tags`, `license_title`, `organization`,
`source_url`) rather than inferring from filenames.

#### `ROUTE: array`

Read `array-skill-builder`'s `SKILL.md` fully, then follow it from
its **Step 2**, pointing its inventory at the staged directory with
`--dir` (it picks up `_ckan_metadata.json` + `_docs/` automatically):

```
read_file /home/jovyan/.deepagents/agent/skills/array-skill-builder/SKILL.md
```
```bash
python /home/jovyan/.deepagents/agent/skills/array-skill-builder/inventory.py \
       --dir /tmp/ckan-skills/<dataset-slug> \
       --out /tmp/array-skill-inv/<dataset-slug>
```

Its **Step 4 hard stop** applies unchanged — propose and wait before
building.

#### `ROUTE: combined`

The dataset holds BOTH array and tabular data (common for simulation
outputs: gridded HDF5 + per-node attribute tables). Read **both**
downstream `SKILL.md` files, run both inventories, then propose at
**one** gate — default to a single combined skill when the files
share a join key, and state the exact join in the proposal. This is
the same combined flow `zenodo-skill-builder`'s SKILL.md documents;
follow it. Do not build before the user approves the plan.

#### `ROUTE: raster` or `ROUTE: none`

Nothing buildable yet (raster-only means GeoTIFF, which
`array-skill-builder` cannot read yet). Tell the user what the
dataset contains — the classification tally and
`_skipped_resources.json` name the categories — and stop.

### Step 4 — Clean up the staged download

After the downstream builder has written the skill AND the user has
verified it, delete the staged directory. It holds the raw resources
— often hundreds of MB — that the finished skill no longer needs
(a bundled skill has already copied what it keeps into
`_skills_/<name>/data/`; a lazy-download skill re-fetches from the
source URL).

```python
import shutil
shutil.rmtree("/tmp/ckan-skills/<dataset-slug>")
```

Never delete the emitted `_skills_/<skill-name>/` directory — that
is the product. Report the freed space in your summary.

## Things to Avoid

- **Do not enumerate resources yourself.** No manual CKAN API calls,
  no `curl <resource url>` loop, no per-resource `requests.get`.
  `fetch.py` is the single entry point.

- **Do not reclassify files by hand.** The format taxonomy inside
  `fetch_common.py` is the contract. If a resource looks
  misclassified, mention it in your handoff and let the user decide;
  do not silently move files between categories.

- **Do not skip the downstream hard stop** just because CKAN gave you
  a title and description. Those hints feed the generated SKILL.md's
  description; they do not replace schema grouping (or, for combined,
  the join decision) and the user's confirmation of the plan.

- **Do not attempt CKAN authentication.** Public datasets only.
  If `fetch.py` gets an HTTP 401/403 the download error is
  recorded in `_skipped_resources.json`; tell the user the
  resource is behind auth and stop.

- **Do not use `SAGE_OUTPUT_DIR` as the download destination.**
  `/tmp/ckan-skills/<slug>/` is correct. `SAGE_OUTPUT_DIR` is on a
  small persistent quota and is for skill outputs only, not for
  build scratch.
