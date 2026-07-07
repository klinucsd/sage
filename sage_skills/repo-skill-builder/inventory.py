#!/usr/bin/env python3
"""Canonical inventory script bundled with the repo-skill-builder meta-skill.

Run this verbatim — do not rewrite it. It walks a cloned repo, captures
schema metadata for every tabular file (CSV/TSV/XLSX/XLS/Parquet), groups
files by schema fingerprint (sorted column names), writes full detail to
`_inventory.json`, and prints a compact action-ready summary to stdout.

The agent reads the stdout summary (a few KB) to make the Step 3 grouping
decision. The JSON is a reference for Step 5+ when build scripts may need
a specific file path or row count.

Usage:
    python inventory.py <repo-dir> [output-json-path]

Examples:
    python inventory.py /tmp/repo-skills/Well_data_Po
    python inventory.py /tmp/repo-skills/Well_data_Po /tmp/custom_inv.json
"""
import json
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path


TABULAR_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".gpkg"}
SKIP_DIRS = {".git", ".github", "__pycache__", ".ipynb_checkpoints"}

# Caps on what we store per record in _inventory.json. The schema
# fingerprint (used for grouping) is always computed from the FULL
# column list — only the per-record JSON payload is capped, to keep
# the file small enough that any accidental agent-side read doesn't
# blow up its context window.
MAX_COLS_STORED = 50         # store at most this many column names per file
MAX_CELL_CHARS = 200         # truncate long string values in sample_rows
INCLUDE_SAMPLE_IF_COLS_LE = 50  # skip sample_rows entirely if columns > this


# ---------------------------------------------------------------------------
# Per-file inspection
# ---------------------------------------------------------------------------

def _detect_csv_delimiter(fp, encoding="utf-8"):
    """Return the most likely delimiter for a CSV/TSV from its first line."""
    try:
        with open(fp, "r", encoding=encoding, errors="replace") as f:
            line = f.readline()
        # priority order: tab > semicolon > pipe > comma
        for d in ["\t", ";", "|", ","]:
            if d in line:
                return d
    except Exception:
        pass
    return ","


def _inspect_csv(fp):
    """Open a CSV/TSV with pandas; capture columns, dtypes, sample, row count.

    Tries utf-8 then latin-1 encoding. Does NOT pass any `errors=` kwarg to
    read_csv (it's been deprecated/removed in some pandas versions — a
    recurring bug in agent-written inventory scripts).
    """
    import pandas as pd

    delim = _detect_csv_delimiter(fp)
    last_err = None
    for enc in ("utf-8", "latin-1"):
        try:
            df = pd.read_csv(fp, sep=delim, nrows=5, encoding=enc)
            row_count = None
            try:
                with open(fp, "r", encoding=enc, errors="replace") as f:
                    row_count = sum(1 for _ in f) - 1  # subtract header
                if row_count < 0:
                    row_count = 0
            except Exception:
                pass
            return {
                "delimiter": delim,
                "encoding": enc,
                "columns": [str(c) for c in df.columns],
                "dtypes": {str(c): str(df[c].dtype) for c in df.columns},
                "row_count": row_count,
                "sample_rows": df.head(3).to_dict(orient="records"),
            }
        except Exception as e:
            last_err = e
            continue
    return {"error": f"{type(last_err).__name__}: {last_err}"}


def _inspect_excel(fp):
    """Open an Excel file with pandas; sheets + first-sheet columns/sample."""
    import pandas as pd

    try:
        xls = pd.ExcelFile(fp)
        sheets = xls.sheet_names
        first = pd.read_excel(fp, sheet_name=sheets[0], nrows=5)
        return {
            "sheets": sheets,
            "columns": [str(c) for c in first.columns],
            "dtypes": {str(c): str(first[c].dtype) for c in first.columns},
            "sample_rows": first.head(3).to_dict(orient="records"),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _inspect_parquet(fp):
    """Read parquet schema + first 5 rows via pyarrow."""
    try:
        import pyarrow.parquet as pq
        tbl = pq.read_table(fp)
        df = tbl.slice(0, 5).to_pandas()
        return {
            "columns": [str(c) for c in df.columns],
            "dtypes": {str(c): str(df[c].dtype) for c in df.columns},
            "row_count": tbl.num_rows,
            "sample_rows": df.head(3).to_dict(orient="records"),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _inspect_gpkg(fp):
    """List layers, then inspect the first layer for schema + sample.

    A GeoPackage (.gpkg) is a SQLite container that may hold multiple
    named layers, each with its own attribute columns plus a geometry.
    Layers are analogous to sheets in an XLSX file — enumerated in the
    `layers` field so a build script can iterate over them if needed.
    The geometry column is appended last in the reported `columns` list
    but excluded from `sample_rows` (WKT strings would blow past the
    per-cell cap and add no useful signal).
    """
    try:
        import geopandas as gpd

        # Enumerate layers. Newer geopandas has list_layers; fall back
        # through pyogrio and fiona for older installs.
        try:
            layers = list(gpd.list_layers(fp)["name"])
        except AttributeError:
            try:
                import pyogrio
                layers = [row[0] for row in pyogrio.list_layers(str(fp))]
            except ImportError:
                import fiona
                layers = list(fiona.listlayers(str(fp)))
        if not layers:
            return {"error": "no layers found in gpkg"}

        # Read a small sample from the first layer. `rows=` requires
        # geopandas 0.14+; fall back to head() on the full read for
        # older versions (slow on huge layers but correct).
        try:
            gdf = gpd.read_file(fp, layer=layers[0], rows=5)
        except TypeError:
            gdf = gpd.read_file(fp, layer=layers[0]).head(5)

        geom_col = gdf.geometry.name
        attr_cols = [c for c in gdf.columns if c != geom_col]
        cols = attr_cols + [geom_col]
        dtypes = {str(c): str(gdf[c].dtype) for c in cols}
        sample = gdf[attr_cols].head(3).to_dict(orient="records")

        result = {
            "layers": layers,
            "columns": [str(c) for c in cols],
            "dtypes": dtypes,
            "sample_rows": sample,
            "geometry_type": (str(gdf.geometry.geom_type.iloc[0])
                              if len(gdf) else None),
            "crs": str(gdf.crs) if gdf.crs else None,
        }

        # Cheap row-count via pyogrio metadata read (no data load); skip
        # silently if pyogrio isn't available.
        try:
            import pyogrio
            info = pyogrio.read_info(str(fp), layer=layers[0])
            if info.get("features") is not None:
                result["row_count"] = int(info["features"])
        except Exception:
            pass

        return result
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Schema fingerprint & grouping
# ---------------------------------------------------------------------------

def _schema_fingerprint(cols):
    """Sorted, lowercased, stripped column-name tuple."""
    return tuple(sorted(str(c).lower().strip() for c in cols))


def _cap_payload(entry):
    """Apply size caps to the per-record payload.

    Mutates `entry` in place. The full column count and the schema
    fingerprint (computed elsewhere) are unaffected — only the JSON
    payload is shrunk, to keep _inventory.json from ballooning on
    wide-format files (e.g. CRU TS with 1400+ date columns).
    """
    cols = entry.get("columns") or []
    n_cols = len(cols)
    entry["n_columns_total"] = n_cols

    # Cap stored column list and dtype dict
    if n_cols > MAX_COLS_STORED:
        entry["columns"] = list(cols[:MAX_COLS_STORED])
        entry["columns_truncated"] = True
        if "dtypes" in entry and isinstance(entry["dtypes"], dict):
            kept = list(entry["dtypes"].items())[:MAX_COLS_STORED]
            entry["dtypes"] = dict(kept)

    # Drop sample_rows for very wide tables, truncate string cells otherwise
    if "sample_rows" in entry:
        if n_cols > INCLUDE_SAMPLE_IF_COLS_LE:
            entry.pop("sample_rows", None)
            entry["sample_rows_skipped"] = (
                f"omitted: {n_cols} columns exceeds limit "
                f"({INCLUDE_SAMPLE_IF_COLS_LE})"
            )
        else:
            capped = []
            for row in entry["sample_rows"]:
                if isinstance(row, dict):
                    capped.append({
                        k: (v[:MAX_CELL_CHARS] + "…"
                            if isinstance(v, str) and len(v) > MAX_CELL_CHARS
                            else v)
                        for k, v in row.items()
                    })
                else:
                    capped.append(row)
            entry["sample_rows"] = capped

    return entry


# ---------------------------------------------------------------------------
# Main inventory walk
# ---------------------------------------------------------------------------

def inventory_repo(repo_dir):
    """Walk repo and return a list of per-file inventory records."""
    records = []
    for fp in sorted(repo_dir.rglob("*")):
        # skip files inside hidden / cache dirs
        if any(part in SKIP_DIRS for part in fp.parts):
            continue
        if not fp.is_file():
            continue
        ext = fp.suffix.lower()
        if ext not in TABULAR_EXTS:
            continue

        entry = {
            "rel_path": str(fp.relative_to(repo_dir)),
            "ext": ext,
            "size_bytes": fp.stat().st_size,
        }
        try:
            if ext in {".csv", ".tsv"}:
                entry.update(_inspect_csv(fp))
            elif ext in {".xlsx", ".xls"}:
                entry.update(_inspect_excel(fp))
            elif ext == ".parquet":
                entry.update(_inspect_parquet(fp))
            elif ext == ".gpkg":
                entry.update(_inspect_gpkg(fp))
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
        # Compute the schema fingerprint from the FULL column list before
        # capping (the cap is for storage only; the fingerprint is what
        # the stdout grouping depends on). Store as a SHA1 hex of the
        # joined fingerprint rather than the full tuple itself — for
        # wide-format files (1000+ columns) the raw tuple would defeat
        # the per-record storage cap.
        full_cols = entry.get("columns") or []
        if full_cols:
            import hashlib
            fp_tuple = _schema_fingerprint(full_cols)
            fp_str = "\x1f".join(fp_tuple)
            entry["_fingerprint"] = hashlib.sha1(fp_str.encode()).hexdigest()
            # Stash the actual tuple on the in-memory record (NOT in JSON)
            # so print_summary can show real column names for the group.
            entry["__fp_tuple"] = fp_tuple
        else:
            entry["_fingerprint"] = None
            entry["__fp_tuple"] = None
        # Cap the per-record payload before storing (keeps _inventory.json
        # small enough that an accidental agent-side read doesn't bloat
        # the conversation context — the stdout summary already has the
        # actionable groupings, and Step 5+ build scripts that DO need
        # full column lists should read the source file directly).
        _cap_payload(entry)
        records.append(entry)
    return records


# ---------------------------------------------------------------------------
# Pretty-print compact stdout summary
# ---------------------------------------------------------------------------

def _group_label(idx):
    return f"Group {chr(64 + idx)}" if 1 <= idx <= 26 else f"Group {idx}"


def print_summary(records, repo_dir, out_json):
    total = len(records)
    by_ext = Counter(r["ext"] for r in records)
    by_top = Counter(
        (r["rel_path"].split("/", 1)[0] if "/" in r["rel_path"] else "<root>")
        for r in records
    )

    # group by schema fingerprint (error-bucket separate). Use the
    # pre-computed _fingerprint hash from inventory_repo, which was
    # built from the full column list before the storage cap was applied.
    # The __fp_tuple (real column tuple, in-memory only) is used for
    # display so we can show real column names even when the per-record
    # `columns` field is truncated.
    groups = defaultdict(list)
    for r in records:
        if r.get("error") or not r.get("_fingerprint"):
            key = "__ERROR__"
        else:
            key = r["_fingerprint"]
        groups[key].append(r)

    print(f"Inventory: {total} tabular files under {repo_dir}")
    print(f"By extension: {dict(by_ext)}")
    print(f"By top-level directory: {dict(by_top)}")
    print()

    sorted_groups = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    print(f"Schema groups ({len(sorted_groups)} total, sorted by file count):")
    for idx, (fingerprint, files) in enumerate(sorted_groups, 1):
        label = _group_label(idx)
        ext_str = Counter(f["ext"] for f in files).most_common(1)[0][0]

        if fingerprint == "__ERROR__":
            print(f"\n  {label} ({len(files)} files) — files that could not be read")
            errs = list({f.get("error", "?") for f in files})[:3]
            print(f"    Errors observed: {errs}")
        else:
            # Use entry["columns"] for ORIGINAL CASING (post-cap, so it's
            # truncated to first MAX_COLS_STORED for wide files — but the
            # first 8 we display will always be present). n_columns_total
            # is the real width (set by _cap_payload).
            cols = list(files[0].get("columns") or [])
            n_cols = files[0].get("n_columns_total") or len(cols)
            head = cols[:8]
            extra = n_cols - 8
            cols_str = ", ".join(repr(c) for c in head)
            if extra > 0:
                cols_str += f", ... (+{extra} more)"
            print(f"\n  {label} ({len(files)} files, {ext_str}) — "
                  f"{n_cols} columns")
            print(f"    Columns: [{cols_str}]")

        examples = [f["rel_path"] for f in files[:3]]
        print(f"    Example files: {examples}")

        rcs = [f.get("row_count") for f in files
               if isinstance(f.get("row_count"), int)]
        if rcs:
            rcs.sort()
            median = rcs[len(rcs) // 2]
            print(f"    Row counts: min={min(rcs)}, "
                  f"max={max(rcs)}, median≈{median}")

        # for XLSX groups, mention sheet names from first file
        sheets = files[0].get("sheets")
        if sheets:
            print(f"    Sheets per file: {sheets}")

    print()
    print(f"Full inventory saved to: {out_json}")
    print("(Reference only — do NOT read this JSON for Step 3 grouping; "
          "use the groups above.)")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(argv):
    if len(argv) < 2:
        print("Usage: python inventory.py <repo-dir>", file=sys.stderr)
        sys.exit(2)

    repo_dir = Path(argv[1]).resolve()
    if not repo_dir.is_dir():
        print(f"Error: not a directory: {repo_dir}", file=sys.stderr)
        sys.exit(2)

    # If extra positional args slipped in — e.g. from `python ... 2>&1` being
    # misparsed by a kernel shim that doesn't recognize `>&` as a shell
    # operator, so tokens '2', '>&', '1' land here as argv — warn but ignore
    # rather than treating them as an output path. Previously the script
    # honored argv[2] as a custom output path, which caused JSON to be
    # written to a file literally named "2" in cwd.
    if len(argv) > 2:
        print(f"Warning: ignoring unexpected extra args: {argv[2:]}",
              file=sys.stderr)

    out_json = repo_dir / "_inventory.json"

    try:
        records = inventory_repo(repo_dir)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

    # Run print_summary BEFORE stripping the in-memory-only __fp_tuple
    # field, since the summary uses it for display.
    print_summary(records, repo_dir, out_json)

    # Strip in-memory-only fields before JSON serialization.
    for r in records:
        r.pop("__fp_tuple", None)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"records": records}, default=str, indent=2))


if __name__ == "__main__":
    main(sys.argv)
