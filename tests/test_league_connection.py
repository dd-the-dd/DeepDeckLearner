from __future__ import annotations

import json

import httpx
import pytest

from deepdeck_learner import league_connection
from deepdeck_learner.catalogs import _platform_url
from deepdeck_learner.jobs import JobManager
from deepdeck_learner.league_account import platform_url as account_platform_url


def test_production_is_the_default_without_silently_selecting_staging(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("DEEPDECK_PLATFORM_URL", raising=False)
    monkeypatch.delenv("DEEPDECK_AGENT_URL", raising=False)
    expected = "https://www.deepdeckleague.com/api/v1"
    assert league_connection.platform_url() == expected
    assert account_platform_url() == expected
    assert _platform_url() == expected
    environment = JobManager(tmp_path)._child_environment()
    assert environment["DEEPDECK_PLATFORM_URL"] == expected
    assert environment["DEEPDECK_AGENT_URL"] == "wss://www.deepdeckleague.com/api/v1/agents/connect"


def test_legacy_apex_configuration_resolves_to_the_same_production_host(monkeypatch) -> None:
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://deepdeckleague.com/api/v1/")
    monkeypatch.setenv("DEEPDECK_AGENT_URL", "wss://deepdeckleague.com/api/v1/agents/connect")
    assert league_connection.platform_url() == "https://www.deepdeckleague.com/api/v1"
    assert league_connection.agent_url() == "wss://www.deepdeckleague.com/api/v1/agents/connect"


def test_an_explicit_staging_or_custom_server_is_preserved(monkeypatch) -> None:
    for url in (
        "https://staging.deepdeckleague.com/api/v1",
        "https://private.test/api/v1",
        "http://127.0.0.1:8001/api/v1",
    ):
        monkeypatch.setenv("DEEPDECK_PLATFORM_URL", url)
        assert league_connection.platform_url() == url


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@host.test/api/v1",
        "http://remote.test/api/v1",
        "https://host.test/api/v1?token=secret",
        "file:///tmp",
    ],
)
def test_unsafe_league_urls_are_rejected(url) -> None:
    with pytest.raises(ValueError):
        league_connection.normalize_platform_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ws://remote.test/agents/connect",
        "wss://user:secret@remote.test/connect",
        "wss://remote.test/connect?token=secret",
    ],
)
def test_unsafe_agent_urls_are_rejected(monkeypatch, url) -> None:
    monkeypatch.setenv("DEEPDECK_AGENT_URL", url)
    with pytest.raises(ValueError):
        league_connection.agent_url()


def test_runtime_capabilities_are_read_without_credentials_and_no_fallback(monkeypatch) -> None:
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://deepdeckleague.com/api/v1")
    monkeypatch.setenv("DEEPDECK_API_KEY", "secret-agent-key")
    original = httpx.Client
    observed = []

    def request(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "releaseProfile": "production-proven",
                    "capabilities": ["league", "mcp"],
                    "naturalCardSearchEnabled": False,
                }
            },
        )

    monkeypatch.setattr(
        league_connection.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(request), **kwargs),
    )
    result = league_connection.connection_status()
    assert result["reachable"] is True and result["environment"] == "production"
    assert "ai-decks" not in result["capabilities"]
    assert "authorization" not in observed[0].headers
    assert str(observed[0].url) == "https://www.deepdeckleague.com/api/v1/runtime-config"
    assert "secret-agent-key" not in json.dumps(result)

    def offline(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.deepdeckleague.com"
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(
        league_connection.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(offline), **kwargs),
    )
    assert league_connection.connection_status()["reachable"] is False
