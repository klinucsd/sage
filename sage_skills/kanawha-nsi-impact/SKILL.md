---
name: kanawha-nsi-impact
description: "Use this for Kanawha River flood questions about student counts, nursing home residents, employee numbers, elderly population, dollar damage, high-value properties, basements, pre-1970 structures, or specific building types (hospitals=COM6, schools=EDU1, high-tech=IND5, heavy industrial=IND1, hotels=RES4, churches=REL1). Uses FEMA NSI 2022 (88,460 structures). Note: hospital bed counts are NOT available in this dataset."
---

# Kanawha NSI Flood Impact Skill

## Description

Analyzes economic damage and population exposure using the FEMA National Structure Inventory (NSI 2022).
Samples USACE HEC-RAS flood depth at all 88,460 structures, then aggregates dollar losses and population at risk.

**⚠️ Complete WFS column list — ONLY these columns exist (no ADDRESS, CITY, STATE, ZIPCODE, NAME, or any other field):**

| Column | Description |
|--------|-------------|
| `ST_DAMCAT` | Damage category: RES, COM, IND, AGR, GOV, EDU, REL |
| `OCCTYPE` | Occupancy type code (see table below) |
| `VAL_STRUCT` | Structure value (2026 $) |
| `VAL_CONT` | Contents value (2026 $) |
| `VAL_VEHIC` | Vehicle value (2026 $) |
| `POP2AMU65` | Nighttime population under 65 |
| `POP2AMO65` | Nighttime population over 65 |
| `POP2PMU65` | Daytime population under 65 |
| `POP2PMO65` | Daytime population over 65 |
| `EMPNUM` | Employee count |
| `STUDENTS` | Student count |
| `NURSGHMPOP` | Nursing home resident count |
| `RESUNITS` | Number of residential units |
| `YRBUILT` | Year built |
| `BSMNT` | Basement present: Y or N |
| `NUM_STORY` | Number of stories |
| `SQFT` | Square footage |
| `NAICS` | NAICS industry code |
| `BID` | Building ID |
| `CBFIPS2010` | Census block FIPS code |
| `CENSREGION` | Census region |
| `FIRMZONE` | FEMA flood zone |
| `X` / `Y` | Longitude / Latitude (EPSG:4326) |
| `geometry` | Point geometry |

Other columns present but rarely needed: `HEIGHT`, `FTPRNTSQFT`, `FOUND_HT`, `EXTWALL`, `FNDTYPE`, `P_EXTWALL`, `P_FNDTYPE`, `P_BSMNT`, `TOTAL_ROOM`, `BEDROOMS`, `TOTAL_BATH`, `P_GARAGE`, `PARKINGSP`, `MED_YR_BLT`, `BLDCOSTCAT`, `NUMVEHIC`, `FTPRNTID`, `FTPRNTSRC`, `SOURCE`, `SURPLUS`, `OTHINSTPOP`, `O65DISABLE`, `U65DISABLE`, `APN`, `FIRMDATE`

**OCCTYPE codes** — use these for filtering by building type (e.g. `nsi[nsi["OCCTYPE"] == "IND5"]`):

| OCCTYPE | Description | ST_DAMCAT |
|---------|-------------|-----------|
| RES1 | Single Family Residential | RES |
| RES2 | Mobile Home | RES |
| RES3 | Multi-Family Housing (2–50+ units) | RES |
| RES4 | Hotel or Motel | RES |
| RES5 | Institutional Dormitory | RES |
| RES6 | Nursing Home | RES |
| COM1 | Retail | COM |
| COM2 | Wholesale | COM |
| COM3 | Personal and Repair Services | COM |
| COM4 | Professional or Technical Services | COM |
| COM5 | Bank | COM |
| COM6 | Hospital (**no bed count field in NSI** — if user asks about hospital beds, inform them this data is not available) | COM |
| COM7 | Medical Office | COM |
| COM8 | Entertainment or Recreation | COM |
| COM9 | Theater | COM |
| COM10 | Garage | COM |
| IND1 | Heavy Industrial | IND |
| IND2 | Light Industrial | IND |
| IND3 | Food, Drug or Chemical Processing | IND |
| IND4 | Metals or Minerals Processing | IND |
| IND5 | High Technology | IND |
| IND6 | Construction | IND |
| AGR1 | Agriculture | AGR |
| REL1 | Church | REL |
| GOV1 | Government Services | GOV |
| GOV2 | Emergency Response Services | GOV |
| EDU1 | School | EDU |
| EDU2 | College or University | EDU |

## When to Use

- User asks about economic damage, dollar losses, or property impact from Kanawha flooding
- User asks about population at risk or displaced residents at a given flow
- User asks for a comprehensive flood impact report including dollar figures

## Example

### Estimate economic damage and population exposure at 300,000 cfs

```python
import json, os
import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from rasterio.io import MemoryFile

GEOSERVER = "https://sparcal.sdsc.edu/geoserver/tiger"
SCENARIOS = [1_000, 50_000, 100_000, 150_000, 200_000, 300_000,
             400_000, 500_000, 700_000, 900_000, 1_000_000, 1_500_000, 2_000_000]

# ← substitute from user's request
flow_cfs = 300_000

# Round UP to nearest scenario
scenario = next((s for s in SCENARIOS if s >= flow_cfs), SCENARIOS[-1])
flow_k = scenario // 1000
print(f"Flow scenario: {scenario:,} cfs")

temp_dir = os.environ.get("TEMP_DIR", "/tmp")

# --- Step 1: Add WMS flood depth layer to map ---
wms_layer = {
    "url": f"{GEOSERVER}/wms",
    "layers": f"tiger:Depth_{scenario}_5070",
    "name": f"Kanawha Flood {flow_k}k cfs",
    "opacity": 0.7,
    "bbox": [37.5, -82.5, 39.0, -80.5],
}
with open(os.path.join(temp_dir, f"kanawha_flood_{flow_k}k.wms.json"), "w") as f:
    json.dump(wms_layer, f)

# --- Step 2: Fetch all NSI structures via WFS ---
print("Fetching NSI structures from WFS (88,460 structures) ...")
wfs_url = (f"{GEOSERVER}/ows?service=WFS&version=2.0.0&request=GetFeature"
           f"&typeName=tiger:NSI2022_2026pricelevel&outputFormat=application/json")
nsi = gpd.read_file(wfs_url)  # EPSG:4326
print(f"Fetched {len(nsi)} structures")

# Reproject to EPSG:5070 for depth sampling
nsi_5070 = nsi.to_crs(epsg=5070)

# --- Step 3: Fetch depth raster via WCS ---
xmin, ymin, xmax, ymax = nsi_5070.total_bounds
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

# --- Step 4: Sample depth at each structure ---
with MemoryFile(resp.content) as memfile:
    with memfile.open() as src:
        nodata = src.nodata
        coords = [(geom.x, geom.y) for geom in nsi_5070.geometry]
        raw = [v[0] for v in src.sample(coords)]

depths = []
for v in raw:
    if (nodata is not None and v == nodata) or v <= 0:
        depths.append(np.nan)
    else:
        depths.append(float(v))

nsi["depth_ft"] = depths
flooded = nsi[nsi["depth_ft"].notna()].copy()
print(f"Flooded structures: {len(flooded):,} of {len(nsi):,}")

# --- Step 5: Economic exposure ---
flooded["total_value"] = flooded["VAL_STRUCT"] + flooded["VAL_CONT"] + flooded["VAL_VEHIC"]
total_struct  = flooded["VAL_STRUCT"].sum()
total_cont    = flooded["VAL_CONT"].sum()
total_vehic   = flooded["VAL_VEHIC"].sum()
total_damage  = flooded["total_value"].sum()

# --- Step 6: Population exposure ---
pop_night = flooded["POP2AMU65"].sum() + flooded["POP2AMO65"].sum()
pop_day   = flooded["POP2PMU65"].sum() + flooded["POP2PMO65"].sum()
pop_over65_night = flooded["POP2AMO65"].sum()
pop_over65_day   = flooded["POP2PMO65"].sum()

# --- Step 7: Breakdown by damage category ---
by_cat = flooded.groupby("ST_DAMCAT").agg(
    structures=("depth_ft", "count"),
    val_struct=("VAL_STRUCT", "sum"),
    val_cont=("VAL_CONT", "sum"),
    val_vehic=("VAL_VEHIC", "sum"),
).assign(total=lambda d: d.val_struct + d.val_cont + d.val_vehic)

# --- Step 8: Apply optional filters for map display ---
# Only filter for map display — full dataset is still used for the summary above.
# Uncomment and adjust the filter that matches the user's request:

# Filter by structure value:
# display = flooded[flooded["VAL_STRUCT"] > 1_000_000]

# Filter by damage category:
# display = flooded[flooded["ST_DAMCAT"] == "COM"]

# Filter by employee count:
# display = flooded[flooded["EMPNUM"] > 50]

# Filter by nursing home population:
# display = flooded[flooded["NURSGHMPOP"] > 0]

# Filter by student count:
# display = flooded[flooded["STUDENTS"] > 0]

# Filter by year built:
# display = flooded[flooded["YRBUILT"] < 1970]

# Filter by basement:
# display = flooded[flooded["BSMNT"] == "Y"]

# Filter by number of residential units:
# display = flooded[flooded["RESUNITS"] > 10]

# Default: show all flooded (may be large — prefer a filter above for map display)
display = flooded

# Save filtered subset as GeoJSON for map
display_cols = ["geometry", "ST_DAMCAT", "OCCTYPE", "depth_ft",
                "VAL_STRUCT", "VAL_CONT", "VAL_VEHIC", "EMPNUM",
                "STUDENTS", "NURSGHMPOP", "RESUNITS", "YRBUILT", "BSMNT"]
display_wgs84 = display[display_cols].copy()
display_wgs84.to_file(os.path.join(temp_dir, f"kanawha_nsi_{flow_k}k.geojson"), driver="GeoJSON")
print(f"Saved {len(display_wgs84)} structures to map layer")

# --- Step 9: Save summary CSV ---
by_cat.to_csv(os.path.join(temp_dir, f"kanawha_nsi_summary_{flow_k}k.csv"))

# --- Step 10: Print report ---
print(f"\n=== NSI Flood Impact Summary ===")
print(f"Flow: {flow_cfs:,} cfs → scenario: {scenario:,} cfs")
print(f"Total structures: {len(nsi):,}")
print(f"Flooded structures: {len(flooded):,} ({100*len(flooded)/len(nsi):.1f}%)")
print(f"\nEconomic Exposure:")
print(f"  Structure damage:  ${total_struct/1e6:,.1f}M")
print(f"  Content damage:    ${total_cont/1e6:,.1f}M")
print(f"  Vehicle damage:    ${total_vehic/1e6:,.1f}M")
print(f"  Total exposure:    ${total_damage/1e6:,.1f}M")
print(f"\nPopulation at Risk:")
print(f"  Nighttime (AM):    {pop_night:,} people ({pop_over65_night:,} over 65)")
print(f"  Daytime (PM):      {pop_day:,} people ({pop_over65_day:,} over 65)")
print(f"\nBy Damage Category:")
print(by_cat[["structures", "total"]].assign(
    total_M=lambda d: d.total.div(1e6).round(1)
)[["structures", "total_M"]].to_string())
```

## Notes

- **WFS fetch**: 88,460 structures — may take 30–60 seconds on first fetch.
- **CRS**: NSI is in EPSG:4326; reprojected to EPSG:5070 before depth sampling.
- **AM/PM population**: AM = nighttime (where people sleep); PM = daytime (where people work/study).
- **Values**: 2026 price level dollars.
- **Map display**: Always apply a filter before saving to GeoJSON — displaying all 35,000+ flooded structures is slow. Use the filter block in Step 8 to show only the subset relevant to the user's request (e.g., nursing homes, high-value buildings, pre-1970 structures).
