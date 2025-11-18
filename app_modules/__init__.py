"""
Helper modules powering the Dash-based OSM extraction app.

The package exposes curated utilities so `app.py` stays focused on UI wiring.
"""

from .config import APP_CONFIG, LayerConfig
from .geofabrik import GeofabrikClient
from .mapbuilder import MapFigureFactory
from .pipeline import convert_to_mbtiles, download_geofabrik, process_geofabrik, run_pipeline
from .polygon import geometry_to_geojson, load_polygon_from_kml, polygon_summary
from .processing import LayerProcessor
from .tasks import BackgroundJobManager
from .tiler import TileServerManager
from .py_tileserver import PythonTileServer

__all__ = [
    "APP_CONFIG",
    "LayerConfig",
    "GeofabrikClient",
    "LayerProcessor",
    "MapFigureFactory",
    "BackgroundJobManager",
    "TileServerManager",
    "PythonTileServer",
    "load_polygon_from_kml",
    "polygon_summary",
    "geometry_to_geojson",
    "run_pipeline",
    "download_geofabrik",
    "process_geofabrik",
    "convert_to_mbtiles",
]
