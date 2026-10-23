from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

from deepdeck_agent import Agent

from .forge_reference import build_forge_reference_agent

AGENT_SOURCE = '''"""Your local DeepDeck agent. Graph weights and patterns live in agent.json.

The app and copilot edit that policy. You can extend this Python factory if you
want custom behavior; saving policy changes never overwrites this file.
"""
from deepdeck_examples.forge_reference import build_forge_reference_agent


def build_agent(policy):
    cards = policy.get("cardPreferences", {})
    return build_forge_reference_agent(
        policy.get("profile", "balanced"),
        weights=policy.get("heuristics", {}),
        prefer_cards=cards.get("prefer", []),
        avoid_cards=cards.get("avoid", []),
        hold_for_interaction=cards.get("holdForInteraction", []),
        strategy_graph=policy.get("strategyGraph", {}),
    )
'''


def load_programmatic_agent(policy_path: Path) -> tuple[Agent, dict[str, Any]]:
    if policy_path.is_dir():
        policy_path = policy_path / "agent.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if (
        not isinstance(policy, dict)
        or policy.get("schemaVersion") != "deepdeck-programmatic-agent/v1"
    ):
        raise ValueError("Unsupported programmatic agent schema.")
    source = policy_path.parent / "agent.py"
    if source.is_file():
        specification = importlib.util.spec_from_file_location(
            f"deepdeck_local_{policy.get('id', 'agent').replace('-', '_')}", source
        )
        if specification is None or specification.loader is None:
            raise ValueError("The local Python agent could not be loaded.")
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        factory = getattr(module, "build_agent", None)
        if not callable(factory):
            raise ValueError("agent.py must expose build_agent(policy).")
        agent = factory(policy)
        if not callable(getattr(agent, "make_decision", None)):
            raise ValueError("The Python factory must return a DeepDeck Agent SDK agent.")
        return cast(Agent, agent), policy
    # Policies created before editable Python agents keep their original behavior.
    cards = policy.get("cardPreferences", {})
    return build_forge_reference_agent(
        str(policy.get("profile", "balanced")),
        weights=policy.get("heuristics", {}),
        prefer_cards=cards.get("prefer", []),
        avoid_cards=cards.get("avoid", []),
        hold_for_interaction=cards.get("holdForInteraction", []),
        strategy_graph=policy.get("strategyGraph", {}),
    ), policy
