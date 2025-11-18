from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .py_tileserver import PythonTileServer

class TileServerManager:
    """Small utility around the external TileServer GL binary."""

    def __init__(self, config: dict):
        self.port: int = config["port"]
        self.config_path: Path = Path(config["config_path"])
        self.mbtiles_path: Path = Path(config["mbtiles"])
        self.style_url: str = config["style_url"]
        self._process: Optional[subprocess.Popen] = None
        self._python_server: Optional[PythonTileServer] = None

    def write_config(self) -> Path:
        """Persist a config file referencing the MBTiles dataset."""

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "options": {"paths": {"root": str(self.mbtiles_path.parent)}},
            "styles": {
                "osm-bright": {
                    "style": self.style_url,
                    "tilejson": {"tiles": [f"http://127.0.0.1:{self.port}/data/vectiles/{{z}}/{{x}}/{{y}}.pbf"]},
                    "sources": {"vectiles": {"type": "vector", "url": "mbtiles://{vectiles}"}},
                }
            },
            "data": {
                "vectiles": {
                    "mbtiles": str(self.mbtiles_path),
                }
            },
        }
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return self.config_path

    def start(self) -> bool:
        """Attempt to start TileServer GL or fall back to the Python server."""

        binary = shutil.which("tileserver-gl")
        if binary:
            self.write_config()
            if self._process and self._process.poll() is None:
                return True
            self._process = subprocess.Popen(
                [binary, "--config", str(self.config_path), "--port", str(self.port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        return self._start_python_server()

    def _start_python_server(self) -> bool:
        if self._python_server:
            return True
        if not self.mbtiles_path.exists():
            print(f"[TileServerManager] MBTiles not found: {self.mbtiles_path}", flush=True)
            return False
        self._python_server = PythonTileServer(self.mbtiles_path, self.port)
        return self._python_server.start()

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=10)
        if self._python_server:
            self._python_server.stop()
            self._python_server = None
