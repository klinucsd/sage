---
name: zenodo-skill-builder
description: >-
  Build ARGUS skills from a Zenodo record — the research-data archive at
  zenodo.org. Use when the user gives a Zenodo record URL
  (`zenodo.org/records/<id>`), a bare record id, or a `10.5281/zenodo.<id>`
  DOI and asks to build, create, or generate a skill from it. Fetcher-only:
  downloads the record's files, classifies them as array (HDF5/NetCDF),
  tabular (CSV/Excel/Parquet/shapefile), or documentation, then hands off
  to `array-skill-builder`, `tabular-skill-builder`, or both. Handles
  records that mix gridded and tabular data.
---

# Zenodo Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

1. **You are a fetcher shell.** Your job is to download the record's
   files, classify them, and hand off. Do not inventory schemas. Do
   not propose skills yourself. Do not write any `SKILL.md`. Once
   `fetch.py` finishes, the rest of the build belongs to
   `array-skill-builder` and/or `tabular-skill-builder`.

2. **Use the bundled `fetch.py`, not a custom script.** It handles
   record-id resolution (URL / bare id / DOI), the Zenodo API call,
   downloads, archive unpacking, format classification, and
   provenance capture. Do not call the Zenodo API yourself first "to
   peek at the files" — `fetch.py` does one metadata call plus one
   download per file, and its stdout summary tells you everything.

3. **Download to `/tmp/zenodo-skills/<record-id>/`.** Never to
   `SAGE_OUTPUT_DIR` or `~/work/` — those live on a persistent volume
   with a 10 GB user quota, and research records routinely run to
   hundreds of MB. `/tmp/` is pod-local and sized for exactly this.

4. **The ROUTE line in `fetch.py`'s output decides what happens next.**
   Do not second-guess it by re-reading the file list. Step 2 below
   maps each route to its handoff.

5. **Do not read `_classification.json` or `_zenodo_metadata.json`
   wholesale into your context.** They are lookup files for scripts.
   `fetch.py`'s stdout already summarises both. Downstream steps read
   specific fields from them with `json.load` in a script.

## When to Use

Trigger when the user provides a Zenodo reference and asks to build a
skill. Accepted shapes:

- `https://zenodo.org/records/3660832`
- `https://zenodo.org/record/3660832` (legacy singular path)
- `3660832` (bare record id)
- `10.5281/zenodo.3660832` (DOI)

Decline (route elsewhere) when:

- The URL is a CKAN dataset — `ckan-skill-builder`.
- The URL is a github.com repo — `repo-skill-builder`.
- The URL is an ArcGIS Feature/Map Service — `arcgis-feature-skill-builder`.
- The URL is a single direct `.h5` file with no containing record —
  `array-skill-builder` handles that directly with its `--url` flag.

## What You Need From the User

Just the Zenodo reference. Title, creators, DOI, license, description,
and keywords are all captured automatically into
`_zenodo_metadata.json` and threaded into the built skill's
frontmatter downstream.

If the user expressed preferences ("only the 2019 files", "skip the
correlation table"), pass them forward in your handoff message so the
downstream builder's proposal respects them. Do not filter files
yourself — the fetcher takes everything by design.

## Steps

### Step 1 — Run `fetch.py`

Under the ARGUS install layout:

```bash
python /home/jovyan/.deepagents/agent/skills/zenodo-skill-builder/fetch.py \
       <zenodo-url-or-id> \
       /tmp/zenodo-skills/<record-id>
```

Substitute the actual skills directory prefix for other runtimes
(Claude Code, Codex).

The script prints a classification tally and a `ROUTE:` line:

```
Classification
  array   : 1 file(s)  -> array-skill-builder
  tabular : 2 file(s)  -> tabular-skill-builder
  docs    : 1 file(s)  -> read for semantics (_docs/)

ROUTE: combined
```

Files land in the out directory; documentation is separated into
`_docs/`. Archives (`.zip`, `.tar.gz`) are unpacked and their contents
re-classified automatically.

### Step 2 — Branch on the route

#### `ROUTE: array`

The record holds only gridded/array data. Read
`array-skill-builder`'s `SKILL.md` fully, then follow it from its
**Step 2**, invoking its inventory with `--dir`:

```
read_file /home/jovyan/.deepagents/agent/skills/array-skill-builder/SKILL.md
```

```bash
python /home/jovyan/.deepagents/agent/skills/array-skill-builder/inventory.py \
       --dir /tmp/zenodo-skills/<record-id> \
       --out /tmp/array-skill-inv/zenodo-<record-id>
```

The inventory picks up `_zenodo_metadata.json` and `_docs/` from the
staged directory automatically, so record provenance and
documentation flow through without extra glue.

#### `ROUTE: tabular`

The record holds only tabular/vector data. Read
`tabular-skill-builder`'s `SKILL.md` fully, then follow it from its
**Step 2**, treating the download directory exactly as it treats a
cloned repo:

```
read_file /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/SKILL.md
```

```bash
python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
       /tmp/zenodo-skills/<record-id>
```

One Zenodo-specific refinement to that skill's Step 8 (Write each
SKILL.md): source the frontmatter `description`, the `## Data`
section's `Source:` bullet, and the citation from
`_zenodo_metadata.json` (`title`, `description`, `creators`, `doi`,
`license`, `source_url`) rather than inferring them from filenames.

#### `ROUTE: combined`

**The record holds both array and tabular data.** This is common for
simulation and observational datasets — a model's gridded output
alongside per-node attribute tables, or an instrument's raw arrays
alongside a summary table.

Do **not** blindly build two separate skills. In most such records the
files are *linked* — they describe the same entities and share an
index, grid id, station id, or timestamp. Splitting them produces two
skills that each answer half a question and neither can join.

Run **both** inventories, then propose at **one** gate:

```bash
python /home/jovyan/.deepagents/agent/skills/array-skill-builder/inventory.py \
       --dir /tmp/zenodo-skills/<record-id> \
       --out /tmp/array-skill-inv/zenodo-<record-id>

python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
       /tmp/zenodo-skills/<record-id>
```

(The tabular inventory ignores `.h5`/`.nc` files; the array inventory
ignores everything that is not an array format. They do not collide.)

Then read **both** builders' `SKILL.md` files, and decide the shape:

**Default — ONE combined skill**, when the array and tabular files
share a join key. Evidence to look for: matching row counts against a
grid dimension, an explicit id column (`node`, `cell`, `station`,
`sim_index`) whose range matches an HDF5 dataset's shape, a shared
time axis, or documentation that describes them as one dataset. The
emitted skill carries loaders from both conventions:

- `pd.read_csv` / `read_excel`-style loaders for the tables
  (`tabular-skill-builder`'s Step 8 conventions)
- `h5py` open + CHANNELS-tuple loaders for the arrays
  (`array-skill-builder`'s Steps 5–7 conventions, including the
  tuple-guard assert and the no-silent-cleaning rule)
- **A `## Cross-reference` section** stating exactly how to join them
  — which column maps to which array index, and any offset. This
  section is the reason the combined skill is worth building; do not
  omit it.

**Two skills**, only when the files are genuinely unrelated — a
different study area, a different entity, no shared key. Say so
explicitly in the proposal and explain why you are splitting.

Either way, **Step 4 of the downstream builder is still a hard stop**:
present the plan and end your turn. Let the user confirm the
one-skill-vs-two decision before building — it is exactly the kind of
judgement call the gate exists for.

#### `ROUTE: none`

No array or tabular files. Tell the user what the record does contain
(the classification tally names the categories) and stop. Do not
improvise a build from PDFs or images.

### Step 3 — Read the documentation before writing any SKILL.md

Whichever route ran, the `_docs/` directory holds the record's README,
technical PDF, and text documentation. **These carry the semantics the
data files do not.** Column names like `Q`, `TOC`, `SedRate` and HDF5
dataset names like `Figure_04/Profile_8099/Hydrate_05` are opaque
without them.

Both downstream builders have a documentation-reading step; make sure
it runs against `/tmp/zenodo-skills/<record-id>/_docs/`. Extract PDF
text with `pypdf` (`pip install --user pypdf` if missing).

If `_docs/` is empty, say so in the proposal and note in the built
skill's Caveats that channel semantics are inferred from names and
conventions alone, not from publisher documentation.

### Step 4 — Cleanup

After the skill is built and the user has verified a working query,
remove the staged download:

```python
import shutil
shutil.rmtree("/tmp/zenodo-skills/<record-id>")
```

Leave the built skill in `_skills_/<skill-name>/` — that is the
product. If the built skill bundles data files (small records only,
see `array-skill-builder`'s bundle-vs-lazy threshold), make sure they
were copied into the skill directory *before* deleting the staging
area.

## Things to Avoid

- **Do not call the Zenodo API yourself.** `fetch.py` is the single
  entry point. No `curl`, no `urllib` peek, no "let me just check the
  file list first."
- **Do not reclassify files by hand.** The extension map in
  `fetch.py` is the contract. If a file is misclassified — a `.txt`
  that is really documentation, or a `.dat` that is really an array —
  mention it in the proposal and let the user decide; do not silently
  move files between categories.
- **Do not skip the hard stop.** The downstream builders' proposal
  gates apply unchanged. A fetcher handing off does not consume the
  gate.
- **Do not build from an embargoed or restricted record.** If
  `fetch.py` reports an HTTP 403/404 from the API, the record is not
  publicly accessible; tell the user rather than trying alternate
  URLs.
