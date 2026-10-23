from __future__ import annotations

import json
import os
from pathlib import Path


def secrets_path(root: Path) -> Path:
    return root / ".deepdeck" / "secrets.json"


def load_api_key(root: Path) -> bool:
    path = secrets_path(root)
    if not path.is_file():
        return bool(os.getenv("DEEPDECK_API_KEY", "").strip())
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("api_key", "")
    except (OSError, ValueError, AttributeError):
        return bool(os.getenv("DEEPDECK_API_KEY", "").strip())
    if not isinstance(value, str) or not value.strip():
        return bool(os.getenv("DEEPDECK_API_KEY", "").strip())
    os.environ["DEEPDECK_API_KEY"] = value.strip()
    return True


def save_api_key(root: Path, value: str) -> None:
    key = value.strip()
    if not key.startswith("ddl_agent_") or len(key) < 24:
        raise ValueError("Enter the complete Deep Deck League account API key.")
    path = secrets_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing["api_key"] = key
    pending.write_text(json.dumps(existing), encoding="utf-8")
    os.chmod(pending, 0o600)
    pending.replace(path)
    os.environ["DEEPDECK_API_KEY"] = key
