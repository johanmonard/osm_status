from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple
import time
from numbers import Integral

import mercantile
import mapbox_vector_tile
from shapely.geometry import box, mapping, shape
from shapely.strtree import STRtree


def _geometry_to_geojson_dict(geom):
    """Return a GeoJSON-like dict with lists instead of tuples."""
    return json.loads(json.dumps(mapping(geom)))


@dataclass
class _LayerFeature:
    geometry: "BaseGeometry"
    properties: dict


class GeoJSONLayerIndex:
    """Spatial index wrapper around a GeoJSON file."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = Path(path)
        self.features: List[_LayerFeature] = []
        self._tree: STRtree | None = None
        self._geoms: list | None = None
        self._geom_id_map: dict[int, int] = {}
        self._fields: set[str] = set()
        self.bounds: tuple[float, float, float, float] | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        geoms = []
        for feature in data.get("features", []):
            geom_payload = feature.get("geometry")
            if not geom_payload:
                continue
            geom = shape(geom_payload)
            if geom.is_empty:
                continue
            properties = feature.get("properties") or {}
            self._fields.update(properties.keys())
            geoms.append(geom)
            self.features.append(_LayerFeature(geometry=geom, properties=properties))
        if geoms:
            self._geoms = geoms
            self._geom_id_map = {id(geom): idx for idx, geom in enumerate(geoms)}
            self._tree = STRtree(geoms)
            self.bounds = self._compute_bounds(geoms)

    @staticmethod
    def _compute_bounds(geoms: Sequence["BaseGeometry"]) -> tuple[float, float, float, float]:
        minx, miny, maxx, maxy = geoms[0].bounds
        for geom in geoms[1:]:
            gx1, gy1, gx2, gy2 = geom.bounds
            minx = min(minx, gx1)
            miny = min(miny, gy1)
            maxx = max(maxx, gx2)
            maxy = max(maxy, gy2)
        return (minx, miny, maxx, maxy)

    def iter_geometries(self) -> Iterable["BaseGeometry"]:
        for feature in self.features:
            yield feature.geometry

    def field_map(self) -> dict[str, str]:
        return {field: "String" for field in sorted(self._fields)}

    def query(self, tile_bounds) -> list[Tuple["BaseGeometry", dict]]:
        if not self._tree:
            return []
        geoms = self._geoms or []
        results = []
        try:
            hit_indexes = self._tree.query(tile_bounds, return_geometries=False)  # shapely >= 2.0.1
        except TypeError:
            raw_hits = list(self._tree.query(tile_bounds))
            if raw_hits and isinstance(raw_hits[0], Integral):  # shapely 2.0.x default behaviour
                hit_indexes = raw_hits
            else:
                hit_indexes = []
                for geom in raw_hits:
                    idx = self._geom_id_map.get(id(geom))
                    if idx is not None:
                        hit_indexes.append(idx)
        for idx in hit_indexes:
            if idx >= len(self.features):
                continue
            props = self.features[idx].properties
            geom = geoms[idx]
            clipped = geom.intersection(tile_bounds)
            if clipped.is_empty:
                continue
            results.append((clipped, props))
        return results


class VectorMBTilesBuilder:
    """Create vector MBTiles directly from GeoJSON layers."""

    def __init__(self, output_path: Path, min_zoom: int = 5, max_zoom: int = 12):
        if min_zoom > max_zoom:
            raise ValueError("min_zoom must be <= max_zoom")
        self.output_path = Path(output_path)
        self.min_zoom = min_zoom
        self.max_zoom = max_zoom

    def build(self, layers: Sequence[Tuple[str, str]]) -> dict:
        layer_indexes = [
            GeoJSONLayerIndex(name, Path(path))
            for name, path in layers
        ]
        valid_layers = [layer for layer in layer_indexes if layer.features]
        if not valid_layers:
            raise ValueError("No GeoJSON layers contained features.")
        bounds = self._combined_bounds(valid_layers)
        if not bounds:
            raise ValueError("Unable to determine dataset bounds.")

        if self.output_path.exists():
            self._safe_unlink()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.output_path) as conn:
            self._initialize_db(conn)
            self._write_metadata(conn, bounds, valid_layers)
            tile_count = 0
            for tile_id in self._collect_candidate_tiles(valid_layers):
                tile = mercantile.Tile(x=tile_id[1], y=tile_id[2], z=tile_id[0])
                encoded = self._encode_tile(tile, valid_layers)
                if encoded:
                    self._insert_tile(conn, tile, encoded)
                    tile_count += 1
            conn.commit()
        return {
            "bounds": bounds,
            "tiles_written": tile_count,
        }

    def _safe_unlink(self, retries: int = 20, delay: float = 0.5) -> None:
        for attempt in range(retries):
            try:
                self.output_path.unlink()
                return
            except FileNotFoundError:
                return
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(delay)

    def _collect_candidate_tiles(self, layers: Sequence[GeoJSONLayerIndex]) -> List[Tuple[int, int, int]]:
        tile_keys: set[Tuple[int, int, int]] = set()
        for layer in layers:
            for geom in layer.iter_geometries():
                minx, miny, maxx, maxy = geom.bounds
                for zoom in range(self.min_zoom, self.max_zoom + 1):
                    for tile in mercantile.tiles(minx, miny, maxx, maxy, zoom):
                        tile_keys.add((tile.z, tile.x, tile.y))
        return sorted(tile_keys)

    def _encode_tile(self, tile: mercantile.Tile, layers: Sequence[GeoJSONLayerIndex]) -> bytes | None:
        bounds = mercantile.bounds(tile)
        tile_bounds = box(bounds.west, bounds.south, bounds.east, bounds.north)
        layer_payload = []
        for layer in layers:
            hits = layer.query(tile_bounds)
            if not hits:
                continue
            features = []
            for geom, props in hits:
                geojson_geom = _geometry_to_geojson_dict(geom)
                features.append({"geometry": geojson_geom, "properties": props})
            if features:
                layer_payload.append({"name": layer.name, "features": features})
        if not layer_payload:
            return None
        return mapbox_vector_tile.encode(
            layer_payload,
            quantize_bounds=(bounds.west, bounds.south, bounds.east, bounds.north),
        )

    def _initialize_db(self, conn: sqlite3.Connection) -> None:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)")
        cursor.execute("DELETE FROM metadata")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS tiles (
                zoom_level INTEGER,
                tile_column INTEGER,
                tile_row INTEGER,
                tile_data BLOB
            )
            """
        )
        cursor.execute("DELETE FROM tiles")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON tiles (zoom_level, tile_column, tile_row)")
        conn.commit()

    def _write_metadata(self, conn: sqlite3.Connection, bounds: tuple[float, float, float, float], layers: Sequence[GeoJSONLayerIndex]) -> None:
        west, south, east, north = bounds
        center_lon = (west + east) / 2
        center_lat = (south + north) / 2
        metadata = [
            ("name", "OSM Layers"),
            ("description", "Generated from GeoJSON via Python builder"),
            ("format", "pbf"),
            ("bounds", f"{west},{south},{east},{north}"),
            ("center", f"{center_lon},{center_lat},{self.min_zoom}"),
            ("minzoom", str(self.min_zoom)),
            ("maxzoom", str(self.max_zoom)),
        ]
        vector_layers = []
        for layer in layers:
            vector_layers.append(
                {
                    "id": layer.name,
                    "description": "",
                    "minzoom": self.min_zoom,
                    "maxzoom": self.max_zoom,
                    "fields": layer.field_map(),
                }
            )
        metadata.append(("json", json.dumps({"vector_layers": vector_layers})))
        cursor = conn.cursor()
        cursor.executemany("INSERT INTO metadata (name, value) VALUES (?, ?)", metadata)
        conn.commit()

    def _insert_tile(self, conn: sqlite3.Connection, tile: mercantile.Tile, data: bytes) -> None:
        tms_row = (2 ** tile.z - 1) - tile.y
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
            (tile.z, tile.x, tms_row, sqlite3.Binary(data)),
        )

    @staticmethod
    def _combined_bounds(layers: Sequence[GeoJSONLayerIndex]) -> tuple[float, float, float, float] | None:
        bounds = [layer.bounds for layer in layers if layer.bounds]
        if not bounds:
            return None
        west = min(b[0] for b in bounds)
        south = min(b[1] for b in bounds)
        east = max(b[2] for b in bounds)
        north = max(b[3] for b in bounds)
        return (west, south, east, north)
