from __future__ import annotations

import os

from deepdeck_agent import AgentConfig, DeckPolicy, PlaySpeed


def random_config() -> AgentConfig:
    return AgentConfig(
        agent_id=os.getenv(
            "DEEPDECK_AGENT_ID", "com.deepdeckleague.example.random"
        ).strip(),
        name="Public random baseline",
        version=os.getenv("DEEPDECK_AGENT_VERSION", "0.1.0").strip(),
        author="Deep Deck League",
        description="Samples one legal action with a reproducible pseudo-random generator.",
        formats=("legacy", "commander"),
        decks=DeckPolicy.all(),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
    )


def alexios_config() -> AgentConfig:
    deck_id = os.getenv("ALEXIOS_DECK_ID", "alexios").strip() or "alexios"
    return AgentConfig(
        agent_id=os.getenv(
            "DEEPDECK_AGENT_ID", "com.deepdeckleague.example.alexios"
        ).strip(),
        name="Alexios fast equipment",
        version=os.getenv("DEEPDECK_AGENT_VERSION", "0.1.0").strip(),
        author="Deep Deck League",
        description="Transparent rule-based Alexios equipment and protection policy.",
        formats=("commander",),
        decks=DeckPolicy.only(deck_id),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
    )


def deep_learning_config(version: str) -> AgentConfig:
    if version not in {"v11", "v11.1", "v12", "v12.1"}:
        raise ValueError("deep learning version must be v11, v11.1, v12, or v12.1")
    pretrained = "." in version
    default_id = f"com.deepdeckleague.example.{version}"
    descriptions = {
        "v11": "Trainable V11 recurrent multiplayer policy example.",
        "v11.1": "Official frozen V11.1 Commander weights.",
        "v12": "Trainable V12 recurrent two-player zero-sum policy example.",
        "v12.1": "Official frozen V12.1 Legacy weights.",
    }
    formats = {
        "v11": ("legacy", "commander"),
        "v11.1": ("commander",),
        "v12": ("legacy",),
        "v12.1": ("legacy",),
    }
    return AgentConfig(
        agent_id=os.getenv("DEEPDECK_AGENT_ID", default_id).strip(),
        name=(
            f"Deep Deck {version.upper()} pretrained"
            if pretrained
            else f"Deep learning {version.upper()} example"
        ),
        version=os.getenv("DEEPDECK_AGENT_VERSION", version if pretrained else "0.1.0").strip(),
        author="Deep Deck League",
        description=descriptions[version],
        formats=formats[version],
        decks=DeckPolicy.all(),
        speeds=(PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
    )
