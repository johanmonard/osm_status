from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

import requests
from shapely.geometry import shape


class GeofabrikClient:
    """Client that resolves AOI polygons to Geofabrik regional downloads."""

    def __init__(self, index_url: str, cache_path: Path, chunk_size: int = 1_048_576):
        self.index_url = index_url
        self.cache_path = cache_path
        self.chunk_size = chunk_size
        self._index_data: Optional[dict] = None

    def load_index(self, force: bool = False) -> dict:
        """Load and cache the Geofabrik index."""

        if self._index_data is not None and not force:
            return self._index_data

        if self.cache_path.exists() and not force:
            self._index_data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return self._index_data

        response = requests.get(self.index_url, timeout=60)
        response.raise_for_status()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(response.text, encoding="utf-8")
        self._index_data = response.json()
        return self._index_data

    def find_region_for_geometry(self, geom) -> dict:
        """Return the most specific Geofabrik feature intersecting the AOI."""

        index = self.load_index()
        matches: list[tuple[float, dict]] = []
        for feature in index.get("features", []):
            feature_geom = shape(feature["geometry"])
            if feature_geom.contains(geom):
                matches.append((feature_geom.area, feature))
            elif feature_geom.intersects(geom):
                matches.append((feature_geom.area * 10, feature))

        if not matches:
            raise ValueError("No Geofabrik region covers the provided area.")

        matches.sort(key=lambda item: item[0])
        return matches[0][1]

    def download_region_shapefile(
        self,
        region_feature: dict,
        destination: Path,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Path:
        """Stream the shapefile ZIP for the region to the raw storage folder."""

        dataset_url = region_feature["properties"]["urls"].get("shp")
        if not dataset_url:
            raise ValueError("This region does not provide a shapefile download.")

        response = requests.get(dataset_url, stream=True, timeout=60)
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0))

        destination.parent.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        with destination.open("wb") as f:
            for chunk in response.iter_content(chunk_size=self.chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total:
                    progress_callback(downloaded / total, "Downloading dataset...")
        if progress_callback:
            progress_callback(1.0, "Download complete")
        return destination

    @staticmethod
    def region_label(feature: dict) -> str:
        props = feature.get("properties", {})
        return props.get("name", "unknown-region")

