from __future__ import annotations

import csv
import json
import shutil
import subprocess
from io import StringIO
from pathlib import Path
from typing import Any

import psutil

DEFAULT_RESOURCE_PLAN = {
    "trainingMatches": 1,
    "leagueMatches": 1,
    "localMatches": 1,
    "gpuMemoryMb": 0,
}


def find_model_run(root: Path, model_id: str) -> tuple[Path, dict[str, Any]]:
    for metadata_path in (root / ".deepdeck" / "runs").glob("*/local-model.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(metadata, dict) and metadata.get("id") == model_id:
            return metadata_path.parent, metadata
    raise ValueError("Unknown local model.")


def load_resource_plan(run: Path) -> dict[str, int]:
    path = run / "learner-resources.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    return {
        key: int(value.get(key, default)) if isinstance(value, dict) else default
        for key, default in DEFAULT_RESOURCE_PLAN.items()
    }


def save_resource_plan(root: Path, model_id: str, payload: dict[str, Any]) -> dict[str, int]:
    run, _ = find_model_run(root, model_id)
    limits = {
        "trainingMatches": (0, 32),
        "leagueMatches": (0, 32),
        "localMatches": (0, 8),
        "gpuMemoryMb": (0, 24 * 1024),
    }
    plan: dict[str, int] = {}
    for key, (minimum, maximum) in limits.items():
        try:
            value = int(payload.get(key, DEFAULT_RESOURCE_PLAN[key]))
        except (TypeError, ValueError) as error:
            raise ValueError(f"{key} must be an integer.") from error
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}.")
        plan[key] = value
    path = run / "learner-resources.json"
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)
    control = run / "training-control.json"
    control.write_text(
        json.dumps(
            {"desiredState": "running" if plan["trainingMatches"] > 0 else "paused"},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return plan


def _gpu_process_memory() -> tuple[dict[int, int], int | None, int | None, bool]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {}, None, None, False
    try:
        process_result = subprocess.run(
            [
                executable,
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}, None, None, False
    process_telemetry = "[N/A]" not in process_result.stdout
    memory: dict[int, int] = {}
    for row in csv.reader(StringIO(process_result.stdout)):
        try:
            memory[int(row[0].strip())] = int(row[1].strip()) * 1024 * 1024
        except (IndexError, ValueError):
            continue
    try:
        gpu_result = subprocess.run(
            [
                executable,
                "--query-gpu=memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return memory, None, None, process_telemetry
    try:
        row = next(csv.reader(StringIO(gpu_result.stdout)))
        return (
            memory,
            int(row[0].strip()) * 1024 * 1024,
            int(row[1].strip()) * 1024 * 1024,
            process_telemetry,
        )
    except (StopIteration, IndexError, ValueError):
        return memory, None, None, process_telemetry


def _process_usage(
    process: psutil.Process, gpu_by_pid: dict[int, int]
) -> tuple[list[int], int, int]:
    processes = [process, *process.children(recursive=True)]
    pids = [candidate.pid for candidate in processes]
    ram = sum(candidate.memory_info().rss for candidate in processes)
    gpu = sum(gpu_by_pid.get(candidate.pid, 0) for candidate in processes)
    return pids, ram, gpu


def _record(
    *,
    job_id: str,
    model_id: str | None,
    label: str,
    kind: str,
    pids: list[int],
    ram: int,
    gpu: int | None,
    slots: int,
) -> dict[str, Any]:
    slot_divisor = max(1, slots)
    return {
        "jobId": job_id,
        "modelId": model_id,
        "label": label,
        "kind": kind,
        "pids": pids,
        "workerSlots": slots,
        "ramBytes": ram,
        "gpuBytes": gpu,
        "ramPerWorkerEstimate": ram // slot_divisor if slots else 0,
        "gpuPerWorkerEstimate": gpu // slot_divisor if gpu is not None and slots else gpu,
    }


def resource_snapshot(root: Path, jobs: list[Any]) -> dict[str, Any]:
    gpu_by_pid, gpu_total, gpu_used, gpu_process_telemetry = _gpu_process_memory()
    records: list[dict[str, Any]] = []
    local_jobs: list[Any] = []
    claimed_pids: set[int] = set()
    for job in jobs:
        process = job.process
        if job.status != "running" or process is None or process.poll() is not None:
            continue
        try:
            parent = psutil.Process(process.pid)
            pids, ram, measured_gpu = _process_usage(parent, gpu_by_pid)
            gpu = measured_gpu if gpu_process_telemetry else None
        except (psutil.Error, OSError):
            ram, gpu, pids = 0, None, [process.pid]
        claimed_pids.update(pids)
        slots = max(1, int(job.worker_slots or 1))
        if job.kind == "training.pool" and job.artifact_path:
            slots = load_resource_plan(Path(job.artifact_path))["trainingMatches"]
        records.append(
            _record(
                job_id=job.id,
                model_id=job.model_id,
                label=job.label,
                kind=job.kind,
                pids=pids,
                ram=ram,
                gpu=gpu,
                slots=slots,
            )
        )
        if job.kind == "playtest.agent":
            local_jobs.append(job)

    processes: list[tuple[psutil.Process, str]] = []
    for candidate in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(candidate.info.get("cmdline") or []).casefold()
        except (psutil.Error, OSError):
            continue
        if candidate.pid not in claimed_pids and (
            "oracle_ai.training.league" in command or "deepdeck_examples.run" in command
        ):
            processes.append((candidate, command))
    candidate_pids = {candidate.pid for candidate, _ in processes}
    top_level_processes: list[tuple[psutil.Process, str]] = []
    for candidate, command in processes:
        try:
            if candidate.ppid() not in candidate_pids:
                top_level_processes.append((candidate, command))
        except psutil.Error:
            continue
    for metadata_path in (root / ".deepdeck" / "runs").glob("*/local-model.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict):
            continue
        model_id = str(metadata.get("id", ""))
        model_name = str(metadata.get("name", model_id))
        run_text = str(metadata_path.parent.resolve()).casefold()
        checkpoint_text = str(Path(str(metadata.get("checkpointPath", ""))).resolve()).casefold()
        for candidate, command in top_level_processes:
            if candidate.pid in claimed_pids:
                continue
            if "oracle_ai.training.league" in command and run_text in command:
                kind = "training.pool"
                slots = load_resource_plan(metadata_path.parent)["trainingMatches"]
            elif "deepdeck_examples.run" in command and checkpoint_text in command:
                kind = (
                    "playtest.agent" if "--start-local-game" in command else "matchmaking.agent"
                )
                slots = 1
            else:
                continue
            try:
                pids, ram, measured_gpu = _process_usage(candidate, gpu_by_pid)
                gpu = measured_gpu if gpu_process_telemetry else None
            except (psutil.Error, OSError):
                continue
            claimed_pids.update(pids)
            records.append(
                _record(
                    job_id=f"recovered-{candidate.pid}",
                    model_id=model_id,
                    label=f"{model_name} - recovered local process",
                    kind=kind,
                    pids=pids,
                    ram=ram,
                    gpu=gpu,
                    slots=slots,
                )
            )
            if kind == "playtest.agent":
                local_jobs.append(candidate)
    engine_ram = 0
    try:
        engine_pids = {
            connection.pid
            for connection in psutil.net_connections(kind="tcp")
            if connection.pid and connection.laddr and connection.laddr.port == 8787
        }
        engine_ram = sum(psutil.Process(pid).memory_info().rss for pid in engine_pids)
    except (AttributeError, psutil.Error, OSError):
        pass
    per_game = engine_ram // max(1, len(local_jobs)) if local_jobs else 0
    virtual = psutil.virtual_memory()
    return {
        "system": {
            "ramTotalBytes": virtual.total,
            "ramUsedBytes": virtual.used,
            "ramAvailableBytes": virtual.available,
            "gpuTotalBytes": gpu_total,
            "gpuUsedBytes": gpu_used,
            "gpuProcessTelemetry": gpu_process_telemetry,
        },
        "workers": records,
        "engine": {
            "ramBytes": engine_ram,
            "activeLocalGames": len(local_jobs),
            "ramPerGameEstimate": per_game,
            "attribution": "Shared Engine RSS divided by active local games.",
        },
    }
