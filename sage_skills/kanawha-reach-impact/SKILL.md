---
name: kanawha-reach-impact
description: Flood impact on Kanawha River critical infrastructure broken down by geographic reach (sub-region) at a given flow (cfs). Each reach corresponds to a USGS gauge station. Returns per-reach counts of impacted facilities by type and severity, and displays reach polygons on the map color-coded by total impact. Use when the user asks about impacts by area, region, or location along the Kanawha River.
---

# Kanawha Reach Impact Skill

## Description

Breaks down CIKR flood impacts by geographic reach — the 13 sub-regions of the Kanawha River,
each tied to a USGS gauge station. Useful for identifying which areas of the river corridor
are most impacted and for geographic prioritization of emergency response.

**Reaches and their gauge stations:**
| Reach | Gauge ID | USGS ID |
|-------|----------|---------|
| reach1 | POPW2 | 03201500 |
| reach2 | POPW2&CRSW2 | — |
| reach3 | POPW2&CRSW2 | — |
| reach4 | CRSW2 | — |
| reach4a | QUSW2 | 03197000 |
| reach4b | CLYW2 | 03196800 |
| reach4c | FRMW2 | 03196600 |
| reach4d | USGS03195500 | 03195500 |
| reach5 | KANW2 | 03193000 |
| reach6 | HINW2 | 03184500 |
| reach6a | BVAW2 | 03192000 |
| reach6b | USGS03189600 | 03189600 |
| reach7 | ALDW2 | 03183500 |

## When to Use

- User asks about flood impacts by area, location, or region along the Kanawha River
- User asks "which reach / area is most impacted at X cfs?"
- User asks to show impacts broken down by geographic section of the river

## Example

### Show flood impact by reach at 300,000 cfs

```python
import json, os
import numpy as np
import geopandas as gpd
import requests
from rasterio.io import MemoryFile

GEOSERVER = "https://sparcal.sdsc.edu/geoserver/tiger"
SCENARIOS = [1_000, 50_000, 100_000, 150_000, 200_000, 300_000,
             400_000, 500_000, 700_000, 900_000, 1_000_000, 1_500_000, 2_000_000]

# ← substitute from user's request
flow_cfs = 300_000

scenario = next((s for s in SCENARIOS if s >= flow_cfs), SCENARIOS[-1])
flow_k = scenario // 1000
print(f"Flow scenario: {scenario:,} cfs")

temp_dir = os.environ.get("TEMP_DIR", "/tmp")

# --- Step 1: Add WMS flood depth layer ---
wms_layer = {
    "url": f"{GEOSERVER}/wms",
    "layers": f"tiger:Depth_{scenario}_5070",
    "name": f"Kanawha Flood {flow_k}k cfs",
    "opacity": 0.7,
    "bbox": [37.5, -82.5, 39.0, -80.5],
}
with open(os.path.join(temp_dir, f"kanawha_flood_{flow_k}k.wms.json"), "w") as f:
    json.dump(wms_layer, f)

# --- Step 2: Fetch reach polygons (EPSG:4326) ---
reaches = gpd.read_file(
    f"{GEOSERVER}/ows?service=WFS&version=2.0.0&request=GetFeature"
    f"&typeName=tiger:REACH_POLYGONS_LRH_Kanawha_River&outputFormat=application/json"
)
print(f"Reaches: {len(reaches)}")

# --- Step 3: Fetch all CIKR points (EPSG:5070) ---
cikr_5070 = gpd.read_file(
    f"{GEOSERVER}/ows?service=WFS&version=2.0.0&request=GetFeature"
    f"&typeName=tiger:Kanawha_CKR_Points_5070&outputFormat=application/json"
)
# Reproject to WGS84 for spatial join with reaches
cikr = cikr_5070.to_crs(epsg=4326)
print(f"CIKR facilities: {len(cikr)}")

# --- Step 4: Fetch depth raster via WCS ---
xmin, ymin, xmax, ymax = cikr_5070.total_bounds
margin = 500
wcs_url = (
    f"{GEOSERVER}/ows?service=WCS&version=2.0.1&request=GetCoverage"
    f"&coverageId=tiger:Depth_{scenario}_5070&format=image/tiff"
    f"&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/5070"
    f"&SUBSET=X({xmin-margin:.0f},{xmax+margin:.0f})"
    f"&SUBSET=Y({ymin-margin:.0f},{ymax+margin:.0f})"
)
print("Fetching depth raster from WCS ...")
resp = requests.get(wcs_url, timeout=120)
resp.raise_for_status()

# --- Step 5: Sample depth at each CIKR point ---
with MemoryFile(resp.content) as memfile:
    with memfile.open() as src:
        nodata = src.nodata
        coords = [(geom.x, geom.y) for geom in cikr_5070.geometry]
        raw = [v[0] for v in src.sample(coords)]

depths = [np.nan if ((nodata is not None and v == nodata) or v <= 0) else float(v)
          for v in raw]
cikr["depth_ft"] = depths

def classify(d):
    if np.isnan(d): return "Not Flooded"
    if d <= 1:      return "Nuisance"
    if d <= 3:      return "Minor"
    if d <= 6:      return "Moderate"
    return "Severe"

cikr["severity"] = cikr["depth_ft"].apply(classify)

# --- Step 6: Spatial join — assign each facility to a reach ---
cikr_reach = gpd.sjoin(cikr, reaches[["Name", "gauge_id", "USGS_ID", "geometry"]],
                        how="left", predicate="within")
cikr_reach["reach"] = cikr_reach["Name"].fillna("Outside Reaches")

# --- Step 7: Aggregate by reach ---
flooded = cikr_reach[cikr_reach["severity"] != "Not Flooded"]
# Note: facility type column is "Point_Type" (not "Type")
by_reach = flooded.groupby("reach").agg(
    total_flooded=("severity", "count"),
    severe=("severity", lambda x: (x == "Severe").sum()),
    moderate=("severity", lambda x: (x == "Moderate").sum()),
    minor=("severity", lambda x: (x == "Minor").sum()),
    nuisance=("severity", lambda x: (x == "Nuisance").sum()),
).sort_values("total_flooded", ascending=False)

# --- Step 8: Save reaches with impact counts as GeoJSON ---
reaches_impact = reaches.merge(
    by_reach.reset_index(), left_on="Name", right_on="reach", how="left"
).fillna(0)
reaches_impact["total_flooded"] = reaches_impact["total_flooded"].astype(int)
layer_name = f"kanawha_reaches_{flow_k}k"
reaches_impact.to_file(os.path.join(temp_dir, f"{layer_name}.geojson"), driver="GeoJSON")
print(f"Saved reach polygons to map layer: {layer_name}")

# --- Step 9: Print report ---
print(f"\n=== Flood Impact by Reach — {scenario:,} cfs ===")
print(f"Total flooded facilities: {len(flooded)} of {len(cikr)}")
print(f"\n{'Reach':<12} {'Total':>6} {'Severe':>8} {'Moderate':>10} {'Minor':>7} {'Nuisance':>10}")
print("-" * 58)
for reach, row in by_reach.iterrows():
    print(f"{reach:<12} {int(row.total_flooded):>6} {int(row.severe):>8} "
          f"{int(row.moderate):>10} {int(row.minor):>7} {int(row.nuisance):>10}")

by_reach.to_csv(os.path.join(temp_dir, f"kanawha_reach_summary_{flow_k}k.csv"))
print("\nSummary saved.")
```

## Notes

- **Spatial join**: CIKR points are matched to reach polygons using `geopandas.sjoin`. Facilities outside all reach polygons appear as "Outside Reaches".
- **CIKR columns**: facility type is `Point_Type` (not `Type`). Always use `groupby("Point_Type")` for facility type breakdowns.
- **Gauge stations**: Each reach is tied to a real-time USGS gauge. Future enhancement: fetch live gauge readings to auto-select the flow scenario per reach.
- **CRS**: Reaches are in EPSG:4326; CIKR reprojected from EPSG:5070 to EPSG:4326 for the spatial join.
