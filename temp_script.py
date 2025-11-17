from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import shape, mapping

from app_modules import MapFigureFactory


def main():
    polygons_path = Path("storage/processed/polygon_layers.geojson")
    lines_path = Path("storage/processed/line_layers.geojson")
    if not polygons_path.exists():
        raise SystemExit(f"{polygons_path} missing")

    polygon_data = json.loads(polygons_path.read_text(encoding="utf-8"))
    extent_geojson = None
    if polygon_data.get("features"):
        combined = shape(
            {
                "type": "GeometryCollection",
                "geometries": [feat["geometry"] for feat in polygon_data["features"]],
            }
        )
        extent_geojson = mapping(combined.envelope)

    factory = MapFigureFactory("open-street-map")
    fig = factory.build(polygons_path, lines_path, "polygon", extent_geojson=extent_geojson)
    out_html = Path("temp_map.html")
    fig.write_html(out_html)
    print(f"Wrote {out_html.resolve()}")


if __name__ == "__main__":
    main()
