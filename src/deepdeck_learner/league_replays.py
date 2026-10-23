from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import psutil

from .experience import experience_settings, write_replay
from .league_connection import normalize_platform_url, platform_url
from .replay_lock import replay_storage_lock
from .replay_summary import replay_match_summary
from .resources import find_model_run

LEAGUE_REPLAY_SCHEMA = "deepdeck-league-replay/v1"
FINISHED = frozenset({"complete", "completed", "failed", "cancelled"})


def _live_claim_owner(pid: Any, started: Any) -> bool:
    if type(pid) is not int or not isinstance(started, (int, float)):
        return False
    try:
        return abs(psutil.Process(pid).create_time() - started) < 0.001
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return True  # Unknown ownership must not grant a duplicate download.


def _origin(snapshot: dict[str, Any], *, fallback: str | None = None) -> str | None:
    explicit = snapshot.get("platformUrl")
    if explicit is None:
        watch = snapshot.get("watchUrl")
        # Historical workers generated this exact path from their API target.
        if isinstance(watch, str) and "/matches?" in watch:
            explicit = watch.split("/matches?", 1)[0] + "/api/v1"
    if explicit is None:
        return fallback
    try:
        return normalize_platform_url(explicit) if isinstance(explicit, str) else None
    except ValueError:
        return None


def replay_inbox_status(root: Path) -> dict[str, Any]:
    """Read durable pending work even after its launch diagnostics are pruned.

    No HTTP requests, inbox initialization or downloaded-file counts are
    synthesized by this status read. Collected means historically received.
    """
    database = root / ".deepdeck" / "league-replays" / "collector.db"
    result: dict[str, Any] = {"items": [], "observedAtUnixMs": int(time.time() * 1000)}
    if not database.is_file():
        return result
    current = platform_url()
    grouped: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(replay_inbox)")}
        if not columns:
            return result
        source = "source_url" if "source_url" in columns else "NULL"
        attempt = "last_attempt_at" if "last_attempt_at" in columns else "NULL"
        rows = db.execute(
            f"SELECT model_id,status,last_error,{source},{attempt},CASE WHEN {source} IS NULL AND status='pending' THEN snapshot ELSE NULL END FROM replay_inbox ORDER BY rowid"
        )
        for model, status, error, origin, last_attempt, snapshot in rows:
            item = grouped.setdefault(
                model,
                {
                    "modelId": model,
                    "collected": 0,
                    "pending": 0,
                    "currentServerPending": 0,
                    "otherServerPending": 0,
                    "unknownServerPending": 0,
                    "lastError": None,
                    "lastAttemptAtUnixMs": None,
                },
            )
            if status == "collected":
                item["collected"] += 1
            elif status == "pending":
                item["pending"] += 1
                origin = origin or (_origin(_object(json.loads(snapshot))) if snapshot else None)
                field = (
                    "unknownServerPending"
                    if not origin
                    else "currentServerPending"
                    if origin == current
                    else "otherServerPending"
                )
                item[field] += 1
                if error:
                    item["lastError"] = str(error)[:2000]
            if type(last_attempt) is int:
                item["lastAttemptAtUnixMs"] = max(item["lastAttemptAtUnixMs"] or 0, last_attempt)
    for model, item in grouped.items():
        try:
            _, metadata = find_model_run(root, model)
            item.update(name=str(metadata.get("name") or model), available=True)
        except ValueError:
            item.update(name=model, available=False)
    result["items"] = sorted(grouped.values(), key=lambda item: (-item["pending"], item["modelId"]))
    return result


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _timestamp(value: Any) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return None


def merge_patch(target: Any, patch: Any) -> Any:
    """Apply the Engine's RFC 7396 observation patch without mutating earlier frames."""
    if not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def _frame(observation: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    # Agent observations and terminal snapshots can be either a bare GameState
    # or a session-like envelope. The Pixi table consumes the latter.
    state = observation.get("state", observation)
    return {
        "revision": item.get("sequenceNumber", 0),
        "state": deepcopy(state),
        "decision": deepcopy(observation.get("decision")),
        "selectedAction": item.get("selectedAction"),
        "perspectiveSeat": item.get("seat", 0),
    }


def replay_for_player(value: dict[str, Any]) -> dict[str, Any]:
    """Expand a cached League trace at read time; keep delta storage small on disk."""
    if value.get("schemaVersion") != LEAGUE_REPLAY_SCHEMA:
        return value
    trace = _object(value.get("trace"))
    frames: list[dict[str, Any]] = []
    items = sorted(
        (item for item in trace.get("frames", []) if isinstance(item, dict)),
        key=lambda item: int(item.get("sequenceNumber", 0)),
    )
    checkpoints = {
        int(item["sequenceNumber"]): item["observation"]
        for item in trace.get("checkpoints", [])
        if isinstance(item, dict) and isinstance(item.get("observation"), dict)
    }
    observation = deepcopy(_object(trace.get("initialObservation")))
    for item in items:
        sequence = int(item.get("sequenceNumber", 0))
        if sequence in checkpoints:
            observation = deepcopy(checkpoints[sequence])
        else:
            delta = _object(item.get("observationDelta"))
            patch = delta.get("patch", item.get("patch", delta))
            observation = merge_patch(observation, patch)
        frames.append(_frame(observation, item))
    # Older platform games publish complete observable states instead of deltas.
    if not frames:
        for item in trace.get("decisions", []):
            if isinstance(item, dict) and isinstance(item.get("observableState"), dict):
                frames.append(_frame(item["observableState"], item))
    if isinstance(trace.get("terminalObservation"), dict):
        frames.append(
            _frame(
                trace["terminalObservation"],
                {"sequenceNumber": (items[-1].get("sequenceNumber", 0) if items else 0) + 1},
            )
        )
    return {
        "schemaVersion": "deepdeck-replay/v1",
        "id": value["id"],
        "createdAtUnixMs": value.get("createdAtUnixMs"),
        "metadata": value.get("metadata", {}),
        "cardCatalog": trace.get("cardCatalog", {}),
        "frames": frames,
    }


class LeagueReplayCollector:
    """Durable per-agent inbox, owned by the running League worker process.

    SQLite keeps multiple deployments safe and remembers collected game IDs even
    when the disk quota later retires their files. No GPU or cloud job is needed.
    """

    def __init__(self, root: Path, model_id: str) -> None:
        self.root = root.resolve()
        self.model_id = model_id
        self._collection_lock = threading.Lock()
        self.run, _ = find_model_run(self.root, model_id)
        self.database = self.root / ".deepdeck" / "league-replays" / "collector.db"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS replay_inbox ("
                "model_id TEXT NOT NULL, game_id TEXT NOT NULL, snapshot TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'pending', last_error TEXT, "
                "collected_at INTEGER, PRIMARY KEY (model_id, game_id))"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(replay_inbox)")}
            for name, kind in [
                ("source_url", "TEXT"),
                ("last_attempt_at", "INTEGER"),
                ("claim_token", "TEXT"),
                ("claim_expires_at", "INTEGER"),
                ("claim_owner_pid", "INTEGER"),
                ("claim_owner_started", "REAL"),
            ]:
                if name not in columns:
                    connection.execute(f"ALTER TABLE replay_inbox ADD COLUMN {name} {kind}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS replay_inbox_pending ON replay_inbox(model_id, status, last_attempt_at)"
            )
            for rowid, raw in connection.execute(
                "SELECT rowid,snapshot FROM replay_inbox WHERE source_url IS NULL AND snapshot!='{}'"
            ):
                origin = _origin(_object(json.loads(raw)))
                if origin:
                    connection.execute(
                        "UPDATE replay_inbox SET source_url=? WHERE rowid=?", (origin, rowid)
                    )
            # Preserve origin evidence before compacting old collected snapshots.
            # IDs remain as deduplication tombstones after replay-file eviction.
            connection.execute(
                "UPDATE replay_inbox SET snapshot='{}' WHERE status='collected' AND snapshot!='{}'"
            )

    def record_match(self, snapshot: dict[str, Any]) -> bool:
        added = False
        origin = _origin(snapshot, fallback=platform_url())
        if origin is None:
            raise ValueError("The replay source server could not be identified.")
        games = snapshot.get("games", [])
        if not games and snapshot.get("gameId") and snapshot.get("status") in FINISHED:
            games = [{"id": snapshot["gameId"], "status": snapshot["status"]}]
        with sqlite3.connect(self.database) as connection:
            for game in games:
                if not isinstance(game, dict) or game.get("status") not in FINISHED:
                    continue
                game_id = str(game.get("id", ""))
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,112}", game_id):
                    continue
                payload = {**snapshot, "game": game}
                previous = connection.execute(
                    "SELECT source_url FROM replay_inbox WHERE model_id=? AND game_id=?",
                    (self.model_id, game_id),
                ).fetchone()
                if previous and previous[0] not in (None, origin):
                    raise ValueError(
                        "This replay identifier is already associated with a different League server."
                    )
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO replay_inbox (model_id, game_id, snapshot, source_url) "
                    "VALUES (?, ?, ?, ?)",
                    (self.model_id, game_id, json.dumps(payload), origin),
                )
                added = added or cursor.rowcount > 0
        return added

    def collect(
        self,
        *,
        client: httpx.Client | None = None,
        limit: int = 50,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Choose between 1 and 50 replay attempts.")
        with self._collection_lock:
            return self._collect(client=client, limit=limit, stop_requested=stop_requested)

    def _collect(
        self,
        *,
        client: httpx.Client | None = None,
        limit: int = 50,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> dict[str, Any]:
        settings = experience_settings(self.root)
        if not settings["autoCollectLeagueReplays"]:
            return self.status(enabled=False)
        if client is None:
            with httpx.Client(timeout=30.0) as owned_client:
                return self._collect(
                    client=owned_client, limit=limit, stop_requested=stop_requested
                )
        base_url = platform_url()
        checked = int(time.time() * 1000)
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                "SELECT game_id, snapshot,claim_token,claim_owner_pid,claim_owner_started FROM replay_inbox "
                "WHERE model_id = ? AND status = 'pending' AND source_url=? "
                "AND (claim_expires_at IS NULL OR claim_expires_at<=?) "
                "ORDER BY COALESCE(last_attempt_at,0),rowid LIMIT ?",
                (self.model_id, base_url, checked, limit),
            ).fetchall()
        key = os.getenv("DEEPDECK_API_KEY", "").strip()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        for game_id, raw, previous_token, owner_pid, owner_started in rows:
            if stop_requested():
                break
            if previous_token and _live_claim_owner(owner_pid, owner_started):
                continue  # A slow live download is not an expired crashed worker.
            token = uuid.uuid4().hex
            attempted = int(time.time() * 1000)
            with sqlite3.connect(self.database) as connection:
                claimed = connection.execute(
                    "UPDATE replay_inbox SET claim_token=?,claim_expires_at=?,last_attempt_at=?,claim_owner_pid=?,claim_owner_started=? "
                    "WHERE model_id=? AND game_id=? AND status='pending' AND (claim_expires_at IS NULL OR claim_expires_at<=?)",
                    (
                        token,
                        attempted + 300_000,
                        attempted,
                        os.getpid(),
                        psutil.Process().create_time(),
                        self.model_id,
                        game_id,
                        attempted,
                    ),
                ).rowcount
            if not claimed:
                continue
            replay_id = f"league-{game_id}"
            try:
                existing = any(
                    (self.run / directory / f"{replay_id}.json").is_file()
                    for directory in ("replays", "saved-replays")
                )
                if not existing:
                    response = client.get(
                        f"{base_url}/games/{quote(game_id, safe='')}/replay",
                        params={"seat": 0},
                        headers=headers,
                    )
                    response.raise_for_status()
                    body = response.json()
                    trace = _object(_object(body).get("data", body))
                    self._save(replay_id, trace, json.loads(raw))
                with sqlite3.connect(self.database) as connection:
                    connection.execute(
                        "UPDATE replay_inbox SET status = 'collected', last_error = NULL, snapshot='{}', "
                        "collected_at = ? WHERE model_id = ? AND game_id = ? AND claim_token=?",
                        (int(time.time() * 1000), self.model_id, game_id, token),
                    )
            except (httpx.HTTPError, ValueError, OSError) as error:
                with sqlite3.connect(self.database) as connection:
                    connection.execute(
                        "UPDATE replay_inbox SET last_error = ? WHERE model_id = ? AND game_id = ? AND claim_token=?",
                        (str(error), self.model_id, game_id, token),
                    )
                if isinstance(error, httpx.RequestError) or (
                    isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code in {401, 403, 429, 503}
                ):
                    break
            finally:
                with sqlite3.connect(self.database) as connection:
                    connection.execute(
                        "UPDATE replay_inbox SET claim_token=NULL,claim_expires_at=NULL,claim_owner_pid=NULL,claim_owner_started=NULL WHERE model_id=? AND game_id=? AND claim_token=?",
                        (self.model_id, game_id, token),
                    )
        return self.status(enabled=True)

    def _save(self, replay_id: str, trace: dict[str, Any], snapshot: dict[str, Any]) -> None:
        game = {**_object(snapshot.get("game")), **_object(trace.get("game"))}
        if trace.get("spectator") or game.get("status") not in FINISHED:
            raise ValueError("The server has not finalized this replay yet.")
        has_deltas = bool(trace.get("initialObservation") and trace.get("frames"))
        if not ((has_deltas and trace.get("terminalObservation")) or trace.get("decisions")):
            raise ValueError("The replay history has not arrived on the server yet.")
        decks = snapshot.get("deckDetails") or snapshot.get("decks", [])
        started = _timestamp(game.get("startedAt"))
        completed = _timestamp(game.get("completedAt"))
        terminal = _object(trace.get("terminalObservation"))
        outcome = _object(terminal.get("state", terminal)).get("outcome", {})
        value: dict[str, Any] = {
            "schemaVersion": LEAGUE_REPLAY_SCHEMA,
            "id": replay_id,
            "createdAtUnixMs": completed or int(time.time() * 1000),
            "metadata": {
                "source": "league",
                "matchupId": snapshot.get("matchId"),
                "gameId": game.get("id"),
                "gameStatus": game.get("status"),
                "roundNumber": game.get("number"),
                "gameMode": snapshot.get("format"),
                "decks": [
                    str(deck.get("versionId") or deck.get("id"))
                    for deck in decks
                    if isinstance(deck, dict) and (deck.get("versionId") or deck.get("id"))
                ],
                "deckNames": [
                    deck["name"] if isinstance(deck, dict) else deck
                    for deck in decks
                    if (isinstance(deck, dict) and deck.get("name")) or isinstance(deck, str)
                ],
                "gameDurationSeconds": (completed - started) / 1000
                if started and completed
                else None,
                "outcome": outcome,
            },
            "trace": trace,
        }
        directory = self.run / "replays"
        value["metadata"]["matchSummary"] = replay_match_summary(value)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{replay_id}.json"
        with replay_storage_lock(self.root):
            # A favorite may have moved while the response was downloading.
            if (
                not destination.is_file()
                and not (self.run / "saved-replays" / f"{replay_id}.json").is_file()
            ):
                write_replay(self.root, destination, value)

    def status(self, *, enabled: bool = True) -> dict[str, Any]:
        with sqlite3.connect(self.database) as connection:
            counts = dict(
                connection.execute(
                    "SELECT status, count(*) FROM replay_inbox WHERE model_id = ? GROUP BY status",
                    (self.model_id,),
                ).fetchall()
            )
            error = connection.execute(
                "SELECT last_error FROM replay_inbox WHERE model_id = ? "
                "AND status = 'pending' AND last_error IS NOT NULL ORDER BY rowid DESC LIMIT 1",
                (self.model_id,),
            ).fetchone()
        return {
            "enabled": enabled,
            "collected": counts.get("collected", 0),
            "pending": counts.get("pending", 0),
            "lastError": error[0] if error else None,
            "checkedAtUnixMs": int(time.time() * 1000),
        }
