from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
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


def test_local_deck_presentation_keeps_normal_token_and_double_faced_art(
    tmp_path: Path,
) -> None:
    deck_dir = tmp_path / ".deepdeck" / "decks"
    deck_dir.mkdir(parents=True)
    (deck_dir / "deck-version.json").write_text(
        json.dumps(
            {
                "name": "Lands",
                "cards": [
                    {
                        "cardId": "dark-depths-id",
                        "imageUri": "https://cards.scryfall.io/front/dark-depths.jpg",
                        "name": "Dark Depths",
                        "quantity": 4,
                        "typeLine": "Legendary Snow Land",
                    },
                    {
                        "cardId": "marit-lage-id",
                        "imageUri": "https://cards.scryfall.io/front/marit-lage.jpg",
                        "name": "Marit Lage",
                        "quantity": 1,
                        "typeLine": "Token Legendary Creature — Avatar",
                    },
                    {
                        "cardId": "dfc-id",
                        "imageBackUri": "https://cards.scryfall.io/back/land.jpg",
                        "imageUri": "https://cards.scryfall.io/front/spell.jpg",
                        "name": "Witch Enchanter // Witch-Blessed Meadow",
                        "quantity": 2,
                        "typeLine": "Creature — Human Warlock // Land",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    response = TestClient(create_app(tmp_path)).get(
        "/api/v1/catalog/decks/deck-version/presentation"
    )

    assert response.status_code == 200
    cards = response.json()["cards"]
    assert cards[0]["imageUrl"].endswith("dark-depths.jpg")
    assert cards[1]["isToken"] is True
    assert cards[1]["name"] == "Marit Lage"
    assert cards[2]["urlBack"].endswith("land.jpg")
    assert [face["name"] for face in cards[2]["faces"]] == [
        "Witch Enchanter",
        "Witch-Blessed Meadow",
    ]


def test_scryfall_image_proxy_returns_cacheable_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        learner_app,
        "scryfall_image",
        lambda image_path: (f"image:{image_path}".encode(), "image/jpeg"),
    )

    response = TestClient(create_app(tmp_path)).get(
        "/api/scryfall-images/border_crop/front/a/b/card.jpg?revision"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "public, max-age=604800, immutable"
    assert response.content == b"image:border_crop/front/a/b/card.jpg"


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


def test_models_route_only_exposes_user_owned_local_model_metadata(tmp_path: Path) -> None:
    run = tmp_path / ".deepdeck" / "runs" / "my-model"
    checkpoint = run / "live" / "my-model-id"
    checkpoint.mkdir(parents=True)
    (checkpoint / "manifest.json").write_text("{}", encoding="utf-8")
    (checkpoint / "checkpoint.pt").touch()
    (run / "local-model.json").write_text(
        json.dumps(
            {
                "schemaVersion": "local-model/v1",
                "id": "my-model-id",
                "name": "My Model",
                "architecture": "v12",
                "format": "legacy",
                "description": "User-owned weights",
                "createdAt": "2026-08-30T00:00:00+00:00",
                "checkpointPath": str(checkpoint),
                "reservePlaytest": True,
            }
        ),
        encoding="utf-8",
    )

    response = TestClient(create_app(tmp_path)).get("/api/v1/models")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item.pop("diskBytes") > 0
    assert item.pop("weightsBytes") == 2
    assert item.pop("trainingState") == {}
    assert item == {
            "schemaVersion": "local-model/v1",
            "id": "my-model-id",
            "name": "My Model",
            "architecture": "v12",
            "format": "legacy",
            "description": "User-owned weights",
            "createdAt": "2026-08-30T00:00:00+00:00",
            "checkpointPath": str(checkpoint),
            "reservePlaytest": True,
            "runPath": str(run.resolve()),
            "status": "stopped",
            "ready": True,
            "decks": [],
    }


def test_model_resources_and_deck_statistics_are_local_and_persistent(tmp_path: Path) -> None:
    run = tmp_path / ".deepdeck" / "runs" / "rated-model"
    run.mkdir(parents=True)
    (run / "local-model.json").write_text(
        json.dumps(
            {
                "schemaVersion": "local-model/v1",
                "id": "rated-model-id",
                "name": "Rated Model",
                "architecture": "v12",
                "format": "legacy",
                "checkpointPath": str(run / "live" / "rated-model-id"),
                "decks": [
                    {
                        "id": "deck-version-12345678",
                        "name": "Reanimator",
                        "format": "legacy",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run / "training-leaderboard.json").write_text(
        json.dumps(
            {
                "ratingSystem": "plackett-luce",
                "deckParticipants": [
                    {
                        "participantId": "rated-model-id",
                        "deckName": "Reanimator Â· v1 Â· deck-ver",
                        "mu": 28.0,
                        "sigma": 5.0,
                        "ordinal": 13.0,
                        "rank": 2,
                        "games": 12,
                        "gameWins": 8,
                        "gameLosses": 4,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(tmp_path))
    token = client.get("/api/v1/session").json()["token"]

    saved = client.put(
        "/api/v1/models/rated-model-id/resources",
        headers={"X-DeepDeck-Token": token},
        json={
            "trainingMatches": 3,
            "leagueMatches": 2,
            "localMatches": 1,
            "gpuMemoryMb": 4096,
        },
    )

    assert saved.status_code == 200
    assert client.get("/api/v1/models/rated-model-id/resources").json() == saved.json()
    assert json.loads((run / "training-control.json").read_text("utf-8")) == {
        "desiredState": "running"
    }
    statistic = client.get("/api/v1/statistics/decks").json()["items"][0]
    assert statistic["ratingSystem"] == "plackett-luce"
    assert statistic["ordinal"] == 13.0
    assert statistic["winRate"] == pytest.approx(2 / 3)
    snapshot = client.get("/api/v1/resources").json()
    assert snapshot["system"]["ramTotalBytes"] > 0
    assert snapshot["engine"]["activeLocalGames"] == 0

