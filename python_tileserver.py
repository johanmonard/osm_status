from pathlib import Path

from app_modules import APP_CONFIG, PythonTileServer


def main():
    tileserver_cfg = APP_CONFIG["tileserver"]
    mbtiles_path = Path(tileserver_cfg["mbtiles"])
    port = tileserver_cfg["port"]
    server = PythonTileServer(mbtiles_path, port)
    try:
        server.start(block=True)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
