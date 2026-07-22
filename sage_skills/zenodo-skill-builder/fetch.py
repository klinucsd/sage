"""
zenodo-skill-builder fetcher.

Downloads every file in a Zenodo record to a local scratch directory, unpacks
archives, classifies each file as ARRAY / TABULAR / DOCS / OTHER, and prints a
routing recommendation telling the caller which core builder(s) should own the
build.

Format classification, content sniffing, archive unpacking, and routing all
live in the shared `fetch_common` module (home: ckan-skill-builder/) so every
fetcher agrees on which core handles which format. This file holds only the
Zenodo-API-specific parts: record-id resolution and the record metadata call.

Usage:
    python fetch.py <zenodo-record-url-or-id> <out-dir>

Contract:
  - This is a FETCHER. It downloads and classifies. It does not inventory
    schemas, propose skills, or write any SKILL.md.
  - Never raises SystemExit (the ARGUS KernelShellBackend runs bundled scripts
    in-process). Errors are reported on stdout as `ERROR: ...`.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Shared fetcher primitives live in ckan-skill-builder/fetch_common.py — the
# single source of truth for format classification + routing. Import it via the
# sibling-skill path, the same dance ndp-skill-builder uses to reach ckan. Both
# skills are core skills, always installed under the same skills root.
_here = Path(__file__).resolve().parent
_ckan_dir = _here.parent / "ckan-skill-builder"
if _ckan_dir.exists() and str(_ckan_dir) not in sys.path:
    sys.path.insert(0, str(_ckan_dir))
try:
    import fetch_common as fc  # noqa: E402
except Exception:  # pragma: no cover - reported cleanly in main()
    fc = None


_ZENODO_RECORD_RE = re.compile(
    r"^https?://(?:www\.)?(?:sandbox\.)?zenodo\.org/(?:records?|record)/(\d+)"
)


class FetchError(RuntimeError):
    """Fetch cannot proceed. Caught in `main`, reported on stdout."""


def record_id_from(url_or_id: str) -> str:
    s = url_or_id.strip()
    if s.isdigit():
        return s
    m = _ZENODO_RECORD_RE.match(s)
    if m:
        return m.group(1)
    m = re.search(r"zenodo\.(\d+)", s)   # tolerate a 10.5281/zenodo.<id> DOI
    if m:
        return m.group(1)
    raise FetchError(
        f"not a recognisable Zenodo record reference: {url_or_id!r}\n"
        f"       expected https://zenodo.org/records/<id>, a bare <id>, "
        f"or a 10.5281/zenodo.<id> DOI"
    )


def fetch_record(record_id: str) -> dict:
    api = f"https://zenodo.org/api/records/{record_id}"
    req = urllib.request.Request(
        api,
        headers={"User-Agent": "zenodo-skill-builder/0.1",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FetchError(
            f"Zenodo API returned HTTP {e.code} for record {record_id}. "
            f"Check the record exists and is public (restricted and embargoed "
            f"records are not accessible without a token)."
        )
    except Exception as e:
        raise FetchError(f"could not reach the Zenodo API: {type(e).__name__}: {e}")


def run(url_or_id: str, out_dir: Path) -> int:
    record_id = record_id_from(url_or_id)
    data = fetch_record(record_id)
    meta = data.get("metadata", {})

    out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = out_dir / "_docs"

    title = meta.get("title") or "(untitled)"
    print(f"Zenodo record : {record_id} — {title[:90]}")
    creators = [c.get("name") for c in meta.get("creators", []) if c.get("name")]
    if creators:
        print(f"Creators      : {'; '.join(creators[:4])}"
              + (" ..." if len(creators) > 4 else ""))
    lic = meta.get("license")
    lic_id = lic.get("id") if isinstance(lic, dict) else lic
    if lic_id:
        print(f"License       : {lic_id}")

    files = data.get("files", [])
    if not files:
        print("\nERROR: record lists no files. Nothing to build.")
        return 1

    print(f"\nDownloading {len(files)} file(s) to {out_dir} ...")
    entries: list[dict] = []
    for f in files:
        key = f.get("key") or f.get("filename") or ""
        links = f.get("links") or {}
        url = links.get("self") or links.get("download")
        if not url:
            entries.append({"filename": key, "class": "other",
                            "error": "no download link in record metadata"})
            continue
        entries += fc.process_resource(url, key, out_dir, docs_dir,
                                       user_agent="zenodo-skill-builder/0.1")

    (out_dir / "_zenodo_metadata.json").write_text(json.dumps({
        "record_id":    record_id,
        "source_url":   f"https://zenodo.org/records/{record_id}",
        "doi":          meta.get("doi"),
        "title":        meta.get("title"),
        "description":  meta.get("description"),
        "creators":     creators,
        "license":      lic_id,
        "publication_date": meta.get("publication_date"),
        "keywords":     meta.get("keywords"),
        "version":      meta.get("version"),
    }, indent=2, default=str))
    (out_dir / "_classification.json").write_text(
        json.dumps({"files": entries}, indent=2, default=str))

    fc.report_classification(entries, out_dir, docs_dir)

    n_docs = sum(1 for e in entries if e["class"] == "docs" and "error" not in e)
    print(f"\nOut dir       : {out_dir}")
    print(f"Metadata      : {out_dir / '_zenodo_metadata.json'}")
    print(f"Classification: {out_dir / '_classification.json'}")
    if n_docs:
        print(f"Docs          : {docs_dir}  ({n_docs} file(s))")
    return 0


def main(argv: list[str]) -> None:
    """Entry point. Never raises SystemExit."""
    try:
        if fc is None:
            print("ERROR: fetch_common not found next to zenodo-skill-builder. "
                  "Ensure ckan-skill-builder is installed under the same skills "
                  "directory (it hosts the shared fetch_common module).")
            return
        if len(argv) < 2:
            print("ERROR: usage: python fetch.py <zenodo-record-url-or-id> "
                  "<out-dir>")
            return
        run(argv[0], Path(argv[1]))
    except FetchError as e:
        print(f"ERROR: {e}")
    except KeyboardInterrupt:
        print("ERROR: interrupted")
    except Exception as e:
        import traceback
        print(f"ERROR: unexpected {type(e).__name__}: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main(sys.argv[1:])
