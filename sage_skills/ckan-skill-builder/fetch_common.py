"""
Shared fetcher primitives — format taxonomy, classification, content sniff,
archive unpacking, per-resource download, and route selection.

This is the SINGLE SOURCE OF TRUTH for "which core builder handles which data
format." All fetcher shells (ckan-skill-builder, zenodo-skill-builder, and —
via ckan — ndp-skill-builder) share it so that adding a new scientific data
format is a one-place edit, not an edit repeated across every fetcher.

Home: ckan-skill-builder/ (the foundational fetcher; ndp delegates to it).
Imported directly by ckan-skill-builder/fetch.py, and via the sibling-skill
path pattern by zenodo-skill-builder/fetch.py (see that file's import dance).

Adding a format later:
  - A format the array/tabular core can ALREADY read  -> add its extension to
    ARRAY_EXTS or TABULAR_EXTS. Done.
  - A format no core reader supports yet -> add it to a new *_EXTS set that
    classify() maps to a distinct class the router reports as "not yet
    supported", so a record of that format gets an honest refusal rather than
    a crash inside the wrong reader. Promote it once the core gains a reader.

GeoTIFF followed exactly that path: it began life in RASTER_EXTS as an honest
refusal, and once array-skill-builder gained a rasterio reader (a single-band
GeoTIFF is a 2D georeferenced array; multi-band is band x y x x), its
extensions moved into the array family below. RASTER_EXTS is kept as a named
subset so the classification report can still say "of which N GeoTIFF".
"""

from __future__ import annotations

import os
import re
import tarfile
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path


# --------------------------------------------------------------------------- #
# Format taxonomy
# --------------------------------------------------------------------------- #

# Array / gridded scientific formats -> array-skill-builder.
# array-skill-builder reads HDF5 via h5py and NetCDF (both NetCDF-3 classic
# and NetCDF-4) via xarray + the netCDF4 backend. Zarr stores are directories
# and usually arrive zipped (unpacked by the archive handling below).
ARRAY_EXTS = {
    ".h5", ".hdf5", ".he5",           # HDF5 family (h5py)
    ".nc", ".nc4", ".cdf",            # NetCDF-3 + NetCDF-4 (xarray/netCDF4)
    ".zarr",                          # Zarr
}

# Raster / GeoTIFF formats. array-skill-builder now reads these via rasterio
# (inventory._walk_raster), so they are part of the array family: classify()
# maps them to 'array' and they route to array-skill-builder like HDF5/NetCDF.
# Kept as a named subset so the classification report can distinguish them and
# so the array inventory can pick the rasterio reader by extension.
RASTER_EXTS = {
    ".tif", ".tiff", ".geotiff",      # GeoTIFF (rasterio/GDAL)
    ".img", ".grd",                   # other GDAL rasters
}

# Tabular / geospatial-vector formats -> tabular-skill-builder.
# `.txt` / `.dat` are deliberately NOT here: a text file is documentation by
# default and only becomes tabular when a content sniff proves it delimited.
TABULAR_EXTS = {
    ".csv", ".tsv",                   # delimited text (unambiguous by ext)
    ".xlsx", ".xls",                  # Excel
    ".parquet",                       # columnar
    ".gpkg", ".geojson", ".shp",      # spatial vector
    ".rdata", ".rda", ".rds",         # R-serialized
}

# Ambiguous text extensions: documentation by default, reclassified to tabular
# only when the content sniff (looks_tabular) finds a consistent delimited grid.
SNIFF_EXTS = {".txt", ".dat"}

# Documentation -> read for semantics, never built into a skill directly.
# Includes the SQLite manifests (Oceans11 file manifests) that LANL/NDP array
# datasets ship: structured per-file descriptions the array builder reads.
DOC_EXTS = {".pdf", ".md", ".rst", ".doc", ".docx", ".html", ".htm",
            ".db", ".sqlite", ".sqlite3"}

# Archives get unpacked and their contents re-classified.
ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}

# Filename stems that mark a file as documentation regardless of extension.
# Narrow on purpose: a `.txt` in a scientific record is more often data than
# prose, so only an explicit documentation-ish name flips it without a sniff.
DOC_NAME_HINTS = ("readme", "license", "licence", "citation", "changelog",
                  "manual", "documentation", "data_description",
                  "datadescription", "user_guide", "userguide", "codebook")

_UNSAFE_FS_CHARS = set('/\\:*?"<>|')

# Build-phase download ceiling. The fetcher downloads whole files to /tmp for
# the build; multi-GB scientific HDF5 (e.g. a 28.6 GB InSAR time-series) would
# fill pod-local disk and take too long. Files larger than this are recorded as
# 'too-large' and skipped — the honest signal that building them needs
# metadata-only remote reads (HTTP Range via h5py ros3/fsspec, design-doc
# §11.4), which array-skill-builder does not do yet.
MAX_DOWNLOAD_BYTES = 5 * 1024 ** 3   # 5 GiB

# Classes the router treats as "buildable data".
_ARRAY = "array"
_TABULAR = "tabular"
_DOCS = "docs"
_RASTER = "raster"
_ARCHIVE = "archive"
_SNIFF = "sniff"
_OTHER = "other"


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def classify(filename: str) -> str:
    """Return one of: array, tabular, docs, sniff, archive, other.

    'sniff' is an ambiguous text file resolved after download by looks_tabular.
    GeoTIFF / GDAL rasters map to 'array' (array-skill-builder reads them via
    rasterio) — see RASTER_EXTS.
    """
    name = filename.rsplit("/", 1)[-1]
    lower = name.lower()
    stem, ext = os.path.splitext(lower)

    # `.tar.gz` / `.tar.bz2` — compound extension
    if stem.endswith(".tar") and ext in ARCHIVE_EXTS:
        return _ARCHIVE

    # Explicit documentation names win over extension (README.txt is prose).
    if any(h in stem for h in DOC_NAME_HINTS):
        return _DOCS

    if ext in ARRAY_EXTS or ext in RASTER_EXTS:
        return _ARRAY
    if ext in DOC_EXTS:
        return _DOCS
    if ext in TABULAR_EXTS:
        return _TABULAR
    if ext in SNIFF_EXTS:
        return _SNIFF
    if ext in ARCHIVE_EXTS:
        return _ARCHIVE
    return _OTHER


def looks_tabular(path: Path, max_lines: int = 40):
    """Content heuristic for ambiguous `.txt` / `.dat` files.

    Returns the detected delimiter token ('\\t', ',', ';', '|', 'whitespace')
    if the file has a consistent multi-column structure, else None. Strict:
    prose / README-style text returns None and stays documentation.
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
    for delim in ("\t", ";", "|", ","):
        counts = [ln.count(delim) for ln in lines]
        if min(counts) >= 1:
            modal, n = Counter(counts).most_common(1)[0]
            if n >= 0.8 * len(lines):
                return delim
    field_counts = [len(re.split(r"\s+", ln.strip())) for ln in lines]
    modal, n = Counter(field_counts).most_common(1)[0]
    if modal >= 2 and n >= 0.8 * len(lines):
        return "whitespace"
    return None


def finalize_sniff(path: Path, docs_dir: Path):
    """Resolve a 'sniff' file's final class from content.

    Returns (final_class, path). Genuinely tabular stays put and becomes
    'tabular'; anything else is moved into `_docs/` and becomes 'docs'.
    """
    if looks_tabular(path) is not None:
        return _TABULAR, path
    docs_dir.mkdir(parents=True, exist_ok=True)
    moved = docs_dir / path.name
    try:
        path.replace(moved)
        return _DOCS, moved
    except Exception:
        return _DOCS, path


def safe_name(filename: str) -> str:
    """Strip path separators and unsafe characters from a source filename."""
    base = filename.rsplit("/", 1)[-1].strip() or "unnamed"
    return "".join("_" if c in _UNSAFE_FS_CHARS else c for c in base)


# --------------------------------------------------------------------------- #
# Download + archive unpacking
# --------------------------------------------------------------------------- #

def download(url: str, dest: Path, user_agent: str = "argus-fetcher/0.1",
             opener=None) -> int:
    """Download `url` to `dest`. If `opener` (a urllib OpenerDirector) is
    given, use it instead of the module default — CKAN portals backed by S3
    need a redirect handler that strips default ports to avoid 403s."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    _open = opener.open if opener is not None else urllib.request.urlopen
    size = 0
    with _open(req, timeout=600) as r, dest.open("wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)
    return size


def probe_size(url, user_agent="argus-fetcher/0.1", opener=None):
    """Return the resource's total byte size via a Range probe, or None.

    Uses `Range: bytes=0-0` and reads the total from Content-Range
    (`bytes 0-0/<total>`). Falls back to Content-Length on a non-partial
    response. Returns None if the size can't be determined (the caller then
    downloads normally, since we can't prove it's oversized)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Range": "bytes=0-0"})
    _open = opener.open if opener is not None else urllib.request.urlopen
    try:
        with _open(req, timeout=60) as r:
            cr = r.headers.get("Content-Range")
            if cr and "/" in cr:
                tail = cr.rsplit("/", 1)[-1].strip()
                if tail.isdigit():
                    return int(tail)
            cl = r.headers.get("Content-Length")
            if cl and cl.isdigit() and getattr(r, "status", None) != 206:
                return int(cl)
    except Exception:
        return None
    return None


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


def unpack(path: Path, dest: Path):
    """Extract a .zip or .tar[.gz/.bz2/.xz] archive into `dest`.

    Returns the list of extracted files (path-traversal entries are dropped).
    Returns [] for an unrecognised or corrupt archive.
    """
    dest.mkdir(parents=True, exist_ok=True)
    before = {p for p in dest.rglob("*") if p.is_file()}
    lower = path.name.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                zf.extractall(dest, members=list(_safe_members_zip(zf, dest)))
        elif (lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2",
                              ".tar.xz")) or lower.endswith(".gz")
              and ".tar" in lower):
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
# Per-resource processing (download -> classify -> sniff -> unpack)
# --------------------------------------------------------------------------- #

def process_resource(url, key, out_dir: Path, docs_dir: Path,
                     user_agent="argus-fetcher/0.1", from_archive=None,
                     opener=None):
    """Download one resource and return a list of classified entry dicts.

    Handles the full pipeline for a single URL:
      - classify by name, download to the right place
      - resolve 'sniff' text files by content (tabular vs docs)
      - unpack archives and recursively classify their contents

    `opener` is forwarded to download() for CKAN's S3 port-stripping.
    Each entry dict: {filename, class, url?, local_path, size_bytes,
    from_archive?, sniffed_delimiter?, error?}.
    """
    entries = []
    kind = classify(key)

    # Build-phase size guard: refuse to download files above the cap. Building
    # them needs metadata-only remote reads (§11.4), not a full /tmp download.
    total = probe_size(url, user_agent, opener=opener)
    if total is not None and total > MAX_DOWNLOAD_BYTES:
        gb = total / 1024 ** 3
        cap = MAX_DOWNLOAD_BYTES / 1024 ** 3
        print(f"  {'too-large':22s} {key}  ({gb:.1f} GB > {cap:.0f} GB cap) "
              f"-> skipped")
        entries.append({"filename": key, "class": "too-large",
                        "intended_class": kind, "url": url,
                        "size_bytes": total})
        return entries

    target_dir = docs_dir if kind == _DOCS else out_dir
    dest = target_dir / safe_name(key)
    try:
        size = download(url, dest, user_agent, opener=opener)
    except Exception as e:
        print(f"  ! {key}: {type(e).__name__}: {e}")
        entries.append({"filename": key, "class": kind, "url": url,
                        "error": f"{type(e).__name__}: {e}"})
        return entries

    sniffed = None
    if kind == _SNIFF:
        kind, dest = finalize_sniff(dest, docs_dir)
        sniffed = looks_tabular(dest) if kind == _TABULAR else "not-tabular"

    note = f"  (delimiter: {sniffed!r})" if sniffed and kind == _TABULAR else ""
    label = f"{kind}" if not from_archive else f"{kind} (from {from_archive})"
    print(f"  {label:22s} {key}  ({size/1024:.0f} KB){note}")
    entry = {"filename": key, "class": kind, "url": url,
             "local_path": str(dest), "size_bytes": size}
    if from_archive:
        entry["from_archive"] = from_archive
    if sniffed:
        entry["sniffed_delimiter"] = sniffed
    entries.append(entry)

    if kind == _ARCHIVE:
        extracted = unpack(dest, out_dir)
        if extracted:
            print(f"    unpacked {len(extracted)} file(s)")
        for ex in extracted:
            sub = classify(ex.name)
            if sub == _SNIFF:
                sub, ex = finalize_sniff(ex, docs_dir)
            elif sub == _DOCS:
                docs_dir.mkdir(parents=True, exist_ok=True)
                moved = docs_dir / ex.name
                try:
                    ex.replace(moved)
                    ex = moved
                except Exception:
                    pass
            entries.append({
                "filename": ex.name if sub == _DOCS
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

    return entries


def process_local_file(local_path, key, out_dir: Path, docs_dir: Path,
                       source_url=None):
    """Classify an ALREADY-LOCAL file the same way process_resource classifies
    a freshly downloaded one — minus the download.

    This is the shared post-fetch routing for fetchers whose fetch step
    produces files on disk directly (a git clone, an S3 sync, an extracted
    archive) rather than a list of URLs to pull. The fetch differs per source;
    the classify -> sniff -> docs -> route steps are identical, and live here.

    `key` is the display name / source-relative path (drives classify()).
    `source_url` is the permanent remote URL the emitted skill can re-fetch
    from (e.g. a raw.githubusercontent.com URL); it is threaded into the entry
    so the downstream inventory can wire a lazy-download loader.
    Returns a list of entry dicts (an archive expands to several).
    """
    src = Path(local_path)
    if not src.is_file():
        return [{"filename": key, "class": _OTHER, "url": source_url,
                 "error": "local file missing"}]
    kind = classify(key)
    size = src.stat().st_size

    # Archives: unpack in place and reclassify the contents (same as the
    # download path's archive handling).
    if kind == _ARCHIVE:
        entries = [{"filename": key, "class": _ARCHIVE, "url": source_url,
                    "local_path": str(src), "size_bytes": size}]
        for ex in unpack(src, out_dir):
            sub = classify(ex.name)
            if sub == _SNIFF:
                sub, ex = finalize_sniff(ex, docs_dir)
            elif sub == _DOCS:
                docs_dir.mkdir(parents=True, exist_ok=True)
                moved = docs_dir / ex.name
                try:
                    ex.replace(moved); ex = moved
                except Exception:
                    pass
            entries.append({
                "filename": ex.name if sub == _DOCS
                            else str(ex.relative_to(out_dir)),
                "class": sub, "from_archive": key, "local_path": str(ex),
                "size_bytes": ex.stat().st_size if ex.exists() else None})
        return entries

    dest, sniffed = src, None
    if kind == _SNIFF:
        kind, dest = finalize_sniff(src, docs_dir)
        sniffed = looks_tabular(dest) if kind == _TABULAR else "not-tabular"
    elif kind == _DOCS:
        # Copy docs into _docs/ so the core builders' semantics step finds
        # them; leave the clone otherwise intact. Disambiguate name clashes
        # across subdirs by falling back to the flattened relative path.
        docs_dir.mkdir(parents=True, exist_ok=True)
        target = docs_dir / src.name
        try:
            if target.exists() and target.resolve() != src.resolve():
                target = docs_dir / str(
                    src.relative_to(out_dir)).replace("/", "__")
            import shutil
            shutil.copy2(src, target)
            dest = target
        except Exception:
            dest = src

    note = f"  (delimiter: {sniffed!r})" if sniffed and kind == _TABULAR else ""
    print(f"  {kind:22s} {key}  ({size/1024:.0f} KB){note}")
    entry = {"filename": key, "class": kind, "url": source_url,
             "local_path": str(dest), "size_bytes": size}
    if sniffed:
        entry["sniffed_delimiter"] = sniffed
    return [entry]


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

def route(entries) -> str:
    """Pick the downstream route from classified entries.

    Returns one of: 'array', 'tabular', 'combined', 'too-large', 'none'.
    Rasters classify as 'array', so a raster-only record routes 'array' and a
    raster+CSV record routes 'combined'.
    """
    def has(kind):
        return any(e["class"] == kind and "error" not in e for e in entries)
    arrays, tabulars = has(_ARRAY), has(_TABULAR)
    if arrays and tabulars:
        return "combined"
    if arrays:
        return "array"
    if tabulars:
        return "tabular"
    if has("too-large"):
        return "too-large"
    return "none"


def report_classification(entries, out_dir: Path, docs_dir: Path,
                          array_inv_out=None) -> str:
    """Print the classification tally + ROUTE line; return the route string.

    Both fetchers call this after downloading so their stdout carries an
    identical, machine-readable ROUTE line the SKILL.md branches on.
    """
    def of(kind):
        return [e for e in entries if e["class"] == kind and "error" not in e]
    arrays, tabulars = of(_ARRAY), of(_TABULAR)
    docs, others = of(_DOCS), of(_OTHER)
    toolarge = of("too-large")
    errs = [e for e in entries if "error" in e]
    the_route = route(entries)

    def _is_raster(e):
        return os.path.splitext(e["filename"].lower())[1] in RASTER_EXTS
    n_raster = sum(1 for e in arrays if _is_raster(e))

    print()
    print("Classification")
    raster_note = (f"  (of which {n_raster} GeoTIFF/raster)"
                   if n_raster else "")
    print(f"  array   : {len(arrays)} file(s)  -> array-skill-builder"
          f"{raster_note}")
    print(f"  tabular : {len(tabulars)} file(s)  -> tabular-skill-builder")
    print(f"  docs    : {len(docs)} file(s)  -> read for semantics (_docs/)")
    if toolarge:
        cap = MAX_DOWNLOAD_BYTES / 1024 ** 3
        print(f"  too-large: {len(toolarge)} file(s)  -> exceeds the {cap:.0f} GB "
              f"build-phase download cap")
        for e in toolarge[:3]:
            gb = (e.get("size_bytes") or 0) / 1024 ** 3
            print(f"      {e['filename']}  ({gb:.1f} GB, would be "
                  f"{e.get('intended_class')})")
    if others:
        shown = ", ".join(e["filename"] for e in others[:4])
        print(f"  other   : {len(others)} file(s)  -> ignored ({shown}"
              f"{' ...' if len(others) > 4 else ''})")
    if errs:
        print(f"  errors  : {len(errs)} file(s) failed to download")

    print()
    print(f"ROUTE: {the_route}")
    if the_route == "array":
        print("  Hand off to array-skill-builder. Run its inventory.py with")
        print(f"  --dir {out_dir}")
    elif the_route == "tabular":
        print("  Hand off to tabular-skill-builder. Run its inventory.py on")
        print(f"  {out_dir}")
    elif the_route == "combined":
        print("  Record holds BOTH array and tabular data. Run BOTH")
        print("  inventories, then propose at ONE gate. Default to a single")
        print("  combined skill when the files share an index/grid/key.")
    elif the_route == "too-large":
        print("  The array file(s) exceed the build-phase download cap.")
        print("  Building large remote HDF5 needs metadata-only Range reads,")
        print("  which array-skill-builder does not do yet — tell the user")
        print("  the file size and stop.")
    else:
        print("  Nothing buildable — no array or tabular files here.")
        print("  Tell the user what was found and stop.")
    return the_route
