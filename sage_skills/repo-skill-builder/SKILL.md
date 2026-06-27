---
name: repo-skill-builder
description: >-
  Build one or more ARGUS skills from a GitHub repository containing
  CSV or Excel data files. Use when the user provides a github.com
  URL and asks to build, create, or generate skills from the data
  in the repo. Two-phase workflow: first you enumerate the data
  files and propose a skill plan, then you STOP for the user's
  approval; the user replies in the next %%ask cell with "yes" /
  edits / "no", and you continue from where you stopped.
---

# Repo Skill Builder

## When to Use

Trigger this skill when the user says any of:

- "build a skill from `https://github.com/<owner>/<repo>`"
- "could you turn this repo's CSV files into a skill"
- "convert the data in `<github url>` into ARGUS skills"
- "make a skill out of this repo: `<github url>`"

Decline (do not use this skill) when:

- The URL is not a github.com URL (use `arcgis-feature-skill-builder`
  for ArcGIS Feature/Map Service URLs; advise the user no skill-builder
  exists yet for other URL types).
- The repo contains only code with no tabular data — there's nothing
  to build a queryable skill against.
- The user has already named specific files and just wants them
  loaded into a notebook for ad-hoc analysis — that's a plain
  `%%ask` task, not a skill-build.

## What This Skill Produces

One or more skills under `SAGE_OUTPUT_DIR/_skills_/<skill-name>/`,
each containing:

- `SKILL.md` — the agent-readable skill descriptor (frontmatter +
  body).
- `data/<skill-name>.parquet` — the cleaned, merged data the skill
  queries.

The skills are **local-only**, written to the notebook's
per-notebook scratch folder. They are NOT installed into the
global registry at `~/.deepagents/agent/skills/`. The user can
promote them globally with an explicit `%%skill _skills_/<name>`
cell, but that's their decision, not yours.

`SAGE_OUTPUT_DIR` is an environment variable available to your
scripts; the folder already exists.

## What You Need From the User

Just the GitHub URL. The user does not need to specify the skill
name, the file format, or anything else. Your job is to inspect
the repo and propose a sensible plan.

If the user has expressed preferences (e.g. "merge them all into
one skill" or "split by region"), incorporate those into your
proposal in Step 3.

## Steps to Build the Skills

### Step 1 — Clone the repo into /tmp

**Clone to `/tmp/repo-skills/<repo-name>` — never to SAGE_OUTPUT_DIR
or anywhere under `~/work/`.** This is non-negotiable. The clone is
throwaway scratch input; once the Parquet caches are written we
never read it again. /tmp is ephemeral pod-local storage that
disappears on pod recycle, which is the correct lifetime for build
scratch. SAGE_OUTPUT_DIR lives on a persistent volume with a strict
quota (~10 GB shared across the user's notebook outputs and skill
caches) and putting throwaway data there bloats that quota.

The system prompt elsewhere restricts file *reading* and *writing*
to SAGE_OUTPUT_DIR plus the skills directory, but `git clone` is a
shell-level operation that materializes a directory tree — and for
this skill specifically, /tmp is the correct staging area. Use it.

```bash
mkdir -p /tmp/repo-skills
cd /tmp/repo-skills
# --depth=1 skips history; we just need the latest snapshot.
git clone --depth=1 https://github.com/<owner>/<repo>.git <repo-name>
```

If the clone fails (private repo, network error), report the
error to the user and stop. Do not try to authenticate.

If the repo is huge (>500 MB) **stop and ask the user** before
downloading — a quick `git ls-remote` or GitHub API call to
estimate size is fine.

### Step 2 — Enumerate the tabular data files

Walk the repo tree and collect every `.csv`, `.tsv`, `.xlsx`,
`.xls`, and `.parquet` file. Skip anything obviously not data:

- README.md, LICENSE, CITATION.cff, .gitignore, .gitattributes
- Notebooks, Python source, configuration files
- Figures, images
- Files inside `.git/` or hidden directories

For each tabular file, capture:

- Relative path
- Size on disk
- For CSV/TSV: detected delimiter, encoding, header row
- Column names (sorted) — this is the **schema fingerprint**
- Approximate row count (for CSV: line count; for XLSX: from
  metadata or by sampling)
- A 3-row sample of the data, so you can sanity-check types

Save this catalog to `/tmp/repo-skills/<repo-name>/_inventory.json`
so you can refer back to it without re-scanning. Same scratch
lifetime as the clone itself.

A small Python helper using pandas/openpyxl is the easiest way.
Do not load the full data here — only enough to learn the
schema.

### Step 3 — Group files into skill candidates

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
I've inspected <repo-name>. Here's what I found and what I propose:

Repo: <repo url>
Files scanned: <N> CSV/Excel files, <total size>

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

(Resume state on disk: /tmp/repo-skills/<repo-name>/ —
the clone and _inventory.json are both there.)
```

After the "Reply with yes…" line, add one **resume hint** line so
your next-cell self knows where the build state lives:

```
(Resume state on disk: /tmp/repo-skills/<repo-name>/ —
the clone and the inventory file are both there.)
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
   re-enumerate the repo from scratch unless something is gone.
2. **Then check the working directory.** Run a single `ls
   /tmp/repo-skills/<repo-name>/` to confirm the clone is still
   there, and `ls /tmp/repo-skills/<repo-name>/_inventory.json`
   to confirm the inventory is still there.
3. **Branch on what you find:**
   - Both present (the normal case) → load `_inventory.json` and
     go straight to Step 5. Do not re-run schema probes, do not
     re-read CSVs to "double-check" anything you already saw in
     Step 2.
   - Clone missing → `/tmp` got cleared (rare; pod recycle
     between cells). Re-clone with the same command from Step 1,
     then continue. Do not re-enumerate everything; the schema
     fingerprints are in your message history.
   - Inventory file missing but clone present → re-run the
     enumerate-and-save step only. Skip the README/processing-
     script reading you already did.

Trusting your own conversation history is the discipline here.
The user will see every redundant `ls`, every re-read of a file
you already read, every re-clone. Those are wasted turns. Make
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
   `<SAGE_OUTPUT_DIR>/_skills_/<skill-name>/data/<skill-name>.parquet`.

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
   - Mentions the data source (the GitHub repo URL).

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
   - Source: `<github url>`
   - Local cache: `data/<skill-name>.parquet`
   - Row count, column count, CRS (if spatial)

5. **## Fields** — table (name, type, meaning) for every column
   in the Parquet.

6. **## Field Value Dictionaries** — code subsections from Step 7,
   one per categorical column with 2–50 distinct values.

7. **## High-cardinality fields** *(if any)* — list columns
   with 51+ distinct values: name, sample of top values, note.

8. **## How to Use** — a `load_data()` helper that reads the
   Parquet and returns a DataFrame (or GeoDataFrame, if there
   are `lat`/`lon` columns). **Use `pyarrow.parquet` directly,
   not `pd.read_parquet`** — the latter crashes on pandas 3.x
   with `future.infer_string=True` (the JupyterHub default
   environment) inside its own extension-type loading path.

   ```python
   import pandas as pd
   from pathlib import Path

   def load_data(skill_dir=None):
       """Load the cached skill data.

       Returns a DataFrame, or a GeoDataFrame if lat/lon columns
       are present. Uses pyarrow.parquet directly to avoid a
       pandas 3.x bug in pd.read_parquet's extension-type loader.
       """
       if skill_dir is None:
           skill_dir = Path(__file__).parent
       p = Path(skill_dir) / "data" / "<skill-name>.parquet"

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

### Step 9 — Confirm with a structured completion message

After all SKILL.md files and Parquet caches are written, emit one
final summary message to the user, using the same shape as the
`arcgis-feature-skill-builder` completion message. The /tmp clone
will be reclaimed automatically on pod recycle — no explicit
cleanup needed.

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
- [ ] Description references the source GitHub URL.
- [ ] Description does not mention implementation details
      (load_data, parquet, pandas).
- [ ] `data/<skill-name>.parquet` exists and is non-empty.
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
