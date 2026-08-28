from __future__ import annotations

import sys
from pathlib import Path

import pytest

from deepdeck_learner.jobs import JobManager, JobValidationError, is_loopback_url


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
    with pytest.raises(JobValidationError, match="loopback"):
        manager._playtest_command(  # noqa: SLF001
            {
                "agent": "random",
                "engine_url": "https://example.com",
                "deck_session_id": "deck-a",
                "opponent_deck_session_id": "deck-b",
            }
        )


def test_matchmaking_uses_selected_catalog_values_without_exposing_ids_in_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_test")
    manager = JobManager(tmp_path)
    argv, label, artifact = manager._matchmaking_command(  # noqa: SLF001
        {
            "agent": "random",
            "speed": "1s",
            "competition_version_id": "competition-version",
            "deck_version_id": "deck-version",
            "continuous": False,
        }
    )
    assert label == "RANDOM Deep Deck League"
    assert artifact is None
    assert argv[argv.index("--competition-version-id") + 1] == "competition-version"
    assert argv[argv.index("--deck-version-id") + 1] == "deck-version"
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
