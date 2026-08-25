from __future__ import annotations

import os

from deepdeck_agent import AgentConfig, DeckPolicy, PlaySpeed


def random_config() -> AgentConfig:
    return AgentConfig(
        agent_id="com.deepdeckleague.example.random",
        name="Public random baseline",
        version="0.1.0",
        author="Deep Deck League",
        description="Samples one legal action with a reproducible pseudo-random generator.",
        formats=("legacy", "commander"),
        decks=DeckPolicy.all(),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/deepdeck-agent-examples",
    )


def alexios_config() -> AgentConfig:
    deck_id = os.getenv("ALEXIOS_DECK_ID", "alexios").strip() or "alexios"
    return AgentConfig(
        agent_id="com.deepdeckleague.example.alexios",
        name="Alexios fast equipment",
        version="0.1.0",
        author="Deep Deck League",
        description="Transparent rule-based Alexios equipment and protection policy.",
        formats=("commander",),
        decks=DeckPolicy.only(deck_id),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/deepdeck-agent-examples",
    )

