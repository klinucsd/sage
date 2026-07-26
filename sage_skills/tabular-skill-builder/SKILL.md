---
name: tabular-skill-builder
description: >-
  Build one or more ARGUS skills from a local directory of tabular,
  geospatial, or R-serialized data files (CSV, TSV, Excel, Parquet,
  GeoPackage, GeoJSON, Shapefile, RData / rda / rds). Typically
  invoked by a fetcher skill (`repo-skill-builder` for GitHub repos,
  `ckan-skill-builder` for CKAN datasets, or a future S3 / Nextcloud
  fetcher) after the source files have been staged locally. Two-phase
  workflow: first you enumerate the files and propose a skill plan,
  then STOP for the user's approval; the user replies in the next
  %%ask cell with "yes" / edits / "no", and you continue from where
  you stopped.
---

# Tabular Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules are violated most often in the field. They are
non-negotiable. Read them carefully **before** doing anything else.
Detailed rationale for each is in the steps below; this section is
the binding contract.

1. **Your FIRST tool call on the source directory is running this
   skill's bundled `inventory.py`.** Not `ls`. Not `wc -l`. Not `head`.
   Not `find | grep`. Not "let me just take a quick look at the
   directory." The inventory script is the *first* picture you get of
   the source data — by design. The only allowed pre-inventory action
   is reading the source directory's top-level `README.md` (one tool
   call, optional).

2. **One Python script per phase, never per-file actions.** This
   includes `python -c` per file, `ls` per directory, and
   **paginated `read_file` calls through large data files**.
   For enumeration, exploration, building, probing — write the
   script to a file with `write_file`, then `execute` it once.
   To extract specific info from a large JSON/dataset, write a
   script that reads it and prints only the summary you need.
   Per-page `read_file` on `_inventory.json` is the same antipattern
   as `ls` per directory, in different clothing.

3. **`inventory.py` must compute and print the schema grouping in
   its stdout summary.** Don't make the agent read `_inventory.json`
   to figure out what files share schemas — the script already has
   that information in memory. Print the groupings directly. The
   stdout summary is what you use for the Step 3 proposal; the JSON
   is just a reference if you later need specific file paths or
   row counts. Total stdout should be a few hundred bytes to maybe
   2 KB — never enumerate individual files in stdout.

4. **After all skills are built and verified, delete the source
   directory.** Step 9c is mandatory. Leftover source files compete
   with the skill's Parquet caches in future `%%ask` cells and destroy
   the user's trust in the skill as the authoritative interface.

5. **Step 4 is a HARD STOP between inventory and build.** After
   running `inventory.py` and grouping the files into skill
   candidates, you write the proposal to the user and **end your
   turn**. Do NOT continue to write build scripts, do NOT create
   Parquet files, do NOT write any `SKILL.md`. The user replies
   in a follow-up `%%ask` cell with "yes" or edits, and ONLY THEN
   do you proceed to Steps 5–9. Building without explicit user
   approval defeats the entire point of this skill — the user
   can no longer redirect grouping decisions (e.g., "actually split
   that into 3 skills, not 1"). Every build observed without this
   stop has produced a skill the user then had to manually rebuild.

6. **At most ONE post-inventory exploration script.** After
   `inventory.py`, if you want more context — subdirectory READMEs,
   deeper schema samples, join-key analysis — you may write ONE
   additional script (e.g. `explore_context.py`) that batches
   everything into a single run. Do NOT write a second exploration
   script. Do NOT write `explore_deep.py`, `explore_full.py`,
   `explore_full2.py`, etc. — those iterations chase perfection
   instead of proposing. If your one exploration script's output
   isn't crystal-clear, PROPOSE ANYWAY based on what you know.
   Grouping decisions can be refined in Phase 2 after user approval.
   Writing a second `explore_*.py` file after the first is a
   symptom that you should already be at Step 4, not continuing to
   explore.

7. **Phase 2: your NEXT `write_file` MUST be a build script. Do NOT
   verify data files first.** After the user approves your Phase 1
   proposal, you have exactly two inputs — `_inventory.json`
   (schemas + 3-row samples for every file) and your Phase 1
   proposal (the reconciliation plan). That IS the plan. Write the
   build script now. Do not verify your Phase 1 assumptions before
   the first build attempt.

   The following actions in Phase 2, BEFORE your first build script
   has run, are rule violations. This list is exhaustive — do not
   invent a category not listed here and claim it doesn't count:

   - `python -c "..."` opening ANY XLSX/CSV/parquet/shapefile to
     inspect columns, values, coordinate ranges, unique IDs, or
     anything else. Even one such call is a violation. `python -c`
     across many files ("let me just check each one") is the most
     common form of this violation and produced the worst Phase 2
     runs on record.
   - `cat`, `head`, `tail`, `awk`, `sed`, `wc`, or any shell tool
     applied to a data file.
   - `read_file` on any data file. The only files you may `read_file`
     in Phase 2 are: this `SKILL.md`, `_inventory.json`, the source
     directory's top-level `README.md`, and any caller-written
     metadata sidecar in the source directory root whose name starts
     with `_` (e.g. `_ckan_metadata.json`, `_skipped_resources.json`
     from `ckan-skill-builder`) — those exist precisely to feed
     Step 8's SKILL.md writing. Every other read is a violation.
   - `write_file` of any script whose purpose is verification.
     Names like `check_*.py`, `verify_*.py`, `explore_*.py`,
     `inspect_*.py`, `sample_*.py`, or any other name signaling
     "let me look at the data" are all forbidden. Your first
     `write_file` in Phase 2 must be a build script (e.g.
     `build_<skill-name>.py`) — nothing else.

   All of these are the same anti-pattern: stalling before building.
   They are dressed up as "just being careful" or "confirming an
   assumption first," but every past Phase 2 that used them wasted
   30+ tool calls before writing any real build code. The correct
   response to uncertainty is to WRITE THE BUILD SCRIPT with your
   best assumption from the inventory + proposal, run it, and let
   the error tell you what's actually wrong.

   The ONE exception applies AFTER a real build script has run and
   errored on a specific issue (e.g. `KeyError: 'ColumnX'`): THEN
   you may write one minimal script that verifies just that one
   column across just the relevant files, then fix the build script.
   Not before the first build attempt. Never.

8. **NEVER call `sys.exit()` or raise `SystemExit` in any script you
   write.** Not in a build script, not in a verification script, not
   on an error path. ARGUS runs `python your_script.py` **in-process**
   (KernelShellBackend), so a `SystemExit` does not end the script —
   it propagates out and **kills the whole `%%ask` cell**, and it
   destroys the diagnostic: your `print("[FAIL] …")` never reaches the
   tool result, so nobody learns what actually broke.

   Collect failures and print them instead:

   ```python
   # WRONG — kills the cell and hides the reason
   except Exception as e:
       print(f"[FAIL] {e}")
       sys.exit(1)

   # RIGHT — the failure is visible and fixable
   failures = []
   ...
   except Exception as e:
       failures.append(f"{name}: {type(e).__name__}: {e}")
   print("VERIFY OK" if not failures else "VERIFY FAILED:")
   for f in failures:
       print("  -", f)
   ```

   Same for `argparse` (`parser.error()` / `parse_args()` call
   `sys.exit` internally — use `exit_on_error=False`) and for any
   "clean exit" `raise SystemExit(0)`; falling off the end is correct.

If your next action would violate any of these, **stop and re-plan**
before taking it. The rest of this document elaborates on why; the
rules above are the contract.

---

## When to Use

Trigger this skill when either:

- **A fetcher skill has just staged data locally and handed off to
  you.** In this case the caller's `SKILL.md` (`repo-skill-builder`,
  `ckan-skill-builder`, etc.) will have already directed you here
  and told you the source directory path (typically
  `/tmp/repo-skills/<name>/`). Follow the steps below verbatim.
- **The user points at a local directory of tabular / geospatial /
  R-serialized data files and asks to build a skill from it.** Rare
  direct-use case; the source URL / fetch step lives elsewhere.

Decline (do not use this skill) when:

- The user's request is a **URL** to a remote data source. Route to
  the matching fetcher skill instead:
  - GitHub repo → `repo-skill-builder`
  - CKAN dataset (`/api/3/action/package_show?id=...` or
    `/dataset/<slug>`) → `ckan-skill-builder`
  - ArcGIS Feature / Map Service → `arcgis-feature-skill-builder`

  The fetcher skill will download the resources locally and then
  hand off to this skill.
- The source directory contains only code, images, or documentation
  with no tabular data — there's nothing to build a queryable skill
  against.
- The user has already named specific files and just wants them
  loaded into a notebook for ad-hoc analysis — that's a plain
  `%%ask` task, not a skill-build.

## What This Skill Produces

One or more skills under `SAGE_OUTPUT_DIR/_skills_/<skill-name>/`,
each containing:

- `SKILL.md` — the agent-readable skill descriptor (frontmatter +
  body).
- `data/<skill-name>.parquet` — the cleaned, merged data the skill
  queries. For **spatial skills** (skills whose data has a geometry
  column, e.g. built from GPKG or GeoJSON inputs), write
  `data/<skill-name>.gpkg` instead — see Step 5.

The skills are **local-only**, written to the notebook's
per-notebook scratch folder. They are NOT installed into the
global registry at `~/.deepagents/agent/skills/`. The user can
promote them globally with an explicit `%%skill _skills_/<name>`
cell, but that's their decision, not yours.

`SAGE_OUTPUT_DIR` is an environment variable available to your
scripts; the folder already exists.

## What You Need

Just the path to the source directory. The caller (either a fetcher
skill that just staged the data, or the user directly) does not need
to specify the skill name, the file format, or anything else. Your
job is to inspect the directory and propose a sensible plan.

If the caller has expressed preferences (e.g. "merge them all into
one skill" or "split by region"), incorporate those into your
proposal in Step 3.

## Steps to Build the Skills

### Step 2 — Enumerate the tabular data files

**Your very first action on the source directory is to run this
skill's bundled `inventory.py`. Do not explore the directory
structure with shell first — no `ls`, no `wc -l`, no `find | grep`,
no "just a quick look" with command-line tools.** The inventory
script itself produces your first picture of the data: file
tree, sizes, schemas, samples — all of it in one structured
JSON file you can read and re-read. Anything you'd learn from
`ls` or `find` is also in the inventory output, faster and
more reliably.

The single exception: you MAY read `README.md` (and any
top-level `TABLE_OF_CONTENTS.md` / `MANIFEST.md` / similar)
*before* the inventory, to orient yourself on the project's
intent. That's a focused two-call action. Do not let it expand
into directory listings.

Walk the source directory and collect every `.csv`, `.tsv`, `.xlsx`,
`.xls`, `.parquet`, `.gpkg`, `.geojson`, `.shp`, `.RData` (or
`.rda`), and `.rds` file. Skip anything obviously not data:

- README.md, LICENSE, CITATION.cff, .gitignore, .gitattributes
- Notebooks, Python source, configuration files
- Figures, images
- Files inside `.git/` or hidden directories
- **Shapefile sidecar files** — `.shx`, `.dbf`, `.prj`, `.cpg`,
  `.qix`, `.sbn`, `.sbx`, `.qmd`, `.shp.xml`. The `.shp` file is
  the entry point; geopandas / OGR reads the sidecars automatically
  when you open the `.shp`. Never inventory sidecars as separate
  records.

For each tabular / geospatial file, capture:

- Relative path
- Size on disk
- For CSV/TSV: detected delimiter, encoding, header row
- For GPKG: layer names (analogous to XLSX sheets), geometry type,
  CRS (loaded via geopandas)
- For GeoJSON / Shapefile: geometry type, CRS, feature count (loaded
  via geopandas; single feature collection per file — for
  shapefile, geopandas reads the `.shx` / `.dbf` / `.prj` /
  `.cpg` sidecars automatically from the `.shp` path)
- For RData / rds: R object names (analogous to XLSX sheets), read
  via pyreadr; only DataFrame objects get schema-inspected
- Column names (sorted) — this is the **schema fingerprint**;
  for GPKG the geometry column is appended last
- Approximate row count (for CSV: line count; for XLSX: from
  metadata or by sampling; for GPKG: from pyogrio metadata; for
  RData/rds: `len(df)` after pyreadr read)
- A 3-row sample of the data, so you can sanity-check types
  (geometry column is excluded from GPKG samples to keep the
  payload small)

Save this catalog to `<source-dir>/_inventory.json` so you can refer
back to it without re-scanning. Same scratch lifetime as the source
directory itself (typically `/tmp/repo-skills/<name>/` when invoked
by a fetcher skill).

**Use the canonical `inventory.py` bundled with this meta-skill.
Do not write your own.** The skill ships `inventory.py` next to
this `SKILL.md`.

That script handles all the things agent-written inventories have
historically gotten wrong: per-pandas-version `read_csv` arg bugs,
encoding fallbacks (utf-8 → latin-1), Excel sheet enumeration,
parquet schema reads, GPKG layer enumeration + CRS/geometry-type
capture via geopandas, GeoJSON reading via geopandas, RData/rds
DataFrame extraction via pyreadr, error isolation (one bad file
doesn't crash the rest), schema fingerprint grouping, and the
correct compact stdout summary format. It also writes the full
inventory to JSON at `<source-dir>/_inventory.json` for reference.

**`_inventory.json` schema (exact field names — do not guess).**
The JSON is **groups-first**: files that share a schema fingerprint
are collected under one group object, with the class-level schema
(columns, dtypes, sample_rows) lifted to the group. This keeps the
file small on repos with many near-identical inputs (e.g. per-species
RData sweeps that produce thousands of same-schema files).

```json
{
  "groups": [
    {
      "_fingerprint":     "<40-char sha1 hex>",          // schema-group identifier
      "n_files":          796,                            // convenience: len(files)
      "exts":             [".rdata"],                     // sorted set of extensions in the group
      "n_columns_total":  16,                             // true column count (may exceed len(columns))
      "columns":          ["col1", "col2", ...],          // capped at 50 for wide files
      "columns_truncated": true,                          // present only if capped
      "dtypes":           {"col1": "object", ...},        // same cap as columns
      "sample_rows":      [{...}, {...}, {...}],          // 3 rows from a representative file; omitted for >50-column files
      "sample_rows_skipped": "omitted: ...",              // present only if sample was skipped
      "files": [
        {
          "rel_path":     "Piemonte/00100410001.csv",     // path relative to source dir
          "ext":          ".csv",
          "size_bytes":   12345,
          "row_count":    8912,
          "delimiter":    ",",                            // CSV/TSV only
          "encoding":     "utf-8",                        // CSV/TSV only
          "sheets":       [...],                          // XLSX only
          "layers":       ["layer_name", ...],            // GPKG only
          "geometry_type":"MultiPolygon",                 // GPKG only (Polygon/Point/LineString/etc.)
          "crs":          "EPSG:4326",                    // GPKG only
          "objects":      ["<unnamed>", "df1", ...]       // RData/rda/rds only — R object names
        },
        ...
      ]
    },
    ...
  ],
  "unreadable_files": [
    {
      "rel_path":   "some/file.dat",
      "ext":        ".dat",
      "size_bytes": 456,
      "error":      "ParserError: ..."
    },
    ...
  ]
}
```

**How to look things up:**

- Every file's row_count / rel_path / ext lives on the per-file
  object inside `group["files"]`.
- The schema (columns, dtypes, a 3-row sample) is on the parent
  group and applies to every file in `group["files"]`.
- To iterate every file in the repo:
  ```python
  for g in inv["groups"]:
      for f in g["files"]:
          # f["rel_path"], f["ext"], f["row_count"], ...
  ```
- To find files matching a specific schema, filter `groups` by
  `_fingerprint`, columns, or presence of a specific column — then
  iterate that group's `files` list.
- Files that couldn't be inspected are in `unreadable_files`, not
  in any group.

Common hallucinations the agent has produced in field testing — do
**not** use these:

- `inv["records"]` — wrong, this used to exist but now the top-level
  keys are `groups` and `unreadable_files`.
- `inv["files"]` — wrong; there is no top-level `files` key.
- `group["fingerprint"]` — wrong, use `group["_fingerprint"]`.
- `file["relpath"]` / `file["path"]` — wrong, use `file["rel_path"]`.
- Reading schema off individual `file` objects — wrong, schema lives
  on the parent `group`.

Note that `dict.get("wrong_key", [])` returns the default silently;
your script will appear to succeed with empty results. Sanity-check
your script's first iteration: if `len(inv["groups"])` is 0 on a
real repo, you're reading the wrong key.

Run it using the directory you just read this `SKILL.md` from —
do not write your own script. Under the ARGUS install layout the
command is:

```bash
python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
       <source-dir>
```

If you read this `SKILL.md` from a different location (Claude
Code, Codex, or another runtime), substitute your runtime's
actual skill directory for the prefix.

No editing, no copying, no rewriting. If the bundled script
errors on a specific file, the error is captured per-file in the
JSON output and the script continues; you don't need to fix the
script. If a file format is genuinely missing (e.g. `.feather`),
report it to the user — don't add to the canonical script
in-place (it's read-only system code).

**Why bundled, not agent-written:** every recent regression with
NRP-served models has involved either (a) verbose per-file stdout
that bloated the next LLM prompt, (b) missing schema grouping that
forced the agent to paginate the JSON, or (c) per-pandas-version
bugs the agent introduced when re-deriving the script. The
canonical script eliminates all three.

Shell tools (`find`, `sort`, `awk`, `sed`, `wc`) are still fine
for one-line checks ("does X exist", "how big is the repo"), but
**do not use them for enumeration** — that's what the bundled
script is for.

Do not load the full data in this step — `inventory.py` only
inspects the first 5 rows of each file to learn the schema.

**Keep the script's stdout minimal — write detail to the JSON, not
to stdout.** This matters more than it sounds. Repos with hundreds
of tabular files produce inventory scripts that, if they print one
line per file (path, size, columns, sample), dump 30–60 KB of text
to stdout. That entire dump becomes the tool result returned to the
LLM, which means the *next* LLM call's prompt is much bigger than
any other step in the build. On NRP and other shared-GPU vLLM
endpoints, prompt length scales response time superlinearly under
KV-cache pressure — observed in field testing as the agent hanging
for 20+ minutes after the inventory step specifically, while every
other step completes in seconds. The fix is to keep stdout to a
short summary the LLM can absorb cheaply.

The bundled script's stdout summary is what you use for the Step 3
grouping decision. It already includes:

- Total file count, extensions breakdown, top-level directory
  breakdown.
- A `Schema groups` section listing each unique column-set group:
  file count, example files, column names (truncated at 8),
  row-count range, sheet names for XLSX.

**Step 3 reads that stdout summary, not `_inventory.json`.** The
JSON is a reference for Step 5+ when build scripts need specific
file paths. Never paginate the JSON with `read_file`.

**The same rule applies to any follow-up exploration after the
inventory.** If you need deeper context — reading the
subdirectory READMEs, sampling more rows from a few key files,
checking which sites appear in which files — write a *second*
Python script that batches the work. Do not switch into
per-file `ls` / `head` / `grep` / `sed` exploration. A
20-line Python script that opens every subdirectory's README and
prints them, or samples the first 10 rows of each schema-distinct
file, is **one tool call**. The shell-by-shell equivalent is 20+
tool calls and gives you the same information slower, less
reliably, and with no persistent artifact you can re-read.

Per-file shell exploration is the same antipattern as the
pagination loop, just in a different disguise — both substitute
many small tool calls for one comprehensive script. If you find
yourself about to run `ls <subdir>` after already having an
inventory, stop and write a script that lists *every* subdir's
contents in one pass instead.

Concretely, after `inventory.py` you may want a follow-up script
like `explore_context.py` that:

- Reads every `README.md` / `README.txt` / `*.txt` documentation
  file under the repo and prints (relpath, first 500 chars) for
  each.
- For each schema-distinct file group, opens one representative
  file and prints (filename, columns, first 10 rows).
- Optionally checks identifier overlap between candidate-skill
  groups so you can flag join keys.

One script, one run, complete picture. Then you can group, propose,
and STOP.

**Mandatory robustness rules for any post-inventory script — these
are the recurring failure modes observed across many builds:**

1. **`pd.read_csv` does NOT accept an `errors=` parameter.** That's
   a kwarg of `open()`, not `read_csv`. The correct kwarg is
   `encoding_errors="replace"`. Using `errors=` raises
   `TypeError: got unexpected keyword argument 'errors'`.

2. **Always try utf-8 first, fall back to latin-1** when reading
   CSVs from European or government sources. The fallback pattern:

   ```python
   def safe_read_csv(path, **kw):
       for enc in ("utf-8", "latin-1"):
           try:
               return pd.read_csv(path, encoding=enc, **kw)
           except UnicodeDecodeError:
               continue
       # last resort: utf-8 with replacement
       return pd.read_csv(path, encoding="utf-8",
                          encoding_errors="replace", **kw)
   ```

   Same pattern for `pd.read_excel` (no encoding arg, but wrap
   in try/except `ParserError` / `UnicodeDecodeError` per file).

3. **Wrap every per-file read in try/except.** Some files in any
   repo will be malformed, truncated, or non-tabular despite the
   extension. One bad file should not crash the script — print
   the error and continue. Pattern:

   ```python
   for f in files:
       try:
           df = safe_read_csv(f, nrows=5)
           print(f"{f.name}: {list(df.columns)}")
       except Exception as e:
           print(f"{f.name}: ERROR: {type(e).__name__}: {e}")
   ```

4. **Bound the script's stdout.** Same discipline as `inventory.py`:
   if you're going to print READMEs (each potentially several KB),
   truncate per-file to ~500 chars. If you're sampling rows from
   many files, truncate per-file to 3-5 rows. Total stdout should
   stay under ~20 KB. Long stdout slows the next LLM call on
   shared-GPU endpoints (NRP especially) and risks the same
   prompt-bloat hang `inventory.py` was redesigned to avoid.

The bundled `inventory.py` (run in the previous step) already
applies these patterns — your post-inventory script must apply
them too.

### Step 3 — Group files into skill candidates

**Use the schema groups printed by `inventory.py` in Step 2.** The
script already grouped files by schema fingerprint and printed the
result in its stdout summary. Read that stdout, not `_inventory.json`.
Do NOT paginate through `_inventory.json` with `read_file` to
"see all the files" — the JSON is a reference for later steps;
loading it into your context now defeats the entire point of
keeping the inventory compact. If the script's stdout summary is
missing the groupings, edit and re-run the script rather than
working around it by reading the JSON.

Group the files by **schema fingerprint** (their sorted column
names). Files in the same group can be merged into one skill;
files in different groups become different skills.

Three sub-cases to handle:

**Exact match.** All files in the group have identical column
names. Trivial — concat with one or more discriminator columns
added (e.g. `region`, `province`, `year`) derived from the file
path or filename. This is the strong-evidence merge.

**Near match.** Files share most columns but differ in a few
(e.g. one calls a column `QUOTA`, another calls it `Quota_msl`,
another `elev_m`). These are usually the same semantic field
under different names. **Do not merge silently.** When you see
near matches, surface them in your proposal as: "I would merge
these files into one skill by reconciling column X (named `A`,
`B`, `C` in different files). Is that correct?" Let the user
confirm before merging.

**Distinct schemas.** Different files clearly hold different
entities (e.g. a wells registry vs. their time-series
measurements). Propose them as separate skills.

A practical fingerprint algorithm:

- Lowercase every column name and strip whitespace.
- Drop one or two columns that are obviously per-file (e.g. a
  `file_id` or `source`).
- Two groups with ≥80% column overlap are candidates for the
  "near match" reconciliation prompt; let your judgment decide
  the exact threshold based on what you see.

### Step 4 — STOP. Propose to the user and wait.

This is a **hard gate**. After you have grouped the files,
present a structured proposal to the user **and end your turn**.
Do not write any Parquet files. Do not write any SKILL.md. Do
not run any more tools.

Your proposal message should have this shape:

```
I've inspected <source-dir>. Here's what I found and what I propose:

Source: <source URL if known from the caller, else the local dir path>
Files scanned: <N> data files, <total size> (breakdown: <N> CSV/TSV, <N> XLSX, <N> Parquet, <N> GPKG — omit categories with 0 files)

Proposed skills:

1. <skill-name-1>
   - Entity: each row is a <plain-English entity>
   - Merged from: <list of files / glob patterns>
   - Estimated rows: <N>
   - Key columns: <a few representative columns>

2. <skill-name-2>
   - …

Open questions (please confirm):
- [Only include if you actually saw near-match reconciliation,
  ambiguous entity naming, or any other judgment call you want
  the user to weigh in on. Skip this whole section if there are
  no real questions — don't manufacture them.]

Reply with "yes" to proceed as proposed, or with edits
(e.g. "rename skill-2 to X", "drop skill-3", "merge 1 and 2").

(Resume state on disk: <source-dir>/ —
the source files and _inventory.json are both there.)
```

After the "Reply with yes…" line, add one **resume hint** line so
your next-cell self knows where the build state lives:

```
(Resume state on disk: <source-dir>/ —
the source files and the inventory file are both there.)
```

Then **stop**. The user reads the proposal and writes a new
`%%ask` cell with their reply. When that cell arrives, you have
the proposal in your conversation history — pick up at Step 5.

If the user's reply is unclear ("looks okay?" "let me think"),
ask one clarifying question and stop again. Do not start
building on ambiguous approval.

### Step 4b — Resuming after the STOP gate

When the user's "yes" / "edit" / "no" reply arrives in the next
`%%ask` cell, your first move is **not** to start working — it's
to re-orient cheaply. Specifically:

1. **Read your own conversation history.** The proposal you made,
   the file inventory, the schema groupings, the open questions
   — all of that is in your message history. Recall it. Do not
   re-enumerate the source directory from scratch unless something
   is gone.
2. **Then check the working directory.** Run a single `ls
   <source-dir>/` to confirm the source files are still there,
   and `ls <source-dir>/_inventory.json` to confirm the inventory
   is still there.
3. **Branch on what you find:**
   - Both present (the normal case) → load `_inventory.json` and
     go straight to Step 5. Do not re-run schema probes, do not
     re-read CSVs to "double-check" anything you already saw in
     Step 2.
   - Source directory missing → `/tmp` got cleared (rare; pod
     recycle between cells). Tell the user the source data is
     gone and ask them to re-run the fetcher step (or, if invoked
     directly, to re-stage the files). Do not attempt to
     re-download or re-clone on your own — that is the caller's
     responsibility.
   - Inventory file missing but source directory present → re-run
     the enumerate-and-save step only. Skip the README/processing-
     script reading you already did.

Trusting your own conversation history is the discipline here.
The user will see every redundant `ls`, every re-read of a file
you already read, every re-fetch. Those are wasted turns. Make
the cheapest possible re-orientation that's correct, then build.

### Step 5 — Merge each skill's files into one Parquet

For each approved skill, write a Python script that:

1. Loads the files in the group.
2. If reconciliations were proposed and approved, rename columns
   to a canonical name.
3. Adds discriminator columns from filenames or directory names
   when needed (e.g. `region` column from the parent directory).
4. Standardizes types: dates → `datetime64[ns]`, numerics →
   `float64`/`int64`, categoricals → `string`.
5. Drops obviously bad rows (all-null, schema mismatch after
   reconciliation).
6. Writes to
   `<SAGE_OUTPUT_DIR>/_skills_/<skill-name>/data/<skill-name>.parquet`
   for tabular skills, or `data/<skill-name>.gpkg` for **spatial
   skills** (any skill whose data has a geometry column — inputs from
   GPKG, GeoJSON, or constructed points from lat/lon).

**SAGE_OUTPUT_DIR caveat for build scripts.** `SAGE_OUTPUT_DIR`
is injected into the *kernel* namespace but is **not** set in the
subprocess environment when you run `python script.py` via the
execute tool. Calling `os.environ["SAGE_OUTPUT_DIR"]` in a build
script will raise `KeyError`. Instead, take the literal path from
your system prompt's working-directory instruction and hardcode
it at the top of the script:

```python
OUT_DIR = Path("/home/jovyan/work/<your-notebook-dir>/_<notebook-stem>_sage_")
```

(The exact prefix is per-deployment — read it from the prompt, do
not copy a literal from this skill.)

**Parquet I/O — work around pandas 3.x bug.** Do **not** use
`pd.read_parquet()` or `df.to_parquet()` without specifying
`engine='fastparquet'`. Pandas 3.x ships with `future.infer_string=True`,
which makes `pd.read_parquet()` (both pyarrow and fastparquet
engines, via pandas) attempt to create ArrowExtensionArray for
string columns and crash with `ArrowKeyError: A type extension
with name pandas.period already defined`. The error is in pandas's
own extension-type loading path, not your code.

Use these patterns instead:

```python
# WRITE — fastparquet engine bypasses the pandas extension loader.
df.to_parquet(out_path, engine="fastparquet", index=False)

# READ — use pyarrow.parquet directly, then strip any Arrow-backed string dtypes.
import pyarrow.parquet as pq
df = pq.read_table(parquet_path).to_pandas()
for c in df.columns:
    dt = str(df[c].dtype).lower()
    if "string" in dt or "arrow" in dt:
        df[c] = df[c].astype("object")
```

This same pattern must appear in the `load_data()` helper inside
every generated SKILL.md — see Step 8.

If pyarrow / fastparquet isn't installed:
```
pip install --user pyarrow fastparquet
```
(Always `--user`; never `pip install` without it. ARGUS's system
prompt enforces this.)

**Loading R serialization sources (`.RData` / `.rda` / `.rds`).**
Use `pyreadr`. An `.RData` file (from R's `save()`) may hold
multiple named objects; an `.rds` file (from `saveRDS()`) holds a
single unnamed object. See the `objects` field in `_inventory.json`
for the object names in each file.

```python
import pyreadr

result = pyreadr.read_r("data.RData")
# result is a dict; keys are object names (or None for saveRDS)
for name, df in result.items():
    if df is not None and hasattr(df, "columns"):
        # df is a pandas DataFrame; use as normal
        ...
```

Non-DataFrame R objects (lists, vectors, model fits) show up as
unsupported types and are skipped by the inventory. If `pyreadr`
isn't installed:
```
pip install --user pyreadr
```

**Loading GPKG, GeoJSON, and Shapefile sources.** Use
`geopandas.read_file`. GeoJSON and Shapefile each hold a single
feature collection; GPKG may hold multiple named layers (see the
`layers` field in `_inventory.json`).

```python
import geopandas as gpd

# GeoJSON — one feature collection per file, no layer arg
gdf = gpd.read_file("data.geojson")

# Shapefile — pass the .shp path; sidecars (.shx, .dbf, .prj, .cpg)
# in the same directory with the same base name are read automatically
gdf = gpd.read_file("data.shp")

# GPKG single-layer file: layer argument is optional
gdf = gpd.read_file("data.gpkg")

# GPKG multi-layer file: pass the layer name from _inventory.json
gdf = gpd.read_file("data.gpkg", layer="layer_name")
```

The `crs` and `geometry_type` fields on each spatial record tell you
whether reprojection is needed before the merge. If skills combine
multiple spatial sources with different CRSs, reproject them to a
common CRS (typically `EPSG:4326`) before concatenating:

```python
gdf = gdf.to_crs("EPSG:4326")
```

**Persist spatial skills as GeoPackage (`.gpkg`), not Parquet or
Shapefile.** When the skill's data has a geometry column — inputs
were GPKG, GeoJSON, Shapefile, or you constructed points from
lat/lon — write the merged result to `data/<skill-name>.gpkg`
instead of the standard `data/<skill-name>.parquet` output. GPKG is
the preferred spatial output format because it:

- Stores geometry natively (no WKT/WKB round-trip through pandas)
- Preserves CRS metadata cleanly on read
- Opens directly in QGIS / ArcGIS / geopandas for the user without
  a load helper
- Is a **single-file format** — unlike Shapefile, which requires
  a set of sidecar files (`.shx`, `.dbf`, `.prj`, ...) to travel
  together and can drop attributes or truncate column names on
  write. If the source is Shapefile, always pack the merged output
  as GPKG, not another Shapefile.

```python
# WRITE — driver is auto-detected from .gpkg extension.
gdf.to_file(out_path, driver="GPKG")   # or just gdf.to_file(out_path)

# READ — geopandas restores the geometry column + CRS automatically.
import geopandas as gpd
gdf = gpd.read_file(gpkg_path)
```

**Two write-time gotchas — apply BEFORE `to_file` for GPKG.** Both
of these appear as warnings on every `load_data()` call if the
build script skips them. The data still loads correctly, but the
warnings clutter output and undermine user trust.

**Gotcha 1 — cast object columns to `string`, AND combine plain-time
columns with a date to make a full datetime.** Two related issues:

Part A: if a source file uses OGR's native Time / Date / Binary
types (common in GeoJSON — e.g. a `local_time` field stored as
`OFTTime`), the type carries into the GPKG. pyogrio can't map those
to native pandas dtypes and emits `Skipping field <name>: unsupported
OGR type: 10` on every read. Cast every non-geometry object column
to pandas `string` before writing:

```python
# BEFORE gdf.to_file(...) for GPKG output:
geom_col = gdf.geometry.name
for col in gdf.columns:
    if col == geom_col:
        continue
    if gdf[col].dtype == "object":
        gdf[col] = gdf[col].astype("string")
```

Part B: newer GDAL/pyogrio versions inspect VALUES on write and
re-classify `HH:MM` (or `HH:MM:SS`) string values as `OFTTime` even
after the pandas cast, because the writer's value-shape sniffer runs
independently of the pandas dtype. The `Skipping field ...: OGR type
10` warning then reappears on read.

The robust fix: if the source has a plain-time column alongside a
date column, **combine them into a full datetime column** before
writing. GDAL writes proper datetimes as `OFTDateTime`, which
pyogrio maps to `datetime64[ns]` on read — no warning. This is also
cleaner data modeling: users get one queryable timestamp, not two
separate string fields to concat.

```python
# Combine date + plain-time → full datetime, drop the plain time.
gdf["event_utc"] = pd.to_datetime(
    gdf["date_ad"].astype(str) + " " + gdf["utc_time"].astype(str),
    errors="coerce",
)
gdf = gdf.drop(columns=["utc_time"])   # keep the combined column only

# If a second time column represents a different zone (e.g. local),
# either compute it from the UTC column via a known offset, or
# combine it the same way.
```

If no date column exists alongside the plain-time column and you
cannot construct one, suppress the specific warning inside the
generated `load_data()` helper (see Step 8) so users don't see it
on every load:

```python
import warnings
warnings.filterwarnings("ignore", message="Skipping field .*unsupported OGR type: 10")
gdf = gpd.read_file(gpkg_path)
```


**Gotcha 2 — rename non-unique `id` / `fid` columns to `source_id`
before writing.** GPKG treats any column named `id`, `fid`,
`feature_id`, or `FID` as the **feature ID (FID)**, which must be
unique. If your source has a non-unique `id` (e.g. a per-batch row
number that restarts), GDAL will silently renumber on write, and
pyogrio warns `Several features with id = N have been found.
Altering it to be unique.` on every read. Fix: rename before write.

```python
# BEFORE gdf.to_file(...) for GPKG output:
def _free_name(base, existing):
    """Return `base` if unused, else `base_2`, `base_3`, ..."""
    if base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"

for id_col in ("id", "fid", "feature_id", "FID"):
    if id_col in gdf.columns and gdf[id_col].duplicated().any():
        new_name = _free_name(f"source_{id_col}", set(gdf.columns))
        gdf = gdf.rename(columns={id_col: new_name})
```

The `_free_name` helper prevents a collision if the source repo
already has a column called `source_id` (which would silently
overwrite that data on rename). If both `source_id` and
`source_id_2` are already taken, it picks `source_id_3`, and so on.

Update the SKILL.md's `## Fields` table to reflect any renames.
Note in the description or the field's row that this column preserves
the source's non-unique identifier — the actual per-record unique
key should be documented separately (e.g. a hash column, a composite
key, or "no per-record unique key exists — use the geometry +
timestamp").

The `load_data()` helper inside the generated `SKILL.md` (see
Step 8) uses `gpd.read_file(path)` when the skill's data file ends
in `.gpkg` — no fastparquet workaround needed, no dtype coercion.
For non-spatial (purely tabular) skills, stick with Parquet and the
fastparquet pattern above.

**Performance — vectorize, never iterrows() on >100K rows.** For
any source with >100,000 rows (timeseries, sensor data, large
catalogs), an `iterrows()` loop will exceed the execute tool's
120-second timeout. Read each source with `pd.read_csv(usecols=[…])`,
convert with `pd.to_datetime` / `pd.to_numeric` in bulk, `.dropna()`,
and `pd.concat` the per-file frames. A vectorized merge of ~1M
rows finishes in ~15 seconds; the iterrows version times out.

**Variable name from filename, not internal column.** If multiple
source files have an internal `variable` (or `parameter`, `field`,
`type`) column that *labels what they hold*, do not trust that
column as the canonical name — files can mislabel each other (e.g.
`pet_sum.csv` may contain `variable='pet'` internally and silently
collide with the separate `pet.csv` on merge). Derive the
canonical variable name from the filename
(`f.stem.split(".")[0]`) and assign it as a new column,
overwriting the internal one. Same principle for region/source
discriminators derived from parent directory.

**Canonicalize identifiers across files.** If a shared identifier
column (site name, well ID, station code) appears in multiple
source files, do not assume formatting is consistent. The same
site may be `"Barro Colorado Island"` in one file,
`"Barro.Colorado.Island"` in another, `"Barro_Colorado_Island"`
in a third, and `"Barro Colorado Island (BCI)"` in a fourth — a
simple `.replace("_"," ")` is not enough because R-mangled names
can lose parentheses and commas irreversibly. Build an explicit
`CANONICAL` dict mapping every observed variant to the
authoritative form (look for a sibling registry file in the repo,
or use the most common form across all files). Apply it to every
skill that uses this identifier, not just the one where you first
notice the problem.

**Post-merge verification.** After every merge, before declaring
success and writing Parquet, run these checks and print the
results:

```python
print(f"  rows={len(df):,}  cols={len(df.columns)}")
print(f"  unique {ID_COL}: {df[ID_COL].nunique()}")
print(f"  duplicates on {ID_COL}: {df[ID_COL].duplicated().sum()}")
if "lat" in df.columns:
    print(f"  lat/lon non-null: {df['lat'].notna().sum()}/{len(df)}"
          f" ({100*df['lat'].notna().mean():.1f}%)")
```

If duplicates exceed your expectation, that's a silent data
collision (often a file's content doesn't match its name — see
"Variable name from filename" above). If lat/lon non-null drops
below ~95% on a join-by-identifier coordinate attach, the
identifier canonicalization is incomplete.

Print a one-line summary of the resulting Parquet — row count,
column count, output path. So the user can sanity-check.

### Step 6 — Spatial context

If any column or sibling file looks like it contains coordinates,
incorporate them into the Parquet as standardized columns:

- Column-name hints: `lat`, `latitude`, `lon`, `lng`, `longitude`,
  `x`, `y`, `easting`, `northing`.
- Sibling shapefile or GeoJSON: load it, match on a join key,
  attach geometry.
- CRS detection: prefer an explicit `.prj` file or a `crs:`
  field in metadata. If none, infer from value ranges (values in
  [-180, 180] / [-90, 90] are WGS84; very large positive integers
  are usually a projected CRS that requires the `.prj`).

Always reproject to **EPSG:4326** and store as `lat` and `lon`
float columns in the Parquet.

**Sanity-check coordinates after every reprojection.** Don't trust
that they landed correctly. Define a plausible bounding box for
the dataset's region (e.g. lat 36–47, lon 6–18 for Italy; lat
-10–25, lon -110–-30 for the Neotropics) and count how many
points fall outside it:

```python
out_of_box = ((df["lat"] < LAT_MIN) | (df["lat"] > LAT_MAX) |
              (df["lon"] < LON_MIN) | (df["lon"] > LON_MAX))
print(f"  out-of-box: {out_of_box.sum()}/{len(df)}")
```

If more than a couple of percent fall outside, *something is
silently wrong* and you must investigate before continuing.
Common silent failures observed in real builds:

- **Column-name CRS claims are unreliable.** A column named
  `X_WGS84` may actually contain UTM-projected easting/northing
  values (e.g. 500,000–700,000 instead of decimal degrees). The
  name is publisher convention, not contract. Validate against
  the bounding box, not against the column label.
- **Truncated digits in projected coordinates.** Some catalogs
  store UTM northing with a leading "4" stripped (e.g. `952907`
  instead of `4952907`), which lands points in the wrong
  hemisphere when reprojected. If a CRS is known projected but
  values look small, try adding the missing leading digit and
  re-checking the bbox.
- **Swapped LAT/LON columns.** A "LAT" column can contain
  longitudes if the source file was authored carelessly. Detect
  by checking both axes: if `LAT` values fall in the expected
  `LON` range *and* vice versa, swap them.

If you can't recover the CRS confidently, mention it in your
completion message as a caveat. Don't silently produce garbage
coordinates.

### Step 7 — Probe categorical fields per skill

For each skill's Parquet, walk the columns and identify the ones
worth enumerating as code dictionaries — same logic as
`arcgis-feature-skill-builder` Step 4, but running locally over
the Parquet.

For each string/categorical column, count distinct values
(limit to the top 51 by frequency to bound work):

```python
df[col].value_counts().head(51)
```

Apply thresholds:

| Distinct values | Treatment |
|---|---|
| 0–1 | Skip — constant or empty. Note in field table. |
| 2–50 | Full enumeration as code dictionary in SKILL.md. |
| 51+ | Top-20 by frequency + a "more values exist" note. |

Skip columns that are obviously identifiers (`OBJECTID`, `*_id`,
UUIDs, monotonic integers), free-text comments (avg length > 80
chars), and high-precision numerics dressed as strings.

### Step 8 — Write each SKILL.md

For each skill, write
`<SAGE_OUTPUT_DIR>/_skills_/<skill-name>/SKILL.md` with this
structure (same conventions as `arcgis-feature-skill-builder`):

1. **Frontmatter** with `name` and `description`. The description:
   - Names the entity each row represents in user-natural
     language ("Each row is a Po Valley groundwater well",
     not "each row is a record in a tabular dataset").
   - Includes 2–3 synonyms users might say.
   - Names the most query-worthy attributes.
   - Stays under 100 words.
   - Mentions the data source URL (GitHub repo, CKAN dataset
     landing page, ArcGIS service URL, etc.). If a caller-written
     metadata sidecar is present (e.g. `_ckan_metadata.json`),
     prefer its `source_url` / `title` / `notes` / `license_title`
     / `organization` fields as canonical over paraphrasing from
     filenames.

   Example:

   ```yaml
   description: >-
     Each row is a groundwater monitoring well in Italy's Po
     Valley (Piemonte, Lombardia, Emilia-Romagna). Use when the
     user asks about Po basin wells, groundwater wells, or well
     locations — by region, by water use, by total depth, or
     within a spatial region. Built from
     https://github.com/rlsandovalp/Well_data_Po
   ```

2. **# `<Skill Title>`** — H1.

3. **## Description** — 1–2 paragraphs. What the data is, where
   it came from, what each row represents, the scale.

4. **## Data** — bullets:
   - Source: `<source URL>` (GitHub repo, CKAN landing page,
     ArcGIS service, etc. — use the caller's canonical URL, not
     the raw resource download URL)
   - Local cache: `data/<skill-name>.parquet` (tabular skills) or
     `data/<skill-name>.gpkg` (spatial skills — geometry column
     present)
   - Row count, column count, CRS (if spatial)

5. **## Fields** — table (name, type, meaning) for every column
   in the data file.

6. **## Field Value Dictionaries** — code subsections from Step 7,
   one per categorical column with 2–50 distinct values.

7. **## High-cardinality fields** *(if any)* — list columns
   with 51+ distinct values: name, sample of top values, note.

8. **## How to Use** — a `load_data()` helper that reads the cached
   data file.

   Both loader variants below locate the bundled data through the same
   portable `_data_dir()` helper the array core uses, so the skill still
   finds its data after it is promoted to the global registry or its
   `load_data` is copied into an analysis script — where `__file__`
   points at the script, not the skill. Emit `_data_dir()` once, then the
   loader for your format.

   ```python
   import os
   from pathlib import Path

   _SKILL_NAME = "<skill-name>"

   def _data_dir():
       """Locate the bundled data/ directory wherever the skill lives,
       without baking in an absolute path. Searches, in order: next to a
       running script; the per-notebook ARGUS output dir
       `_<stem>_sage_/_skills_/<name>/data`; the cwd and its ancestors;
       the global registry. The output dir is a *child* of the kernel's
       cwd, not an ancestor, and `$SAGE_OUTPUT_DIR` is absent from the
       execute-tool subprocess — so we glob for the `*_sage_` dir beneath
       each level rather than trusting an ancestor walk or the env var."""
       name = _SKILL_NAME
       cands = []
       def add(p):
           p = Path(p)
           if p not in cands:
               cands.append(p)
       if "__file__" in globals():
           add(Path(__file__).resolve().parent / "data")
       env = os.environ.get("SAGE_OUTPUT_DIR")
       if env:
           add(Path(env) / "_skills_" / name / "data")
       cwd = Path.cwd()
       for base in (cwd, *cwd.parents):
           add(base / "_skills_" / name / "data")
           try:
               for sage in base.glob("*_sage_"):
                   add(sage / "_skills_" / name / "data")
           except OSError:
               pass
       add(Path.home() / ".deepagents" / "agent" / "skills" / name / "data")
       for c in cands:
           if c.is_dir():
               return c
       raise FileNotFoundError(
           f"bundled data/ for '{name}' not found; searched {[str(c) for c in cands]}")
   ```

   **For spatial skills (`.gpkg` output):** use
   `geopandas.read_file` — it restores the geometry column and CRS
   natively, no dtype-coercion workaround needed.

   ```python
   from pathlib import Path
   import geopandas as gpd

   def load_data(skill_dir=None):
       """Load the cached spatial skill data as a GeoDataFrame."""
       data = Path(skill_dir) / "data" if skill_dir else _data_dir()
       return gpd.read_file(data / "<skill-name>.gpkg")
   ```

   **For tabular skills (`.parquet` output):** **use
   `pyarrow.parquet` directly, not `pd.read_parquet`** — the latter
   crashes on pandas 3.x with `future.infer_string=True` (the
   JupyterHub default) inside its own extension-type loading path.

   ```python
   import pandas as pd
   from pathlib import Path

   def load_data(skill_dir=None):
       """Load the cached skill data.

       Returns a DataFrame, or a GeoDataFrame if lat/lon columns
       are present. Uses pyarrow.parquet directly to avoid a
       pandas 3.x bug in pd.read_parquet's extension-type loader.
       """
       data = Path(skill_dir) / "data" if skill_dir else _data_dir()
       p = data / "<skill-name>.parquet"

       import pyarrow.parquet as pq
       df = pq.read_table(p).to_pandas()

       # Strip Arrow-backed string dtypes for downstream compatibility.
       for c in df.columns:
           dt = str(df[c].dtype).lower()
           if "string" in dt or "arrow" in dt:
               df[c] = df[c].astype("object")

       if {"lat", "lon"}.issubset(df.columns):
           import geopandas as gpd
           df = gpd.GeoDataFrame(
               df,
               geometry=gpd.points_from_xy(df["lon"], df["lat"]),
               crs="EPSG:4326",
           )
       return df
   ```

9. **## Examples** — 3–5 example queries showing realistic uses.
   Use the codes from Field Value Dictionaries so examples are
   real and correct. Include comments showing what the user
   might do next with the returned DataFrame.

### Step 9 — Verify, clean up the source directory, then summarize

**Before** emitting any summary, do these three things, in order:

#### 9a. Verify every skill's outputs exist on disk

For each skill in the build, confirm both of these:

```
<SAGE_OUTPUT_DIR>/_skills_/<skill-name>/SKILL.md       (exists, non-empty)
<SAGE_OUTPUT_DIR>/_skills_/<skill-name>/data/<skill-name>.parquet  (exists, non-empty; OR .gpkg for spatial skills)
```

If any are missing, the build is incomplete — fix the missing
outputs and re-verify. **Do NOT proceed to cleanup until every
proposed skill has both files on disk.** The cleanup step deletes
the source directory, which is the only fallback if a Parquet
write failed.

#### 9b. Delete intermediate scratch files from each skill directory

Steps 7 and 8 often use per-skill intermediate files (e.g.
`_categorical_probes.json`, `_probe_results.json`) so Step 8's
SKILL.md-writer can read the probe results back from disk. **These
files are build-time artifacts, not part of the deployable skill,
and must be deleted before finishing.** The final skill directory
should contain ONLY:

```
<SAGE_OUTPUT_DIR>/_skills_/<skill-name>/
  SKILL.md
  data/
    <skill-name>.parquet    # or <skill-name>.gpkg for spatial skills
```

Anything else — files starting with `_`, temporary scripts, backup
`.old` files, hidden `.probe/` directories — is scratch and should
be removed. A `find <skill-dir> -type f` should list at most two
files (SKILL.md + one data file) per skill.

#### 9c. Delete the source directory

**After verification passes, delete the source directory.** This
is mandatory, not optional. When invoked by a fetcher skill, the
source directory is typically `/tmp/repo-skills/<name>/`; when
invoked directly by the user, use whichever path they staged. In
both cases: delete it now.

Two real problems if the source files are left in place:

1. **They compete with the skill in future `%%ask` cells.** When
   a future query asks something like "compare Lombardia and
   Piemonte well counts", the agent may discover the raw
   CSV/XLSX files in the source directory and query them directly
   — bypassing the skill's clean Parquet caches. The user can't
   tell whether they got data from the skill or from the raw
   source. The skill must be the **only** authoritative path to
   its data. Two paths to the same data is worse than one slow
   path, because the user loses trust in which they're getting.

2. **They bloat persistent quota.** If the source directory
   landed in `SAGE_OUTPUT_DIR` or anywhere under `~/work/`, it's
   on a persistent volume with a ~10 GB shared quota. A 500 MB
   staging directory eats 5% of the user's quota for no benefit;
   the Parquet caches are what they actually use.

Run exactly the source directory path, no parent. Example:

```bash
# Typical fetcher-staged location:
rm -rf /tmp/repo-skills/<name>
```

**Safety rules — read these before running `rm -rf`:**

- Never `rm -rf $SAGE_OUTPUT_DIR` itself, or `~/work`, or any
  ancestor of the source directory. You delete **only** the
  specific staging directory the caller supplied.
- Verify the path you're about to delete contains a
  `_inventory.json` file (proof it's the source you built from,
  not unrelated user data) before running `rm -rf`. A quick
  `ls <path>/_inventory.json` check is cheap.
- If you're uncertain about the source path, list the candidate
  directories first (`ls /tmp/repo-skills/ 2>/dev/null` and
  `ls "$SAGE_OUTPUT_DIR" 2>/dev/null`) and confirm.

#### 9d. Emit the structured summary

After verification + cleanup, emit one final summary message to
the user, using the same shape as the `arcgis-feature-skill-builder`
completion message.

**When you have built multiple skills, give every skill the same
level of detail.** Do not compress later skills just because the
overall message is getting long. The user opened a one-cell
operation and wants to learn what each skill is, separately. A
two-line filter table on the third skill teaches less than a
five-line table on the first one — that asymmetry is a quality
defect. Treat each skill as if it were the only one in the build.

**Compression at any skill is a defect, not a courtesy.** If your
overall message is getting long, the right response is to cut prose
elsewhere — the intro sentence, the closing paragraph — not to
shrink later skills' tables or queries. Every skill in the build
must meet the table-sizing and query-quality minimums below; a
"summary" version of a skill that falls short of those minimums is
shipped broken. This rule applies whether you have 2 skills or 12.
If a skill's data shape genuinely cannot support 7 queries (e.g.
the source has 3 columns and 12 rows), state that explicitly in
the one-line caveat ("only 4 query axes are meaningful given the
3-column schema") rather than producing a quietly thin section.

**Use Markdown pipe tables, not code-block ASCII tables.** Render
the filter table as `| Dimension | Field | Example values |` with
a `|---|---|---|` separator row. Pipe tables render reliably in
Jupyter, GitHub previews, and nbviewer; ASCII tables with `─`
separators render unevenly across these surfaces and look unpolished
in saved notebooks. The template below shows the correct format.

For each skill, in order, output (rendered as Markdown — the table
must use pipe syntax, not code-block ASCII):

```markdown
✓ Built skill **<skill-name>** at `_skills_/<skill-name>/SKILL.md`.

Each row is <one-sentence entity description>
(<count> <units>, <high-level qualifier>).

**What you can filter on:**

| Dimension | Field | Example values |
|---|---|---|
| <dim1> | `<col1>` | <4–6 sample values> |
| <dim2> | `<col2>` | <4–6 sample values> |
| <dim3> | `<col3>` | <4–6 sample values> |
| <dim4> | `<col4>` | <4–6 sample values> |
| <dim5> | `<col5>` | <4–6 sample values> |
| Spatial area | (`lat`, `lon`) | any rectangle in WGS84 |

(Omit the Spatial area row if the skill has no geometry.)

**Example queries:**

- **By <axis1>** — "<filter query>"
- **By <axis1>** — "<aggregate or compare query on same axis>"
- **By <axis2>** — "<filter query>"
- **By <axis2>** — "<aggregate or compare query on same axis>"
- **By location** — "<spatial filter>"
- **Combined** — "<multi-axis or spatial + attribute query>"
- **Cross-skill** — "<query that joins to another skill in this build>"

The skill returns a `<DataFrame>` / `<GeoDataFrame>` you can join with
other skills (e.g., `<plausible companion skill>`) in the same
`%%ask` cell.

[Optional one-line caveat — only if you know of a real gotcha:
CRS confidence, missing values in a key field, non-standard
units. Skip otherwise.]
```

**Table sizing rules:**
- Include at least **5 dimensions** in the filter table. Typical
  pattern: region/source, a primary categorical, a secondary
  categorical, a numeric range, identifier, spatial (if
  geometry-bearing). If the skill has joined attributes from a
  registry (e.g. a timeseries skill that inherits `well_type` /
  `province` from the well registry), show those joined dimensions
  too — the user can filter on them just as easily as on the
  native ones, but only if you tell them.
- For each dimension, show **4–6 concrete example values** pulled
  from the Field Value Dictionaries. Use the codes / short forms
  the user would type, not abstract field-set descriptions. A row
  like `Temperature vars | 11 BIO cols | BIO1, BIO5, BIO12, …` is
  wrong — that's meta-description, not example values. The right
  form is `Bioclim variable | bio_var | BIO1 (annual mean temp),
  BIO5 (max temp warmest month), BIO12 (annual precip), BIO15
  (precip seasonality)`. The user must come away knowing what
  specific values to put in their query.
- For wide schemas (50+ summary-stat columns, etc.), pick the
  4–6 most query-worthy column groups and list them by name. Do
  not write `~50 columns` and a vague "etc." — that hides the
  schema. Concrete examples beat counts.

**Query quality rules:**
- Produce **at least 7 example queries per skill**, spread across
  the dimensions in the table. One query per axis is not enough
  — pair a filter with an aggregate or compare on the same axis,
  so the user sees both shapes (e.g. "drinking-water wells in
  Lombardia" + "average depth of drinking-water wells by region").
- Write queries in **domain language**, not column names. A
  scientist will ask "wells near Bologna", not "wells where
  municipality == 'Bologna'". The agent will translate.
- When skills in this build can join to each other, **include at
  least one cross-skill query per skill** that demonstrates the
  join. This is the most valuable kind of example because skills
  rarely live alone — the composition cue makes the build feel
  whole.
- Mix three query shapes: **filter** ("show me X where Y"),
  **aggregate** ("how many / mean / max of X by Y"), and
  **compare** ("X versus Y"). A 7-query list with only filter
  queries is undercooked; the user can't tell what the skill is
  capable of beyond `df.query()`.

Do not add a closing line about installing the skill or making
it "available". The skill is already available in this notebook:
the agent's setup auto-scans `<SAGE_OUTPUT_DIR>/_skills_/` on
every `%%ask` cell, so the freshly-built skill is loaded on the
user's next turn without any additional command. Adding a "to
make the skill available, run %%skill…" line is misleading — it
implies the user must do something they don't.

Do not auto-install into the global registry. If the user later
wants the skill in other notebooks too, that's their explicit
follow-up — they already know how to invoke `%%skill`, and
suggesting it unprompted is noise.

## Quality Checklist

Before reporting success, verify each generated SKILL.md:

- [ ] Skill name is lowercase kebab-case.
- [ ] Frontmatter description explicitly names the entity in plain
      language and includes 2–3 user-natural synonyms.
- [ ] Description references the source URL (GitHub repo, CKAN
      landing page, ArcGIS service, etc.).
- [ ] Description does not mention implementation details
      (load_data, parquet, pandas).
- [ ] `data/<skill-name>.parquet` (tabular) or
      `data/<skill-name>.gpkg` (spatial) exists and is non-empty.
- [ ] Field Value Dictionaries section has at least one entry
      (otherwise the data is fully high-cardinality, which is
      unusual — double-check the probe didn't fail).
- [ ] Examples section has at least 3 working queries using real
      values from the dictionaries.

## Things to Avoid

- **Do not skip the Step 4 STOP gate.** Even if the inventory
  looks unambiguous, give the user a chance to redirect. The
  cost is one cell of waiting; the benefit is you don't build
  three skills the user wanted as one.
- **Do not merge across schemas silently.** Near matches go in
  the proposal as questions, not as decisions.
- **Do not write skills to `~/.deepagents/agent/skills/`.** Local
  only; the user promotes explicitly.
- **Do not invent caveats** to fill the completion message's
  optional caveat line. If there's no real gotcha, omit it.
- **Do not say "in the dataset"** or "in this catalog" in
  example queries — skills are reusable building blocks, not
  standalone datasets.

## Worked Example (abbreviated)

The Po Valley wells repo
(`https://github.com/rlsandovalp/Well_data_Po`) contains data
from three Italian regions in three formats:

- Piemonte: per-well CSV time series + a shapefile of well
  positions in ESRI:54012 (Eckert IV).
- Lombardia: 12 per-province XLSX files with both registry and
  measurements, coordinates in EPSG:4326.
- Emilia-Romagna: mixed XLSX with coordinates in EPSG:23032
  (Monte Mario / Italy zone 2).

After Step 2 enumeration and Step 3 grouping, the proposal looks
like:

```
Proposed skills:

1. po-wells
   - Entity: each row is a Po Valley groundwater well
   - Merged from: Piemonte/wellPositions.shp,
                  Lombardia/*.xlsx (registry sheets),
                  EmiliaRomagna/*.xlsx (registry sheets)
   - Estimated rows: ~2,400
   - Key columns: well_id, region, lat, lon, elevation_m,
                  total_depth_m, water_use, well_type

2. po-wells-timeseries
   - Entity: each row is one water-table-depth measurement
   - Merged from: Piemonte/*.csv,
                  Lombardia/*.xlsx (measurement sheets),
                  EmiliaRomagna/*.xlsx (measurement sheets)
   - Estimated rows: ~620,000
   - Key columns: well_id, region, date, water_table_depth_m

Open questions:
- Reconciliation: column elevation has names QUOTA (Piemonte),
  Quota_msl (Lombardia), elev_m (Emilia-Romagna). I'll normalize
  to elevation_m. OK?

Reply with "yes" to proceed.
```

User replies "yes". The agent then executes Steps 5–9 and
produces both skills with a final completion message for each.
