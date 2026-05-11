"""sentinel2_l2a — Sentinel-2 L2A median composite from Microsoft Planetary Computer.

Searches sentinel-2-l2a via the Planetary Computer STAC, filters by cloud
cover, takes the N most recent scenes, windows each to the bbox in its
native UTM, resamples to a target resolution, and computes a per-band
nanmedian composite. Output is a single multiband GeoTIFF.

10 default spectral bands (visible + red-edge + NIR + SWIR), plus NDVI:
  B02 (Blue)   B03 (Green)  B04 (Red)
  B05 (RE1)    B06 (RE2)    B07 (RE3)    B8A (RE4)
  B08 (NIR)    B11 (SWIR1)  B12 (SWIR2)
  NDVI = (B08 − B04) / (B08 + B04)

No authentication needed (Planetary Computer signs URLs anonymously).
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _ensure_deps() -> None:
    missing = []
    for pkg, imp in [
        ("pystac-client", "pystac_client"),
        ("planetary-computer", "planetary_computer"),
        ("rasterio", "rasterio"),
        ("scipy", "scipy"),
    ]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[sentinel2-l2a] installing: {' '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", *missing]
        )


_ensure_deps()

import numpy as np                 # noqa: E402
import planetary_computer          # noqa: E402
import pystac_client               # noqa: E402
import rasterio                    # noqa: E402
from rasterio.transform import from_bounds as transform_from_bounds  # noqa: E402
from rasterio.warp import transform_bounds  # noqa: E402
from rasterio.windows import from_bounds as window_from_bounds        # noqa: E402
from scipy.ndimage import zoom    # noqa: E402


_DEFAULT_BANDS = [
    "B02", "B03", "B04",     # visible    (10 m native)
    "B05", "B06", "B07",     # red-edge   (20 m native)
    "B08",                   # NIR        (10 m native)
    "B8A",                   # red-edge 4 (20 m native)
    "B11", "B12",            # SWIR       (20 m native)
]

_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


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


def _read_band_window(
    href: str,
    bbox: tuple[float, float, float, float],
    resolution: int,
    ref_profile: dict | None,
) -> tuple[np.ndarray, dict]:
    """Read one band, window to bbox, resample to target resolution.

    Returns (data_array, profile). If `ref_profile` is supplied, the data
    is resampled to its (height, width); otherwise a profile is computed
    from this band's native resolution and the target_resolution.
    """
    with rasterio.open(href) as src:
        if src.crs.to_string() != "EPSG:4326":
            bbox_t = transform_bounds("EPSG:4326", src.crs, *bbox)
        else:
            bbox_t = bbox
        window = window_from_bounds(*bbox_t, transform=src.transform).round_offsets().round_lengths()
        if window.width <= 0 or window.height <= 0:
            raise ValueError("bbox does not intersect raster")
        data = src.read(1, window=window).astype("float32")

        if ref_profile is None:
            native_res = src.res[0]
            scale = native_res / resolution
            th = max(1, int(round(data.shape[0] * scale)))
            tw = max(1, int(round(data.shape[1] * scale)))
            target_transform = transform_from_bounds(*bbox_t, tw, th)
            profile = {
                "driver": "GTiff",
                "height": th,
                "width": tw,
                "count": 1,
                "dtype": "float32",
                "crs": src.crs,
                "transform": target_transform,
                "compress": "lzw",
                "tiled": True,
            }
        else:
            profile = ref_profile

        target_shape = (profile["height"], profile["width"])
        if data.shape != target_shape:
            zf = (target_shape[0] / data.shape[0], target_shape[1] / data.shape[1])
            data = zoom(data, zf, order=1)
        return data, profile


def _process_scene(
    item,
    bbox: tuple[float, float, float, float],
    bands: list[str],
    resolution: int,
    scene_idx: int,
) -> dict:
    """Read all bands of one scene. Returns dict with scene_data + profile."""
    scene_data: dict[str, np.ndarray] = {}
    profile = None
    crs = None

    for b_name in bands:
        if b_name not in item.assets:
            return {"idx": scene_idx, "data": None, "profile": None, "crs": None,
                    "error": f"missing asset {b_name}"}
        try:
            href = item.assets[b_name].href
            data, prof = _read_band_window(href, bbox, resolution, profile)
            if profile is None:
                profile = prof
                crs = prof["crs"]
            scene_data[b_name] = data
        except Exception as exc:
            return {"idx": scene_idx, "data": None, "profile": None, "crs": None,
                    "error": f"{b_name}: {exc}"[:120]}

    if len(scene_data) != len(bands):
        return {"idx": scene_idx, "data": None, "profile": None, "crs": None,
                "error": "incomplete"}
    return {"idx": scene_idx, "data": scene_data, "profile": profile, "crs": crs, "error": None}


def fetch_sentinel2_l2a(
    bbox: tuple[float, float, float, float],
    output_path: str | Path,
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_cloud_cover: float = 20.0,
    max_scenes: int = 10,
    resolution: int = 30,
    bands: list[str] | None = None,
    add_ndvi: bool = True,
    n_workers: int | None = None,
) -> Path:
    """Download a Sentinel-2 L2A median composite for a bounding box.

    Args:
        bbox:            (minx, miny, maxx, maxy) in EPSG:4326.
        output_path:     Destination GeoTIFF path.
        year:            Convenience shorthand for full calendar year.
        start_date:      "YYYY-MM-DD" — used with end_date for custom ranges.
        end_date:        "YYYY-MM-DD".
        max_cloud_cover: STAC `eo:cloud_cover` upper bound, percent (default 20).
        max_scenes:      Cap on the number of most-recent scenes to composite (10).
        resolution:      Target resolution in meters: 10, 20, 30, or 60 (default 30).
                         Bands are resampled to this resolution; smaller = larger files.
        bands:           Spectral bands to include. Default: visible + red-edge + NIR + SWIR
                         (B02, B03, B04, B05, B06, B07, B08, B8A, B11, B12).
        add_ndvi:        If True, append NDVI = (B08 - B04) / (B08 + B04) as an extra band.
        n_workers:       Parallel scene readers. Default: min(4, cpu_count).

    Returns:
        Path to the saved GeoTIFF. Bands appear in the order:
        the requested spectral bands, followed by NDVI (if add_ndvi=True).
    """
    bands = list(bands) if bands else list(_DEFAULT_BANDS)
    if add_ndvi and ("B08" not in bands or "B04" not in bands):
        raise ValueError("add_ndvi=True requires both B08 and B04 in bands")

    t_start, t_end = _parse_temporal(year=year, start_date=start_date, end_date=end_date)

    print(f"Searching Sentinel-2 L2A — bbox={bbox}  {t_start} → {t_end}  cloud<{max_cloud_cover}%")
    catalog = pystac_client.Client.open(
        _STAC_URL,
        modifier=planetary_computer.sign_inplace,
    )
    search = catalog.search(
        collections=["sentinel-2-l2a"],
        bbox=bbox,
        datetime=f"{t_start}/{t_end}",
        query={"eo:cloud_cover": {"lt": max_cloud_cover}},
    )
    items = list(search.items())
    print(f"Found {len(items)} matching scenes")
    if not items:
        raise RuntimeError(
            "No Sentinel-2 scenes match the bbox/date/cloud filter. "
            "Try a wider date range or higher cloud threshold."
        )

    items = sorted(items, key=lambda it: it.datetime, reverse=True)[:max_scenes]
    print(f"Using {len(items)} most recent scenes; target resolution = {resolution} m")

    if n_workers is None:
        try:
            import multiprocessing
            n_workers = min(4, multiprocessing.cpu_count())
        except Exception:
            n_workers = 2

    all_bands: dict[str, list[np.ndarray]] = {b: [] for b in bands}
    ref_profile: dict | None = None

    print(f"Reading scenes with {n_workers} workers ...")
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_process_scene, it, bbox, bands, resolution, i): it
            for i, it in enumerate(items)
        }
        results = []
        for fut in as_completed(futures):
            it = futures[fut]
            res = fut.result()
            results.append(res)
            scene_id = (it.id or "")[:48]
            if res["error"] is None:
                print(f"  ok  {scene_id}")
            else:
                print(f"  --  {scene_id}  ({res['error']})")

    results.sort(key=lambda r: r["idx"])
    for r in results:
        if r["data"] is None:
            continue
        if ref_profile is None:
            ref_profile = r["profile"]
            print(f"Reference CRS: {ref_profile['crs']}; "
                  f"shape={ref_profile['height']}x{ref_profile['width']}")
        for b_name, arr in r["data"].items():
            if arr.shape != (ref_profile["height"], ref_profile["width"]):
                zf = (ref_profile["height"] / arr.shape[0],
                      ref_profile["width"]  / arr.shape[1])
                arr = zoom(arr, zf, order=1)
            all_bands[b_name].append(arr)

    if ref_profile is None or any(len(v) == 0 for v in all_bands.values()):
        missing = [k for k, v in all_bands.items() if not v]
        raise RuntimeError(f"No valid scene data for bands: {missing}")

    print("Computing per-band median composite ...")
    medians: dict[str, np.ndarray] = {
        b: np.nanmedian(np.stack(arrs), axis=0) for b, arrs in all_bands.items()
    }

    if add_ndvi:
        ndvi = (medians["B08"] - medians["B04"]) / (medians["B08"] + medians["B04"] + 1e-10)
        medians["NDVI"] = ndvi.astype("float32")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    out_profile = ref_profile.copy()
    out_profile["count"] = len(medians)
    print(f"Saving {out_profile['width']}x{out_profile['height']} "
          f"× {out_profile['count']} bands → {output_path}")

    with rasterio.open(output_path, "w", **out_profile) as dst:
        for i, (name, data) in enumerate(medians.items(), start=1):
            dst.write(data.astype("float32"), i)
            dst.set_band_description(i, name)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Saved: {size_mb:.1f} MB")
    return output_path
