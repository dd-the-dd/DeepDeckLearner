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
TRACKED_SUBMODULES = ("external/deepdeck-engine", "external/deepdeck-pixi")


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


def setup(*, update_only: bool = False, deep_learning: bool = False, dev: bool = False) -> None:
    git = require("git", "Install Git from https://git-scm.com/downloads.")
    npm = require("npm", "Install the current Node.js LTS release.")
    run([git, "submodule", "sync", "--recursive"])
    for relative in TRACKED_SUBMODULES:
        source = ROOT / relative
        if source.is_dir() and (source / ".git").exists():
            result = subprocess.run(
                [git, "status", "--porcelain"],
                cwd=source,
                capture_output=True,
                text=True,
                check=True,
                shell=False,
            )
            if result.stdout.strip():
                raise SystemExit(
                    f"{relative} contains local changes. Commit or move them before updating."
                )
    run(
        [
            git,
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--",
            "external/deepdeck-agent",
        ]
    )
    run(
        [
            git,
            "submodule",
            "update",
            "--init",
            "--remote",
            "--checkout",
            "--",
            *TRACKED_SUBMODULES,
        ]
    )

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
    extras = [name for name, enabled in (("deep-learning", deep_learning), ("dev", dev)) if enabled]
    package = f"{ROOT}[{','.join(extras)}]" if extras else str(ROOT)
    run([str(python), "-m", "pip", "install", "-e", package])
    action = "updated" if update_only else "ready"
    print(f"DeepDeckLearner is {action}.", flush=True)


def start(*, host: str, port: int, no_browser: bool) -> None:
    python = venv_python()
    if not python.is_file():
        raise SystemExit(
            "DeepDeckLearner is not set up. Run 'python scripts/workbench.py setup' first."
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
    install = commands.add_parser(
        "setup", help="Install Engine/Pixi main, the pinned Agent, and build the app."
    )
    update = commands.add_parser(
        "update", help="Update Engine/Pixi from main and apply the pinned Agent."
    )
    for command in (install, update):
        command.add_argument(
            "--deep-learning",
            action="store_true",
            help="Also install optional neural training dependencies, including PyTorch.",
        )
        command.add_argument(
            "--dev", action="store_true", help="Also install Python test/lint tools."
        )
    launch = commands.add_parser("start", help="Start the local workbench.")
    launch.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    launch.add_argument("--port", type=int, default=8765)
    launch.add_argument("--no-browser", action="store_true")
    return result


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "setup":
        setup(deep_learning=arguments.deep_learning, dev=arguments.dev)
    elif arguments.command == "update":
        setup(update_only=True, deep_learning=arguments.deep_learning, dev=arguments.dev)
    else:
        start(host=arguments.host, port=arguments.port, no_browser=arguments.no_browser)


if __name__ == "__main__":
    main()
