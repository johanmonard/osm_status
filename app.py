from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import dash
from dash import Dash, Input, Output, State, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_mantine_components as dmc

from app_modules import (
    APP_CONFIG,
    BackgroundJobManager,
    MapFigureFactory,
    TileServerManager,
    convert_to_mbtiles,
    download_geofabrik,
    geometry_to_geojson,
    load_polygon_from_kml,
    polygon_summary,
    process_geofabrik,
)
from app_modules.pipeline import load_cached_download, load_cached_mbtiles, load_cached_processed


job_manager = BackgroundJobManager()
tileserver_manager = TileServerManager(APP_CONFIG["tileserver"])
tileserver_manager.write_config()
mapbox_token = os.getenv("MAPBOX_TOKEN")

if mapbox_token:
    map_style = "carto-positron"
    tile_status = (
        "green",
        "MAPBOX_TOKEN detected. Using Mapbox tiles in the Dash map; the Python vector tile server activates automatically once Step 3 completes.",
    )
else:
    map_style = "open-street-map"
    tile_status = (
        "yellow",
        "MAPBOX_TOKEN not set. Using Plotly's OpenStreetMap tiles; the Python vector tile server activates automatically once Step 3 completes.",
    )

background_options = [
    {"label": "OpenStreetMap", "value": "open-street-map"},
    {"label": "Carto Positron", "value": "carto-positron"},
    {"label": "Carto DarkMatter", "value": "carto-darkmatter"},
    {"label": "Stamen Terrain", "value": "stamen-terrain"},
    {"label": "Stamen Toner", "value": "stamen-toner"},
    {"label": "Stamen Watercolor", "value": "stamen-watercolor"},
]
default_background = background_options[0]["value"]

map_factory = MapFigureFactory(map_style, access_token=mapbox_token)


c_cached_processed = load_cached_processed()
c_cached_download = load_cached_download()
c_cached_mbtiles = load_cached_mbtiles()

if c_cached_mbtiles:
    cached_path = Path(c_cached_mbtiles.get("mbtiles_path", ""))
    if cached_path.exists():
        tileserver_manager.start()

def _default_polygon_store() -> str | None:
    if c_cached_processed and c_cached_processed.get("polygon_geojson"):
        return json.dumps(c_cached_processed["polygon_geojson"])
    if c_cached_download and c_cached_download.get("polygon_geojson"):
        return json.dumps(c_cached_download["polygon_geojson"])
    return None

external_scripts = []
app = Dash(__name__, suppress_callback_exceptions=True)
server = app.server

app.layout = dmc.MantineProvider(
    id="theme-provider",
    theme={"colorScheme": "dark"},
    children=dmc.Container(
        [
            dmc.Stack(
                [
                    dmc.Title("OSM Geofabrik Extractor", order=2),
                    dmc.Alert(tile_status[1], title="Tile server status", color=tile_status[0]),
                    dmc.Card(
                        [
                            dmc.Text("1. Upload a polygon KML describing the area of interest."),
                            dcc.Upload(
                                id="upload-polygon",
                                children=dmc.Button("Upload KML", variant="outline"),
                                multiple=False,
                            ),
                            dmc.Space(h=10),
                            dmc.Alert(
                                "Awaiting polygon upload.",
                                id="polygon-summary",
                                color="gray",
                            ),
                            dmc.Space(h=20),
                            dmc.Text(
                                "2. Configure your map display options. Use the workflow cards below to run each step independently.",
                            ),
                            dmc.SegmentedControl(
                                id="layer-mode",
                                data=[
                                    {"label": "Polygons", "value": "polygon"},
                                    {"label": "Lines", "value": "line"},
                                    {"label": "Both", "value": "both"},
                                ],
                                value="polygon",
                            ),
                            dmc.Space(h=10),
                            dmc.MultiSelect(
                                id="fclass-filter",
                                data=[],
                                value=[],
                                placeholder="Filter by fclass (default: all)",
                                nothingFoundMessage="No classes found",
                                searchable=True,
                                clearable=True,
                            ),
                            dmc.Space(h=10),
                            dmc.Group(
                                [
                                    dmc.Select(
                                        id="map-background",
                                        data=background_options,
                                        value=default_background,
                                        label="Fond de carte",
                                    ),
                            dmc.Switch(
                                id="show-boundary",
                                label="Show polygon",
                                checked=False,
                                onLabel="AOI",
                                offLabel="AOI",
                            ),
                                ],
                                grow=True,
                            ),
                            dmc.Space(h=10),
                            dmc.Text(id="zoom-indicator", children="Zoom : --"),
                        ],
                        withBorder=True,
                        shadow="sm",
                        padding="lg",
                    ),
                    dmc.Card(
                        [
                            dmc.Title("Step 1 · Geofabrik Download", order=4),
                            dmc.Button("Download", id="download-button"),
                            dmc.Space(h=5),
                            dmc.Progress(id="download-progress", value=0, striped=True, color="blue"),
                            dmc.Space(h=5),
                            dmc.Text("Waiting...", id="download-status", c="gray"),
                            dmc.Text("", id="download-details", size="sm", c="dimmed"),
                        ],
                        withBorder=True,
                        padding="lg",
                        shadow="sm",
                    ),
                    dmc.Card(
                        [
                            dmc.Title("Step 2 · Processing", order=4),
                            dmc.Button("Process archive", id="process-button"),
                            dmc.Space(h=5),
                            dmc.Button("Run Step 2 processing", id="process-step-trigger", variant="outline"),
                            dmc.Space(h=5),
                            dmc.Progress(id="process-progress", value=0, striped=True, color="blue"),
                            dmc.Space(h=5),
                            dmc.Text("Waiting...", id="process-status", c="gray"),
                            dmc.Text("", id="process-details", size="sm", c="dimmed"),
                        ],
                        withBorder=True,
                        padding="lg",
                        shadow="sm",
                    ),
                    dmc.Card(
                        [
                            dmc.Title("Step 3 · Convert to MBTiles", order=4),
                            dmc.Button("Create MBTiles", id="mbtiles-button"),
                            dmc.Space(h=5),
                            dmc.Progress(id="mbtiles-progress", value=0, striped=True, color="blue"),
                            dmc.Space(h=5),
                            dmc.Text("Waiting...", id="mbtiles-status", c="gray"),
                            dmc.Text("", id="mbtiles-details", size="sm", c="dimmed"),
                        ],
                        withBorder=True,
                        padding="lg",
                        shadow="sm",
                    ),
                    dmc.Card(
                        [
                            dmc.Text("3. Visualize extracted layers."),
                            dmc.AspectRatio(
                                ratio=1,
                                w="100%",
                                children=dcc.Graph(
                                    id="map-graph",
                                    figure=map_factory.build(None, None, "polygon"),
                                    style={"height": "100%"},
                                ),
                            ),
                        ],
                        withBorder=True,
                        padding="lg",
                        shadow="sm",
                    ),
                    dmc.Card(
                        [
                            dmc.Title("Vector tile preview (MapLibre)", order=4),
                            dmc.Text(
                                "This preview loads the MBTiles through the built-in Python tile server once Step 3 completes.",
                                size="sm",
                                c="dimmed",
                            ),
                            html.Iframe(
                                id="local-tile-frame",
                                src="/assets/local_tiles_viewer.html",
                                style={"width": "100%", "height": "500px", "border": "none"},
                            ),
                        ],
                        id="local-tile-card",
                        withBorder=True,
                        padding="lg",
                        shadow="sm",
                        style={"display": "none"},
                    ),
                ],
                gap="xl",
            ),
            dcc.Store(id="polygon-store", data=_default_polygon_store()),
            dcc.Store(id="download-job-store"),
            dcc.Store(id="process-job-store"),
            dcc.Store(id="mbtiles-job-store"),
            dcc.Store(id="download-metadata-store", data=c_cached_download),
            dcc.Store(id="processed-store", data=c_cached_processed),
            dcc.Store(id="mbtiles-metadata-store", data=c_cached_mbtiles),
            dcc.Store(id="zoom-store", data={"zoom": APP_CONFIG.get("detail_zoom_threshold", 13)}),
            dcc.Interval(id="job-poll", interval=2000, disabled=False),
        ],
        size="xl",
        pt=30,
        pb=80,
    )
)


@app.callback(
    Output("polygon-store", "data"),
    Output("polygon-summary", "children"),
    Output("polygon-summary", "color"),
    Input("upload-polygon", "contents"),
    State("upload-polygon", "filename"),
    prevent_initial_call=True,
)
def handle_polygon_upload(contents: str | None, filename: str | None):
    if not contents:
        raise PreventUpdate
    content_type, content_string = contents.split(",", 1)
    decoded = base64.b64decode(content_string)
    geom = load_polygon_from_kml(decoded)
    summary = polygon_summary(geom)
    info = f"Bounds W/S/E/N: {summary['bounds']['west']}, {summary['bounds']['south']}, {summary['bounds']['east']}, {summary['bounds']['north']}"
    if summary.get("area_hint_sqkm"):
        info += f" | Approx area: {summary['area_hint_sqkm']:.2f} km^2"
    return (
        geometry_to_geojson(geom),
        info,
        "green",
    )


@app.callback(
    Output("download-button", "disabled"),
    Output("process-button", "disabled"),
    Output("process-step-trigger", "disabled"),
    Output("mbtiles-button", "disabled"),
    Input("download-job-store", "data"),
    Input("process-job-store", "data"),
    Input("mbtiles-job-store", "data"),
    Input("polygon-store", "data"),
    Input("download-metadata-store", "data"),
    Input("processed-store", "data"),
)
def coordinator_button_states(download_job, process_job, mbtiles_job, polygon_store, download_meta, processed_meta):
    download_running = bool(download_job and download_job.get("job_id"))
    process_running = bool(process_job and process_job.get("job_id"))
    mbtiles_running = bool(mbtiles_job and mbtiles_job.get("job_id"))

    download_disabled = download_running
    process_disabled = process_running
    mbtiles_disabled = mbtiles_running
    return download_disabled, process_disabled, process_disabled, mbtiles_disabled


@app.callback(
    Output("map-graph", "figure"),
    Input("processed-store", "data"),
    Input("layer-mode", "value"),
    Input("fclass-filter", "value"),
    Input("map-background", "value"),
    Input("show-boundary", "checked"),
    State("polygon-store", "data"),
    State("zoom-store", "data"),
)
def update_map(processed_store, mode, fclass_filter, background_value, show_boundary, polygon_store, zoom_state):
    style = background_value or default_background
    resolved_style = style
    map_factory.style = resolved_style
    boundary = None
    if show_boundary:
        if processed_store and processed_store.get("polygon_geojson"):
            boundary = processed_store.get("polygon_geojson")
        elif polygon_store:
            boundary = json.loads(polygon_store)
    zoom_level = (zoom_state or {}).get("zoom", APP_CONFIG.get("detail_zoom_threshold", 13))
    use_simple = zoom_level < APP_CONFIG.get("detail_zoom_threshold", 13)

    if not processed_store:
        extent = json.loads(polygon_store) if polygon_store else None
        return map_factory.build(None, None, mode, extent_geojson=extent, selected_fclasses=fclass_filter, boundary_geojson=boundary)
    grouped = processed_store["processed"]["grouped"]
    grouped_simple = processed_store["processed"].get("grouped_simple", {})
    polygons_path = grouped.get("polygon")
    lines_path = grouped.get("line")
    if use_simple:
        polygons_path = grouped_simple.get("polygon", polygons_path)
        lines_path = grouped_simple.get("line", lines_path)
    extent = processed_store.get("polygon_geojson")
    if not extent and polygon_store:
        extent = json.loads(polygon_store)
    return map_factory.build(
        polygons_path,
        lines_path,
        mode,
        extent_geojson=extent,
        selected_fclasses=fclass_filter,
        boundary_geojson=boundary,
    )


@app.callback(
    Output("zoom-store", "data"),
    Input("map-graph", "relayoutData"),
    State("zoom-store", "data"),
    prevent_initial_call=True,
)
def capture_zoom(relayout_data, current):
    if not relayout_data:
        raise PreventUpdate
    zoom = relayout_data.get("mapbox.zoom")
    if zoom is None:
        for key, value in relayout_data.items():
            if key.endswith("zoom"):
                zoom = value
                break
    if zoom is None:
        raise PreventUpdate
    state = current or {}
    if isinstance(state, list) and state:
        state = state[-1]
    if isinstance(state, str):
        try:
            state = {"zoom": float(state)}
        except ValueError:
            state = {}
    elif not isinstance(state, dict):
        state = {"zoom": state} if isinstance(state, (int, float)) else {}
    current_zoom = state.get("zoom")
    if current_zoom is not None and abs(current_zoom - zoom) < 0.05:
        raise PreventUpdate
    return {"zoom": zoom}


@app.callback(
    Output("zoom-indicator", "children"),
    Input("zoom-store", "data"),
)
def update_zoom_indicator(zoom_state):
    state = zoom_state or {}
    if isinstance(state, list) and state:
        state = state[-1]
    if isinstance(state, str):
        try:
            state = {"zoom": float(state)}
        except ValueError:
            state = {}
    elif not isinstance(state, dict):
        state = {"zoom": state} if isinstance(state, (int, float)) else {}
    zoom_value = state.get("zoom")
    if zoom_value is None:
        return "Zoom : --"
    return f"Zoom : {zoom_value:.1f}"


@app.callback(
    Output("download-details", "children"),
    Input("download-metadata-store", "data"),
)
def update_download_details(meta):
    if not meta:
        return "No download recorded."
    region = meta.get("region") or "N/A"
    archive = Path(meta.get("download_path", "")).name
    ts = meta.get("timestamp", "")
    return f"Region: {region} · Archive: {archive} · {ts}"


@app.callback(
    Output("process-details", "children"),
    Input("processed-store", "data"),
)
def update_process_details(meta):
    if not meta:
        return "No processing record."
    processed = meta.get("processed") or {}
    layers = processed.get("layers") or []
    total_features = sum(layer.get("feature_count", 0) for layer in layers)
    ts = meta.get("timestamp", "")
    return f"{len(layers)} layers · {total_features} features · {ts}"


@app.callback(
    Output("mbtiles-details", "children"),
    Input("mbtiles-metadata-store", "data"),
)
def update_mbtiles_details(meta):
    if not meta:
        return "No MBTiles conversion performed."
    path = Path(meta.get("mbtiles_path", "")).name
    ts = meta.get("timestamp", "")
    return f"File: {path} · {ts}"


@app.callback(
    Output("fclass-filter", "data"),
    Output("fclass-filter", "value"),
    Input("processed-store", "data"),
)
def sync_fclass_filter(processed_store):
    if not processed_store:
        return [], []
    fclasses = processed_store["processed"].get("fclasses", {})
    combined = sorted({item for values in fclasses.values() for item in values})
    options = [{"label": fclass, "value": fclass} for fclass in combined]
    return options, combined
@app.callback(
    Output("download-job-store", "data", allow_duplicate=True),
    Output("download-progress", "value", allow_duplicate=True),
    Output("download-status", "children", allow_duplicate=True),
    Output("download-status", "color", allow_duplicate=True),
    Input("download-button", "n_clicks"),
    State("polygon-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def start_download_job(n_clicks, polygon_store):
    if not n_clicks:
        raise PreventUpdate
    print(f"[callback] start_download_job triggered (n_clicks={n_clicks}, polygon_store={'set' if polygon_store else 'missing'})", flush=True)
    if not polygon_store:
        return None, 0, "Upload a polygon before downloading.", "red"
    polygon_geojson = json.loads(polygon_store)
    print("[callback] Download button accepted, creating job…", flush=True)
    job = job_manager.create_job(download_geofabrik, polygon_geojson=polygon_geojson)
    return {"job_id": job.job_id}, 5, "Downloading...", "blue"


@app.callback(
    Output("download-progress", "value", allow_duplicate=True),
    Output("download-status", "children", allow_duplicate=True),
    Output("download-status", "color", allow_duplicate=True),
    Output("download-job-store", "data", allow_duplicate=True),
    Output("download-metadata-store", "data", allow_duplicate=True),
    Input("job-poll", "n_intervals"),
    State("download-job-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def monitor_download_job(_n, job_data):
    if not job_data or not job_data.get("job_id"):
        raise PreventUpdate
    job = job_manager.get_job(job_data["job_id"])
    if not job:
        return 0, "Job not found.", "red", None, no_update
    if job.status == "completed":
        print("[callback] Download job completed", flush=True)
        return 100, "Download completed.", "green", None, job.result
    if job.status == "failed":
        print(f"[callback] Download job failed: {job.error}", flush=True)
        return 0, job.error or "Download failed.", "red", None, no_update
    progress_value = max(5, job.progress * 100)
    return progress_value, job.message or "Downloading...", "blue", job_data, no_update
@app.callback(
    Output("process-job-store", "data", allow_duplicate=True),
    Output("process-progress", "value", allow_duplicate=True),
    Output("process-status", "children", allow_duplicate=True),
    Output("process-status", "color", allow_duplicate=True),
    Input("process-button", "n_clicks"),
    Input("process-step-trigger", "n_clicks"),
    State("download-metadata-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def start_process_job(primary_clicks, secondary_clicks, download_meta):
    total_clicks = (primary_clicks or 0) + (secondary_clicks or 0)
    if total_clicks == 0:
        raise PreventUpdate
    print(f"[callback] start_process_job triggered (clicks={total_clicks}, primary={primary_clicks}, secondary={secondary_clicks}, download_meta={'set' if download_meta else 'missing'})", flush=True)
    if not download_meta:
        return None, 0, "Download data before processing.", "red"
    print("[callback] Processing button accepted, creating job…", flush=True)
    job = job_manager.create_job(process_geofabrik, download_metadata=download_meta)
    return {"job_id": job.job_id}, 5, "Processing...", "blue"


@app.callback(
    Output("process-progress", "value", allow_duplicate=True),
    Output("process-status", "children", allow_duplicate=True),
    Output("process-status", "color", allow_duplicate=True),
    Output("process-job-store", "data", allow_duplicate=True),
    Output("processed-store", "data", allow_duplicate=True),
    Input("job-poll", "n_intervals"),
    State("process-job-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def monitor_process_job(_n, job_data):
    if not job_data or not job_data.get("job_id"):
        raise PreventUpdate
    job = job_manager.get_job(job_data["job_id"])
    if not job:
        return 0, "Job not found.", "red", None, no_update
    if job.status == "completed":
        print("[callback] Processing job completed", flush=True)
        return 100, "Processing finished.", "green", None, job.result
    if job.status == "failed":
        print(f"[callback] Processing job failed: {job.error}", flush=True)
        return 0, job.error or "Processing failed.", "red", None, None
    progress_value = max(5, job.progress * 100)
    return progress_value, job.message or "Processing...", "blue", job_data, no_update
@app.callback(
    Output("mbtiles-job-store", "data", allow_duplicate=True),
    Output("mbtiles-progress", "value", allow_duplicate=True),
    Output("mbtiles-status", "children", allow_duplicate=True),
    Output("mbtiles-status", "color", allow_duplicate=True),
    Input("mbtiles-button", "n_clicks"),
    State("processed-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def start_mbtiles_job(n_clicks, processed_store):
    if not n_clicks:
        raise PreventUpdate
    print(f"[callback] start_mbtiles_job triggered (n_clicks={n_clicks}, processed_store={'set' if processed_store else 'missing'})", flush=True)
    if not processed_store:
        return None, 0, "No processed GeoJSON available.", "red"
    print("[callback] MBTiles button accepted, stopping tile server before rebuild…", flush=True)
    tileserver_manager.stop()
    print("[callback] MBTiles button accepted, creating job…", flush=True)
    job = job_manager.create_job(convert_to_mbtiles, processed_metadata=processed_store)
    return {"job_id": job.job_id}, 5, "Converting...", "blue"


@app.callback(
    Output("mbtiles-progress", "value", allow_duplicate=True),
    Output("mbtiles-status", "children", allow_duplicate=True),
    Output("mbtiles-status", "color", allow_duplicate=True),
    Output("mbtiles-job-store", "data", allow_duplicate=True),
    Output("mbtiles-metadata-store", "data", allow_duplicate=True),
    Input("job-poll", "n_intervals"),
    State("mbtiles-job-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def monitor_mbtiles_job(_n, job_data):
    if not job_data or not job_data.get("job_id"):
        raise PreventUpdate
    job = job_manager.get_job(job_data["job_id"])
    if not job:
        return 0, "Job not found.", "red", None, no_update
    if job.status == "completed":
        print("[callback] MBTiles job completed", flush=True)
        tileserver_manager.start()
        return 100, "MBTiles ready.", "green", None, job.result
    if job.status == "failed":
        print(f"[callback] MBTiles job failed: {job.error}", flush=True)
        return 0, job.error or "Conversion failed.", "red", None, no_update
    progress_value = max(5, job.progress * 100)
    return progress_value, job.message or "Converting...", "blue", job_data, no_update


@app.callback(
    Output("local-tile-card", "style"),
    Input("mbtiles-metadata-store", "data"),
    Input("mbtiles-job-store", "data"),
)
def toggle_local_tile_card(meta, mbtiles_job):
    if mbtiles_job and mbtiles_job.get("job_id"):
        return {"display": "none"}
    if meta and meta.get("mbtiles_path"):
        tileserver_manager.start()
        return {"display": "block"}
    return {"display": "none"}


if __name__ == "__main__":
    app.run(debug=True)
