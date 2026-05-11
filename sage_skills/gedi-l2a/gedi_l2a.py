"""gedi_l2a — download GEDI L2A canopy height data for a bounding box.

Uses earthaccess for NASA Earthdata authentication, granule search, and
download. Granules are downloaded to a temp directory, canopy-height fields
extracted, then the HDF5 files are deleted. No streaming — streaming via
h5py remote is too slow for practical use.

Quality filter applied per-shot:
  quality_flag == 1  AND  degrade_flag == 0  AND  0 < rh98 < 130
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _ensure_deps() -> None:
    missing = []
    for pkg, imp in [("earthaccess", "earthaccess"), ("h5py", "h5py")]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[gedi-l2a] installing: {' '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q"] + missing
        )


_ensure_deps()

import earthaccess  # noqa: E402  (after install guard)
import h5py         # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd # noqa: E402


# RH percentile indices to extract from the 101-element rh array
_RH_INDICES = {
    "rh25": 25, "rh50": 50, "rh75": 75,
    "rh95": 95, "rh98": 98, "rh100": 100,
}


def _parse_temporal(
    year: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, str]:
    if start_date and end_date:
        return start_date, end_date
    if year:
        return f"{year}-01-01", f"{year}-12-31"
    raise ValueError("Provide year= or both start_date= and end_date=")


def _extract_beam(
    f: h5py.File,
    beam: str,
    bbox: tuple[float, float, float, float],
) -> pd.DataFrame | None:
    """Return a DataFrame of valid shots from one beam, or None if empty."""
    try:
        quality  = f[f"{beam}/quality_flag"][:]
        degrade  = f[f"{beam}/degrade_flag"][:]
        rh_all   = f[f"{beam}/rh"][:]
        rh98     = rh_all[:, 98]
        lat      = f[f"{beam}/lat_lowestmode"][:]
        lon      = f[f"{beam}/lon_lowestmode"][:]

        valid = (
            (quality == 1) & (degrade == 0) &
            (rh98 > 0) & (rh98 < 130) &
            (lon >= bbox[0]) & (lon <= bbox[2]) &
            (lat >= bbox[1]) & (lat <= bbox[3])
        )
        if not np.any(valid):
            return None

        rh_v = rh_all[valid]
        row = {
            "beam":              f[f"{beam}/beam"][valid],
            "shot_number":       f[f"{beam}/shot_number"][valid],
            "delta_time":        f[f"{beam}/delta_time"][valid],
            "latitude":          lat[valid],
            "longitude":         lon[valid],
            "elev_highestreturn":f[f"{beam}/elev_highestreturn"][valid],
            "elev_lowestmode":   f[f"{beam}/elev_lowestmode"][valid],
            "sensitivity":       f[f"{beam}/sensitivity"][valid],
            "quality_flag":      quality[valid],
            "degrade_flag":      degrade[valid],
        }
        for col, idx in _RH_INDICES.items():
            row[col] = rh_v[:, idx]

        return pd.DataFrame(row)

    except Exception as exc:
        print(f"  [gedi-l2a] beam {beam}: {exc}")
        return None


def _extract_granule(
    h5_path: Path,
    bbox: tuple[float, float, float, float],
) -> pd.DataFrame | None:
    frames = []
    try:
        with h5py.File(h5_path, "r") as f:
            beams = [k for k in f.keys() if k.startswith("BEAM")]
            for beam in beams:
                df = _extract_beam(f, beam, bbox)
                if df is not None:
                    frames.append(df)
    except Exception as exc:
        print(f"  [gedi-l2a] cannot open {h5_path.name}: {exc}")
        return None
    return pd.concat(frames, ignore_index=True) if frames else None


def fetch_gedi_l2a(
    bbox: tuple[float, float, float, float],
    output_path: str | Path,
    earthdata_user: str | None = None,
    earthdata_pass: str | None = None,
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    batch_size: int = 8,
) -> pd.DataFrame:
    """Download GEDI L2A canopy height data for a bounding box.

    Args:
        bbox:           (minx, miny, maxx, maxy) in EPSG:4326.
        output_path:    Destination CSV file path.
        earthdata_user: NASA Earthdata username. Falls back to
                        EARTHDATA_USERNAME environment variable.
        earthdata_pass: NASA Earthdata password. Falls back to
                        EARTHDATA_PASSWORD environment variable.
        year:           Convenience shorthand — downloads the full
                        calendar year (e.g. year=2024).
        start_date:     "YYYY-MM-DD" — used together with end_date when
                        a custom date range is needed instead of year.
        end_date:       "YYYY-MM-DD".
        batch_size:     Granules to download per batch (default 8).

    Returns:
        pd.DataFrame with columns:
            beam, shot_number, delta_time,
            latitude, longitude,
            elev_highestreturn, elev_lowestmode,
            rh25, rh50, rh75, rh95, rh98, rh100,
            sensitivity, quality_flag, degrade_flag

        Also written to output_path as CSV.
    """
    if earthdata_user:
        os.environ["EARTHDATA_USERNAME"] = earthdata_user
    if earthdata_pass:
        os.environ["EARTHDATA_PASSWORD"] = earthdata_pass

    if not os.environ.get("EARTHDATA_USERNAME") or not os.environ.get("EARTHDATA_PASSWORD"):
        raise ValueError(
            "Earthdata credentials missing. Pass earthdata_user/earthdata_pass "
            "or set EARTHDATA_USERNAME / EARTHDATA_PASSWORD in the environment."
        )

    t_start, t_end = _parse_temporal(year=year, start_date=start_date, end_date=end_date)

    print(f"Logging into NASA Earthdata ...")
    earthaccess.login(strategy="environment")

    print(f"Searching GEDI L2A granules — bbox={bbox}  {t_start} → {t_end}")
    results = earthaccess.search_data(
        short_name="GEDI02_A",
        version="002",
        bounding_box=tuple(bbox),
        temporal=(t_start, t_end),
    )
    print(f"Found {len(results)} granules")
    if not results:
        print("No granules found for the given bbox/temporal range.")
        return pd.DataFrame()

    temp_dir = Path(tempfile.mkdtemp(prefix="gedi_l2a_"))
    all_frames: list[pd.DataFrame] = []
    total = len(results)

    try:
        for batch_start in range(0, total, batch_size):
            batch     = results[batch_start : batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            n_batches = (total + batch_size - 1) // batch_size
            print(f"\nBatch {batch_num}/{n_batches}: downloading {len(batch)} granules ...")

            try:
                downloaded = earthaccess.download(batch, str(temp_dir))
            except Exception as exc:
                print(f"  Download error: {exc} — skipping batch")
                continue

            for h5_path in [Path(p) for p in downloaded]:
                if not h5_path.exists():
                    continue
                if h5_path.stat().st_size < 1_000_000:
                    print(f"  {h5_path.name}: too small, skipping")
                    h5_path.unlink(missing_ok=True)
                    continue

                df = _extract_granule(h5_path, bbox)
                if df is not None and not df.empty:
                    all_frames.append(df)
                    print(f"  {h5_path.name}: {len(df):,} shots")
                else:
                    print(f"  {h5_path.name}: no valid shots in bbox")

                h5_path.unlink(missing_ok=True)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if not all_frames:
        print("\nNo valid shots found in the requested bbox/time range.")
        return pd.DataFrame()

    combined = (
        pd.concat(all_frames, ignore_index=True)
        .sort_values("delta_time")
        .reset_index(drop=True)
    )
    print(f"\nTotal valid shots: {len(combined):,}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output_path, index=False)
    print(f"Saved → {output_path}")
    return combined
