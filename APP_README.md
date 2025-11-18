# Dash Geofabrik Extractor

This repository now centers on a Plotly Dash application (`app.py`) that automates the “Option 1” workflow (Geofabrik regional downloads + clipping). Older experimental scripts have been moved into the `archive/` folder and should be ignored unless explicitly referenced.

## High-level flow

1. **Upload AOI** - User uploads a polygon KML via the Dash upload widget. `polygon.py` normalizes the CRS, computes stats, and stores a GeoJSON payload in `polygon-store`.
2. **Step 1 - Download** - The "Download" button runs `download_geofabrik` to resolve the region, download the Geofabrik shapefile, and cache metadata in `storage/raw/latest_download.json`. Progress is shown in the first card.
3. **Step 2 - Processing** - The "Process archive" button runs `process_geofabrik` to clip each configured layer, optionally simplify it, and write both full and simplified GeoJSON sets under `storage/processed/` plus metadata in `storage/processed/latest_run.json`.
4. **Step 3 - Convert to MBTiles** - "Create MBTiles" invokes `convert_to_mbtiles`, which prefers the `tippecanoe` CLI when it is installed but can also fall back to a pure-Python builder (powered by `mercantile` + `mapbox-vector-tile`) to produce `storage/tileserver/osm_layers.mbtiles`. Metadata for the last run lives in `storage/tileserver/latest_mbtiles.json`.
5. **Visualize output** - `MapFigureFactory` renders polygons/lines on a Mapbox canvas with a square aspect ratio. It applies per-`fclass` coloring, provides a filter and AOI boundary toggle, and swaps between simplified and full-detail GeoJSON based on the current zoom. The app defaults to Plotly's `open-street-map` style unless you install TileServer GL or provide `MAPBOX_TOKEN`.

> **MBTiles fallback:** When `tippecanoe` is missing the "Create MBTiles" step automatically switches to the pure-Python builder so the workflow still completes, albeit more slowly on very large AOIs.
> **Local tile server:** Once an MBTiles file exists the Dash app automatically launches the bundled FastAPI/uvicorn server and exposes it through the "Local TileServer" map style so everything stays Python-only.

## Repository layout

```
app.py                    # Dash UI, callbacks, background job orchestration
app_modules/
  __init__.py             # Re-exports helpers for concise imports in app.py
  config.py               # Layer definitions, storage paths, tileserver config
  geofabrik.py            # Geofabrik index caching, region lookup, ZIP download
  mapbuilder.py           # Plotly figure factory (polygons + line traces)
  pipeline.py             # End-to-end pipeline tying downloader + processor
  polygon.py              # KML ingestion, GeoJSON serialization, area summary
  processing.py           # Layer extraction/clip/export to GeoJSON
  tasks.py                # BackgroundJobManager (threaded worker + progress)
  tiler.py                # TileServer GL config generator & optional launcher
storage/
  raw/                    # Downloaded Geofabrik archives (zip)
  processed/              # Per-layer GeoJSON + merged polygon/line collections
  tileserver/             # Tileserver config + expected MBTiles dataset
app_requirements.txt      # Python dependencies specific to the Dash app
APP_README.md             # This file
archive/                  # Legacy scripts (ignored unless explicitly needed)
```

## Key callback wiring (for quick LLM reference)

| Callback | Inputs | Outputs | Purpose |
| --- | --- | --- | --- |
| `handle_polygon_upload` | `dcc.Upload#upload-polygon.contents` | `polygon-store`, summary alert | Decode base64 KML, parse polygon, store GeoJSON + stats |
| `coordinator_button_states` | Job stores + metadata | Button disables | Keep each workflow card enabled only when prerequisites are met and no job is running |
| `start_download_job` / `monitor_download_job` | Download button / polling interval | Download progress + metadata store | Download the Geofabrik archive |
| `start_process_job` / `monitor_process_job` | Processing button / polling interval | Processing progress + `processed-store` | Clip layers and export GeoJSON |
| `start_mbtiles_job` / `monitor_mbtiles_job` | MBTiles button / polling interval | Conversion progress + MBTiles metadata | Run `tippecanoe` to build MBTiles |
| `update_map` | `processed-store`, layer mode, `fclass` filter, basemap dropdown, AOI toggle, `zoom-store` | `dcc.Graph#map-graph` | Render polygons/lines with square aspect ratio while alternating between simplified/full detail |
| `sync_fclass_filter` | `processed-store` | `MultiSelect#fclass-filter` | Populate available `fclass` options after each processing job |
| `capture_zoom` / `update_zoom_indicator` | `dcc.Graph#map-graph.relayoutData` / `zoom-store` | Zoom indicator | Persist and display the latest Mapbox zoom to drive detail switching |

`processed-store` holds the most recent processing result: `{"region": "...", "download_path": "...", "processed": {"layers": [...], "grouped": {"polygon": "<path>", "line": "<path>"}, "grouped_simple": {...}}, "polygon_geojson": {...}}`.

## Installation

Create or activate a virtual environment and install the dedicated dependencies (the Dash 3.x line brings React 18+ so Mantine works):

```bash
pip install -r app_requirements.txt
```

> NOTE: GeoPandas requires GDAL/GEOS on your system; satisfy those prerequisites first.

## Running the app

1. *(Optional but recommended)* If you already rely on TileServer GL, place an MBTiles file (e.g., `openmaptiles.mbtiles`) inside `storage/tileserver/` and ensure the `tileserver-gl` binary is on your `PATH`. Otherwise the app automatically launches the bundled FastAPI/uvicorn tile server that streams vector tiles straight from `storage/tileserver/osm_layers.mbtiles` as soon as a cached file exists (either from a previous run or immediately after Step 3 completes). You can also run it manually via `python python_tileserver.py` to keep the tiles available outside of Dash.
2. Launch the Dash server locally:

```bash
python app.py
```

3. Open `http://127.0.0.1:8050/`, upload your polygon KML, then run the workflow cards (download → processing → MBTiles) as needed. Each step is cached, so you can rerun just the portion you're tweaking. After processing, use the display controls to explore polygons/lines.

> **Tippecanoe vs Python builder:** the MBTiles step prefers the `tippecanoe` CLI defined in `APP_CONFIG["mbtiles"]["tippecanoe_cmd"]`. If the binary is missing, the app falls back to the bundled Python implementation (mercantile + mapbox-vector-tile). The fallback keeps the workflow self-contained but is slower on very large AOIs.

All intermediate outputs live in `storage/`, so they can be reused or inspected outside the app: `storage/raw/latest_download.json`, `storage/processed/latest_run.json`, and `storage/tileserver/latest_mbtiles.json` capture the last successful run for each stage. The `archive/` directory is still untouched and should only be consulted when explicitly requested.
