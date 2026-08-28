from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def project_root() -> Path:
    configured = os.getenv("DEEPDECK_LEARNER_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def git_revision(path: Path) -> str | None:
    if not path.exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=3,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def engine_healthy(url: str) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}/health", timeout=0.35) as response:  # noqa: S310
            return 200 <= int(response.status) < 300
    except (OSError, URLError, TimeoutError):
        return False


def capability_status(root: Path, engine_url: str) -> dict[str, Any]:
    engine_path = root / "external" / "deepdeck-engine"
    pixi_path = root / "external" / "deepdeck-pixi"
    pixi_built = (pixi_path / "dist" / "index.js").is_file()
    return {
        "controller": {"ready": True, "version": "0.2.0"},
        "python": {"ready": True},
        "sdk": {"ready": module_available("deepdeck_agent")},
        "torch": {"ready": module_available("torch")},
        "engine": {
            "source_available": (engine_path / "Cargo.toml").is_file(),
            "revision": git_revision(engine_path),
            "url": engine_url,
            "healthy": engine_healthy(engine_url),
        },
        "pixi": {
            "source_available": (pixi_path / "package.json").is_file(),
            "built": pixi_built,
            "revision": git_revision(pixi_path),
        },
        "hosted": {
            "api_key_configured": bool(os.getenv("DEEPDECK_API_KEY")),
            "trajectory_training": False,
            "reason": "The hosted trajectory-v1 capability is not published yet.",
        },
        "workflows": {
            "training_smoke": module_available("torch"),
            "training_dataset": module_available("torch"),
            "training_decks": False,
            "training_hosted": False,
            "playtest_local": module_available("deepdeck_agent"),
        },
    }
