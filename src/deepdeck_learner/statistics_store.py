"""Quota-controlled, compact local match results and optional numeric telemetry.

No raw game state, hands, API keys or replay frames are stored here. A match is
one atomic file, so retention removes both opponents' results together.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .replay_lock import replay_storage_lock
from .statistics_metrics import GROUPS, METRICS

DEFAULT_LIMIT = 128 * 1024**2
MIN_LIMIT = 1024**2
MAX_LIMIT = 10 * 1024**3


class StatisticsStorageError(OSError):
    pass


def _directory(root: Path) -> Path:
    return root / ".deepdeck" / "statistics" / "matches"


def _object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _settings(root: Path) -> dict[str, Any]:
    data = _object(root / ".deepdeck" / "statistics.json")
    limit = data.get("storageLimitBytes", DEFAULT_LIMIT)
    if type(limit) is not int or not MIN_LIMIT <= limit <= MAX_LIMIT:
        limit = DEFAULT_LIMIT
    valid = {g["id"] for g in GROUPS}
    rules = (
        [
            {
                "kind": r["kind"],
                "id": r["id"],
                "groups": [g for g in r["groups"] if isinstance(g, str) and g in valid],
            }
            for r in data.get("rules", [])
            if isinstance(r, dict)
            and r.get("kind") in ("agent", "deck", "player")
            and isinstance(r.get("id"), str)
            and isinstance(r.get("groups"), list)
        ]
        if isinstance(data.get("rules", []), list)
        else []
    )
    return {"storageLimitBytes": limit, "rules": rules}


def legacy_files(root: Path) -> list[Path]:
    return [
        p
        for directory in ("agents", "players")
        for p in (root / ".deepdeck" / directory).glob("*/local-results.jsonl")
    ] + legacy_engine_files(root)


def legacy_engine_files(root: Path) -> list[Path]:
    candidates = [
        root / "runs/deck-analytics.json",
        root / "external/deepdeck-engine/runs/deck-analytics.json",
    ]
    configured = os.environ.get("MTG_DECK_ANALYTICS_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        candidates.append(path if path.is_absolute() else root / path)
    return list(dict.fromkeys(p.resolve() for p in candidates if p.is_file()))


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def storage_status(root: Path) -> dict[str, Any]:
    with replay_storage_lock(root, namespace="statistics"):
        settings = _settings(root)
        files = list(_directory(root).glob("*.json"))
        legacy = sum(_size(p) for p in legacy_files(root))
        engine_bytes = sum(_size(p) for p in legacy_engine_files(root))
        health = _object(root / ".deepdeck" / "statistics-health.json")
        used = legacy + sum(_size(p) for p in _directory(root).glob("*") if p.is_file())
        return {
            **settings,
            "usedBytes": used,
            "legacyBytes": legacy,
            "legacyEngineBytes": engine_bytes,
            "matchFiles": len(files),
            "groups": GROUPS,
            "metrics": METRICS,
            "lastError": health.get("error"),
            "engineAnalyticsDisabled": health.get("engineAnalyticsDisabled"),
            "retention": "oldest-match-first",
            "quotaScope": "Local result files, advanced telemetry and existing Engine cache files; excludes replays and advanced training artifacts.",
        }


def _atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    try:
        with pending.open("wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _encoded(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _evict(root: Path, limit: int, reserve: int = 0, protected: Path | None = None) -> int:
    # Exclusive OS lock means no other writer owns these interrupted temporaries.
    for pending in _directory(root).glob("*.json.pending"):
        pending.unlink()
    legacy = sum(_size(p) for p in legacy_files(root))
    # Legacy files are user history: don't silently delete or rewrite them.
    if legacy + reserve > limit:
        raise StatisticsStorageError(
            "Statistics budget is too small for this match and existing legacy results or Engine cache. Increase it; legacy files were kept."
        )
    files = [p for p in _directory(root).glob("*.json") if p != protected]
    used = legacy + sum(_size(p) for p in files)
    removed = 0
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        if used + reserve <= limit:
            break
        size = _size(path)
        path.unlink()
        used -= size
        removed += 1
    return removed


def save_settings(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    limit = payload.get("storageLimitBytes", DEFAULT_LIMIT)
    if type(limit) is not int or not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise ValueError("Statistics storage must be between 1 MB and 10 GB.")
    rules = payload.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 1000:
        raise ValueError("Choose at most 1000 tracking rules.")
    valid_groups = {g["id"] for g in GROUPS}
    clean, keys = [], set()
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("kind") not in ("agent", "deck", "player"):
            raise ValueError("Tracking rules must target an agent, deck or player.")
        identity, groups = rule.get("id"), rule.get("groups")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 200:
            raise ValueError("Choose a named tracking target.")
        if not isinstance(groups, list) or any(
            not isinstance(g, str) or g not in valid_groups for g in groups
        ):
            raise ValueError("Choose supported metric groups.")
        key = (rule["kind"], identity)
        if key in keys:
            raise ValueError("Each target can have only one tracking rule.")
        keys.add(key)
        if groups:
            clean.append({"kind": rule["kind"], "id": identity, "groups": sorted(set(groups))})
    with replay_storage_lock(root, namespace="statistics"):
        removed = _evict(root, limit)
        _atomic(
            root / ".deepdeck" / "statistics.json",
            _encoded({"storageLimitBytes": limit, "rules": clean}),
        )
        return {**storage_status(root), "removedMatches": removed}


def tracking_targets(
    root: Path, seats: list[dict[str, Any]], decks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    with replay_storage_lock(root, namespace="statistics"):
        rules = _settings(root)["rules"]
    result = []
    for index, (seat, deck) in enumerate(zip(seats, decks, strict=True), 1):
        human = seat.get("kind") == "human"
        groups = {
            g
            for r in rules
            if (r["kind"] == "deck" and r["id"] == deck["id"])
            or (r["kind"] == ("player" if human else "agent") and r["id"] == seat["agentId"])
            for g in r["groups"]
        }
        if groups:
            result.append(
                {
                    "playerId": "local-human" if human else f"batch-seat-{index}",
                    "groups": sorted(groups),
                }
            )
    return result


def write_result(
    root: Path, seat: dict[str, Any], row: dict[str, Any], profile: dict[str, Any] | None = None
) -> None:
    write_match_results(root, [(seat, row, profile)])


def write_match_results(
    root: Path, entries: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]]
) -> None:
    if not entries:
        return
    identities = {
        str(row.get("replayId") or f"{row.get('batchId')}:{row.get('gameIndex')}")
        for _, row, _ in entries
    }
    if len(identities) != 1:
        raise ValueError("A statistics transaction must contain exactly one match.")
    match_id = identities.pop()
    key = hashlib.sha256(match_id.encode()).hexdigest()
    path = _directory(root) / f"{key}.json"
    with replay_storage_lock(root, namespace="statistics"):
        data = _object(path) or {
            "version": "deepdeck-statistics-match/v1",
            "id": match_id,
            "rows": {},
        }
        for seat, row, profile in entries:
            player = str(row.get("playerId") or seat["agentId"])
            data["rows"][player] = {
                **row,
                "actorId": seat["agentId"],
                "actorKind": "player" if seat.get("kind") == "human" else "agent",
                "profile": profile,
            }
        encoded = _encoded(data)
        if shutil.disk_usage(root).free < len(encoded) + 65536:
            raise StatisticsStorageError(
                "Not enough free disk space to save statistics. Match results and replay execution are independent."
            )
        _evict(root, int(_settings(root)["storageLimitBytes"]), len(encoded), path)
        original_time = path.stat().st_mtime if path.exists() else time.time()
        _atomic(path, encoded)
        os.utime(path, (original_time, original_time))
        health_path = root / ".deepdeck" / "statistics-health.json"
        _atomic(health_path, _encoded({**_object(health_path), "error": None}))


def note_storage_error(root: Path, error: Exception) -> None:
    try:
        with replay_storage_lock(root, namespace="statistics"):
            health_path = root / ".deepdeck" / "statistics-health.json"
            _atomic(health_path, _encoded({**_object(health_path), "error": str(error)[:500]}))
    except OSError:
        pass


def note_engine_analytics(root: Path, disabled: bool) -> None:
    try:
        with replay_storage_lock(root, namespace="statistics"):
            path = root / ".deepdeck/statistics-health.json"
            _atomic(path, _encoded({**_object(path), "engineAnalyticsDisabled": disabled}))
    except OSError:
        pass


def iter_rows(root: Path, *, include_profiles: bool = False) -> Iterator[dict[str, Any]]:
    with replay_storage_lock(root, namespace="statistics"):
        for path in _directory(root).glob("*.json"):
            rows = _object(path).get("rows")
            if isinstance(rows, dict):
                for row in rows.values():
                    if isinstance(row, dict):
                        yield (
                            row
                            if include_profiles
                            else {k: v for k, v in row.items() if k != "profile"}
                        )


def stored_rows(root: Path) -> list[dict[str, Any]]:
    return list(iter_rows(root))


def advanced_statistics(root: Path, kind: str, identity: str, days: int = 0) -> dict[str, Any]:
    if kind not in ("agent", "deck", "player") or days not in {0, 7, 30, 90}:
        raise ValueError("Choose an agent, deck or player and an available period.")
    from datetime import datetime, timezone

    cutoff = time.time() - days * 86400 if days else 0
    count, tracked, dropped = 0, 0, 0
    totals: dict[str, dict[str, float]] = {}
    snapshots: dict[str, dict[str, float]] = {}
    curve: dict[int, dict[str, dict[str, float]]] = {}

    def add(target: dict[str, dict[str, float]], metric: str, value: float) -> None:
        cell = target.setdefault(metric, {"sum": 0, "samples": 0})
        cell["sum"] += value
        cell["samples"] += 1

    def means(target: dict[str, dict[str, float]]) -> dict[str, Any]:
        return {k: {**v, "mean": v["sum"] / v["samples"]} for k, v in target.items()}

    for row in iter_rows(root, include_profiles=True):
        if (
            row.get("deckVersionId") == identity
            if kind == "deck"
            else row.get("actorKind") == kind and row.get("actorId") == identity
        ):
            try:
                created = datetime.fromisoformat(
                    str(row.get("createdAt", "")).replace("Z", "+00:00")
                )
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created.timestamp() < cutoff:
                    continue
            except ValueError:
                if days:
                    continue
            count += 1
            profile = row.get("profile")
            if not isinstance(profile, dict):
                continue
            tracked += 1
            dropped += profile.get("droppedTurnSamples", 0)
            for metric, value in profile.get("totals", {}).items():
                if isinstance(value, (int, float)):
                    add(totals, metric, float(value))
            for sample in profile.get("turns", []):
                for metric, value in sample.get("values", {}).items():
                    add(snapshots, metric, float(value))
                    add(curve.setdefault(sample["turn"], {}), metric, float(value))
    return {
        "kind": kind,
        "id": identity,
        "seatResults": count,
        "trackedSeatResults": tracked,
        "totals": means(totals),
        "snapshots": means(snapshots),
        "curve": [{"turn": t, "values": means(values)} for t, values in sorted(curve.items())],
        "droppedTurnSamples": dropped,
        "note": "Observed samples only. Missing groups and fields are not zeroes. Deck views count each participating seat, including mirrors.",
    }
