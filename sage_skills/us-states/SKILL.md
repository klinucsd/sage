---
name: us-states
description: Use this skill for requests related to the geometry definitions of USA states; it provides a way to get the geometries of USA states as a geodataframe.   
---

# Us-states Skill

## Description

This skill gets the geometries of USA states by quering KnowWhereGraph on FRINK using their names.

## When to Use

Use this skill when you need to:
- Find objects in some USA states by checking their geometries
- Find objects intersecting with some USA states by checking their geometries
- Get the geometries of some USA states.

## How to Use

### Step 1: Construct a SPARQL query

The following SPARQL gets the state names and their geometries for three specified states.

```
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT DISTINCT ?stateName ?stateGeometry
WHERE {
  VALUES ?inputState {
    "california"
    "nevada"
    "arizona"
  }
  ?state rdf:type <http://stko-kwg.geog.ucsb.edu/lod/ontology/AdministrativeRegion_1> ;
         rdfs:label ?stateName ;
         geo:hasGeometry/geo:asWKT ?stateGeometry .
  FILTER(STRSTARTS(STR(?state), "http://stko-kwg.geog.ucsb.edu/lod/resource/"))
  FILTER(LCASE(?stateName) = ?inputState)
}
LIMIT 50
```

Please note that use "California" rather than "California State" in the query.

Find all states and their geometries needs to add the following filter into the query:
   FILTER( STRLEN(?stateName) > 2 ) 

### Step 2: Send the SPARQL query to the FRINK endpoint

Use the library sparql_dataframe, you can do

```
    pip install sparql_dataframe

    import sparql_dataframe
    endpoint_url = "https://frink.apps.renci.org/federation/sparql"
    df = sparql_dataframe.get(endpoint_url, query)
```

This code returns a dataframe from the SPARQL query

### Step 3: Wrap the dataframe as a GeoDataframe

```
    # Identify the WKT column automatically
    wkt_col = None
    for col in df.columns:
        # Check if the column name suggests it's a geometry column
        col_lower = col.lower()
        if 'geometry' in col_lower or 'geom' in col_lower or 'wkt' in col_lower:
            # Verify it actually contains WKT by checking if MOST values start with valid WKT types
            sample = df[col].dropna().astype(str)
            if len(sample) > 0:
                valid_wkt = sample.str.match(r'^(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*\(')
                if valid_wkt.sum() / len(sample) > 0.5:  # More than 50% are valid WKT
                    wkt_col = col
                    break

    if wkt_col is None:
        raise ValueError("No WKT geometry column found in SPARQL result.")

    # Drop missing geometries
    df = df.dropna(subset=[wkt_col]).copy()

    # Convert WKT to shapely geometries
    df['geometry'] = df[wkt_col].apply(wkt.loads)

    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs="EPSG:4326")

```

## Notes

-- Unless explicitly stated, do not assume FRINK contains other types of data. 
