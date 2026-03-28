---
name: kanawha-flood-depth
description: Display the flood depth extent for the Kanawha River at a given flow (cfs) using USACE HEC-RAS depth rasters served from GeoServer WMS.
---

# Kanawha Flood Depth Skill

## Description

Displays the USACE HEC-RAS flood depth raster for the Kanawha River as a WMS overlay on the map.
The flow is rounded to the nearest available scenario.

**Available flow scenarios (cfs):**
1,000 / 50,000 / 100,000 / 150,000 / 200,000 / 300,000 / 400,000 / 500,000 / 700,000 / 900,000 / 1,000,000 / 1,500,000 / 2,000,000

## When to Use

- User asks to show / display / visualize the flood area or flood depth for the Kanawha River at a given flow
- User asks "what does the flood look like at X cfs on the Kanawha River?"

## Example

### Show flood depth for 350,000 cfs (rounds up to 400,000 cfs)

```python
import json, os

# Available scenarios in cfs
SCENARIOS = [1_000, 50_000, 100_000, 150_000, 200_000, 300_000,
             400_000, 500_000, 700_000, 900_000, 1_000_000, 1_500_000, 2_000_000]

# ← substitute with the flow from the user's request
flow_cfs = 350_000

# Round UP to nearest scenario (conservative — never underestimate)
scenario = next((s for s in SCENARIOS if s >= flow_cfs), SCENARIOS[-1])
flow_k = scenario // 1000

wms_layer = {
    "url": "https://sparcal.sdsc.edu/geoserver/tiger/wms",
    "layers": f"tiger:Depth_{scenario}_5070",
    "name": f"Kanawha Flood {flow_k}k cfs",
    "opacity": 0.7,
    "bbox": [37.5, -82.5, 39.0, -80.5],
}

temp_dir = os.environ.get("TEMP_DIR", "/tmp")
filename = f"kanawha_flood_{flow_k}k.wms.json"
with open(os.path.join(temp_dir, filename), "w") as f:
    json.dump(wms_layer, f)
print(f"WMS layer added: {wms_layer['name']} (scenario: {scenario:,} cfs)")
```

## Notes

- **Rounding**: Always round UP to the next scenario — underestimating flood extent is worse than overestimating.
- **Data source**: USACE HEC-RAS 2D model, Kanawha River, provided by USACE Huntington District.
- **CRS**: Rasters are in EPSG:5070 (Albers NAD83); GeoServer reprojects for WMS display.
- **Depth units**: Feet above ground surface.
