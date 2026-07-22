---
name: ndp-skill-builder
description: >-
  Build one or more ARGUS skills from a National Data Platform (NDP)
  catalog dataset (nationaldataplatform.org). Use when the user
  provides an NDP URL — either the canonical `/dataset/<slug>` form
  seen when browsing nationaldataplatform.org, or the underlying CKAN
  native `/catalog/dataset/<slug>` or
  `/catalog/api/3/action/package_show?id=<slug>` form. Fetcher-only:
  rewrites the NDP URL to the equivalent CKAN API URL and delegates
  the download, classification, and routing to `ckan-skill-builder`
  in-process — which routes to `tabular-skill-builder` (CSV/Excel/…),
  `array-skill-builder` (HDF5/NetCDF), or both. Whichever downstream
  builder runs stops for the user's approval before building.
---

# NDP Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules define the contract this skill fulfills. Read them
before doing anything else.

1. **You are a fetcher shell.** Your only responsibility is to run
   `ndp-skill-builder`'s `fetch.py`, which rewrites the NDP URL and
   delegates to `ckan-skill-builder` for the download + classification.
   Do not write an inventory script. Do not probe schemas. Do not
   propose skills yourself. Once `fetch.py` finishes, follow the
   `ROUTE:` line it prints (see Step 2).

2. **Use the bundled `fetch.py`, not a custom script.** The skill
   ships `fetch.py` next to this `SKILL.md`. It rewrites any NDP
   URL form to the CKAN API URL, then imports and calls
   `ckan-skill-builder`'s `fetch.py` in-process. Do not
   re-implement it. Do not call the CKAN API yourself first.

3. **Download to `/tmp/ndp-skills/<slug>/`.** Pod-local scratch,
   never `SAGE_OUTPUT_DIR` or `~/work/` (10 GB quota; a `.tar.gz` of
   HDF5 can overwhelm it). The slug comes from the NDP URL.

4. **The `ROUTE:` line decides the handoff.** `fetch.py`'s output
   is identical to `ckan-skill-builder`'s (it runs it under the hood),
   ending in a `ROUTE:` line. Follow `ckan-skill-builder`'s **Step 3**
   route branches (array / tabular / combined) — read its `SKILL.md`.

5. **STOP for user approval before building — no exceptions.**
   Whichever downstream builder runs, its proposal gate applies:
   propose and END YOUR TURN; build only after the user replies
   "yes". Delegating through a chain of fetchers does not consume
   that gate.

## When to Use

Trigger this skill when the user provides an NDP URL and asks to
build a skill from it. Example URL shapes:

- `https://nationaldataplatform.org/dataset/<slug>` (canonical,
  user-facing Next.js frontend — the URL a user copies from the
  browser bar)
- `https://nationaldataplatform.org/catalog/dataset/<slug>` (CKAN
  native browse — the underlying page)
- `https://nationaldataplatform.org/catalog/api/3/action/package_show?id=<slug>`
  (already the CKAN API URL)

Decline (do not use this skill) when:

- The URL is not on `nationaldataplatform.org` — use
  `ckan-skill-builder` for other CKAN portals (data.cnra.ca.gov,
  data.gov, etc.), `repo-skill-builder` for GitHub repos,
  `s3-skill-builder` for S3 prefixes, or
  `arcgis-feature-skill-builder` for ArcGIS services.

## What You Need From the User

Just the NDP URL. `fetch.py` figures out which URL form was given
and produces the CKAN API URL. All downstream metadata (title,
tags, license, per-resource description) comes from CKAN's
`package_show` response as usual, and is written to
`_ckan_metadata.json` by `ckan-skill-builder` under the hood
(unchanged).

If the user has expressed preferences ("only the CSV files, skip
the metadata worksheet", "just the tree tables"), pass those
forward in your handoff message so `tabular-skill-builder`'s
Step 3 grouping respects them — but do not attempt to filter
resources yourself in this skill; the fetcher takes everything
tabular by design.

## Steps

### Step 1 — Run `fetch.py` to rewrite the URL and download

**Do not attempt to rewrite the URL yourself first**, and do not
call the CKAN API yourself first. `fetch.py` does both in one
invocation.

Under the ARGUS install layout the command is:

```bash
python /home/jovyan/.deepagents/agent/skills/ndp-skill-builder/fetch.py \
       <ndp-url> \
       /tmp/ndp-skills/<slug>
```

Substitute the actual skills directory prefix for other runtimes.

The script's stdout starts with two lines documenting the rewrite,
then the standard `ckan-skill-builder` output — the per-resource
download + classification lines and a `ROUTE:` line:

```
NDP URL       : <the URL you gave>
CKAN API URL  : https://nationaldataplatform.org/catalog/api/3/action/package_show?id=<slug>

Classification
  array   : <N> file(s)  -> array-skill-builder
  tabular : <N> file(s)  -> tabular-skill-builder
  docs    : <N> file(s)  -> read for semantics (_docs/)

ROUTE: <array | tabular | combined | raster | none>
```

### Step 2 — Branch on the `ROUTE:` line

Because `ndp-skill-builder`'s `fetch.py` runs `ckan-skill-builder`'s
`fetch.py` under the hood, the output — the staged directory,
`_ckan_metadata.json`, `_classification.json`, `_docs/`, and the
`ROUTE:` line — is identical to running the CKAN fetcher directly.
**Follow `ckan-skill-builder`'s Step 3 (Branch on the `ROUTE:` line)
exactly:**

```
read_file /home/jovyan/.deepagents/agent/skills/ckan-skill-builder/SKILL.md
```

Then apply its `ROUTE: tabular` / `ROUTE: array` / `ROUTE: combined`
branch to the `/tmp/ndp-skills/<slug>/` directory. Every rule there —
including the downstream hard stop — applies unchanged.

## Things to Avoid

- **Do not implement URL rewriting yourself.** `fetch.py`'s
  `_rewrite_ndp_url` handles all three NDP URL shapes correctly.
- **Do not call the CKAN API yourself.** `ndp-skill-builder`'s
  `fetch.py` calls `ckan-skill-builder`'s `fetch.py` internally.
- **Do not use `SAGE_OUTPUT_DIR` as the download destination.**
  `/tmp/ndp-skills/<slug>/` is correct.
- **Do not skip the downstream hard stop.** Even simple NDP datasets
  go through the propose-and-approve gate.
