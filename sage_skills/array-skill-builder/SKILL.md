---
name: array-skill-builder
description: >-
  Build an ARGUS skill from HDF5 / NetCDF array data — a directory of
  staged files handed over by a fetcher shell (`zenodo-skill-builder`,
  `ckan-skill-builder`), or a direct `.h5` / `.hdf5` / `.nc` URL.
  Handles single-file datasets and multi-file collections whose files
  share a schema and get queried as one logical dataset (e.g. 12
  monthly files → one `load_month(m)` interface). Reads sibling
  README / PDF documentation for physical-quantity labels. Two-phase:
  inventory + propose + STOP, then generate the SKILL.md after user
  approval. Use for gridded/array scientific data.
---

# Array Skill Builder

This is a **meta-skill**: instructions for the agent on how to author
a new skill that exposes an HDF5 collection (or a single HDF5 file) to
natural-language queries. Follow the steps below to produce a skill
that lands in `_skills_/<skill-name>/SKILL.md` next to the user's
notebook.

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

Non-negotiable. Detailed rationale is in the steps below.

1. **Your FIRST tool call on the source URL is running this skill's
   bundled `inventory.py`.** Not `curl`, not `wget`, not a custom
   Python script to open the file yourself. Not `pypdf` on the docs
   sidecars. The bundled inventory downloads the HDF5 file(s),
   walks the group tree, computes schema fingerprints, groups files
   by fingerprint, infers the partition axis, fetches sibling
   documentation from the record, and prints a compact summary. It
   is the *first* picture you get.

2. **HDF5 alone almost never carries the semantics you need.**
   Physical-quantity meanings, dimension units, calibration
   information, and dataset provenance normally live in a sibling
   README, technical PDF, or text file that Zenodo / CKAN ship
   alongside the data. The bundled inventory downloads these into
   `<out-dir>/_docs/` and inlines short text files into the JSON
   under `documentation[].text_head`. **Before writing the SKILL.md
   you MUST read every documentation sidecar** (PDFs via `pypdf`,
   text files via the inlined `text_head` field, or with a Python
   script for anything larger). Emitted skills whose descriptions
   and Caveats are derived from HDF5 alone are consistently thin
   and mislabelled.

3. **Never load `_inventory.json` wholesale into your context.**
   The JSON is a *lookup table for scripts*, not reading material.
   On a 12-file collection it runs ~60 KB (~15 000 tokens); on a
   large one it is far bigger. All of these are violations:

   - `cat <out-dir>/_inventory.json` (with or without `head -c`)
   - `read_file` on `_inventory.json`, paginated or not
   - `python -c "print(open('_inventory.json').read())"`
   - any script whose stdout echoes the raw JSON

   Instead: `inventory.py`'s **stdout summary** is what you use for
   the Step 4 proposal — it already contains the group count, file
   counts, partition axis, dialect splits, and data-quality flags.
   When a later step needs a specific field (per-file URLs, channel
   stats), write a script that `json.load`s the file and prints
   *only the few values it needs*.

   The cost is not hypothetical: on shared-GPU vLLM endpoints (NRP
   especially) prompt length scales response time superlinearly
   under KV-cache pressure. A single wholesale JSON read has been
   observed to add tens of thousands of tokens to every subsequent
   call in the build.

4. **At most ONE post-inventory exploration script.** After
   `inventory.py`, if you need more context — reading the doc
   sidecars, pulling channel stats, checking dialect variants —
   write **one** script that batches all of it into a single run.
   Do not write a second. Names like `read_docs.py` +
   `inspect_inv.py` + `dump_group.py` + `flags.py` are four scripts
   doing what one should; that pattern chases completeness instead
   of proposing. If your one script's output leaves something
   unclear, **propose anyway** and flag the uncertainty as an open
   question at the Step 4 gate — the user can correct it there,
   which is exactly what the gate is for.

   Write helper scripts **inside the slug directory**
   (`/tmp/array-skill-inv/<slug>/explore.py`), never in the shared
   parent, so Step 9b's cleanup removes them with everything else.

5. **One Python script per phase, never per-file actions.** For
   docs reading, for build — write ONE script and execute it. No
   `python -c` per file. No `curl` per URL.

6. **Step 4 is a HARD STOP between inventory and build.** After
   running inventory and drafting a proposal, you write the
   proposal and **end your turn**. Do NOT write the SKILL.md, do
   NOT start any build script. The user replies in a follow-up
   `%%ask` cell with "yes", "no", or edits, and ONLY THEN do you
   proceed to Steps 5–9. Building without explicit user approval
   defeats the entire point of this skill — the user can no longer
   redirect grouping decisions or correct dataset-semantic labels
   the inventory got wrong.

7. **CHANNELS in the emitted SKILL.md MUST use tuple values, even
   for single-candidate channels.** This is the array analogue of
   tabular-skill-builder's "column-name canonicalization." The
   inventory identifies channels whose source-name varies across
   files in a group (Igor Pro unit-suffix vs bare dialects are the
   canonical case); tuple values let the loader try each candidate
   source name in order and pick whichever exists in a given file.
   A bare-string value gets iterated character-by-character by the
   candidate loop and silently drops the channel from every file.
   The emitted skill code MUST also carry the runtime assert:
   `assert all(isinstance(v, tuple) for v in CHANNELS.values())`.

8. **Do not silently clean data in the emitted loader.** If the
   inventory flags channels for data-quality anomalies (extreme
   magnitudes, order-of-magnitude spread), document them in the
   generated SKILL.md's Caveats section as **caveat-plus-recipe**
   entries — describe the pattern and provide a copy-paste
   plausibility filter — but do NOT mutate values in `load_*`.
   The scientist owns cleaning decisions; the skill catalogs and
   surfaces. Timestamps are the one narrow exception: corrupt
   timestamps can't be represented usefully, so the loader may
   drop those rows, but that drop must be documented.

If your next action would violate any of these, stop and re-plan.

---

## When to Use

This skill is a **core builder**, normally reached via a fetcher
shell. Two entry paths:

- **From a fetcher (the usual path).** `zenodo-skill-builder` or
  `ckan-skill-builder` has already downloaded and classified a
  record's files and hands you a staged directory. You run
  `inventory.py --dir <staged-dir>`. Record provenance
  (`_zenodo_metadata.json`) and documentation (`_docs/`) are picked
  up from that directory automatically.
- **Direct single-file URL.** A `%%skill-build` cell whose body is a
  bare `.h5` / `.hdf5` / `.nc` URL with no containing record. Use
  `inventory.py --url <url>`.

**Decline** (route elsewhere) when:

- The source is a **Zenodo record** — `zenodo-skill-builder` owns
  Zenodo. It handles record resolution, mixed array+tabular records,
  and archive unpacking, then calls this skill with `--dir`. Do not
  call the Zenodo API from here.
- The source is a **CKAN dataset** — `ckan-skill-builder`, same
  reasoning.
- The data is purely tabular (CSV / XLSX / Parquet / GeoPackage /
  GeoJSON / Shapefile / RData) — `tabular-skill-builder`.
- The URL is an ArcGIS Feature/Map Service —
  `arcgis-feature-skill-builder`.
- The URL is a GitHub repo — `repo-skill-builder`.

**Mixed records** (array + tabular in one dataset) are handled by the
fetcher's `combined` route, which runs both inventories and composes
a single skill with a cross-reference section. If you were invoked
directly on a staged directory that also contains tabular files, say
so in your proposal rather than silently ignoring them.

## What This Skill Produces

A complete ARGUS skill under `SAGE_OUTPUT_DIR/_skills_/<skill-name>/`,
with this shape:

```
_skills_/<skill-name>/
└── SKILL.md            # description, channel catalog, load helpers, examples, caveats
```

For collections below ~100 MiB total, the skill may bundle data files
in a `data/` subdirectory. Larger collections default to **lazy
download**: the emitted `load_*()` helpers fetch each file on first
use to `/tmp/<skill-name>-cache/`, using the remote source URLs (the
inventory's stdout prints the full `partition → source URL` map, and
each file's `source_url` is in `_inventory.json` — use those, never a
`file://` staging path).

**Bundled-data path resolution — never hardcode an absolute path.**
When you bundle data under `data/`, the loader must find it relative
to the skill's own location, because the skill can be promoted to the
global registry or copied elsewhere and an absolute
`SAGE_OUTPUT_DIR`-rooted path breaks the moment it moves. Emit this
exact `_data_dir()` helper (substituting the real skill name) and
route every bundled read through it:

```python
from pathlib import Path

_SKILL_NAME = "<skill-name>"

def _data_dir():
    """Locate the bundled data/ directory relative to the skill, wherever
    the skill currently lives. Searches the current working tree's
    `_skills_/<name>/data` and the global registry — no absolute path."""
    candidates = []
    # 1. Alongside this skill when it was copied next to a running script.
    if "__file__" in globals():
        candidates.append(Path(__file__).resolve().parent / "data")
    # 2. Under any _skills_/<name>/ in the cwd or an ancestor (local skills).
    cwd = Path.cwd()
    for base in (cwd, *cwd.parents):
        candidates.append(base / "_skills_" / _SKILL_NAME / "data")
    # 3. The global registry (promoted skills).
    candidates.append(
        Path.home() / ".deepagents" / "agent" / "skills" / _SKILL_NAME / "data")
    for c in candidates:
        if c.is_dir():
            return c
    raise FileNotFoundError(
        f"bundled data/ for '{_SKILL_NAME}' not found; searched "
        f"{[str(c) for c in candidates]}")
```

Do NOT emit `SKILL_DIR = Path("/home/jovyan/work/.../_skills_/...")`
or any other absolute literal. Do NOT emit hallucinated imports such
as `import _sage_pip_install_helper` — if the runtime provides a pip
helper it is already in scope; guard optional deps with a plain
`try: import h5py / except ImportError:` instead.

The skill is written to the notebook's per-notebook `SAGE_OUTPUT_DIR`
and is available to subsequent `%%ask` cells automatically — no global
install.

## What You Need

A staged directory (from a fetcher) or a direct file URL. The user
does not supply the skill name, the file format, the channel catalog,
or anything else. Your job is to inspect the source, gather semantic
hints from the sibling documentation, propose a sensible plan, and —
after approval — write the skill.

If the user has expressed preferences ("call it `atlasm5`", "skip
the rain channels", "keep only the gases"), incorporate them into
the proposal.

## Steps to Build the Skill

### Step 1 — Identify your input

- **A staged directory** (from `zenodo-skill-builder` /
  `ckan-skill-builder`) — the usual path. The fetcher's handoff
  message names it. It contains the array files, plus
  `_zenodo_metadata.json` (or `_ckan_metadata.json`) and a `_docs/`
  subdirectory. Use `--dir`.
- **A direct HDF5 URL** with no containing record — use `--url`.
  Flag in your proposal that **no documentation sidecars are
  available**, so the skill's semantics will be
  filename/attribute-derived only. Encourage the user to point at
  the containing record instead if one exists.
- **A Zenodo or CKAN URL** — you should not be here. Hand back to
  the matching fetcher shell (see "Decline" above).

### Step 2 — Run the bundled inventory

**Your first tool call after routing MUST be running this skill's
bundled `inventory.py`.** Do not open the HDF5 file yourself. Do
not curl the docs. The inventory does all of it in one pass.

The script is bundled next to this `SKILL.md`. Under the ARGUS
install layout the command is:

```bash
# From a fetcher-staged directory (the usual path):
python /home/jovyan/.deepagents/agent/skills/array-skill-builder/inventory.py \
       --dir /tmp/zenodo-skills/<record-id> \
       --out /tmp/array-skill-inv/<short-slug>

# From a direct URL (no containing record):
python /home/jovyan/.deepagents/agent/skills/array-skill-builder/inventory.py \
       --url <hdf5-url> \
       --out /tmp/array-skill-inv/<short-slug>
```

Substitute the actual skill directory prefix for other runtimes
(Claude Code, Codex).

With `--dir`, files are read **in place** — nothing is re-downloaded,
and the fetcher's `_zenodo_metadata.json` / `_ckan_metadata.json` /
`_docs/` are picked up automatically so provenance and documentation
flow into the inventory without extra glue.

**Reader dependencies.** `inventory.py` reads HDF5 with `h5py` and
NetCDF with `xarray` (+ the `netCDF4` backend, which handles both
NetCDF-3 classic and NetCDF-4). Neither is preinstalled. If the
inventory errors with `xarray is not installed ...` or `h5py is not
installed ...`, install what it names and re-run:
`pip install --user h5py` or `pip install --user xarray netCDF4`.

**Always route `--out` under `/tmp/`, never under `SAGE_OUTPUT_DIR`
or `~/work/`.** HDF5 collections routinely total tens or hundreds of
MB; the persistent-storage volumes have a 10 GB user quota, and
filling it with build-time scratch would displace real notebooks
and skill outputs. `/tmp/` is pod-local, cleared on pod restart,
sized for exactly this kind of transient work. Use
`/tmp/array-skill-inv/<slug>/` where `<slug>` is derived from the
source (e.g. `zenodo-3660832`, or an 8-char sha1 prefix of a direct
URL). This keeps concurrent builds isolated and makes Step 9b's
cleanup a single `rmtree`.

The `--out` directory receives:

- `_inventory.json` — full structured record (groups, per-channel
  stats, dialect variants, documentation manifest).
- `_h5_cache/` — the downloaded HDF5 files (bounded by
  `--max-file-mb`, default 200).
- `_docs/` — every fetched documentation sidecar (PDFs, READMEs,
  text files).

Stdout is a bounded summary you should read immediately — it names
each fingerprint group, the number of files per group, the inferred
partition axis (`month`, `date`, `site`, `run`, or `index`),
per-group dialect splits (channels with multiple source-names
across files), and per-group data-quality flags.

**Do not edit the bundled script.** If it errors on a specific file,
the error is captured per-file in the JSON output and the script
continues. If a file format is genuinely missing (`.zarr`, `.grib`),
report it to the user — do not add to the canonical script in-place.

### Step 3 — Read the documentation sidecars

**This step is what makes array skills good.** HDF5 dataset names
like `Data/O3__ppb_`, `wave_temperature`, `Figure_04/Profile_8099/
Hydrate_05` mean nothing without the accompanying prose. Zenodo /
CKAN records almost always ship a README or PDF with the actual
physical-quantity meanings.

Write **ONE** Python script — this is the single post-inventory
exploration script allowed by pre-flight rule 4. Put it inside the
slug directory (`/tmp/array-skill-inv/<slug>/explore.py`) so Step 9b
cleans it up. Batch everything you need into this one run; there is
no second script.

The script:

1. `json.load`s `_inventory.json` (from Step 2's `--out` directory)
   — loads it *in the script*, never echoes it to stdout.
2. For every entry in `inventory["documentation"]`:
   - If `text_head` is present, use it directly.
   - If the file is a PDF (extension `.pdf`), open its `local_path`
     with `pypdf.PdfReader` and extract text from every page.
   - For text files without inlined `text_head` (i.e. larger than
     30 KB, likely tabular data siblings), print the first 500 chars
     to see if they hold useful metadata; skip if they're clearly
     tabular data columns.
3. Prints the extracted documentation text with per-doc headers.
4. Prints any *specific* inventory fields you still need for the
   proposal that the stdout summary didn't already give you — e.g.
   the full normalised-channel list, or per-channel stats for the
   flagged channels. Print the values, not the JSON.

Keep total stdout under ~20 KB. If the docs are long, truncate
per-document rather than printing everything.

This script's stdout is your semantic Rosetta Stone. From it you
harvest:

- What each dataset physically represents (temperature, ozone,
  pressure, hydrate saturation, potential field, cell count, …).
- Units for every channel.
- Any dimension semantics (time axis convention, spatial axis units).
- Publication provenance and licensing.
- Known limitations, calibration ranges, sensor operational windows.

If `pypdf` isn't installed:
```
pip install --user pypdf
```
(Always `--user`.)

If the record has NO documentation sidecars — flag this explicitly
in the Step 4 proposal. The skill can still be built, but its
Caveats section must state that "channel semantics are derived from
HDF5 attributes and dataset names alone; verify with the publisher
before quantitative use."

### Step 4 — STOP. Propose to the user and wait.

**Hard gate.** After you have the inventory stdout summary and the
extracted documentation text in your context, present a structured
proposal to the user and **end your turn**. Do NOT start writing
SKILL.md. Do NOT run any more tools.

Your proposal message should have this shape:

```
I've inspected <source URL>. Here's what I found and what I propose:

Source: <record title, creators, license>
Files scanned: <N> HDF5 file(s); <M> documentation sidecar(s)

Fingerprint grouping: <N groups>

Proposed skill(s):

1. <skill-name-1>
   - Entity: each file/record is a <plain-English entity>
   - Files: <count>, partitioned by <axis> (keys: <example, example, ...>)
   - Channels: <N> total (<M> with dialect-variant source names — will use tuple CHANNELS)
   - Time axis: <name or "none">
   - Data-quality flags: <count of channels flagged>; will document in Caveats
   - Documentation drawn from: <list of sidecar files consulted>

2. <skill-name-2>
   - ... (only if the inventory produced multiple fingerprint groups)

Open questions (please confirm):
- [Only include if there's a real judgement call. Common cases:
   - Ambiguous entity naming — "each row a monthly weather record" vs "each row a station-year"
   - Uncertain unit assignments where the docs don't say
   - >1 fingerprint group where it's unclear if the user wants one skill or several
- Skip this whole section if there are no real questions.]

Reply with "yes" to proceed as proposed, or with edits
(e.g. "rename skill-1 to X", "drop channels X, Y", "call the partition
key `station` instead of `site`").

(Resume state on disk: <out-dir>/ —
_inventory.json, _h5_cache/, and _docs/ are all there.)
```

Then **stop**. When the user's follow-up cell arrives with their
reply, pick up at Step 5.

If the user's reply is unclear ("looks okay?" "hmm"), ask one
clarifying question and stop again. Do not start building on
ambiguous approval.

### Step 4b — Resuming after the STOP gate

When the user's reply arrives, your first move is to re-orient
cheaply:

1. **Read your own conversation history.** The proposal, the
   inventory summary, the extracted docs — all in your message
   history. Recall it. Do not re-run the inventory unless something
   is gone from disk.
2. **Check `<out-dir>/_inventory.json` and `<out-dir>/_docs/` are
   still there** with one `ls` call.
3. **Branch:**
   - Both present → proceed to Step 5.
   - Missing → the scratch dir got cleared (rare; pod recycle). Tell
     the user and ask them to re-run `%%skill-build`.

### Step 5 — Branch on the group's format, then design the loaders

The inventory reports **`groups[i].format`** — either `hdf5` or
`netcdf`. The two use different readers and loader shapes:

- **`format: hdf5`** — h5py + a CHANNELS mapping. Follow Steps 5a → 7
  below (the CHANNELS / `load_month` / Igor-time-axis machinery).
- **`format: netcdf`** — xarray. **Skip the CHANNELS steps entirely**
  and follow **Step 6b** instead. NetCDF variables already carry
  clean names, named dimensions, and CF units, so there is no
  dialect-reconciliation or CHANNELS-tuple work — xarray reads the
  file directly.

Pick the branch per group. A record can even be combined (some HDF5,
some NetCDF), though that is rare.

#### Step 5a — Design the CHANNELS mapping (HDF5 only)

From the inventory's `groups[i].normalised_channels` field, produce
the CHANNELS dict for each proposed HDF5 skill. The rules:

**One entry per normalised channel.** Key = clean short name in
snake_case (agent's choice, informed by the documentation).
Value = **tuple** of candidate source-name strings observed across
the files in the group, in the order they should be tried.

**Never emit a bare string as a value.** Every value must be a
tuple. Include the runtime assert immediately after the dict:

```python
CHANNELS = {
    "o3_ppb":            ("O3__ppb_",),
    "outdoor_temp_c":    ("Outdoor_Temperature___C_", "Outdoor_Temperature"),
    "wind_speed_kmh":    ("Wind_Speed__km_h_", "Wind_Speed"),
    "rain_daily_mm":     ("Daily_Rain__mm_", "Daily_Rain"),
    # ...
}
assert all(isinstance(v, tuple) for v in CHANNELS.values()), (
    "CHANNELS values must be tuples of candidate names, not bare strings"
)
```

**Choose clean names informed by the docs, not the source names.**
The Igor `Indoor_Humidity___C_` dataset (whose values are
percent-relative-humidity despite the `_C_` suffix — a source
mislabelling documented in the ATLASM5 case study) should become
`indoor_rh_pct` in CHANNELS. Fix the labelling at the CHANNELS
boundary; document the mislabelling in Caveats.

**Skip administrative datasets.** Igor's `S_fileName`, `S_path`,
`S_waveNames`, `V_Flag` datasets are metadata about the export, not
measurements. Do not put them in CHANNELS. Same for HDF5 datasets
inspected with `is_group: true` (those are groups, not measurements).

**Skip the time axis.** The inventory's per-group `time_axis` field
identifies the timestamp dataset (`dateW`, `time`, `timestamp`).
That gets its own load-helper machinery, not a CHANNELS entry.

### Step 6 — Design the load helpers

The emitted skill needs three concentric loaders. Naming depends on
the group's `partition_axis`:

**Single-file group (n_files = 1).**

```python
def load(): ...          # returns the full DataFrame or dict-of-arrays
def open_file(): ...     # returns the raw h5py.File for advanced use
```

**Multi-file group (n_files ≥ 2, temporal partition — `axis=month`).**

```python
def _ensure(key): ...             # downloads that partition if not cached
def open_partition(key): ...      # h5py.File handle for one partition
def load_month(key): ...          # DataFrame for one partition
def load_year(): ...              # concat of all partitions
MONTH_URLS = { "Jan-2019": "...", "Feb-2019": "...", ... }
```

**Multi-file group (spatial partition — `axis=site`).**

```python
def load_site(name): ...
def load_all(): ...
SITE_URLS = {...}
```

**Multi-file group (parameter sweep — `axis=run`).**

```python
def load_run(name): ...
def load_all(): ...
RUN_URLS = {...}
```

The concrete `load_month` body should follow the pattern proven in
the ATLASM5 skill:

```python
from collections import Counter
def load_month(key):
    with h5py.File(_ensure(key), "r") as f:
        # Top-level group name may have a typo / quoted string in the source;
        # take whichever single top-group is present.
        keys_ = list(f.keys())
        if len(keys_) != 1:
            raise ValueError(f"expected one top group in {key!r}, got {keys_}")
        grp = f[keys_[0]]
        ts = _timestamps(grp["<TIME_AXIS_NAME>"][:])   # e.g. "dateW"
        raw = {}
        for clean, candidates in CHANNELS.items():
            for src in candidates:
                if src not in grp:
                    continue
                try:
                    raw[clean] = grp[src][:]
                except Exception:
                    # h5py can raise on source-side dataset corruption
                    continue
                break
        # Reconcile lengths — some source files carry a padded dateW
        # or truncated channels. Modal-length truncation is the safe default.
        lengths = [len(ts)] + [len(a) for a in raw.values()]
        n = Counter(lengths).most_common(1)[0][0]
        ts = ts[:n]
        data = {k: a[:n] for k, a in raw.items() if len(a) >= n}
    df = pd.DataFrame(data, index=ts)
    df = df[df.index.notna()].sort_index()
    df.index.name = "timestamp_utc"
    return df
```

The `_ensure` helper downloads a partition on demand to a local
cache. Standard pattern:

```python
_CACHE_DIR = Path("/tmp/<skill-name>-cache")

def _ensure(key):
    if key not in MONTH_URLS:
        raise ValueError(f"unknown {key!r}; expected one of {list(MONTH_URLS)}")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local = _CACHE_DIR / f"{key}.h5"
    if not local.exists():
        from urllib.request import Request, urlopen
        req = Request(MONTH_URLS[key],
                      headers={"User-Agent": "<skill-name>/0.1"})
        with urlopen(req, timeout=300) as r, local.open("wb") as f:
            while (chunk := r.read(1 << 15)):
                f.write(chunk)
    return local
```

### Step 6b — NetCDF loaders (xarray) — use INSTEAD of Steps 5a–7

For a group whose `format` is `netcdf`, do NOT build a CHANNELS dict
or an h5py loader. NetCDF is self-describing — variables carry names,
named dimensions, units, and (usually CF) time — so xarray reads it
directly. The emitted skill's How-to-Use block is just:

```python
import xarray as xr
from pathlib import Path

_SKILL_NAME = "<skill-name>"

# Deps: xarray + the netCDF4 backend (reads NetCDF-3 classic AND
# NetCDF-4). Not preinstalled — install on first use.
try:
    import xarray as xr
except ImportError:
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "pip", "install", "--user",
                    "xarray", "netCDF4"], check=True)
    import xarray as xr

def _data_dir():
    ...   # the portable _data_dir() from the bundled-data rule above,
          # OR the /tmp/<skill-name>-cache lazy-download path

def load(<partition-key args if a multi-file collection>):
    """Open the NetCDF dataset as an xarray.Dataset.

    decode_times=True lets xarray apply CF time decoding; drop to
    False if the file uses a non-CF time axis (e.g. IOAPI TFLAG) —
    see Caveats.
    """
    path = _data_dir() / "<filename>"          # single-file case
    return xr.open_dataset(path, decode_times=True)
```

Loader shape still follows the group topology (Step 6): a single-file
group emits one `load()`; a multi-file collection emits
`load_<axis>(key)` + `load_all()` that `xr.open_mfdataset(...)`
concatenates along the partition dimension (or opens each and
`xr.concat`s). For the collection case, prefer:

```python
def load_all():
    """Open all partitions as one dataset, concatenated along <dim>."""
    return xr.open_mfdataset(sorted(_data_dir().glob("*.nc")),
                             combine="by_coords")   # or concat_dim="<time>"
```

**Emit a variable table, not a CHANNELS dict.** From the inventory's
`datasets_union` (which carries each variable's `dims`, `units`,
`long_name`), write a `## Variables` table: variable name, dims,
units, meaning (from `long_name` / the docs). List coordinate
variables separately. Access is by name and dimension, e.g.
`ds["PM25"].isel(TSTEP=0)` or `ds["PM25"].sel(time="2025-09-26")`.

**Data-quality still applies** (Step 8's rule): if the inventory
flagged a variable, document the range + a filter recipe in Caveats;
do not mutate values in `load()`. The `where` idiom is xarray's
filter: `ds["PM25"].where(ds["PM25"] >= 0)`.

Then skip to Step 8 to compose the SKILL.md (the NetCDF variant).

### Step 7 — Design the time-axis conversion (HDF5 only)

If the inventory identified a time axis (`groups[i].time_axis` is
not null), the emitted skill needs a converter. Recognised
conventions:

- **Igor Pro `dat`** — seconds since 1904-01-01 UTC. Convert with
  `arr - 2082844800.0` to get Unix seconds, then
  `pd.to_datetime(..., unit="s", utc=True)`.
- **CF `units="..."` attribute** — the h5py attribute
  `units` on the time dataset carries something like
  `"seconds since 2000-01-01 00:00:00"`. Parse the anchor + unit;
  add via `pd.to_datetime`.
- **Plain Unix seconds** — no anchor attribute; just convert with
  `pd.to_datetime(..., unit="s", utc=True)`.

Handle corruption defensively: coerce out-of-range values to NaT
rather than crashing. Example (Igor):

```python
_IGOR_MINUS_UNIX = 2082844800.0
def _timestamps(dateW):
    import numpy as np
    from datetime import datetime
    arr = np.asarray(dateW, dtype="float64")
    lo = (datetime(YYYY, 1, 1) - datetime(1904, 1, 1)).total_seconds()
    hi = (datetime(YYYY+3, 1, 1) - datetime(1904, 1, 1)).total_seconds()
    valid = (arr >= lo) & (arr <= hi)
    unix = np.where(valid, arr - _IGOR_MINUS_UNIX, np.nan)
    return pd.to_datetime(unix, unit="s", errors="coerce", utc=True)
```

`YYYY` is a plausible year floor / ceiling derived from the record's
publication metadata or the docs.

### Step 8 — Compose the SKILL.md

Assemble the generated content in this order. All the substantive
labels come from the documentation you extracted in Step 3; the
inventory's structural data (shapes, channel lists, dialect splits,
partition axis) fills in the rest.

1. **Frontmatter** — `name` (kebab-case, chosen in Step 4 proposal
   and approved by the user) and `description` under 100 words.
   The description follows the standard skill-description rules:
   name the entity each partition represents, common synonyms, and
   trigger conditions ("Use when the user asks about …"). See
   `[[skill-descriptions]]`.

2. **# `<Skill Title>`** — H1 header.

3. **## Description** — 1–2 paragraphs. What the dataset is, who
   publishes it, what each partition represents, the total scale.
   Mention the source URL prominently and cite the DOI/citation
   from the docs.

4. **## Data** — bullets covering:
   - Source URL and record DOI
   - License
   - File count and total size (approximate)
   - Cadence (temporal partition case) or extent (spatial)
   - Format notes — including any dialects the inventory flagged
     and how the loader handles them

5. **## File-URL catalogue** *(collection case only)* — a Markdown
   table mapping every partition key to its download URL. Even
   though the loader treats these as one logical dataset, the
   table lets domain experts verify which file corresponds to
   which partition.

6. **## HDF5 structure (per file)** — one-line explanation of the
   top-group naming convention (including any typos/quirks the
   inventory surfaced) and how `open_file` / `open_partition`
   navigates it.

7. **## Channels / Variables table** —
   - **HDF5:** `## Channels (N total)` — `Clean name | Source-name(s)
     in files | Physical quantity | Units`, populated from CHANNELS +
     docs. List every candidate the CHANNELS tuple carries. Mark the
     time axis separately below the table.
   - **NetCDF:** `## Variables (N total)` — `Variable | Dimensions |
     Units | Meaning`, populated from the inventory's `datasets_union`
     (`dims`, `units`, `long_name`) + docs. List coordinate variables
     separately.

8. **## How to Use** — narrative + code:
   - **HDF5:** import block (h5py, pandas, numpy, urllib); the full
     CHANNELS dict verbatim + the tuple-guard assert; the `_ensure`,
     `open_*`, `load_*` helpers from Step 6; the `_timestamps` helper
     from Step 7.
   - **NetCDF:** import block (xarray, with the on-demand install
     guard); the `_data_dir()` / cache helper; the `load()` /
     `load_all()` xarray helpers from Step 6b. No CHANNELS, no
     `_timestamps` — xarray's `decode_times` handles CF time.

9. **## Examples** — 3–7 code examples showing realistic queries.
   Each has a natural-language description and a code block using
   the load helpers. Use real partition keys from the group's
   `partition_keys`.

10. **## Caveats** — this section is where the data-quality audit
    lands. Include one entry per finding:

    - **Time-axis convention** — the epoch, timezone, and how to
      convert (Igor vs Unix vs CF). **NetCDF:** state whether the
      file is CF-compliant (`load(decode_times=True)` works) or uses
      a non-CF axis such as IOAPI `TFLAG` (`YYYYDDD,HHMMSS`), where
      `decode_times=True` will fail/mislead and the user must decode
      the flag manually — give the recipe.
    - **Source-name mislabelling** — any channels where the source
      dataset name is misleading (ATLASM5's `Indoor_Humidity___C_`
      being RH-percent, not degrees C).
    - **Data-quality flags** — for every channel the inventory
      flagged, describe the specific pattern (extreme magnitudes,
      order-of-magnitude spread) and provide a copy-paste
      plausibility filter. Do NOT clean values in `load_*`;
      surface the recipe here.
    - **Missing channels per partition** — if the group merge
      involved files that omit certain channels
      (`rain_yearly_mm` absent in Jun/Jul, `rain_monthly_mm`
      absent in Oct), state which partitions lack which channels.
    - **Sampling-rate variation** — if row counts differ
      dramatically across partitions (>2×), note it.
    - **Time-of-day boundary artifacts** — if the time axis
      resets on UTC midnight and users might shift to local time,
      flag the boundary case with a `.loc[start:end]` recipe.
    - **License and citation** — the license id from the record
      metadata, plus the citation string.

### Step 9 — Write the SKILL.md to disk and stop

Save the assembled markdown to
`$SAGE_OUTPUT_DIR/_skills_/<skill-name>/SKILL.md`. Each notebook has
its own `SAGE_OUTPUT_DIR` (`_<notebook-stem>_sage_/`), so each
notebook gets its own private `_skills_/` scope.

```python
from pathlib import Path
import os

out_dir = Path(os.environ["SAGE_OUTPUT_DIR"]) / "_skills_" / "<skill-name>"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "SKILL.md").write_text(skill_md_content)
```

If a skill of the same name already exists at that path, **do not
overwrite without confirmation.** Tell the user there's a conflict
and ask whether to overwrite or pick a new name.

**Do not install the skill into the global registry**
(`~/.deepagents/agent/skills/`). Do not copy the directory there,
do not call any internal install helper. The freshly-written skill
is automatically picked up by the next `%%ask` cell.

### Step 9b — Clean up the inventory scratch

Once the skill is written and the user has verified a working
`%%ask` query against it, delete the scratch directory to avoid
holding stale HDF5 downloads on disk:

```python
import shutil
shutil.rmtree(<out-dir>)   # e.g. /tmp/array-skill-inv/zenodo-3660832
```

Do NOT delete the emitted `_skills_/<skill-name>/` directory —
that's the skill itself.

---

## Robustness notes for the emitted skill code

These patterns must appear in every generated SKILL.md's How-to-Use
block. They were surfaced by the ATLASM5 and Blake Ridge manual
builds and prevent whole classes of downstream failure:

- **Length reconciliation via modal-count truncation.** Some source
  files carry a padded time axis or truncated channels. Naive
  `pd.DataFrame(data, index=ts)` crashes on length mismatch. Instead
  compute the modal length across `ts` + all channel arrays and
  truncate everything to it (see Step 6's template).

- **h5py read errors are per-channel, not fatal.** Individual
  datasets can be corrupted at the source (`h5py._objects.KeyError:
  Unable to synchronously open object (invalid dataset size, likely
  file corruption)`). Wrap the per-channel read in try/except and
  skip on failure — the emitted loader must not crash the whole
  month because one channel is bad in one file.

- **CHANNELS-tuple candidate loop.** Every emitted loader must
  iterate the tuple with:

  ```python
  for src in candidates:
      if src not in grp:
          continue
      try:
          raw[clean] = grp[src][:]
      except Exception:
          continue
      break
  ```

  Not `for src in [candidates]:` (double-wraps), not `raw[clean] =
  grp[candidates[0]][:]` (ignores dialect variants). The exact
  shape above.

- **Timestamps: coerce corrupt values to NaT, drop those rows
  post-hoc.** Use `pd.to_datetime(..., errors="coerce")` and
  `df = df[df.index.notna()]` after DataFrame construction. Do NOT
  raise on corrupt timestamps; do NOT silently produce a DataFrame
  with unusable index values.

## Adapting to future formats

v0 handles HDF5 (`.h5`, `.hdf5`) and, via the same `h5py` open path,
NetCDF-4 files. Adding real NetCDF-3, Zarr, or GeoTIFF support means
extending `inventory.py`'s per-file walker with a format-specific
branch; the fingerprint, grouping, and STOP-gate flow apply
unchanged. Do NOT add these branches inline in a build — surface
the missing-format case to the user as a known gap and stop.
