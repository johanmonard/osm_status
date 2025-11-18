from __future__ import annotations

import base64
import json
import os

import dash
from dash import Dash, Input, Output, State, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_mantine_components as dmc

from app_modules import (
    APP_CONFIG,
    BackgroundJobManager,
    MapFigureFactory,
    TileServerManager,
    geometry_to_geojson,
    load_polygon_from_kml,
    polygon_summary,
    run_pipeline,
)
from app_modules.config import PROCESSED_DIR


job_manager = BackgroundJobManager()
tileserver_manager = TileServerManager(APP_CONFIG["tileserver"])
tileserver_manager.write_config()
tile_server_running = tileserver_manager.start()
mapbox_token = os.getenv("MAPBOX_TOKEN")

if tile_server_running:
    map_style = f"http://127.0.0.1:{APP_CONFIG['tileserver']['port']}/styles/osm-bright/style.json"
    tile_status = ("green", "TileServer GL is running locally.")
else:
    if mapbox_token:
        map_style = "carto-positron"
        tile_status = (
            "yellow",
            "TileServer GL binary not found. Using Mapbox tiles (requires MAPBOX_TOKEN).",
        )
    else:
        map_style = "open-street-map"
        tile_status = (
            "yellow",
            "TileServer GL binary not found and MAPBOX_TOKEN missing. Falling back to OpenStreetMap tiles.",
        )

background_options = [
    {"label": "OpenStreetMap", "value": "open-street-map"},
    {"label": "Carto Positron", "value": "carto-positron"},
    {"label": "Carto DarkMatter", "value": "carto-darkmatter"},
    {"label": "Stamen Terrain", "value": "stamen-terrain"},
    {"label": "Stamen Toner", "value": "stamen-toner"},
    {"label": "Stamen Watercolor", "value": "stamen-watercolor"},
]
if tile_server_running:
    background_options.insert(
        0,
        {"label": "Local TileServer", "value": "local"},
    )
default_background = background_options[0]["value"]

map_factory = MapFigureFactory(map_style, access_token=mapbox_token)


def load_cached_state() -> tuple[str | None, dict | None]:
    cache_path = PROCESSED_DIR / "latest_run.json"
    if not cache_path.exists():
        return None, None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        polygon_data = data.get("polygon_geojson")
        polygon_store_data = json.dumps(polygon_data) if polygon_data else None
        return polygon_store_data, data
    except Exception:
        return None, None


c_cached_polygon, c_cached_processed = load_cached_state()

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
                            dmc.Text("2. Run the Geofabrik download and processing pipeline."),
                            dmc.Group(
                                [
                                    dmc.Button("Start download & processing", id="start-processing", disabled=False),
                                    dmc.SegmentedControl(
                                        id="layer-mode",
                                        data=[
                                            {"label": "Polygons", "value": "polygon"},
                                            {"label": "Lines", "value": "line"},
                                            {"label": "Both", "value": "both"},
                                        ],
                                        value="polygon",
                                    ),
                                ],
                                justify="space-between",
                            ),
                            dmc.Space(h=10),
                            dmc.MultiSelect(
                                id="fclass-filter",
                                data=[],
                                value=[],
                                placeholder="Filtrer par fclass (défaut : toutes)",
                                nothingFoundMessage="Aucune fclass",
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
                                        label="Afficher le polygone",
                                        checked=False,
                                        onLabel="AOI",
                                        offLabel="AOI",
                                    ),
                                ],
                                grow=True,
                            ),
                            dmc.Space(h=10),
                            dmc.Progress(
                                id="job-progress",
                                value=0,
                                color="blue",
                                size="lg",
                                striped=True,
                            ),
                            dmc.Space(h=5),
                            dmc.Alert("Waiting for tasks...", id="job-alert", color="gray"),
                            dmc.Space(h=10),
                            dmc.Text(id="zoom-indicator", children="Zoom : --"),
                        ],
                        withBorder=True,
                        shadow="sm",
                        padding="lg",
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
                ],
                gap="xl",
            ),
dcc.Store(id="polygon-store", data=c_cached_polygon),
dcc.Store(id="job-store"),
dcc.Store(id="processed-store", data=c_cached_processed),
dcc.Store(id="zoom-store", data={"zoom": APP_CONFIG.get("detail_zoom_threshold", 11)}),
dcc.Interval(id="job-poll", interval=2000, disabled=True),
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
    Output("job-store", "data", allow_duplicate=True),
    Output("job-alert", "children", allow_duplicate=True),
    Output("job-alert", "color", allow_duplicate=True),
    Output("job-progress", "value", allow_duplicate=True),
    Output("job-poll", "disabled", allow_duplicate=True),
    Output("processed-store", "data", allow_duplicate=True),
    Input("start-processing", "n_clicks"),
    State("polygon-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def trigger_pipeline(n_clicks, polygon_store):
    if not n_clicks:
        raise PreventUpdate
    if not polygon_store:
        return no_update, "Upload a polygon first.", "red", 0, True, no_update

    polygon_geojson = json.loads(polygon_store)
    job = job_manager.create_job(run_pipeline, polygon_geojson=polygon_geojson)
    return (
        {"job_id": job.job_id},
        "Pipeline running...",
        "blue",
        0,
        False,
        None,
    )


@app.callback(
    Output("job-progress", "value", allow_duplicate=True),
    Output("job-alert", "children", allow_duplicate=True),
    Output("job-alert", "color", allow_duplicate=True),
    Output("job-store", "data", allow_duplicate=True),
    Output("processed-store", "data", allow_duplicate=True),
    Output("job-poll", "disabled", allow_duplicate=True),
    Input("job-poll", "n_intervals"),
    State("job-store", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def monitor_job(_n, job_meta):
    if not job_meta:
        raise PreventUpdate
    job = job_manager.get_job(job_meta["job_id"])
    if not job:
        return 0, "Unknown job", "red", None, no_update, True

    progress_value = job.progress * 100
    if job.status == "completed":
        return 100, "Finished", "green", None, job.result, True
    if job.status == "failed":
        return progress_value, job.message or "Failed", "red", None, None, True
    return progress_value, job.message or "Running...", "blue", job_meta, no_update, False


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
    if style == "local":
        if tile_server_running:
            resolved_style = f"http://127.0.0.1:{APP_CONFIG['tileserver']['port']}/styles/osm-bright/style.json"
        else:
            resolved_style = "open-street-map"
    else:
        resolved_style = style
    map_factory.style = resolved_style
    boundary = None
    if show_boundary:
        if processed_store and processed_store.get("polygon_geojson"):
            boundary = processed_store.get("polygon_geojson")
        elif polygon_store:
            boundary = json.loads(polygon_store)
    zoom_level = (zoom_state or {}).get("zoom", APP_CONFIG.get("detail_zoom_threshold", 11))
    use_simple = zoom_level < APP_CONFIG.get("detail_zoom_threshold", 11)

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




if __name__ == "__main__":
    app.run(debug=True)
@app.callback(
    Output("zoom-indicator", "children"),
    Input("zoom-store", "data"),
)
def update_zoom_indicator(zoom_state):
    state = zoom_state or {}
    if isinstance(state, list) and state:
        state = state[-1]
    if not isinstance(state, dict):
        state = {"zoom": state} if isinstance(state, (int, float)) else {}
    zoom_value = state.get("zoom")
    if zoom_value is None:
        return "Zoom : --"
    return f"Zoom : {zoom_value:.1f}"
