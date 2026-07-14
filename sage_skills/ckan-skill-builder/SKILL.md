---
name: ckan-skill-builder
description: >-
  Build one or more ARGUS skills from a CKAN dataset (data.gov,
  data.cnra.ca.gov, and other CKAN-based open-data portals). Use when
  the user provides a CKAN dataset URL — either the
  `/api/3/action/package_show?id=<slug>` API URL or the
  `/dataset/<slug>` browse URL — and asks to build, create, or
  generate skills from it. Fetcher-only: downloads the dataset's
  tabular resources into a local directory, then hands off to the
  `tabular-skill-builder` skill for the enumerate → propose → build
  pipeline. Two-phase workflow (from tabular-skill-builder): first you
  enumerate the downloaded files and propose a skill plan, then STOP
  for the user's approval; on "yes" in the next %%ask cell you
  continue to build.
---

# CKAN Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules define the contract this skill fulfills. Read them
before doing anything else.

1. **You are a fetcher shell.** Your only responsibility is to
   download the dataset's tabular resources and hand off to
   `tabular-skill-builder`. Do not write an inventory script. Do not
   probe schemas. Do not propose skills yourself. Once `fetch.py`
   finishes, every remaining step in the build is
   `tabular-skill-builder`'s Steps 2 through 9.

2. **Use the bundled `fetch.py`, not a custom script.** The skill
   ships `fetch.py` next to this `SKILL.md`. It handles CKAN URL
   resolution (API vs browse URL), format allowlist, ZIP unpacking,
   safe filename picking, dataset-level metadata capture, and a
   per-resource skipped-list. Do not re-implement it. Do not call
   the CKAN API yourself first "to peek at the resources" —
   `fetch.py` does exactly one metadata call plus one download
   call per allowed resource, and its stdout summary is what you
   read to know what happened.

3. **Download to `/tmp/repo-skills/<dataset-slug>/`.** This is the
   same scratch location `repo-skill-builder` clones repos into and
   that `tabular-skill-builder` expects to inventory.
   Sharing the path lets the handoff step invoke
   `tabular-skill-builder`'s `inventory.py` on the download directory
   with no additional glue.

4. **After the download, explicitly load `tabular-skill-builder`'s
   `SKILL.md` and follow it.** The two skills exist as separate
   files; the handoff is real, not implicit. Step 3 below tells you
   the exact `read_file` call to make.

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
- The dataset has no tabular resources (only PDFs, HTML pages,
  raster imagery, or documentation) — `fetch.py` will exit with
  zero downloaded resources; tell the user there is nothing
  queryable to build here.

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

### Step 2 — Run `fetch.py` to download the tabular resources

**Do not read the CKAN API yourself first.** `fetch.py` does one
metadata GET plus one download per allowed resource; anything you
learn by pre-calling the API is redundant with what the script
already captures into `_ckan_metadata.json`.

Under the ARGUS install layout the command is:

```bash
python /home/jovyan/.deepagents/agent/skills/ckan-skill-builder/fetch.py \
       <ckan-url> \
       /tmp/repo-skills/<dataset-slug>
```

If you read this `SKILL.md` from a different runtime (Claude Code,
Codex), substitute the actual skills directory for the prefix.

The script's stdout ends with a summary block:

```
CKAN dataset : '<title>'
Slug         : <slug>
Downloaded   : <N> tabular resource(s)
Skipped      : <M> resource(s)
Out dir      : /tmp/repo-skills/<dataset-slug>
Metadata     : /tmp/repo-skills/<dataset-slug>/_ckan_metadata.json
Skipped list : /tmp/repo-skills/<dataset-slug>/_skipped_resources.json  (only if M > 0)

Next: hand off to tabular-skill-builder starting at its Step 2 —
      run its inventory.py on the out dir above.
```

**If `Downloaded` is 0**, stop and tell the user: the dataset has
no resources in a format we can build a skill from. Mention what
was skipped (from `_skipped_resources.json`) so they know why.

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
   tabular data files). Treat `/tmp/repo-skills/<dataset-slug>/`
   exactly as if it were a cloned GitHub repo. Every rule in
   `tabular-skill-builder`'s Pre-Flight and every step from 2 through
   9 applies unchanged. In particular:

   - Run `tabular-skill-builder`'s bundled `inventory.py` on the
     download directory:

     ```bash
     python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
            /tmp/repo-skills/<dataset-slug>
     ```

   - Step 4 is a hard stop: propose the skill plan and end your
     turn. Wait for the user's "yes" in the next `%%ask` cell before
     building.

3. **One CKAN-specific refinement to `tabular-skill-builder`'s Step 8**
   (Write each SKILL.md): before writing the frontmatter, read
   `/tmp/repo-skills/<dataset-slug>/_ckan_metadata.json` and use it
   to source:

   - The `description` field's plain-English framing (from `title`
     and `notes`).
   - The synonyms / query-worthy attributes (from `tags` and the
     per-resource `description` fields).
   - The `## Data` section's `Source:` bullet — point it at
     `_ckan_metadata.json`'s `source_url` (the CKAN dataset landing
     page), not the raw resource download URL. This lets the user
     trace back to the catalog page for license and provenance.
   - The `## Description` paragraph — include the dataset's
     `license_title` and `organization` so the built skill carries
     provenance.

### Step 9d — CKAN-specific cleanup

`tabular-skill-builder`'s Step 9c deletes the download directory
after verification. `_ckan_metadata.json` and
`_skipped_resources.json` are inside that directory and go with it
— their contents are already threaded into each built skill's
`SKILL.md`, so no additional cleanup is needed.

## Things to Avoid

- **Do not enumerate resources yourself.** No manual CKAN API
  calls, no `curl <resource url>` loop, no per-resource
  `python -c "requests.get(...)"`. `fetch.py` is the single entry
  point.

- **Do not decide which formats to include or exclude.** The
  allowlist inside `fetch.py` is the contract. Non-tabular
  resources (PDF, HTML, JPG, KML, NetCDF, GeoTIFF, etc.) are
  recorded in `_skipped_resources.json` for user visibility, not
  because they might be silently useful. Mention the skipped count
  in your handoff message so the user knows about them.

- **Do not short-circuit `tabular-skill-builder`'s Step 4 stop just
  because CKAN gave you a title and description.** Those hints
  feed the generated SKILL.md's description in Step 8; they do
  not replace schema-fingerprint grouping and user confirmation
  of the skill plan.

- **Do not attempt CKAN authentication.** Public datasets only.
  If `fetch.py` gets an HTTP 401/403 the download error is
  recorded in `_skipped_resources.json`; tell the user the
  resource is behind auth and stop.

- **Do not use `SAGE_OUTPUT_DIR` as the download destination.**
  `/tmp/repo-skills/<slug>/` is correct. `SAGE_OUTPUT_DIR` is on a
  small persistent quota and is for skill outputs only, not for
  build scratch.
