from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Tuple

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn


class PythonTileServer:
    """Minimal vector tile server that reads MBTiles and exposes HTTP endpoints."""

    def __init__(self, mbtiles_path: Path, port: int, host: str = "127.0.0.1"):
        self.mbtiles_path = Path(mbtiles_path)
        self.port = port
        self.host = host
        self._conn: sqlite3.Connection | None = None
        self._app: FastAPI | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self, block: bool = False) -> bool:
        if not self.mbtiles_path.exists():
            print(f"[PythonTileServer] MBTiles not found: {self.mbtiles_path}", flush=True)
            return False
        self._ensure_app()
        self._ensure_event_loop_policy()
        config = uvicorn.Config(self._app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)

        if block:
            print(f"[PythonTileServer] Serving {self.mbtiles_path} on http://{self.host}:{self.port}", flush=True)
            self._server.run()
            return True

        if self._thread and self._thread.is_alive():
            return True

        def _runner():
            print(
                f"[PythonTileServer] Background server running on http://{self.host}:{self.port}",
                flush=True,
            )
            self._server.run()

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._server:
            self._server.should_exit = True
            if hasattr(self._server, "force_exit"):
                self._server.force_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                self._thread.join(timeout=5)
        self._thread = None
        self._server = None
        if self._conn:
            self._conn.close()
            self._conn = None

    # Internal helpers -------------------------------------------------

    def _ensure_app(self):
        if self._app:
            return
        self._ensure_connection()
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/metadata.json")
        def metadata():
            return self._metadata()

        @app.get("/data/vectiles/{z}/{x}/{y}.pbf")
        def tile(z: int, x: int, y: int):
            payload = self._fetch_tile(z, x, y)
            if payload is None:
                raise HTTPException(status_code=404, detail="Tile not found")
            return Response(payload, media_type="application/x-protobuf")

        @app.get("/styles/osm-bright/style.json")
        def style():
            return self._style_payload()

        self._app = app

    def _ensure_event_loop_policy(self):
        if os.name == "nt":
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def _ensure_connection(self):
        if self._conn:
            return
        self._conn = sqlite3.connect(self.mbtiles_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def _metadata(self) -> Dict[str, str]:
        cur = self._conn.execute("SELECT name, value FROM metadata")
        return {row["name"]: row["value"] for row in cur.fetchall()}

    def _fetch_tile(self, z: int, x: int, y: int) -> bytes | None:
        tms_y = (2 ** z - 1) - y
        cur = self._conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y),
        )
        row = cur.fetchone()
        if not row:
            return None
        return bytes(row["tile_data"])

    def _style_payload(self) -> dict:
        metadata = self._metadata()
        vector_layers = self._vector_layers(metadata)
        tiles_url = f"http://{self.host}:{self.port}/data/vectiles/{{z}}/{{x}}/{{y}}.pbf"
        minzoom = int(metadata.get("minzoom", 5))
        maxzoom = int(metadata.get("maxzoom", 12))
        return {
            "version": 8,
            "sources": {
                "vectiles": {
                    "type": "vector",
                    "tiles": [tiles_url],
                    "minzoom": minzoom,
                    "maxzoom": maxzoom,
                }
            },
            "layers": [self._style_entry(layer_id, idx) for idx, layer_id in enumerate(vector_layers)],
        }

    def _vector_layers(self, metadata: Dict[str, str]) -> List[str]:
        meta_json = metadata.get("json")
        if not meta_json:
            return []
        try:
            payload = json.loads(meta_json)
        except json.JSONDecodeError:
            return []
        layers = payload.get("vector_layers") or []
        return [layer.get("id") for layer in layers if layer.get("id")]

    def _style_entry(self, layer_id: str, idx: int) -> dict:
        palette = [
            "#FF6B6B",
            "#FFD93D",
            "#6BCB77",
            "#4D96FF",
            "#C77DFF",
            "#FF8FB1",
            "#F3A712",
            "#14B8A6",
        ]
        color = palette[idx % len(palette)]
        layer_id_lower = layer_id.lower()
        if "line" in layer_id_lower or "road" in layer_id_lower:
            return {
                "id": layer_id,
                "type": "line",
                "source": "vectiles",
                "source-layer": layer_id,
                "paint": {"line-color": color, "line-width": 2},
            }
        return {
            "id": layer_id,
            "type": "fill",
            "source": "vectiles",
            "source-layer": layer_id,
            "paint": {"fill-color": color, "fill-opacity": 0.5},
        }
