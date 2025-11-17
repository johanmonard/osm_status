from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import plotly.graph_objects as go
from plotly.colors import qualitative
from shapely.geometry import shape


class MapFigureFactory:
    """Creates Plotly Mapbox figures from processed GeoJSON layers."""

    def __init__(self, style: str, access_token: str | None = None, polygon_opacity: float = 0.5):
        self.style = style
        self.access_token = access_token
        self.polygon_opacity = polygon_opacity

    def _load_geojson(self, path: Optional[Path | str]) -> Optional[dict]:
        if not path:
            return None
        resolved = Path(path)
        if not resolved.exists():
            return None
        data = json.loads(resolved.read_text(encoding="utf-8"))
        if not data.get("features"):
            return None
        return data

    def build(
        self,
        polygons_geojson: Optional[Path],
        lines_geojson: Optional[Path],
        selection: str,
        extent_geojson: Optional[dict] = None,
        selected_fclasses: Optional[list[str]] = None,
        boundary_geojson: Optional[dict] = None,
    ) -> go.Figure:
        fig = go.Figure()

        polygons_data = self._load_geojson(polygons_geojson)
        lines_data = self._load_geojson(lines_geojson)
        allowed = set(selected_fclasses or [])
        color_map = self._build_color_map(polygons_data, lines_data)

        if selection in {"polygon", "both"} and polygons_data:
            for fclass, payload in self._group_polygons(polygons_data, allowed, color_map).items():
                fig.add_trace(
                    go.Choroplethmapbox(
                        geojson=payload["geojson"],
                        locations=payload["locations"],
                        z=[payload["value"]] * len(payload["locations"]),
                        colorscale=[[0, payload["rgba"]], [1, payload["rgba"]]],
                        showscale=False,
                        marker_line_width=0.2,
                        name=fclass,
                        showlegend=True,
                        customdata=[[fclass]] * len(payload["locations"]),
                        hovertemplate="fclass: %{customdata[0]}<extra></extra>",
                    )
                )

        if selection in {"line", "both"} and lines_data:
            for fclass, payload in self._group_lines(lines_data, allowed, color_map).items():
                fig.add_trace(
                    go.Scattermapbox(
                        lat=payload["lat"],
                        lon=payload["lon"],
                        mode="lines",
                        line={"width": 2, "color": payload["color"]},
                        name=fclass,
                        showlegend=True,
                        hoverinfo="text",
                        text=[fclass if val is not None else "" for val in payload["lat"]],
                    )
                )
        if boundary_geojson:
            boundary_lat, boundary_lon = self._boundary_coords(boundary_geojson)
            if boundary_lat and boundary_lon:
                fig.add_trace(
                    go.Scattermapbox(
                        lat=boundary_lat,
                        lon=boundary_lon,
                        mode="lines",
                        line={"color": "#FFFFFF", "width": 3},
                        name="AOI boundary",
                        hoverinfo="skip",
                        showlegend=True,
                    )
                )

        center = {"lat": 0, "lon": 0}
        zoom = 3
        if extent_geojson:
            geom = shape(extent_geojson)
            lon, lat = geom.centroid.x, geom.centroid.y
            center = {"lat": lat, "lon": lon}
            zoom = self._compute_zoom(geom)

        mapbox_layout = {
            "style": self.style,
            "center": center,
            "zoom": zoom,
        }
        if self.access_token:
            mapbox_layout["accesstoken"] = self.access_token

        fig.update_layout(
            mapbox=mapbox_layout,
            margin={"l": 0, "r": 0, "t": 0, "b": 0},
            showlegend=True,
        )

        return fig

    @staticmethod
    def _lines_to_latlon(geojson_data: dict) -> tuple[list[float], list[float]]:
        lat, lon = [], []
        for feature in geojson_data.get("features", []):
            geom = shape(feature["geometry"])
            if geom.geom_type == "LineString":
                coords = list(geom.coords)
                lon.extend([c[0] for c in coords] + [None])
                lat.extend([c[1] for c in coords] + [None])
            elif geom.geom_type == "MultiLineString":
                for line in geom.geoms:
                    coords = list(line.coords)
                    lon.extend([c[0] for c in coords] + [None])
                    lat.extend([c[1] for c in coords] + [None])
        return lat, lon

    @staticmethod
    def _ensure_feature_ids(geojson_data: dict) -> list[str]:
        locations: list[str] = []
        for idx, feature in enumerate(geojson_data.get("features", [])):
            fid = feature.get("id")
            if fid is None:
                fid = str(idx)
                feature["id"] = fid
            locations.append(str(fid))
        return locations

    @staticmethod
    def _compute_zoom(geom) -> float:
        minx, miny, maxx, maxy = geom.bounds
        span = max(maxx - minx, maxy - miny, 1e-6)
        zoom = math.log2(360 / span)
        return max(2, min(zoom, 16))

    def _build_color_map(self, polygons: Optional[dict], lines: Optional[dict]) -> dict[str, str]:
        palettes = qualitative.Alphabet + qualitative.Dark24 + qualitative.Plotly + qualitative.Safe
        color_map: dict[str, str] = {}
        idx = 0
        for dataset in filter(None, [polygons, lines]):
            for feature in dataset.get("features", []):
                fclass = feature.get("properties", {}).get("fclass")
                if not fclass or fclass in color_map:
                    continue
                color_map[fclass] = palettes[idx % len(palettes)]
                idx += 1
        return color_map

    def _group_polygons(self, data: dict, allowed: set[str], color_map: dict[str, str]) -> dict[str, dict]:
        grouped: dict[str, dict] = {}
        for feature in data.get("features", []):
            fclass = feature.get("properties", {}).get("fclass", "unknown")
            if allowed and fclass not in allowed:
                continue
            grouped.setdefault(fclass, {"features": [], "color": color_map.get(fclass, "#3388ff")})
            grouped[fclass]["features"].append(feature)

        output: dict[str, dict] = {}
        for fclass, payload in grouped.items():
            geojson = {"type": "FeatureCollection", "features": payload["features"]}
            locations = self._ensure_feature_ids(geojson)
            rgba_color = self._to_rgba(payload["color"], self.polygon_opacity)
            output[fclass] = {
                "geojson": geojson,
                "locations": locations,
                "color": payload["color"],
                "rgba": rgba_color,
                "value": hash(fclass) % 1000 or 1,
            }
        return output

    @staticmethod
    def _boundary_coords(geojson_obj: dict) -> tuple[list[float], list[float]]:
        geom = shape(geojson_obj)
        lat, lon = [], []

        def append_coords(coords):
            for coord in coords:
                if len(coord) >= 2:
                    x, y = coord[0], coord[1]
                    lon.append(x)
                    lat.append(y)
            lon.append(None)
            lat.append(None)

        if geom.geom_type == "Polygon":
            append_coords(list(geom.exterior.coords))
        elif geom.geom_type == "MultiPolygon":
            for poly in geom.geoms:
                append_coords(list(poly.exterior.coords))
        return lat, lon

    def _group_lines(self, data: dict, allowed: set[str], color_map: dict[str, str]) -> dict[str, dict]:
        grouped: dict[str, dict] = {}
        for feature in data.get("features", []):
            fclass = feature.get("properties", {}).get("fclass", "unknown")
            if allowed and fclass not in allowed:
                continue
            grouped.setdefault(fclass, {"features": [], "color": color_map.get(fclass, "#F26419")})
            grouped[fclass]["features"].append(feature)

        output: dict[str, dict] = {}
        for fclass, payload in grouped.items():
            lat, lon = self._lines_to_latlon({"features": payload["features"]})
            output[fclass] = {
                "lat": lat,
                "lon": lon,
                "color": payload["color"],
            }
        return output

    @staticmethod
    def _to_rgba(color: str, alpha: float) -> str:
        color = color.strip()
        if color.startswith("rgba"):
            return color
        if color.startswith("rgb("):
            comps = [c.strip() for c in color[4:-1].split(",")]
            if len(comps) >= 3:
                return f"rgba({comps[0]},{comps[1]},{comps[2]},{alpha})"
            return color
        color = color.lstrip("#")
        if len(color) == 6:
            r, g, b = (
                int(color[0:2], 16),
                int(color[2:4], 16),
                int(color[4:6], 16),
            )
        elif len(color) == 3:
            r, g, b = (
                int(color[0] * 2, 16),
                int(color[1] * 2, 16),
                int(color[2] * 2, 16),
            )
        else:
            return color
        return f"rgba({r},{g},{b},{alpha})"
