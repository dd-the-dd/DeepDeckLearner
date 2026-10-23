"""Paths and process dispatch shared by source and standalone desktop builds."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def configure_local_http_loop() -> None:
    """Keep Windows client resets from closing the local HTTP listener.

    Only the controller uses this policy. SDK/Engine child processes retain
    their normal loop and subprocess support. The controller starts its workers
    with synchronous Popen, not asyncio subprocess pipes.
    """
    import asyncio

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


WORKER_MODULES = frozenset({"deepdeck_examples.run", "deepdeck_examples.local_batch"})


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_directory() -> Path | None:
    location = getattr(sys, "_MEIPASS", None)
    return Path(location).resolve() if frozen() and location else None


def bundle_manifest() -> dict[str, Any]:
    directory = bundle_directory()
    if directory is None:
        return {}
    try:
        value = json.loads((directory / "deepdeck-runtime.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        value
        if isinstance(value, dict) and value.get("schemaVersion") == "deepdeck-desktop/v1"
        else {}
    )


def bundled_engine() -> Path | None:
    directory = bundle_directory()
    if directory is None or not bundle_manifest():
        return None
    name = "mtg-engine-server.exe" if os.name == "nt" else "mtg-engine-server"
    candidate = directory / "engine" / name
    return candidate if candidate.is_file() else None


def component_revision(component: str) -> str | None:
    value = bundle_manifest().get("components", {}).get(component, {}).get("revision")
    return str(value) if value else None


def desktop_data_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (base / "DeepDeck" / "PlayerLab").resolve()


def local_engine_url() -> str:
    return os.environ.get("DEEPDECK_LOCAL_ENGINE_URL", "http://127.0.0.1:8787").rstrip("/")


def worker_argv(arguments: list[str]) -> list[str]:
    """A frozen executable is not python.exe; use the bundled console worker."""
    if not frozen() or len(arguments) < 3 or arguments[:2] != [sys.executable, "-m"]:
        return arguments
    if arguments[2] not in WORKER_MODULES:
        raise ValueError(
            "This advanced Python workflow is not included in the desktop player. Use the source checkout for neural training."
        )
    worker = Path(sys.executable).with_name(
        "DeepDeckWorker.exe" if os.name == "nt" else "DeepDeckWorker"
    )
    if not worker.is_file():
        raise ValueError(
            "DeepDeckWorker is missing. Extract the complete application folder again."
        )
    return [str(worker), *arguments[1:]]


def hidden_process_options() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
