from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from shapely.geometry import shape

from .config import APP_CONFIG, PROCESSED_DIR, RAW_DIR, TILESERVER_DIR
from .geofabrik import GeofabrikClient
from .processing import LayerProcessor
from .mbtiles import VectorMBTilesBuilder


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _write_metadata(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_metadata(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def download_geofabrik(
    polygon_geojson: dict,
    progress_callback: Callable[[float, str], None],
) -> dict:
    print("[download_geofabrik] Starting download step", flush=True)
    config = APP_CONFIG
    geofabrik = GeofabrikClient(
        index_url=config["geofabrik_index_url"],
        cache_path=config["geofabrik_cache"],
        chunk_size=config["download_chunk_size"],
    )
    polygon_geom = shape(polygon_geojson)

    progress_callback(0.05, "Resolving Geofabrik region...")
    region = geofabrik.find_region_for_geometry(polygon_geom)
    region_label = geofabrik.region_label(region)
    filename = slugify(region_label or "region")
    raw_zip = RAW_DIR / f"{filename}.zip"

    def _download_progress(pct: float, message: str):
        progress_callback(0.05 + pct * 0.9, message)

    print(f"[download_geofabrik] Downloading {region_label} into {raw_zip}", flush=True)
    geofabrik.download_region_shapefile(region, raw_zip, progress_callback=_download_progress)
    download_data = {
        "region": region_label,
        "download_path": str(raw_zip),
        "polygon_geojson": polygon_geojson,
        "timestamp": datetime.utcnow().isoformat(),
    }
    print(f"[download_geofabrik] Download complete -> {download_data['download_path']}", flush=True)
    _write_metadata(RAW_DIR / "latest_download.json", download_data)
    return download_data


def process_geofabrik(
    download_metadata: dict,
    progress_callback: Callable[[float, str], None],
) -> dict:
    if not download_metadata:
        raise ValueError("No download metadata found. Run the download step first.")
    print("[process_geofabrik] Starting processing step", flush=True)
    zip_path = Path(download_metadata["download_path"])
    if not zip_path.exists():
        raise FileNotFoundError(f"Downloaded archive missing: {zip_path}")

    config = APP_CONFIG
    processor = LayerProcessor(PROCESSED_DIR, config["layers"], config["simplify_tolerance"])
    polygon_geojson = download_metadata["polygon_geojson"]

    def _processing_progress(pct: float, message: str):
        progress_callback(0.05 + pct * 0.9, message)

    print(f"[process_geofabrik] Processing archive {zip_path}", flush=True)
    outputs = processor.extract_layers(zip_path, polygon_geojson, progress_callback=_processing_progress)
    processed = {
        "region": download_metadata.get("region"),
        "download_path": str(zip_path),
        "polygon_geojson": polygon_geojson,
        "processed": outputs,
        "timestamp": datetime.utcnow().isoformat(),
    }
    print("[process_geofabrik] Processing finished", flush=True)
    _write_metadata(PROCESSED_DIR / "latest_run.json", processed)
    return processed


def convert_to_mbtiles(
    processed_metadata: dict,
    progress_callback: Callable[[float, str], None],
) -> dict:
    if not processed_metadata:
        raise ValueError("No processed data available. Run the processing step first.")
    print("[convert_to_mbtiles] Starting conversion", flush=True)

    config = APP_CONFIG["mbtiles"]
    processed = processed_metadata.get("processed") or {}
    grouped_full = processed.get("grouped") or {}
    grouped_simple = processed.get("grouped_simple") or {}
    inputs: list[str] = []

    def _collect_files(group: dict):
        for path in group.values():
            if path and Path(path).exists():
                inputs.append(path)

    _collect_files(grouped_full)
    if not inputs:
        _collect_files(grouped_simple)
    if not inputs:
        raise ValueError("No GeoJSON files found to convert.")

    output_path = Path(config["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    min_zoom = config.get("min_zoom", 5)
    max_zoom = config.get("max_zoom", 12)

    tippecanoe_cmd = config.get("tippecanoe_cmd", "tippecanoe")
    tippecanoe_available = bool(tippecanoe_cmd and shutil.which(tippecanoe_cmd))

    if tippecanoe_available:
        args = [
            tippecanoe_cmd,
            "-o",
            str(output_path),
            "--force",
            "--minimum-zoom",
            str(min_zoom),
            "--maximum-zoom",
            str(max_zoom),
        ]

        for path in inputs:
            layer_name = Path(path).stem
            args.extend(["-L", f"{layer_name}:{path}"])

        progress_callback(0.2, "Launching tippecanoe...")
        print(f"[convert_to_mbtiles] Running command: {' '.join(args)}", flush=True)
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[convert_to_mbtiles] Tippecanoe failed: {result.stderr or result.stdout}", flush=True)
            raise RuntimeError(result.stderr or result.stdout or "Tippecanoe failed")
    else:
        print("[convert_to_mbtiles] Tippecanoe not found, using Python tile builder.", flush=True)
        builder_inputs = [(Path(path).stem, path) for path in inputs]
        builder = VectorMBTilesBuilder(output_path, min_zoom=min_zoom, max_zoom=max_zoom)
        progress_callback(0.2, "Building MBTiles via Python...")
        builder.build(builder_inputs)

    mbtiles_meta = {
        "mbtiles_path": str(output_path),
        "inputs": inputs,
        "timestamp": datetime.utcnow().isoformat(),
    }
    print(f"[convert_to_mbtiles] MBTiles created at {output_path}", flush=True)
    _write_metadata(TILESERVER_DIR / "latest_mbtiles.json", mbtiles_meta)
    progress_callback(1.0, "MBTiles ready.")
    return mbtiles_meta


def run_pipeline(
    polygon_geojson: dict,
    progress_callback: Callable[[float, str], None],
) -> dict:
    """Legacy helper that chains the three steps for compatibility."""

    download_meta = download_geofabrik(polygon_geojson, progress_callback)
    processed_meta = process_geofabrik(download_meta, progress_callback)
    convert_to_mbtiles(processed_meta, progress_callback)
    return processed_meta


def load_cached_download() -> dict | None:
    return _read_metadata(RAW_DIR / "latest_download.json")


def load_cached_processed() -> dict | None:
    return _read_metadata(PROCESSED_DIR / "latest_run.json")


def load_cached_mbtiles() -> dict | None:
    return _read_metadata(TILESERVER_DIR / "latest_mbtiles.json")
