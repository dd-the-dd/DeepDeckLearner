from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from deepdeck_learner import deck_workshop
from deepdeck_learner.agent_studio import create_agent, list_agents
from deepdeck_learner.app import create_app


def _scryfall(name: str) -> dict[str, object]:
    return {
        "id": f"printing-{name.casefold().replace(' ', '-')}",
        "oracle_id": f"oracle-{name.casefold().replace(' ', '-')}",
        "name": name,
        "type_line": "Basic Land — Island" if name == "Island" else "Instant",
        "oracle_text": "Draw a card.",
        "mana_cost": "{U}",
        "cmc": 1,
        "colors": ["U"],
        "color_identity": ["U"],
        "keywords": [],
        "layout": "normal",
        "image_uris": {"normal": f"https://cards.example/{name}.jpg"},
    }


def test_local_deck_is_materialized_for_agents_and_play(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(deck_workshop, "_scryfall_card", _scryfall)

    deck = deck_workshop.create_deck(
        tmp_path,
        {
            "name": "Local Tempo",
            "format": "legacy",
            "cards": [
                {"name": "Island", "quantity": 4, "section": "main", "tags": ["land"]},
                {"name": "Brainstorm", "quantity": 4, "section": "main"},
            ],
        },
    )

    assert deck["id"].startswith("localdeck-")
    assert len(deck_workshop.list_decks(tmp_path)) == 1
    snapshot_path = tmp_path / ".deepdeck" / "decks" / f"{deck['id']}.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["cardCount"] == 8
    assert snapshot["colors"] == ["U"]
    assert snapshot["cards"][0]["scryfallId"].startswith("printing-")

    updated = deck_workshop.update_deck(
        tmp_path,
        deck["id"],
        {
            "description": "A fast blue experiment.",
            "cards": [
                {**deck["cards"][0], "quantity": 8},
                deck["cards"][1],
            ],
        },
    )
    assert updated["version"] == 2
    assert updated["cards"][0]["quantity"] == 8


def test_downloaded_deck_can_be_forked_without_editing_server_snapshot(tmp_path: Path) -> None:
    source_path = tmp_path / ".deepdeck" / "decks" / "league-v7.json"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        json.dumps(
            {
                "id": "league-v7",
                "name": "League Delver",
                "version": 7,
                "format": "legacy",
                "cards": [
                    {
                        "name": "Island",
                        "quantity": 60,
                        "section": "main",
                        "scryfallId": "island",
                        "cardId": "island",
                        "colorIdentity": ["U"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    fork = deck_workshop.fork_downloaded_deck(tmp_path, "league-v7")
    assert fork["id"] != "league-v7"
    assert fork["origin"] == {"kind": "league", "versionId": "league-v7"}
    assert json.loads(source_path.read_text(encoding="utf-8"))["version"] == 7


def test_workshop_routes_require_local_session_for_mutations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(deck_workshop, "_scryfall_card", _scryfall)
    client = TestClient(create_app(tmp_path))

    assert client.post("/api/v1/deck-workshop/decks", json={"name": "No token"}).status_code == 403
    token = client.get("/api/v1/session").json()["token"]
    created = client.post(
        "/api/v1/deck-workshop/decks",
        headers={"X-DeepDeck-Token": token},
        json={"name": "API Deck", "format": "legacy"},
    )
    assert created.status_code == 201
    deck_id = created.json()["id"]
    assert client.get(f"/api/v1/deck-workshop/decks/{deck_id}").status_code == 200
    assert client.get("/api/v1/deck-workshop/decks").json()["items"][0]["name"] == "API Deck"

    removed = client.delete(
        f"/api/v1/deck-workshop/decks/{deck_id}",
        headers={"X-DeepDeck-Token": token},
    )
    assert removed.json()["deleted"] is True


def test_deleting_an_attached_deck_is_blocked_and_missing_files_do_not_hide_agent(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(deck_workshop, "_scryfall_card", _scryfall)
    deck = deck_workshop.create_deck(
        tmp_path,
        {"name": "Attached", "format": "legacy", "cards": [{"name": "Island", "quantity": 60}]},
    )
    policy = create_agent(
        tmp_path, {"name": "Still visible", "format": "legacy", "deckVersionIds": [deck["id"]]}
    )
    client = TestClient(create_app(tmp_path))
    token = client.get("/api/v1/session").json()["token"]
    response = client.delete(
        f"/api/v1/deck-workshop/decks/{deck['id']}", headers={"X-DeepDeck-Token": token}
    )
    assert response.status_code == 409
    assert "Still visible" in response.json()["detail"]
    assert deck_workshop.find_deck(tmp_path, deck["id"])
    snapshot = tmp_path / ".deepdeck" / "decks" / f"{deck['id']}.json"
    snapshot.unlink()
    visible = list_agents(tmp_path)
    assert visible[0]["id"] == policy["id"]
    assert visible[0]["decks"][0]["unavailable"] is True
