---
name: sdge-surface-fuels
description: Display surface fuel model and live fuel moisture content for Southern California from SDG&E WIFIRE. Use for questions about wildfire fuel conditions, fuel loads, live fuel moisture, fire risk terrain, and surface fuel maps in the San Diego and Southern California region. Also use when asked to color or classify fire detections by fuel moisture risk level.
license: Apache-2.0
---

# SDG&E Surface Fuels Skill

Displays surface fuel model and live fuel moisture layers for Southern California from the SDG&E WIFIRE GeoServer. Both layers are rendered as interactive WMS maps.

**Available layers:**
- **Surface Fuel Model** (`lf-fbfm40-230`) — Fire Behavior Fuel Model 40 (FBFM40), classifies terrain by fuel type and load
- **Live Fuel Moisture** (`lfmc_abi`) — Near real-time live fuel moisture content (%) from GOES ABI satellite

## Display Surface Fuel Model Map

```python
import json, os

output_dir = os.environ.get('SAGE_OUTPUT_DIR', '/tmp')

wms = {
    "url": "https://sdge.sdsc.edu/geoserver/ows",
    "layers": "WIFIRE:lf-fbfm40-230",
    "name": "Surface Fuel Model (FBFM40)",
    "opacity": 0.75,
    "bbox": [32.0, -118.5, 34.5, -115.5]
}
wms_path = os.path.join(output_dir, "surface_fuels.wms.json")
with open(wms_path, "w") as f:
    json.dump(wms, f)
print(f"Surface fuel model layer saved: {wms_path}")
```

## Display Live Fuel Moisture Map

```python
import json, os

output_dir = os.environ.get('SAGE_OUTPUT_DIR', '/tmp')

wms = {
    "url": "https://sdge.sdsc.edu/geoserver/ows",
    "layers": "WIFIRE:lfmc_abi",
    "name": "Live Fuel Moisture Content (%)",
    "opacity": 0.75,
    "bbox": [32.0, -118.5, 34.5, -115.5]
}
wms_path = os.path.join(output_dir, "live_fuel_moisture.wms.json")
with open(wms_path, "w") as f:
    json.dump(wms, f)
print(f"Live fuel moisture layer saved: {wms_path}")
```

## Classification Helper: color any fire detection GeoJSON by live fuel moisture

Use this when asked to color or classify fire detections by fuel moisture risk. Include this function in your script and call it on any GeoDataFrame that has `lon` and `lat` columns.

```python
def classify_by_fuel_moisture(gdf):
    """
    Downloads live fuel moisture raster from SDG&E WIFIRE, samples each point,
    classifies by risk level, and adds 'fuel_moisture', 'risk', and '_color' columns.
    risk values: 'critically dry', 'moderate', 'safe', 'unknown'
    _color values: 'red', 'orange', 'green', 'gray'
    """
    import requests, numpy as np, rasterio
    from io import BytesIO

    MINX, MINY, MAXX, MAXY = -124.0, 32.0, -114.0, 42.0
    IMG_W, IMG_H = 600, 600
    resp = requests.get("https://sdge.sdsc.edu/geoserver/ows", params={
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": "WIFIRE:lfmc_abi",
        "bbox": f"{MINX},{MINY},{MAXX},{MAXY}",
        "width": IMG_W, "height": IMG_H,
        "srs": "EPSG:4326", "format": "image/geotiff", "styles": ""
    })
    resp.raise_for_status()

    with rasterio.open(BytesIO(resp.content)) as src:
        band = src.read(1).astype(float)
        nodata = src.nodata
        def _sample(lon, lat):
            col = int((lon - MINX) / (MAXX - MINX) * IMG_W)
            row = int((MAXY - lat) / (MAXY - MINY) * IMG_H)
            if 0 <= row < IMG_H and 0 <= col < IMG_W:
                val = band[row, col]
                if nodata is not None and val == nodata:
                    return None
                return float(val) if val > 0 else None
            return None
        gdf = gdf.copy()
        gdf["fuel_moisture"] = gdf.apply(lambda r: _sample(r["lon"], r["lat"]), axis=1)

    def _risk(fm):
        if fm is None or (isinstance(fm, float) and np.isnan(fm)):
            return "unknown"
        if fm < 80:
            return "critically dry"
        if fm < 100:
            return "moderate"
        return "safe"

    COLORS = {"critically dry": "red", "moderate": "orange", "safe": "green", "unknown": "gray"}
    gdf["risk"] = gdf["fuel_moisture"].apply(_risk)
    gdf["_color"] = gdf["risk"].map(COLORS)
    return gdf
```

## Notes

- Low live fuel moisture (< 80%) indicates high fire danger
- FBFM40 fuel model classes range from grass/shrub to timber litter — higher fuel load classes carry more fire risk
- Both WMS layers cover Southern California (lat 32–34.5°N, lon 118.5–115.5°W)
- These are WMS layers — they display as map tiles, not raw data
