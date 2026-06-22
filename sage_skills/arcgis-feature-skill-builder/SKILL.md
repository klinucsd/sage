---
name: arcgis-feature-skill-builder
description: >-
  Build an ARGUS skill from an ArcGIS Feature Service or MapService URL.
  Use when the user asks to build, create, or generate a skill for a
  URL whose path contains /FeatureServer or /MapServer.
---

# ArcGIS Feature Skill Builder

This is a **meta-skill**: instructions for the agent on how to author a
new skill that exposes an ArcGIS Feature Service to natural-language
queries. The agent reads this skill, follows the steps below, and
produces a freshly-installed skill that subsequent `%%ask` cells can use.

## When to Use

Invoke this skill whenever the user's request involves building a skill
for an ArcGIS Feature Service. Triggers include:

- A `%%skill-build` cell whose body is an ArcGIS Feature Service URL.
- A `%%ask` cell asking to "create a skill for this ArcGIS service" or
  "build a skill for <URL>" where the URL points to an ArcGIS layer.
- Any prompt where the user gives a URL containing `/FeatureServer/<n>`
  or `/MapServer/<n>` and asks for queryable access.

If the URL is **not** an ArcGIS Feature Service (a CSV file, a GitHub
repo, a generic web page), do not invoke this skill. A different
skill-builder applies.

## What This Skill Produces

A complete, immediately-usable ARGUS skill under
`_skills_/<skill-name>/SKILL.md` next to the user's notebook, with this
shape:

```
_skills_/<skill-name>/
└── SKILL.md           # description + fields + code dictionaries + loader + examples
```

(No `data/` folder — ArcGIS queries are live, not bundled.)

The generated SKILL.md is structured to be **immediately queryable in
natural language**: it contains a field table with inferred meanings, code
dictionaries for every categorical field (so the agent knows that
`STATE='CA'`, not `'California'`), a tested `get_features` loader function
with proper pagination and CRS handling, and 3–5 worked example queries.

After the SKILL.md is written, **install it** by invoking the standard
`%%skill` mechanism on the path `_skills_/<skill-name>/`. The skill is
then available in the same kernel session for subsequent `%%ask` cells.

## Steps to Build the Skill

Follow these in order. Each step's output feeds the next.

### Step 1 — Fetch service metadata

Make an HTTP GET to `<service_url>?f=json` and parse the JSON response.
This single call provides almost everything you need:

- `name`, `description` — service title and description
- `geometryType` — `esriGeometryPoint`, `esriGeometryPolygon`, etc.
- `extent` — the spatial bounding box of all features
- `sourceSpatialReference.wkid` or `extent.spatialReference.wkid` — the
  service's native spatial reference (e.g., `4326`, `3857`, `102100`)
- `maxRecordCount` — the per-query record cap (typically 1000–2000)
- `fields` — list of field metadata objects, each with `name`, `type`,
  `alias`, and optionally `domain`
- `supportsPagination`, `advancedQueryCapabilities` — capability flags

If the request fails (404, 500, or the response is HTML/error JSON
instead of valid metadata), abort with a clear message: "URL does not
appear to be a valid ArcGIS Feature Service layer."

### Step 2 — Decide the skill name

The skill name must be **lowercase kebab-case** (letters, digits, and
hyphens only). Derive it from the service `name` field in the metadata:

- Lowercase
- Replace spaces and underscores with hyphens
- Strip punctuation other than hyphens
- Collapse multiple hyphens
- If the resulting name is generic (e.g., just `feature-layer`,
  `service`, `layer-0`), use your judgment to produce a more meaningful
  name from the service description or URL path. For example:
  - Service named "Surface_and_Underground_Coal_Mines_in_the_US" →
    `coal-mines`
  - Service named "US_Hospitals" → `hospitals`
  - Service named "CA_Perimeters_NIFC_FIRIS_public_view" →
    `ca-wildfire-perimeters`

Tell the user the chosen name in your response. If the user expressed a
preferred name in the original request, honor it.

### Step 3 — Build the field table

For each field in `metadata.fields`, produce a Markdown table row:

```
| <field_name> | <type> | <meaning> |
```

- `<field_name>`: from `field.name` verbatim
- `<type>`: a human-readable version of `field.type` (strip the
  `esriFieldType` prefix: `String`, `Integer`, `Double`, `Date`, etc.)
- `<meaning>`: see below

For the meaning column:

- If the field has a non-empty `alias` that differs meaningfully from
  the name (not just a casing variant), use the alias as the starting
  point.
- Use your understanding of common geospatial conventions to expand
  the meaning. E.g., `GIS_ACRES` → "Feature area in acres". `INCIDENT_NAME`
  → "Human-readable incident name". `LATEST_ATTR_DATETIME` → "Latest
  attribute update timestamp (epoch milliseconds)".
- For fields where you cannot reliably infer the meaning, write
  `<TODO: confirm meaning>` so the user can edit the SKILL.md.

Mark date/timestamp fields explicitly — ArcGIS dates are usually epoch
milliseconds, which is non-obvious. Example:
`ALARM_DATE | Date | Date the fire was reported (epoch milliseconds)`.

### Step 4 — Identify which fields are categorical

A "categorical" field is one whose values come from a finite set of
codes (like `STATE='CA'`). The SKILL.md will list the codes so the agent
can write correct WHERE clauses. **Not every string field is
categorical** — descriptions, names, and identifiers are not. Apply
these rules in order:

**Rule A — Domain wins.** If `field.domain.type == "codedValue"`, the
field has a publisher-declared code list under `field.domain.codedValues`.
Use these directly; no probing needed. This is the gold standard.

**Rule B — Name pattern skip.** Skip the probe for fields whose name
contains any of these substrings (case-insensitive). These are
overwhelmingly free-text or identifiers, never categorical:

```
description, descr, summary, narrative, notes, note_,
comment, remark, details, message, body, text,
address, addr, street,
url, link, website, web, image,
filename, filepath, path, uri,
date, time, _dt, _ts, datetime, timestamp
```

Also skip ID fields by these patterns:
- exact match: `id`, `fid`, `oid`, `objectid`, `gid`
- suffixes: `_id`, `_uuid`, `_guid`, `_key`
- prefixes: `id_`

**Important**: do NOT skip on the substring `name` or `title` alone —
`STATE_NAME` may be categorical, `MINE_NAME` is not. Rely on the
distinct-value probe below to distinguish.

**Rule C — Distinct-value probe.** Run ONE script that probes EVERY
string field that passed Rules A and B in a single batched pass. Do
not run one probe per field — that wastes service calls and fills your
own context with redundant data. Write the full probe results to a
single JSON file under `SAGE_OUTPUT_DIR` (e.g., `probe_results.json`),
keyed by field name, with the distinct values and their counts. From
that point on, **read from the JSON file** when composing the field
table, the code dictionaries, and the examples. Do NOT re-issue
service queries to recover information you already collected — even
"just to verify a count" or "just to confirm the top value." The JSON
file is the source of truth for the rest of the build. Re-probing
after the first batched pass is a build failure: stop and ask the
user if anything is unclear.

Use the following canonical probe-script template **verbatim**,
substituting `<SERVICE_URL>`, `<OID_FIELD>` (usually `OBJECTID` or
`FID`), and `<CANDIDATE_FIELDS>` (the list of string fields that
passed Rules A and B). Critically, every HTTP request **must** have
`timeout=60` — without a timeout, a single slow or stalled response
hangs the script indefinitely (observed on Colab 2026-06-23).
`flush=True` on the progress prints ensures stdout reaches the agent
even if the script is killed mid-run.

```python
import os, json, requests
from pathlib import Path

SERVICE_URL = "<SERVICE_URL>"
OID_FIELD = "<OID_FIELD>"
CANDIDATE_FIELDS = <CANDIDATE_FIELDS>   # e.g. ["source", "displayStatus", ...]

output_dir = Path(os.environ["SAGE_OUTPUT_DIR"])
query_url = SERVICE_URL.rstrip("/") + "/query"
results = {}

for field in CANDIDATE_FIELDS:
    print(f"Probing {field}...", flush=True)
    params = {
        "where": "1=1",
        "groupByFieldsForStatistics": field,
        "outStatistics": json.dumps([{
            "statisticType": "count",
            "onStatisticField": OID_FIELD,
            "outStatisticFieldName": "n",
        }]),
        "f": "json",
    }
    try:
        r = requests.get(query_url, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        results[field] = {"error": f"{type(e).__name__}: {e}"}
        continue
    if "error" in data:
        results[field] = {"error": str(data["error"])}
        continue
    values = [
        {"value": f["attributes"].get(field),
         "count": f["attributes"].get("n", 0)}
        for f in data.get("features", [])
    ]
    values.sort(key=lambda v: -v["count"])
    results[field] = {"distinct_count": len(values), "values": values}

(output_dir / "probe_results.json").write_text(
    json.dumps(results, indent=2, default=str)
)
print(f"\n✓ Wrote {len(results)} field probes to probe_results.json",
      flush=True)
```

The underlying query each call makes hits the service's statistics
endpoint:

```
GET <service_url>/query
  ?where=1=1
  &groupByFieldsForStatistics=<field>
  &outStatistics=[{"statisticType":"count","onStatisticField":"<oid_field>","outStatisticFieldName":"n"}]
  &f=json
```

Where `<oid_field>` is the ObjectID field (typically `OBJECTID` or
`FID`; fall back to `"objectid"` if uncertain — most services accept
it case-insensitively).

The response is a list of records, one per distinct value, with a count.
Apply thresholds:

| Distinct value count | Treatment |
|---|---|
| 0–1 | Skip — constant or empty. Note in field table. |
| 2–50 | Full enumeration in the code dictionary. |
| 51–500 | Show top 20 by frequency + "more values exist". |
| > 500 (or response capped at maxRecordCount) | Skip — high cardinality. Mark in a "fields with high cardinality" note. |

**Rule D — Length sanity check.** Even if a field passes the count
threshold, if the average distinct-value string length exceeds 80
characters, treat as non-categorical (it's likely paragraph-style
free text masquerading as a string column).

### Step 5 — Build code dictionaries for surviving categorical fields

For each field that passed the filtering, write a Markdown subsection:

```markdown
### `<field_name>` codes
- `<CODE1>` = <human-readable meaning>
- `<CODE2>` = <human-readable meaning>
- ...
```

For meanings:

- **Domain fields**: use `codedValues[i].name` verbatim.
- **Probed fields**: the raw code is the left column. For the meaning:
  - If the code is self-evident (`CA`, `OPEN`, `CLOSED`, `Y`, `N`), no
    description needed — just list the codes:
    ```
    - `OPEN`, `CLOSED`, `UNDER_CONSTRUCTION`
    ```
  - If the code is acronym-like and you can infer the expansion with
    high confidence from the field name + service context, do so
    (`CALEPA` in an agency field → "California Environmental Protection
    Agency").
  - If uncertain, just list the raw code without a description. Do not
    invent meanings.

Include the per-value counts when available — they help the agent
understand which categories are common:

```markdown
- `FEDERAL` (5,221 records)
- `STATE` (1,832 records)
```

### Step 6 — Identify the geometry type and CRS handling

From the metadata:

- `geometryType` — translate to a plain English description:
  - `esriGeometryPoint` → "Point"
  - `esriGeometryPolyline` → "Polyline"
  - `esriGeometryPolygon` → "Polygon"
  - `esriGeometryMultipoint` → "MultiPoint"
- `sourceSpatialReference.wkid` (or `extent.spatialReference.wkid` if
  the source SR isn't published) — the native SR as an integer EPSG
  code. Common values:
  - `4326` — WGS84 lon/lat
  - `3857` or `102100` — Web Mercator
  - `2229` and similar — state plane projections

Record these in the SKILL.md and use them as the `<NATIVE_SR>`
substitution in the canonical loader (Step 7).

### Step 7 — Embed the canonical `get_features` loader

The SKILL.md must include this function definition **verbatim**, with
two placeholders filled in: the `SERVICE_URL` constant and the
`NATIVE_SR` comment. This is the tested, correct implementation — do
not modify the pagination logic, the CRS handling, or the error
handling. Modifying any of these regenerates known bugs.

The agent then calls `get_features()` when the user asks queries.

```python
import requests
import geopandas as gpd

SERVICE_URL = "<SERVICE_URL>"      # ← substitute the service URL here
# Native spatial reference: <NATIVE_SR>  (informational only — loader
# sets inSR=4326 when a bbox is supplied, regardless of native SR)

def get_features(where="1=1", bbox=None, target_crs="EPSG:4326",
                 page_size=2000, timeout=60, service_url=SERVICE_URL):
    """Fetch features from an ArcGIS Feature Service as a GeoDataFrame.

    Parameters
    ----------
    where : str
        SQL-style WHERE clause (e.g. "STATE = 'CA' AND BEDS >= 100").
        Use "1=1" to match all features.
    bbox : tuple or None
        Spatial filter as (min_lon, min_lat, max_lon, max_lat) in WGS84
        (EPSG:4326). When supplied, both inSR=4326 and outSR are set so
        the bbox is interpreted as WGS84 regardless of the service's
        native SR. Strongly recommended for any state/county/regional
        query — the service uses bbox as a pre-filter before applying
        WHERE, which dramatically reduces wire traffic.
    target_crs : str
        Output CRS for the returned GeoDataFrame. Default "EPSG:4326".
    page_size : int
        Records per page. ArcGIS services typically cap this at
        1000–2000; the service uses its own max if this is higher.
    timeout : int
        Per-request HTTP timeout in seconds.

    Returns
    -------
    geopandas.GeoDataFrame
        All matching features in `target_crs`. Empty (with a `geometry`
        column) if no matches.
    """
    target_sr = target_crs.split(":")[-1]
    base_params = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "f": "geojson",
        "outSR": target_sr,
        "resultRecordCount": page_size,
    }
    if bbox is not None:
        minx, miny, maxx, maxy = bbox
        base_params.update({
            "geometry": f"{minx},{miny},{maxx},{maxy}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",   # bbox is interpreted as WGS84
            "spatialRel": "esriSpatialRelIntersects",
        })

    query_url = service_url.rstrip("/") + "/query"
    all_features = []
    offset = 0
    while True:
        params = dict(base_params, resultOffset=offset)
        r = requests.get(query_url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS service error: {data['error']}")
        page_features = data.get("features", [])
        all_features.extend(page_features)
        if not data.get("exceededTransferLimit", False):
            break
        offset += page_size

    if not all_features:
        return gpd.GeoDataFrame(columns=["geometry"], crs=target_crs)
    return gpd.GeoDataFrame.from_features(all_features, crs=target_crs)
```

### Step 8 — Compose the SKILL.md

Assemble the generated content in this order:

1. **Frontmatter** — `name` (the kebab-case name from Step 2) and
   `description` (this is what the *agent* reads to decide if the
   skill applies to a future user query — be deliberate here).

   The description must satisfy three things:

   a. **Explicitly name the entity each row represents.** Use a short
      noun phrase a domain user would actually say, not a paraphrase
      of the service title. For each row, ask: "if a scientist
      pointed at one row and said 'this is a …', what would the word
      be?" The answer is the entity name. Examples:

      - `coal-mines` → "Each row is a U.S. coal mine."
      - `hospitals` → "Each row is a U.S. hospital."
      - `ca-wildfire-perimeters` → "Each row is a California
        wildfire (one perimeter polygon per fire)."
      - `po-wells` → "Each row is a Po basin groundwater well."

      State this entity in the description. Users will refer to the
      entity by name in their queries ("show me California
      wildfires", "list hospitals with helipads"), and the agent
      needs to recognize the match.

   b. **Include common synonyms and user-natural phrasings.** A
      wildfire is also a "fire", a "blaze", a "fire incident". A
      well is also a "monitoring station", a "borehole". Don't
      enumerate every possible synonym — pick 2–3 of the most
      likely terms a user would use. This protects against the case
      where the user phrases the query in a different vocabulary
      than the service publisher's field names.

   c. **State the trigger conditions for the skill.** Use the
      phrasing "Use when the user asks about <entity> — by
      <attribute1>, by <attribute2>, in <spatial region>, …".
      List the 3–6 most query-worthy attributes. These are the
      keywords the agent matches against future user queries.

   Keep the total description under 100 words. Body sections do not
   affect routing — only the frontmatter description does. Spend the
   words on the entity name, the synonyms, and the trigger
   attributes; do not waste them on implementation detail (loader
   name, output type, file location). See
   `[[skill-descriptions]]` for the convention.

   Example (for ca-wildfire-perimeters):

   ```yaml
   description: >-
     Each row is a California wildfire (one perimeter polygon per
     fire). Use when the user asks about California wildfires,
     fires, fire perimeters, or fire incidents — by incident name,
     by active/inactive status, by source (FIRIS, CAL FIRE, NIFC,
     USFS, WFIGS), by discovery date, by acreage, or within a
     spatial region.
   ```
2. **# `<Skill Title>` Skill** — H1 header.
3. **## Description** — 1–2 paragraphs. What the dataset is, who
   publishes it, what each row represents, the typical scale.
   Mention the data source URL prominently.
4. **## Service Details** — bullets with native SR, geometry type,
   maxRecordCount, and the service URL. Helps the agent reason
   about scale and projection.
5. **## Fields** — Markdown table (name, type, meaning) covering
   every field from Step 3.
6. **## Field Value Dictionaries** — code subsections from Step 5,
   one per categorical field. Open with a short note:
   > Use the **code** (left) in WHERE clauses, not the description.
7. **## Fields with high cardinality** *(only if any)* — list fields
   that probed too many distinct values, so the agent knows not to
   assume a fixed code set:
   ```
   - `INCIDENT_NAME`: ~1,200 distinct values (free text)
   - `OBJECTID`: unique per row (identifier)
   ```
8. **## How to Use** — narrative section explaining the query
   pattern. Always include:
   - A note about using `bbox` for regional queries
   - A reminder that WHERE values must match the codes in the
     Field Value Dictionaries section
   - The `get_features` code block from Step 7 (verbatim, with
     `<SERVICE_URL>` and `<NATIVE_SR>` substituted)
9. **## Examples** — 3–5 example queries showing realistic uses.
   Each example has a natural-language description and a code block
   calling `get_features(where=..., bbox=...)`. Use the codes from
   the Field Value Dictionaries so the examples are real and
   correct. Include comments showing what the user might want to
   do next with the returned GeoDataFrame.

### Step 9 — Write the SKILL.md to disk and stop

Save the assembled markdown to `_skills_/<skill-name>/SKILL.md`
inside the notebook's `SAGE_OUTPUT_DIR`. Each notebook has its own
`SAGE_OUTPUT_DIR` (named `_<notebook-stem>_sage_/`), so each notebook
gets its own private `_skills_/` scope — two notebooks in the same
directory do NOT share skills.

```python
from pathlib import Path
import os

skill_dir = Path(os.environ["SAGE_OUTPUT_DIR"]) / "_skills_" / "<skill-name>"
skill_dir.mkdir(parents=True, exist_ok=True)
(skill_dir / "SKILL.md").write_text(skill_md_content)
```

Layout — after the write, the filesystem looks like this. The
`_skills_/` folder is INSIDE the notebook's per-notebook output
folder, not next to the notebook itself:

```
<notebook-directory>/
├── my_notebook.ipynb
└── _my_notebook_sage_/           ← SAGE_OUTPUT_DIR
    ├── (per-cell scratch files)
    └── _skills_/                 ← generated skills live here
        └── <skill-name>/
            └── SKILL.md
```

If a skill of the same name already exists at that path, **do not
overwrite without confirmation**. Tell the user there's a conflict and
ask whether to overwrite or pick a new name.

**Do not install the skill into the global skill registry**
(`~/.deepagents/agent/skills/`). Do not copy the directory there, do
not call any internal install helper, do not invoke `%%skill` on the
local path. The freshly-written skill in `_skills_/` is automatically
picked up by the next `%%ask` cell: the agent's skill loader scans
both the global registry AND `<notebook-dir>/_skills_/` on every cell,
so the new skill is available immediately.

Why this matters: the global registry is curated and shared across
notebooks; auto-publishing every generated skill into it would pollute
that space. The user can promote a generated skill into the global
registry deliberately, by issuing an explicit `%%skill _skills_/<name>`
cell when they decide to. Until then, the skill lives in the
notebook's directory where it belongs.

Confirm the build to the user in a message that follows this shape:

1. **One-line confirmation** stating the skill name and where it was saved.
2. **One short sentence naming the entity** in user-natural language —
   the same entity name used in the SKILL.md frontmatter description
   (e.g., "Each row is a California wildfire"). Avoid jargon
   ("perimeter polygon", "feature record", "vector geometry") in this
   sentence — those terms describe the storage format, not the
   thing. Users searching for the thing in natural language won't use
   them.
3. **2–3 example natural-language queries** the user can run in the
   next `%%ask` cell. Use the entity name and synonyms from the
   description. Pick attributes the user is most likely to want to
   filter on — typically status, magnitude/size, source/region, and
   spatial. The queries serve two purposes: they show the user what
   the skill enables, and they demonstrate the vocabulary the agent
   will recognize so the user does not have to guess.

Concrete template, applied to the wildfire example:

```
✓ Built skill 'ca-wildfire-perimeters' at
  _skills_/ca-wildfire-perimeters/SKILL.md.

Each row is a California wildfire. Try queries like:
  - "show me all active California wildfires"
  - "find California wildfires larger than 1000 acres"
  - "list FIRIS-sourced wildfires in Southern California"

The skill is available in the next %%ask cell.
```

Optionally also report counts (fields, categorical dictionaries,
features at build time) as a one-line aside, but do not let those
numbers crowd out the entity name or the example queries. The
queries are the most valuable part of the completion message — they
unblock the user.

## Skill Quality Checklist

Before reporting success, verify your generated SKILL.md:

- [ ] Skill name is lowercase kebab-case (no uppercase, no underscores, no spaces).
- [ ] Frontmatter has both `name` and `description` populated.
- [ ] The description **explicitly names the entity** each row
      represents in plain language ("Each row is a …"), uses 2–3
      user-natural synonyms, and lists the most query-worthy
      attributes. Re-read the description and ask: "if a future user
      typed '<plausible query>', would the LLM match my description?"
      If unsure, the entity name probably isn't crisp enough.
- [ ] The description is under 100 words and contains zero
      implementation detail (no mention of `get_features`, no
      mention of `_skills_/`, no mention of pagination, etc.).
- [ ] At least one example in the Examples section references a real
      categorical code from the Field Value Dictionaries section. (The
      single best test of skill quality is whether a worked example uses
      a real code, not a guessed one.)
- [ ] The `get_features` function is included verbatim from this skill's
      Step 7 — same parameter signature, same pagination loop, same
      `inSR=4326` when bbox is supplied.
- [ ] `SERVICE_URL` constant is set to the actual service URL.
- [ ] Any field meaning you were uncertain about is marked
      `<TODO: confirm meaning>`.

## Worked Example Output

Here is what a correctly-built SKILL.md looks like for a small ArcGIS
service. Use this as the structural model for your output.

### Input

User issues:

```
%%skill-build
https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/Surface_and_Underground_Coal_Mines_in_the_US/FeatureServer/0
```

### Generated `_skills_/coal-mines/SKILL.md`

```markdown
---
name: coal-mines
description: >-
  Each row is a U.S. coal mine (surface or underground). Use when the
  user asks about coal mines, mines, mining operations — by mine name,
  by mine type (surface or underground), by state, by production
  volume, by refuse-site flag, or within a spatial region.
---

# Coal Mines Skill

## Description

This skill retrieves locations and attributes of surface and underground
coal mines in the United States from the U.S. coal mines ArcGIS Feature
Service. Each row is one mine, with Point geometry, MSHA identifier,
mine name, state, county, reported coal production, and a flag for
refuse/waste sites.

Data source: U.S. coal mines Feature Service at
https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/Surface_and_Underground_Coal_Mines_in_the_US/FeatureServer/0

## Service Details

- Geometry type: Point
- Native spatial reference: EPSG:3857 (Web Mercator)
- Output spatial reference: EPSG:4326 (loader handles transformation)
- Maximum record count: 2000

## Fields

| Field | Type | Meaning |
|---|---|---|
| FID | Integer | Internal feature ID used by ArcGIS |
| MSHA_ID | String | Mine's official ID assigned by MSHA |
| MINE_NAME | String | Name of the coal mine |
| MINE_TYPE | String | Type of mine (surface or underground) |
| MINE_STATE | String | State FIPS code |
| state | String | State name |
| FIPS_COUNT | String | County FIPS code |
| MINE_COUNT | String | County name |
| PRODUCTION | Double | Reported coal output |
| PHYSICAL_U | String | Unit for production (e.g., tons) |
| REFUSE | String | Y/N — refuse or waste site indicator |
| Source | String | Data source or provider |
| PERIOD | Integer | Reporting year |
| Longitude | Double | Mine longitude |
| Latitude | Double | Mine latitude |

## Field Value Dictionaries

Use the **code** (left) in WHERE clauses, not the description.

### `MINE_TYPE` codes (2 distinct values)
- `Surface` (4,127 records)
- `Underground` (1,082 records)

### `state` codes (27 distinct values — top 10 shown)
- `Kentucky` (1,847 records)
- `West Virginia` (1,621 records)
- `Pennsylvania` (823 records)
- `Wyoming` (412 records)
- `Indiana` (319 records)
- `Illinois` (287 records)
- `Virginia` (252 records)
- `Ohio` (241 records)
- `Alabama` (188 records)
- `Colorado` (153 records)

### `REFUSE` codes
- `Y`, `N`

### `PHYSICAL_U` codes (1 distinct value)
- `Tons`

## Fields with high cardinality (not enumerated)

These fields have too many distinct values to list. Use `LIKE`
patterns or numeric comparisons instead.

- `MSHA_ID`: unique per mine (identifier)
- `MINE_NAME`: ~5,000 distinct values (free text)
- `MINE_COUNT`: county names; consider filtering by `FIPS_COUNT`
- `Source`, `PERIOD`: use as filters with exact values when known

## How to Use

For any regional query (state, county, river basin, custom area),
**always supply a bbox** in WGS84 — it dramatically reduces wire
traffic. Use the codes from the *Field Value Dictionaries* section in
WHERE clauses, not the human-readable names.

\`\`\`python
import requests
import geopandas as gpd

SERVICE_URL = "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/Surface_and_Underground_Coal_Mines_in_the_US/FeatureServer/0"

def get_features(where="1=1", bbox=None, target_crs="EPSG:4326",
                 page_size=2000, timeout=60, service_url=SERVICE_URL):
    # (full function body as in the canonical template)
    ...
\`\`\`

## Examples

### Example 1: All underground mines in Kentucky

\`\`\`python
ky_bbox = (-89.57, 36.50, -81.96, 39.15)
ky_underground = get_features(
    where="MINE_TYPE = 'Underground' AND state = 'Kentucky'",
    bbox=ky_bbox,
)
print(f"Found {len(ky_underground)} underground mines in Kentucky")
\`\`\`

### Example 2: Surface mines producing more than 5 million tons

\`\`\`python
top_producers = get_features(
    where="MINE_TYPE = 'Surface' AND PRODUCTION > 5000000",
)
print(top_producers[['MINE_NAME', 'state', 'PRODUCTION']]
      .sort_values('PRODUCTION', ascending=False))
\`\`\`

### Example 3: Mines named "River" anywhere in the country

\`\`\`python
river_mines = get_features(where="MINE_NAME LIKE '%River%'")
\`\`\`

### Example 4: Refuse sites only

\`\`\`python
refuse_sites = get_features(where="REFUSE = 'Y'")
print(f"{len(refuse_sites)} refuse sites total")
\`\`\`
```

(In a real generated SKILL.md, the `get_features` function body is the
full template from Step 7, not the `...` placeholder shown above.)

## Notes and Edge Cases

**Layer 0 is not the only layer.** Many ArcGIS services have multiple
layers under a single `FeatureServer`. If the user gives a URL ending in
`/FeatureServer` (without `/0` or another integer), fetch
`<URL>?f=json` (the service-level metadata, not a layer's) — it lists
available layers. Ask the user which layer they want, or pick the first
one if the service has only one.

**Time-enabled layers.** Some services have a `timeInfo` field with
`startTimeField` and `endTimeField`. When present, mention them in the
SKILL.md `## Service Details` section so the agent can reason about
temporal queries.

**Services requiring authentication.** Some services require a
token. The `?f=json` response will include an authentication error if
so. Tell the user the service requires credentials and abort — the
skill builder does not handle authenticated services in v1.

**Already-installed skills.** Before writing to
`_skills_/<skill-name>/SKILL.md`, check whether the path exists. If it
does, ask the user whether to overwrite. The user may have hand-edited
the previous version, and silent overwrite would lose those edits.

**Large services (millions of features).** If the metadata indicates
the layer has many millions of features, mention this in the SKILL.md
Description section so the agent always supplies a `bbox` or a tight
`where`. A full-table query against such services would return data
slowly and waste bandwidth.

**Stale dictionaries.** Code dictionaries reflect the service state at
build time. If new codes are added to the service later, the SKILL.md
becomes stale. Include a `Last built: <date>` note in the generated
SKILL.md and tell the user that re-running `%%skill-build` against the
same URL refreshes the dictionaries.

## Why the canonical loader's pagination matters

Two failure modes are easy to write into a hand-rolled ArcGIS loader,
and both produce silent wrong answers:

1. **Missing `inSR` when bbox is supplied.** The service interprets the
   bbox in its native SR. For a Web Mercator service (very common), a
   WGS84 bbox like `(-118.67, 33.70, -118.16, 34.34)` becomes
   coordinates somewhere off the coast of Africa, and the query returns
   zero features with no error. The canonical loader sets
   `inSR=4326` when a bbox is supplied — always.
2. **Terminating pagination on `len(features) < page_size`.** A page
   that happens to return exactly `page_size` features will leave the
   loop running indefinitely (or terminate too early if the off-by-one
   goes the other way). The correct termination signal is the
   service-published `exceededTransferLimit` boolean. The canonical
   loader uses that.

Always use the canonical loader verbatim. Modifying it regenerates
these bugs.
