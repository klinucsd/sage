#!/usr/bin/env python3
"""GitHub-repository fetcher for repo-skill-builder.

Clones a public GitHub repo into a local scratch directory, classifies every
file in the working tree (array / tabular / docs / other), and prints a ROUTE
line telling the caller which core builder(s) to hand off to. Data lives in
repos as a mix of formats — CSV/GPKG tables next to GeoTIFF/NetCDF/HDF5 grids
next to modelling code — so a repo commonly routes 'combined'.

The fetch step (git clone) is the only repo-specific part. Classification and
routing are the SHARED logic in `fetch_common` (home: ckan-skill-builder/) that
every fetcher uses, so CKAN, Zenodo, NDP, and repo all agree on which core owns
which format. Once the clone finishes, a repo is routed exactly like a download.

Usage:
    python fetch.py <github-url> <out-dir>

<github-url> is any of:
  - https://github.com/<owner>/<repo>
  - https://github.com/<owner>/<repo>.git
  - https://github.com/<owner>/<repo>/tree/<branch>

Writes to <out-dir>/:
  - The cloned working tree (data files stay in place; docs are copied to
    `_docs/`; the code files are classified 'other' and ignored).
  - `_repo_metadata.json` — repo title / README description / license /
    source URL (for the downstream SKILL.md writer).
  - `_classification.json` — per-file class + raw.githubusercontent URL (the
    permanent source the emitted skill re-fetches from for lazy loading).

Never raises SystemExit (the ARGUS KernelShellBackend runs bundled scripts
in-process; a SystemExit would derail the agent). Errors print as `ERROR: ...`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Shared fetcher primitives live in ckan-skill-builder/fetch_common.py — the
# single source of truth for format classification + routing. Import it via the
# sibling-skill path, the same dance zenodo/ndp use to reach ckan. All are core
# skills installed under the same skills root.
_here = Path(__file__).resolve().parent
_ckan_dir = _here.parent / "ckan-skill-builder"
if _ckan_dir.exists() and str(_ckan_dir) not in sys.path:
    sys.path.insert(0, str(_ckan_dir))
try:
    import fetch_common as fc  # noqa: E402
except Exception:  # pragma: no cover - reported cleanly in main()
    fc = None

_GITHUB_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?"
    r"(?:/tree/([^/]+))?/?$"
)

# Directories never worth walking for data.
_SKIP_DIRS = {".git", "_docs", ".github", "node_modules", "__pycache__",
              ".ipynb_checkpoints", ".venv", "venv", ".idea", ".vscode"}

_README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")


def _parse_github_url(url: str):
    m = _GITHUB_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"Not a recognised GitHub repo URL: {url!r}. Expected "
            "https://github.com/<owner>/<repo>[/tree/<branch>].")
    owner, repo, branch = m.group(1), m.group(2), m.group(3)
    return owner, repo, branch


def _clone(url: str, branch: str | None, out_dir: Path) -> None:
    if (out_dir / ".git").exists():
        print(f"  (clone already present at {out_dir}; reusing it)")
        return
    cmd = ["git", "clone", "--depth=1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [url, str(out_dir)]
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            "git clone failed (private repo, bad branch, or network). "
            f"stderr: {r.stderr.strip()[:400]}")


def _current_branch(out_dir: Path, fallback: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(out_dir), "rev-parse",
                            "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True)
        b = r.stdout.strip()
        if b and b != "HEAD":
            return b
    except Exception:
        pass
    return fallback


def _read_readme(out_dir: Path) -> str | None:
    for p in sorted(out_dir.iterdir()):
        if p.is_file() and p.name.lower() in _README_NAMES:
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:8000]
            except Exception:
                return None
    return None


def _license_name(out_dir: Path) -> str | None:
    for p in out_dir.iterdir():
        if p.is_file() and p.name.lower() in ("license", "license.md",
                                              "license.txt", "licence"):
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:200]
                return head.strip().splitlines()[0] if head.strip() else "see LICENSE"
            except Exception:
                return "see LICENSE"
    return None


def main(argv: list[str]) -> None:
    try:
        if fc is None:
            print("ERROR: could not import fetch_common (expected sibling "
                  "skill ckan-skill-builder). Is it installed?")
            return
        if len(argv) != 3:
            print("ERROR: usage: python fetch.py <github-url> <out-dir>")
            return

        url = argv[1]
        out_dir = Path(argv[2])
        owner, repo, branch = _parse_github_url(url)
        clone_url = f"https://github.com/{owner}/{repo}.git"
        out_dir.mkdir(parents=True, exist_ok=True)
        docs_dir = out_dir / "_docs"

        print(f"Repo         : {owner}/{repo}"
              + (f"  (branch {branch})" if branch else ""))
        print(f"Cloning to   : {out_dir}")
        _clone(clone_url, branch, out_dir)
        branch = _current_branch(out_dir, branch or "main")
        raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"

        # Walk the working tree; classify each file via the shared routing.
        print(f"\nClassifying working tree ...")
        entries: list[dict] = []
        for p in sorted(out_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(out_dir)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            if rel.parts and rel.parts[0].startswith("."):
                continue
            key = str(rel)
            source_url = f"{raw_base}/{key}"
            entries += fc.process_local_file(p, key, out_dir, docs_dir,
                                             source_url=source_url)

        # Repo provenance for the downstream SKILL.md writer.
        metadata = {
            "record_id":   f"{owner}/{repo}",
            "title":       repo,
            "description": _read_readme(out_dir),
            "license":     _license_name(out_dir),
            "creators":    [owner],
            "doi":         None,
            "source_url":  f"https://github.com/{owner}/{repo}",
            "branch":      branch,
        }
        (out_dir / "_repo_metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False))
        (out_dir / "_classification.json").write_text(
            json.dumps({"files": entries}, indent=2, default=str))

        fc.report_classification(entries, out_dir, docs_dir)

        n_docs = sum(1 for e in entries
                     if e["class"] == "docs" and "error" not in e)
        print(f"\nOut dir      : {out_dir}")
        print(f"Metadata     : {out_dir / '_repo_metadata.json'}")
        print(f"Classification: {out_dir / '_classification.json'}")
        if n_docs:
            print(f"Docs         : {docs_dir}  ({n_docs} file(s))")

    except ValueError as e:            # bad URL
        print(f"ERROR: {e}")
    except RuntimeError as e:           # clone failed
        print(f"ERROR: {e}")
    except KeyboardInterrupt:
        print("ERROR: interrupted")
    except Exception as e:
        import traceback
        print(f"ERROR: unexpected {type(e).__name__}: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main(sys.argv)
