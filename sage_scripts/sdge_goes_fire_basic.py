#!/usr/bin/env python3
"""
Fetch GOES fire detections (last 7 days) from SDG&E WIFIRE and save as GeoJSON.
Usage: sdge_goes_fire_basic.py <output_file>
"""
import sys, requests, geopandas as gpd, pandas as pd, os

output_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get('SAGE_OUTPUT_DIR', '/tmp'), 'goes_fire_detections.geojson'
)

resp = requests.get("https://sdge.sdsc.edu/geoserver/ows", params={
    "service": "WFS", "version": "2.0.0", "request": "GetFeature",
    "typeName": "WIFIRE:view_wfabba_goes_last_7days", "outputFormat": "application/json"
})
resp.raise_for_status()
gdf = gpd.GeoDataFrame.from_features(resp.json()["features"], crs="EPSG:4326")
gdf["lon"] = gdf.geometry.x
gdf["lat"] = gdf.geometry.y
gdf["data_time"] = pd.to_datetime(gdf["data_time"], utc=True)
gdf["hours_ago"] = (gdf["seconds_ago"].astype(float) / 3600).round(1)

gdf.to_file(output_file, driver="GeoJSON")
print(f"Loaded {len(gdf)} fire detections")
print(f"Date range: {gdf['data_time'].min()} to {gdf['data_time'].max()}")
print(gdf[["data_time", "lon", "lat", "hours_ago"]].head(10).to_string(index=False))
print(f"Saved to: {output_file}")
