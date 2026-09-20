from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser

from .config import ConfigStore
from .logging_setup import setup_logging
from .runtime import XiaoZhiRuntime
from .webui import WebUIServer


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--background", action="store_true")
    args, _ = parser.parse_known_args()

    cfg = ConfigStore().load()
    port = int(cfg.web_port or 8765)
    url = f"http://127.0.0.1:{port}/"
    if _port_open(port):
        if not args.background:
            webbrowser.open(url)
        return 0

    runtime = XiaoZhiRuntime()
    server = WebUIServer(runtime, port=port)
    if not args.background:
        def open_later():
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=open_later, daemon=True).start()
    try:
        server.start()
    except KeyboardInterrupt:
        runtime.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
