#!/usr/bin/env python3
"""Local-path fetcher for local-skill-builder.

Classifies an already-local directory (or a single local file) IN PLACE --- no
download, no copy, no move --- and prints a ROUTE line telling the caller which
core builder(s) to hand off to. The data stays exactly where it is, so this
works for arbitrarily large local datasets that must not be duplicated (the
whole point of building from a local path rather than re-fetching a source).

Acquisition is a no-op here; the classify -> route step is the SHARED
`fetch_common` logic every fetcher uses. This is repo-skill-builder without the
clone: nothing is staged, and the emitted skill BUNDLES its data because a local
file has no remote URL to lazily re-fetch from.

Usage:
    python fetch.py <local-path> [<scratch-dir>]

<scratch-dir> defaults to /tmp/local-skills/<name>/ and holds only small
sidecars (`_classification.json`, `_local_metadata.json`); the data is never
written there.

Never raises SystemExit (the ARGUS KernelShellBackend runs bundled scripts
in-process). Errors print as `ERROR: ...`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Shared fetcher primitives live in ckan-skill-builder/fetch_common.py --- the
# single source of truth for format classification + routing. Import it via the
# sibling-skill path, the same dance repo/zenodo/ndp use.
_here = Path(__file__).resolve().parent
_ckan_dir = _here.parent / "ckan-skill-builder"
if _ckan_dir.exists() and str(_ckan_dir) not in sys.path:
    sys.path.insert(0, str(_ckan_dir))
try:
    import fetch_common as fc  # noqa: E402
except Exception:  # pragma: no cover - reported cleanly in main()
    fc = None

_SKIP_DIRS = {".git", "__pycache__", ".ipynb_checkpoints", ".venv", "venv",
              ".idea", ".vscode", "node_modules", ".github"}
_DOC_NAME_HINTS = ("readme", "license", "licence", "citation", "changelog",
                   "data_dictionary", "datadictionary", "codebook",
                   "user_guide", "userguide")


def _is_top_level_doc(rel: Path) -> bool:
    """A documentation file the agent should read for semantics: at the top
    level of the source, and either a doc extension or a doc-ish name."""
    if len(rel.parts) != 1:
        return False
    n = rel.name.lower()
    return (n.endswith((".md", ".rst", ".pdf", ".txt"))
            or any(h in n for h in _DOC_NAME_HINTS))


def main(argv: list[str]) -> None:
    try:
        if fc is None:
            print("ERROR: could not import fetch_common (expected sibling "
                  "skill ckan-skill-builder). Is it installed?")
            return
        if len(argv) < 2:
            print("ERROR: usage: python fetch.py <local-path> [<scratch-dir>]")
            return

        src = Path(argv[1]).expanduser().resolve()
        if not src.exists():
            print(f"ERROR: path does not exist: {src}")
            return

        scratch = (Path(argv[2]).expanduser().resolve() if len(argv) > 2
                   else Path("/tmp/local-skills") / src.name)
        scratch.mkdir(parents=True, exist_ok=True)

        # Enumerate files in place. A single file is its own one-item list;
        # a directory is walked, skipping VCS/cache/hidden dirs.
        if src.is_file():
            files = [src]
            base = src.parent
        else:
            base = src
            files = []
            for p in sorted(src.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(base)
                if any(part in _SKIP_DIRS or part.startswith(".")
                       for part in rel.parts):
                    continue
                files.append(p)

        print(f"Local source : {src}")
        print("(read in place --- data is NOT copied or moved)")
        print(f"\nClassifying {len(files)} file(s) ...")

        entries: list[dict] = []
        docs: list[Path] = []
        for p in files:
            rel = p.relative_to(base)
            kind = fc.classify(p.name)
            # No remote URL for a local file: url=None signals the core to
            # BUNDLE the data rather than wire a lazy download.
            entries.append({"filename": str(rel), "class": kind, "url": None,
                            "local_path": str(p),
                            "size_bytes": p.stat().st_size})
            if _is_top_level_doc(rel):
                docs.append(p)

        # Provenance from the folder itself + its README (there is no catalog
        # record). record_id / title default to the folder name.
        readme = next((p for p in files
                       if p.name.lower().startswith("readme")), None)
        description = None
        if readme is not None:
            try:
                description = readme.read_text(encoding="utf-8",
                                               errors="replace")[:8000]
            except Exception:
                pass
        metadata = {
            "record_id":   src.name,
            "title":       src.name,
            "description": description,
            "license":     None,
            "creators":    [],
            "doi":         None,
            "source_url":  f"file://{src}",
            "source_kind": "local-path",
        }
        (scratch / "_local_metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False))
        (scratch / "_classification.json").write_text(
            json.dumps({"files": entries}, indent=2, default=str))

        # Shared routing: prints the classification tally + ROUTE line. Pass
        # `src` as the out-dir so the hand-off text names the real --dir.
        the_route = fc.report_classification(entries, src, scratch / "_docs")

        # For a local source the documentation lives at the top level, not in a
        # fetcher-staged _docs/, so point the agent at the actual files.
        if docs:
            print("\nDocumentation (read these IN PLACE for semantics):")
            for d in docs:
                print(f"  {d}")
        else:
            print("\nNo top-level README / documentation found in the folder.")

        print(f"\nData dir (--dir): {src}")
        print(f"Scratch/meta   : {scratch}")
        print("The emitted skill must BUNDLE its data: a local source has no "
              "remote URL to lazily re-fetch from.")

    except KeyboardInterrupt:
        print("ERROR: interrupted")
    except Exception as e:
        import traceback
        print(f"ERROR: unexpected {type(e).__name__}: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main(sys.argv)
