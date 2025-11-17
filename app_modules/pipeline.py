from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Callable

from shapely.geometry import shape

from .config import APP_CONFIG, PROCESSED_DIR, RAW_DIR
from .geofabrik import GeofabrikClient
from .processing import LayerProcessor


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def run_pipeline(
    polygon_geojson: dict,
    progress_callback: Callable[[float, str], None],
) -> dict:
    config = APP_CONFIG
    geofabrik = GeofabrikClient(
        index_url=config["geofabrik_index_url"],
        cache_path=config["geofabrik_cache"],
        chunk_size=config["download_chunk_size"],
    )
    processor = LayerProcessor(PROCESSED_DIR, config["layers"])
    polygon_geom = shape(polygon_geojson)

    progress_callback(0.05, "Resolving Geofabrik region...")
    region = geofabrik.find_region_for_geometry(polygon_geom)
    region_label = geofabrik.region_label(region)
    filename = slugify(region_label or "region")
    raw_zip = RAW_DIR / f"{filename}.zip"

    def _download_progress(pct: float, message: str):
        progress_callback(0.05 + pct * 0.45, message)

    geofabrik.download_region_shapefile(region, raw_zip, progress_callback=_download_progress)

    def _processing_progress(pct: float, message: str):
        progress_callback(0.55 + pct * 0.4, message)

    outputs = processor.extract_layers(raw_zip, polygon_geojson, progress_callback=_processing_progress)
    progress_callback(0.99, "Rendering layers...")
    result = {
        "region": region_label,
        "download_path": str(raw_zip),
        "processed": outputs,
        "polygon_geojson": polygon_geojson,
    }
    cache_path = PROCESSED_DIR / "latest_run.json"
    cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result
