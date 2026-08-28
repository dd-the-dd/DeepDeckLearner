from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .dependencies import current_revision, engine_binary, engine_build_current, pinned_revision

MAX_LOG_LINES = 500
TRAINING_KINDS = {"training.smoke", "training.dataset"}
PLAYTEST_KIND = "playtest.agent"
MATCHMAKING_KIND = "matchmaking.agent"
DEPENDENCY_KINDS = {
    "dependency.engine.start",
    "dependency.pixi.prepare",
    "dependency.sync",
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
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [job.public() for job in jobs[:25]]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public() if job else None

    def create(self, raw: dict[str, Any]) -> dict[str, Any]:
        kind = str(raw.get("kind", ""))
        if kind not in SUPPORTED_KINDS:
            raise JobValidationError(f"Unsupported job kind: {kind or '(missing)'}")
        argv, label, artifact = self._command(kind, raw)
        job = Job(
            id=str(uuid.uuid4()),
            kind=kind,
            label=label,
            argv=argv,
            artifact_path=str(artifact) if artifact else None,
        )
        with self._lock:
            if kind in TRAINING_KINDS and any(
                candidate.kind in TRAINING_KINDS and candidate.status in {"queued", "running"}
                for candidate in self._jobs.values()
            ):
                raise JobValidationError("Only one training job may run at a time.")
            dependency = self._dependency_for(kind, raw)
            if dependency and any(
                candidate.kind in DEPENDENCY_KINDS
                and dependency in candidate.label.lower()
                and candidate.status in {"queued", "running"}
                for candidate in self._jobs.values()
            ):
                raise JobValidationError(f"A {dependency} dependency task is already running.")
            self._jobs[job.id] = job
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
            process.terminate()
        return job.public()

    def _command(
        self, kind: str, raw: dict[str, Any]
    ) -> tuple[list[str], str, Path | None]:
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
        return None

    def _dependency_command(
        self, kind: str, raw: dict[str, Any]
    ) -> tuple[list[str], str, None]:
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

    def _training_command(
        self, kind: str, raw: dict[str, Any]
    ) -> tuple[list[str], str, Path]:
        model = str(raw.get("model", "v12"))
        if model not in {"v11", "v12"}:
            raise JobValidationError("Model must be v11 or v12.")
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
        example = str(raw.get("agent", "random"))
        if example not in {"random", "alexios", "v11", "v12"}:
            raise JobValidationError("Unknown example agent.")
        engine_url = str(raw.get("engine_url", "http://127.0.0.1:8787"))
        if not is_loopback_url(engine_url):
            raise JobValidationError("Local playtesting requires a loopback Engine URL.")
        own_deck = str(raw.get("deck_session_id", "")).strip()
        opponent_deck = str(raw.get("opponent_deck_session_id", "")).strip()
        if not own_deck or not opponent_deck:
            raise JobValidationError("Both local deck session IDs are required.")
        game_format = str(raw.get("format", "legacy"))
        if game_format not in {"legacy", "commander"}:
            raise JobValidationError("Format must be legacy or commander.")
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
            "--local-format",
            game_format,
            "--local-deck-session-id",
            own_deck,
            "--local-opponent-deck-session-id",
            opponent_deck,
        ]
        checkpoint = raw.get("checkpoint")
        if example in {"v11", "v12"}:
            if checkpoint:
                argv.extend(["--checkpoint", str(Path(str(checkpoint)).expanduser().resolve())])
            else:
                argv.append("--allow-untrained")
        return argv, f"{example.upper()} local {game_format}", None

    def _matchmaking_command(self, raw: dict[str, Any]) -> tuple[list[str], str, None]:
        if not os.getenv("DEEPDECK_API_KEY"):
            raise JobValidationError(
                "Add DEEPDECK_API_KEY to the project .env and restart DeepDeckLearner."
            )
        example = str(raw.get("agent", "random"))
        if example not in {"random", "alexios", "v11", "v12"}:
            raise JobValidationError("Unknown example agent.")
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
        checkpoint = raw.get("checkpoint")
        if example in {"v11", "v12"}:
            checkpoint_path = Path(str(checkpoint or "")).expanduser().resolve()
            if not (checkpoint_path / "config.json").is_file():
                raise JobValidationError("Choose a completed checkpoint for V11 or V12.")
            argv.extend(["--checkpoint", str(checkpoint_path)])
        return argv, f"{example.upper()} Deep Deck League", None

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
        try:
            environment = self._child_environment()
            if job.kind == "dependency.engine.start":
                engine_environment = self._engine_environment()
                environment.update(engine_environment)
                if engine_environment:
                    job.logs.append("Using Rust's bundled linker for the local Windows build.")
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
                job.logs.append(line.rstrip())
            job.exit_code = process.wait()
            job.status = "completed" if job.exit_code == 0 else "failed"
        except OSError as error:
            job.logs.append(f"Unable to start process: {error}")
            job.status = "failed"
            job.exit_code = -1
        finally:
            job.process = None
            job.finished_at = utc_now()

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
            sysroot
            / "lib"
            / "rustlib"
            / "x86_64-pc-windows-msvc"
            / "bin"
            / "rust-lld.exe"
            if sysroot
            else None
        )
        if not linker or not linker.is_file():
            return {}
        return {"RUSTFLAGS": f"-C linker={linker}"}
