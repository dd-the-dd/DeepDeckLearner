from __future__ import annotations

import json
from itertools import combinations

import httpx
import pytest
from fastapi.testclient import TestClient

from deepdeck_learner import research
from deepdeck_learner.app import create_app
from deepdeck_learner.catalogs import CatalogError, download_platform_deck


def test_card_filters_build_native_grammar_with_escaped_text() -> None:
    query = research.card_query(
        {
            "query": "draw OR loot",
            "oracle_contains": 'draw "two"',
            "semantic_text": "protect a threat",
            "types": ["Instant", "Sorcery"],
            "color_identity": "ub",
            "min_mana_value": 0,
            "max_mana_value": 2,
        }
    )
    assert (
        query.replace("id!=W id!=R id!=G", "id<=UB")
        == '(draw OR loot) o:"draw \\"two\\"" o~"protect a threat" (t:"Instant" OR t:"Sorcery") id<=UB mv>=0 mv<=2'
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"types": "Instant"},
        {"query": "é" * 257},
        {"query": "draw", "color_identity": "X"},
        {"min_mana_value": float("nan")},
        {"min_mana_value": 4, "max_mana_value": 2},
        {"max_mana_value": -1},
    ],
)
def test_invalid_filters_are_rejected_before_network(arguments) -> None:
    with pytest.raises(ValueError):
        research.card_query(arguments)


@pytest.mark.parametrize(
    "colors", ["".join(group) for size in range(1, 6) for group in combinations("WUBRG", size)]
)
def test_all_colored_identities_exclude_only_outside_colors(colors):
    query = research.card_query({"color_identity": colors})
    outside = [f"id!={color}" for color in "WUBRG" if color not in colors]
    assert query == (" ".join(outside) if outside else "(id:C OR id!=C)")
    assert "id:C" not in query or colors == "WUBRG"


def test_colorless_identity_is_explicit():
    assert research.card_query({"color_identity": "C"}) == "id:C"


def test_public_search_never_uses_account_or_llm_credentials(monkeypatch) -> None:
    observed = []
    real_client = httpx.Client

    def respond(request):
        observed.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(
            200,
            json={"data": {"items": [{"name": f"Card {n}"} for n in range(30)], "hasMore": True}},
        )

    monkeypatch.setenv("DEEPDECK_API_KEY", "private-league-key")
    monkeypatch.setenv("DEEPDECK_LLM_API_KEY", "private-llm-key")
    monkeypatch.setattr(
        research.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = research.search_cards({"oracle_contains": "draw", "format": "commander"})
    assert len(result["items"]) == 20 and result["omittedItems"] == 10
    assert result["hasMore"] is True
    assert observed[0].url.path == "/api/v1/cards/search"
    assert observed[0].url.params["format"] == "commander"


def test_every_card_remains_reachable_across_server_page_boundaries(monkeypatch) -> None:
    def page(path, params):
        assert path == "cards/search" and params["q"] == "(draw)"
        first = (params["page"] - 1) * 175
        return {
            "items": [{"name": f"Card {n}"} for n in range(first, min(first + 175, 196))],
            "hasMore": params["page"] == 1,
        }

    monkeypatch.setattr(research, "_public_get", page)
    cursor = {"page": 1, "page_offset": 0}
    seen = []
    while cursor:
        result = research.search_cards({"query": "draw", **cursor})
        assert len(result["items"]) <= 20
        seen.extend(item["name"] for item in result["items"])
        cursor = result["resultWindow"]["next"]
    assert seen == [f"Card {n}" for n in range(196)]


@pytest.mark.parametrize(
    "arguments",
    [
        {"page": True},
        {"page": 1.5},
        {"page": "1"},
        {"page": 0},
        {"page": 10_001},
        {"page_offset": True},
        {"page_offset": 1.5},
        {"page_offset": "20"},
        {"page_offset": -1},
        {"page_offset": 175},
    ],
)
def test_invalid_card_cursor_is_rejected_before_network(monkeypatch, arguments):
    monkeypatch.setattr(research, "_public_get", lambda *_: pytest.fail("Unexpected request"))
    with pytest.raises(ValueError):
        research.search_cards({"query": "draw", **arguments})


def test_changed_page_and_server_navigation_limit_are_explicit(monkeypatch):
    monkeypatch.setattr(
        research, "_public_get", lambda *_: {"items": [{"name": "One"}], "hasMore": True}
    )
    with pytest.raises(CatalogError, match="results changed"):
        research.search_cards({"query": "draw", "page_offset": 20})
    result = research.search_cards({"query": "draw", "page": 10_000})
    assert result["resultWindow"]["next"] is None
    assert result["resultWindow"]["navigationLimitReached"] is True


@pytest.mark.asyncio
async def test_sdk_card_tool_can_follow_the_returned_cursor_without_generation(
    tmp_path, monkeypatch
):
    from deepdeck_learner.llm_gateway import _sdk_tools

    monkeypatch.setattr(
        research,
        "_public_get",
        lambda *_: {"items": [{"name": f"Card {n}"} for n in range(25)], "hasMore": False},
    )
    events = []
    tool = next(
        t for t in _sdk_tools(tmp_path, lambda _: {}, events, "deck") if t.name == "search_cards"
    )
    assert tool.params_json_schema["properties"]["page_offset"]["maximum"] == 174
    first = json.loads(
        await tool.on_invoke_tool(None, json.dumps({"query": "draw", "format": "legacy"}))
    )
    next_page = json.loads(
        await tool.on_invoke_tool(
            None,
            json.dumps(
                {"query": "draw", "format": "legacy", **first["result"]["resultWindow"]["next"]}
            ),
        )
    )
    assert next_page["ok"] is True
    assert [card["name"] for card in next_page["result"]["items"]] == [
        f"Card {n}" for n in range(20, 25)
    ]
    assert next_page["result"]["resultWindow"]["next"] is None
    assert [event["tool"] for event in events] == ["search_cards", "search_cards"]


def test_semantic_search_reports_the_actual_server_limit_without_fallback(monkeypatch) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        research.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    503, json={"error": {"message": "Semantic search is disabled."}}
                )
            ),
            **kwargs,
        ),
    )
    with pytest.raises(CatalogError, match="Semantic search is disabled"):
        research.search_decks({"query": "a resilient tempo deck", "semantic": True})


def test_research_routes_require_local_session_and_validate_arguments(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/v1/research/cards", json={"query": "draw"}).status_code == 403
    assert client.post("/api/v1/agent-studio/mcp-test").status_code == 403
    token = client.get("/api/v1/session").json()["token"]
    response = client.post(
        "/api/v1/research/cards", headers={"X-DeepDeck-Token": token}, json={"max_mana_value": -1}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("version_id", ["../private", "../deck/../../secret", "a?key=foo", "a/b"])
def test_import_rejects_path_traversal_before_any_request(tmp_path, version_id) -> None:
    with pytest.raises(CatalogError, match="version"):
        download_platform_deck(tmp_path, version_id)
