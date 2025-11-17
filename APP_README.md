# Dash Geofabrik Extractor

This repository now centers on a Plotly Dash application (`app.py`) that automates the “Option 1” workflow (Geofabrik regional downloads + clipping). Older experimental scripts have been moved into the `archive/` folder and should be ignored unless explicitly referenced.

## High-level flow

1. **Upload AOI** – User provides a polygon KML via Dash’s upload control. `polygon.py` parses it, normalizes CRS to EPSG:4326, computes quick stats, and stores a GeoJSON payload inside `dcc.Store#polygon-store`.
2. **Run pipeline** – Clicking “Start download & processing” triggers `BackgroundJobManager` which runs `run_pipeline` in a worker thread. Job status is polled by `dcc.Interval#job-poll`.
3. **Resolve & download region** – `GeofabrikClient` reads the cached `index-v1.json`, finds the most specific polygon intersecting the AOI, then streams the corresponding shapefile ZIP into `storage/raw/<region>.zip` while reporting progress.
4. **Extract + clip layers** – `LayerProcessor` unzips the bundle, clips the configured shapefiles (see `config.DEFAULT_LAYERS`) to the AOI, and writes individual GeoJSON files under `storage/processed/`. Polygon and line layers are additionally merged into `polygon_layers.geojson` and `line_layers.geojson` for easy plotting.
5. **Visualize output** - Once the job completes, `MapFigureFactory` builds a Plotly Mapbox figure showing either polygons or lines based on the segmented control. The map now auto-fits to your AOI, keeps a square aspect ratio, colors each `fclass` distinctly, lets you toggle specific `fclass` values via a multiselect control, and offers a checkbox for overlaying the AOI boundary. Hovering any feature displays its `fclass`. If a local TileServer GL binary is present, `TileServerManager` auto-writes a config in `storage/tileserver/` and serves vector tiles on `localhost:8090`; otherwise the map falls back to the public `open-street-map` style (or Mapbox tiles when `MAPBOX_TOKEN` is set).

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
| `trigger_pipeline` | Button click + `polygon-store` | `job-store`, progress controls | Start threaded pipeline job, reset UI state |
| `monitor_job` | `dcc.Interval#job-poll` + `job-store` | progress bar, alerts, `processed-store` | Poll BackgroundJobManager until success/failure |

`processed-store` holds the pipeline result: `{"region": "...", "download_path": "...", "processed": {"layers": [...], "grouped": {"polygon": "<path>", "line": "<path>"}}}`.

## Installation

Create or activate a virtual environment and install the dedicated dependencies (the Dash 3.x line brings React 18+ so Mantine works):

```bash
pip install -r app_requirements.txt
```

> NOTE: GeoPandas requires GDAL/GEOS on your system; satisfy those prerequisites first.

## Running the app

1. *(Optional but recommended)* Place an MBTiles file (e.g., `openmaptiles.mbtiles`) inside `storage/tileserver/` and install TileServer GL (`npm install -g tileserver-gl`). The app writes `tileserver.config.json` and attempts to start the server on port `8090`. If TileServer GL is unavailable you can provide a Mapbox token via the `MAPBOX_TOKEN` environment variable; otherwise the app automatically falls back to Plotly's built-in `open-street-map` style. You can also switch to other free basemaps (Carto, Stamen, etc.) via the dropdown in the UI.
2. Launch the Dash server locally:

```bash
python app.py
```

3. Open `http://127.0.0.1:8050/`, upload your polygon KML, start the pipeline, and choose between polygon/line layers via the segmented control.

All intermediate outputs live in `storage/`, so they can be reused or inspected outside the app. The app also caches the most recent run under `storage/processed/latest_run.json`, allowing the UI to preload your last AOI and extracted layers on restart. The `archive/` directory is left untouched and should only be consulted when explicitly requested.
