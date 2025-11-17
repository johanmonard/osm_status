from __future__ import annotations

import json
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Optional

import geopandas as gpd
from shapely.geometry import shape

from .config import LayerConfig


class LayerProcessor:
    """Responsible for extracting, clipping and exporting layer data."""

    def __init__(self, processed_dir: Path, layers: Iterable[LayerConfig]):
        self.processed_dir = processed_dir
        self.layers = list(layers)

    def _geometry_df(self, polygon_geojson: dict) -> gpd.GeoDataFrame:
        polygon = shape(polygon_geojson)
        return gpd.GeoDataFrame(geometry=[polygon], crs="EPSG:4326")

    def extract_layers(
        self,
        zip_path: Path,
        polygon_geojson: dict,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> dict:
        """Extract configured layers from a downloaded Geofabrik ZIP."""

        clipping_geom = self._geometry_df(polygon_geojson)
        total_layers = len(self.layers)

        result_files: dict[str, list[Path]] = {"polygon": [], "line": []}
        per_layer_records: list[dict] = []
        fclass_registry: dict[str, set] = defaultdict(set)

        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(tmpdir)

            for idx, layer in enumerate(self.layers, start=1):
                shp_path = Path(tmpdir) / layer.shapefile
                if not shp_path.exists():
                    continue

                gdf = gpd.read_file(shp_path)
                clip_geom = clipping_geom.to_crs(gdf.crs) if gdf.crs else clipping_geom
                clipped = gpd.clip(gdf, clip_geom)
                if clipped.empty:
                    continue

                clipped = self._ensure_fclass(clipped, layer.name)
                fclass_registry[layer.geometry].update(
                    v for v in clipped["fclass"].dropna().unique()
                )

                layer_file = self._write_geojson(clipped, layer)
                per_layer_records.append(
                    {
                        "name": layer.name,
                        "geometry": layer.geometry,
                        "path": str(layer_file),
                        "feature_count": len(clipped),
                    }
                )
                result_files[layer.geometry].append(layer_file)

                if progress_callback:
                    progress_callback(idx / total_layers, f"Processed {layer.name}")

        grouped_outputs = {
            geom: str(self._merge_to_single_geojson(files, geom))
            for geom, files in result_files.items()
            if files
        }

        return {
            "layers": per_layer_records,
            "grouped": grouped_outputs,
            "fclasses": {
                geom: sorted(values)
                for geom, values in fclass_registry.items()
            },
        }

    def _write_geojson(self, gdf: gpd.GeoDataFrame, layer: LayerConfig) -> Path:
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.processed_dir / f"{layer.name}.geojson"
        gdf.to_file(out_path, driver="GeoJSON")
        return out_path

    def _merge_to_single_geojson(self, files: list[Path], geom_type: str) -> Path:
        merged_path = self.processed_dir / f"{geom_type}_layers.geojson"
        geojson_content = {"type": "FeatureCollection", "features": []}
        for file in files:
            data = json.loads(Path(file).read_text(encoding="utf-8"))
            geojson_content["features"].extend(data.get("features", []))
        merged_path.write_text(json.dumps(geojson_content), encoding="utf-8")
        return merged_path

    @staticmethod
    @staticmethod
    def _ensure_fclass(gdf: gpd.GeoDataFrame, layer_name: str) -> gpd.GeoDataFrame:
        if "fclass" not in gdf.columns:
            gdf["fclass"] = layer_name
        else:
            gdf["fclass"] = gdf["fclass"].fillna(layer_name)
        return gdf
