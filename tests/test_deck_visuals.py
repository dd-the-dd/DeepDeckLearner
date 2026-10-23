from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from deepdeck_learner import catalogs
from deepdeck_learner.app import create_app
from deepdeck_learner.deck_visuals import deck_cover


def card(name, **values):
    return {
        "name": name,
        "quantity": 4,
        "section": "main",
        "typeLine": "Creature",
        "imageUri": f"https://cards.example/{name}.jpg",
        **values,
    }


def test_signature_card_beats_a_generic_staple_and_name_beats_quantity():
    deck = {
        "name": "Sneak Attack",
        "cards": [
            card("Brainstorm", typeLine="Instant"),
            card("Sneak Attack", quantity=2, typeLine="Enchantment"),
            card("Emrakul", tags=["finisher"]),
        ],
    }
    assert deck_cover(deck)["name"] == "Sneak Attack"
    assert deck_cover(deck)["reason"] == "deck-name"
    deck["name"] = "Combo experiment"
    assert deck_cover(deck)["name"] == "Emrakul"


def test_commander_wins_and_sideboard_considering_and_tokens_do_not_participate():
    commander = card("Atraxa", quantity=1, section="commander")
    excluded = [
        card("Combo", section=section, tags=["signature"])
        for section in ("sideboard", "considering", "companion")
    ]
    assert (
        deck_cover(
            {
                "name": "Combo",
                "cards": excluded
                + [
                    card("Combo", isToken=True),
                    card("Combo", isGamePiece=True),
                    card("Combo", quantity=0),
                    commander,
                ],
            }
        )["name"]
        == "Atraxa"
    )


def test_nonland_threat_beats_basic_lands_and_selection_is_stable():
    cards = [card("Island", typeLine="Basic Land", quantity=24), card("Zulu"), card("Alpha")]
    assert deck_cover({"cards": cards})["name"] == "Alpha"
    assert deck_cover({"cards": list(reversed(cards))})["name"] == "Alpha"


@pytest.mark.parametrize(
    "image",
    [
        "javascript:alert(1)",
        "http://cards.example/x.jpg",
        "https://user:secret@cards.example/x.jpg",
        "data:image/svg+xml,test",
    ],
)
def test_unsafe_images_are_not_returned(image):
    assert deck_cover({"cards": [card("Unsafe", imageUri=image)]}) is None


def test_double_faced_card_uses_front_image():
    assert (
        deck_cover(
            {
                "cards": [
                    card(
                        "Delver",
                        imageUri="",
                        card_faces=[{"image_uris": {"normal": "https://cards.example/delver.jpg"}}],
                    )
                ]
            }
        )["imageUrl"]
        == "https://cards.example/delver.jpg"
    )


def test_cached_artwork_needs_no_network_and_is_exposed_in_catalog(tmp_path, monkeypatch):
    target = tmp_path / ".deepdeck" / "decks" / "version-1.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"name": "Delver", "format": "legacy", "cards": [card("Delver")]}))
    monkeypatch.setattr(
        catalogs.httpx,
        "Client",
        lambda **kwargs: pytest.fail("Local artwork must not access the network"),
    )
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/v1/catalog/decks/version-1/cover")
        assert response.status_code == 200
        assert response.json()["coverCard"]["name"] == "Delver"
        assert (
            client.get("/api/v1/catalog/downloaded-decks").json()["items"][0]["coverCard"]["name"]
            == "Delver"
        )


def test_remote_artwork_is_public_and_does_not_download_a_deck(tmp_path, monkeypatch):
    requests = []
    client_type = httpx.Client

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, json={"data": {"cards": [card("Remote threat")]}}, request=request
        )

    monkeypatch.setattr(
        catalogs.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setenv("DEEPDECK_API_KEY", "must-not-leak")
    catalogs._public_deck_cover.cache_clear()
    for _ in range(2):
        assert (
            catalogs.deck_cover_presentation(tmp_path, "remote-version")["coverCard"]["name"]
            == "Remote threat"
        )
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
    assert not (tmp_path / ".deepdeck" / "decks").exists()
