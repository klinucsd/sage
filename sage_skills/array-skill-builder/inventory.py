"""
array-skill-builder inventory.

Given one or more HDF5 URLs (or a Zenodo record URL), fetch each file's
metadata (via HTTP Range through h5py's ros3/fsspec, or by a small local
download when Range isn't available), compute a schema fingerprint per
file, group same-fingerprint files, infer a partition axis from
filenames, sample per-numeric-dataset stats for data-quality caveats,
and emit:

  - stdout: compact human/agent-readable summary
  - <out-dir>/_inventory.json: structured record of everything

Usage:
  python inventory.py --url <hdf5-or-zenodo-url> [--url ...] --out <dir>
  python inventory.py --zenodo-record 3660832                --out <dir>

Contract:
  - Never downloads the array payload — file bytes are limited to a
    small local copy per file (bounded by --max-file-mb, default 200)
    because ros3 driver availability varies across environments. For
    v0 we accept the small-file trade-off; the fingerprint pass runs on
    what we actually download.
  - **Documentation sidecars are collected too.** HDF5 datasets rarely
    carry sufficient semantic metadata on their own; the SKILL.md
    builder needs the record's README / PDF / text siblings to
    author physical-quantity meanings, dimension units, and Caveats.
    Zenodo / CKAN records almost always ship these alongside the data
    files; the inventory downloads and lists them for the builder.
  - Output JSON schema is groups-first, mirroring tabular-skill-builder.
  - Stdout summary is bounded (<8 KB target) — the caller reads it, not
    the JSON, when writing the Phase-1 proposal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Unit-suffix regexes for dataset-name normalisation (Igor Pro dialect).
# Igor writes names like `Outdoor_Temperature___C_`, `Wind_Speed__km_h_`,
# `Outdoor_Humidity____` (bare underscore tail when unit is empty), and
# `NO_ppb_`. Two dialects observed in the ATLASM5 case study — with and
# without the unit tail. Normalisation collapses both to a common form.
#
# The unit tail is recognised by the pattern
#     __ + [unit body] + _
# where the double-underscore separator is Igor's own convention. `_UNIT_TAIL_BODY_RE`
# matches everything from the last double-underscore onward, provided the
# body is a valid unit-shaped token (letters/digits/underscores). The
# `_BARE_TAIL_RE` afterward trims stray trailing underscores.
_UNIT_TAIL_BODY_RE = re.compile(r"^_+[a-zA-Z][a-zA-Z0-9_]*_*$")
_BARE_TAIL_RE     = re.compile(r"_+$")

# Datasets that carry Igor Pro export metadata, not measurements. We keep
# them in the fingerprint (so files sharing this administrative shape
# still group correctly) but skip them for statistics.
_IGOR_ADMIN_NAMES = frozenset({
    "S_fileName", "S_path", "S_waveNames", "V_Flag",
})

# Time-axis dataset name hints. Igor Pro writes `dateW`; NetCDF/CF uses
# `time`; xarray/pandas typically write `timestamp` or similar.
_TIME_AXIS_HINTS = ("datew", "time", "timestamp", "date")

# Filename-regex partition-axis heuristics. Applied in order; first hit
# wins. Each entry: (regex, axis-label, key-extractor).
_MONTH_ABBR = "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
_PARTITION_HEURISTICS: list[tuple[re.Pattern, str, "callable"]] = [
    (re.compile(rf"[_\- ]({_MONTH_ABBR})[a-z]*[_\- ]?(\d{{4}})", re.I),
     "month",
     lambda m: f"{m.group(1)[:3].capitalize()}-{m.group(2)}"),
    (re.compile(r"(\d{4})[-_](0[1-9]|1[0-2])(?:[-_](\d{2}))?"),
     "date",
     lambda m: m.group(0)),
    (re.compile(r"[_\- ](site[_\-]?[A-Za-z0-9]+)", re.I),
     "site",
     lambda m: m.group(1)),
    (re.compile(r"[_\- ]run[_\-]?(\d+)", re.I),
     "run",
     lambda m: f"run{m.group(1)}"),
    (re.compile(r"[_\- ]v?(\d+\.\d+(?:\.\d+)?)", re.I),
     "version",
     lambda m: f"v{m.group(1)}"),
]

# Physical-plausibility of a numeric dataset — very loose, name-blind. We
# flag *ranges* to the SKILL.md builder, not diagnoses. Anything that
# looks like uninitialised memory (>1e12) is worth surfacing; anything
# whose magnitude spread exceeds 6 orders is suspicious.
_CORRUPT_MAG_THRESHOLD = 1e12
_CORRUPT_SPREAD_ORDERS = 6.0

# Documentation-sidecar extensions. HDF5 data almost never carries the
# physical-quantity semantics the SKILL.md builder needs; those live in
# README / PDF / txt siblings. We collect everything with these
# extensions from the record and hand them to the builder.
_DOC_EXTS = (".pdf", ".txt", ".md", ".rst", ".readme", ".doc", ".docx")
_DOC_NAME_HINTS = ("readme", "manual", "documentation", "codebook",
                   "user_guide", "userguide", "changelog", "notes")
# Cap per-doc download so a 500 MB pdf doesn't hold up the inventory.
_DOC_MAX_BYTES = 20 * 1024 * 1024

# Similarity threshold for merging near-match fingerprint groups. Files
# whose normalised-channel sets have Jaccard overlap >= this threshold
# are treated as the same logical group. ATLASM5 has 3 sub-schemas
# whose Jaccard vs. the union is ~0.96 (they differ only by one or two
# optional rain channels); a threshold of 0.8 collapses all of them.
# The CHANNELS-tuple mechanism in the emitted loader then handles the
# per-file missing channels transparently.
_JACCARD_MERGE_THRESHOLD = 0.80


# --------------------------------------------------------------------------- #
# HDF5 walk
# --------------------------------------------------------------------------- #

class _InventoryError(RuntimeError):
    """Raised when the inventory can't proceed. Caught in `_cli` and
    reported to stdout — we never let SystemExit escape because the
    ARGUS KernelShellBackend runs this script in-process, and any
    SystemExit propagation derails the agent's downstream tool calls.
    """


def _import_h5py():
    try:
        import h5py  # noqa: F401
    except ImportError:
        raise _InventoryError(
            "h5py is not installed. Run: pip install --user h5py"
        )
    import h5py
    return h5py


def _import_xarray():
    try:
        import xarray  # noqa: F401
    except ImportError:
        raise _InventoryError(
            "xarray is not installed (needed to read NetCDF). Run: "
            "pip install --user xarray netCDF4"
        )
    import xarray
    return xarray


def _import_numpy():
    import numpy as np
    return np


# File extensions handled by the NetCDF (xarray) path vs the HDF5 (h5py) path.
_NETCDF_EXTS = (".nc", ".nc4", ".cdf")
_HDF5_EXTS = (".h5", ".hdf5", ".he5")


def _normalise_name(name: str) -> str:
    """Strip Igor Pro unit-suffix tails so `Outdoor_Temperature___C_`
    and `Outdoor_Temperature` fingerprint identically.

    Strategy: locate the LAST occurrence of `__` (Igor's double-underscore
    separator before a unit tail). If the substring from that position
    to end matches a unit-body pattern, strip it. Then trim any stray
    trailing underscores.
    """
    if name in _IGOR_ADMIN_NAMES:
        return name
    idx = name.rfind("__")
    if idx > 0:
        tail = name[idx:]
        if _UNIT_TAIL_BODY_RE.match(tail):
            name = name[:idx]
    name = _BARE_TAIL_RE.sub("", name)
    return name


def _parse_unit_tail(name: str) -> str | None:
    """Return the unit-tail piece if the name carries one, else None.

    `Outdoor_Temperature___C_` -> `C`
    `Wind_Speed__km_h_`        -> `km_h`
    `Outdoor_Humidity____`     -> None   (empty tail)
    `dateW`                    -> None
    """
    idx = name.rfind("__")
    if idx <= 0:
        return None
    tail = name[idx:]
    if not _UNIT_TAIL_BODY_RE.match(tail):
        return None
    return tail.strip("_") or None


def _detect_time_axis(datasets: dict[str, dict]) -> str | None:
    """Return the source-name of the file's time axis, if one is present.

    Heuristics: (1) name matches `dateW` / `time` / `timestamp` /
    `date`; (2) dataset is 1-D numeric. If a dataset has an
    IGORWaveUnits='dat' attribute we take that as strong evidence.
    """
    # IGORWaveUnits='dat' is definitive
    for src, meta in datasets.items():
        wu = meta.get("attrs", {}).get("IGORWaveUnits")
        if isinstance(wu, (bytes, str)):
            if (wu.decode() if isinstance(wu, bytes) else wu).strip() == "dat":
                return src
    # CF-convention time: a variable whose `units` reads "<unit> since <date>".
    for src, meta in datasets.items():
        u = meta.get("attrs", {}).get("units")
        if isinstance(u, (bytes, str)):
            us = (u.decode() if isinstance(u, bytes) else u).lower()
            if " since " in us and any(us.startswith(p) for p in
                                       ("seconds", "minutes", "hours", "days")):
                return src
    for src, meta in datasets.items():
        base = src.lower().rsplit("/", 1)[-1]
        if any(base == hint or base.startswith(hint) for hint in _TIME_AXIS_HINTS):
            if len(meta.get("shape", ())) == 1:
                return src
    return None


def _walk_group(np, root, prefix: str, out: dict[str, dict]) -> None:
    """Recursively walk an h5py.Group; populate `out` keyed by full path."""
    for key in root.keys():
        try:
            obj = root[key]
        except Exception as e:
            out[f"{prefix}/{key}"] = {"error": f"{type(e).__name__}: {e}"}
            continue
        full = f"{prefix}/{key}".lstrip("/")
        # h5py.Dataset check without importing the class each call
        if hasattr(obj, "shape") and hasattr(obj, "dtype"):
            attrs: dict[str, Any] = {}
            for ak, av in obj.attrs.items():
                try:
                    if hasattr(av, "tolist"):
                        av = av.tolist()
                    elif isinstance(av, bytes):
                        av = av.decode(errors="replace")
                    attrs[ak] = av
                except Exception:
                    attrs[ak] = "<unreadable>"
            out[full] = {
                "shape": tuple(int(x) for x in obj.shape),
                "dtype": str(obj.dtype),
                "dtype_kind": obj.dtype.kind,
                "attrs": attrs,
            }
        else:
            # It's a group — recurse
            out[full] = {"is_group": True}
            _walk_group(np, obj, full, out)


def _dataset_stats(np, obj, max_samples: int = 200_000) -> dict[str, Any]:
    """Compute name-blind stats on a numeric dataset. Sampled if very large."""
    n = int(obj.size)
    if n == 0:
        return {"n": 0}
    try:
        if n > max_samples:
            # Regular-stride sample instead of full read to keep memory bounded.
            step = max(1, n // max_samples)
            arr = np.asarray(obj[::step]).ravel().astype("float64", copy=False)
        else:
            arr = np.asarray(obj[:]).ravel().astype("float64", copy=False)
    except Exception as e:
        return {"n": n, "error": f"{type(e).__name__}: {e}"}
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return {"n": n, "n_finite": 0}
    return {
        "n": n,
        "n_finite": int(finite.size),
        "min":    float(finite.min()),
        "max":    float(finite.max()),
        "median": float(np.median(finite)),
        "p05":    float(np.percentile(finite, 5)),
        "p95":    float(np.percentile(finite, 95)),
    }


def _stats_from_array(np, arr, n, max_samples=200_000):
    """Shared stats body for an already-materialised flat numpy array."""
    if n == 0:
        return {"n": 0}
    if arr.size > max_samples:
        step = max(1, arr.size // max_samples)
        arr = arr[::step]
    arr = arr.astype("float64", copy=False)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return {"n": n, "n_finite": 0}
    return {
        "n": n,
        "n_finite": int(finite.size),
        "min":    float(finite.min()),
        "max":    float(finite.max()),
        "median": float(np.median(finite)),
        "p05":    float(np.percentile(finite, 5)),
        "p95":    float(np.percentile(finite, 95)),
    }


def _walk_netcdf(np, path):
    """Walk a NetCDF file with xarray. Returns (datasets, global_attrs).

    `datasets` is keyed by variable name and shaped like the h5py walker's
    output — shape / dtype / dtype_kind / attrs — plus NetCDF-specific
    `dims` (named dimensions) and `is_coord`. Reads NetCDF-3 (classic) and
    NetCDF-4 (HDF5-based) alike via the netCDF4 backend.
    """
    xr = _import_xarray()
    datasets: dict[str, dict] = {}
    # decode_times=False: a non-CF / IOAPI time axis must not fail the open;
    # time is detected + documented separately, and the emitted loader lets
    # the user opt into xarray's CF decoding.
    ds = xr.open_dataset(path, decode_times=False, mask_and_scale=False)
    try:
        global_attrs = {}
        for k, v in ds.attrs.items():
            try:
                global_attrs[k] = v.item() if hasattr(v, "item") else v
            except Exception:
                global_attrs[k] = str(v)
        for name, var in ds.variables.items():
            attrs = {}
            for k, v in var.attrs.items():
                try:
                    attrs[k] = v.item() if hasattr(v, "item") else v
                except Exception:
                    attrs[k] = str(v)
            meta = {
                "shape":      tuple(int(x) for x in var.shape),
                "dtype":      str(var.dtype),
                "dtype_kind": var.dtype.kind,
                "dims":       [str(d) for d in var.dims],
                "attrs":      attrs,
                "is_coord":   name in ds.coords,
            }
            if var.dtype.kind in ("f", "i", "u"):
                # Skip stats on very large variables to bound memory (the file
                # size guard caps the download, but a single var can still be
                # hundreds of MB). Shape/dtype/attrs are still recorded.
                if getattr(var, "nbytes", 0) <= 400 * 1024 * 1024:
                    try:
                        flat = np.asarray(var.values).ravel()
                        stats = _stats_from_array(np, flat, int(var.size))
                        meta["stats"] = stats
                        flag = _corruption_flag(stats)
                        if flag:
                            meta["corruption_flag"] = flag
                    except Exception as e:
                        meta["stats"] = {"error": f"{type(e).__name__}: {e}"}
            datasets[str(name)] = meta
    finally:
        ds.close()
    return datasets, global_attrs


def _corruption_flag(stats: dict[str, Any]) -> str | None:
    """Return a short flag if the dataset looks numerically suspect."""
    if "min" not in stats or "max" not in stats:
        return None
    lo, hi = stats["min"], stats["max"]
    if abs(hi) > _CORRUPT_MAG_THRESHOLD or abs(lo) > _CORRUPT_MAG_THRESHOLD:
        return f"extreme magnitude (|max|={abs(hi):.2e})"
    med = stats.get("median", 0.0)
    # Compare max to typical scale (median or p95). If max is many orders
    # bigger than the "middle of the distribution", flag it.
    ref = max(abs(med), abs(stats.get("p95", 0.0)), 1e-30)
    if abs(hi) > 0 and ref > 0:
        import math
        try:
            spread = math.log10(abs(hi) + 1e-30) - math.log10(ref)
            if spread >= _CORRUPT_SPREAD_ORDERS:
                return f"spread ~{spread:.1f} orders of magnitude"
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------------- #
# Fetch: download HDF5 file to a local scratch path (bounded)
# --------------------------------------------------------------------------- #

def _download_file(url: str, dest: Path, max_bytes: int) -> tuple[Path, int]:
    """Download `url` to `dest`, capped at `max_bytes`. Returns (path, size)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "array-skill-builder-inventory/0.1"},
    )
    size = 0
    with urllib.request.urlopen(req, timeout=300) as r, dest.open("wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                # Truncate: keep only what's in the file, warn upstream.
                f.write(chunk[: max_bytes - (size - len(chunk))])
                size = max_bytes
                break
            f.write(chunk)
    return dest, size


# --------------------------------------------------------------------------- #
# Fingerprinting and grouping
# --------------------------------------------------------------------------- #

def _fingerprint(datasets: dict[str, dict],
                 time_axis_src: str | None) -> tuple[str, dict]:
    """Compute the schema fingerprint for one file.

    Returns (fingerprint_hex, breakdown_dict). The breakdown is stored
    alongside so the summariser can explain what the fingerprint means.
    """
    normalised_shape: list[tuple[str, int, str]] = []
    for src, meta in sorted(datasets.items()):
        if meta.get("is_group") or "error" in meta:
            continue
        norm = _normalise_name(src.rsplit("/", 1)[-1])
        rank = len(meta.get("shape", ()))
        kind = meta.get("dtype_kind", "?")
        # Family of kinds: numeric floats ('f'), signed ints ('i'),
        # unsigned ('u'), boolean ('b'), string ('S','U','O'). Group ints
        # together so a channel switching int32↔int64 doesn't split a
        # group.
        family = {"f": "float", "i": "int", "u": "int",
                  "b": "bool",  "S": "str", "U": "str", "O": "str"}.get(kind, kind)
        normalised_shape.append((norm, rank, family))

    time_norm = _normalise_name(time_axis_src.rsplit("/", 1)[-1]) if time_axis_src else None
    breakdown = {
        "normalised_datasets": [
            {"name": n, "rank": r, "family": f} for n, r, f in normalised_shape
        ],
        "time_axis": time_norm,
    }
    payload = json.dumps(breakdown, sort_keys=True).encode()
    fp = hashlib.sha1(payload).hexdigest()[:40]
    return fp, breakdown


def _infer_partition_axis(filenames: list[str]) -> tuple[str, dict[str, str]]:
    """From a group of filenames, guess the partition axis + per-file key.

    Returns (axis_label, {filename: key}). Falls back to "index" if no
    heuristic matches.
    """
    for regex, label, extract in _PARTITION_HEURISTICS:
        keys: dict[str, str] = {}
        hits = 0
        for fn in filenames:
            m = regex.search(fn)
            if m:
                keys[fn] = extract(m)
                hits += 1
        if hits >= max(1, len(filenames) // 2):
            # At least half the files matched — take this axis.
            for fn in filenames:
                if fn not in keys:
                    keys[fn] = fn  # opaque fallback for unmatched
            return label, keys
    return "index", {fn: str(i) for i, fn in enumerate(filenames)}


# --------------------------------------------------------------------------- #
# Main inventory pass
# --------------------------------------------------------------------------- #

def _inventory_one_file(url: str, filename: str,
                        cache_dir: Path, max_bytes: int) -> dict[str, Any]:
    """Download-and-inspect one array file (HDF5 or NetCDF). Returns a
    per-file record. The reader is chosen by extension: NetCDF (.nc/.nc4/
    .cdf) via xarray, HDF5 (.h5/.hdf5/.he5) via h5py."""
    np = _import_numpy()
    is_netcdf = filename.lower().endswith(_NETCDF_EXTS)
    t0 = time.time()
    if url.startswith("file://"):
        # Already staged locally by a fetcher shell — read in place rather
        # than making a second copy of a potentially very large file.
        path = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path))
        if not path.exists():
            return {"filename": filename, "url": url,
                    "error": f"staged file not found: {path}"}
        size = path.stat().st_size
    else:
        local = cache_dir / hashlib.sha1(url.encode()).hexdigest()
        try:
            path, size = _download_file(url, local, max_bytes)
        except Exception as e:
            return {
                "filename": filename, "url": url,
                "error": f"download failed: {type(e).__name__}: {e}",
            }
    dl_sec = time.time() - t0

    fmt = "netcdf" if is_netcdf else "hdf5"
    try:
        if is_netcdf:
            datasets, global_attrs = _walk_netcdf(np, path)
            top_group_name = None
        else:
            h5py = _import_h5py()
            global_attrs = {}
            with h5py.File(path, "r") as f:
                top_keys = list(f.keys())
                if len(top_keys) == 1:
                    top_group_name = top_keys[0]
                    grp = f[top_group_name]
                else:
                    top_group_name = None
                    grp = f
                datasets = {}
                _walk_group(np, grp, "", datasets)
                # Per-dataset stats for numeric leaves
                for src, meta in list(datasets.items()):
                    if meta.get("is_group") or "error" in meta:
                        continue
                    if meta.get("dtype_kind") in ("f", "i", "u"):
                        if src.rsplit("/", 1)[-1] in _IGOR_ADMIN_NAMES:
                            continue
                        try:
                            obj = grp[src]
                        except Exception:
                            continue
                        stats = _dataset_stats(np, obj)
                        meta["stats"] = stats
                        flag = _corruption_flag(stats)
                        if flag:
                            meta["corruption_flag"] = flag
        time_axis_src = _detect_time_axis(datasets)
        fp, breakdown = _fingerprint(datasets, time_axis_src)
    except _InventoryError:
        raise
    except Exception as e:
        reader = "xarray" if is_netcdf else "h5py"
        return {
            "filename": filename, "url": url, "format": fmt,
            "download_seconds": round(dl_sec, 2),
            "downloaded_bytes": size,
            "error": f"{reader} open/walk failed: {type(e).__name__}: {e}",
        }

    return {
        "filename":         filename,
        "url":              url,
        "format":           fmt,
        "downloaded_bytes": size,
        "download_seconds": round(dl_sec, 2),
        "top_group":        top_group_name,
        "global_attrs":     global_attrs,
        "fingerprint":      fp,
        "time_axis":        time_axis_src,
        "n_datasets":       sum(1 for m in datasets.values()
                                if not m.get("is_group") and "error" not in m),
        "datasets":         datasets,
        "fingerprint_breakdown": breakdown,
    }


def _build_groups(files_by_fp: dict[str, list[dict]]) -> list[dict]:
    """Turn per-file records into group records with partition-axis inference."""
    groups: list[dict] = []
    for fp, records in files_by_fp.items():
        filenames = [r["filename"] for r in records]
        axis, keys = _infer_partition_axis(filenames)
        # Union of dataset names across all files in the group. Reported
        # via source-name; the builder computes clean names downstream.
        union: dict[str, dict] = {}
        for r in records:
            for src, meta in r["datasets"].items():
                if meta.get("is_group") or "error" in meta:
                    continue
                union.setdefault(src, {
                    "src_names_seen": set(),
                    "ranks": Counter(),
                    "dtype_kinds": Counter(),
                    "flagged_in_files": [],
                    "attrs_examples": {},
                    "stats_by_file": {},
                    "dims": None,        # NetCDF named dimensions (first seen)
                    "is_coord": False,
                })
                union[src]["src_names_seen"].add(src)
                union[src]["ranks"][len(meta.get("shape", ()))] += 1
                union[src]["dtype_kinds"][meta.get("dtype_kind", "?")] += 1
                if meta.get("dims") and union[src]["dims"] is None:
                    union[src]["dims"] = list(meta["dims"])
                if meta.get("is_coord"):
                    union[src]["is_coord"] = True
                if meta.get("corruption_flag"):
                    union[src]["flagged_in_files"].append(
                        (r["filename"], meta["corruption_flag"])
                    )
                for ak, av in meta.get("attrs", {}).items():
                    union[src]["attrs_examples"].setdefault(ak, av)
                if "stats" in meta:
                    union[src]["stats_by_file"][r["filename"]] = meta["stats"]
        # Also compute the normalised-name view — this is what the loader
        # will use when building CHANNELS tuples.
        normalised: dict[str, list[str]] = defaultdict(list)
        for src in sorted(union.keys()):
            base = src.rsplit("/", 1)[-1]
            norm = _normalise_name(base)
            normalised[norm].append(base)

        # Serialisation cleanup for the JSON payload
        for src, u in union.items():
            u["src_names_seen"] = sorted(u.pop("src_names_seen"))
            u["ranks"] = dict(u.pop("ranks"))
            u["dtype_kinds"] = dict(u.pop("dtype_kinds"))

        groups.append({
            "_fingerprint":  fp,
            "n_files":       len(records),
            "format":        records[0].get("format", "hdf5"),
            "partition_axis": axis,
            "partition_keys": keys,
            "time_axis":     records[0]["time_axis"],
            "top_group_names": sorted({r.get("top_group") for r in records
                                        if r.get("top_group")}),
            "global_attrs":  records[0].get("global_attrs") or {},
            "datasets_union": union,
            "normalised_channels": {
                norm: sorted(set(sources))
                for norm, sources in normalised.items()
            },
            "files": [{
                "filename":         r["filename"],
                # `source_url` is the remote download URL — this is what the
                # emitted skill's lazy-download helper must embed. `url` may
                # be a file:// staging path when a fetcher supplied the files.
                "source_url":       r.get("source_url") or r["url"],
                "url":              r["url"],
                "downloaded_bytes": r.get("downloaded_bytes"),
                "top_group":        r.get("top_group"),
                "partition_key":    keys.get(r["filename"]),
                "n_datasets":       r.get("n_datasets"),
            } for r in records],
        })
    return groups


# --------------------------------------------------------------------------- #
# Documentation-sidecar handling
# --------------------------------------------------------------------------- #

def _is_documentation(filename_lower: str) -> bool:
    if filename_lower.endswith(_DOC_EXTS):
        return True
    stem = filename_lower.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return any(hint in stem for hint in _DOC_NAME_HINTS)


def _download_docs(docs: list[dict], out_dir: Path) -> list[dict]:
    """Download every documentation sidecar to <out_dir>/_docs/ and return a
    manifest the SKILL.md builder can iterate. PDFs are downloaded verbatim
    (opened later by pypdf); plain-text files get inlined into the manifest
    up to 30 KB so the builder can read semantics without extra I/O."""
    doc_dir = out_dir / "_docs"
    manifest: list[dict] = []
    for f in docs:
        fn = f.get("filename") or ""
        url = f.get("url")
        if not url:
            continue
        try:
            if url.startswith("file://"):
                # Fetcher already staged it — point at it in place.
                local = Path(urllib.parse.unquote(
                    urllib.parse.urlparse(url).path))
                size = local.stat().st_size
            else:
                local, size = _download_file(url, doc_dir / fn, _DOC_MAX_BYTES)
        except Exception as e:
            manifest.append({
                "filename": fn, "url": url,
                "error": f"{type(e).__name__}: {e}",
            })
            continue
        entry = {"filename": fn, "url": url,
                 "local_path": str(local), "size_bytes": size}
        if fn.lower().endswith((".txt", ".md", ".rst", ".readme")):
            try:
                text = local.read_text(encoding="utf-8", errors="replace")
                entry["text_head"] = text[:30_000]
                if len(text) > 30_000:
                    entry["text_truncated"] = True
            except Exception:
                pass
        manifest.append(entry)
    return manifest


# --------------------------------------------------------------------------- #
# Group merging by Jaccard similarity of normalised-channel sets
# --------------------------------------------------------------------------- #

def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / max(len(a | b), 1)


def _merge_similar_groups(groups: list[dict],
                          threshold: float = _JACCARD_MERGE_THRESHOLD
                          ) -> list[dict]:
    """Union-find over groups whose normalised-channel sets are similar
    enough. This collapses ATLASM5's three exact-fingerprint sub-schemas
    (differing only by an optional Yearly_Rain / Monthly_Rain channel)
    into one logical group. The CHANNELS-tuple mechanism in the emitted
    loader handles per-file missing channels.
    """
    n = len(groups)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    sets = [set(g["normalised_channels"].keys()) for g in groups]
    for i in range(n):
        for j in range(i + 1, n):
            if _jaccard(sets[i], sets[j]) >= threshold:
                union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    merged: list[dict] = []
    for root, members in clusters.items():
        if len(members) == 1:
            merged.append(groups[members[0]])
            continue
        # Merge: combine files + take union of channels + re-infer axis
        combined_files = []
        combined_dsu: dict = {}
        combined_channels: dict[str, list[str]] = defaultdict(list)
        component_fps = []
        top_groups: set[str] = set()
        for m in members:
            g = groups[m]
            component_fps.append(g["_fingerprint"][:8])
            combined_files.extend(g["files"])
            for src, u in g["datasets_union"].items():
                # Deep-merge, NOT setdefault. When two fingerprint groups
                # both carry a channel under the same source name (common —
                # the gases keep one name across every dialect), a
                # first-wins merge silently discards the second group's
                # data-quality flags and per-file stats. That under-reports
                # corruption in the emitted SKILL.md's Caveats, which is
                # the one thing this inventory exists to get right.
                tgt = combined_dsu.get(src)
                if tgt is None:
                    combined_dsu[src] = {
                        "src_names_seen":   list(u.get("src_names_seen", [])),
                        "ranks":            dict(u.get("ranks", {})),
                        "dtype_kinds":      dict(u.get("dtype_kinds", {})),
                        "flagged_in_files": list(u.get("flagged_in_files", [])),
                        "attrs_examples":   dict(u.get("attrs_examples", {})),
                        "stats_by_file":    dict(u.get("stats_by_file", {})),
                        "dims":             u.get("dims"),
                        "is_coord":         u.get("is_coord", False),
                    }
                    continue
                tgt["src_names_seen"] = sorted(
                    set(tgt["src_names_seen"]) | set(u.get("src_names_seen", [])))
                for k, v in u.get("ranks", {}).items():
                    tgt["ranks"][k] = tgt["ranks"].get(k, 0) + v
                for k, v in u.get("dtype_kinds", {}).items():
                    tgt["dtype_kinds"][k] = tgt["dtype_kinds"].get(k, 0) + v
                tgt["flagged_in_files"].extend(u.get("flagged_in_files", []))
                for k, v in u.get("attrs_examples", {}).items():
                    tgt["attrs_examples"].setdefault(k, v)
                tgt["stats_by_file"].update(u.get("stats_by_file", {}))
            for norm, srcs in g["normalised_channels"].items():
                combined_channels[norm].extend(srcs)
            top_groups.update(g.get("top_group_names") or [])
        filenames = [f["filename"] for f in combined_files]
        axis, keys = _infer_partition_axis(filenames)
        for f in combined_files:
            f["partition_key"] = keys.get(f["filename"], f["partition_key"])
        merged.append({
            "_fingerprint":  "merged:" + "+".join(component_fps),
            "merged_from":   component_fps,
            "n_files":       len(combined_files),
            "format":        groups[members[0]].get("format", "hdf5"),
            "partition_axis": axis,
            "partition_keys": keys,
            "time_axis":     groups[members[0]]["time_axis"],
            "top_group_names": sorted(top_groups),
            "global_attrs":  groups[members[0]].get("global_attrs") or {},
            "datasets_union":  combined_dsu,
            "normalised_channels": {
                norm: sorted(set(srcs))
                for norm, srcs in combined_channels.items()
            },
            "files": combined_files,
        })
    return merged


# --------------------------------------------------------------------------- #
# Summary formatting (bounded stdout)
# --------------------------------------------------------------------------- #

def _print_summary(source_desc: str,
                   record_meta: dict | None,
                   groups: list[dict],
                   unreadable: list[dict],
                   docs: list[dict]) -> None:
    lines = []
    lines.append(f"array-skill-builder inventory — {source_desc}")
    if record_meta:
        title = record_meta.get("title") or "(no title)"
        lines.append(f"  record title: {title[:100]}")
        if record_meta.get("files"):
            lines.append(f"  files listed: {len(record_meta['files'])}")
        if record_meta.get("doi"):
            lines.append(f"  doi: {record_meta['doi']}")
        creators = record_meta.get("creators") or []
        if creators:
            lines.append(f"  creators: {'; '.join(creators[:3])}"
                         + (" ..." if len(creators) > 3 else ""))
        lic = (record_meta.get("license") or {})
        if isinstance(lic, dict):
            lic_id = lic.get("id") or lic.get("title")
            if lic_id:
                lines.append(f"  license: {lic_id}")
    if docs:
        lines.append(f"\nDocumentation sidecars: {len(docs)} (fetched to _docs/)")
        for d in docs[:10]:
            if "error" in d:
                lines.append(f"  ✗ {d['filename']}: {d['error'][:80]}")
            else:
                size_k = (d.get("size_bytes") or 0) / 1024
                marker = "(text inlined)" if "text_head" in d else ""
                lines.append(f"  • {d['filename']}  {size_k:.0f} KB  {marker}")
        if len(docs) > 10:
            lines.append(f"  ... and {len(docs)-10} more")
        lines.append("READ THESE via inventory JSON's `documentation[].local_path`"
                     " before writing the SKILL.md — HDF5 alone rarely carries"
                     " semantic labels.")
    lines.append("")
    lines.append(f"Groups by schema fingerprint: {len(groups)}")
    for i, g in enumerate(groups, 1):
        n = g["n_files"]
        axis = g["partition_axis"]
        example_files = [f["filename"] for f in g["files"][:3]]
        example_keys  = [f["partition_key"] for f in g["files"][:3]]
        n_channels = len(g["normalised_channels"])
        fmt = g.get("format", "hdf5")
        lines.append(
            f"  [{i}] fp={g['_fingerprint'][:8]}  format={fmt}  n_files={n}  "
            f"axis={axis}  {'variables' if fmt=='netcdf' else 'channels'}={n_channels}"
        )
        lines.append(f"       time_axis={g['time_axis']!r}  "
                     f"top_groups={g['top_group_names']}")
        lines.append(f"       example files: {example_files}")
        lines.append(f"       partition keys: {example_keys}")
        # NetCDF: show each variable's named dims + units so the SKILL.md
        # writer can emit dimension-aware xarray loaders without re-opening
        # the file. (HDF5 has no named dims; this block is NetCDF-only.)
        if fmt == "netcdf":
            lines.append("       variables (name: dims [units]):")
            for src, u in list(g["datasets_union"].items())[:20]:
                dims = u.get("dims") or []
                ax = u.get("attrs_examples", {})
                units = ax.get("units", "")
                coord = " [coord]" if u.get("is_coord") else ""
                lines.append(f"         {src}: {dims} "
                             f"[{units}]{coord}".rstrip())
        # The emitted skill needs (partition_key -> remote URL). Print the
        # full mapping here so the SKILL.md writer never has to open the
        # inventory JSON just to recover download URLs.
        remote = [(f.get("partition_key"), f.get("source_url"))
                  for f in g["files"]
                  if f.get("source_url", "").startswith(("http://", "https://"))]
        if remote:
            lines.append(f"       partition -> source URL ({len(remote)}):")
            for k, u in remote:
                lines.append(f"         {k} = {u}")

        # Flag channels where any source-name differs from another —
        # the CHANNELS-tuple case.
        multi_name = [(norm, srcs) for norm, srcs in
                      g["normalised_channels"].items() if len(srcs) > 1]
        if multi_name:
            lines.append(f"       DIALECT SPLIT — {len(multi_name)} channels "
                         f"appear under multiple source names:")
            for norm, srcs in multi_name[:5]:
                lines.append(f"         '{norm}': {srcs}")
            if len(multi_name) > 5:
                lines.append(f"         ... and {len(multi_name)-5} more")

        # Corruption flags summarised across the group.
        flagged = [(src, len(u["flagged_in_files"]))
                   for src, u in g["datasets_union"].items()
                   if u["flagged_in_files"]]
        if flagged:
            lines.append(f"       DATA-QUALITY FLAGS on {len(flagged)} channels "
                         f"(name-blind — verify manually):")
            for src, n_files in sorted(flagged, key=lambda x: -x[1])[:5]:
                lines.append(f"         '{src}' flagged in {n_files} file(s)")
            if len(flagged) > 5:
                lines.append(f"         ... and {len(flagged)-5} more")

        lines.append("")

    if unreadable:
        lines.append(f"Unreadable files: {len(unreadable)}")
        for u in unreadable[:5]:
            lines.append(f"  - {u['filename']}: {u.get('error', '?')[:100]}")
        if len(unreadable) > 5:
            lines.append(f"  ... and {len(unreadable)-5} more")
        lines.append("")

    lines.append("Loader-shape hint by group size:")
    for i, g in enumerate(groups, 1):
        n = g["n_files"]
        axis = g["partition_axis"]
        if n == 1:
            shape = "single-file skill (load() returns the whole dataset)"
        elif axis == "month":
            shape = "temporal partition — load_month(m) + load_year()"
        elif axis == "date":
            shape = "temporal partition — load_date(d) + load_all()"
        elif axis == "site":
            shape = "spatial partition — load_site(name) + load_all()"
        elif axis == "run":
            shape = "parameter sweep — load_run(name) + load_all()"
        else:
            shape = f"indexed collection — load_partition(k) + load_all() (keys: index)"
        lines.append(f"  [{i}] {shape}")

    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def _print_gate_reminder() -> None:
    """Print the mandatory-stop block.

    The proposal gate is the single most-skipped instruction in the field.
    SKILL.md prose gets skimmed; this stdout block does not, because the
    agent always reads the inventory output it just ran. Printed LAST so it
    is the final thing in the tool result.
    """
    bar = "=" * 66
    sys.stdout.write(
        f"\n{bar}\n"
        "NEXT STEP — MANDATORY STOP (array-skill-builder Step 4)\n"
        "\n"
        "  1. Read the documentation sidecars (if any) for semantics.\n"
        "  2. PRESENT A PROPOSAL to the user and END YOUR TURN.\n"
        "\n"
        "  Do NOT write a SKILL.md. Do NOT write a build script. Do NOT\n"
        "  bundle or merge data. The user replies 'yes' (or with edits)\n"
        "  in the NEXT cell — only then do you build.\n"
        "\n"
        "  Building now would skip the user's only chance to correct the\n"
        "  skill shape, the variable/channel semantics, and the join or\n"
        "  partition decisions — all invisible once built.\n"
        f"{bar}\n"
    )
    sys.stdout.flush()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _cli(argv: list[str]) -> int:
    # exit_on_error=False so argparse raises ArgumentError instead of
    # calling sys.exit() when the user passes bad args — sys.exit()
    # would propagate as SystemExit through KernelShellBackend and
    # derail the agent.
    ap = argparse.ArgumentParser(exit_on_error=False)
    ap.add_argument("--dir", default=None,
                    help="Directory of already-staged HDF5/NetCDF files "
                         "(what a fetcher shell hands you). Primary mode.")
    ap.add_argument("--url", action="append", default=[],
                    help="Direct URL to an HDF5 file. May be repeated. "
                         "Convenience mode for a single ad-hoc file.")
    ap.add_argument("--out", required=True,
                    help="Output directory for _inventory.json + cache.")
    ap.add_argument("--max-file-mb", type=int, default=200,
                    help="Cap per-file download size in MB (default 200). "
                         "Applies to --url only; --dir files are read in place.")
    try:
        args = ap.parse_args(argv)
    except (argparse.ArgumentError, argparse.ArgumentTypeError) as e:
        sys.stdout.write(f"ERROR: invalid arguments: {e}\n")
        sys.stdout.write(ap.format_usage())
        return 2

    if not args.url and not args.dir:
        sys.stdout.write(
            "ERROR: provide --dir (staged files from a fetcher) or --url\n"
            "       Zenodo/CKAN records are handled by their fetcher shells\n"
            "       (zenodo-skill-builder / ckan-skill-builder), which stage\n"
            "       the files and then call this script with --dir.\n"
        )
        sys.stdout.write(ap.format_usage())
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "_h5_cache"
    max_bytes = args.max_file_mb * 1024 * 1024

    record_meta: dict | None = None
    urls: list[tuple[str, str]] = []   # (url-or-local-path, filename)
    docs: list[dict] = []
    remote_urls: dict[str, str] = {}   # filename -> original download URL

    if args.dir:
        src = Path(args.dir)
        if not src.is_dir():
            sys.stdout.write(f"ERROR: --dir is not a directory: {src}\n")
            return 2
        # The fetcher records where each staged file came from. The emitted
        # skill needs those REMOTE urls for its lazy-download helper — a
        # local file:// staging path is useless once the scratch dir is
        # cleaned up. Thread them through so the SKILL.md writer never has
        # to go digging in _classification.json.
        cls_path = src / "_classification.json"
        if cls_path.exists():
            try:
                cls = json.loads(cls_path.read_text())
                for e in cls.get("files", []):
                    fn, u = e.get("filename"), e.get("url")
                    if fn and u:
                        remote_urls[Path(fn).name] = u
            except Exception as e:
                sys.stdout.write(
                    f"WARNING: could not read {cls_path.name}: {e}\n")
        # Array files staged by the fetcher.
        for p in sorted(src.rglob("*")):
            if p.is_file() and p.suffix.lower() in (".h5", ".hdf5", ".he5",
                                                    ".nc", ".nc4", ".cdf"):
                urls.append((p.as_uri(), p.name))
        # Provenance written by the fetcher, if present — threaded into the
        # inventory so the SKILL.md writer has title/DOI/license/citation.
        # Zenodo and CKAN/NDP fetchers write different sidecars; read whichever
        # is present and normalise to a common shape.
        zen_path = src / "_zenodo_metadata.json"
        ckan_path = src / "_ckan_metadata.json"
        if zen_path.exists():
            try:
                z = json.loads(zen_path.read_text())
                record_meta = {
                    "record_id":   z.get("record_id"),
                    "title":       z.get("title"),
                    "description": z.get("description"),
                    "license":     z.get("license"),
                    "creators":    z.get("creators") or [],
                    "doi":         z.get("doi"),
                    "source_url":  z.get("source_url"),
                    "files":       [],
                }
            except Exception as e:
                sys.stdout.write(
                    f"WARNING: could not read {zen_path.name}: {e}\n")
        elif ckan_path.exists():
            try:
                c = json.loads(ckan_path.read_text())
                record_meta = {
                    "record_id":   c.get("name"),
                    "title":       c.get("title"),
                    "description": c.get("notes"),          # CKAN field name
                    "license":     c.get("license_title"),
                    "creators":    [c["organization"]] if c.get("organization") else [],
                    "doi":         None,
                    "source_url":  c.get("source_url"),
                    "files":       [],
                }
            except Exception as e:
                sys.stdout.write(
                    f"WARNING: could not read {ckan_path.name}: {e}\n")
        # Documentation staged by the fetcher under _docs/.
        docs_dir = src / "_docs"
        if docs_dir.is_dir():
            for p in sorted(docs_dir.rglob("*")):
                if p.is_file():
                    docs.append({"filename": p.name, "url": p.as_uri()})

    for u in args.url:
        fn = urllib.parse.unquote(u.rstrip("/").rsplit("/", 1)[-1])
        urls.append((u, fn))

    if not urls:
        sys.stdout.write(
            "ERROR: no .h5/.hdf5/.nc files found to inventory.\n"
            "       If this came from a fetcher, the record may hold only\n"
            "       tabular data — route it to tabular-skill-builder instead.\n"
        )
        return 2

    sys.stdout.write(f"Inspecting {len(urls)} file(s)...\n")
    sys.stdout.flush()

    per_file: list[dict] = []
    for i, (u, fn) in enumerate(urls, 1):
        sys.stdout.write(f"  [{i}/{len(urls)}] {fn}\n")
        sys.stdout.flush()
        rec = _inventory_one_file(u, fn, cache_dir, max_bytes)
        # Attach the original remote URL when the file was staged locally
        # by a fetcher. This is what the emitted skill's lazy-download
        # helper must use; `url` itself is a file:// staging path there.
        if fn in remote_urls:
            rec["source_url"] = remote_urls[fn]
        per_file.append(rec)

    ok = [r for r in per_file if "fingerprint" in r]
    bad = [r for r in per_file if "fingerprint" not in r]

    by_fp: dict[str, list[dict]] = defaultdict(list)
    for r in ok:
        by_fp[r["fingerprint"]].append(r)
    groups = _build_groups(by_fp)
    groups = _merge_similar_groups(groups)

    doc_manifest: list[dict] = []
    if docs:
        sys.stdout.write(f"Fetching {len(docs)} documentation sidecar(s)...\n")
        sys.stdout.flush()
        doc_manifest = _download_docs(docs, out_dir)

    inventory = {
        "source": {
            "staged_dir":     args.dir,
            "urls_supplied":  len(args.url),
            "record_id":      record_meta.get("record_id") if record_meta else None,
        },
        "record_metadata":  record_meta,
        "documentation":    doc_manifest,
        "groups":           groups,
        "unreadable_files": bad,
    }
    (out_dir / "_inventory.json").write_text(
        json.dumps(inventory, indent=2, default=str)
    )

    if args.dir:
        source_desc = f"staged dir {args.dir}"
        if record_meta and record_meta.get("record_id"):
            source_desc += f" (record {record_meta['record_id']})"
    else:
        source_desc = f"{len(args.url)} direct URL(s)"
    sys.stdout.write("\n")
    _print_summary(source_desc, record_meta, groups, bad, doc_manifest)
    sys.stdout.write(f"\nWrote {out_dir / '_inventory.json'}\n")
    if args.dir:
        sys.stdout.write(
            f"(Read staged files in place from {args.dir}; nothing was "
            f"downloaded.)\n"
        )
    else:
        sys.stdout.write(
            f"(Downloaded files cached at {cache_dir}; safe to delete after "
            f"the skill is built.)\n"
        )
    _print_gate_reminder()
    return 0


def _main(argv: list[str]) -> None:
    """Entry point safe for both real subprocess and in-process invocation.

    Never raises SystemExit. Errors are printed to stdout as an
    'ERROR: ...' line and the function returns; the agent reading
    stdout sees the failure without the interpreter tearing down."""
    try:
        _cli(argv)
    except _InventoryError as e:
        sys.stdout.write(f"ERROR: {e}\n")
    except KeyboardInterrupt:
        sys.stdout.write("ERROR: interrupted\n")
    except Exception as e:
        # Last-resort: something we didn't anticipate. Surface it, but
        # never let a SystemExit escape.
        import traceback
        sys.stdout.write(
            f"ERROR: unexpected {type(e).__name__}: {e}\n"
            + "".join(traceback.format_exception(type(e), e, e.__traceback__))
        )


if __name__ == "__main__":
    _main(sys.argv[1:])
