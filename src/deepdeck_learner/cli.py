from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn
from dotenv import find_dotenv, load_dotenv


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Open the DeepDeckLearner local workbench.")
    result.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    result.add_argument("--port", default=8765, type=int)
    result.add_argument("--no-browser", action="store_true")
    return result


def main() -> None:
    arguments = parser().parse_args()
    if not 1024 <= arguments.port <= 65535:
        raise SystemExit("port must be between 1024 and 65535")
    url = f"http://{arguments.host}:{arguments.port}"
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    if not arguments.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("deepdeck_learner.app:app", host=arguments.host, port=arguments.port)


if __name__ == "__main__":
    main()
