from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NetworkSettings:
    mode: str = "local"
    port: int = 8765

    @property
    def host(self) -> str:
        return "0.0.0.0" if self.mode == "lan" else "127.0.0.1"


def settings_path(root: Path) -> Path:
    return root / ".deepdeck" / "learner.json"


def _validated_network(raw: object) -> NetworkSettings:
    if not isinstance(raw, dict):
        return NetworkSettings()
    mode = raw.get("mode", "local")
    port = raw.get("port", 8765)
    if mode not in {"local", "lan"}:
        mode = "local"
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        port = 8765
    return NetworkSettings(mode=mode, port=port)


def load_network_settings(root: Path) -> NetworkSettings:
    path = settings_path(root)
    if not path.is_file():
        return NetworkSettings()
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return NetworkSettings()
    if not isinstance(payload, dict):
        return NetworkSettings()
    return _validated_network(payload.get("network"))


def save_network_settings(root: Path, settings: NetworkSettings) -> None:
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text("utf-8"))
            if isinstance(existing, dict):
                payload = existing
        except (OSError, ValueError):
            pass
    payload["network"] = asdict(settings)
    handle, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix="learner-", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
