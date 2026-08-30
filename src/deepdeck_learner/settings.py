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


@dataclass(frozen=True)
class TrainingDeck:
    id: str
    name: str
    version: int
    format: str


@dataclass(frozen=True)
class TrainingSettings:
    model: str = "v12"
    format: str = "legacy"
    decks: tuple[TrainingDeck, ...] = ()


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


def _validated_training(raw: object) -> TrainingSettings:
    if not isinstance(raw, dict):
        return TrainingSettings()
    model = raw.get("model", "v12")
    game_format = raw.get("format", "legacy")
    if model not in {"v11", "v12"}:
        model = "v12"
    if game_format not in {"legacy", "commander"}:
        game_format = "legacy"
    decks: list[TrainingDeck] = []
    raw_decks = raw.get("decks", [])
    if isinstance(raw_decks, list):
        seen: set[str] = set()
        for item in raw_decks[:100]:
            if not isinstance(item, dict):
                continue
            deck_id = str(item.get("id", "")).strip()
            name = " ".join(str(item.get("name", "")).split())[:160]
            version = item.get("version", 1)
            deck_format = str(item.get("format", game_format)).lower()
            if (
                not deck_id
                or not name
                or deck_id in seen
                or not isinstance(version, int)
                or isinstance(version, bool)
                or version < 1
                or deck_format != game_format
            ):
                continue
            seen.add(deck_id)
            decks.append(TrainingDeck(deck_id, name, version, deck_format))
    return TrainingSettings(model=model, format=game_format, decks=tuple(decks))


def load_training_settings(root: Path) -> TrainingSettings:
    path = settings_path(root)
    if not path.is_file():
        return TrainingSettings()
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return TrainingSettings()
    if not isinstance(payload, dict):
        return TrainingSettings()
    return _validated_training(payload.get("training"))


def _save_section(root: Path, section: str, value: Any) -> None:
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
    payload[section] = asdict(value)
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


def save_network_settings(root: Path, settings: NetworkSettings) -> None:
    _save_section(root, "network", settings)


def save_training_settings(root: Path, settings: TrainingSettings) -> None:
    _save_section(root, "training", settings)


def training_settings_from_payload(raw: object) -> TrainingSettings:
    settings = _validated_training(raw)
    if not isinstance(raw, dict):
        raise ValueError("Invalid training settings.")
    raw_decks = raw.get("decks", [])
    if not isinstance(raw_decks, list) or len(settings.decks) != len(raw_decks):
        raise ValueError("Every training deck must be legal in the selected format.")
    return settings
