from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from deepdeck_learner.card_models import (
    compile_oracle_rules,
    enrich_card_characteristics,
    oracle_request,
)
from deepdeck_learner.jobs import (
    MAX_PUBLIC_LOG_LINE_CHARS,
    PUBLIC_LOG_LINES,
    Job,
    JobManager,
    JobValidationError,
    is_loopback_url,
)
from deepdeck_learner.resources import find_model_run


def local_checkpoint(root: Path, architecture: str = "v12") -> Path:
    run = root / ".deepdeck" / "runs" / "test-local-model"
    checkpoint = run / "live" / "my-local-ai"
    checkpoint.mkdir(parents=True)
    (checkpoint / "manifest.json").write_text("{}", encoding="utf-8")
    (checkpoint / "checkpoint.pt").touch()
    (run / "training-decks.json").write_text(
        json.dumps(
            {
                "Player Pool Deck": [
                    {
                        "id": "island",
                        "name": "Island",
                        "typeLine": "Basic Land — Island",
                        "rules": [],
                        "sourceSessionId": "pool-deck-1",
                    }
                ],
                "AI Pool Deck": [
                    {
                        "id": "mountain",
                        "name": "Mountain",
                        "typeLine": "Basic Land — Mountain",
                        "rules": [],
                        "sourceSessionId": "pool-deck-2",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run / "local-model.json").write_text(
        json.dumps(
            {
                "schemaVersion": "local-model/v1",
                "id": "my-local-ai",
                "name": "My Local AI",
                "architecture": architecture,
                "format": "legacy",
                "checkpointPath": str(checkpoint),
                "decks": [
                    {"id": "pool-deck-1", "name": "Player Pool Deck"},
                    {"id": "pool-deck-2", "name": "AI Pool Deck"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return checkpoint


def test_loopback_url_validation() -> None:
    assert is_loopback_url("http://127.0.0.1:8787")
    assert is_loopback_url("http://localhost:8787")
    assert not is_loopback_url("https://deepdeckleague.com")
    assert not is_loopback_url("file:///tmp/engine")


def test_owned_model_uses_agent_sdk_runtime_instead_of_architecture_allowlist(
    tmp_path: Path,
) -> None:
    checkpoint = local_checkpoint(tmp_path, architecture="v-next")
    (checkpoint / "sdk.weights").touch()
    metadata_path = checkpoint.parent.parent / "local-model.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["agentSdkRuntime"] = {
        "module": "future_agent.sdk_runner",
        "arguments": ["serve"],
        "checkpointArgument": "--weights",
        "requiredFiles": ["sdk.weights"],
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    command, resolved_checkpoint, name, checkpoint_argument = JobManager(  # noqa: SLF001
        tmp_path
    )._owned_model(
        {
            "model_id": "my-local-ai",
            # Client hints are deliberately stale: registered metadata is authoritative.
            "agent": "v13",
            "checkpoint": "not-the-registered-checkpoint",
        }
    )

    assert command == [
        sys.executable,
        "-m",
        "future_agent.sdk_runner",
        "serve",
    ]
    assert resolved_checkpoint == checkpoint.resolve()
    assert name == "My Local AI"
    assert checkpoint_argument == "--weights"


def test_public_job_logs_are_bounded_for_fast_dashboard_refreshes() -> None:
    job = Job(id="job", kind="training.pool", label="Trainer", argv=[])
    for index in range(PUBLIC_LOG_LINES + 5):
        job.logs.append(f"line-{index}" + ("x" * MAX_PUBLIC_LOG_LINE_CHARS))

    logs = job.public()["logs"]

    assert len(logs) == PUBLIC_LOG_LINES
    assert logs[0].startswith("line-5")
    assert all(len(line) <= MAX_PUBLIC_LOG_LINE_CHARS + len("... [truncated]") for line in logs)


def test_stop_reconnects_a_persisted_job_to_its_recovered_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    worker = {
        "jobId": "recovered-42",
        "modelId": "my-local-ai",
        "kind": "playtest.agent",
        "label": "Recovered local game",
        "pids": [42],
        "workerSlots": 1,
    }
    monkeypatch.setattr(manager, "resources", lambda: {"workers": [worker]})
    monkeypatch.setattr(
        manager,
        "list_jobs",
        lambda: [
            {
                "id": "original-job",
                "status": "running",
                "kind": "playtest.agent",
                "model_id": "my-local-ai",
            }
        ],
    )

    terminated: list[int] = []

    class RecoveredProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def terminate(self) -> None:
            terminated.append(self.pid)

        def kill(self) -> None:
            raise AssertionError("A responsive process should not be killed.")

    monkeypatch.setattr("deepdeck_learner.jobs.psutil.Process", RecoveredProcess)
    monkeypatch.setattr(
        "deepdeck_learner.jobs.psutil.wait_procs",
        lambda processes, timeout: (processes, []),
    )

    stopped = manager.stop("original-job")

    assert stopped is not None
    assert stopped["id"] == "original-job"
    assert stopped["status"] == "stopped"
    assert terminated == [42]


def test_league_match_markers_track_only_active_matches(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = Job(
        id="league-job",
        kind="matchmaking.agent",
        label="My agent",
        argv=[],
        status="running",
    )

    manager._consume_process_marker(  # noqa: SLF001
        job,
        'DEEPDECK_LEAGUE_MATCH {"matchId":"match-1","status":"running"}',
    )
    assert job.details == {"leagueMatches": [{"matchId": "match-1", "status": "running"}]}
    manager._consume_process_marker(  # noqa: SLF001
        job,
        'DEEPDECK_LEAGUE_MATCH {"matchId":"match-1","status":"complete"}',
    )
    assert job.details == {"leagueMatches": []}


def test_league_run_progress_is_retained_without_inventing_worker_completion(tmp_path):
    manager = JobManager(tmp_path)
    job = Job(
        id="bounded-job",
        kind="matchmaking.agent",
        label="A bounded worker",
        argv=[],
        status="running",
    )
    report = {"completedEntries": 2, "activeEntries": 1, "maxMatches": 3, "durationSeconds": None}
    manager._consume_process_marker(job, "DEEPDECK_LEAGUE_RUN " + json.dumps(report))
    assert job.details == {"leagueRun": report}
    assert job.status == "running"


@pytest.mark.parametrize("kind", ["playtest.table", "playtest.batch"])
@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed", "stopped"])
def test_agent_edit_guard_covers_every_table_seat(tmp_path, monkeypatch, kind, status) -> None:
    manager = JobManager(tmp_path)
    job = Job(
        id="table",
        kind=kind,
        label="Two different agents",
        argv=[],
        status=status,
        model_id="first-agent",
        details={"seats": [{"agentId": "first-agent"}, {"agentId": "opponent-agent"}]},
    )
    manager._jobs[job.id] = job  # noqa: SLF001
    monkeypatch.setattr(manager, "resources", lambda: {"workers": []})
    assert manager.model_has_active_workers("opponent-agent") is (status in {"queued", "running"})
    assert manager.model_has_active_workers("first-agent") is (status in {"queued", "running"})
    assert manager.model_has_active_workers("unrelated-agent") is False


def test_agent_edit_guard_still_covers_recovered_worker(tmp_path, monkeypatch) -> None:
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "resources", lambda: {"workers": [{"modelId": "recovered"}]})
    assert manager.model_has_active_workers("recovered") is True


def test_stop_gives_a_table_time_to_flush_its_replay(tmp_path, monkeypatch) -> None:
    manager = JobManager(tmp_path)
    directory = tmp_path / ".deepdeck" / "batches" / "0123456789abcdef"
    directory.mkdir(parents=True)

    class ResponsiveTable:
        finished = False

        def poll(self):
            return 0 if self.finished else None

        def wait(self, timeout):
            assert (directory / "stop.request").is_file()
            assert timeout == 8
            self.finished = True
            return 0

        def terminate(self):
            pytest.fail("A responsive table must flush before termination.")

    job = Job(
        id="table",
        kind="playtest.table",
        label="Test table",
        argv=[],
        status="running",
        artifact_path=str(directory),
        process=ResponsiveTable(),
    )
    manager._jobs[job.id] = job  # noqa: SLF001
    monkeypatch.setattr(
        "deepdeck_learner.jobs.subprocess.run",
        lambda *args, **kwargs: pytest.fail("No force-kill expected"),
    )
    assert manager.stop(job.id)["status"] == "stopped"
    assert job.stop_requested is True and job.process.finished is True


def test_batch_stop_request_refuses_paths_outside_the_owned_batch_directory(tmp_path) -> None:
    manager = JobManager(tmp_path)
    unrelated = tmp_path / "0123456789abcdef"
    unrelated.mkdir()
    assert manager._request_batch_stop("playtest.table", str(unrelated)) is False  # noqa: SLF001
    assert list(unrelated.iterdir()) == []


def test_stopped_queued_job_does_not_launch_a_process(tmp_path, monkeypatch) -> None:
    manager = JobManager(tmp_path)
    job = Job(id="queued", kind="playtest.table", label="Queued table", argv=[])
    manager._jobs[job.id] = job  # noqa: SLF001
    monkeypatch.setattr(
        "deepdeck_learner.jobs.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("The queued job was stopped"),
    )
    manager.stop(job.id)
    manager._run(job)  # noqa: SLF001
    assert job.status == "stopped"


@pytest.mark.parametrize("kind", ["dependency.engine.start", "dependency.stack.prepare"])
def test_engine_jobs_use_a_durable_log_instead_of_a_web_owned_pipe(tmp_path, monkeypatch, kind):
    manager = JobManager(tmp_path)
    job = Job(id="engine-log", kind=kind, label="Engine", argv=["engine"])
    calls = []
    monkeypatch.setattr(manager, "_engine_environment", lambda: {})
    monkeypatch.setattr(manager, "_child_environment", lambda: {})

    def durable(received, environment, *, log_path):
        calls.append((received.id, log_path))
        return 0

    monkeypatch.setattr(manager, "_run_durable_training_process", durable)
    monkeypatch.setattr(
        "deepdeck_learner.jobs.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Engine must not use the fallback PIPE launcher"),
    )
    manager._run(job)
    assert calls == [(job.id, tmp_path / ".deepdeck" / "worker-logs" / "engine-log.log")]
    assert job.status == "completed"


def test_source_engine_uses_learner_embedded_pixi_and_configured_loopback(tmp_path, monkeypatch):
    monkeypatch.setattr("deepdeck_learner.jobs.bundled_engine", lambda: None)
    monkeypatch.setattr(
        "deepdeck_learner.jobs.engine_build_environment", lambda: {"LINKER": "rust"}
    )
    monkeypatch.setenv("DEEPDECK_LOCAL_ENGINE_URL", "http://127.0.0.1:8897")
    assert JobManager(tmp_path)._engine_environment() == {
        "LINKER": "rust",
        "MTG_ENGINE_ADDR": "127.0.0.1:8897",
        "MTG_UI_ADDR": "",
    }
    monkeypatch.setenv("DEEPDECK_LOCAL_ENGINE_URL", "https://example.test")
    with pytest.raises(JobValidationError, match="loopback"):
        JobManager(tmp_path)._engine_environment()


def test_smoke_command_is_argv_and_uses_current_python(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    argv, label, artifact = manager._training_command(  # noqa: SLF001
        "training.smoke",
        {"model": "v12", "epochs": 2, "learning_rate": 0.001, "seed": 4},
    )
    assert argv[0] == sys.executable
    assert "--smoke" in argv
    assert argv[argv.index("--device") + 1] == "cuda"
    assert label == "V12 smoke"
    assert artifact is not None and artifact.parent.is_dir() and not artifact.exists()


def test_training_command_generates_a_seed_when_none_is_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_random_seed", lambda: 8675309)

    argv, _, _ = manager._training_command(  # noqa: SLF001
        "training.smoke", {"model": "v12"}
    )

    assert argv[argv.index("--seed") + 1] == "8675309"


def test_v13_world_model_training_uses_its_separate_trainer(tmp_path: Path) -> None:
    config = tmp_path / "configs" / "oracle-ai" / "v13-world-model-smoke.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("training:\n  steps: 1\n", encoding="utf-8")
    manager = JobManager(tmp_path)

    argv, label, artifact = manager._training_command(  # noqa: SLF001
        "training.v13-world-model", {}
    )

    assert argv[1:3] == ["-m", "oracle_ai.training.world_model"]
    assert argv[argv.index("--config") + 1] == str(config)
    assert argv[argv.index("--output") + 1] == str(artifact)
    assert label == "V13 structured world-model smoke"


def test_v13_agent_can_be_configured_without_a_deck_or_api_key(tmp_path: Path) -> None:
    config = tmp_path / "configs" / "oracle-ai" / "v13-world-model-smoke.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "model:\n  latent_dim: 32\ntraining:\n  steps: 2\n",
        encoding="utf-8",
    )
    manager = JobManager(tmp_path)

    model_id = manager.prepare_model(
        {"model": "v13", "model_name": "Graph Pilot", "training_steps": 3}
    )
    argv, label, run = manager._existing_pool_training_command(model_id)  # noqa: SLF001
    metadata = json.loads((run / "local-model.json").read_text(encoding="utf-8"))

    assert metadata["architecture"] == "v13"
    assert metadata["decks"] == []
    assert metadata["reservePlaytest"] is True
    assert argv[1:3] == ["-m", "oracle_ai.training.world_model"]
    assert argv[argv.index("--output") + 1] == str(run)
    assert "V13" in label and "world model" in label

    world_checkpoint = run / "checkpoints" / "step-3" / "world-model.pt"
    world_checkpoint.parent.mkdir(parents=True)
    world_checkpoint.touch()
    argv, label, _ = manager._existing_pool_training_command(model_id)  # noqa: SLF001
    assert argv[1:3] == ["-m", "oracle_ai.training.rl_v13"]
    assert "PPO self-play" in label

    argv, label, _ = manager._existing_pool_training_command(  # noqa: SLF001
        model_id, "world-model"
    )
    assert argv[1:3] == ["-m", "oracle_ai.training.world_model"]
    assert "world model" in label

    (run / "v13-engine-baseline.pt").touch()
    argv, label, _ = manager._existing_pool_training_command(  # noqa: SLF001
        model_id, "engine-evaluation"
    )
    assert argv[1:3] == ["-m", "oracle_ai.training.rl_v13"]
    assert argv[-1] == "--evaluate-only"
    assert "Engine evaluation" in label


def test_v15_agent_publishes_a_generic_sdk_runtime_and_training_command(
    tmp_path: Path,
) -> None:
    config = tmp_path / "configs" / "oracle-ai" / "v15-typed-latent-engine.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "model:\n  latent_dim: 32\ntraining:\n  steps: 2\n  checkpoint_every: 1\n",
        encoding="utf-8",
    )
    manager = JobManager(tmp_path)

    model_id = manager.prepare_model(
        {"model": "v15", "model_name": "Latent Planner", "training_steps": 3}
    )
    run, metadata = find_model_run(tmp_path, model_id)
    argv, label, artifact = manager._existing_pool_training_command(model_id)  # noqa: SLF001

    assert metadata["architecture"] == "v15"
    assert metadata["agentSdkRuntime"] == {
        "module": "deepdeck_examples.run",
        "arguments": ["v15"],
        "checkpointArgument": "--checkpoint",
        "requiredFiles": ["v15-model.pt"],
    }
    assert argv[1:3] == ["-m", "oracle_ai.training.world_model_v15"]
    assert argv[argv.index("--output") + 1] == str(run)
    assert label.endswith("V15 · typed latent world model")
    assert artifact == run


def test_dataset_must_exist(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    with pytest.raises(JobValidationError, match="existing .jsonl"):
        manager._training_command(  # noqa: SLF001
            "training.dataset", {"model": "v11", "dataset": tmp_path / "missing.jsonl"}
        )


def test_playtest_rejects_remote_engine(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    with pytest.raises(JobValidationError, match="loopback"):
        manager._playtest_command(  # noqa: SLF001
            {
                "agent": "v12",
                "model_id": "my-local-ai",
                "checkpoint": str(checkpoint),
                "engine_url": "https://example.com",
                "deck_session_id": "deck-a",
                "opponent_deck_session_id": "deck-b",
            }
        )


def test_playtest_uses_inline_decks_from_the_models_training_pool(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)

    argv, label, artifact = manager._playtest_command(  # noqa: SLF001
        {
            "agent": "v12",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "engine_url": "http://127.0.0.1:8787",
            "format": "legacy",
            "deck_version_id": "pool-deck-1",
            "opponent_deck_version_id": "pool-deck-2",
        }
    )

    setup_path = Path(argv[argv.index("--local-game-setup") + 1])
    setup = json.loads(setup_path.read_text(encoding="utf-8"))
    assert artifact is None
    assert label == "My Local AI · local legacy"
    assert setup["humanPlayerIds"] == ["local-human"]
    assert setup["setup"]["players"][0]["name"] == "Player Pool Deck"
    assert setup["setup"]["players"][1]["name"] == "AI Pool Deck"


def test_v13_playtest_uses_its_rl_checkpoint(tmp_path: Path) -> None:
    checkpoint = local_checkpoint(tmp_path, architecture="v13")
    (checkpoint / "manifest.json").unlink()
    (checkpoint / "checkpoint.pt").unlink()
    (checkpoint / "rl-model.pt").touch()
    manager = JobManager(tmp_path)

    argv, label, _ = manager._playtest_command(  # noqa: SLF001
        {
            "agent": "v13",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "engine_url": "http://127.0.0.1:8787",
            "format": "legacy",
            "deck_version_id": "pool-deck-1",
            "opponent_deck_version_id": "pool-deck-2",
        }
    )

    assert argv[argv.index("-m") + 1 : argv.index("--target")] == [
        "deepdeck_examples.run",
        "v13",
    ]
    assert argv[argv.index("--checkpoint") + 1] == str(checkpoint.resolve())
    assert label.startswith("My Local AI ")
    assert label.endswith(" local legacy")


def test_playtest_uses_deck_added_after_the_last_training_resolution(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    run = checkpoint.parent.parent
    (run / "resolved-config.json").write_text(
        json.dumps({"resolvedLegacyDecks": ["Player Pool Deck"]}),
        encoding="utf-8",
    )

    argv, _, _ = manager._playtest_command(  # noqa: SLF001
        {
            "agent": "v12",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "engine_url": "http://127.0.0.1:8787",
            "format": "legacy",
            "deck_version_id": "pool-deck-2",
            "opponent_deck_version_id": "pool-deck-1",
        }
    )

    setup_path = Path(argv[argv.index("--local-game-setup") + 1])
    setup = json.loads(setup_path.read_text(encoding="utf-8"))
    assert setup["setup"]["players"][0]["name"] == "AI Pool Deck"


def test_playtest_generates_a_fresh_game_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    monkeypatch.setattr(manager, "_random_seed", lambda: 31415926)

    argv, _, _ = manager._playtest_command(  # noqa: SLF001
        {
            "agent": "v12",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "engine_url": "http://127.0.0.1:8787",
            "format": "legacy",
            "deck_version_id": "pool-deck-1",
            "opponent_deck_version_id": "pool-deck-2",
        }
    )

    setup_path = Path(argv[argv.index("--local-game-setup") + 1])
    setup = json.loads(setup_path.read_text(encoding="utf-8"))
    assert setup["seed"] == 31415926


def test_playtest_recompiles_cached_card_rules_with_the_current_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    run = checkpoint.parent.parent
    catalog_path = run / "training-decks.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["Player Pool Deck"] = [
        {
            "id": "sneak-attack",
            "name": "Sneak Attack",
            "typeLine": "Enchantment",
            "manaCost": "{3}{R}",
            "rules": [
                {
                    "kind": "activatedAbility",
                    "effects": [
                        {
                            "kind": "sacrificeAtNextEndStep",
                            "object": {"kind": "chosenTarget", "id": "handCard"},
                        }
                    ],
                }
            ],
            "sourceSessionId": "pool-deck-1",
        }
    ]
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    snapshots = tmp_path / ".deepdeck" / "decks"
    snapshots.mkdir(parents=True)
    (snapshots / "pool-deck-1.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "cardId": "sneak-attack",
                        "name": "Sneak Attack",
                        "typeLine": "Enchantment",
                        "manaCost": "{3}{R}",
                        "oracleText": (
                            "{R}: You may put a creature card from your hand onto the "
                            "battlefield. That creature gains haste. Sacrifice the creature "
                            "at the beginning of the next end step."
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".deepdeck" / "oracle-card-rules.json").write_text(
        json.dumps({"sneak-attack": catalog["Player Pool Deck"][0]["rules"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("deepdeck_learner.card_models.engine_signature", lambda _: "engine-build")
    delayed = {
        "kind": "installDelayedStepTrigger",
        "step": "endStep",
        "trackedObject": {"kind": "chosenTarget", "id": "handCard"},
        "effects": [
            {
                "kind": "sacrificePermanent",
                "permanent": {"kind": "triggeringPermanent"},
            }
        ],
    }
    requests: list[dict[str, object]] = []

    def oracle_rules(url: str, **kwargs: object) -> httpx.Response:
        requests.append(dict(kwargs["json"]))  # type: ignore[arg-type]
        return httpx.Response(
            200,
            json={"rules": [{"kind": "activatedAbility", "effects": [delayed]}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("deepdeck_learner.card_models.httpx.post", oracle_rules)

    argv, _, _ = manager._playtest_command(  # noqa: SLF001
        {
            "agent": "v12",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "engine_url": "http://127.0.0.1:8787",
            "format": "legacy",
            "deck_version_id": "pool-deck-1",
            "opponent_deck_version_id": "pool-deck-2",
        }
    )

    setup_path = Path(argv[argv.index("--local-game-setup") + 1])
    setup = json.loads(setup_path.read_text(encoding="utf-8"))
    rules = setup["setup"]["players"][0]["cards"][0]["rules"]
    assert len(requests) == 1
    assert requests[0]["cardName"] == "Sneak Attack"
    assert rules[0]["effects"][0] == delayed
    cache = json.loads(
        (tmp_path / ".deepdeck" / "oracle-card-rules.json").read_text(encoding="utf-8")
    )
    assert cache["schemaVersion"] == "oracle-card-rules/v2"
    assert cache["engineBuild"] == "engine-build"


def test_playtest_restores_missing_creature_stats_from_scryfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    snapshots = tmp_path / ".deepdeck" / "decks"
    snapshots.mkdir(parents=True)
    (snapshots / "pool-deck-1.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "cardId": "large-creature",
                        "scryfallId": "scryfall-large-creature",
                        "name": "Large Creature",
                        "typeLine": "Legendary Creature — Eldrazi",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    requests: list[dict[str, object]] = []

    def scryfall_collection(url: str, **kwargs: object) -> httpx.Response:
        requests.append(dict(kwargs["json"]))  # type: ignore[arg-type]
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "scryfall-large-creature",
                        "power": "15",
                        "toughness": "15",
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("deepdeck_learner.card_models.httpx.post", scryfall_collection)

    refreshed = manager._refresh_playtest_deck_rules(  # noqa: SLF001
        "http://127.0.0.1:8787",
        "pool-deck-1",
        [
            {
                "id": "large-creature",
                "name": "Large Creature",
                "typeLine": "Legendary Creature — Eldrazi",
                "power": None,
                "toughness": None,
                "rules": [],
            }
        ],
    )

    assert requests == [{"identifiers": [{"id": "scryfall-large-creature"}]}]
    assert refreshed[0]["power"] == "15"
    assert refreshed[0]["toughness"] == "15"
    cache = json.loads(
        (tmp_path / ".deepdeck" / "scryfall-card-characteristics.json").read_text(encoding="utf-8")
    )
    assert cache["cards"]["scryfall-large-creature"] == {
        "power": "15",
        "toughness": "15",
    }


def test_modal_double_faced_card_characteristics_are_refreshed_from_scryfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / ".deepdeck" / "scryfall-card-characteristics.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "schemaVersion": "scryfall-card-characteristics/v1",
                "cards": {"witch": {"power": "2", "toughness": "2"}},
            }
        ),
        encoding="utf-8",
    )

    def scryfall_collection(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "witch",
                        "layout": "modal_dfc",
                        "card_faces": [
                            {
                                "name": "Witch Enchanter",
                                "type_line": "Creature - Human Warlock",
                                "mana_cost": "{3}{W}",
                                "oracle_text": (
                                    "When Witch Enchanter enters, destroy target artifact "
                                    "or enchantment an opponent controls."
                                ),
                                "power": "2",
                                "toughness": "2",
                            },
                            {
                                "name": "Witch-Blessed Meadow",
                                "type_line": "Land",
                                "mana_cost": "",
                                "oracle_text": "Witch-Blessed Meadow enters tapped.\n{T}: Add {W}.",
                            },
                        ],
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("deepdeck_learner.card_models.httpx.post", scryfall_collection)
    [witch] = enrich_card_characteristics(
        tmp_path,
        [
            {
                "cardId": "witch-card",
                "scryfallId": "witch",
                "name": "Witch Enchanter // Witch-Blessed Meadow",
                "typeLine": "Creature - Human Warlock // Land",
                "power": "2",
                "toughness": "2",
                "imageBackUri": "https://example.test/witch-back.jpg",
            }
        ],
    )

    assert witch["layout"] == "modal_dfc"
    assert witch["faces"][0]["name"] == "Witch Enchanter"
    assert witch["faces"][1]["name"] == "Witch-Blessed Meadow"
    assert witch["faces"][1]["typeLine"] == "Land"
    assert oracle_request(witch)["layout"] == "modal_dfc"
    assert len(oracle_request(witch)["faces"]) == 2


def test_transform_planeswalker_face_keeps_starting_loyalty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def scryfall_collection(url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "tamiyo",
                        "layout": "transform",
                        "card_faces": [
                            {
                                "name": "Tamiyo, Inquisitive Student",
                                "type_line": "Legendary Creature - Moonfolk Wizard",
                                "mana_cost": "{U}",
                                "oracle_text": (
                                    "When you draw your third card in a turn, transform Tamiyo."
                                ),
                                "power": "0",
                                "toughness": "3",
                            },
                            {
                                "name": "Tamiyo, Seasoned Scholar",
                                "type_line": "Legendary Planeswalker - Tamiyo",
                                "mana_cost": "",
                                "oracle_text": (
                                    "+2: Until your next turn, attacking creatures get -1/-0."
                                ),
                                "loyalty": "2",
                            },
                        ],
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("deepdeck_learner.card_models.httpx.post", scryfall_collection)
    [tamiyo] = enrich_card_characteristics(
        tmp_path,
        [
            {
                "cardId": "tamiyo-card",
                "scryfallId": "tamiyo",
                "name": "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar",
                "typeLine": (
                    "Legendary Creature - Moonfolk Wizard // Legendary Planeswalker - Tamiyo"
                ),
                "imageBackUri": "https://example.test/tamiyo-back.jpg",
            }
        ],
    )

    assert tamiyo["faces"][1]["loyalty"] == "2"
    assert oracle_request(tamiyo)["faces"][1]["loyalty"] == "2"


def test_compiled_transform_faces_keep_planeswalker_loyalty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("deepdeck_learner.card_models.engine_signature", lambda _: None)

    def oracle_rules(url: str, **kwargs: object) -> httpx.Response:
        request = kwargs["json"]
        assert isinstance(request, dict)
        faces = request["faces"]
        assert isinstance(faces, list)
        compiled_faces = [
            {key: value for key, value in face.items() if key != "loyalty"} for face in faces
        ]
        return httpx.Response(
            200,
            json={"rules": [{"kind": "rulesMarker", "transformFaces": compiled_faces}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("deepdeck_learner.card_models.httpx.post", oracle_rules)
    cards = [
        {
            "id": "tamiyo",
            "name": "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar",
            "typeLine": "Legendary Creature // Legendary Planeswalker",
            "layout": "transform",
            "faces": [
                {
                    "id": "tamiyo-front",
                    "name": "Tamiyo, Inquisitive Student",
                    "typeLine": "Legendary Creature",
                    "oracleText": "When you draw your third card, transform Tamiyo.",
                },
                {
                    "id": "tamiyo-back",
                    "name": "Tamiyo, Seasoned Scholar",
                    "typeLine": "Legendary Planeswalker",
                    "oracleText": "+2: Draw a card.",
                    "loyalty": "2",
                },
            ],
        }
    ]

    rules = compile_oracle_rules(tmp_path, "http://127.0.0.1:8787", cards)

    assert rules["tamiyo"][0]["transformFaces"][1]["loyalty"] == "2"


def test_playtest_resolves_player_random_deck_before_weighting_ai_deck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    seeded = random.Random(7)
    monkeypatch.setattr("deepdeck_learner.jobs.random.SystemRandom", lambda: seeded)

    raw = {
        "agent": "v12",
        "model_id": "my-local-ai",
        "checkpoint": str(checkpoint),
        "engine_url": "http://127.0.0.1:8787",
        "format": "legacy",
        "deck_version_id": "random",
        "opponent_deck_version_id": "random",
    }
    manager._playtest_command(raw)  # noqa: SLF001

    details = raw["_job_details"]
    assert details["selectionOrder"] == "player-then-rating-proximity"
    assert details["playerDeck"]["id"] in {"pool-deck-1", "pool-deck-2"}
    assert details["opponentDeck"]["id"] in {"pool-deck-1", "pool-deck-2"}
    assert details["playerDeck"]["id"] != details["opponentDeck"]["id"]


def test_matchmaking_uses_selected_catalog_values_without_exposing_ids_in_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_test")
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    argv, label, artifact = manager._matchmaking_command(  # noqa: SLF001
        {
            "agent": "v12",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "speed": "1s",
            "competition_version_id": "competition-version",
            "deck_version_id": "deck-version",
            "continuous": False,
        }
    )
    assert label == "My Local AI · Deep Deck League"
    assert artifact is None
    assert argv[argv.index("--competition-version-id") + 1] == "competition-version"
    assert argv[argv.index("--deck-version-id") + 1] == "deck-version"
    assert argv[argv.index("--checkpoint") + 1] == str(checkpoint)
    assert "--once" in argv
    assert manager._child_environment()["DEEPDECK_API_KEY"] == "ddl_agent_test"  # noqa: SLF001
    sdk_source = manager._child_environment()["PYTHONPATH"].split(os.pathsep)[0]  # noqa: SLF001
    assert Path(sdk_source) == (tmp_path / "external" / "deepdeck-agent" / "src").resolve()


@pytest.mark.parametrize(
    "policy,flag,value",
    [
        ({"mode": "matches", "matches": 5}, "--league-max-matches", "5"),
        ({"mode": "duration", "minutes": 2}, "--league-duration-seconds", "120"),
    ],
)
def test_matchmaking_passes_finite_limits_to_the_actual_worker(
    tmp_path, monkeypatch, policy, flag, value
):
    monkeypatch.setenv("DEEPDECK_API_KEY", "fixture-key")
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    argv, _, _ = manager._matchmaking_command(
        {
            "agent": "v12",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "competition_version_id": "competition",
            "deck_version_id": "deck",
            "connections": 2,
            "league_run": policy,
        }
    )
    assert argv[argv.index(flag) + 1] == value
    assert "--once" not in argv
    assert argv[argv.index("--matchmaking-concurrency") + 1] == "2"


def test_bad_league_run_policy_is_rejected_before_account_or_model_lookup(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    with pytest.raises(JobValidationError, match="whole number"):
        JobManager(tmp_path)._matchmaking_command(
            {"league_run": {"mode": "matches", "matches": True}}
        )


def test_rotation_turn_contract_uses_exact_identity_private_paths_and_one_timed_attempt(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPDECK_API_KEY", "fixture-key")
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    turn = {
        "queueId": "a" * 32,
        "entryId": "b" * 32,
        "dispatchId": "c" * 32,
        "sequence": 12,
        "remainingSeconds": 40,
    }
    payload = {
        "agent": "v12",
        "model_id": "my-local-ai",
        "checkpoint": str(checkpoint),
        "competition_version_id": "league",
        "deck_version_id": "deck",
        "league_queue_turn": turn,
        "continuous": True,
    }
    argv = manager._matchmaking_command(payload)[0]
    assert argv[argv.index("--league-run-id") + 1] == "c" * 32
    assert "--once" in argv and argv[argv.index("--matchmaking-concurrency") + 1] == "1"
    assert argv[argv.index("--league-duration-seconds") + 1] == "40"
    assert Path(
        argv[argv.index("--league-stop-file") + 1]
    ) == tmp_path / ".deepdeck" / "league-queues" / ("a" * 32) / "controls" / ("b" * 32 + ".json")
    assert Path(argv[argv.index("--league-result-file") + 1]).name == "result.json"
    assert payload["_job_details"]["leagueQueueDispatchId"] == "c" * 32
    with pytest.raises(JobValidationError):
        manager._matchmaking_command({**payload, "connections": 2})
    with pytest.raises(JobValidationError):
        manager._matchmaking_command(
            {**payload, "league_queue_turn": {**turn, "queueId": "../outside"}}
        )


def test_each_builtin_league_execution_has_a_distinct_recoverable_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPDECK_API_KEY", "fixture-key")
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    payload = {
        "agent": "v12",
        "model_id": "my-local-ai",
        "checkpoint": str(checkpoint),
        "competition_version_id": "competition",
        "deck_version_id": "deck",
    }
    first = manager._matchmaking_command(payload)[0]
    second = manager._matchmaking_command(payload)[0]
    first_id, second_id = (args[args.index("--league-run-id") + 1] for args in (first, second))
    assert len(first_id) == 32 and len(second_id) == 32 and first_id != second_id


def test_recovery_does_not_attach_an_old_job_to_a_new_run_of_the_same_agent():
    old_id, new_id = "a" * 32, "b" * 32
    shared = ["python", "-m", "deepdeck_examples.run", "--agent-config", "same-agent"]
    job = {
        "id": "old",
        "kind": "matchmaking.agent",
        "status": "running",
        "argv": [*shared, "--league-run-id", old_id],
    }
    command = " ".join([*shared, "--league-run-id", new_id])
    assert JobManager._reconcile_persisted_job(job, [command])["status"] == "stopped"
    assert JobManager._reconcile_persisted_job(job, [" ".join(job["argv"])])["status"] == "running"
    assert (
        JobManager._reconcile_persisted_job(job, [" ".join(job["argv"]) + "-different"])["status"]
        == "stopped"
    )
    legacy = {**job, "argv": shared}
    assert JobManager._reconcile_persisted_job(legacy, [command])["status"] == "stopped"


def test_exact_job_lookup_is_not_limited_to_the_recent_dashboard_window(tmp_path):
    manager = JobManager(tmp_path)
    manager._persist(
        Job(
            id="old-result",
            kind="matchmaking.agent",
            label="Old result",
            argv=[],
            status="completed",
            created_at="2020-01-01T00:00:00Z",
        )
    )
    for index in range(100):
        manager._persist(
            Job(
                id=f"new-{index}",
                kind="matchmaking.agent",
                label="New result",
                argv=[],
                status="completed",
                created_at="2026-01-01T00:00:00Z",
            )
        )
    assert all(job["id"] != "old-result" for job in manager.list_jobs())
    assert manager.get("old-result")["label"] == "Old result"
    assert manager.get("missing-job") is None
    assert manager.stop("old-result")["status"] == "completed"


@pytest.mark.parametrize("same_execution", [False, True])
def test_detached_stop_verifies_the_exact_execution_before_terminating(
    tmp_path, monkeypatch, same_execution
):
    manager = JobManager(tmp_path)
    old_id = "a" * 32
    persisted = {
        "id": "old",
        "kind": "matchmaking.agent",
        "status": "running",
        "model_id": "pilot",
        "argv": ["python", "-m", "deepdeck_examples.run", "--league-run-id", old_id],
    }
    monkeypatch.setattr(manager, "get", lambda _: persisted)
    monkeypatch.setattr(
        manager,
        "resources",
        lambda: {
            "workers": [
                {
                    "jobId": "recovered-42",
                    "modelId": "pilot",
                    "kind": "matchmaking.agent",
                    "pids": [42],
                }
            ]
        },
    )
    terminated = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return [
                "python",
                "-m",
                "deepdeck_examples.run",
                "--league-run-id",
                old_id if same_execution else "b" * 32,
            ]

        def terminate(self):
            terminated.append(self.pid)

        def kill(self):
            raise AssertionError("Unnecessary force kill")

    monkeypatch.setattr("deepdeck_learner.jobs.psutil.Process", Process)
    monkeypatch.setattr(
        "deepdeck_learner.jobs.psutil.wait_procs", lambda values, timeout: (values, [])
    )
    result = manager.stop("old")
    assert terminated == ([42] if same_execution else [])
    assert (result is not None) == same_execution


def test_matchmaking_coerces_unsupported_deep_learning_speed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_test")
    manager = JobManager(tmp_path)
    checkpoint = local_checkpoint(tmp_path)
    argv, _, _ = manager._matchmaking_command(  # noqa: SLF001
        {
            "agent": "v12",
            "model_id": "my-local-ai",
            "checkpoint": str(checkpoint),
            "speed": "100ms",
            "connections": 2,
            "competition_version_id": "competition-version",
            "deck_version_ids": ["deck-version-a", "deck-version-b"],
        }
    )

    assert argv[argv.index("--speed") + 1] == "1s"
    assert argv[argv.index("--matchmaking-concurrency") + 1] == "2"
    assert argv[argv.index("--deck-version-id") + 1] == "deck-version-a"
    assert argv[argv.index("--additional-deck-version-id") + 1] == "deck-version-b"


def test_matchmaking_requires_an_account_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    manager = JobManager(tmp_path)
    with pytest.raises(JobValidationError, match="Settings"):
        manager._matchmaking_command(  # noqa: SLF001
            {"competition_version_id": "competition", "deck_version_id": "deck"}
        )


def test_dependency_commands_are_allowlisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "external" / "deepdeck-engine" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[package]\nname='test'\n", encoding="utf-8")
    monkeypatch.setattr("deepdeck_learner.jobs.shutil.which", lambda executable: "cargo")
    monkeypatch.setattr("deepdeck_learner.jobs.current_revision", lambda root, name: "abc")
    monkeypatch.setattr("deepdeck_learner.jobs.target_revision", lambda root, name: "abc")
    manager = JobManager(tmp_path)

    engine, engine_label, _ = manager._dependency_command(  # noqa: SLF001
        "dependency.engine.start", {}
    )
    pixi, pixi_label, _ = manager._dependency_command(  # noqa: SLF001
        "dependency.pixi.prepare", {}
    )
    sync, sync_label, _ = manager._dependency_command(  # noqa: SLF001
        "dependency.sync", {"dependency": "pixi"}
    )
    stack, stack_label, _ = manager._dependency_command(  # noqa: SLF001
        "dependency.stack.prepare", {}
    )

    assert engine[:2] == ["cargo", "run"]
    assert "--release" in engine
    assert engine_label == "DeepDeckEngine local server"
    assert pixi[2:5] == ["deepdeck_learner.dependencies", "prepare-pixi", "--root"]
    assert pixi_label == "Prepare DeepDeckPixi"
    assert sync[-2:] == ["--dependency", "pixi"]
    assert sync_label == "Sync DeepDeckPixi"
    assert stack[2:5] == ["deepdeck_learner.dependencies", "bootstrap", "--root"]
    assert stack_label == "Local Engine + Pixi setup"


def test_job_history_is_persisted_in_local_sqlite(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    created = manager.create(
        {"kind": "training.smoke", "model": "v12", "epochs": 1, "device": "cpu"}
    )
    assert created["id"]

    restored = JobManager(tmp_path).list_jobs()

    assert any(job["id"] == created["id"] for job in restored)
    assert (tmp_path / ".deepdeck" / "learner.db").is_file()


def test_player_history_filters_training_before_the_recent_limit(tmp_path):
    manager = JobManager(tmp_path)
    for index in range(101):
        manager._persist(
            Job(
                id=f"training-{index}",
                kind="training.pool",
                label="Advanced",
                argv=[],
                status="completed",
                created_at="2026-01-01T00:00:00Z",
            )
        )
    game = Job(
        id="player-game",
        kind="playtest.batch",
        label="Player game",
        argv=[],
        status="completed",
        created_at="2020-01-01T00:00:00Z",
    )
    manager._persist(game)
    manager._jobs["live-trainer"] = Job(
        id="live-trainer", kind="training.pool", label="Trainer", argv=[], status="running"
    )
    assert [job["id"] for job in manager.list_jobs(player_only=True)] == ["player-game"]
    assert "live-trainer" in {job["id"] for job in manager.list_jobs()}
    assert "player-game" not in {job["id"] for job in manager.list_jobs()}
    assert JobManager(tmp_path).list_jobs(player_only=True)[0]["id"] == "player-game"


def test_restart_does_not_reopen_a_detached_playtest_overlay() -> None:
    reconciled = JobManager._reconcile_persisted_job(  # noqa: SLF001
        {
            "id": "old-playtest",
            "kind": "playtest.agent",
            "status": "running",
            "finished_at": None,
            "logs": ["local game started"],
        }
    )

    assert reconciled["status"] == "stopped"
    assert reconciled["finished_at"]
    assert "no longer attached" in reconciled["logs"][-1]


def test_restart_keeps_a_playtest_visible_when_its_agent_process_is_alive() -> None:
    setup = "D:/workspace/.deepdeck/playtests/live-game.json"
    reconciled = JobManager._reconcile_persisted_job(  # noqa: SLF001
        {
            "id": "live-playtest",
            "kind": "playtest.agent",
            "status": "running",
            "finished_at": None,
            "argv": [
                "python",
                "-m",
                "deepdeck_examples.run",
                "v12",
                "--start-local-game",
                "--local-game-setup",
                setup,
            ],
            "details": {"sessionId": "game-session:6"},
        },
        [
            "python -m deepdeck_examples.run v12 --start-local-game "
            f"--local-game-setup {setup.casefold()}"
        ],
    )

    assert reconciled["status"] == "running"
    assert reconciled["details"]["sessionId"] == "game-session:6"


def test_pool_training_builds_local_catalog_and_parallel_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version_id = "deck-version-1"
    pool_dir = tmp_path / ".deepdeck"
    deck_dir = pool_dir / "decks"
    config_dir = tmp_path / "configs" / "oracle-ai"
    deck_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (pool_dir / "training-deck-pool.json").write_text(
        json.dumps(
            {
                "decks": [
                    {
                        "id": version_id,
                        "name": "Test Legacy",
                        "version": 2,
                        "format": "legacy",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (deck_dir / f"{version_id}.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "cardId": "island",
                        "name": "Island",
                        "typeLine": "Basic Land — Island",
                        "quantity": 2,
                        "section": "main",
                        "rules": [],
                    },
                    {
                        "cardId": "negate",
                        "name": "Negate",
                        "typeLine": "Instant",
                        "quantity": 1,
                        "section": "sideboard",
                        "rules": [],
                    },
                    {
                        "cardId": "zombie-token",
                        "name": "Zombie",
                        "typeLine": "Token Creature — Zombie",
                        "quantity": 1,
                        "section": "sideboard",
                        "rules": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "league-v12-legacy.yaml").write_text(
        (
            "seed: 1\ntrainingSeed: 2\ndeckSource: database\n"
            "outputDir: old\nparallelGameWorkers: 1\nevaluation:\n  seed: 3\n"
        ),
        encoding="utf-8",
    )

    manager = JobManager(tmp_path)
    generated_seeds = iter((101, 202, 303))
    monkeypatch.setattr(manager, "_random_seed", lambda: next(generated_seeds))
    argv, label, run = manager._training_command(  # noqa: SLF001
        "training.pool",
        {
            "model": "v12",
            "model_name": "Test Pilot",
            "parallel_matches": 3,
            "reserve_playtest": True,
        },
    )

    config = yaml.safe_load((run / "training-config.yaml").read_text(encoding="utf-8"))
    catalog = json.loads((run / "training-decks.json").read_text(encoding="utf-8"))
    cards = next(iter(catalog.values()))
    assert argv[:3] == [sys.executable, "-m", "oracle_ai.training.league"]
    assert label == "Test Pilot · V12 · 1 deck"
    assert config["parallelGameWorkers"] == 3
    assert config["rolloutBatchGames"] == 3
    assert config["maxCheckpoints"] == 2
    assert config["continuous"] is True
    assert config["seed"] == 101
    assert config["trainingSeed"] == 202
    assert config["evaluation"]["seed"] == 303
    assert config["learnerSettings"]["reservePlaytest"] is True
    assert config["learnerSettings"]["modelName"] == "Test Pilot"
    assert "deckSource" not in config
    assert len(cards) == 4
    assert cards[2]["isSideboard"] is True
    assert cards[-1]["isToken"] is True
    assert cards[-1]["isGamePiece"] is True


def test_existing_training_catalog_repairs_unmarked_tokens(tmp_path: Path) -> None:
    checkpoint = local_checkpoint(tmp_path)
    run = checkpoint.parent.parent
    (run / "training-config.yaml").write_text("parallelGameWorkers: 1\n", encoding="utf-8")
    catalog_path = run / "training-decks.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["Player Pool Deck"].append(
        {
            "id": "zombie-token",
            "name": "Zombie",
            "typeLine": "Token Creature — Zombie",
            "isSideboard": True,
            "sourceSessionId": "pool-deck-1",
        }
    )
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    JobManager(tmp_path)._existing_pool_training_command("my-local-ai")  # noqa: SLF001

    repaired = json.loads(catalog_path.read_text(encoding="utf-8"))
    token = repaired["Player Pool Deck"][-1]
    assert token["isToken"] is True
    assert token["isGamePiece"] is True
    config = yaml.safe_load((run / "training-config.yaml").read_text(encoding="utf-8"))
    assert config["resumeOptimizer"] is True


def test_existing_agent_can_update_its_name_modes_and_deck_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = local_checkpoint(tmp_path)
    run = checkpoint.parent.parent
    metadata_path = run / "local-model.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(
        {
            "format": "legacy",
            "reservePlaytest": True,
            "selfPlayAllSeats": True,
            "source": "user-trained",
        }
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    (run / "training-config.yaml").write_text(
        yaml.safe_dump(
            {
                "engineUrl": "http://127.0.0.1:8787",
                "learnerSettings": {"modelId": "my-local-ai"},
            }
        ),
        encoding="utf-8",
    )
    deck_dir = tmp_path / ".deepdeck" / "decks"
    deck_dir.mkdir(parents=True)
    (deck_dir / "new-deck.json").write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "cardId": "new-island",
                        "name": "Island",
                        "typeLine": "Basic Land — Island",
                        "quantity": 60,
                        "section": "main",
                        "rules": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_compile_oracle_rules", lambda *_: {})

    updated_id = manager.update_model(
        "my-local-ai",
        {
            "name": "Edited Local AI",
            "decks": [
                {
                    "id": "new-deck",
                    "name": "New Legacy Deck",
                    "version": 3,
                    "format": "legacy",
                }
            ],
            "reservePlaytest": False,
            "selfPlayAllSeats": False,
        },
    )

    updated = json.loads(metadata_path.read_text(encoding="utf-8"))
    config = yaml.safe_load((run / "training-config.yaml").read_text(encoding="utf-8"))
    catalog = json.loads((run / "training-decks.json").read_text(encoding="utf-8"))
    assert updated_id == "my-local-ai"
    assert checkpoint.is_dir()
    assert updated["name"] == "Edited Local AI"
    assert updated["decks"][0]["id"] == "new-deck"
    assert updated["reservePlaytest"] is False
    assert updated["selfPlayAllSeats"] is False
    assert config["learnerSettings"]["selectedDeckVersionIds"] == ["new-deck"]
    assert config["trainingOpponentMix"] == {"self": 0.5, "anchor": 0.5}
    assert len(next(iter(catalog.values()))) == 60
