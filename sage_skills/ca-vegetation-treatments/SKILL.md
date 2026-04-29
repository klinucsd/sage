---
name: ca-vegetation-treatments
description: Retrieve California Interagency Vegetation Treatment activity records (ITS V2.0). Query by county, region, activity category (mechanical fuels reduction, prescribed fire, grazing, timber harvest, tree planting), administering organization, ownership group, vegetation type, status, year, or quantity. Returns point geometries with treatment details.
---

# California Interagency Vegetation Treatments (ITS V2.0) Skill

## Description

This skill retrieves vegetation treatment activity records reported through the California Interagency Tracking System (ITS) by accessing the ArcGIS Feature Service at the following URL:

```
https://sparcal.sdsc.edu/arcgis/rest/services/Hosted/ITS_V2_0_Activity_webmap_gdb/FeatureServer/0
```

**Service Details:**
- Geographic coverage: **California only** (counties, regions, and ownership groups are CA-specific)
- Geometry type: Point (one point per activity record)
- Maximum record count per request: 2000 (pagination supported)
- Native spatial reference: EPSG:3857 (Web Mercator); the query function below sends `inSR=4326` (so the bbox is interpreted as WGS84) and `outSR=4326` (so results come back in WGS84). **Both must be set** — without `inSR=4326`, the server interprets the WGS84 bbox as Web Mercator coordinates and returns zero features.
- Total records: ~168,000 activities

The service returns the following columns:

- **objectid**: System-assigned unique identifier
- **agency**: Top-level reporting agency (coded — see below)
- **administering_org**: Sub-agency or organization administering the activity (coded — see below)
- **primary_ownership_group**: Land ownership category (coded — see below)
- **county**: California county code (3-letter; coded — see below)
- **region**: California vegetation region (coded — see below)
- **activity_description**: Free-text description of the treatment (e.g., "MOWING", "PRESCRIBED BURN")
- **activity_cat**: Activity category (coded — see below)
- **broad_vegetation_type**: Broad vegetation type at the treatment location (coded — see below)
- **activity_status**: Lifecycle status (coded — see below)
- **activity_quantity**: Numeric quantity of the activity
- **activity_uom**: Unit of measurement for `activity_quantity` (coded — see below)
- **activity_end**: Date the activity ended (epoch milliseconds; convert with `pd.to_datetime(..., unit='ms')`)
- **year_txt**: Year of the activity as text (e.g., "2023")
- **valid_geom**: Geometry validity flag (e.g., "VALID")
- **entity_type**: Reporting entity type (e.g., "State", "Federal")

## ⚠️ IMPORTANT: All Categorical Fields Use Coded Values

Most filter-friendly fields store **short codes**, not human-readable names. Always query with the **code** (e.g., `county = 'LA'`, not `county = 'Los Angeles'`).

### `agency` codes
- `CALEPA` = CA Environmental Protection Agency
- `CALSTA` = CA State Transportation Agency
- `CNRA`   = CA Natural Resources Agency
- `DOD`    = U.S. Department of Defense
- `DOE`    = U.S. Department of Energy
- `DOI`    = U.S. Department of the Interior
- `USDA`   = U.S. Department of Agriculture
- `TIMBER` = Industrial Timber
- `OTHER`  = Other

### `administering_org` codes (selected)
- `CALFIRE` = CAL FIRE
- `USFS`    = U.S. Forest Service
- `BLM`     = Bureau of Land Management
- `NPS`     = National Park Service
- `FWS`     = U.S. Fish and Wildlife Service
- `BIA`     = Bureau of Indian Affairs
- `NRCS`    = Natural Resources Conservation Service
- `CALTRANS` = CA Department of Transportation
- `PARKS`   = CA State Parks
- `CDFW`    = CA Department of Fish and Wildlife
- `DWR`     = CA Department of Water Resources
- `SNC`     = Sierra Nevada Conservancy
- `WCB`     = CA Wildlife Conservation Board
- `TIMBER`  = Timber Companies

(There are ~30 codes total; query the service metadata for the full list if needed.)

### `primary_ownership_group` codes
- `FEDERAL`
- `STATE`
- `LOCAL`
- `TRIBAL`
- `NGO`
- `PRIVATE_INDUSTRY`     (Private — Industrial)
- `PRIVATE_NON-INDUSTRY` (Private — Non-Industrial)

### `region` codes
- `CENTRAL_COAST` = Central California
- `NORTH_COAST`   = North California
- `SIERRA_NEVADA` = Sierra Nevada
- `SOUTHERN_CA`   = Southern California
- `NON_SPATIAL`   = Non-Spatial (records without a known location)

### `activity_cat` codes (5 categories)
- `MECH_HFR`     = Mechanical and Hand Fuels Reduction
- `PRESCRB_FIRE` = Prescribed Fire
- `GRAZING`      = Targeted Grazing
- `TIMB_HARV`    = Timber Harvest
- `TREE_PLNTING` = Tree Planting

### `broad_vegetation_type` codes
- `FOREST`
- `SHRB_CHAP`   = Shrublands and Chaparral
- `GRASS_HERB`  = Grass/Herbaceous
- `WETLAND`
- `SPARSE`
- `AGRICULTURE`
- `URBAN`
- `WATER`

### `activity_status` codes
- `ACTIVE`, `COMPLETE`, `PLANNED`, `PROPOSED`, `OUTYEAR`, `CANCELLED`

### `activity_uom` codes
- `AC` = Acres
- `EA` = Each
- `MI` = Miles
- `HR` = Hours
- `TON` = Tons
- `OTHER`

### `county` codes (California, 3-letter; selected)
- `LA` = Los Angeles, `SD` = San Diego, `SF` = San Francisco, `SAC` = Sacramento
- `ORA` = Orange, `RIV` = Riverside, `SBD` = San Bernardino, `VEN` = Ventura
- `SCL` = Santa Clara, `ALA` = Alameda, `CC` = Contra Costa, `SM` = San Mateo
- `FRE` = Fresno, `KER` = Kern, `TUL` = Tulare, `MON` = Monterey
- `BUT` = Butte, `SHA` = Shasta, `SIS` = Siskiyou, `HUM` = Humboldt
- `MEN` = Mendocino, `SON` = Sonoma, `NAP` = Napa, `LAK` = Lake
- `PLU` = Plumas, `LAS` = Lassen, `MOD` = Modoc, `TEH` = Tehama
- `ED` = El Dorado, `PLA` = Placer, `NEV` = Nevada, `SIE` = Sierra
- `TUO` = Tuolumne, `MPA` = Mariposa, `MAD` = Madera, `MER` = Merced
- `STA` = Stanislaus, `SJ` = San Joaquin, `SBT` = San Benito, `SCR` = Santa Cruz
- `SB` = Santa Barbara, `SLO` = San Luis Obispo, `IMP` = Imperial, `INY` = Inyo
- `MNO` = Mono, `ALP` = Alpine, `AMA` = Amador, `CAL` = Calaveras
- `COL` = Colusa, `GLE` = Glenn, `KIN` = Kings, `MRN` = Marin, `DN` = Del Norte
- `SOL` = Solano, `SUT` = Sutter, `TRI` = Trinity, `YOL` = Yolo, `YUB` = Yuba

(58 codes total covering all California counties plus `NON_SPATIAL` for unlocated records.)

## When to Use

Use this skill to:

- Find vegetation treatment activities in a specific California county or region
- Query treatments by category (e.g., all prescribed fire activities, all timber harvests)
- Find treatments by administering organization (CAL FIRE, USFS, etc.)
- Filter treatments by ownership group, vegetation type, status, or year
- Compute total acres treated by category, county, or year
- Analyze the spatial distribution of vegetation treatment activities
- Find recent or upcoming planned treatments

## How to Use

### Step 1: Import Required Libraries

```python
import geopandas as gpd
import pandas as pd
import requests
```

### Step 2: Define the Query Function

The service has a maximum of 2000 records per request and supports pagination via `resultOffset`. Use `get_all_features` (below) for queries that may return more than 2000 results.

```python
def get_features(service_url, where, bbox=None, max_records=2000):
    """
    Query the CA Interagency Vegetation Treatments Feature Service.

    Parameters:
    -----------
    service_url : str
        The URL of the ArcGIS Feature Service layer
    where : str
        SQL-like WHERE clause to filter features
    bbox : list or tuple, optional
        Bounding box as [minx, miny, maxx, maxy] in WGS84 (EPSG:4326).
        Defaults to California bounds.
    max_records : int, optional
        Maximum records to return in a single request (default: 2000)

    Returns:
    --------
    geopandas.GeoDataFrame
        GeoDataFrame containing treatment activity points in EPSG:4326
    """
    # Default to California bounds
    if bbox is None:
        bbox = [-124.5, 32.5, -114.0, 42.1]

    minx, miny, maxx, maxy = bbox

    params = {
        "where": where,
        "geometry": f"{minx},{miny},{maxx},{maxy}",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",   # CRITICAL: bbox is in WGS84; service's native SR is 3857
        "outFields": "*",
        "returnGeometry": "true",
        "f": "geojson",
        "outSR": "4326",
        "resultOffset": 0,
        "resultRecordCount": min(max_records, 2000),
    }

    response = requests.get(service_url + "/query", params=params)
    data = response.json()

    if data.get("features"):
        return gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4326")
    else:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")


def get_all_features(service_url, where, bbox=None):
    """
    Query the service and handle pagination for >2000 results.
    Use this when the result set may exceed 2000 records.
    """
    if bbox is None:
        bbox = [-124.5, 32.5, -114.0, 42.1]

    minx, miny, maxx, maxy = bbox
    all_features = []
    offset = 0
    batch_size = 2000

    while True:
        params = {
            "where": where,
            "geometry": f"{minx},{miny},{maxx},{maxy}",
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",   # CRITICAL: bbox is in WGS84; service's native SR is 3857
            "outFields": "*",
            "returnGeometry": "true",
            "f": "geojson",
            "outSR": "4326",
            "resultOffset": offset,
            "resultRecordCount": batch_size,
        }

        response = requests.get(service_url + "/query", params=params)
        data = response.json()

        feats = data.get("features") or []
        if not feats:
            break

        all_features.extend(feats)

        if len(feats) < batch_size:
            break

        offset += batch_size

    if all_features:
        return gpd.GeoDataFrame.from_features(all_features, crs="EPSG:4326")
    else:
        return gpd.GeoDataFrame(columns=["geometry"], crs="EPSG:4326")
```

### Step 3: Query Examples

#### Example 1: All Activities in a County

```python
url = "https://sparcal.sdsc.edu/arcgis/rest/services/Hosted/ITS_V2_0_Activity_webmap_gdb/FeatureServer/0"

# All treatments in Los Angeles County
where = "county = 'LA'"
la_treatments = get_all_features(url, where)

print(f"Found {len(la_treatments)} treatment activities in Los Angeles County")
print(la_treatments[['activity_cat', 'activity_description', 'activity_quantity',
                     'activity_uom', 'year_txt']].head())
```

#### Example 2: All Prescribed Fire Activities Statewide

```python
# Prescribed fires across California
where = "activity_cat = 'PRESCRB_FIRE'"
prescribed_fires = get_all_features(url, where)

print(f"Found {len(prescribed_fires)} prescribed fire activities")

# Total acres burned (where unit is acres)
acres_burned = prescribed_fires[prescribed_fires['activity_uom'] == 'AC']['activity_quantity'].sum()
print(f"Total acres treated by prescribed fire: {acres_burned:,.0f}")
```

#### Example 3: Activities by a Specific Administering Org

```python
# All CAL FIRE activities
where = "administering_org = 'CALFIRE'"
calfire = get_all_features(url, where)

print(f"CAL FIRE activities: {len(calfire)}")

# Breakdown by category
print(calfire['activity_cat'].value_counts())
```

#### Example 4: Recent Completed Activities (by year)

```python
# Completed activities in 2023
where = "activity_status = 'COMPLETE' AND year_txt = '2023'"
completed_2023 = get_all_features(url, where)

print(f"Completed in 2023: {len(completed_2023)} activities")

# Acres treated in 2023
acres_2023 = completed_2023[completed_2023['activity_uom'] == 'AC']['activity_quantity'].sum()
print(f"Total acres treated in 2023: {acres_2023:,.0f}")
```

#### Example 5: Mechanical Fuels Reduction in the Sierra Nevada

```python
# Mechanical/hand fuels reduction in Sierra Nevada region
where = "activity_cat = 'MECH_HFR' AND region = 'SIERRA_NEVADA'"
sierra_mech = get_all_features(url, where)

print(f"Mechanical fuels reduction in Sierra Nevada: {len(sierra_mech)} activities")

# By administering org
print(sierra_mech['administering_org'].value_counts().head(10))
```

#### Example 6: Treatments on Federal vs. State Land

```python
# All federal-land treatments in 2024
where = "primary_ownership_group = 'FEDERAL' AND year_txt = '2024'"
federal_2024 = get_all_features(url, where)

# All state-land treatments in 2024
where = "primary_ownership_group = 'STATE' AND year_txt = '2024'"
state_2024 = get_all_features(url, where)

print(f"2024 federal-land treatments: {len(federal_2024)}")
print(f"2024 state-land treatments:   {len(state_2024)}")
```

#### Example 7: Forest Treatments in a Specific County

```python
# Forest treatments in Humboldt County
where = "county = 'HUM' AND broad_vegetation_type = 'FOREST'"
humboldt_forest = get_all_features(url, where)

print(f"Humboldt forest treatments: {len(humboldt_forest)}")
print(humboldt_forest[['activity_cat', 'activity_description',
                       'activity_quantity', 'activity_uom']].head(10))
```

#### Example 8: Multi-County Query

```python
# Treatments in any of three Bay Area counties
where = "county IN ('SCL', 'ALA', 'CC')"
bay_area = get_all_features(url, where)

print(f"Bay Area treatments: {len(bay_area)}")
print(bay_area['county'].value_counts())
```

#### Example 9: Activities Ending After a Specific Date

```python
# Activities that ended after Jan 1 2024
# Date fields use epoch-milliseconds; ArcGIS supports the TIMESTAMP literal
where = "activity_end >= TIMESTAMP '2024-01-01 00:00:00'"
recent = get_all_features(url, where)

print(f"Activities ending in/after 2024: {len(recent)}")

# Convert epoch ms to readable dates
recent['end_date'] = pd.to_datetime(recent['activity_end'], unit='ms')
print(recent[['activity_cat', 'activity_description', 'end_date']].head())
```

#### Example 10: Treatment Description Keyword Search

```python
# Free-text search in activity_description (e.g., find all "MOWING")
where = "activity_description LIKE '%MOWING%'"
mowing = get_all_features(url, where)

print(f"Mowing-related activities: {len(mowing)}")
print(mowing[['county', 'administering_org', 'activity_quantity', 'activity_uom']].head())
```

## Notes

### Geographic Scope: California Only

This dataset covers California exclusively. Counties, regions, and many administering organizations are CA-specific. The default bbox in `get_features` reflects this — there is no need to pass a bbox unless you want to narrow further (e.g., a single county).

### Spatial Reference

The service stores geometries in EPSG:3857 (Web Mercator), but the query function above requests `outSR=4326` so results come back in WGS84. No client-side transformation is needed.

### Coded Fields vs. Free-Text Fields

Most filter-friendly fields are **coded** (short codes like `'CALFIRE'`, `'PRESCRB_FIRE'`, `'LA'`). Use exact match (`=`) and the code, not the human-readable name. Two fields are free-text:

- `activity_description` — variable, often uppercase verbs (e.g., "MOWING", "PRESCRIBED BURN", "THINNING"). Use `LIKE '%KEYWORD%'` with uppercase.
- `valid_geom` — typically `'VALID'`; rarely useful as a filter.

### Activity Quantity and Unit of Measurement

The same dataset mixes units (acres, miles, hours, each, tons). When summing or comparing quantities, **always filter by `activity_uom` first**:

```python
# WRONG — mixes acres, miles, hours, etc.
total = df['activity_quantity'].sum()

# RIGHT — sum only acre values
acres = df[df['activity_uom'] == 'AC']['activity_quantity'].sum()
```

### Year Filtering

The `year_txt` field is a **string**, not an integer. Use string comparison:

```python
where = "year_txt = '2023'"          # OK
where = "year_txt IN ('2023', '2024')"  # OK
where = "year_txt >= '2020'"         # also works (lexicographic)
```

### Date Field Conversion

`activity_end` comes back as **epoch milliseconds** (e.g., `1693267200000`). Convert in pandas with:

```python
df['end_date'] = pd.to_datetime(df['activity_end'], unit='ms')
```

For server-side date filtering use the ArcGIS `TIMESTAMP 'YYYY-MM-DD HH:MM:SS'` literal as in Example 9.

### Pagination

The service caps each response at 2000 records, but the underlying dataset has ~168,000 records. Use `get_all_features` for any query that may return more than 2000 results (statewide queries, broad category queries, multi-year queries). The function pages through results automatically.

### WHERE Clause Tips

- **Always uppercase** for coded values: `"county = 'LA'"`, not `"county = 'la'"`
- **String literals in single quotes**: `"activity_cat = 'PRESCRB_FIRE'"`
- **Combine with AND/OR**: `"county = 'LA' AND activity_cat = 'PRESCRB_FIRE'"`
- **Multiple values**: `"county IN ('LA', 'ORA', 'SBD')"`
- **Free-text search**: `"activity_description LIKE '%MOWING%'"`
- **Numeric comparisons** for `activity_quantity`: `"activity_quantity > 100"`
- **All records**: `"1=1"` (use with a tighter bbox to avoid pulling all 168K records)

### Saving Output for Sage Auto-Display

When running inside Sage, save the queried GeoDataFrame as a GeoJSON file in `SAGE_OUTPUT_DIR` so it appears on the combined Folium map automatically. The agent does not need to call any display function explicitly — Sage's renderer scans for new `.geojson` files at the end of the cell.

```python
import os

outdir = globals().get("SAGE_OUTPUT_DIR", "/tmp")
os.makedirs(outdir, exist_ok=True)

# After the GeoDataFrame `df` is built:
out_path = os.path.join(outdir, "ca_vegetation_treatments.geojson")
df.to_file(out_path, driver="GeoJSON")
print(f"Saved {len(df)} records → {out_path}")
```

For categorical coloring on the map (e.g., color points by `activity_cat`), also save a colormap sidecar with the same base name plus `.colormap.json`:

```python
import json

colormap = {
    "field": "activity_cat",
    "title": "Activity Category",
    "palette": {
        "MECH_HFR":     "#fdb462",  # orange — Mech. & Hand Fuels Reduction
        "PRESCRB_FIRE": "#fb8072",  # red — Prescribed Fire
        "GRAZING":      "#b3de69",  # green — Targeted Grazing
        "TIMB_HARV":    "#80b1d3",  # blue — Timber Harvest
        "TREE_PLNTING": "#bebada",  # purple — Tree Planting
    },
}
with open(os.path.join(outdir, "ca_vegetation_treatments.colormap.json"), "w") as f:
    json.dump(colormap, f, indent=2)
```

### Complete Example: Statewide Acres Treated by Category in 2023

```python
import geopandas as gpd
import pandas as pd
import requests
import matplotlib.pyplot as plt

url = "https://sparcal.sdsc.edu/arcgis/rest/services/Hosted/ITS_V2_0_Activity_webmap_gdb/FeatureServer/0"

# All completed activities in 2023, with acre units
where = "activity_status = 'COMPLETE' AND year_txt = '2023' AND activity_uom = 'AC'"
df = get_all_features(url, where)

print(f"Completed acreage activities in 2023: {len(df)}")

# Group by category and sum acres
by_cat = df.groupby('activity_cat')['activity_quantity'].agg(['sum', 'count'])
by_cat.columns = ['total_acres', 'num_activities']
by_cat = by_cat.sort_values('total_acres', ascending=False)

# Map codes to human-readable names
cat_names = {
    'MECH_HFR':     'Mech. & Hand Fuels Reduction',
    'PRESCRB_FIRE': 'Prescribed Fire',
    'GRAZING':      'Targeted Grazing',
    'TIMB_HARV':    'Timber Harvest',
    'TREE_PLNTING': 'Tree Planting',
}
by_cat.index = by_cat.index.map(cat_names)

print("\n2023 California vegetation treatments by category:")
print(by_cat)
print(f"\nTotal acres treated: {by_cat['total_acres'].sum():,.0f}")

# Bar chart
fig, ax = plt.subplots(figsize=(10, 5))
by_cat['total_acres'].plot.barh(ax=ax)
ax.set_xlabel('Acres treated')
ax.set_title('2023 California Vegetation Treatments by Category')
ax.invert_yaxis()
plt.tight_layout()
plt.show()

# County-level breakdown
print("\nTop 10 counties by acres treated in 2023:")
top_counties = df.groupby('county')['activity_quantity'].sum().nlargest(10)
print(top_counties)
```
