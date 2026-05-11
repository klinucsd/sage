"""cop30_topo — Copernicus COP30 (or SRTM / AW3D30) DEM + slope + aspect from OpenTopography.

Fetches a single merged GeoTIFF from OpenTopography's GlobalDEM API for the
requested bbox, reprojects it from EPSG:4326 to the local UTM zone at the
target pixel size, then computes slope and aspect from the gradient on the
square-pixel target grid.

Source: https://portal.opentopography.org/API/globaldem
DEM types supported (`demtype` argument):
  - "COP30"   — Copernicus 30 m Global DEM (default; TanDEM-X derived)
  - "SRTMGL1" — SRTM 1 arc-second (~30 m)
  - "SRTMGL3" — SRTM 3 arc-second (~90 m)
  - "AW3D30"  — ALOS World 3D 30 m

Authentication: an OpenTopography API key is required for the GlobalDEM
endpoint. Pass it via `api_key=` or set `OPENTOPOGRAPHY_API_KEY` in the
environment. Get a free key at https://portal.opentopography.org.

Why reprojection-then-derivative: COP30 is delivered in EPSG:4326, which
has different x and y pixel sizes (in meters) at any non-equator latitude.
Computing slope on the native lat/lon grid systematically biases the
gradient. Reprojecting to UTM first (square meter-pixels) yields the
correct gradient.

Output is a 3-band GeoTIFF: elevation (m), slope (degrees), aspect
(degrees, 0=North, clockwise).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _ensure_deps() -> None:
    missing = []
    for pkg, imp in [
        ("requests", "requests"),
        ("rasterio", "rasterio"),
    ]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[cop30-topo] installing: {' '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", *missing]
        )


_ensure_deps()

import numpy as np                                                         # noqa: E402
import rasterio                                                            # noqa: E402
import requests                                                            # noqa: E402
from pyproj import CRS                                                     # noqa: E402
from rasterio.enums import Resampling                                      # noqa: E402
from rasterio.transform import from_bounds as transform_from_bounds        # noqa: E402
from rasterio.warp import reproject, transform_bounds                      # noqa: E402


_OPENTOPO_URL = "https://portal.opentopography.org/API/globaldem"
_SUPPORTED_DEMTYPES = {"COP30", "SRTMGL1", "SRTMGL3", "AW3D30"}


def _utm_epsg_from_bbox(bbox: tuple[float, float, float, float]) -> str:
    """Local UTM zone EPSG for the bbox center."""
    lon_c = (bbox[0] + bbox[2]) / 2
    lat_c = (bbox[1] + bbox[3]) / 2
    zone = int((lon_c + 180) / 6) + 1
    return f"EPSG:{(32600 if lat_c >= 0 else 32700) + zone}"


def _compute_slope_aspect(
    dem: np.ndarray, pixel_size: float
) -> tuple[np.ndarray, np.ndarray]:
    """Slope (degrees, 0–90) and aspect (degrees, 0=N clockwise) from a square-pixel DEM."""
    dy, dx = np.gradient(dem, pixel_size)
    slope = np.degrees(np.arctan(np.sqrt(dx ** 2 + dy ** 2)))
    aspect_rad = np.arctan2(-dy, dx)
    aspect = (90.0 - np.degrees(aspect_rad)) % 360.0
    return slope.astype("float32"), aspect.astype("float32")


def _download_from_opentopo(
    bbox: tuple[float, float, float, float],
    demtype: str,
    api_key: str,
    dst_path: Path,
) -> None:
    """Stream a GeoTIFF from OpenTopography GlobalDEM API to disk."""
    params = {
        "demtype": demtype,
        "south": bbox[1],
        "north": bbox[3],
        "west":  bbox[0],
        "east":  bbox[2],
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }
    print(f"Requesting {demtype} from OpenTopography ...")
    with requests.get(_OPENTOPO_URL, params=params, stream=True, timeout=600) as resp:
        if resp.status_code == 401:
            raise RuntimeError(
                "OpenTopography returned 401 — invalid or missing API key. "
                "Get one at https://portal.opentopography.org and set "
                "OPENTOPOGRAPHY_API_KEY."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenTopography request failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        chunk = 64 * 1024
        with open(dst_path, "wb") as f:
            for block in resp.iter_content(chunk):
                f.write(block)
                downloaded += len(block)
                if total > 0:
                    pct = downloaded / total * 100
                    print(f"\r  {downloaded/1e6:.1f} / {total/1e6:.1f} MB ({pct:.1f}%)",
                          end="", flush=True)
        print()


def fetch_cop30_topo(
    bbox: tuple[float, float, float, float],
    output_path: str | Path,
    demtype: str = "COP30",
    api_key: str | None = None,
    target_crs: str | None = None,
    pixel_size: float = 30.0,
    add_slope: bool = True,
    add_aspect: bool = True,
) -> Path:
    """Download a COP30 elevation tile (+ slope + aspect) from OpenTopography.

    Args:
        bbox:        (minx, miny, maxx, maxy) in EPSG:4326.
        output_path: Destination GeoTIFF path.
        demtype:     One of "COP30" (default), "SRTMGL1", "SRTMGL3", "AW3D30".
        api_key:     OpenTopography API key. Falls back to OPENTOPOGRAPHY_API_KEY env var.
        target_crs:  Output CRS — anything accepted by `pyproj.CRS.from_string`.
                     Defaults to the local UTM zone derived from the bbox center.
        pixel_size:  Output pixel size in meters (default 30, matching COP30 native).
        add_slope:   Append slope (degrees) as band 2.
        add_aspect:  Append aspect (degrees, 0=N clockwise) as band 3.

    Returns:
        Path to the saved GeoTIFF. Bands in order:
        elevation [m], slope [deg], aspect [deg].

    Notes:
        - Slope/aspect are computed in the OUTPUT CRS where pixels are square.
          Never trust slope/aspect computed in EPSG:4326 — anisotropic pixels
          bias the gradient.
        - The output may not pixel-align with your Sentinel-2 / Sentinel-1
          GeoTIFFs — at feature-extraction or prediction time, reproject this
          file to the S2 grid using `rasterio.warp.reproject(..., resampling=Resampling.bilinear)`.
    """
    if demtype not in _SUPPORTED_DEMTYPES:
        raise ValueError(
            f"Unsupported demtype {demtype!r}. "
            f"Must be one of: {sorted(_SUPPORTED_DEMTYPES)}"
        )

    api_key = api_key or os.environ.get("OPENTOPOGRAPHY_API_KEY")
    if not api_key:
        raise ValueError(
            "Missing OpenTopography API key. Pass api_key=... or set "
            "OPENTOPOGRAPHY_API_KEY in the environment / .env."
        )

    target_crs_str = target_crs or _utm_epsg_from_bbox(bbox)
    target_crs_obj = CRS.from_string(target_crs_str)

    print(f"Downloading {demtype} for bbox={bbox}")
    print(f"  target_crs={target_crs_str}  pixel_size={pixel_size} m")

    # --- 1) Pull the merged DEM tile from OpenTopography -------------------
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        _download_from_opentopo(bbox, demtype, api_key, tmp_path)

        with rasterio.open(tmp_path) as src:
            src_crs = src.crs
            src_transform = src.transform
            src_nodata = src.nodata
            src_data = src.read(1)
            print(f"  Native shape: {src.height}x{src.width}; CRS={src_crs}; "
                  f"res={src.res}")
    finally:
        # Keep the temp file around only if reprojection fails for debugging? No — clean up.
        try:
            tmp_path.unlink()
        except OSError:
            pass

    # --- 2) Compute target grid in target_crs ------------------------------
    bbox_t = transform_bounds("EPSG:4326", target_crs_obj, *bbox)
    minx = (bbox_t[0] // pixel_size) * pixel_size
    miny = (bbox_t[1] // pixel_size) * pixel_size
    maxx = ((bbox_t[2] // pixel_size) + 1) * pixel_size
    maxy = ((bbox_t[3] // pixel_size) + 1) * pixel_size
    width = int((maxx - minx) / pixel_size)
    height = int((maxy - miny) / pixel_size)
    target_transform = transform_from_bounds(minx, miny, maxx, maxy, width, height)
    print(f"  Target grid: {width}x{height} in {target_crs_str}")

    # --- 3) Reproject native DEM into target grid --------------------------
    dem = np.full((height, width), np.nan, dtype="float32")
    reproject(
        source=src_data,
        destination=dem,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=target_transform,
        dst_crs=target_crs_obj,
        resampling=Resampling.bilinear,
        src_nodata=src_nodata,
        dst_nodata=np.nan,
    )

    # --- 4) Compute slope and aspect on the square-pixel target grid -------
    bands = [dem]
    descriptions = ["elevation"]
    if add_slope or add_aspect:
        print("  Computing slope/aspect ...")
        slope, aspect = _compute_slope_aspect(dem, pixel_size)
        if add_slope:
            bands.append(slope)
            descriptions.append("slope")
        if add_aspect:
            bands.append(aspect)
            descriptions.append("aspect")

    # --- 5) Save GeoTIFF ---------------------------------------------------
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": len(bands),
        "dtype": "float32",
        "crs": target_crs_obj,
        "transform": target_transform,
        "compress": "lzw",
        "tiled": True,
        "nodata": np.nan,
    }
    print(f"  Saving {width}x{height} × {len(bands)} bands → {output_path}")
    with rasterio.open(output_path, "w", **profile) as dst:
        for i, (arr, desc) in enumerate(zip(bands, descriptions), start=1):
            dst.write(arr.astype("float32"), i)
            dst.set_band_description(i, desc)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Saved: {size_mb:.1f} MB")
    return output_path
