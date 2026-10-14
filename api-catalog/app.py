"""Run with Python 3.9+. No pip, Node, Hermes or Ollama required."""

import argparse
import sys
import threading
import webbrowser
from pathlib import Path


def main():
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9 veya üzeri gerekli.")
    from catalog_app.server import make_server
    parser = argparse.ArgumentParser(description="Yerel API kataloğu")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    server, _app = make_server(args.data_dir, args.port)
    url = "http://127.0.0.1:" + str(server.server_port)
    print("API kataloğu: " + url, flush=True)
    print("Kapatmak için Ctrl+C. Hedef uygulamalara API isteği gönderilmez.", flush=True)
    if args.open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
