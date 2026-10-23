from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from deepdeck_learner import deck_import, deck_workshop, llm_gateway
from deepdeck_learner.app import create_app


@pytest.fixture
def cards(monkeypatch):
    def resolve(name):
        if name == "Missing card":
            raise ValueError("Card 'Missing card' could not be resolved with Scryfall.")
        return {"id": name, "name": name, "type_line": "Instant", "color_identity": ["U"]}

    monkeypatch.setattr(deck_workshop, "_scryfall_card", resolve)


def test_text_import_handles_zones_duplicate_lands_and_arena_printings():
    result = deck_import.parse_decklist("""```text
Commander
1 Toph, the First Metalbender
Deck
4x Forest
2 Forest (M21) 274
1 Fire // Ice
SB: 1 Ponder
Considering
1 Brainstorm
Companion
1 Yorion, Sky Nomad
```""")
    assert [(c["name"], c["quantity"], c["section"]) for c in result] == [
        ("Toph, the First Metalbender", 1, "commander"),
        ("Forest", 6, "main"),
        ("Fire // Ice", 1, "main"),
        ("Ponder", 1, "sideboard"),
        ("Brainstorm", 1, "considering"),
        ("Yorion, Sky Nomad", 1, "companion"),
    ]


def test_french_markdown_category_headers_return_to_main_after_commander():
    cards = deck_import.parse_decklist(
        "## Commandante — 1\n1 Toph\n## Créatures — 25\n1 Bear\n## Terrains — 34\n4 Forest"
    )
    assert [c["section"] for c in cards] == ["commander", "main", "main"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Commander",
        "1 Ponder\nThis is prose",
        "0 Ponder",
        "100 Ponder",
        "99 Ponder\n1 Ponder",
        "x" * 100001,
    ],
    ids=["blank", "header", "prose", "zero", "quantity", "duplicate-overflow", "size"],
)
def test_bad_text_never_creates_a_partial_deck(tmp_path, text, cards):
    with pytest.raises(ValueError):
        deck_import.import_decklist(
            tmp_path, {"name": "No partial", "format": "legacy", "deck_list": text}
        )
    assert deck_workshop.list_decks(tmp_path) == []


def test_unknown_name_fails_whole_import_and_can_be_corrected(tmp_path, cards):
    arguments = {
        "name": "Complete list",
        "format": "legacy",
        "deck_list": "4 Ponder\n1 Missing card",
    }
    with pytest.raises(ValueError, match="Missing card"):
        deck_import.import_decklist(tmp_path, arguments)
    assert deck_workshop.list_decks(tmp_path) == []
    result = deck_import.import_decklist(
        tmp_path, {**arguments, "deck_list": "4 Ponder\n1 Brainstorm"}
    )
    assert len(result["cards"]) == 2
    assert result["version"] == 1
    assert (tmp_path / ".deepdeck/decks" / f"{result['id']}.json").is_file()


def test_bulk_absolute_edits_preserve_unmentioned_cards_tags_and_identity(tmp_path, cards):
    deck = deck_workshop.create_deck(
        tmp_path,
        {
            "name": "Original",
            "format": "legacy",
            "cards": [
                {"name": "Island", "quantity": 20},
                {"name": "Ponder", "quantity": 2, "tags": ["selection"]},
                {"name": "Brainstorm", "quantity": 4},
            ],
        },
    )
    original = deck["cards"][1]["entryId"]
    result = deck_import.update_deck_cards(
        tmp_path,
        {
            "deck_id": deck["id"],
            "expected_version": 1,
            "cards": [
                {"name": "Ponder", "quantity": 4},
                {"name": "Brainstorm", "quantity": 0},
                {"name": "Daze", "quantity": 2, "section": "sideboard", "tags": ["interaction"]},
            ],
        },
    )
    assert [(c["name"], c["quantity"]) for c in result["cards"]] == [
        ("Island", 20),
        ("Ponder", 4),
        ("Daze", 2),
    ]
    assert result["cards"][1]["tags"] == ["selection"]
    assert result["cards"][1]["entryId"] == original
    assert result["version"] == 2
    with pytest.raises(ValueError, match="changed"):
        deck_import.update_deck_cards(
            tmp_path,
            {
                "deck_id": deck["id"],
                "expected_version": 1,
                "cards": [{"name": "Ponder", "quantity": 3}],
            },
        )
    assert deck_workshop.find_deck(tmp_path, deck["id"]) == result


@pytest.mark.parametrize(
    "changes",
    [
        [],
        [{"name": "Ponder", "quantity": True}],
        [{"name": "Ponder", "quantity": 1.5}],
        [{"name": "Ponder", "quantity": 1}, {"name": "ponder", "quantity": 2}],
        [{"name": "Ponder", "quantity": 2}, {"name": "Missing card", "quantity": 1}],
    ],
)
def test_invalid_bulk_edit_changes_nothing(tmp_path, cards, changes):
    deck = deck_workshop.create_deck(
        tmp_path,
        {"name": "Keep me", "format": "legacy", "cards": [{"name": "Ponder", "quantity": 4}]},
    )
    with pytest.raises(ValueError):
        deck_import.update_deck_cards(
            tmp_path, {"deck_id": deck["id"], "expected_version": 1, "cards": changes}
        )
    assert deck_workshop.find_deck(tmp_path, deck["id"]) == deck


@pytest.mark.parametrize("context", ["agent", "deck"])
def test_all_editor_tools_are_available_regardless_of_global_chat_focus(context):
    tools = {t["function"]["name"]: t["function"] for t in llm_gateway._tools(context)}
    assert {
        "create_agent",
        "create_deck",
        "import_decklist",
        "import_league_deck",
        "update_deck_cards",
        "update_deck_plan",
    } <= tools.keys()
    assert "expected_version" in tools["update_deck_cards"]["parameters"]["required"]
    assert len(tools) == len(llm_gateway._tools(context))


@pytest.mark.asyncio
async def test_global_chat_imports_and_edits_real_editor_fields_through_actual_sdk_and_http(
    tmp_path, cards, monkeypatch
):
    llm_gateway.save_llm_settings(
        tmp_path, {"baseUrl": "http://127.0.0.1:11434/v1", "model": "fixture"}
    )

    async def run(agent, *_args, **_kwargs):
        assert "rather than instructing the user to paste" in agent.instructions
        tools = {tool.name: tool for tool in agent.tools}

        async def invoke(name, args):
            event = json.loads(await tools[name].on_invoke_tool(None, json.dumps(args)))
            assert event["ok"], event
            return event["result"]

        created = await invoke(
            "import_decklist",
            {
                "name": "My imported deck",
                "format": "commander",
                "deck_list": "Commander\n1 Toph\nDeck\n4 Forest",
            },
        )
        changed = await invoke(
            "update_deck_cards",
            {
                "deck_id": created["id"],
                "expected_version": 1,
                "cards": [
                    {"name": "Forest", "quantity": 8},
                    {"name": "Ponder", "quantity": 1, "section": "considering"},
                ],
            },
        )
        await invoke(
            "update_deck_plan",
            {
                "deck_id": created["id"],
                "expected_version": changed["version"],
                "name": "Edited in chat",
                "description": "Protect my engine",
                "tags": ["combo"],
                "collections": ["experiments"],
            },
        )
        return SimpleNamespace(final_output="Local list imported and edited.")

    monkeypatch.setattr(llm_gateway.Runner, "run", run)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url="http://testserver"
    ) as client:
        token = (await client.get("/api/v1/session")).json()["token"]
        response = await client.post(
            "/api/v1/agent-studio/chat",
            headers={"X-DeepDeck-Token": token},
            json={
                "context": "agent",
                "messages": [{"role": "user", "content": "Import my deck and edit it yourself."}],
            },
        )
        assert response.status_code == 200, response.text
        events = response.json()["toolEvents"]
        assert [e["tool"] for e in events] == [
            "import_decklist",
            "update_deck_cards",
            "update_deck_plan",
        ]
        deck_id = events[0]["result"]["id"]
        saved = (await client.get("/api/v1/deck-workshop/decks/" + deck_id)).json()
        assert saved["name"] == "Edited in chat" and saved["description"] == "Protect my engine"
        assert saved["cards"][1]["quantity"] == 8 and saved["cards"][2]["section"] == "considering"
        assert saved["tags"] == ["combo"] and saved["collections"] == ["experiments"]
        assert saved["version"] == 3 and not saved["syncStatus"]["linked"]


@pytest.mark.asyncio
async def test_global_agent_chat_can_fork_a_public_league_deck_without_modifying_source(
    tmp_path, monkeypatch
):
    def download(root, version):
        source = root / ".deepdeck/decks" / f"{version}.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            json.dumps(
                {
                    "id": version,
                    "name": "Public deck",
                    "format": "commander",
                    "version": 7,
                    "cards": [],
                }
            )
        )
        return {"versionId": version}

    monkeypatch.setattr(llm_gateway, "download_platform_deck", download)
    tool = next(
        t
        for t in llm_gateway._sdk_tools(tmp_path, lambda _: {}, [], "agent")
        if t.name == "import_league_deck"
    )
    event = json.loads(await tool.on_invoke_tool(None, '{"version_id":"public-v7"}'))
    assert event["ok"] and event["result"]["id"].startswith("localdeck-")
    assert event["result"]["origin"] == {"kind": "league", "versionId": "public-v7"}
    assert json.loads((tmp_path / ".deepdeck/decks/public-v7.json").read_text())["version"] == 7
