---
name: s3-skill-builder
description: >-
  Build one or more ARGUS skills from an anonymous-readable S3 prefix
  (AWS Open Data buckets like `s3://noaa-ghcn-pds/`, `s3://openaq-data-archive/`,
  `s3://sagemaker-sample-files/`, and similar). Use when the user
  provides an S3 URL — either `s3://<bucket>/<prefix>` or the
  matching `https://<bucket>.s3.amazonaws.com/<prefix>` form — and
  asks to build, create, or generate skills from the objects under
  that prefix. Fetcher-only: downloads every tabular object under
  the prefix into a local directory (unpacking ZIPs and gunzipping
  single-file `.gz` archives along the way), then hands off to the
  `tabular-skill-builder` skill for the enumerate → propose → build
  pipeline. Two-phase workflow (from tabular-skill-builder): first
  you enumerate the downloaded files and propose a skill plan, then
  STOP for the user's approval; on "yes" in the next %%ask cell you
  continue to build.
---

# S3 Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules define the contract this skill fulfills. Read them
before doing anything else.

1. **You are a fetcher shell.** Your only responsibility is to
   download the objects under an S3 prefix and hand off to
   `tabular-skill-builder`. Do not write an inventory script. Do
   not probe schemas. Do not propose skills yourself. Once
   `fetch.py` finishes, every remaining step in the build is
   `tabular-skill-builder`'s Steps 2 through 9.

2. **Use the bundled `fetch.py`, not a custom script.** The skill
   ships `fetch.py` next to this `SKILL.md`. It handles S3 URL
   parsing (both `s3://` and virtual-hosted HTTPS forms), anonymous
   `ListObjectsV2` with pagination, the tabular format allowlist,
   ZIP unpacking, single-file gunzip, safe local path assembly, and
   region-redirect handling. Do not re-implement it. Do not
   pre-list the bucket yourself — `fetch.py`'s stdout summary is
   what you read to know what happened.

3. **Download to `/tmp/repo-skills/<slug>/`.** Same scratch location
   `ckan-skill-builder` and `repo-skill-builder` use, and that
   `tabular-skill-builder` expects to inventory. Use a slug derived
   from the bucket + prefix (e.g. `noaa-ghcn-pds-1700s` for
   `s3://noaa-ghcn-pds/csv/by_year/17`) — the exact string doesn't
   matter as long as it's a stable directory name.

4. **After the download, explicitly load `tabular-skill-builder`'s
   `SKILL.md` and follow it.** The two skills exist as separate
   files; the handoff is real, not implicit. Step 3 below tells you
   the exact `read_file` call to make.

5. **Public buckets only.** `fetch.py` uses anonymous HTTPS GETs
   against `<bucket>.s3.amazonaws.com`. Authenticated buckets
   (requiring AWS SigV4) will return HTTP 403; report the error
   and stop. Do not attempt to sign requests yourself.

## When to Use

Trigger this skill when the user provides an S3 URL and asks to
build a skill from the objects under it. Example URL shapes:

- `s3://<bucket>/<prefix>` (canonical form)
- `https://<bucket>.s3.amazonaws.com/<prefix>` (virtual-hosted HTTPS)
- `https://s3.amazonaws.com/<bucket>/<prefix>` (path-style HTTPS)
- `https://<bucket>.s3.<region>.amazonaws.com/<prefix>`
  (region-specific virtual-hosted)

Decline (do not use this skill) when:

- The URL is a github.com repo → use `repo-skill-builder`.
- The URL is a CKAN dataset → use `ckan-skill-builder`.
- The URL is an ArcGIS Feature/Map Service → use
  `arcgis-feature-skill-builder`.
- The user gave a bucket URL with no prefix at all — `fetch.py`
  refuses to download an entire bucket for safety. Ask them for
  a specific sub-prefix.

## What You Need From the User

Just the S3 URL. Everything else — object list, sizes, per-object
metadata, region — is captured automatically from the S3 API and
saved to `_s3_metadata.json` in the download directory.

If the user has expressed preferences ("only the CSV files, skip
the JSON manifest", "just the 2020 partition"), pass those forward
in your handoff message so `tabular-skill-builder`'s Step 3
grouping respects them — but do not attempt to filter objects
yourself in this skill; the fetcher takes everything tabular by
design (the format allowlist inside `fetch.py`).

## Steps

### Step 1 — Determine the download-directory slug

Pick a stable local slug from the user's URL. Common patterns:

- `s3://noaa-ghcn-pds/csv/by_year/17` → `noaa-ghcn-pds-1700s`
- `s3://openaq-data-archive/records/csv.gz/locationid=1/` →
  `openaq-locationid-1`
- `s3://sagemaker-sample-files/datasets/tabular/iris/` →
  `sagemaker-iris`

The slug is arbitrary — it's just the directory name under
`/tmp/repo-skills/`. Any short, filesystem-safe string works.

### Step 2 — Run `fetch.py` to download the tabular objects

**Do not pre-list the S3 bucket yourself.** `fetch.py` does one
`ListObjectsV2` request per 1000 objects (pagination handled
internally) plus one download per allowed object; anything you
learn by pre-calling the API is redundant with what the script
already captures into `_s3_metadata.json`.

Under the ARGUS install layout the command is:

```bash
python /home/jovyan/.deepagents/agent/skills/s3-skill-builder/fetch.py \
       <s3-url> \
       /tmp/repo-skills/<slug>
```

The script's stdout ends with a summary block:

```
S3 bucket    : <bucket>
Prefix       : <prefix>
Region       : <region if detectable>
Matched      : <N> object(s)
Downloaded   : <M> tabular object(s) (<X.X> MiB)
Skipped      : <K> object(s)
Out dir      : /tmp/repo-skills/<slug>
Metadata     : /tmp/repo-skills/<slug>/_s3_metadata.json
Skipped list : /tmp/repo-skills/<slug>/_skipped_objects.json  (only if K > 0)

Next: hand off to tabular-skill-builder starting at its Step 2 —
      run its inventory.py on the out dir above.
```

**If `Downloaded` is 0**, stop and tell the user: the prefix has
no objects in a format we can build a skill from. Mention what
was skipped (from `_skipped_objects.json`) so they know why.

**If `Downloaded` is non-zero**, proceed to Step 3.

### Step 3 — Hand off to `tabular-skill-builder`

`tabular-skill-builder`'s `SKILL.md` is a large document with strict
pre-flight rules, a two-phase workflow with a hard-stop between
inventory and build, and per-format loading conventions. You must
load it fully into your context before continuing.

1. **Read `tabular-skill-builder`'s `SKILL.md`:**

   ```
   read_file /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/SKILL.md
   ```

   Substitute the actual skills directory prefix for other runtimes.

2. **Then follow that skill starting at its Step 2** (Enumerate the
   tabular data files). Treat `/tmp/repo-skills/<slug>/` exactly
   as the source directory. Every rule in `tabular-skill-builder`'s
   Pre-Flight and every step from 2 through 9 applies unchanged.
   In particular:

   - Run `tabular-skill-builder`'s bundled `inventory.py` on the
     download directory:

     ```bash
     python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
            /tmp/repo-skills/<slug>
     ```

   - Step 4 is a hard stop: propose the skill plan and end your
     turn. Wait for the user's "yes" in the next `%%ask` cell
     before building.

3. **One S3-specific refinement to `tabular-skill-builder`'s Step 8**
   (Write each SKILL.md): before writing the frontmatter, read
   `/tmp/repo-skills/<slug>/_s3_metadata.json` and use it to source:

   - The `## Data` section's `Source:` bullet — point it at the
     canonical `s3://<bucket>/<prefix>` URL (from `source_url`), so
     users can re-run the fetch or point their own tools at the
     underlying bucket.
   - The `## Description` paragraph — note the bucket name, the
     region (from `_s3_metadata.json`'s `region`), the object count,
     and the total on-disk size. These are the S3 equivalent of
     CKAN's `title` / `organization` fields; a domain scientist
     reading the built SKILL.md should know at a glance where the
     data lives and how much of it there is.
   - If the bucket has a well-known landing page on the AWS Open
     Data Registry (e.g. NOAA GHCN, OpenAQ), mention it in the
     description — but you'll only know this from context, not
     from the metadata sidecar.

**Observation worth encoding into the proposal**: S3 buckets are
typically time-, geography-, or subject-partitioned with **one
schema across the whole prefix**. That means the near-match
reconciliation logic in `tabular-skill-builder`'s Step 3 will
usually collapse the whole download into a single skill. Do not
force multi-skill splits for buckets whose objects genuinely share
one schema — one skill is the right answer for a partitioned
time-series or spatial grid.

### Step 9d — S3-specific cleanup

`tabular-skill-builder`'s Step 9c deletes the download directory
after verification. `_s3_metadata.json` and `_skipped_objects.json`
are inside that directory and go with it — their contents are
already threaded into each built skill's `SKILL.md`, so no
additional cleanup is needed.

## Things to Avoid

- **Do not enumerate objects yourself.** No `aws s3 ls` calls, no
  `curl` against `<bucket>.s3.amazonaws.com/?list-type=2`, no
  per-object `requests.get` loops. `fetch.py` is the single entry
  point.

- **Do not decide which formats to include or exclude.** The
  allowlist inside `fetch.py` is the contract. Non-tabular objects
  (`.pdf`, `.html`, `.grib`, `.nc`, images) are recorded in
  `_skipped_objects.json` for user visibility, not because they
  might be silently useful. Mention the skipped count in your
  handoff message so the user knows about them.

- **Do not short-circuit `tabular-skill-builder`'s Step 4 stop just
  because the prefix has a single obvious schema.** Even the
  "obvious" single-skill S3 case must go through the stop-and-
  propose gate — the user might want a subset, a different name,
  or additional discriminator columns.

- **Do not attempt authentication.** Public buckets only. If
  `fetch.py` gets an HTTP 403 on either the list or a download,
  tell the user the bucket requires AWS credentials and stop.

- **Do not use `SAGE_OUTPUT_DIR` as the download destination.**
  `/tmp/repo-skills/<slug>/` is correct. `SAGE_OUTPUT_DIR` is on a
  small persistent quota and is for skill outputs only, not for
  build scratch.

- **Do not fetch an entire bucket without a prefix.** `fetch.py`
  refuses this for safety. Public S3 buckets can hold terabytes;
  demand a prefix.
