"""Public SDK for connecting Python agents to the authoritative Rust engine."""

from typing import Any

from oracle_ai.agents.base import MagicAgent
from oracle_ai.agents.protocol import (
    AgentAuthor,
    AgentCapabilities,
    AgentCompatibility,
    AgentManifest,
    AgentRepository,
    DeckSelection,
    GameSharing,
    ObservationStream,
    ScientificPublication,
    TimeoutCategory,
)

__all__ = [
    "AgentAuthor",
    "AgentCapabilities",
    "AgentClient",
    "AgentCompatibility",
    "AgentManifest",
    "AgentRepository",
    "DeckSelection",
    "GameSharing",
    "MagicAgent",
    "ObservationStream",
    "OracleModelAgent",
    "ScientificPublication",
    "TimeoutCategory",
]


def __getattr__(name: str) -> Any:
    # Keep protocol and manifest imports usable in tooling that does not install
    # the optional runtime transport yet.
    if name == "AgentClient":
        from oracle_ai.agents.client import AgentClient

        return AgentClient
    if name == "OracleModelAgent":
        from oracle_ai.agents.model_agent import OracleModelAgent

        return OracleModelAgent
    raise AttributeError(name)
