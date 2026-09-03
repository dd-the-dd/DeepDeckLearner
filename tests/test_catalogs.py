from __future__ import annotations

import httpx

from deepdeck_learner import catalogs


def test_hosted_catalog_uses_authenticated_agent_route(monkeypatch) -> None:
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
        "/api/v1/agents/catalog/decks",
        "/api/v1/agents/catalog/competitions",
    ]
    assert all(
        request.headers["Authorization"] == "Bearer ddl_agent_secret" for request in requests
    )


def test_downloaded_deck_count_excludes_tokens_and_keeps_their_snapshot(
    tmp_path, monkeypatch
) -> None:
    client_type = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
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
