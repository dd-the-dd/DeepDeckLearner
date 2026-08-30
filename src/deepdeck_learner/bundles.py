from __future__ import annotations

DECK_BUNDLES: tuple[dict[str, object], ...] = (
    {
        "id": "legacy-meta-2026-08",
        "name": "Legacy Meta · August 2026",
        "description": "Eight leading archetypes for broad competitive training coverage.",
        "format": "legacy",
        "updatedAt": "2026-08-29",
        "sources": [
            "https://www.mtggoldfish.com/metagame/legacy",
            "https://mtgdecks.net/Legacy",
        ],
        "archetypes": [
            {"name": "Dimir Tempo", "queries": ["Dimir Tempo"]},
            {"name": "Reanimator", "queries": ["Rakdos Reanimator", "Reanimator"]},
            {"name": "Azorius Tempo", "queries": ["Azorius Tempo", "UW Blink"]},
            {"name": "Izzet Delver", "queries": ["Izzet Delver", "Izzet Cutter"]},
            {"name": "Lands", "queries": ["Lands"]},
            {
                "name": "Energy",
                "queries": ["Boros Energy", "Boros Ocelot", "Mardu Energy", "Naya Energy"],
            },
            {"name": "Sneak and Show", "queries": ["Sneak and Show"]},
            {"name": "Eldrazi Stompy", "queries": ["Eldrazi Stompy", "Eldrazi"]},
        ],
    },
)


def deck_bundles(game_format: str) -> list[dict[str, object]]:
    normalized = game_format.strip().lower()
    return [bundle for bundle in DECK_BUNDLES if bundle["format"] == normalized]
