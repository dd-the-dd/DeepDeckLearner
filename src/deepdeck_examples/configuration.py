from __future__ import annotations

import os

from deepdeck_agent import AgentConfig, DeckPolicy, PlaySpeed


def _repository_commit() -> str:
    return os.getenv("DEEPDECK_AGENT_COMMIT", "local-worktree").strip() or "local-worktree"


def _heuristic_formats() -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(
            value.strip().casefold()
            for value in os.getenv("DEEPDECK_AGENT_FORMATS", "legacy,commander").split(",")
            if value.strip()
        )
    )
    if not values or any(value not in {"legacy", "commander"} for value in values):
        raise ValueError("DEEPDECK_AGENT_FORMATS supports only legacy and commander")
    return values


def random_config() -> AgentConfig:
    return AgentConfig(
        agent_id=os.getenv("DEEPDECK_AGENT_ID", "com.deepdeckleague.example.random").strip(),
        name="Public random baseline",
        version=os.getenv("DEEPDECK_AGENT_VERSION", "0.1.0").strip(),
        author="Deep Deck League",
        description="Samples one legal action with a reproducible pseudo-random generator.",
        formats=("legacy", "commander"),
        decks=DeckPolicy.all(),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
        repository_commit=_repository_commit(),
    )


def alexios_config() -> AgentConfig:
    deck_id = os.getenv("ALEXIOS_DECK_ID", "alexios").strip() or "alexios"
    return AgentConfig(
        agent_id=os.getenv("DEEPDECK_AGENT_ID", "com.deepdeckleague.example.alexios").strip(),
        name="Alexios fast equipment",
        version=os.getenv("DEEPDECK_AGENT_VERSION", "0.1.0").strip(),
        author="Deep Deck League",
        description="Transparent rule-based Alexios equipment and protection policy.",
        formats=("commander",),
        decks=DeckPolicy.only(deck_id),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
        repository_commit=_repository_commit(),
    )


def omnath_config() -> AgentConfig:
    deck_id = os.getenv("OMNATH_DECK_ID", "omnath").strip() or "omnath"
    return AgentConfig(
        agent_id=os.getenv("DEEPDECK_AGENT_ID", "com.deepdeckleague.example.omnath").strip(),
        name="Omnath mana and X spells",
        version=os.getenv("DEEPDECK_AGENT_VERSION", "0.1.0").strip(),
        author="Deep Deck League",
        description="Mana-first Omnath policy that protects its commander and times X spells.",
        formats=("commander",),
        decks=DeckPolicy.only(deck_id),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
        repository_commit=_repository_commit(),
    )


def heuristic_config() -> AgentConfig:
    return AgentConfig(
        agent_id=os.getenv("DEEPDECK_AGENT_ID", "com.deepdeckleague.example.heuristic").strip(),
        name=os.getenv("DEEPDECK_AGENT_NAME", "Programmatic heuristic baseline").strip(),
        version=os.getenv("DEEPDECK_AGENT_VERSION", "0.1.0").strip(),
        author=os.getenv("DEEPDECK_AGENT_AUTHOR", "Deep Deck League").strip(),
        description=(
            os.getenv(
                "DEEPDECK_AGENT_DESCRIPTION",
                "Deterministic, explainable and configurable rule-based policy.",
            ).strip()
        ),
        formats=_heuristic_formats(),
        decks=DeckPolicy.all(),
        speeds=(PlaySpeed.MS_100, PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
        repository_commit=_repository_commit(),
    )


def deep_learning_config(version: str) -> AgentConfig:
    if version not in {"v11", "v12"}:
        raise ValueError("deep learning version must be v11 or v12")
    default_id = f"com.deepdeckleague.example.{version}"
    return AgentConfig(
        agent_id=os.getenv("DEEPDECK_AGENT_ID", default_id).strip(),
        name=f"Deep learning {version.upper()} example",
        version=os.getenv("DEEPDECK_AGENT_VERSION", "0.1.0").strip(),
        author="Deep Deck League",
        description=(
            "Trainable V11 recurrent multiplayer policy example."
            if version == "v11"
            else "Trainable V12 recurrent two-player zero-sum policy example."
        ),
        formats=("legacy", "commander") if version == "v11" else ("legacy",),
        decks=DeckPolicy.all(),
        speeds=(PlaySpeed.SECOND_1, PlaySpeed.SECONDS_10),
        repository_url="https://github.com/dd-the-dd/DeepDeckLearner",
        repository_commit=_repository_commit(),
    )
