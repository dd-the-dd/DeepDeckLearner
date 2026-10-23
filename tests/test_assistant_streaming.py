from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from deepdeck_learner import app as app_module
from deepdeck_learner import llm_gateway, research
from deepdeck_learner.app import create_app


def test_four_color_commander_plan_requires_eligibility_legality_and_identity_count():
    plan = research.card_search_plan(
        {"format": "commander", "commander_only": True, "color_count": 4}
    )
    assert plan["source"] == "scryfall"
    assert (
        "f:commander" in plan["query"]
        and "is:commander" in plan["query"]
        and "id=4" in plan["query"]
    )
    assert plan["filters"]["color_identity"] == ""  # Do not invent which color to omit.


@pytest.mark.parametrize(
    "args",
    [
        {"format": "legacy", "commander_only": True},
        {"color_count": True},
        {"color_count": 6},
        {"color_count": 4, "color_identity": "UB"},
        {"identity_match": "anything"},
        {"format": "commander", "commander_only": True, "semantic_text": "draw"},
    ],
)
def test_inconsistent_filters_never_reach_a_catalog(args, monkeypatch):
    monkeypatch.setattr(research, "_public_get", lambda *_: pytest.fail("unexpected network"))
    with pytest.raises(ValueError):
        research.search_cards(args)


def test_specialized_search_preserves_filters_source_and_real_pagination(monkeypatch):
    calls = []

    def page(query, number, _bucket):
        calls.append((query, number))
        return {"items": [{"name": f"Commander {n}"} for n in range(25)], "hasMore": False}

    monkeypatch.setattr(research, "_scryfall_page", page)
    result = research.search_cards(
        {"format": "commander", "commander_only": True, "color_count": 4}
    )
    assert result["source"] == "scryfall" and len(result["items"]) == 20
    next_result = research.search_cards({**result["filters"], **result["resultWindow"]["next"]})
    assert len(next_result["items"]) == 5 and next_result["resultWindow"]["next"] is None
    assert calls[0][0] == calls[1][0]


@pytest.mark.asyncio
async def test_sdk_stream_forwards_text_and_actual_tool_results_without_reexecution(
    tmp_path, monkeypatch
):
    llm_gateway.save_llm_settings(
        tmp_path, {"baseUrl": "http://127.0.0.1:11434/v1", "model": "fixture"}
    )
    seen = []

    class Stream:
        final_output = "Ready {R}."

        async def stream_events(self):
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="Ready "),
            )
            tool = next(item for item in seen[0].tools if item.name == "list_agents")
            await tool.on_invoke_tool(None, "{}")
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="{R}."),
            )

    def run(agent, *_, **__):
        seen.append(agent)
        return Stream()

    monkeypatch.setattr(llm_gateway.Runner, "run_streamed", run)
    events = []
    result = await llm_gateway.chat(
        tmp_path,
        {"messages": [{"role": "user", "content": "List my agents"}]},
        lambda _: {},
        on_event=events.append,
    )
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Ready {R}."
    assert sum(e["type"] == "tool" for e in events) == 1
    assert result["message"] == "Ready {R}." and len(result["toolEvents"]) == 1
    assert "'''magic_card" in seen[0].instructions
    assert "set_card_search_filters" in seen[0].instructions


def test_stream_route_authorization_and_sse_contract(tmp_path, monkeypatch):
    calls = []

    async def fake_chat(_root, payload, *_args, on_event=None):
        calls.append(payload)
        on_event({"type": "delta", "text": "Bonjour {R}"})
        return {"message": "Bonjour {R}", "toolEvents": [], "agents": []}

    monkeypatch.setattr(app_module, "agent_chat", fake_chat)
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/v1/agent-studio/chat/stream", json={}).status_code == 403
        token = client.get("/api/v1/session").json()["token"]
        result = client.post(
            "/api/v1/agent-studio/chat/stream",
            headers={"X-DeepDeck-Token": token},
            json={"messages": []},
        )
        assert result.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")
        ]
        assert [event["type"] for event in events] == ["status", "delta", "done"]
        assert len(calls) == 1


def test_stream_error_never_exposes_provider_secrets_or_retries(tmp_path, monkeypatch):
    calls = []

    async def broken(*_args, **_kwargs):
        calls.append(1)
        raise ValueError("PRIVATE_PROVIDER_CREDENTIAL")

    monkeypatch.setattr(app_module, "agent_chat", broken)
    with TestClient(create_app(tmp_path)) as client:
        token = client.get("/api/v1/session").json()["token"]
        result = client.post(
            "/api/v1/agent-studio/chat/stream", headers={"X-DeepDeck-Token": token}, json={}
        )
        assert "PRIVATE_PROVIDER_CREDENTIAL" not in result.text
        assert '"type": "error"' in result.text and len(calls) == 1
