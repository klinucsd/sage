---
name: ndp-search
description: Search the National Data Platform catalog using keyword, spatial, and temporal filters. Use for finding datasets, sensors, stations, or services in NDP by topic, location, or time range.
license: Apache-2.0
---

# NDP Search Skill

Search the National Data Platform CKAN catalog via the NDP CQE Search API.

**API base URL:** `http://awesome-compute.sdsc.edu:8081`

## Step 1 — Search

```python
import requests, re

API_URL = "http://awesome-compute.sdsc.edu:8081"

def ndp_search(text, bbox=None, start_time=None, end_time=None, rows=100, start=0):
    """
    Search NDP catalog.

    bbox: [min_lon, min_lat, max_lon, max_lat]
    start_time / end_time: ISO date strings e.g. "2024-01-01"
    """
    params = {"text": text, "rows": rows, "start": start}
    if bbox:
        # Solr ENVELOPE order: (min_lon, max_lon, max_lat, min_lat)
        params["location"] = f"ENVELOPE({bbox[0]},{bbox[2]},{bbox[3]},{bbox[1]})"
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    resp = requests.get(f"{API_URL}/v1/cqe/search_adv", params=params)
    resp.raise_for_status()
    return resp.json()
```

## Step 2 — Normalize Results

The API returns raw Solr documents where every field is an array. Always normalize before using:

```python
def _extract_center(wkt):
    """Extract center (lon, lat) from a WKT polygon."""
    coords = re.findall(r'[-\d.]+', wkt)
    if not coords:
        return None, None
    lons = [float(coords[i]) for i in range(0, len(coords), 2)]
    lats = [float(coords[i]) for i in range(1, len(coords), 2)]
    return round(sum(lons)/len(lons), 6), round(sum(lats)/len(lats), 6)

def normalize_result(r):
    """Convert raw Solr result to a clean, usable dict."""
    name = r.get("name", [""])[0]
    wkt  = r.get("spatial_elements", "")
    lon, lat = _extract_center(wkt)
    temporal = r.get("temporal_elements", [])
    res_names   = r.get("resources_name", [])
    res_urls    = r.get("resources_url", [])
    res_formats = r.get("resources_format", [])
    return {
        "id":             r.get("ckan-id", [""])[0],
        "name":           name,
        "title":          r.get("title", [""])[0],
        "notes":          r.get("notes", [""])[0],
        "organization":   r.get("organization_title", [""])[0],
        "tags":           r.get("tags", []),
        "resources":      [{"name": n, "url": u, "format": f}
                           for n, u, f in zip(res_names, res_urls, res_formats)],
        "lon":            lon,
        "lat":            lat,
        "temporal_start": temporal[0] if len(temporal) > 0 else None,
        "temporal_end":   temporal[1] if len(temporal) > 1 else None,
        "ckan_url":       f"https://nationaldataplatform.org/catalog/dataset/{name}",
    }

def normalize_all(data):
    return [normalize_result(r) for r in data.get("results", [])]
```

## Step 3 — Fetch All Pages (for large result sets)

```python
def fetch_all_datasets(text, bbox=None, start_time=None, end_time=None, page_size=100):
    all_results = []
    offset = 0
    while True:
        data = ndp_search(text, bbox=bbox, start_time=start_time,
                          end_time=end_time, rows=page_size, start=offset)
        page = data.get("results", [])
        all_results.extend(page)
        if len(all_results) >= data.get("hits", 0) or not page:
            break
        offset += len(page)
    return normalize_all({"results": all_results})
```

## Step 4 — Post-Search Relevance Filtering

```python
def filter_relevant_datasets(datasets, required_terms=None, excluded_terms=None, required_any=None):
    """
    datasets: list of normalized dicts from normalize_all()
    required_terms: all must appear (AND)
    excluded_terms: any present → reject
    required_any: list of groups; at least one term per group must appear (OR within group, AND across groups)
    """
    filtered = []
    for d in datasets:
        text = f"{d['title']} {d['notes']} {' '.join(d['tags'])}".lower()
        if excluded_terms and any(t.lower() in text for t in excluded_terms):
            continue
        if required_terms and not all(t.lower() in text for t in required_terms):
            continue
        if required_any:
            if not all(any(t.lower() in text for t in grp) for grp in required_any):
                continue
        filtered.append(d)
    return filtered
```

## Step 5 — Geometry Verification (MANDATORY for county/state/region queries)

Bounding boxes are rectangular and include areas outside the actual boundary. Always verify after spatial search.

```python
from shapely import wkt as shapely_wkt
from shapely.geometry import Point

def verify_geometry_intersection(datasets, target_geometry):
    """Keep only datasets whose location truly intersects target_geometry."""
    verified = []
    for d in datasets:
        lon, lat = d.get("lon"), d.get("lat")
        if lon is None or lat is None:
            continue
        try:
            if Point(lon, lat).intersects(target_geometry):
                verified.append(d)
        except Exception:
            continue
    return verified
```

## Complete Example

```python
# "Find GNSS stations within 100 miles of lat=39.1675, lon=-119.0238"
# 100 miles ≈ 1.45 degrees
lat, lon = 39.1675, -119.0238
pad = 1.45
bbox = [lon - pad, lat - pad, lon + pad, lat + pad]

datasets = fetch_all_datasets("GNSS GPS station", bbox=bbox)

stations = filter_relevant_datasets(
    datasets,
    required_any=[["gnss", "gps", "station", "receiver"]],
    excluded_terms=["model", "simulation"]
)

for s in stations:
    print(f"{s['title']:30s}  lat={s['lat']}, lon={s['lon']}")
    for res in s['resources']:
        print(f"  [{res['format']}] {res['url']}")
```

## Output Format

Present results as a numbered list (top 10):
```
1. [Title]  (lat, lon)
   Organization: ...
   Time range: temporal_start → temporal_end
   Resources: CSV, PNG, ...
   CKAN: https://nationaldataplatform.org/catalog/dataset/[name]
```

Save final results as JSON. If datasets have coordinates, also save as GeoJSON (point features).

## Key Rules

1. **Always normalize** — call `normalize_all()` before using any result fields.
2. **Expand keywords** — use synonyms and technical terms (e.g., `"GPS GNSS station receiver geodetic"`).
3. **No geographic keywords** — filter spatially via `bbox`, not by putting place names in `text`.
4. **Geometry verification** — mandatory for county/state/region queries.
5. **Spatial envelope order** — `ENVELOPE(min_lon, max_lon, max_lat, min_lat)` — Solr's order has max_lat before min_lat.
