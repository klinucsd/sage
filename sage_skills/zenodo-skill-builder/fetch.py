"""
zenodo-skill-builder fetcher.

Downloads every file in a Zenodo record to a local scratch directory,
unpacks archives, classifies each file as ARRAY / TABULAR / DOCS /
OTHER, and prints a routing recommendation telling the caller which
skill-builder core(s) should own the build.

Usage:
    python fetch.py <zenodo-record-url-or-id> <out-dir>

Contract:
  - This is a FETCHER. It downloads and classifies. It does not
    inventory schemas, does not propose skills, does not write any
    SKILL.md. Those belong to array-skill-builder / tabular-skill-builder.
  - Never raises SystemExit. The ARGUS KernelShellBackend runs bundled
    scripts in-process; a SystemExit escaping would derail the agent's
    downstream tool calls. Errors are reported on stdout as `ERROR: ...`.
  - Writes `_zenodo_metadata.json` (record-level provenance for the
    downstream SKILL.md frontmatter) and `_classification.json`
    (per-file routing decisions) into <out-dir>.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

# --------------------------------------------------------------------------- #
# Format classification
# --------------------------------------------------------------------------- #

# Array/gridded scientific formats -> array-skill-builder
ARRAY_EXTS = {
    ".h5", ".hdf5", ".he5",           # HDF5 family
    ".nc", ".nc4", ".cdf",            # NetCDF
    ".zarr",                          # Zarr (directory, but may appear zipped)
}

# Tabular / geospatial-vector formats -> tabular-skill-builder.
# NOTE: `.txt` and `.dat` are deliberately NOT here. In general a `.txt`
# file is documentation, not tabular data — treating it as tabular is the
# exceptional case, not the default. Those extensions go through a content
# sniff (SNIFF_EXTS below) and are reclassified to tabular ONLY when their
# content is genuinely delimited.
TABULAR_EXTS = {
    ".csv", ".tsv",                   # delimited text (unambiguous by ext)
    ".xlsx", ".xls",                  # Excel
    ".parquet",                       # columnar
    ".gpkg", ".geojson", ".shp",      # spatial vector
    ".rdata", ".rda", ".rds",         # R-serialized
}

# Ambiguous text extensions: default to documentation (read for metadata),
# but content-sniff each one — if it is genuinely delimited tabular data,
# reclassify to tabular. See `_looks_tabular`.
SNIFF_EXTS = {".txt", ".dat"}

# Documentation -> read for semantics, never built into a skill directly.
DOC_EXTS = {".pdf", ".md", ".rst", ".doc", ".docx", ".html", ".htm"}

# Archives get unpacked and their contents re-classified.
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}

# Filename stems that mark a file as documentation regardless of extension.
# Deliberately narrow: a `.txt` in a scientific record is far more often
# data than prose, so only an explicit documentation-ish name flips it.
DOC_NAME_HINTS = ("readme", "license", "licence", "citation", "changelog",
                  "manual", "documentation", "data_description",
                  "datadescription", "user_guide", "userguide", "codebook")

_UNSAFE_FS_CHARS = set('/\\:*?"<>|')
_ZENODO_RECORD_RE = re.compile(
    r"^https?://(?:www\.)?(?:sandbox\.)?zenodo\.org/(?:records?|record)/(\d+)"
)


class FetchError(RuntimeError):
    """Fetch cannot proceed. Caught in `main`, reported on stdout."""


def classify(filename: str) -> str:
    """Return one of 'array', 'tabular', 'docs', 'sniff', 'archive', 'other'.

    'sniff' means an ambiguous text file whose final class (tabular vs docs)
    can only be decided by looking at its content — resolved after download
    by `_looks_tabular`.
    """
    name = filename.rsplit("/", 1)[-1]
    lower = name.lower()
    stem, ext = os.path.splitext(lower)

    # `.tar.gz` / `.tar.bz2` — look at the compound extension
    if stem.endswith(".tar") and ext in ARCHIVE_EXTS:
        return "archive"

    # Explicit documentation names win over extension, so a `README.txt`
    # is prose (docs) without needing a content sniff.
    if any(h in stem for h in DOC_NAME_HINTS):
        return "docs"

    if ext in ARRAY_EXTS:
        return "array"
    if ext in DOC_EXTS:
        return "docs"
    if ext in TABULAR_EXTS:
        return "tabular"
    if ext in SNIFF_EXTS:
        return "sniff"
    if ext in ARCHIVE_EXTS:
        return "archive"
    return "other"


def _looks_tabular(path: Path, max_lines: int = 40) -> str | None:
    """Content heuristic: does this text file look like delimited tabular data?

    Returns the detected delimiter token ('\\t', ',', ';', '|', or
    'whitespace') if the file has a consistent multi-column structure, else
    None. The general rule is that a `.txt` is NOT tabular — only a file whose
    lines split consistently into 2+ columns qualifies. Deliberately strict:
    prose and README-style text return None and stay documentation.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for line in f:
                s = line.rstrip("\n")
                if s.strip():
                    lines.append(s)
                if len(lines) >= max_lines:
                    break
    except Exception:
        return None
    if len(lines) < 3:
        return None

    # Explicit single-char delimiters, priority tab > semicolon > pipe > comma.
    for delim in ("\t", ";", "|", ","):
        counts = [ln.count(delim) for ln in lines]
        if min(counts) >= 1:                       # every line has >=2 columns
            modal, n = Counter(counts).most_common(1)[0]
            if n >= 0.8 * len(lines):              # consistent column count
                return delim

    # Whitespace-delimited fallback (runs of spaces/tabs), e.g. fixed-width
    # scientific dumps. Require 2+ columns consistently.
    field_counts = [len(re.split(r"\s+", ln.strip())) for ln in lines]
    modal, n = Counter(field_counts).most_common(1)[0]
    if modal >= 2 and n >= 0.8 * len(lines):
        return "whitespace"
    return None


def _finalize_sniff(path: Path, docs_dir: Path):
    """Resolve a 'sniff' file's final class from its content.

    Returns (final_class, path). A genuinely tabular file stays where it is
    and becomes 'tabular'; anything else is moved into `_docs/` so downstream
    still reads it for metadata, and becomes 'docs'.
    """
    if _looks_tabular(path) is not None:
        return "tabular", path
    docs_dir.mkdir(parents=True, exist_ok=True)
    moved = docs_dir / path.name
    try:
        path.replace(moved)
        return "docs", moved
    except Exception:
        return "docs", path


def safe_name(filename: str) -> str:
    """Strip path separators and unsafe characters from a Zenodo filename."""
    base = filename.rsplit("/", 1)[-1].strip() or "unnamed"
    return "".join("_" if c in _UNSAFE_FS_CHARS else c for c in base)


# --------------------------------------------------------------------------- #
# Zenodo API
# --------------------------------------------------------------------------- #

def record_id_from(url_or_id: str) -> str:
    s = url_or_id.strip()
    if s.isdigit():
        return s
    m = _ZENODO_RECORD_RE.match(s)
    if m:
        return m.group(1)
    # Tolerate a DOI form: 10.5281/zenodo.<id>
    m = re.search(r"zenodo\.(\d+)", s)
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


def download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": "zenodo-skill-builder/0.1"})
    size = 0
    with urllib.request.urlopen(req, timeout=600) as r, dest.open("wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)
    return size


# --------------------------------------------------------------------------- #
# Archive unpacking
# --------------------------------------------------------------------------- #

def _safe_members_zip(zf: zipfile.ZipFile, dest: Path):
    for info in zf.infolist():
        p = (dest / info.filename).resolve()
        if str(p).startswith(str(dest.resolve())):
            yield info


def _safe_members_tar(tf: tarfile.TarFile, dest: Path):
    for m in tf.getmembers():
        p = (dest / m.name).resolve()
        if str(p).startswith(str(dest.resolve())) and (m.isfile() or m.isdir()):
            yield m


def unpack(path: Path, dest: Path) -> list[Path]:
    """Extract an archive into `dest`; return the list of extracted files."""
    dest.mkdir(parents=True, exist_ok=True)
    before = {p for p in dest.rglob("*") if p.is_file()}
    lower = path.name.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                zf.extractall(dest, members=list(_safe_members_zip(zf, dest)))
        elif any(lower.endswith(s) for s in
                 (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            with tarfile.open(path) as tf:
                tf.extractall(dest, members=list(_safe_members_tar(tf, dest)))
        else:
            return []
    except Exception as e:
        print(f"    ! unpack failed: {type(e).__name__}: {e}")
        return []
    after = {p for p in dest.rglob("*") if p.is_file()}
    return sorted(after - before)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

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
        kind = classify(key)
        # 'sniff' and 'docs' both download to a neutral spot first; a sniff
        # that turns out non-tabular is moved into _docs/ afterward.
        target_dir = docs_dir if kind == "docs" else out_dir
        dest = target_dir / safe_name(key)
        try:
            size = download(url, dest)
        except Exception as e:
            print(f"  ! {key}: {type(e).__name__}: {e}")
            entries.append({"filename": key, "class": kind, "url": url,
                            "error": f"{type(e).__name__}: {e}"})
            continue

        # Resolve ambiguous text files by content. `.txt` is documentation by
        # default; only genuinely delimited content becomes tabular.
        sniffed = None
        if kind == "sniff":
            kind, dest = _finalize_sniff(dest, docs_dir)
            sniffed = _looks_tabular(dest) if kind == "tabular" else "not-tabular"

        note = f"  (delimiter: {sniffed!r})" if sniffed and kind == "tabular" else ""
        print(f"  {kind:8s} {key}  ({size/1024:.0f} KB){note}")
        entries.append({
            "filename": key, "class": kind, "url": url,
            "local_path": str(dest), "size_bytes": size,
            **({"sniffed_delimiter": sniffed} if sniffed else {}),
        })

        if kind == "archive":
            extracted = unpack(dest, out_dir)
            if extracted:
                print(f"    unpacked {len(extracted)} file(s)")
            for ex in extracted:
                sub = classify(ex.name)
                if sub == "sniff":
                    sub, ex = _finalize_sniff(ex, docs_dir)
                elif sub == "docs":
                    docs_dir.mkdir(parents=True, exist_ok=True)
                    moved = docs_dir / ex.name
                    try:
                        ex.replace(moved)
                        ex = moved
                    except Exception:
                        pass
                entries.append({
                    "filename": ex.name if sub == "docs"
                                else str(ex.relative_to(out_dir)),
                    "class": sub,
                    "from_archive": key,
                    "local_path": str(ex),
                    "size_bytes": ex.stat().st_size if ex.exists() else None,
                })
            # The archive itself is a container, not data.
            try:
                dest.unlink()
            except Exception:
                pass

    # ----------------------------------------------------------------- #
    # Classification tallies + routing recommendation
    # ----------------------------------------------------------------- #
    def of(kind):
        return [e for e in entries if e["class"] == kind and "error" not in e]

    arrays, tabulars = of("array"), of("tabular")
    docs, others = of("docs"), of("other")

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

    if arrays and tabulars:
        route = "combined"
    elif arrays:
        route = "array"
    elif tabulars:
        route = "tabular"
    else:
        route = "none"

    print()
    print("Classification")
    print(f"  array   : {len(arrays)} file(s)  -> array-skill-builder")
    print(f"  tabular : {len(tabulars)} file(s)  -> tabular-skill-builder")
    print(f"  docs    : {len(docs)} file(s)  -> read for semantics (_docs/)")
    if others:
        print(f"  other   : {len(others)} file(s)  -> ignored "
              f"({', '.join(e['filename'] for e in others[:4])}"
              f"{' ...' if len(others) > 4 else ''})")
    errs = [e for e in entries if "error" in e]
    if errs:
        print(f"  errors  : {len(errs)} file(s) failed to download")

    print()
    print(f"ROUTE: {route}")
    if route == "array":
        print("  Hand off to array-skill-builder. Run its inventory.py with")
        print(f"  --dir {out_dir}")
    elif route == "tabular":
        print("  Hand off to tabular-skill-builder. Run its inventory.py on")
        print(f"  {out_dir}")
    elif route == "combined":
        print("  Record holds BOTH array and tabular data. Run BOTH")
        print("  inventories, then propose at ONE gate. Default to a single")
        print("  combined skill when the files share an index/grid/key —")
        print("  see the SKILL.md 'combined' branch.")
    else:
        print("  Nothing buildable — no array or tabular files in this record.")
        print("  Tell the user what was found and stop.")

    print(f"\nOut dir       : {out_dir}")
    print(f"Metadata      : {out_dir / '_zenodo_metadata.json'}")
    print(f"Classification: {out_dir / '_classification.json'}")
    if docs:
        print(f"Docs          : {docs_dir}  ({len(docs)} file(s))")
    return 0


def main(argv: list[str]) -> None:
    """Entry point. Never raises SystemExit."""
    try:
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
