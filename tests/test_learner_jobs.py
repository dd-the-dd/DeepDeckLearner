from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest
import yaml

from deepdeck_learner.jobs import JobManager, JobValidationError, is_loopback_url


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


def test_matchmaking_requires_an_account_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    manager = JobManager(tmp_path)
    with pytest.raises(JobValidationError, match="project .env"):
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
    monkeypatch.setattr("deepdeck_learner.jobs.pinned_revision", lambda root, name: "abc")
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


def test_pool_training_builds_local_catalog_and_parallel_config(tmp_path: Path) -> None:
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
                ]
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "league-v12-legacy.yaml").write_text(
        "deckSource: database\noutputDir: old\nparallelGameWorkers: 1\n",
        encoding="utf-8",
    )

    manager = JobManager(tmp_path)
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
    assert config["continuous"] is True
    assert config["learnerSettings"]["reservePlaytest"] is True
    assert config["learnerSettings"]["modelName"] == "Test Pilot"
    assert "deckSource" not in config
    assert len(cards) == 3
    assert cards[-1]["isSideboard"] is True
