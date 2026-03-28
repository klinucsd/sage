---
name: ndp-search
description: Search the National Data Platform catalog using a proxy API with keyword, spatial, and temporal filters.
license: Apache-2.0
---

# NDP Search Skill

Search the National Data Platform CKAN catalog via the NDP OpenSearch Proxy API. Supports keyword search, spatial filtering (bounding box), and temporal filtering (date range).

**Proxy URL:** `https://kaiucsd-ndp-opensearch.hf.space`

## Workflow Requirements

### TODO List for Complex Searches

Before executing complex searches (geographic queries, multi-filter searches, queries with 3+ requirements), create a detailed TODO list breaking down all steps:

```
User request: "Find all datasets collected from GPS stations in San Diego County since 2023"

TODO:
1. Get San Diego County geometry and bounding box from us-counties skill
2. Build search request with keywords, spatial filter, temporal filter
3. Execute search via proxy API
4. Apply post-search relevance filtering with required/excluded terms
5. MANDATORY: Verify each dataset intersects San Diego County geometry using verify_geometry_intersection()
6. Export results to JSON and GeoJSON
```

### Geographic Query Requirements

For queries specifying a county, state, or region, you MUST:
1. Use bounding box for initial spatial filtering
2. Apply post-search relevance filtering
3. MANDATORY: Verify geometry intersection using verify_geometry_intersection()

Bounding boxes are rectangular and include areas outside actual boundaries. Without geometry verification, results will include datasets from wrong locations.

Example: San Diego County's bounding box includes parts of Mexico and neighboring counties. You must verify actual intersection with county geometry.

## Implementation

### Connection
```python
import requests

PROXY_URL = "https://kaiucsd-ndp-opensearch.hf.space"
```

### Keyword Search
```python
response = requests.post(f"{PROXY_URL}/search", json={
    "query": "climate data wildfire",
    "size": 1000
})

data = response.json()
print(f"Found {data['total']} datasets")
results = data['results']  # List of dataset objects
```

### Spatial Search (Bounding Box)
```python
# bbox format: [min_lon, min_lat, max_lon, max_lat]
bbox = [-117.6, 32.5, -116.1, 33.5]

response = requests.post(f"{PROXY_URL}/search", json={
    "query": "GNSS GPS station",
    "size": 100,
    "bbox": bbox
})
```

### Temporal Search (Date Range)
```python
response = requests.post(f"{PROXY_URL}/search", json={
    "query": "temperature weather",
    "size": 100,
    "temporal_start": "2024-01-01",
    "temporal_end": "2024-12-31"
})
```

### Combined Search
```python
response = requests.post(f"{PROXY_URL}/search", json={
    "query": "GNSS GPS station receiver",
    "size": 100,
    "bbox": bbox,
    "temporal_start": "2023-01-01",
    "temporal_end": "2024-12-31"
})
```

## Processing Results

### Response Structure
```python
data = response.json()

# Total number of matching datasets
total = data['total']

# List of dataset results
results = data['results']

for dataset in results:
    dataset_name = dataset['name']  # Use for CKAN URLs
    title = dataset['title']
    description = dataset['notes']

    organization = dataset.get('organization', {})
    tags = [tag['name'] for tag in dataset.get('tags', [])]

    resources = dataset.get('resources', [])
    resource_count = dataset.get('resource_count', 0)
    resource_formats = dataset.get('resource_formats', [])

    spatial = dataset.get('spatial')  # GeoJSON Point or Polygon
    spatial_text = dataset.get('spatial_text')

    temporal_start = dataset.get('temporal_start')
    temporal_end = dataset.get('temporal_end')

    # Build CKAN URL using 'name' field
    ckan_url = f"https://nationaldataplatform.org/catalog/dataset/{dataset_name}"
```

## Query Expansion

Expand queries with synonyms and related terms:

```python
# Combine related terms, synonyms, technical variations
query = "GPS GNSS station receiver positioning geodetic"
```

## Post-Search Relevance Filtering

CRITICAL: Apply filtering after the proxy returns results to ensure datasets match user's actual request.

### Filter Function
```python
def filter_relevant_datasets(results, required_terms=None, excluded_terms=None, required_any=None):
    """
    Filter search results for semantic relevance.

    Args:
        results: List of dataset objects from proxy API
        required_terms: List of terms that MUST ALL appear (AND logic)
        excluded_terms: List of terms that indicate wrong dataset (automatic rejection)
        required_any: List of term groups where at least ONE must appear (OR logic)

    Returns:
        Filtered list of relevant datasets
    """
    filtered = []

    for dataset in results:
        searchable_text = ' '.join([
            dataset.get('title', ''),
            dataset.get('notes', ''),
            ' '.join([tag.get('name', '') for tag in dataset.get('tags', [])])
        ]).lower()

        # Check excluded terms (automatic rejection)
        if excluded_terms and any(term.lower() in searchable_text for term in excluded_terms):
            continue

        # Check required terms (all must be present)
        if required_terms and not all(term.lower() in searchable_text for term in required_terms):
            continue

        # Check required_any (at least one from each group)
        if required_any:
            matches_all_groups = True
            for term_group in required_any:
                if not any(term.lower() in searchable_text for term in term_group):
                    matches_all_groups = False
                    break
            if not matches_all_groups:
                continue

        filtered.append(dataset)

    return filtered
```

### Usage Example
```python
# User: "Find datasets for wildfire probability in California"

# Step 1: Proxy search (wide net)
response = requests.post(f"{PROXY_URL}/search", json={
    "query": "wildfire fire probability risk hazard",
    "size": 100
})

data = response.json()

# Step 2: Post-filter for relevance
filtered_results = filter_relevant_datasets(
    data['results'],
    required_terms=['wildfire'],
    required_any=[['probability', 'risk', 'hazard']],
    excluded_terms=['debris', 'post-fire', 'erosion', 'landslide']
)
```

### Term Selection Guidelines

**required_terms**: Core subject that must be mentioned
- Example: ['wildfire'], ['earthquake'], ['lidar']

**required_any**: Synonyms or alternative expressions for same concept
- Example: [['probability', 'risk', 'hazard']], [['station', 'receiver', 'site']]

**excluded_terms**: Related but different topics to reject
- Post-event data when asking for predictions: 'post-', 'aftermath', 'damage'
- Models when asking for observations: 'model', 'simulation', 'forecast'
- Derived when asking for raw: 'derived', 'product', 'processed'
- Administrative when asking for data: 'boundary', 'jurisdiction', 'operational unit'

Be generous with excluded terms. Better to exclude marginal datasets than include wrong ones.

## Geometry Verification (MANDATORY for Geographic Queries)

When user specifies a county, state, or region, you MUST verify geometry intersection after bounding box search.

### Implementation
```python
from shapely.geometry import shape

def verify_geometry_intersection(datasets, target_geometry):
    """
    MANDATORY for geographic queries. Verify datasets actually intersect target geometry.
    Bounding boxes are rectangular and include areas outside actual boundaries.

    Args:
        datasets: List of filtered dataset objects from proxy API
        target_geometry: Shapely geometry object from us-counties or us-states skill

    Returns:
        List of datasets that truly intersect target geometry
    """
    verified_datasets = []

    for dataset in datasets:
        dataset_spatial = dataset.get('spatial')

        if not dataset_spatial:
            continue

        try:
            dataset_geom = shape(dataset_spatial)
            if dataset_geom.intersects(target_geometry):
                verified_datasets.append(dataset)
        except Exception as e:
            continue

    return verified_datasets
```

### Complete Workflow for Geographic Queries
```python
# User: "Find datasets from GPS stations in San Diego County since 2023"

# Step 1: Get geometry and bounding box
san_diego_geojson = {...}  # From us-counties skill
county_geometry = shape(san_diego_geojson['geometry'])
bbox = county_geometry.bounds  # (min_lon, min_lat, max_lon, max_lat)

# Step 2: Proxy search with bounding box
response = requests.post(f"{PROXY_URL}/search", json={
    "query": "GPS GNSS station receiver",
    "size": 100,
    "bbox": list(bbox),
    "temporal_start": "2023-01-01"
})

data = response.json()

# Step 3: Relevance filtering
filtered_results = filter_relevant_datasets(
    data['results'],
    required_terms=['GPS'],
    required_any=[['station', 'receiver', 'site']],
    excluded_terms=['model', 'simulation', 'derived']
)

# Step 4: MANDATORY geometry verification
verified_results = verify_geometry_intersection(filtered_results, county_geometry)

# verified_results now contains only datasets truly in San Diego County
```

## Pagination

The proxy supports `size` (max 1000 per page) and `from` (offset) for pagination. Use `page_size=100` to keep individual responses small (some datasets have thousands of resources). Use `fetch_all_datasets` to retrieve all results automatically:

```python
def fetch_all_datasets(query, bbox=None, temporal_start=None, temporal_end=None, page_size=100):
    """Fetch all datasets matching the criteria using pagination."""
    all_results = []
    offset = 0

    while True:
        body = {"query": query, "size": page_size, "from": offset}
        if bbox is not None:
            body["bbox"] = bbox
        if temporal_start is not None:
            body["temporal_start"] = temporal_start
        if temporal_end is not None:
            body["temporal_end"] = temporal_end

        data = requests.post(f"{PROXY_URL}/search", json=body).json()
        page = data["results"]
        all_results.extend(page)

        if len(all_results) >= data["total"] or not page:
            break
        offset += len(page)

    return all_results
```

Apply post-search filtering and geometry verification AFTER retrieving all results.

## Output Format

**CRITICAL**: Present results listing top 10 datasets:
```
1. [Dataset Title]
   URL: https://nationaldataplatform.org/catalog/dataset/[name]
   Description: [clean text without HTML tags]
   Time Range: [temporal_start to temporal_end]
   Formats: [resource_formats]
```

Save the result as JSON. If all datasets have spatial locations or coverages, also save the result to GeoJSON.

Handle serialization:
- Convert numpy arrays to lists
- Convert datetime objects to strings
- Handle NaN/None values with fillna() or where()

Delete intermediate files, keep only final JSON and GeoJSON.

## Key Requirements

1. **TODO lists**: Create for all complex searches, especially geographic queries
2. **Post-search filtering**: Always apply filter_relevant_datasets() for specific queries
3. **Geometry verification**: MANDATORY for county/state/region queries - use verify_geometry_intersection()
4. **Query strategy**:
   - Avoid generic terms alone (management, system, network, data, station)
   - Use specific names, technical terms, unique identifiers
   - Do NOT use geographic names as keywords (filter spatially instead)
5. **Pagination**: Use `fetch_all_datasets()` with `page_size=100` (default); do NOT request large page sizes — some datasets have thousands of resources and will produce huge responses
6. **Meeting requirements**: Only return datasets matching user's exact request, not related topics
