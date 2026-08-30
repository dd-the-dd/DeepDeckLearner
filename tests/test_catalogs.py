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
            json={
                "data": []
                if request.url.path.endswith("/training-lots")
                else {"items": [], "pagination": {"page": 1}}
            },
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
    catalogs.platform_training_lots()

    assert [request.url.path for request in requests] == [
        "/api/v1/agents/catalog/decks",
        "/api/v1/agents/catalog/competitions",
        "/api/v1/agents/training-lots",
    ]
    assert all(
        request.headers["Authorization"] == "Bearer ddl_agent_secret"
        for request in requests
    )


def test_training_lot_download_reports_received_payload_bytes(monkeypatch) -> None:
    client_type = httpx.Client
    payload = {"data": {"schema": "deepdeck-training-lot/v1", "id": "lot", "decks": []}}

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=payload)

    transport = httpx.MockTransport(respond)
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_secret")
    monkeypatch.setattr(
        catalogs.httpx,
        "Client",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )

    manifest, downloaded_bytes = catalogs.download_training_lot("lot")

    assert manifest["schema"] == "deepdeck-training-lot/v1"
    assert downloaded_bytes > 0
