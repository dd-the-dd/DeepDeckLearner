from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _model_decks(root: Path, run: Path, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    configured = metadata.get("decks", [])
    if isinstance(configured, list) and configured:
        return [deck for deck in configured if isinstance(deck, dict)]
    try:
        resolved = json.loads((run / "resolved-config.json").read_text(encoding="utf-8"))
        selected_ids = resolved.get("learnerSettings", {}).get("selectedDeckVersionIds", [])
    except (AttributeError, OSError, ValueError):
        selected_ids = []
    decks: list[dict[str, Any]] = []
    for version_id in selected_ids if isinstance(selected_ids, list) else []:
        try:
            deck = json.loads(
                (root / ".deepdeck" / "decks" / f"{version_id}.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            continue
        if not isinstance(deck, dict):
            continue
        decks.append(
            {
                "id": str(deck.get("id", version_id)),
                "name": str(deck.get("name", "Local training deck")),
                "creator": deck.get("creator"),
                "version": int(deck.get("version", 1)),
                "format": str(deck.get("format", metadata.get("format", "legacy"))),
                "colors": deck.get("colors", []),
                "playableCardCount": int(
                    deck.get("playableCardCount", deck.get("cardCount", 0))
                ),
            }
        )
    return decks


def local_models(root: Path, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return user-owned models created by local deck-pool training runs."""
    statuses = {
        str(Path(str(job["artifact_path"])).resolve()): str(job["status"])
        for job in jobs
        if job.get("artifact_path")
    }
    models: list[dict[str, Any]] = []
    runs = root / ".deepdeck" / "runs"
    if not runs.is_dir():
        return models
    for metadata_path in runs.glob("*/local-model.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict) or metadata.get("schemaVersion") != "local-model/v1":
            continue
        run_path = metadata_path.parent.resolve()
        checkpoint = Path(str(metadata.get("checkpointPath", "")))
        ready = bool(metadata.get("reservePlaytest")) and (
            checkpoint / "manifest.json"
        ).is_file() and (checkpoint / "checkpoint.pt").is_file()
        models.append(
            {
                **metadata,
                "runPath": str(run_path),
                "checkpointPath": str(checkpoint),
                "status": statuses.get(str(run_path), "stopped"),
                "ready": ready,
                "decks": _model_decks(root, metadata_path.parent, metadata),
            }
        )
    return sorted(models, key=lambda model: str(model.get("createdAt", "")), reverse=True)


def deck_statistics(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    runs = root / ".deepdeck" / "runs"
    if not runs.is_dir():
        return rows
    for metadata_path in runs.glob("*/local-model.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            leaderboard = json.loads(
                (metadata_path.parent / "training-leaderboard.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            leaderboard = {"deckParticipants": []}
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        if not isinstance(metadata, dict):
            continue
        participants = (
            leaderboard.get("deckParticipants", []) if isinstance(leaderboard, dict) else []
        )
        for deck in _model_decks(root, metadata_path.parent, metadata):
            if not isinstance(deck, dict):
                continue
            version_id = str(deck.get("id", ""))
            candidate = next(
                (
                    item
                    for item in participants
                    if isinstance(item, dict)
                    and item.get("participantId") == metadata.get("id")
                    and (
                        version_id[:8] in str(item.get("deckName", ""))
                        or str(item.get("deckName", "")).startswith(str(deck.get("name", "")))
                    )
                ),
                {},
            )
            game_wins = int(candidate.get("gameWins", 0))
            game_losses = int(candidate.get("gameLosses", 0))
            decided_games = game_wins + game_losses
            rows.append(
                {
                    "modelId": metadata.get("id"),
                    "modelName": metadata.get("name"),
                    "architecture": metadata.get("architecture"),
                    "deckVersionId": version_id,
                    "deckName": deck.get("name"),
                    "format": deck.get("format", metadata.get("format")),
                    "ratingSystem": "plackett-luce",
                    "mu": float(candidate.get("mu", 25.0)),
                    "sigma": float(candidate.get("sigma", 25.0 / 3.0)),
                    "ordinal": float(candidate.get("ordinal", 0.0)),
                    "rank": candidate.get("rank"),
                    "matches": int(candidate.get("games", 0)),
                    "gameWins": game_wins,
                    "gameLosses": game_losses,
                    "winRate": game_wins / decided_games if decided_games else None,
                }
            )
    return sorted(rows, key=lambda row: (str(row["modelName"]), -float(row["ordinal"])))
