---
name: repo-skill-builder
description: >-
  Build one or more ARGUS skills from a GitHub repository containing
  data files — tabular / geospatial vectors (CSV, TSV, Excel, Parquet,
  GeoPackage, GeoJSON, Shapefile, RData), gridded arrays (HDF5,
  NetCDF), or rasters (GeoTIFF). Use when the user gives a github.com
  URL and asks to build, create, or generate skills from the repo's
  data. Fetcher-only: clones the repo into local scratch, classifies
  every file with the shared taxonomy, and prints a ROUTE line, then
  hands off to `tabular-skill-builder` and/or `array-skill-builder`
  for the enumerate → propose → STOP → build pipeline. Two-phase:
  propose a skill plan and STOP for approval, then build on "yes".
---

# Repo Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules define the contract this skill fulfills. Read them
before doing anything else.

1. **You are a fetcher shell.** Your job is to clone the repository
   and run the bundled `fetch.py`, which classifies the working tree
   and prints a `ROUTE:` line. The rest of the build belongs to
   `tabular-skill-builder` and/or `array-skill-builder`, per that
   `ROUTE:` line. Do not enumerate the repo yourself, do not probe
   schemas, do not propose skills here.

2. **Run `fetch.py`; do not hand-roll the clone + walk.** `fetch.py`
   clones with `git clone --depth=1`, walks the tree, classifies each
   file (array / tabular / docs / other) through the shared
   `fetch_common` taxonomy, copies docs to `_docs/`, records the raw
   GitHub URL per data file (for lazy loaders), writes
   `_repo_metadata.json` + `_classification.json`, and prints the
   `ROUTE:` line. It is the single entry point.

3. **Clone/scratch lives at `/tmp/repo-skills/<repo-name>/`.** Never
   under `SAGE_OUTPUT_DIR` or `~/work/` — those are a small
   persistent quota, and the clone is throwaway scratch that `/tmp`
   handles correctly.

4. **The `ROUTE:` line decides the handoff.** After `fetch.py` runs,
   read its `ROUTE:` line and follow the matching branch in Step 2.
   A repo mixing CSV/GPKG tables with GeoTIFF/NetCDF grids routes
   `combined` — both cores run, one gate.

5. **The downstream Step 4 hard STOP applies unchanged.** Whichever
   core you hand off to, you propose the skill plan and END YOUR TURN
   before building. The README gives you a head start on the
   description — it does NOT replace schema grouping, the join/variant
   decision, or the user's confirmation.

## When to Use

Trigger when the user gives a `github.com` URL and asks to build a
skill from the data in the repo. URL shapes:

- `https://github.com/<owner>/<repo>`
- `https://github.com/<owner>/<repo>.git`
- `https://github.com/<owner>/<repo>/tree/<branch>`

Decline (use a different skill) when:

- The URL is a CKAN dataset (`/dataset/<slug>`) → `ckan-skill-builder`.
- The URL is a Zenodo record → `zenodo-skill-builder`.
- The URL is an ArcGIS Feature/Map Service →
  `arcgis-feature-skill-builder`.
- The repo has only code and no data files — `fetch.py` prints
  `ROUTE: none`; tell the user there is nothing queryable to build.

Note: repository **code** (`.py`, `.ipynb`, etc.) is classified
`other` and ignored — this skill builds from the repo's *data*, not
its programs.

## What You Need From the User

Just the GitHub URL. If the user expressed preferences ("merge into
one skill", "split by region"), pass them forward into the downstream
Step 3 grouping — but do not filter files here; the clone brings
everything and the taxonomy decides.

## Steps

### Step 1 — Run `fetch.py`

```bash
python /home/jovyan/.deepagents/agent/skills/repo-skill-builder/fetch.py \
       https://github.com/<owner>/<repo> \
       /tmp/repo-skills/<repo-name>
```

Substitute the actual skills-directory prefix for other runtimes.
`fetch.py` clones, classifies, and ends with a tally and a `ROUTE:`
line, e.g.:

```
Classification
  array   : 2 file(s)  -> array-skill-builder  (of which 2 GeoTIFF/raster)
  tabular : 17 file(s)  -> tabular-skill-builder
  docs    : 5 file(s)  -> read for semantics (_docs/)
  other   : 34 file(s)  -> ignored (…)

ROUTE: combined
```

If the clone fails (private repo, 404, network), `fetch.py` reports
the error — relay it and stop. Do not attempt authentication. If the
repo is very large, `fetch.py` still clones `--depth=1`; if you know
in advance it is huge (>500 MB), confirm with the user first.

### Step 2 — Branch on the `ROUTE:` line

#### `ROUTE: tabular`

Read `tabular-skill-builder`'s `SKILL.md` fully, then follow it from
its **Step 2**, treating the clone as the source directory:

```
read_file /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/SKILL.md
```
```bash
python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
       /tmp/repo-skills/<repo-name>
```

Its **Step 4 hard stop** applies unchanged. When writing each
generated SKILL.md (its Step 8), source the `description` and the
`## Data` `Source:` bullet from `_repo_metadata.json` (repo title,
README description, license) and point provenance at the
`https://github.com/<owner>/<repo>` URL.

#### `ROUTE: array`

Read `array-skill-builder`'s `SKILL.md` fully, then follow it from
its **Step 2**, pointing its inventory at the clone with `--dir` (it
picks up `_repo_metadata.json` + `_docs/` automatically):

```
read_file /home/jovyan/.deepagents/agent/skills/array-skill-builder/SKILL.md
```
```bash
python /home/jovyan/.deepagents/agent/skills/array-skill-builder/inventory.py \
       --dir /tmp/repo-skills/<repo-name> \
       --out /tmp/array-skill-inv/<repo-name>
```

Its **Step 4 hard stop** applies unchanged. For GeoTIFF rasters, the
array builder emits spatial-query helpers (zonal statistics, point
sample) per its Step 6c; the emitted skill lazy-downloads each raster
from the raw GitHub URL `fetch.py` recorded, or bundles it if small.

#### `ROUTE: combined`

The repo holds BOTH array and tabular data — the canonical case is a
GeoTIFF population/quantity raster alongside CSV records and GPKG
admin-boundary polygons. Read **both** downstream `SKILL.md` files,
run **both** inventories on the clone (the array `--dir`, the tabular
on the same dir), then propose at **ONE** gate. Default to a single
combined skill when the layers share a spatial join or key, and state
the exact join in the proposal (e.g. "`zonal_sum(district_geom)` sums
the population raster inside each GPKG district polygon; both are
EPSG:4326"). This is the same combined flow `zenodo-skill-builder`'s
SKILL.md documents. Do not build before the user approves.

#### `ROUTE: none`

No data files — only code, or an empty repo. Tell the user what the
repo contains (the classification tally names the categories) and
stop; there is nothing queryable to build.

### Step 3 — Clean up the clone

After the downstream builder has written the skill AND the user has
verified it, delete the clone. It holds the full repo — often
hundreds of MB of data — that the finished skill no longer needs (a
bundled skill copied what it keeps into `_skills_/<name>/data/`; a
lazy-download skill re-fetches from the raw GitHub URL).

```python
import shutil
shutil.rmtree("/tmp/repo-skills/<repo-name>")
```

If the array builder was involved, also remove its inventory scratch
(`/tmp/array-skill-inv/<repo-name>`). Never delete the emitted
`_skills_/<skill-name>/` directory — that is the product. Report the
freed space.

## Things to Avoid

- **Do not enumerate the repo yourself.** No `ls`-then-analyze, no
  manual `find` walks, no per-file `head`. `fetch.py` + the downstream
  inventories are the entry points.

- **Do not reclassify files by hand.** The taxonomy in
  `fetch_common.py` is the contract. If a file looks misclassified,
  raise it in your proposal and let the user decide; do not silently
  move files between categories.

- **Do not build from the repo's code.** `.py` / `.ipynb` modelling
  scripts are classified `other` and ignored. Exposing what those
  programs *do* is a separate capability, not this skill.

- **Do not skip the downstream hard stop** just because the README
  gave you a title and description. Those feed the generated skill's
  description; they do not replace grouping, the join/variant
  decision, or the user's confirmation.

- **Do not attempt authentication.** Public repos only. On a clone
  403/404, report to the user and stop.

- **Do not use `SAGE_OUTPUT_DIR` as the clone destination.**
  `/tmp/repo-skills/<repo-name>/` is correct.
