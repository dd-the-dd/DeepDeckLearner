from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from deepdeck_learner import league_capacity
from deepdeck_learner.app import create_app


def server(monkeypatch, payload=None, *, status=200):
    monkeypatch.setenv("DEEPDECK_API_KEY", "qa-agent-key")
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://league.test/api/v1")
    original = httpx.Client
    observed = []

    def request(request):
        observed.append(request)
        return httpx.Response(status, json={"data": payload})

    monkeypatch.setattr(
        league_capacity.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(request), **kwargs),
    )
    return observed


@pytest.mark.parametrize("limit,used", [(1, 0), (10, 3), (37, 2), (1, 2)])
def test_capacity_is_read_from_server_not_a_local_membership_rule(monkeypatch, limit, used):
    observed = server(
        monkeypatch,
        {
            "limit": limit,
            "used": used,
            "available": max(0, limit - used),
            "privateMembership": "do-not-expose",
        },
    )
    value = league_capacity.league_capacity()
    assert value["status"] == "ready"
    assert value["limit"] == limit and value["used"] == used
    assert "privateMembership" not in value
    assert str(observed[0].url) == "https://league.test/api/v1/matchmaking/capacity"
    assert observed[0].headers["Authorization"] == "Bearer qa-agent-key"


@pytest.mark.parametrize("status", [401, 403, 404, 500, 302])
def test_missing_capacity_never_invents_a_quota_or_exposes_credentials(monkeypatch, status):
    server(monkeypatch, status=status)
    value = league_capacity.league_capacity()
    assert value["status"] in {"unsupported", "unavailable"}
    assert value["limit"] is None and value["used"] is None and value["available"] is None
    assert "qa-agent-key" not in str(value)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"limit": True, "used": 0, "available": 1},
        {"limit": 10, "used": 2, "available": 10},
        {"limit": -1, "used": 0, "available": 0},
    ],
)
def test_invalid_capacity_is_not_displayed_as_authoritative(monkeypatch, payload):
    server(monkeypatch, payload)
    assert league_capacity.league_capacity()["limit"] is None


def test_capacity_route_preserves_the_server_contract(tmp_path, monkeypatch):
    server(monkeypatch, {"limit": 37, "used": 7, "available": 30})
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/v1/league-capacity")
        assert response.status_code == 200
        assert response.json()["available"] == 30


def test_disconnected_account_does_not_contact_a_server(monkeypatch):
    monkeypatch.delenv("DEEPDECK_API_KEY", raising=False)
    monkeypatch.setattr(
        league_capacity.httpx, "Client", lambda **_: pytest.fail("Unexpected network request")
    )
    assert league_capacity.league_capacity()["limit"] is None
