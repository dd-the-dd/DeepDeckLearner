from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import find_dotenv, load_dotenv

from .app import create_app
from .settings import load_network_settings
from .status import project_root


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Open the DeepDeckLearner local workbench.")
    result.add_argument("--host", default=None, choices=("127.0.0.1", "localhost", "0.0.0.0"))
    result.add_argument("--port", default=None, type=int)
    result.add_argument("--no-browser", action="store_true")
    return result


def main() -> None:
    arguments = parser().parse_args()
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    root = project_root()
    first_start = True
    while True:
        configured = load_network_settings(root)
        host = arguments.host or configured.host
        port = arguments.port or configured.port
        if not 1024 <= port <= 65535:
            raise SystemExit("port must be between 1024 and 65535")
        browser_host = "127.0.0.1" if host == "0.0.0.0" else host
        url = f"http://{browser_host}:{port}"
        application = create_app(Path(root))
        restart_requested = threading.Event()
        server = uvicorn.Server(uvicorn.Config(application, host=host, port=port, log_level="info"))

        def restart(
            event: threading.Event = restart_requested,
            current_server: uvicorn.Server = server,
        ) -> None:
            event.set()
            current_server.should_exit = True

        application.state.restart_callback = restart
        if host == "0.0.0.0":
            print(f"Trusted LAN mode enabled on port {port}.")
        if first_start and not arguments.no_browser:
            threading.Timer(1.0, lambda target=url: webbrowser.open(target)).start()
        first_start = False
        server.run()
        if not restart_requested.is_set():
            break


if __name__ == "__main__":
    main()
