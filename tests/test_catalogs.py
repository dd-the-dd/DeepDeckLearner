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
        request.headers["Authorization"] == "Bearer ddl_agent_secret"
        for request in requests
    )
