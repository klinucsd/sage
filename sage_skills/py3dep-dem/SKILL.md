---
name: py3dep-dem
description: "Use this skill for digital elevation model (DEM) requests: check available 3DEP resolutions for an area, download DEM rasters, compute terrain derivatives (slope, aspect, hillshade), sample elevation along a river centerline, overlay a river on a DEM, or compute a Relative Elevation Model (REM) for a river floodplain visualization."
---

# py3dep DEM Skill

## Required Libraries

```python
import subprocess
import sys; subprocess.run([sys.executable, "-m", "pip", "install", "-q", "py3dep", "rioxarray", "xarray"], check=True)

import py3dep
import rioxarray
import xarray as xr
```

---

## CRITICAL — Never write your own elevation sampling code

Always use `elevation_profile()` defined below. It accepts a GeoDataFrame or a
GeoJSON file path, and optionally a saved DEM TIF path. It handles all cases correctly.

Never write your own loop with rasterio — `~transform * (x, y)` swaps row/col and
produces spikes. `src.index()` called outside the `with` block silently returns wrong
results. `list(line.coords)` concatenation ignores segment direction. All produce bad profiles.

## When to Use

- When you need to fetch a DEM for a bbox, use Example 1
- When you need to check if a DEM is available for a bbox, use Example 2
- When you need to overlay a river on a DEM, use Example 3
- When you need an elevation profile along a river, use Example 4
- When you need to compute a REM, use Example 5
- When you need the full pipeline (DEM + river overlay + elevation profile + REM), use Example 6

---

## Usage

This skill defines five functions. Copy all five function definitions into your
notebook first, then call them as shown in the examples. Do not rewrite the
function bodies. For elevation profiles, always use `elevation_profile()` — it is
the only correct way and handles every case.

---

## Function Definitions — copy all five verbatim

```python
def get_dem(bbox, res=10, output_path=None):
    """
    Download a DEM for a bounding box and optionally save it.

    Parameters
    ----------
    bbox        : tuple  (west, south, east, north) in EPSG:4326
    res         : int    resolution in metres — 1, 10 (default), or 30
    output_path : str    if provided, saves dem_{res}m.tif here

    Returns
    -------
    dem : xarray.DataArray  in UTM CRS (e.g. EPSG:32611). NOT in EPSG:4326.
          All x/y coordinates are in metres.

    CRITICAL: dem is in UTM, not EPSG:4326. Any vector data (rivers, polygons)
    plotted on top of dem MUST be reprojected with .to_crs(dem.rio.crs) first,
    or it will be invisible (coordinates in degrees vs metres).
    """
    import py3dep

    dem = py3dep.get_dem(bbox, res)

    if output_path:
        dem.rio.to_raster(f"{output_path}/dem_{res}m.tif")
        print(f"get_dem: saved to {output_path}/dem_{res}m.tif")

    print(f"get_dem: shape={dem.shape}, CRS={dem.rio.crs}, "
          f"elevation {float(dem.min()):.0f}–{float(dem.max()):.0f} m")
    return dem


def sample_elevation(river_line, main_channel, dem):
    """
    Sample elevation along the river centerline and return river_elev in UTM.

    DO NOT reimplement this function. The common alternative — opening the saved
    GeoTIFF with rasterio and using `~transform * (x, y)` — returns (col, row)
    not (row, col), causing large random elevation spikes in the profile.
    Always call this function.

    Parameters
    ----------
    river_line   : LineString   output of get_main_channel() from nhd-rivers skill,
                                in EPSG:4326
    main_channel : GeoDataFrame output of get_main_channel() from nhd-rivers skill
    dem          : xarray.DataArray  from get_dem(), in UTM CRS

    Returns
    -------
    river_elev : ndarray  shape (N, 3) — [utm_x, utm_y, elevation_m]
                 CRS matches dem.rio.crs. Required input for compute_rem().
    distances  : ndarray  shape (N,) — along-channel distances in metres,
                 starting at 0.
    """
    import numpy as np
    import rasterio
    import shapely
    import pygeoutils

    # Resample to 10 m spacing.
    # river_line.length is in degrees — multiply by 111_000 to convert to metres.
    # Do NOT use river_line.length / 10 directly — that gives ~1000x too few points.
    npts = int(np.ceil(river_line.length * 111_000 / 10))
    river_line_smooth = pygeoutils.smooth_linestring(river_line, 0.1, npts)

    # Sample elevation from USGS seamless 10 m DEM VRT
    url = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
    with rasterio.open(url) as src:
        # Transform river coords from EPSG:4326 to raster CRS before sampling
        xy_raster = shapely.get_coordinates(
            pygeoutils.geo_transform(river_line_smooth, main_channel.crs, src.crs)
        )
        # src.sample yields one (1,) array per point — extract scalar value
        z = np.array([val[0] for val in src.sample(xy_raster)])

    # Build river_elev in UTM (same CRS as dem) — NOT in EPSG:4326 degrees.
    # Reproject river_line_smooth to dem.rio.crs so x/y are in UTM metres.
    river_line_utm = pygeoutils.geo_transform(river_line_smooth, main_channel.crs, dem.rio.crs)
    xy_utm = shapely.get_coordinates(river_line_utm)

    assert len(xy_utm) == len(z), (
        f"Coordinate/elevation mismatch: {len(xy_utm)} coords vs {len(z)} elevations"
    )

    river_elev = np.c_[xy_utm, z]

    # Along-channel distances in metres, reset to start at 0
    pts = shapely.points(river_line_smooth.coords)
    distances = shapely.line_locate_point(river_line_smooth, pts)
    distances = distances - distances[0]

    print(f"sample_elevation: {len(river_elev)} points, "
          f"elevation {z.min():.0f}–{z.max():.0f} m, "
          f"length {distances[-1]/1000:.1f} km")
    return river_elev, distances


def plot_river_on_dem(dem, main_channel, river_name, output_path):
    """
    Overlay the main channel on the DEM and save the figure.

    Parameters
    ----------
    dem          : xarray.DataArray  from get_dem(), in UTM CRS
    main_channel : GeoDataFrame  output of get_main_channel() from nhd-rivers skill
    river_name   : str  used for the plot title
    output_path  : str  directory where river_channel.png is saved

    CRITICAL: main_channel is always reprojected to dem.rio.crs inside this
    function. Never plot vector data on a DEM without reprojecting — it will
    be invisible with no error or warning.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
    dem.plot(ax=ax, robust=True, cmap='terrain')
    # Reproject to DEM CRS — never plot vector data without this
    main_channel.to_crs(dem.rio.crs).plot(
        ax=ax, color='red', linewidth=1.5, label=river_name.title()
    )
    ax.set_title(f"{river_name.title()} — main channel")
    ax.legend()
    plt.tight_layout()
    plt.savefig(f"{output_path}/river_channel.png", dpi=100, bbox_inches='tight')
    plt.close()
    print(f"plot_river_on_dem: saved to {output_path}/river_channel.png")


def compute_rem(dem, river_elev, output_path):
    """
    Compute a Relative Elevation Model (REM) using IDW interpolation and
    visualize it with datashader (hillshade base + inferno REM overlay).

    Parameters
    ----------
    dem         : xarray.DataArray  from get_dem(), in UTM CRS
    river_elev  : ndarray  shape (N, 3) — [utm_x, utm_y, elevation_m]
                  output of sample_elevation(), in the SAME UTM CRS as dem
    output_path : str  directory where rem.png is saved

    Returns
    -------
    rem : xarray.DataArray  REM values in metres, same grid as dem

    DO NOT substitute a different interpolation method, colormap, or
    visualization library. The output will look wrong — a generic elevation
    map instead of a floodplain visualization.
    """
    import numpy as np
    import xarray as xr
    from scipy.spatial import KDTree
    import opt_einsum as oe
    from pathlib import Path

    import subprocess, sys
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "scipy", "opt_einsum", "xarray-spatial", "datashader"],
        check=True
    )

    # xarray-spatial installs as 'xarray-spatial' but imports as 'xrspatial'
    import xrspatial as xs
    import datashader.transfer_functions as tf
    from datashader.colors import Greys9, inferno
    from datashader import utils as ds_utils

    assert river_elev.ndim == 2 and river_elev.shape[1] == 3, \
        "river_elev must be shape (N, 3): [utm_x, utm_y, elevation_m]"

    # k must not exceed the number of river points
    k = min(200, len(river_elev))

    # IDW trend surface — KDTree.query returns (distances, indices)
    grid_coords = np.dstack(np.meshgrid(dem.x, dem.y)).reshape(-1, 2)
    distances, idxs = KDTree(river_elev[:, :2]).query(grid_coords, k=k, workers=-1)

    w = np.reciprocal(np.power(distances, 2) + np.isclose(distances, 0))
    w_sum = np.sum(w, axis=1)
    w_norm = oe.contract(
        "ij,i->ij", w,
        np.reciprocal(w_sum + np.isclose(w_sum, 0)),
        optimize="optimal"
    )
    elevation = oe.contract("ij,ij->i", w_norm, river_elev[idxs, 2], optimize="optimal")
    elevation = elevation.reshape((dem.sizes["y"], dem.sizes["x"]))
    elevation = xr.DataArray(elevation, dims=("y", "x"), coords={"x": dem.x, "y": dem.y})

    rem = (dem - elevation).clip(min=0)

    # Datashader visualization: greyscale DEM + hillshade + inferno REM
    illuminated = xs.hillshade(dem, angle_altitude=10, azimuth=90)
    tf.Image.border = 0
    img = tf.stack(
        tf.shade(dem,         cmap=Greys9,            how="linear"),
        tf.shade(illuminated, cmap=["black", "white"], how="linear", alpha=180),
        tf.shade(rem,         cmap=inferno[::-1],      span=[0, 7],  how="log", alpha=200),
    )
    ds_utils.export_image(img[::-1], Path(output_path, "rem").as_posix())
    print(f"compute_rem: saved to {Path(output_path, 'rem.png')}")
    return rem


def elevation_profile(river_source, output_path, river_name="River", dem_source=None, res=30):
    """
    Compute and plot an elevation profile for a river. Handles all cases.

    Parameters
    ----------
    river_source : GeoDataFrame or str
                   River segments as a GeoDataFrame, or a file path to a GeoJSON.
                   Works with NHD output, saved files, or any other source.
    output_path  : str   directory where elevation_profile.png is saved
    river_name   : str   used in the plot title (default "River")
    dem_source   : str or None
                   Path to a saved DEM GeoTIFF (e.g. "dem_30m.tif"), or None to
                   download the DEM automatically.
    res          : int   DEM resolution in metres if downloading (default 30)

    Returns
    -------
    river_elev : ndarray  shape (N, 3) — [utm_x, utm_y, elevation_m]
    distances  : ndarray  shape (N,) — along-channel distances in metres
    """
    import numpy as np
    import matplotlib.pyplot as plt
    import geopandas as gpd
    import rioxarray
    from shapely import ops

    # Accept file path or GeoDataFrame
    if isinstance(river_source, str):
        river_gdf = gpd.read_file(river_source)
    else:
        river_gdf = river_source.copy()

    # Ensure EPSG:4326
    if river_gdf.crs is None:
        river_gdf = river_gdf.set_crs("EPSG:4326")
    elif river_gdf.crs.to_epsg() != 4326:
        river_gdf = river_gdf.to_crs("EPSG:4326")

    # Sort by hydroseq if available (NHD flow order)
    if 'hydroseq' in river_gdf.columns:
        river_gdf = river_gdf.sort_values('hydroseq', ascending=True)

    # Merge segments into a single LineString
    segments = river_gdf.explode(index_parts=False).reset_index(drop=True)
    river_line = ops.linemerge(segments.geometry.tolist())
    if river_line.geom_type != 'LineString':
        river_line = max(river_line.geoms, key=lambda g: g.length)
        print(f"elevation_profile: segments not fully connected — "
              f"using longest piece (~{river_line.length * 111:.0f} km)")

    # Load or download DEM
    if dem_source is not None:
        dem = rioxarray.open_rasterio(dem_source, masked=True).squeeze()
        dem = dem.rename({'band': 'elevation'}) if 'band' in dem.dims else dem
    else:
        west, south, east, north = river_gdf.total_bounds
        dem = get_dem((west, south, east, north), res=res)

    # Sample elevation and plot
    river_elev, distances = sample_elevation(river_line, river_gdf, dem)

    fig, ax = plt.subplots(figsize=(14, 8), dpi=150)
    ax.plot(distances / 1000, river_elev[:, 2], linewidth=1.5, color='steelblue')
    ax.set_xlabel('Distance along river (km)')
    ax.set_ylabel('Elevation (m)')
    ax.set_title(f'{river_name} — Longitudinal Elevation Profile')
    z_min, z_max = np.nanmin(river_elev[:, 2]), np.nanmax(river_elev[:, 2])
    ax.set_ylim(z_min - (z_max - z_min) * 0.05, z_max + (z_max - z_min) * 0.05)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{output_path}/elevation_profile.png", dpi=150, bbox_inches='tight')
    plt.close()
    print(f"elevation_profile: saved to {output_path}/elevation_profile.png")

    return river_elev, distances
```

---

## Example 1 — Download DEM and display it

```python
bbox = (-119.59, 39.24, -119.47, 39.30)   # replace with actual bbox

dem = get_dem(bbox, res=10, output_path=output_path)

import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(8, 6))
dem.plot(ax=ax, robust=True, cmap='terrain')
plt.tight_layout()
plt.savefig(f"{output_path}/dem.png", dpi=100, bbox_inches='tight')
plt.close()
```

---

## Example 2 — Check available DEM resolutions

```python
import py3dep
bbox = (-119.59, 39.24, -119.47, 39.30)
availability = py3dep.check_3dep_availability(bbox)
# {'1m': True, '10m': True, '30m': True, ...}
# 1 m (Lidar) = best detail; 10 m = good default; 30 m = large regions

# WRONG — do not check availability by downloading the DEM at each resolution
# for res in [1, 10, 30]:
#     try:
#         dem = py3dep.get_dem(bbox, res)   # slow and wasteful
```

---

## Example 3 — Overlay vector data on DEM

```python
dem = get_dem(bbox, res=10, output_path=output_path)

# nhd-rivers skill
flw                      = fetch_flowlines(bbox)
river_line, main_channel = get_main_channel(flw, 'carson river')

plot_river_on_dem(dem, main_channel, 'carson river', output_path)
```

---

## Example 4 — Elevation profile along a river

```python
# Case A: river from a saved GeoJSON file (most common when reusing previous output)
river_elev, distances = elevation_profile(
    f"{output_path}/carson_river_main_channel.geojson",
    output_path,
    river_name="Carson River",
    dem_source=f"{output_path}/dem_30m.tif",   # omit to download automatically
)

# Case B: river GeoDataFrame already in memory (e.g. from nhd-rivers skill)
# flw = fetch_flowlines(bbox)
# river_line, main_channel = get_main_channel(flw, 'carson river')
# river_elev, distances = elevation_profile(main_channel, output_path, river_name="Carson River")
```

---

## Example 5 — Compute REM

```python
dem = get_dem(bbox, res=10, output_path=output_path)

# nhd-rivers skill
flw                      = fetch_flowlines(bbox)
river_line, main_channel = get_main_channel(flw, 'carson river')

river_elev, distances = sample_elevation(river_line, main_channel, dem)
rem = compute_rem(dem, river_elev, output_path)
```

---

## Example 6 — Full pipeline: DEM + river overlay + elevation profile + REM

```python
output_path = "/home/jovyan/work/rem_output"   # set explicitly
bbox        = (-119.59, 39.24, -119.47, 39.30)

# DEM
dem = get_dem(bbox, res=10, output_path=output_path)

# River geometry — nhd-rivers skill
flw                      = fetch_flowlines(bbox)
river_line, main_channel = get_main_channel(flw, 'carson river')

# Elevation — py3dep-dem skill
river_elev, distances    = sample_elevation(river_line, main_channel, dem)
plot_river_on_dem(dem, main_channel, 'carson river', output_path)
rem                      = compute_rem(dem, river_elev, output_path)
```

---

## Notes

- `elevation_profile()` is the only correct way to compute an elevation profile. Pass a file path or GeoDataFrame; pass a saved DEM TIF path or omit to download automatically.
- `get_dem()` returns UTM, not EPSG:4326. Always use `.to_crs(dem.rio.crs)` before plotting any vector layer on top — or use `plot_river_on_dem()` which handles this automatically.
- `compute_rem()` uses KDTree IDW + datashader. Do not substitute `scipy.ndimage`, `matplotlib`, `viridis`, or any other method — the result will be a generic elevation map, not a floodplain visualization.
- `xarray-spatial` installs as `xarray-spatial` but **imports as `xrspatial`** — not `xarray_spatial`.
- `output_path` must be defined as a string before calling any function.
