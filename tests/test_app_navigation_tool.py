from pathlib import Path

import pytest

from deepdeck_learner.llm_gateway import _execute_tool, _sdk_tools, _tools


@pytest.mark.parametrize("context", ["agent", "deck"])
def test_navigation_tool_available_in_both_workshops(context):
    definition = next(
        t["function"] for t in _tools(context) if t["function"]["name"] == "navigate_app"
    )
    assert definition["parameters"]["required"] == ["page"]


@pytest.mark.parametrize(
    "page", ["play", "decks", "agents", "league", "competitions", "replays", "insights"]
)
def test_navigation_only_proposes_a_page_without_side_effects(tmp_path: Path, page):
    def no_match(_payload):
        pytest.fail("Navigation must not launch a match")

    assert _execute_tool(tmp_path, "navigate_app", {"page": page}, no_match) == {
        "page": page,
        "requiresConfirmation": True,
        "navigated": False,
    }
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_sdk_navigation_rejects_arbitrary_paths(tmp_path):
    events = []
    tool = next(t for t in _sdk_tools(tmp_path, lambda _: {}, events) if t.name == "navigate_app")
    await tool.on_invoke_tool(None, '{"page":"https://evil.test"}')
    assert events[-1]["ok"] is False


def test_favicon_is_served_as_an_image_instead_of_the_spa_fallback(tmp_path):
    from fastapi.testclient import TestClient

    from deepdeck_learner.app import create_app

    assets = tmp_path / "apps/learner-web/dist/assets"
    assets.mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\nfixture"
    (assets / "favicon.png").write_bytes(png)
    (assets.parent / "index.html").write_text("SPA fallback", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/assets/favicon.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == png
