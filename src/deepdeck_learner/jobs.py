from __future__ import annotations

import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psutil

from oracle_ai.training.matchmaking import plackett_luce_matchmaking_weight

from .dependencies import current_revision, engine_binary, engine_build_current, pinned_revision
from .resources import find_model_run, load_resource_plan, resource_snapshot

MAX_LOG_LINES = 500
TRAINING_KINDS = {"training.smoke", "training.dataset", "training.pool"}
PLAYTEST_KIND = "playtest.agent"
MATCHMAKING_KIND = "matchmaking.agent"
DEPENDENCY_KINDS = {
    "dependency.engine.start",
    "dependency.pixi.prepare",
    "dependency.sync",
    "dependency.stack.prepare",
}
SUPPORTED_KINDS = TRAINING_KINDS | {PLAYTEST_KIND, MATCHMAKING_KIND} | DEPENDENCY_KINDS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


@dataclass
class Job:
    id: str
    kind: str
    label: str
    argv: list[str]
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    artifact_path: str | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    payload: dict[str, Any] | None = field(default=None, repr=False)
    model_id: str | None = None
    worker_slots: int = 1
    details: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "argv": list(self.argv),
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "artifact_path": self.artifact_path,
            "logs": list(self.logs),
            "model_id": self.model_id,
            "worker_slots": self.worker_slots,
            "details": self.details,
        }


class JobValidationError(ValueError):
    pass


class JobManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.checkpoint_root = self.root / ".deepdeck" / "checkpoints"
        self.trajectory_path = self.root / ".deepdeck" / "trajectories" / "decisions.jsonl"
        self.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
        self.trajectory_path.touch(exist_ok=True)
        self.database_path = self.root / ".deepdeck" / "learner.db"
        self._initialize_database()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            current = {job.id: job.public() for job in self._jobs.values()}
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT payload FROM jobs ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        persisted = [self._reconcile_persisted_job(json.loads(row[0])) for row in rows]
        merged = {item["id"]: item for item in persisted}
        merged.update(current)
        return sorted(merged.values(), key=lambda item: item["created_at"], reverse=True)[:100]

    @staticmethod
    def _reconcile_persisted_job(job: dict[str, Any]) -> dict[str, Any]:
        if job.get("status") != "running":
            return job
        if job.get("kind") != "training.pool":
            return {
                **job,
                "status": "stopped",
                "finished_at": job.get("finished_at") or utc_now(),
                "logs": [
                    *list(job.get("logs", [])),
                    "The workbench restarted; this local process is no longer attached.",
                ][-MAX_LOG_LINES:],
            }
        artifact = str(job.get("artifact_path", "")).strip()
        if not artifact:
            return job
        expected = str(Path(artifact).resolve()).casefold()
        for process in psutil.process_iter(["cmdline"]):
            try:
                command = " ".join(process.info.get("cmdline") or []).casefold()
            except (psutil.Error, OSError):
                continue
            if "oracle_ai.training.league" in command and expected in command:
                return job
        return {**job, "status": "stopped"}

    def _initialize_database(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS jobs ("
                "id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
                "kind TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def _persist(self, job: Job) -> None:
        payload = job.public()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO jobs (id, created_at, kind, payload) VALUES (?, ?, ?, ?)",
                (job.id, job.created_at, job.kind, json.dumps(payload)),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public() if job else None

    def resources(self) -> dict[str, Any]:
        with self._lock:
            jobs = list(self._jobs.values())
        return resource_snapshot(self.root, jobs)

    def create(self, raw: dict[str, Any]) -> dict[str, Any]:
        kind = str(raw.get("kind", ""))
        if kind not in SUPPORTED_KINDS:
            raise JobValidationError(f"Unsupported job kind: {kind or '(missing)'}")
        if kind == "training.pool":
            model = str(raw.get("model", "v12"))
            if model not in {"v11", "v12"}:
                raise JobValidationError("Model must be v11 or v12.")
            model_name = self._local_model_name(raw)
            self._bounded_int(raw, "parallel_matches", default=1, minimum=1, maximum=32)
            self._bounded_int(raw, "gpu_memory_mb", default=0, minimum=0, maximum=24 * 1024)
            argv: list[str] = []
            label = f"{model_name} · {model.upper()} · preparing"
            artifact: Path | None = None
            deferred_payload: dict[str, Any] | None = dict(raw)
        else:
            argv, label, artifact = self._command(kind, raw)
            deferred_payload = None
        model_id = str(raw.get("model_id", "")).strip() or None
        worker_slots = (
            self._bounded_int(raw, "parallel_matches", default=1, minimum=1, maximum=32)
            if kind == "training.pool"
            else 1
        )
        job = Job(
            id=str(uuid.uuid4()),
            kind=kind,
            label=label,
            argv=argv,
            artifact_path=str(artifact) if artifact else None,
            payload=deferred_payload,
            model_id=model_id,
            worker_slots=worker_slots,
            details=raw.get("_job_details"),
        )
        with self._lock:
            if kind in TRAINING_KINDS and any(
                candidate.kind in TRAINING_KINDS and candidate.status in {"queued", "running"}
                for candidate in self._jobs.values()
            ):
                raise JobValidationError("Only one training job may run at a time.")
            if model_id and kind in {PLAYTEST_KIND, MATCHMAKING_KIND}:
                run, _ = find_model_run(self.root, model_id)
                plan = load_resource_plan(run)
                limit_key = "localMatches" if kind == PLAYTEST_KIND else "leagueMatches"
                running = sum(
                    worker["kind"] == kind and worker["modelId"] == model_id
                    for worker in resource_snapshot(self.root, list(self._jobs.values()))[
                        "workers"
                    ]
                )
                queued = sum(
                    candidate.kind == kind
                    and candidate.model_id == model_id
                    and candidate.status == "queued"
                    for candidate in self._jobs.values()
                )
                active = running + queued
                if active >= plan[limit_key]:
                    raise JobValidationError(
                        f"{limit_key} allocation is full for this local model."
                    )
            dependency = self._dependency_for(kind, raw)
            active_dependency_jobs = [
                candidate
                for candidate in self._jobs.values()
                if candidate.kind in DEPENDENCY_KINDS and candidate.status in {"queued", "running"}
            ]
            if dependency == "stack" and active_dependency_jobs:
                raise JobValidationError("A local runtime task is already running.")
            if dependency and any(
                candidate.kind == "dependency.stack.prepare" for candidate in active_dependency_jobs
            ):
                raise JobValidationError("The local runtime setup is already running.")
            if dependency and any(
                dependency in candidate.label.lower() for candidate in active_dependency_jobs
            ):
                raise JobValidationError(f"A {dependency} dependency task is already running.")
            self._jobs[job.id] = job
            self._persist(job)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job.public()

    def stop(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            process = job.process if job else None
        if not job:
            return None
        if process and process.poll() is None:
            job.logs.append("Stop requested by user.")
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    shell=False,
                    text=True,
                )
            else:
                process.terminate()
            self._persist(job)
        return job.public()

    def _command(self, kind: str, raw: dict[str, Any]) -> tuple[list[str], str, Path | None]:
        if kind in TRAINING_KINDS:
            return self._training_command(kind, raw)
        if kind in DEPENDENCY_KINDS:
            return self._dependency_command(kind, raw)
        if kind == MATCHMAKING_KIND:
            return self._matchmaking_command(raw)
        return self._playtest_command(raw)

    @staticmethod
    def _dependency_for(kind: str, raw: dict[str, Any]) -> str | None:
        if kind == "dependency.engine.start":
            return "engine"
        if kind == "dependency.pixi.prepare":
            return "pixi"
        if kind == "dependency.sync":
            dependency = str(raw.get("dependency", ""))
            return dependency if dependency in {"engine", "pixi"} else None
        if kind == "dependency.stack.prepare":
            return "stack"
        return None

    def _dependency_command(self, kind: str, raw: dict[str, Any]) -> tuple[list[str], str, None]:
        if kind == "dependency.stack.prepare":
            return (
                [
                    sys.executable,
                    "-m",
                    "deepdeck_learner.dependencies",
                    "bootstrap",
                    "--root",
                    str(self.root),
                ],
                "Local Engine + Pixi setup",
                None,
            )
        if kind == "dependency.engine.start":
            manifest = self.root / "external" / "deepdeck-engine" / "Cargo.toml"
            if not manifest.is_file():
                raise JobValidationError("DeepDeckEngine is missing. Synchronize it first.")
            if current_revision(self.root, "engine") != pinned_revision(self.root, "engine"):
                raise JobValidationError(
                    "DeepDeckEngine is not at the compatible revision. Sync it first."
                )
            cargo = shutil.which("cargo")
            if not cargo and not engine_build_current(self.root):
                raise JobValidationError("Install Rust and Cargo before starting DeepDeckEngine.")
            if engine_build_current(self.root):
                return (
                    [str(engine_binary(self.root))],
                    "DeepDeckEngine local server",
                    None,
                )
            assert cargo is not None
            return (
                [
                    cargo,
                    "run",
                    "--manifest-path",
                    str(manifest),
                    "--locked",
                    "--bin",
                    "mtg-engine-server",
                ],
                "DeepDeckEngine local server",
                None,
            )
        if kind == "dependency.pixi.prepare":
            return (
                [
                    sys.executable,
                    "-m",
                    "deepdeck_learner.dependencies",
                    "prepare-pixi",
                    "--root",
                    str(self.root),
                ],
                "Prepare DeepDeckPixi",
                None,
            )
        dependency = str(raw.get("dependency", ""))
        if dependency not in {"engine", "pixi"}:
            raise JobValidationError("Dependency must be engine or pixi.")
        return (
            [
                sys.executable,
                "-m",
                "deepdeck_learner.dependencies",
                "sync",
                "--root",
                str(self.root),
                "--dependency",
                dependency,
            ],
            f"Sync DeepDeck{dependency.title()}",
            None,
        )

    def _training_command(self, kind: str, raw: dict[str, Any]) -> tuple[list[str], str, Path]:
        model = str(raw.get("model", "v12"))
        if model not in {"v11", "v12"}:
            raise JobValidationError("Model must be v11 or v12.")
        if kind == "training.pool":
            return self._pool_training_command(model, raw)
        epochs = self._bounded_int(raw, "epochs", default=3, minimum=1, maximum=1000)
        seed = self._bounded_int(raw, "seed", default=1, minimum=0, maximum=2**31 - 1)
        learning_rate = self._bounded_float(
            raw, "learning_rate", default=0.0003, minimum=1e-8, maximum=1.0
        )
        device = str(raw.get("device", "cuda"))
        if device != "cpu" and not device.startswith("cuda"):
            raise JobValidationError("Device must be cpu or a cuda device.")
        argv = [
            sys.executable,
            "-m",
            "deepdeck_examples.deep_learning.training",
            model,
        ]
        if kind == "training.smoke":
            argv.append("--smoke")
        else:
            dataset = Path(str(raw.get("dataset", ""))).expanduser().resolve()
            if not dataset.is_file() or dataset.suffix.lower() != ".jsonl":
                raise JobValidationError("Dataset must be an existing .jsonl file.")
            argv.extend(["--dataset", str(dataset)])
        target = self._new_checkpoint_target(model)
        argv.extend(
            [
                "--output",
                str(target),
                "--epochs",
                str(epochs),
                "--learning-rate",
                str(learning_rate),
                "--device",
                device,
                "--seed",
                str(seed),
            ]
        )
        resume = raw.get("resume")
        if resume:
            resume_path = Path(str(resume)).expanduser().resolve()
            if not (resume_path / "config.json").is_file():
                raise JobValidationError("Resume must be a checkpoint directory.")
            argv.extend(["--resume", str(resume_path)])
        return argv, f"{model.upper()} {'smoke' if kind.endswith('smoke') else 'dataset'}", target

    def _pool_training_command(
        self, model: str, raw: dict[str, Any]
    ) -> tuple[list[str], str, Path]:
        try:
            import yaml
        except ModuleNotFoundError as error:
            raise JobValidationError(
                "Install DeepDeckLearner's deep-learning dependencies."
            ) from error
        workers = self._bounded_int(raw, "parallel_matches", default=1, minimum=1, maximum=32)
        model_name = self._local_model_name(raw)
        reserve_playtest = bool(raw.get("reserve_playtest", True))
        pool_path = self.root / ".deepdeck" / "training-deck-pool.json"
        try:
            pool = json.loads(pool_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise JobValidationError("Create a training deck pool before starting.") from error
        selected = pool.get("decks", []) if isinstance(pool, dict) else []
        required_format = "legacy" if model == "v12" else "commander"
        compatible = [
            deck
            for deck in selected
            if isinstance(deck, dict) and deck.get("format") == required_format
        ]
        if not compatible:
            raise JobValidationError(
                f"{model.upper()} requires at least one {required_format} deck in the pool."
            )
        now = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
        slug = re.sub(r"[^a-z0-9]+", "-", model_name.casefold()).strip("-") or "local-model"
        model_id = f"{slug[:40]}-{now.lower()}"
        run = self.root / ".deepdeck" / "runs" / f"{now}-{slug[:40]}-{model}"
        run.mkdir(parents=True, exist_ok=False)
        template = (
            self.root
            / "configs"
            / "oracle-ai"
            / ("league-v12-legacy.yaml" if model == "v12" else "league-v11-alphastar.yaml")
        )
        config = yaml.safe_load(template.read_text(encoding="utf-8"))
        engine_url = str(config.get("engineUrl", "http://127.0.0.1:8787"))
        rules_cache_path = self.root / ".deepdeck" / "oracle-card-rules.json"
        try:
            rules_cache = json.loads(rules_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rules_cache = {}
        if not isinstance(rules_cache, dict):
            rules_cache = {}
        catalog: dict[str, list[dict[str, Any]]] = {}
        with httpx.Client(base_url=engine_url.rstrip("/"), timeout=30.0) as engine:
            for deck in compatible:
                version_id = str(deck["id"])
                snapshot_path = self.root / ".deepdeck" / "decks" / f"{version_id}.json"
                try:
                    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as error:
                    raise JobValidationError(
                        f"Download {deck.get('name', version_id)} again before training."
                    ) from error
                entries = [
                    entry
                    for entry in snapshot.get("cards", [])
                    if isinstance(entry, dict) and entry.get("section") != "considering"
                ]
                missing_rules: dict[str, dict[str, Any]] = {}
                for entry in entries:
                    card_id = str(
                        entry.get("cardId")
                        or entry.get("printingId")
                        or entry.get("scryfallId")
                        or ""
                    )
                    if card_id and not isinstance(entry.get("rules"), list) and not isinstance(
                        rules_cache.get(card_id), list
                    ):
                        missing_rules[card_id] = entry

                def parse_rules(item: tuple[str, dict[str, Any]]) -> tuple[str, list[Any]]:
                    card_id, entry = item
                    response = engine.post(
                        "/oracle/rules",
                        json={
                            "cardName": str(entry.get("name", "")),
                            "typeLine": str(entry.get("typeLine", "")),
                            "manaCost": entry.get("manaCost"),
                            "oracleText": entry.get("oracleText"),
                            "faces": [],
                        },
                    )
                    if response.status_code >= 400:
                        raise JobValidationError(
                            f"Engine could not parse {entry.get('name', card_id)}: "
                            f"{response.text}"
                        )
                    return card_id, list(response.json().get("rules", []))

                if missing_rules:
                    worker_count = min(8, len(missing_rules))
                    with ThreadPoolExecutor(max_workers=worker_count) as executor:
                        for card_id, parsed_rules in executor.map(
                            parse_rules, missing_rules.items()
                        ):
                            rules_cache[card_id] = parsed_rules
                cards: list[dict[str, Any]] = []
                for entry in entries:
                    quantity = max(0, int(entry.get("quantity", 0)))
                    card_id = str(
                        entry.get("cardId")
                        or entry.get("printingId")
                        or entry.get("scryfallId")
                        or ""
                    )
                    if not card_id:
                        raise JobValidationError(
                            f"{deck.get('name', version_id)} contains a card without an id."
                        )
                    raw_rules = entry.get("rules")
                    rules: list[Any]
                    if isinstance(raw_rules, list):
                        rules = raw_rules
                    else:
                        cached = rules_cache.get(card_id)
                        if isinstance(cached, list):
                            rules = cached
                        else:
                            raise JobValidationError(
                                f"Engine did not return rules for {entry.get('name', card_id)}."
                            )
                    section = str(entry.get("section", "main"))
                    normalized = {
                        "id": card_id,
                        "name": str(entry.get("name", "")),
                        "typeLine": str(entry.get("typeLine", "")),
                        "manaCost": str(entry.get("manaCost") or ""),
                        "power": entry.get("power"),
                        "toughness": entry.get("toughness"),
                        "rules": rules,
                        "isSideboard": section in {"sideboard", "companion"},
                        "isCommander": section == "commander",
                        "sourceSessionId": version_id,
                    }
                    cards.extend(dict(normalized) for _ in range(quantity))
                name = (
                    f"{deck.get('name', 'Deck')} · v{deck.get('version', 1)} · "
                    f"{version_id[:8]}"
                )
                catalog[name] = cards
        rules_cache_path.write_text(
            json.dumps(rules_cache, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        catalog_path = run / "training-decks.json"
        catalog_path.write_text(
            json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        config.pop("deckSource", None)
        config.pop("metaLegacyDeckSelection", None)
        config["deckCatalog"] = str(catalog_path)
        config["outputDir"] = str(run)
        config["candidateModelName"] = model_id
        config["analyticsPilotId"] = model_id
        config["trainingParticipantId"] = model_id
        config["continuous"] = True
        config["parallelGameWorkers"] = workers
        config["rolloutBatchGames"] = workers
        resource_plan = {
            "trainingMatches": workers,
            "leagueMatches": 1,
            "localMatches": 1,
            "gpuMemoryMb": self._bounded_int(
                raw, "gpu_memory_mb", default=0, minimum=0, maximum=24 * 1024
            ),
        }
        resource_path = run / "learner-resources.json"
        resource_path.write_text(
            json.dumps(resource_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        config["learnerResourcePlan"] = str(resource_path)
        config["gpuMemoryLimitMb"] = resource_plan["gpuMemoryMb"]
        config["modelEvaluationEnabled"] = False
        config["groundTruthEvaluationEnabled"] = False
        config["resumeLeagueState"] = False
        config.pop("resumeCheckpoint", None)
        config.pop("resumeCheckpointOptional", None)
        config["serviceRefreshEvery"] = 1 if reserve_playtest else 1_000_000
        config["learnerSettings"] = {
            "modelId": model_id,
            "modelName": model_name,
            "reservePlaytest": reserve_playtest,
            "selectedDeckVersionIds": [str(deck["id"]) for deck in compatible],
        }
        config_path = run / "training-config.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        checkpoint_path = run / "live" / model_id
        metadata = {
            "schemaVersion": "local-model/v1",
            "id": model_id,
            "name": model_name,
            "architecture": model,
            "format": required_format,
            "description": (
                "Structured two-player Legacy policy with two relative value slots."
                if model == "v12"
                else "Structured Commander policy with four multiplayer value slots."
            ),
            "createdAt": utc_now(),
            "checkpointPath": str(checkpoint_path),
            "reservePlaytest": reserve_playtest,
            "decks": compatible,
            "resourcePlan": resource_plan,
        }
        (run / "local-model.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        argv = [sys.executable, "-m", "oracle_ai.training.league", "--config", str(config_path)]
        deck_label = "deck" if len(compatible) == 1 else "decks"
        return argv, f"{model_name} · {model.upper()} · {len(compatible)} {deck_label}", run

    @staticmethod
    def _local_model_name(raw: dict[str, Any]) -> str:
        name = str(raw.get("model_name", "")).strip()
        if not 2 <= len(name) <= 64 or any(ord(character) < 32 for character in name):
            raise JobValidationError("Model name must contain between 2 and 64 visible characters.")
        return name

    def _new_checkpoint_target(self, model: str) -> Path:
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)
        base = self.checkpoint_root / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{model}"
        target = base
        suffix = 2
        while target.exists():
            target = Path(f"{base}-{suffix}")
            suffix += 1
        return target

    def _playtest_command(self, raw: dict[str, Any]) -> tuple[list[str], str, None]:
        example, checkpoint_path, model_name = self._owned_model(raw)
        engine_url = str(raw.get("engine_url", "http://127.0.0.1:8787"))
        if not is_loopback_url(engine_url):
            raise JobValidationError("Local playtesting requires a loopback Engine URL.")
        own_deck = str(raw.get("deck_version_id", "")).strip()
        opponent_deck = str(raw.get("opponent_deck_version_id", "")).strip()
        if not own_deck or not opponent_deck:
            raise JobValidationError("Choose a deck or use the random option for both seats.")
        game_format = str(raw.get("format", "legacy"))
        if game_format not in {"legacy", "commander"}:
            raise JobValidationError("Format must be legacy or commander.")
        run = checkpoint_path.parent.parent
        try:
            catalog = json.loads((run / "training-decks.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise JobValidationError(
                "This model's local training deck catalog is unavailable."
            ) from error
        if not isinstance(catalog, dict):
            raise JobValidationError("This model's training deck catalog is invalid.")

        validated_names = self._playtest_validated_deck_names(run, game_format)
        eligible_catalog = {
            str(deck_name): cards
            for deck_name, cards in catalog.items()
            if not validated_names or str(deck_name) in validated_names
        }

        def selected_deck(version_id: str) -> tuple[str, list[dict[str, Any]]]:
            for deck_name, cards in eligible_catalog.items():
                if isinstance(cards, list) and any(
                    isinstance(card, dict) and card.get("sourceSessionId") == version_id
                    for card in cards
                ):
                    return str(deck_name), cards
            raise JobValidationError("Choose decks that belong to this model's training pool.")

        available = [
            (str(deck_name), cards)
            for deck_name, cards in eligible_catalog.items()
            if isinstance(cards, list)
            and any(isinstance(card, dict) and card.get("sourceSessionId") for card in cards)
        ]
        if not available:
            raise JobValidationError("This model has no playable deck in its training pool.")

        def version_id(cards: list[dict[str, Any]]) -> str:
            return str(
                next(card["sourceSessionId"] for card in cards if card.get("sourceSessionId"))
            )

        randomizer = random.SystemRandom()
        if own_deck == "random":
            own_name, own_cards = randomizer.choice(available)
            own_deck = version_id(own_cards)
        else:
            own_name, own_cards = selected_deck(own_deck)

        if opponent_deck == "random":
            candidates = [
                candidate for candidate in available if version_id(candidate[1]) != own_deck
            ] or available
            stats_by_id = {
                str(item.get("deckVersionId")): item
                for item in self._deck_statistics_for_model(str(raw.get("model_id", "")))
            }
            target = stats_by_id.get(own_deck, {"ordinal": 0.0, "games": 0})
            config = self._playtest_matchmaking_config(run)
            weights = [
                plackett_luce_matchmaking_weight(
                    stats_by_id.get(version_id(cards), {"ordinal": 0.0, "games": 0}),
                    target_ordinal=float(target.get("ordinal", 0.0) or 0.0),
                    minimum_games=int(target.get("games", 0) or 0),
                    random_floor=float(config.get("randomFloor", 0.2)),
                    rating_scale=float(config.get("ratingScale", 10.0)),
                    underplayed_strength=float(config.get("underplayedStrength", 0.35)),
                    game_prior=float(config.get("matchPrior", 10.0)),
                )
                for _, cards in candidates
            ]
            opponent_name, opponent_cards = randomizer.choices(candidates, weights=weights, k=1)[0]
            opponent_deck = version_id(opponent_cards)
        else:
            opponent_name, opponent_cards = selected_deck(opponent_deck)
        setup_path = self.root / ".deepdeck" / "playtests" / f"{uuid.uuid4()}.json"
        setup_path.parent.mkdir(parents=True, exist_ok=True)
        starting_life = 40 if game_format == "commander" else 20
        setup_path.write_text(
            json.dumps(
                {
                    "setup": {
                        "openingHandSize": 7,
                        "startingPlayer": 0,
                        "players": [
                            {
                                "id": "local-human",
                                "name": own_name,
                                "startingLife": starting_life,
                                "cards": own_cards,
                            },
                            {
                                "id": "local-agent",
                                "name": opponent_name,
                                "startingLife": starting_life,
                                "cards": opponent_cards,
                            },
                        ],
                    },
                    "seed": 1,
                    "gameMode": game_format,
                    "maxTurns": 200,
                    "humanPlayerIds": ["local-human"],
                    "analyticsDeckSessionByPlayerId": {
                        "local-human": own_deck,
                        "local-agent": opponent_deck,
                    },
                    "mulliganEnabled": True,
                    "freeMulligans": 1 if game_format == "commander" else 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        argv = [
            sys.executable,
            "-m",
            "deepdeck_examples.run",
            example,
            "--target",
            "local",
            "--engine-url",
            engine_url,
            "--start-local-game",
            "--local-game-setup",
            str(setup_path),
            "--local-format",
            game_format,
        ]
        argv.extend(["--checkpoint", str(checkpoint_path)])
        raw["_job_details"] = {
            "engineUrl": engine_url,
            "playerDeck": {"id": own_deck, "name": own_name},
            "opponentDeck": {"id": opponent_deck, "name": opponent_name},
            "selectionOrder": "player-then-rating-proximity",
        }
        return argv, f"{model_name} · local {game_format}", None

    def _deck_statistics_for_model(self, model_id: str) -> list[dict[str, Any]]:
        from .models import deck_statistics

        return [item for item in deck_statistics(self.root) if item.get("modelId") == model_id]

    @staticmethod
    def _playtest_matchmaking_config(run: Path) -> dict[str, Any]:
        try:
            resolved = json.loads((run / "resolved-config.json").read_text(encoding="utf-8"))
            config = resolved.get("trainingScenarioRandomizer", {}).get("matchmaking", {})
            return config if isinstance(config, dict) else {}
        except (AttributeError, OSError, ValueError):
            return {}

    @staticmethod
    def _playtest_validated_deck_names(run: Path, game_format: str) -> set[str]:
        key = "resolvedCommanderDecks" if game_format == "commander" else "resolvedLegacyDecks"
        try:
            resolved = json.loads((run / "resolved-config.json").read_text(encoding="utf-8"))
            names = resolved.get(key, [])
            return {str(name) for name in names} if isinstance(names, list) else set()
        except (OSError, ValueError):
            return set()

    def _matchmaking_command(self, raw: dict[str, Any]) -> tuple[list[str], str, None]:
        if not os.getenv("DEEPDECK_API_KEY"):
            raise JobValidationError(
                "Add DEEPDECK_API_KEY to the project .env and restart DeepDeckLearner."
            )
        example, checkpoint_path, model_name = self._owned_model(raw)
        competition = str(raw.get("competition_version_id", "")).strip()
        deck = str(raw.get("deck_version_id", "")).strip()
        if not competition or not deck:
            raise JobValidationError("Choose an active competition and a deck.")
        speed = str(raw.get("speed", "1s"))
        if speed not in {"100ms", "1s", "10s"}:
            raise JobValidationError("Speed must be 100ms, 1s, or 10s.")
        argv = [
            sys.executable,
            "-m",
            "deepdeck_examples.run",
            example,
            "--target",
            "ddl",
            "--speed",
            speed,
            "--competition-version-id",
            competition,
            "--deck-version-id",
            deck,
        ]
        if not bool(raw.get("continuous", False)):
            argv.append("--once")
        argv.extend(["--checkpoint", str(checkpoint_path)])
        return argv, f"{model_name} · Deep Deck League", None

    def _owned_model(self, raw: dict[str, Any]) -> tuple[str, Path, str]:
        architecture = str(raw.get("agent", ""))
        if architecture not in {"v11", "v12"}:
            raise JobValidationError("Choose one of your locally trained V11 or V12 models.")
        checkpoint = Path(str(raw.get("checkpoint", ""))).expanduser().resolve()
        runs_root = (self.root / ".deepdeck" / "runs").resolve()
        try:
            checkpoint.relative_to(runs_root)
        except ValueError as error:
            raise JobValidationError("Choose a model created by this Learner workspace.") from error
        metadata_path = checkpoint.parent.parent / "local-model.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise JobValidationError("The selected local model metadata is unavailable.") from error
        if (
            metadata.get("architecture") != architecture
            or Path(str(metadata.get("checkpointPath", ""))).resolve() != checkpoint
        ):
            raise JobValidationError("The selected local model does not match its checkpoint.")
        if str(raw.get("model_id", "")).strip() != str(metadata.get("id", "")):
            raise JobValidationError("Choose the registered identity of your local model.")
        if not (checkpoint / "manifest.json").is_file() or not (
            checkpoint / "checkpoint.pt"
        ).is_file():
            raise JobValidationError("This model is still preparing its first playable weights.")
        return architecture, checkpoint, str(metadata.get("name", architecture.upper()))

    @staticmethod
    def _bounded_int(
        raw: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int
    ) -> int:
        try:
            value = int(raw.get(key, default))
        except (TypeError, ValueError) as error:
            raise JobValidationError(f"{key} must be an integer.") from error
        if not minimum <= value <= maximum:
            raise JobValidationError(f"{key} must be between {minimum} and {maximum}.")
        return value

    @staticmethod
    def _bounded_float(
        raw: dict[str, Any], key: str, *, default: float, minimum: float, maximum: float
    ) -> float:
        try:
            value = float(raw.get(key, default))
        except (TypeError, ValueError) as error:
            raise JobValidationError(f"{key} must be a number.") from error
        if not minimum <= value <= maximum:
            raise JobValidationError(f"{key} must be between {minimum} and {maximum}.")
        return value

    def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = utc_now()
        self._persist(job)
        try:
            if job.kind == "training.pool" and job.payload is not None:
                job.logs.append("Preparing local cards and Oracle rules for training.")
                self._persist(job)
                try:
                    argv, label, artifact = self._training_command(job.kind, job.payload)
                except (JobValidationError, httpx.HTTPError, OSError, ValueError) as error:
                    job.logs.append(f"Unable to prepare training: {error}")
                    job.status = "failed"
                    job.exit_code = -1
                    return
                job.argv = argv
                job.label = label
                job.artifact_path = str(artifact)
                metadata = json.loads((artifact / "local-model.json").read_text(encoding="utf-8"))
                job.model_id = str(metadata["id"])
                job.payload = None
                self._persist(job)
            environment = self._child_environment()
            if job.kind in {"dependency.engine.start", "dependency.stack.prepare"}:
                engine_environment = self._engine_environment()
                environment.update(engine_environment)
                if engine_environment:
                    job.logs.append("Using Rust's bundled linker for the local Windows build.")
            if job.kind == "training.pool" and job.artifact_path:
                environment["PYTHONUNBUFFERED"] = "1"
                job.exit_code = self._run_durable_training_process(job, environment)
            else:
                process = subprocess.Popen(
                    job.argv,
                    cwd=self.root,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )
                job.process = process
                assert process.stdout is not None
                for line in process.stdout:
                    clean = line.rstrip()
                    job.logs.append(clean)
                    marker = "DEEPDECK_PLAYTEST_SESSION "
                    if clean.startswith(marker):
                        try:
                            session = json.loads(clean[len(marker):])
                        except ValueError:
                            session = None
                        if isinstance(session, dict):
                            job.details = {**(job.details or {}), **session}
                            self._persist(job)
                job.exit_code = process.wait()
            job.status = "completed" if job.exit_code == 0 else "failed"
        except OSError as error:
            job.logs.append(f"Unable to start process: {error}")
            job.status = "failed"
            job.exit_code = -1
        finally:
            job.process = None
            job.finished_at = utc_now()
            self._persist(job)

    def _run_durable_training_process(
        self, job: Job, environment: dict[str, str]
    ) -> int:
        log_path = Path(str(job.artifact_path)) / "learner-process.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        offset = log_path.stat().st_size if log_path.is_file() else 0
        with log_path.open("a", encoding="utf-8") as output:
            process = subprocess.Popen(
                job.argv,
                cwd=self.root,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            job.process = process
            while True:
                try:
                    exit_code = process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    exit_code = None
                offset = self._append_process_log(job, log_path, offset)
                if exit_code is not None:
                    return exit_code

    @staticmethod
    def _append_process_log(job: Job, path: Path, offset: int) -> int:
        with path.open("r", encoding="utf-8", errors="replace") as source:
            source.seek(offset)
            for line in source:
                job.logs.append(line.rstrip())
            return source.tell()

    @staticmethod
    def _child_environment() -> dict[str, str]:
        allowed = {
            "PATH",
            "Path",
            "PYTHONPATH",
            "SYSTEMROOT",
            "SystemRoot",
            "TEMP",
            "TMP",
            "CUDA_VISIBLE_DEVICES",
            "HOME",
            "LOCALAPPDATA",
            "USERPROFILE",
            "ProgramData",
            "ProgramFiles",
            "ProgramFiles(x86)",
            "ProgramW6432",
            "DEEPDECK_API_KEY",
            "DEEPDECK_PLATFORM_URL",
            "DDL_PLATFORM_API_URL",
        }
        return {key: value for key, value in os.environ.items() if key in allowed}

    @staticmethod
    def _engine_environment() -> dict[str, str]:
        if os.name != "nt" or shutil.which("link"):
            return {}
        rustc = shutil.which("rustc")
        if not rustc:
            return {}
        result = subprocess.run(
            [rustc, "--print", "sysroot"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
            shell=False,
        )
        sysroot = Path(result.stdout.strip()) if result.returncode == 0 else None
        linker = (
            sysroot / "lib" / "rustlib" / "x86_64-pc-windows-msvc" / "bin" / "rust-lld.exe"
            if sysroot
            else None
        )
        if not linker or not linker.is_file():
            return {}
        return {"RUSTFLAGS": f"-C linker={linker}"}
