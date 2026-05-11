"""sentinel1_sar — Sentinel-1 SAR median composite from Microsoft Planetary Computer.

Searches `sentinel-1-rtc` (Radiometrically Terrain Corrected) first — the
preferred product because it has proper CRS metadata and is geometrically
aligned to a fixed grid. Falls back to `sentinel-1-grd` if RTC has no
matches; for GRD the CRS is taken from `proj:epsg` or computed from the
bbox UTM zone.

Output is a 2-band GeoTIFF (VV, VH) holding the per-pixel nanmedian across
the N most-recent scenes in the requested window. SAR penetrates cloud, so
no cloud filter is applied.

Why SAR for canopy height: VH backscatter is sensitive to volume scattering
in the canopy and correlates with above-ground biomass / canopy structure;
VV is sensitive to surface roughness and bare-soil signal. Together they
add information that optical (Sentinel-2) can't see once the canopy
saturates spectrally.
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
        print(f"[sentinel1-sar] installing: {' '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", *missing]
        )


_ensure_deps()

import numpy as np                 # noqa: E402
import planetary_computer          # noqa: E402
import pystac_client               # noqa: E402
import rasterio                    # noqa: E402
from pyproj import CRS             # noqa: E402
from rasterio.warp import transform_bounds          # noqa: E402
from rasterio.windows import from_bounds as window_from_bounds  # noqa: E402
from scipy.ndimage import zoom    # noqa: E402


_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
_BACKSCATTER_CLIP = (-50.0, 10.0)   # matches the reference pipeline


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


def _utm_epsg_from_bbox(bbox: tuple[float, float, float, float]) -> str:
    """Best-guess UTM EPSG from the bbox center — used as fallback for GRD."""
    lon_c = (bbox[0] + bbox[2]) / 2
    lat_c = (bbox[1] + bbox[3]) / 2
    zone = int((lon_c + 180) / 6) + 1
    return f"EPSG:{(32600 if lat_c >= 0 else 32700) + zone}"


def _read_band_window(
    href: str,
    bbox: tuple[float, float, float, float],
    fallback_crs: str | None,
    ref_shape: tuple[int, int] | None,
) -> tuple[np.ndarray, dict]:
    """Read VV or VH band, window to bbox, optionally resample to ref_shape."""
    with rasterio.open(href) as src:
        src_crs = src.crs or (CRS.from_string(fallback_crs) if fallback_crs else None)
        if src_crs is None:
            raise ValueError("source has no CRS and no fallback was provided")

        if src_crs.to_string() != "EPSG:4326":
            bbox_t = transform_bounds("EPSG:4326", src_crs, *bbox)
        else:
            bbox_t = bbox

        window = window_from_bounds(*bbox_t, transform=src.transform).round_offsets().round_lengths()
        if window.width <= 0 or window.height <= 0:
            raise ValueError("bbox does not intersect raster")

        data = src.read(1, window=window).astype("float32")
        profile = {
            "driver": "GTiff",
            "height": data.shape[0],
            "width": data.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": src_crs,
            "transform": src.window_transform(window),
            "compress": "lzw",
            "tiled": True,
        }

        if ref_shape is not None and data.shape != ref_shape:
            zf = (ref_shape[0] / data.shape[0], ref_shape[1] / data.shape[1])
            data = zoom(data, zf, order=1)
            profile["height"], profile["width"] = ref_shape

        return data, profile


def _process_scene(
    item,
    bbox,
    polarizations,
    scene_idx,
    ref_shape,
    fallback_crs,
):
    scene_data: dict[str, np.ndarray] = {}
    profile = None

    asset_pairs = []
    for pol in polarizations:
        if pol.lower() in item.assets:
            asset_pairs.append((pol, pol.lower()))
        elif pol.upper() in item.assets:
            asset_pairs.append((pol, pol.upper()))

    if len(asset_pairs) != len(polarizations):
        return {"idx": scene_idx, "data": None, "profile": None,
                "error": f"missing one of {polarizations}"}

    for pol, asset_key in asset_pairs:
        try:
            href = item.assets[asset_key].href
            data, prof = _read_band_window(
                href, bbox, fallback_crs,
                ref_shape if ref_shape is not None else (profile["height"], profile["width"]) if profile else None,
            )
            if profile is None:
                profile = prof
            scene_data[pol] = data
        except Exception as exc:
            return {"idx": scene_idx, "data": None, "profile": None,
                    "error": f"{pol}: {exc}"[:120]}

    return {"idx": scene_idx, "data": scene_data, "profile": profile, "error": None}


def _search_rtc(catalog, bbox, t_start, t_end, max_scenes):
    search = catalog.search(
        collections=["sentinel-1-rtc"],
        bbox=bbox,
        datetime=f"{t_start}/{t_end}",
    )
    items = sorted(list(search.items()), key=lambda it: it.datetime, reverse=True)[:max_scenes]
    return items


def _search_grd(catalog, bbox, t_start, t_end, max_scenes):
    search = catalog.search(
        collections=["sentinel-1-grd"],
        bbox=bbox,
        datetime=f"{t_start}/{t_end}",
    )
    items = sorted(list(search.items()), key=lambda it: it.datetime, reverse=True)[:max_scenes]
    return items


def fetch_sentinel1_sar(
    bbox: tuple[float, float, float, float],
    output_path: str | Path,
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    max_scenes: int = 5,
    polarizations: tuple[str, ...] = ("VV", "VH"),
    fallback_grd: bool = True,
    n_workers: int | None = None,
) -> Path:
    """Download a Sentinel-1 SAR median composite for a bounding box.

    Args:
        bbox:           (minx, miny, maxx, maxy) in EPSG:4326.
        output_path:    Destination GeoTIFF path.
        year:           Convenience shorthand for full calendar year.
        start_date:     "YYYY-MM-DD" — used with end_date.
        end_date:       "YYYY-MM-DD".
        max_scenes:     Most-recent scenes to composite (default 5).
        polarizations:  Bands to keep (default ("VV", "VH")).
        fallback_grd:   If RTC returns nothing, retry sentinel-1-grd
                        (with manual UTM CRS assignment if missing).
        n_workers:      ThreadPoolExecutor width. Default min(4, cpu_count).

    Returns:
        Path to the saved 2-band GeoTIFF (one band per polarization, in the
        order given). Pixel values are linear gamma_naught (RTC) or raw DN
        (GRD), clipped to [-50, 10]. Convert to dB downstream if needed:
        ``db = 10 * np.log10(linear + 1e-10)``.
    """
    polarizations = tuple(p.upper() for p in polarizations)
    t_start, t_end = _parse_temporal(year=year, start_date=start_date, end_date=end_date)

    catalog = pystac_client.Client.open(
        _STAC_URL,
        modifier=planetary_computer.sign_inplace,
    )

    print(f"Searching Sentinel-1 RTC — bbox={bbox}  {t_start} → {t_end}")
    items = _search_rtc(catalog, bbox, t_start, t_end, max_scenes)
    fallback_crs = None
    collection = "sentinel-1-rtc"

    if not items and fallback_grd:
        print("No RTC scenes; falling back to sentinel-1-grd")
        items = _search_grd(catalog, bbox, t_start, t_end, max_scenes)
        fallback_crs = _utm_epsg_from_bbox(bbox)
        collection = "sentinel-1-grd"

    if not items:
        raise RuntimeError(
            "No Sentinel-1 scenes match the bbox/date filter "
            "(neither RTC nor GRD). Try a wider date range."
        )

    print(f"Found {len(items)} scenes in {collection}")

    if n_workers is None:
        try:
            import multiprocessing
            n_workers = min(4, multiprocessing.cpu_count())
        except Exception:
            n_workers = 2

    all_bands: dict[str, list[np.ndarray]] = {p: [] for p in polarizations}
    ref_profile: dict | None = None
    ref_shape: tuple[int, int] | None = None

    print(f"Reading scenes with {n_workers} workers ...")
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {
            pool.submit(_process_scene, it, bbox, polarizations, i, ref_shape, fallback_crs): it
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
            ref_shape = (ref_profile["height"], ref_profile["width"])
            print(f"Reference CRS: {ref_profile['crs']}; shape={ref_shape[0]}x{ref_shape[1]}")
        for pol, arr in r["data"].items():
            if arr.shape != ref_shape:
                zf = (ref_shape[0] / arr.shape[0], ref_shape[1] / arr.shape[1])
                arr = zoom(arr, zf, order=1)
            all_bands[pol].append(arr)

    if ref_profile is None or any(len(v) == 0 for v in all_bands.values()):
        missing = [p for p, v in all_bands.items() if not v]
        raise RuntimeError(f"No valid scene data for polarizations: {missing}")

    print("Computing per-band median composite ...")
    medians: dict[str, np.ndarray] = {}
    for pol, arrs in all_bands.items():
        med = np.nanmedian(np.stack(arrs), axis=0)
        medians[pol] = np.clip(med, *_BACKSCATTER_CLIP).astype("float32")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    out_profile = ref_profile.copy()
    out_profile["count"] = len(medians)
    print(f"Saving {out_profile['width']}x{out_profile['height']} "
          f"× {out_profile['count']} bands → {output_path}")

    with rasterio.open(output_path, "w", **out_profile) as dst:
        for i, pol in enumerate(polarizations, start=1):
            dst.write(medians[pol], i)
            dst.set_band_description(i, pol)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Saved: {size_mb:.1f} MB")
    return output_path
