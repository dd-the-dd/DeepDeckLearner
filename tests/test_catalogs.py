from __future__ import annotations

from collections import OrderedDict

import httpx
import pytest

from deepdeck_learner import catalogs


def test_artwork_proxy_reuses_images_and_evicts_by_bytes_not_an_unbounded_file_count(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            headers={"content-type": "image/jpeg"},
            content=b"abc",
        )

    monkeypatch.setattr(catalogs.httpx, "get", get)
    monkeypatch.setattr(catalogs, "_image_cache", OrderedDict())
    monkeypatch.setattr(catalogs, "_image_cache_bytes", 0)
    monkeypatch.setattr(catalogs, "_image_cache_budget", 6)
    assert catalogs.scryfall_image("a.jpg") == (b"abc", "image/jpeg")
    catalogs.scryfall_image("a.jpg")
    assert len(calls) == 1
    catalogs.scryfall_image("b.jpg")
    catalogs.scryfall_image("c.jpg")
    assert list(catalogs._image_cache) == ["b.jpg", "c.jpg"]
    assert catalogs._image_cache_bytes == 6
    catalogs.scryfall_image("a.jpg")
    assert len(calls) == 4


def test_missing_token_artwork_is_exact_bounded_and_cached(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "data": [
                    {
                        "name": "Eldrazi Spawn",
                        "power": "0",
                        "toughness": "1",
                        "id": "spawn",
                        "image_uris": {
                            "normal": "https://cards.scryfall.io/normal/front/spawn.jpg"
                        },
                    },
                ]
            },
        )

    monkeypatch.setattr(catalogs.httpx, "get", get)
    catalogs.scryfall_card_artwork.cache_clear()
    artwork = catalogs.scryfall_card_artwork("Eldrazi Spawn Token", True, "0", "1")
    assert artwork["imageUrl"].endswith("/spawn.jpg")
    assert calls[0][1]["params"]["q"] == '!"Eldrazi Spawn" t:token'
    assert catalogs.scryfall_card_artwork("Eldrazi Spawn Token", True, "0", "1") == artwork
    assert len(calls) == 1
    assert catalogs.scryfall_card_artwork.cache_info().maxsize == 512
    catalogs.scryfall_card_artwork.cache_clear()


def test_missing_artwork_supports_alternate_faces_and_rejects_concealed_or_external_images(
    monkeypatch,
):
    catalogs.scryfall_card_artwork.cache_clear()
    monkeypatch.setattr(
        catalogs.httpx,
        "get",
        lambda url, **_: httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "name": "Front // Back",
                "card_faces": [
                    {
                        "name": "Front",
                        "image_uris": {"normal": "https://cards.scryfall.io/front.jpg"},
                    },
                    {
                        "name": "Back",
                        "image_uris": {"normal": "https://cards.scryfall.io/back.jpg"},
                    },
                ],
            },
        ),
    )
    assert (
        catalogs.scryfall_card_artwork("Back")["imageUrl"] == "https://cards.scryfall.io/back.jpg"
    )
    for name in ["Hidden card", "Unknown card", "x\nattack", "x" * 151]:
        with pytest.raises(catalogs.CatalogError):
            catalogs.scryfall_card_artwork(name)
    catalogs.scryfall_card_artwork.cache_clear()
    monkeypatch.setattr(
        catalogs.httpx,
        "get",
        lambda url, **_: httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"image_uris": {"normal": "http://localhost/private"}},
        ),
    )
    assert catalogs.scryfall_card_artwork("Not an allowed image") is None
    catalogs.scryfall_card_artwork.cache_clear()


def test_public_deck_catalog_does_not_need_an_agent_key_but_competitions_do(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    client_type = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"data": {"items": [], "pagination": {"page": 1}}},
        )

    transport = httpx.MockTransport(respond)
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_secret")
    monkeypatch.setattr(
        catalogs.httpx,
        "Client",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )

    catalogs.platform_decks("reanimator", "legacy")
    catalogs.active_competitions()

    assert [request.url.path for request in requests] == [
        "/api/v1/decks",
        "/api/v1/agents/catalog/competitions",
    ]
    assert "authorization" not in requests[0].headers
    assert requests[1].headers["Authorization"] == "Bearer ddl_agent_secret"


def test_downloaded_deck_count_excludes_tokens_and_keeps_their_snapshot(
    tmp_path, monkeypatch
) -> None:
    client_type = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/decks/deck-version"
        assert "authorization" not in request.headers
        cards = [
            {
                "cardId": "island",
                "name": "Island",
                "quantity": 60,
                "section": "main",
                "typeLine": "Basic Land — Island",
            },
            {
                "cardId": "zombie-token",
                "name": "Zombie",
                "quantity": 1,
                "section": "sideboard",
                "typeLine": "Token Creature — Zombie",
            },
        ]
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "name": "Token test",
                    "format": "legacy",
                    "cardCount": 61,
                    "cards": cards,
                }
            },
        )

    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_secret")
    monkeypatch.setattr(
        catalogs.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )

    downloaded = catalogs.download_platform_deck(tmp_path, "deck-version")

    assert downloaded["cardCount"] == 60
    assert downloaded["rawCardCount"] == 61
    assert "zombie-token" in (tmp_path / ".deepdeck" / "decks" / "deck-version.json").read_text(
        encoding="utf-8"
    )
