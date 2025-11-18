from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
RAW_DIR = STORAGE_DIR / "raw"
PROCESSED_DIR = STORAGE_DIR / "processed"
TILESERVER_DIR = STORAGE_DIR / "tileserver"


@dataclass(frozen=True)
class LayerConfig:
    """Configuration for a Geofabrik layer of interest."""

    name: str
    shapefile: str
    geometry: Literal["polygon", "line"]
    color: str = "#3388ff"
    line_width: float = 1.2


DEFAULT_LAYERS: list[LayerConfig] = [
    LayerConfig(
        name="buildings",
        shapefile="gis_osm_buildings_a_free_1.shp",
        geometry="polygon",
        color="#FA7921",
    ),
    LayerConfig(
        name="landuse",
        shapefile="gis_osm_landuse_a_free_1.shp",
        geometry="polygon",
        color="#91C499",
    ),
    LayerConfig(
        name="water",
        shapefile="gis_osm_water_a_free_1.shp",
        geometry="polygon",
        color="#1868AE",
    ),
    LayerConfig(
        name="roads",
        shapefile="gis_osm_roads_free_1.shp",
        geometry="line",
        color="#F3A712",
        line_width=1.6,
    ),
    LayerConfig(
        name="railways",
        shapefile="gis_osm_railways_free_1.shp",
        geometry="line",
        color="#B02E0C",
        line_width=1.4,
    ),
    LayerConfig(
        name="powerlines",
        shapefile="gis_osm_powerlines_free_1.shp",
        geometry="line",
        color="#595959",
    ),
]


APP_CONFIG = {
    "layers": DEFAULT_LAYERS,
    "geofabrik_index_url": "https://download.geofabrik.de/index-v1.json",
    "geofabrik_cache": TILESERVER_DIR / "geofabrik-index.json",
    "download_chunk_size": 1_048_576,
    # "simplify_tolerance": 0.0007,
    "simplify_tolerance": 0.005,
    "detail_zoom_threshold": 13,
    "tileserver": {
        "port": 8090,
        "config_path": TILESERVER_DIR / "tileserver.config.json",
        "mbtiles": TILESERVER_DIR / "openmaptiles.mbtiles",
        "style_url": "http://127.0.0.1:8090/styles/osm-bright/style.json",
    },
}
