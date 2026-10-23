from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .dependencies import (
    current_revision,
    engine_build_current,
    has_local_changes,
    pinned_revision,
    pixi_build_current,
    target_revision,
    tracked_branch,
)
from .runtime import bundle_directory, desktop_data_root, frozen


def project_root() -> Path:
    configured = os.getenv("DEEPDECK_LEARNER_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if frozen():
        return desktop_data_root()
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file() and (
        source_root / "src/deepdeck_learner/status.py"
    ).is_file():
        return source_root
    # A wheel lives in site-packages, often in a protected Python installation.
    # Keep player data independent from installing/upgrading the application.
    return desktop_data_root()


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def engine_healthy(url: str) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}/health", timeout=0.35) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except (OSError, URLError, TimeoutError):
        return False


def capability_status(root: Path, engine_url: str) -> dict[str, Any]:
    engine_path = root / "external" / "deepdeck-engine"
    pixi_path = root / "external" / "deepdeck-pixi"
    engine_current = current_revision(root, "engine")
    engine_pinned = pinned_revision(root, "engine")
    engine_target = target_revision(root, "engine")
    pixi_current = current_revision(root, "pixi")
    pixi_pinned = pinned_revision(root, "pixi")
    pixi_target = target_revision(root, "pixi")
    pixi_marker = root / ".deepdeck" / "dependencies" / "pixi-build-revision"
    pixi_built_revision = pixi_marker.read_text("utf-8").strip() if pixi_marker.is_file() else None
    bundle = bundle_directory()
    pixi_build_present = (pixi_path / "dist" / "index.js").is_file() or bool(
        bundle and (bundle / "deepdeck_learner" / "web" / "index.html").is_file()
    )
    pixi_built = pixi_build_current(root)
    return {
        "controller": {"ready": True, "version": "0.2.0"},
        "paths": {
            "project": str(root),
            "trajectory": str(root / ".deepdeck" / "trajectories" / "decisions.jsonl"),
            "checkpoints": str(root / ".deepdeck" / "checkpoints"),
        },
        "python": {"ready": True},
        "sdk": {"ready": module_available("deepdeck_agent")},
        "torch": {"ready": module_available("torch")},
        "engine": {
            "source_available": (engine_path / "Cargo.toml").is_file(),
            "revision": engine_current,
            "pinned_revision": engine_pinned,
            "tracked_branch": tracked_branch("engine"),
            "target_revision": engine_target,
            "synced": bool(engine_current and engine_current == engine_target),
            "dirty": has_local_changes(root, "engine"),
            "built": engine_build_current(root),
            "url": engine_url,
            "healthy": engine_healthy(engine_url),
        },
        "pixi": {
            "source_available": (pixi_path / "package.json").is_file(),
            "built": pixi_built,
            "build_present": pixi_build_present,
            "built_revision": pixi_built_revision,
            "revision": pixi_current,
            "pinned_revision": pixi_pinned,
            "tracked_branch": tracked_branch("pixi"),
            "target_revision": pixi_target,
            "synced": bool(pixi_current and pixi_current == pixi_target),
            "dirty": has_local_changes(root, "pixi"),
        },
        "hosted": {
            "api_key_configured": bool(os.getenv("DEEPDECK_API_KEY")),
            "trajectory_training": False,
            "reason": "The hosted trajectory-v1 capability is not published yet.",
        },
        "workflows": {
            "training_smoke": module_available("torch"),
            "training_dataset": module_available("torch"),
            "training_decks": module_available("torch"),
            "training_hosted": False,
            "playtest_local": module_available("deepdeck_agent"),
        },
    }
