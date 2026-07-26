---
name: local-skill-builder
description: >-
  Build one or more ARGUS skills from a LOCAL filesystem path — a directory
  (or a single file) already on this machine, containing data files:
  tabular / geospatial vectors (CSV, TSV, Excel, Parquet, GeoPackage, GeoJSON,
  Shapefile, RData), gridded arrays (HDF5, NetCDF), or rasters (GeoTIFF). Use
  when the user gives a local path (absolute like `/home/…`, `~/…`, or a
  relative folder) rather than a URL, and asks to build, create, or generate
  skills from the data in that folder — including a dataset the user already
  downloaded or a repository already cloned locally. Reads the folder IN PLACE:
  it never downloads, copies, or moves the data, so it works on arbitrarily
  large local datasets. Classifies the files with the shared taxonomy, prints a
  ROUTE line, then hands off to `tabular-skill-builder` and/or
  `array-skill-builder` for the enumerate → propose → STOP → build pipeline.
---

# Local-Path Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

1. **You are a fetcher shell, and acquisition is a no-op.** The data is already
   on disk. Your job is to run the bundled `fetch.py`, which classifies the
   folder and prints a `ROUTE:` line; the rest of the build belongs to
   `tabular-skill-builder` and/or `array-skill-builder`, per that line.

2. **Read the folder IN PLACE. Never copy, move, download, or write into it.**
   This is the whole point of building from a local path: the data may be huge
   and must not be duplicated. `fetch.py` writes only small sidecars to a
   `/tmp` scratch; the core inventories read the data in place and are directed
   (Step 2) to write their own output to `/tmp`, not into the user's folder.

3. **The emitted skill BUNDLES its data.** A local source has no remote URL to
   lazily re-fetch from, so each core copies the data it keeps into
   `_skills_/<name>/data/`. Do not emit a lazy-download loader that points at a
   `file://` path (that would break the moment the notebook moves to another
   machine) unless a single file is too large to bundle, in which case say so
   in the Caveats.

4. **The `ROUTE:` line decides the handoff.** After `fetch.py` runs, follow the
   matching branch in Step 2. A folder mixing CSV/GPKG tables with
   GeoTIFF/NetCDF grids routes `combined` — both cores run, one gate.

5. **Documentation lives at the top level, not in `_docs/`.** `fetch.py` lists
   the folder's own README / data-dictionary / PDF files; read those directly
   for semantics and provenance. Provenance is the folder itself (its name and
   README), since there is no catalog record.

## When to Use

Trigger when the user gives a **local filesystem path** and asks to build a
skill from the data in it. Path shapes: `/home/jovyan/work/my-dataset`,
`~/Downloads/study-folder`, `./data`, or a single file like
`~/data/measurements.nc`.

Decline (use another skill) when the source is a URL: a github.com repo
(`repo-skill-builder`), a CKAN/NDP dataset (`ckan`/`ndp`), a Zenodo record
(`zenodo`), an S3 prefix (`s3`), or an ArcGIS service
(`arcgis-feature-skill-builder`).

Repository **code** (`.py`, `.ipynb`, etc.) in the folder is classified `other`
and ignored — this skill builds from the folder's *data*, not its programs.

## Steps

### Step 1 — Run `fetch.py`

```bash
python /home/jovyan/.deepagents/agent/skills/local-skill-builder/fetch.py \
       <local-path>
```

Substitute the actual skills-directory prefix for other runtimes, and the
user's path for `<local-path>`. `fetch.py` classifies the folder in place and
ends with a tally, a `ROUTE:` line, and the list of documentation files to
read. If the path does not exist, it reports the error — relay it and stop.

### Step 2 — Branch on the `ROUTE:` line

In every branch, point the core inventory at the **original local path** as its
`--dir`, and direct its output to `/tmp` so nothing is written into the user's
folder.

#### `ROUTE: tabular`

Read `tabular-skill-builder`'s `SKILL.md` fully, then follow it from its
**Step 2**, with the inventory JSON redirected out of the folder:

```
read_file /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/SKILL.md
```
```bash
python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
       <local-path> --out-json /tmp/local-skills/<name>/_tabular_inventory.json
```

Its **Step 4 hard stop** applies unchanged. Source the generated skill's
description and `## Data` provenance from the folder's README (the files
`fetch.py` listed) and the folder name; there is no catalog record.

#### `ROUTE: array`

Read `array-skill-builder`'s `SKILL.md` fully, then follow it from its
**Step 2**, pointing its inventory at the local path with `--dir` (it already
writes `_inventory.json` to `--out`, so it does not touch the folder):

```
read_file /home/jovyan/.deepagents/agent/skills/array-skill-builder/SKILL.md
```
```bash
python /home/jovyan/.deepagents/agent/skills/array-skill-builder/inventory.py \
       --dir <local-path> \
       --out /tmp/array-skill-inv/<name>
```

Because the files are local, the emitted skill bundles them (there is no source
URL to lazy-download). Its **Step 4 hard stop** applies unchanged. For semantics
read the folder's own documentation directly (`fetch.py` listed it), since it is
at the top level rather than in a fetcher-staged `_docs/`.

#### `ROUTE: combined`

The folder holds BOTH array and tabular data. Read **both** downstream
`SKILL.md` files, run **both** inventories on the local path (array with
`--dir … --out /tmp/…`, tabular with `… --out-json /tmp/…`), then propose at
**ONE** gate. Default to a single combined skill when the layers share a spatial
join or key, and state the join in the proposal. Do not build before the user
approves.

#### `ROUTE: none`

No data files — only code, docs, or an empty folder. Tell the user what the
folder contains (the classification tally names the categories) and stop.

### Step 3 — Clean up

Delete only the `/tmp` scratch you created
(`/tmp/local-skills/<name>/`, `/tmp/array-skill-inv/<name>`). **Never delete or
modify anything under the user's local path** — it was read in place. Never
delete the emitted `_skills_/<skill-name>/` — that is the product.

## Things to Avoid

- **Do not copy or download the data.** It is already local; the reason to build
  from a path rather than a URL is to avoid duplicating a possibly-large folder.
- **Do not write into the user's folder.** `fetch.py` sidecars and both core
  inventories are directed to `/tmp`. If any step would write into the source
  folder, redirect it.
- **Do not enumerate the folder yourself.** `fetch.py` and the downstream
  inventories are the entry points — no `ls`-then-analyze, no per-file `head`.
- **Do not build from the folder's code.** `.py` / `.ipynb` files are `other`
  and ignored.
- **Do not skip the downstream hard stop** just because the README gave you a
  description. It does not replace schema grouping, the join/variant decision,
  or the user's confirmation.
- **Do not emit a `file://` lazy-download loader** for bundled-size data; bundle
  it, so the skill travels with the notebook.
