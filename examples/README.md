# ARGUS Example Notebooks

These notebooks demonstrate ARGUS across multiple scientific domains.
GitHub's notebook renderer does not handle embedded maps and charts well —
use the nbviewer links below for the best experience.

| Notebook | Description | View |
|----------|-------------|------|
| [canopy_height.ipynb](canopy_height.ipynb) | Forest canopy height prediction: GEDI L2A lidar ground truth, Sentinel-2 optical, Sentinel-1 SAR, and COP30 topography; staged Random Forest comparison showing the incremental R² gain from each data source | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/canopy_height.ipynb) |
| [clm_mcp.ipynb](clm_mcp.ipynb) | MCP integration: `%%mcp` registers a California Landscape Metrics server, zonal statistics for carbon turnover time and annual burn probability across California counties | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/clm_mcp.ipynb) |
| [earthquake_gnss.ipynb](earthquake_gnss.ipynb) | Multi-step geophysical reasoning: USGS earthquake retrieval, GNSS station discovery, time-series displacement analysis | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/earthquake_gnss.ipynb) |
| [exoplanet_transits.ipynb](exoplanet_transits.ipynb) | Astronomy: NASA Exoplanet Archive catalog, interactive planet picker (`sage-dropdown`), TESS/Kepler light curve from MAST, phase-folded transit, transit summary card with depth, duration, and Rp/Rs | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/exoplanet_transits.ipynb) |
| [flood_impacts.ipynb](flood_impacts.ipynb) | Geospatial data integration: Kanawha River flood depth, school and commercial building impacts, elderly population exposure | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/flood_impacts.ipynb) |
| [fusion_study.ipynb](fusion_study.ipynb) | DIII-D tokamak analysis: plasma shape characterization, current and energy evolution, multi-shot elongation statistics | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/fusion_study.ipynb) |
| [kanawha_river.ipynb](kanawha_river.ipynb) | Terrain and floodplain analysis: NHD main channel, 3DEP DEM overlay, longitudinal elevation profile, river reaches, Relative Elevation Model (REM) for a clipped reach | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/kanawha_river.ipynb) |
| [sdge_fire.ipynb](sdge_fire.ipynb) | Wildfire situational awareness: GOES satellite fire detections, live fuel moisture mapping, per-detection risk classification, day-by-day activity trend | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/sdge_fire.ipynb) |
| [skills_manage.ipynb](skills_manage.ipynb) | Runtime skill acquisition: SkillsMP marketplace search, live skill installation, hurricane emergency response planning | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/skills_manage.ipynb) |
| [usgs_3dep.ipynb](usgs_3dep.ipynb) | USGS 3DEP airborne LiDAR: interactive coverage map with reactive bbox→dataset dropdown linkage (`sage-bbox-map` + `sage-dropdown`), point cloud download via Entwine Point Tile, 3D preview, 1-meter DEM with `gdaldem` hillshade overlay | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/usgs_3dep.ipynb) |
| [vegetation_activities.ipynb](vegetation_activities.ipynb) | California vegetation treatment tracking: Interagency Tracking System (ITS V2.0) ArcGIS Feature Service, prescribed fire and mechanical fuels reduction activities, year-over-year comparison by county | [▶ Open in nbviewer](https://nbviewer.org/github/klinucsd/sage/blob/main/examples/vegetation_activities.ipynb) |
