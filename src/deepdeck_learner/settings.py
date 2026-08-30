from __future__ import annotations

import json
import os
from pathlib import Path


def secrets_path(root: Path) -> Path:
    return root / ".deepdeck" / "secrets.json"


def load_api_key(root: Path) -> bool:
    if os.getenv("DEEPDECK_API_KEY", "").strip():
        return True
    path = secrets_path(root)
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("api_key", "")
    except (OSError, ValueError, AttributeError):
        return False
    if not isinstance(value, str) or not value.strip():
        return False
    os.environ["DEEPDECK_API_KEY"] = value.strip()
    return True


def save_api_key(root: Path, value: str) -> None:
    key = value.strip()
    if not key.startswith("ddl_agent_") or len(key) < 24:
        raise ValueError("Enter the complete Deep Deck League account API key.")
    path = secrets_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps({"api_key": key}), encoding="utf-8")
    os.chmod(pending, 0o600)
    pending.replace(path)
    os.environ["DEEPDECK_API_KEY"] = key
