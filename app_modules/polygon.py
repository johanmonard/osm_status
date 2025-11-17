from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
from pyproj import Geod
from shapely.geometry import MultiPolygon, Polygon, mapping


GEOD = Geod(ellps="WGS84")


def load_polygon_from_kml(content: bytes) -> Polygon | MultiPolygon:
    """
    Load a polygon or multipolygon from a KML payload.

    The temporary file indirection keeps geopandas/fiona happy on every OS.
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "area.kml"
        tmp_path.write_bytes(content)
        gdf = gpd.read_file(tmp_path)
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)

    if gdf.empty:
        raise ValueError("The uploaded KML file does not contain any geometry.")

    geom = gdf.unary_union
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    raise ValueError("The uploaded geometry must be a polygon or multipolygon.")


def polygon_summary(geom: Polygon | MultiPolygon) -> dict[str, Any]:
    """Return quick stats for UI display."""

    area, _ = GEOD.geometry_area_perimeter(geom)
    area_sqkm = abs(area) / 1_000_000
    minx, miny, maxx, maxy = geom.bounds
    return {
        "bounds": {
            "west": round(minx, 6),
            "south": round(miny, 6),
            "east": round(maxx, 6),
            "north": round(maxy, 6),
        },
        "area_hint_sqkm": area_sqkm,
    }


def geometry_to_geojson(geom: Polygon | MultiPolygon) -> str:
    """Serialize the geometry to a GeoJSON string for persisting in dcc.Store."""

    return json.dumps(mapping(geom))
