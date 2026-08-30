#!/usr/bin/env python3
"""Cross-platform setup, dependency update, and launcher for DeepDeckLearner."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def run(arguments: list[str]) -> None:
    """Run a fixed argv command without involving a platform shell."""
    print("+", subprocess.list2cmdline(arguments), flush=True)
    subprocess.run(arguments, cwd=ROOT, check=True, shell=False)


def require(executable: str, install_hint: str) -> str:
    found = shutil.which(executable)
    if not found:
        raise SystemExit(f"{executable} is required. {install_hint}")
    return found


def venv_python() -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return VENV / relative


def setup(*, update_only: bool = False) -> None:
    git = require("git", "Install Git from https://git-scm.com/downloads.")
    npm = require("npm", "Install the current Node.js LTS release.")
    run([git, "submodule", "sync", "--recursive"])
    run([git, "submodule", "update", "--init", "--recursive"])

    python = venv_python()
    if not python.is_file():
        if update_only:
            raise SystemExit(
                "The local environment does not exist. Run "
                "'python scripts/workbench.py setup' first."
            )
        run([sys.executable, "-m", "venv", str(VENV)])

    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-e",
            str(ROOT / "external" / "deepdeck-agent"),
        ]
    )
    frontend = ROOT / "apps" / "learner-web"
    run([npm, "--prefix", str(frontend), "ci"])
    run([npm, "--prefix", str(frontend), "run", "build"])
    run([str(python), "-m", "pip", "install", "-e", f"{ROOT}[deep-learning,dev]"])
    action = "updated" if update_only else "ready"
    print(f"DeepDeckLearner is {action}.", flush=True)


def start(*, host: str, port: int, no_browser: bool) -> None:
    python = venv_python()
    if not python.is_file():
        raise SystemExit(
            "DeepDeckLearner is not set up. Run "
            "'python scripts/workbench.py setup' first."
        )
    arguments = [
        str(python),
        "-m",
        "deepdeck_learner.cli",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if no_browser:
        arguments.append("--no-browser")
    run(arguments)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Set up, update, or start the DeepDeckLearner workbench."
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Install pinned dependencies and build the app.")
    commands.add_parser("update", help="Apply the pinned Engine/Pixi/Agent revisions.")
    launch = commands.add_parser("start", help="Start the local workbench.")
    launch.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    launch.add_argument("--port", type=int, default=8765)
    launch.add_argument("--no-browser", action="store_true")
    return result


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "setup":
        setup()
    elif arguments.command == "update":
        setup(update_only=True)
    else:
        start(host=arguments.host, port=arguments.port, no_browser=arguments.no_browser)


if __name__ == "__main__":
    main()
