---
name: usgs-earthquake-events
description: Retrieve earthquake events from USGS API and convert to GeoDataFrame for spatial analysis
license: Apache-2.0
compatibility: Designed for deepagents CLI
metadata:
  author: USGS Earthquake Catalog
  version: "1.0"
allowed-tools: Bash(curl:*) Read
---

# USGS Earthquake Events Skill

## Description
This skill retrieves earthquake event data from the USGS (United States Geological Survey) Earthquake Catalog API and converts the GeoJSON response into a GeoPandas GeoDataFrame for spatial analysis. The API provides comprehensive earthquake data including magnitude, location, depth, time, and various intensity metrics. No API key is required.

## When to Use
- When the user asks for earthquake data in a specific region or time period
- When you need to analyze earthquake patterns spatially using a GeoDataFrame
- When the task involves filtering earthquakes by magnitude, location, or date range
- When performing geospatial analysis of seismic activity

## How to Use

### Step 1: Construct the API Request
Build the USGS API URL with appropriate query parameters:
- **Base URL**: `https://earthquake.usgs.gov/fdsnws/event/1/query`
- **Required**: `format=geojson`
- **Spatial filters**: Use either bounding box (minlatitude, maxlatitude, minlongitude, maxlongitude) OR radius (latitude, longitude, maxradiuskm)
- **Temporal filters**: `starttime` and `endtime` in YYYY-MM-DD format
- **Magnitude filters**: `minmagnitude` and/or `maxmagnitude`
- **Limit**: `limit` parameter (default 20000 if not specified)

### Step 2: Fetch the Data
Use curl or requests to retrieve the GeoJSON response:
```bash
curl "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=2024-01-01&endtime=2024-12-31&minlatitude=32&maxlatitude=42&minlongitude=-125&maxlongitude=-114&minmagnitude=4"
```

### Step 3: Convert to GeoDataFrame
Parse the GeoJSON response and convert to a GeoPandas GeoDataFrame:
```python
import geopandas as gpd
import requests
from shapely.geometry import Point

response = requests.get(url)
data = response.json()

# Extract features
features = data['features']

# Create GeoDataFrame
records = []
for feature in features:
    props = feature['properties']
    coords = feature['geometry']['coordinates']
    
    record = {
        'magnitude': props['mag'],
        'place': props['place'],
        'time': props['time'],
        'depth_km': coords[2],
        'longitude': coords[0],
        'latitude': coords[1],
        'geometry': Point(coords[0], coords[1])
    }
    records.append(record)

gdf = gpd.GeoDataFrame(records, crs='EPSG:4326')
```

## Best Practices
- Always specify `format=geojson` for structured spatial data
- Use bounding boxes for state/regional queries, radius for point-based searches
- Include `minmagnitude` filter to reduce noise (e.g., minmagnitude=2.5 or 4.0)
- The `time` field is in Unix milliseconds - convert to datetime for readability
- Coordinates are in [longitude, latitude, depth] order (GeoJSON standard)
- Set reasonable time ranges to avoid overwhelming responses
- The `magType` field indicates magnitude calculation method (mw=moment, ml=local, md=duration)

## Supporting Files
- `earthquake_fetcher.py` - Python script for automated data retrieval and GeoDataFrame conversion
- `example_queries.json` - Sample query configurations for common use cases

## Examples

### Example 1: California Earthquakes Above Magnitude 4
**User Request:** "Get all earthquakes in California with magnitude 4+ in the last month"

**Approach:**
1. Determine California bounding box: minlat=32.5, maxlat=42, minlon=-124.5, maxlon=-114
2. Calculate date range: last 30 days from current date
3. Construct URL with minmagnitude=4
4. Fetch GeoJSON and convert to GeoDataFrame
5. Result: GeoDataFrame with columns [magnitude, place, time, depth_km, geometry]

```python
url = "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=2024-11-24&endtime=2024-12-24&minlatitude=32.5&maxlatitude=42&minlongitude=-124.5&maxlongitude=-114&minmagnitude=4"
```

### Example 2: Earthquakes Near Specific Location
**User Request:** "Find earthquakes within 100km of Carlsbad, CA in the past year"

**Approach:**
1. Get Carlsbad coordinates: lat=33.1581, lon=-117.3506
2. Use radius search with maxradiuskm=100
3. Set date range for past year
4. Convert response to GeoDataFrame with spatial reference
5. GeoDataFrame enables distance calculations and spatial joins

```python
url = "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&latitude=33.1581&longitude=-117.3506&maxradiuskm=100&starttime=2024-01-01&minmagnitude=2.5"
```

## Map Coloring by Magnitude

To display earthquakes colored by magnitude class, copy this block as-is into your script. Replace `output_file` with the path you used to save the GeoJSON.

```python
import json, os

# Single source of truth: category names used for both classification and palette
_MAG_PALETTE = {
    "M2-3": "#fee8c8",
    "M3-4": "#fdd49e",
    "M4-5": "#fc8d59",
    "M5-6": "#e34a33",
    "M6+":  "#b30000",
}

def _mag_class(m):
    if m < 3: return "M2-3"
    if m < 4: return "M3-4"
    if m < 5: return "M4-5"
    if m < 6: return "M5-6"
    return "M6+"

gdf["magnitude_class"] = gdf["magnitude"].apply(_mag_class)

# Save colormap sidecar — Sage reads this to color the map and show the legend
json.dump(
    {"field": "magnitude_class", "title": "Earthquake Magnitude", "palette": _MAG_PALETTE},
    open(output_file.replace(".geojson", ".colormap.json"), "w")
)
```

## Notes
- The API has no authentication but respect reasonable usage limits
- Response includes metadata: `count` shows total number of events returned
- The `bbox` field in response shows the spatial extent of all events
- Depth is measured in kilometers below sea level
- Alert levels (green/yellow/orange/red) indicate estimated impact severity
- `felt` reports come from USGS "Did You Feel It?" citizen science data
- For real-time monitoring, query recent time windows (last 7 days, last 24 hours)
- Documentation: https://earthquake.usgs.gov/fdsnws/event/1/
