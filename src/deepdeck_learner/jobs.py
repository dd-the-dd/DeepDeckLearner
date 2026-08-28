from __future__ import annotations

import os
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

MAX_LOG_LINES = 500
TRAINING_KINDS = {"training.smoke", "training.dataset"}
PLAYTEST_KIND = "playtest.agent"
SUPPORTED_KINDS = TRAINING_KINDS | {PLAYTEST_KIND}


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
        return self._playtest_command(raw)

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
        device = str(raw.get("device", "cpu"))
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
            process = subprocess.Popen(
                job.argv,
                cwd=self.root,
                env=self._child_environment(),
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
        }
        return {key: value for key, value in os.environ.items() if key in allowed}
