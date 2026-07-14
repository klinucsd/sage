---
name: repo-skill-builder
description: >-
  Build one or more ARGUS skills from a GitHub repository containing
  tabular, geospatial, or R-serialized data files (CSV, TSV, Excel,
  Parquet, GeoPackage, GeoJSON, Shapefile, RData / rda / rds). Use
  when the user provides a github.com URL and asks to build, create,
  or generate skills from the data in the repo. Fetcher-only: clones
  the repository into a local scratch directory, then hands off to
  the `tabular-skill-builder` skill for the enumerate → propose →
  build pipeline. Two-phase workflow (from tabular-skill-builder):
  first you enumerate the cloned files and propose a skill plan,
  then STOP for the user's approval; on "yes" in the next %%ask cell
  you continue to build.
---

# Repo Skill Builder

## ⚠️ MANDATORY PRE-FLIGHT RULES — read these first

These rules define the contract this skill fulfills. Read them
before doing anything else.

1. **You are a fetcher shell.** Your only responsibility is to
   clone the repository and hand off to `tabular-skill-builder`.
   Do not write an inventory script. Do not probe schemas. Do not
   propose skills yourself. Once `git clone` finishes, every
   remaining step in the build is `tabular-skill-builder`'s
   Steps 2 through 9.

2. **Clone with `git clone --depth=1`. No custom wrapper.** For
   GitHub, `git clone` is the entire fetch step — there is no API
   metadata call to make, no resource filtering to apply, no archive
   unpacking to do. Do not write a Python wrapper around it. Do not
   authenticate; if the clone fails (private repo, network error,
   404), report the error and stop.

3. **Clone to `/tmp/repo-skills/<repo-name>/`.** Same scratch
   location `ckan-skill-builder` downloads into, and that
   `tabular-skill-builder` expects to inventory. Never clone to
   `SAGE_OUTPUT_DIR` or anywhere under `~/work/` — those are on a
   persistent volume with a ~10 GB shared quota, and the clone is
   throwaway scratch that /tmp handles correctly.

4. **After the clone, explicitly load `tabular-skill-builder`'s
   `SKILL.md` and follow it.** The two skills exist as separate
   files; the handoff is real, not implicit. Step 2 below tells
   you the exact `read_file` call to make.

## When to Use

Trigger this skill when the user provides a `github.com` URL and
asks to build a skill from the data in the repo. Example URL shapes:

- `https://github.com/<owner>/<repo>`
- `https://github.com/<owner>/<repo>.git`
- `https://github.com/<owner>/<repo>/tree/<branch>` (branch/tag
  ignored — clone always takes HEAD of the default branch)

Decline (do not use this skill) when:

- The URL is a CKAN dataset (`/api/3/action/package_show?id=...` or
  `/dataset/<slug>`) — use `ckan-skill-builder`.
- The URL is an ArcGIS Feature / Map Service — use
  `arcgis-feature-skill-builder`.
- The repo contains only code with no tabular data — there's nothing
  to build a queryable skill against.
- The user has already named specific files and just wants them
  loaded into a notebook for ad-hoc analysis — that's a plain
  `%%ask` task, not a skill-build.

## What You Need From the User

Just the GitHub URL. The user does not need to specify the skill
name, the file format, or anything else. `tabular-skill-builder`
will inspect the clone and propose a sensible plan.

If the user has expressed preferences (e.g. "merge them all into
one skill" or "split by region"), pass those forward in your
handoff message so `tabular-skill-builder`'s Step 3 grouping
respects them — but do not attempt to filter files yourself in
this skill; the clone always brings everything.

## Steps

### Step 1 — Clone the repository

```bash
mkdir -p /tmp/repo-skills
cd /tmp/repo-skills
# --depth=1 skips history; we just need the latest snapshot.
git clone --depth=1 https://github.com/<owner>/<repo>.git <repo-name>
```

If the clone fails (private repo, network error, 404), report the
error to the user and stop. Do not try to authenticate.

If the repo is huge (>500 MB), **stop and ask the user** before
downloading — a quick `git ls-remote` or GitHub API call to
estimate size is fine.

### Step 2 — Hand off to `tabular-skill-builder`

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
   tabular data files). Treat `/tmp/repo-skills/<repo-name>/`
   exactly as the source directory. Every rule in
   `tabular-skill-builder`'s Pre-Flight and every step from 2
   through 9 applies unchanged. In particular:

   - Run `tabular-skill-builder`'s bundled `inventory.py` on the
     clone:

     ```bash
     python /home/jovyan/.deepagents/agent/skills/tabular-skill-builder/inventory.py \
            /tmp/repo-skills/<repo-name>
     ```

   - Step 4 is a hard stop: propose the skill plan and end your
     turn. Wait for the user's "yes" in the next `%%ask` cell before
     building.

3. **When writing the generated SKILL.md's frontmatter** (in
   `tabular-skill-builder`'s Step 8), source the `description` and
   `## Data` section from the repo's top-level `README.md` (if
   present) plus the GitHub URL itself. Point the "Source:" bullet
   at the `https://github.com/<owner>/<repo>` URL — that's the
   canonical provenance link the user can trace back through. If
   the repo's README names a paper, dataset title, or license, thread
   those into the built SKILL.md's Description paragraph so the
   provenance survives.

## Things to Avoid

- **Do not enumerate the repo yourself.** No `ls`-then-analyze,
  no manual `find` walks, no per-file `head` inspections.
  `tabular-skill-builder`'s `inventory.py` is the single entry
  point.

- **Do not short-circuit `tabular-skill-builder`'s Step 4 stop just
  because you already read the README.** README hints feed the
  generated SKILL.md's description in Step 8; they do not replace
  schema-fingerprint grouping and user confirmation of the skill
  plan.

- **Do not attempt authentication.** Public repos only. If
  `git clone` gets a 403, 404, or credentials prompt, report to the
  user and stop.

- **Do not use `SAGE_OUTPUT_DIR` as the clone destination.**
  `/tmp/repo-skills/<repo-name>/` is correct. `SAGE_OUTPUT_DIR` is
  on a small persistent quota and is for skill outputs only, not
  for build scratch.

- **Do not write your own `inventory.py` here.** The canonical copy
  lives at `tabular-skill-builder/inventory.py` and Step 2 above
  invokes it directly. `repo-skill-builder/` no longer ships an
  inventory script (as of the 2026-07-14 refactor into a fetcher
  shell — see [[project_fetcher_core_split]]).
