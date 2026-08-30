from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from deepdeck_learner import app as learner_app
from deepdeck_learner.app import create_app


def test_health_status_and_protected_job_start(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    status = client.get("/api/v1/status").json()
    assert status["controller"]["ready"] is True
    assert status["hosted"]["trajectory_training"] is False
    forbidden = client.post("/api/v1/jobs", json={"kind": "training.smoke"})
    assert forbidden.status_code == 403
    token = client.get("/api/v1/session").json()["token"]
    invalid = client.post(
        "/api/v1/jobs",
        headers={"X-DeepDeck-Token": token},
        json={"kind": "unknown"},
    )
    assert invalid.status_code == 422


def test_catalog_routes_keep_deck_identifiers_behind_named_results(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_test")
    monkeypatch.setattr(
        learner_app,
        "platform_decks",
        lambda search, game_format, page: {
            "items": [{"id": "deck-version", "name": "Reanimator", "format": game_format}],
            "pagination": {"page": page},
        },
    )
    client = TestClient(create_app(tmp_path))
    response = client.get("/api/v1/catalog/decks?search=reanimator&format=legacy")
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Reanimator"


def test_platform_catalog_requires_account_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    client = TestClient(create_app(tmp_path))

    assert client.get("/api/v1/catalog/decks?format=legacy").status_code == 401
    assert client.get("/api/v1/catalog/competitions").status_code == 401


def test_api_key_can_be_saved_locally_without_being_returned(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    client = TestClient(create_app(tmp_path))
    token = client.get("/api/v1/session").json()["token"]
    key = "ddl_agent_complete_local_test_key"
    try:
        response = client.post(
            "/api/v1/settings/api-key",
            headers={"X-DeepDeck-Token": token},
            json={"api_key": key},
        )
        assert response.status_code == 200
        assert response.json() == {"configured": True}
        assert key not in response.text
        assert client.get("/api/v1/status").json()["hosted"]["api_key_configured"]
        stored = (tmp_path / ".deepdeck" / "secrets.json").read_text("utf-8")
        assert key in stored
    finally:
        os.environ.pop("DEEPDECK_API_KEY", None)

