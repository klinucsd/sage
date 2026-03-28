---
name: kanawha-cikr-impact
description: Flood impact on Kanawha River critical facilities (schools, hospitals, EMS, fire stations, electric substations, power generation, law enforcement, colleges, waste water plants, airports) at a given flow (cfs). Use instead of any generic school/hospital/infrastructure skill. Returns facility locations and counts by severity.
---

# Kanawha CIKR Impact Skill

## Description

Analyzes flood impact on critical infrastructure (CIKR) along the Kanawha River.
Samples USACE HEC-RAS flood depth at each facility location, classifies severity,
displays results on the map, and returns a summary for reporting.

**Severity Classification (depth in feet above ground):**
| Severity | Depth |
|----------|-------|
| Not Flooded | 0 ft |
| Nuisance | 0 – 1 ft |
| Minor | 1 – 3 ft |
| Moderate | 3 – 6 ft |
| Severe | > 6 ft |

**Available flow scenarios (cfs) — always round UP to nearest:**
1,000 / 50,000 / 100,000 / 150,000 / 200,000 / 300,000 / 400,000 / 500,000 / 700,000 / 900,000 / 1,000,000 / 1,500,000 / 2,000,000

**WFS columns — the layer has ONLY these 3 fields (no name, address, or ID field):**
- `Point_Type` (string): facility type — use this for all filtering and grouping
- `Count` (integer): always 1
- `id` (string): internal GeoServer feature ID (e.g., "Kanawha_CKR_Points_5070.1") — not a facility name

**⚠️ There is NO facility name, address, or any other attribute. Do NOT attempt to access `Name`, `NAME`, `Facility`, or any other column — it will raise a KeyError.**

**Available Point_Type values for filtering:**
Airports, Broadcast Communication, Colleges and Universities, EMS,
Electric Power Generation, Electric Substations, Fire Stations, Fire Stations EMS,
Heliports, Hospitals, Hydroelectric Power Generation, Intermodal Terminal Facilities,
Law Enforcement, Natural Gas Storage, Petroleum Terminals, Schools,
Waste Water Treatment Plants

## When to Use

- User asks about flood impact on infrastructure at a given flow
- User asks "how many schools/hospitals/substations are flooded at X cfs?"
- User asks to show impacted facilities of a specific type on the map
- User asks for a flood impact report for the Kanawha River

## When NOT to Use

This skill only returns facility locations and severity — it has no population or economic data.
For the following requests, use **kanawha-nsi-impact** instead:
- Student counts in flooded buildings
- Nursing home residents at risk
- Hospital beds or hospital capacity — **neither CIKR nor NSI contains bed count data; inform the user this information is not available**
- Employee numbers in flooded buildings
- Dollar damage or property value estimates
- Elderly population at risk

## Example

### Show all schools impacted by flow 400,000 cfs

Substitute `flow_cfs` and `filter_type` (set to None to include all facility types).

```python
import json, os
import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from io import BytesIO
import rasterio
from rasterio.io import MemoryFile

GEOSERVER = "https://sparcal.sdsc.edu/geoserver/tiger"
SCENARIOS = [1_000, 50_000, 100_000, 150_000, 200_000, 300_000,
             400_000, 500_000, 700_000, 900_000, 1_000_000, 1_500_000, 2_000_000]

# ← substitute from user's request
flow_cfs = 400_000
# Single type: "Schools"  |  Multiple types: ["Schools", "Hospitals"]  |  All: None
filter_type = "Schools"

# Round UP to nearest scenario
scenario = next((s for s in SCENARIOS if s >= flow_cfs), SCENARIOS[-1])
flow_k = scenario // 1000
print(f"Flow scenario: {scenario:,} cfs")

# --- Step 1: Add WMS flood depth layer to map ---
temp_dir = os.environ.get("TEMP_DIR", "/tmp")
wms_layer = {
    "url": f"{GEOSERVER}/wms",
    "layers": f"tiger:Depth_{scenario}_5070",
    "name": f"Kanawha Flood {flow_k}k cfs",
    "opacity": 0.7,
    "bbox": [37.5, -82.5, 39.0, -80.5],
}
with open(os.path.join(temp_dir, f"kanawha_flood_{flow_k}k.wms.json"), "w") as f:
    json.dump(wms_layer, f)

# --- Step 2: Fetch CIKR points from WFS ---
wfs_url = (f"{GEOSERVER}/ows?service=WFS&version=2.0.0&request=GetFeature"
           f"&typeName=tiger:Kanawha_CKR_Points_5070&outputFormat=application/json")
if filter_type:
    from urllib.parse import quote
    if isinstance(filter_type, list):
        types_quoted = ", ".join(f"'{t}'" for t in filter_type)
        wfs_url += "&CQL_FILTER=" + quote(f"Point_Type IN ({types_quoted})")
    else:
        wfs_url += "&CQL_FILTER=" + quote(f"Point_Type='{filter_type}'")

cikr = gpd.read_file(wfs_url)  # EPSG:5070
print(f"CIKR facilities fetched: {len(cikr)} ({filter_type or 'all types'})")

# --- Step 3: Fetch depth raster via WCS (subset to CIKR bbox) ---
margin = 500  # 500m margin
xmin, ymin, xmax, ymax = cikr.total_bounds
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

# --- Step 4: Sample depth at each CIKR point ---
with MemoryFile(resp.content) as memfile:
    with memfile.open() as src:
        nodata = src.nodata
        coords = [(geom.x, geom.y) for geom in cikr.geometry]
        raw = [v[0] for v in src.sample(coords)]

depths = []
for v in raw:
    if nodata is not None and v == nodata:
        depths.append(np.nan)
    elif v <= 0:
        depths.append(np.nan)
    else:
        depths.append(float(v))

cikr["depth_ft"] = depths

# --- Step 5: Classify severity ---
def classify(d):
    if np.isnan(d): return "Not Flooded"
    if d <= 1:      return "Nuisance"
    if d <= 3:      return "Minor"
    if d <= 6:      return "Moderate"
    return "Severe"

cikr["severity"] = cikr["depth_ft"].apply(classify)

# --- Step 6: Save ALL facilities as GeoJSON for map (color-coded by severity) ---
cikr_wgs84 = cikr.to_crs(epsg=4326)
if filter_type is None:
    type_label = "all_cikr"
elif isinstance(filter_type, list):
    type_label = "_".join(t.split()[0].lower() for t in filter_type)
else:
    type_label = filter_type.lower().replace(" ", "_")
layer_name = f"kanawha_{type_label}_{flow_k}k"
cikr_wgs84.to_file(os.path.join(temp_dir, f"{layer_name}.geojson"), driver="GeoJSON")
print(f"Saved {len(cikr_wgs84)} facilities to map layer: {layer_name}")

# --- Step 7: Print detailed summary for report ---
flooded = cikr[cikr["severity"] != "Not Flooded"]
print(f"\n=== CIKR Flood Impact Summary ===")
print(f"Flow: {flow_cfs:,} cfs → scenario: {scenario:,} cfs")
type_desc = "All types" if filter_type is None else (", ".join(filter_type) if isinstance(filter_type, list) else filter_type)
print(f"Facility type: {type_desc}")
print(f"Total facilities: {len(cikr)}")
print(f"Flooded (any severity): {len(flooded)}")
print(f"\nBy severity:")
for sev in ["Nuisance", "Minor", "Moderate", "Severe"]:
    n = (cikr["severity"] == sev).sum()
    print(f"  {sev}: {n}")

if filter_type is None:
    print(f"\nBy facility type (flooded only):")
    summary = (flooded.groupby(["Point_Type", "severity"])
               .size().reset_index(name="count"))
    pivot = summary.pivot_table(index="Point_Type", columns="severity",
                                values="count", fill_value=0)
    for col in ["Nuisance", "Minor", "Moderate", "Severe"]:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot[["Nuisance", "Minor", "Moderate", "Severe"]]
    pivot["Total Flooded"] = pivot.sum(axis=1)
    print(pivot.to_string())

    # Save summary table as CSV
    pivot.to_csv(os.path.join(temp_dir, f"kanawha_cikr_summary_{flow_k}k.csv"))
    print(f"\nSummary table saved.")

print(f"\nBy facility type (flooded only):")
summary = (flooded.groupby(["Point_Type", "severity"])
           .size().reset_index(name="count"))
pivot = summary.pivot_table(index="Point_Type", columns="severity",
                            values="count", fill_value=0)
for col in ["Nuisance", "Minor", "Moderate", "Severe"]:
    if col not in pivot.columns:
        pivot[col] = 0
pivot = pivot[["Nuisance", "Minor", "Moderate", "Severe"]]
pivot["Total"] = pivot.sum(axis=1)
print(pivot.to_string())
pivot.to_csv(os.path.join(temp_dir, f"kanawha_cikr_summary_{flow_k}k.csv"))
```

## Notes

- **WFS filter**: Use `CQL_FILTER=Point_Type='Schools'` to fetch only one facility type. Use `None` for all 505 facilities.
- **Depth source**: USACE HEC-RAS 2D model depth rasters served via WCS. Depth in feet above ground surface.
- **CRS**: CIKR points and depth rasters are both in EPSG:5070 — no reprojection needed for sampling.
- **Not Flooded**: Facilities outside the flood extent have depth = NaN (nodata in raster).
- **Always add WMS**: Include the flood depth WMS layer on the map alongside the CIKR points.
- **Getting lat/lon coordinates**: CIKR is in EPSG:5070. To print coordinates, reproject the **entire GeoDataFrame** first — never call `.to_crs()` on an individual geometry object (raises AttributeError):
  ```python
  # CORRECT — reproject the GeoDataFrame, then read x/y from each row
  flooded_wgs84 = flooded.to_crs(epsg=4326)
  for _, row in flooded_wgs84.iterrows():
      print(f"  ({row.geometry.y:.4f}, {row.geometry.x:.4f}) — {row['depth_ft']:.2f} ft ({row['severity']})")

  # WRONG — do NOT call .to_crs() on a geometry object
  # row.geometry.to_crs(epsg=4326)  ← AttributeError: 'Point' has no attribute 'to_crs'
  ```
