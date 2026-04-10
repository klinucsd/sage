---
name: sdge-goes-fire
description: Retrieve GOES satellite fire detections for Southern California from SDG&E WIFIRE. Use for questions about recent wildfires, active fire hotspots, satellite-detected fires, fire radiative power, and fire activity trends in the San Diego and Southern California region.
license: Apache-2.0
---

# SDG&E GOES Fire Detection Skill

Fetches GOES satellite fire detections from the SDG&E WIFIRE GeoServer. Data updated in near real-time.

## Task: Fetch recent fire detections

Run the pre-installed script. Pass the output path as the first argument — choose a filename that fits the context.

```python
import subprocess, sys, os
output_dir = os.environ.get('SAGE_OUTPUT_DIR', '/tmp')
output_file = os.path.join(output_dir, 'fire_detections.geojson')
result = subprocess.run(
    [sys.executable, '/opt/sage_scripts/sdge_goes_fire_basic.py', output_file],
    capture_output=True, text=True, env={**os.environ}
)
print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
```

The script saves a GeoJSON with fields: `data_time`, `lon`, `lat`, `hours_ago`.

## Analysis Tips

- `hours_ago` — how many hours since the detection
- `data_time` — UTC timestamp of the detection
- Group detections by date: `gdf.groupby(gdf["data_time"].dt.date).size()`
- Filter to a county: use the us-counties skill to get county geometry, then spatial join
- To color fire points by fuel moisture risk, use the sdge-surface-fuels skill
