"""
usgs_lidar — pure data helpers for the USGS 3DEP LiDAR skill.

Two skill-agnostic data primitives:
  * fetch_coverage(output_path) — download the USGS 3DEP coverage GeoJSON,
    optionally save it to a file (so it can be used as a sage-bbox-map
    overlay), return a GeoDataFrame for in-memory queries.
  * filter_by_bbox(coverage, bbox, max_points) — given a GeoDataFrame and a
    bbox tuple, return a list of dicts describing the intersecting datasets.

Both functions are GUI-free. They're imported by the agent-generated scripts
that compose this skill with sage-bbox-map (area selection) and sage-dropdown
(dataset selection).
"""

import json
import random
from pathlib import Path

import geopandas as gpd
import pyproj
import requests
from shapely.geometry import box


# Reproducible 27-color categorical palette mirroring the USGS 3DEP web app.
# Used to color individual coverage polygons so the user can visually
# distinguish overlapping datasets when a sage-bbox-map overlay is rendered.
_COLORS = [
    "#8c510a", "#bf812d", "#c7eae5", "#80cdc1", "#35978f", "#01665e", "#762a83",
    "#9970ab", "#d9f0d3", "#a6dba0", "#5aae61", "#1b7837", "#b35806", "#e08214",
    "#fdb863", "#d8daeb", "#b2abd2", "#8073ac", "#542788", "#377eb8", "#4daf4a",
    "#984ea3", "#ff7f00", "#ffff33", "#a65628", "#f781bf", "#999999",
]


def fetch_coverage(color_features=True):
    """Download the USGS 3DEP coverage GeoJSON and return a GeoDataFrame.

    Returns the catalog as an in-memory GeoDataFrame only — no file is
    written. Pass the result directly as `overlay_geojson` to
    `sage_bbox_map.show_bbox_map`. (Writing the catalog to SAGE_OUTPUT_DIR
    would trigger Sage's auto-Folium fallback at the end of the cell,
    producing a duplicate static map next to the live ipyleaflet widget.)

    Args:
        color_features: if True, write a reproducible random color into each
            feature's `properties._color`. sage-bbox-map honors this for
            per-feature coloring; without it the overlay would be a single
            color.

    Returns:
        GeoDataFrame in EPSG:4326 with one row per dataset. Important columns:
        `name` (dataset name), `url` (EPT endpoint), `count` (total point
        count for the dataset), `geometry` (Polygon or MultiPolygon).
    """
    resp = requests.get(
        "https://usgs.entwine.io/boundaries/resources.geojson", timeout=30
    )
    resp.raise_for_status()
    data = resp.json()

    if color_features:
        random.seed(42)
        for feat in data["features"]:
            feat.setdefault("properties", {})["_color"] = random.choice(_COLORS)

    gdf = gpd.GeoDataFrame.from_features(
        [f for f in data["features"]
         if f["geometry"]["type"] in ("Polygon", "MultiPolygon")],
        crs="EPSG:4326",
    )
    return gdf


def filter_by_bbox(coverage, bbox, max_points=20_000_000):
    """Return datasets intersecting `bbox` whose estimated point count is below max_points.

    The estimated point count for a clipped dataset is computed as
    `dataset_count * (clip_area / dataset_area)`, where the areas are computed
    on a WGS84 ellipsoid. Datasets above `max_points` are excluded so the user
    cannot accidentally request a download with hundreds of millions of
    points.

    Args:
        coverage: GeoDataFrame returned by `fetch_coverage()`.
        bbox: 4-tuple `(minx, miny, maxx, maxy)` in EPSG:4326.
        max_points: skip datasets whose estimated bbox-clipped point count
            exceeds this threshold (default 20 million).

    Returns:
        List of dicts: `[{"name", "url", "count", "est"}]`, where `count` is
        the dataset's total point count and `est` is the bbox-clipped
        estimate. Empty list if no datasets intersect (or all are too large).
    """
    if bbox is None:
        raise ValueError("filter_by_bbox: bbox is None — draw a rectangle first")
    minx, miny, maxx, maxy = bbox
    query_geom = box(minx, miny, maxx, maxy)

    intersecting = coverage[coverage.intersects(query_geom)]
    if intersecting.empty:
        return []

    geod = pyproj.Geod(ellps="WGS84")
    matches = []
    for _, row in intersecting.iterrows():
        url = row.get("url", "")
        name = row.get("name", "Unknown")
        if not url:
            continue
        count = row.get("count") or 0
        poly = row.geometry
        if count and poly and not poly.is_empty:
            dataset_area = abs(geod.geometry_area_perimeter(poly)[0])
            clip = query_geom.intersection(poly)
            clip_area = (abs(geod.geometry_area_perimeter(clip)[0])
                         if not clip.is_empty else 0)
            est = int(count * clip_area / dataset_area) if dataset_area > 0 else 0
        else:
            est = 0
        if est > max_points:
            continue
        matches.append({
            "name": name,
            "url": url,
            "count": int(count) if count else 0,
            "est": est,
        })
    return matches
