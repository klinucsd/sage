---
name: ndp-skill-builder
description: >-
  Build one or more ARGUS skills from a National Data Platform (NDP)
  catalog dataset (nationaldataplatform.org). Use when the user
  provides an NDP URL — either the canonical `/dataset/<slug>` form
  seen when browsing nationaldataplatform.org, or the underlying CKAN
  native `/catalog/dataset/<slug>` or
  `/catalog/api/3/action/package_show?id=<slug>` form. Fetcher-only:
  rewrites the NDP URL to the equivalent CKAN API URL, delegates the
  download and metadata capture to `ckan-skill-builder` in-process,
  and hands off to `tabular-skill-builder` for the enumerate → propose
  → build pipeline. Two-phase workflow (from tabular-skill-builder):
  first you enumerate the downloaded files and propose a skill plan,
  then STOP for the user's approval; on "yes" in the next %%ask cell
  you continue to build.
---

# NDP Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules define the contract this skill fulfills. Read them
before doing anything else.

1. **You are a fetcher shell.** Your only responsibility is to run
   `ndp-skill-builder`'s `fetch.py`, which rewrites the NDP URL and
   delegates to `ckan-skill-builder` for the actual download. Do
   not write an inventory script. Do not probe schemas. Do not
   propose skills yourself. Once `fetch.py` finishes, follow the
   handoff printed in its summary line to `tabular-skill-builder`
   Steps 2 through 9.

2. **Use the bundled `fetch.py`, not a custom script.** The skill
   ships `fetch.py` next to this `SKILL.md`. It rewrites any NDP
   URL form to the CKAN API URL, then imports and calls
   `ckan-skill-builder`'s `fetch.py` in-process. Do not
   re-implement it. Do not call the CKAN API yourself first.

3. **Download to `/tmp/repo-skills/<slug>/`.** Same scratch location
   `ckan-skill-builder` and every other fetcher use. The slug can
   be derived directly from the NDP URL's dataset slug — the exact
   string doesn't matter as long as it's a stable directory name.

4. **After the download, explicitly load `tabular-skill-builder`'s
   `SKILL.md` and follow it.** `fetch.py`'s stdout summary ends
   with the standard "Next: hand off to tabular-skill-builder…"
   line inherited from `ckan-skill-builder`. Follow it verbatim.

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
       /tmp/repo-skills/<slug>
```

Substitute the actual skills directory prefix for other runtimes.

The script's stdout starts with two lines documenting the rewrite:

```
NDP URL       : <the URL you gave>
CKAN API URL  : https://nationaldataplatform.org/catalog/api/3/action/package_show?id=<slug>
(delegating to ckan-skill-builder for the download)
```

…then the standard `ckan-skill-builder` output (per-resource
`downloading` lines and the summary block ending with the
"Next: hand off to tabular-skill-builder starting at its Step 2 —
run its inventory.py on the out dir above." handoff line).

**If `Downloaded` is 0**, stop and tell the user: the dataset
has no resources in a format we can build a skill from. Mention
what was skipped (from `_skipped_resources.json`) so they know
why.

**If `Downloaded` is non-zero**, proceed to Step 2.

### Step 2 — Hand off to `tabular-skill-builder`

Same handoff as every other fetcher — inherited unchanged, since
this fetcher delegates in-process to `ckan-skill-builder`. Follow
the summary's instruction:

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

3. **The `_ckan_metadata.json` sidecar** written by
   `ckan-skill-builder` under the hood still holds the dataset
   title, organization, source URL, and per-resource metadata.
   Thread those into each generated SKILL.md's frontmatter and
   `## Data` section exactly as documented in `ckan-skill-builder`'s
   Step 8 refinement — the sidecar's format and semantics are
   identical to the CalSim / waterfowl cases.

## Things to Avoid

- **Do not implement URL rewriting yourself.** `fetch.py`'s
  `_rewrite_ndp_url` handles all three NDP URL shapes correctly.
  If you're tempted to string-manipulate the URL from the agent
  loop, you're bypassing the tested rewrite logic — don't.
- **Do not call the CKAN API yourself.** `ndp-skill-builder`'s
  `fetch.py` imports and calls `ckan-skill-builder`'s `fetch.py`
  internally; there is nothing for you to add.
- **Do not use `SAGE_OUTPUT_DIR` as the download destination.**
  `/tmp/repo-skills/<slug>/` is correct.
- **Do not short-circuit `tabular-skill-builder`'s Step 4 stop.**
  Even simple NDP datasets go through the propose-and-approve gate
  — the user might want a subset, a different name, or additional
  discriminator columns.
