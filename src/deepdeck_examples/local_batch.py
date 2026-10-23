from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import time
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from deepdeck_agent import (
    Agent,
    AgentRunner,
    Decision,
    DecisionResponse,
    Event,
    Game,
    PlaySpeed,
    ServerTarget,
)
from deepdeck_agent.protocol import DecisionResolvedRequest

from deepdeck_learner.decision_audit import summarize_decisions
from deepdeck_learner.experience import ReplayStorageError, write_replay
from deepdeck_learner.local_seats import local_player_ids
from deepdeck_learner.play_settings import game_seed
from deepdeck_learner.replay_summary import replay_match_summary
from deepdeck_learner.statistics_metrics import ProfileCollector
from deepdeck_learner.statistics_store import (
    note_engine_analytics,
    note_storage_error,
    tracking_targets,
    write_match_results,
)

from .configuration import programmatic_config
from .programmatic import load_programmatic_agent

TERMINAL_STATUSES = frozenset(
    {"complete", "completed", "gameOver", "turnLimitReached", "error", "failed"}
)
REPLAY_ZONES = ("library", "hand", "battlefield", "graveyard", "exile", "commandZone")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run a local batch between programmatic agents.")
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--spec", type=Path, required=True)
    result.add_argument("--engine-url", default="http://127.0.0.1:8787")
    return result


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Unable to read {label}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return value


class DecisionRecorder:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.unmatched_feedback = 0
        self.metrics: ProfileCollector | None = None

    def reset(self) -> None:
        self.frames = []
        self.unmatched_feedback = 0
        self.metrics = None

    def summary(self) -> dict[str, Any]:
        return summarize_decisions(self.frames, unmatched_feedback=self.unmatched_feedback)


class RecordingAgent(Agent):
    """Observe SDK decisions without changing the policy or legal-action selection."""

    def __init__(
        self, delegate: Agent, recorder: DecisionRecorder, *, decision_interval_ms: int = 0
    ) -> None:
        self.delegate = delegate
        self.recorder = recorder
        self.decision_interval_ms = decision_interval_ms

    async def analyze_starting_situation(
        self, observation: dict[str, object], known_deck: list[dict[str, object]]
    ) -> None:
        await self.delegate.analyze_starting_situation(observation, known_deck)

    async def on_game_start(self, game: Game, known_deck: list[dict[str, object]]) -> None:
        await self.delegate.on_game_start(game, known_deck)

    async def on_observation(self, game: Game) -> None:
        if self.recorder.metrics:
            self.recorder.metrics.observe({"state": game.raw})
        await self.delegate.on_observation(game)

    async def on_event(self, event: Event, game: Game) -> None:
        await self.delegate.on_event(event, game)

    async def on_game_end(self, outcome: dict[str, object]) -> None:
        await self.delegate.on_game_end(outcome)

    async def on_decision_resolved(self, result: DecisionResolvedRequest) -> None:
        for frame in reversed(self.recorder.frames):
            if (
                (frame.get("decision") or {}).get("id") == result.request_id
                and isinstance(frame.get("agentDecision"), dict)
                and not frame["agentDecision"].get("resolution")
            ):
                proposed = deepcopy(frame.get("selectedAction"))
                if result.action_id:
                    selected = frame.get("selectedAction") or {}
                    if selected.get("id") != result.action_id:
                        selected = {"id": result.action_id, "label": "Engine fallback"}
                    frame["selectedAction"] = selected
                if result.fallback_reason:
                    frame["fallbackReason"] = result.fallback_reason
                    frame["agentResponse"] = proposed
                    frame["selectedAction"] = {
                        "id": result.action_id,
                        "label": "Engine fallback"
                        if result.action_id
                        else "Engine fallback · exact choice not supplied",
                    }
                frame["agentDecision"]["resolution"] = (
                    "fallback" if result.fallback_reason else "confirmed"
                )
                break
        else:
            self.recorder.unmatched_feedback += 1
        await self.delegate.on_decision_resolved(result)

    async def make_decision(self, decision: Decision) -> DecisionResponse:
        if self.recorder.metrics:
            self.recorder.metrics.observe({"state": decision.game.raw})
        started = time.perf_counter()
        frame = compact_view(
            {
                "state": decision.game.raw,
                "revision": len(self.recorder.frames) + 1,
                "decision": {
                    "id": decision.request_id,
                    "kind": decision.kind,
                    "playerId": decision.player_id,
                },
            }
        )
        diagnostics: dict[str, Any] = {"status": "pending", "resolution": None}
        frame["agentDecision"] = diagnostics
        # Keep cancelled/error attempts too. Appending only after a successful
        # return made missing decisions look like a complete healthy trace.
        self.recorder.frames.append(frame)
        policy_started = time.perf_counter()
        try:
            response = await self.delegate.make_decision(decision)
        except asyncio.CancelledError:
            diagnostics.update(status="cancelled", error="The SDK cancelled this decision handler.")
            raise
        except Exception as error:
            diagnostics.update(status="error", error=str(error)[:300])
            raise
        finally:
            diagnostics["policyDurationMs"] = round(
                (time.perf_counter() - policy_started) * 1000, 3
            )
            diagnostics["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
        action = next(
            (candidate for candidate in decision.actions if candidate.id == response.action_id),
            None,
        )
        frame["selectedAction"] = {
            **(deepcopy(action.raw) if action is not None else {}),
            **response.model_dump(by_alias=True, exclude_none=True),
            "id": response.action_id,
            "label": action.label if action else decision.kind,
        }
        explanation = getattr(self.delegate, "last_decision_explanation", None)
        if isinstance(explanation, dict):
            frame["strategy"] = deepcopy(explanation)
        # Local pacing is independent of the League SDK's transport clock. Do
        # not cancel a useful policy merely because it needs more than 1ms.
        remaining = self.decision_interval_ms / 1000 - (time.perf_counter() - started)
        if remaining > 0:
            try:
                await asyncio.sleep(remaining)
            except asyncio.CancelledError:
                diagnostics.update(status="cancelled", error="Local decision pacing was cancelled.")
                diagnostics["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
                raise
        diagnostics["status"] = "returned"
        diagnostics["durationMs"] = round((time.perf_counter() - started) * 1000, 3)
        return response


def _runner(
    policy_path: Path,
    engine_url: str,
    recorder: DecisionRecorder | None = None,
    *,
    namespace: str = "",
    speed: PlaySpeed = PlaySpeed.SECOND_1,
    decision_interval_ms: int = 0,
) -> AgentRunner:
    agent, policy = load_programmatic_agent(policy_path)
    if recorder is not None:
        agent = RecordingAgent(agent, recorder, decision_interval_ms=decision_interval_ms)
    config = programmatic_config(policy)
    if namespace:
        config = replace(config, agent_id=f"{config.agent_id}.local.{namespace}")
    return AgentRunner(
        agent=agent,
        config=config,
        target=ServerTarget.local(engine_url),
        speed=speed,
    )


def _compact_card(card: Any) -> dict[str, Any] | None:
    if not isinstance(card, dict):
        return None
    definition = card.get("definition")
    definition = definition if isinstance(definition, dict) else {}
    return {
        key: value
        for key, value in {
            **{
                key: deepcopy(value)
                for key, value in card.items()
                if key not in {"definition", "rules"}
            },
            "instanceId": card.get("instanceId"),
            "name": definition.get("name", card.get("name")),
            "cardId": definition.get("id", card.get("cardId")),
            "controllerId": card.get("controllerId", card.get("controller")),
            "ownerId": card.get("ownerId", card.get("owner")),
            "tapped": card.get("tapped"),
            "manaCost": definition.get("manaCost", card.get("manaCost")),
            "typeLine": definition.get("typeLine", card.get("typeLine")),
            "power": card.get("power", definition.get("power")),
            "toughness": card.get("toughness", definition.get("toughness")),
            "counters": card.get("counters"),
            "keywords": definition.get("keywords", card.get("keywords")),
        }.items()
        if value not in (None, [], {})
    }


def compact_view(view: dict[str, Any]) -> dict[str, Any]:
    state = view.get("state", {})
    state = state if isinstance(state, dict) else {}
    compact_state = {
        key: deepcopy(value)
        for key, value in state.items()
        if key
        in {
            "activePlayer",
            "priorityPlayer",
            "activePlayerId",
            "priorityPlayerId",
            "turnNumber",
            "step",
            "status",
            "outcome",
            "winnerIds",
            "combat",
            "gameMode",
            "phase",
        }
    }
    compact_players: list[dict[str, Any]] = []
    for player in state.get("players", []):
        if not isinstance(player, dict):
            continue
        compact_player = {
            key: deepcopy(player.get(key))
            for key in (
                "id",
                "name",
                "life",
                "poisonCounters",
                "hasLost",
                "energyCounters",
                "manaPool",
                "commanderDamageTaken",
                "landsPlayedThisTurn",
            )
            if player.get(key) is not None
        }
        for zone in REPLAY_ZONES:
            cards = player.get(zone, [])
            if isinstance(cards, list):
                compact_player[zone] = [_compact_card(card) for card in cards]
        compact_players.append(compact_player)
    compact_state["players"] = compact_players
    compact_state["stack"] = []
    for item in state.get("stack", []):
        if not isinstance(item, dict):
            continue
        card = _compact_card(item.get("card"))
        if card is not None:
            compact_state["stack"].append(
                {
                    key: deepcopy(value)
                    for key, value in {
                        "id": item.get("id"),
                        "controller": item.get("controller"),
                        "abilityKind": item.get("abilityKind"),
                        "targets": item.get("targets"),
                        "cantBeCountered": item.get("cantBeCountered"),
                        "card": card,
                    }.items()
                    if value is not None
                }
            )
    decision = view.get("decision")
    compact_decision = None
    if isinstance(decision, dict):
        compact_decision = {
            key: deepcopy(decision.get(key))
            for key in ("id", "kind", "playerId", "choice", "sourceCardInstanceId")
            if decision.get(key) is not None
        }
    return {
        "schemaVersion": view.get("schemaVersion", "mtg-game-session/v1"),
        "revision": view.get("revision"),
        "state": compact_state,
        "decision": compact_decision,
        "matchState": deepcopy(view.get("matchState")),
    }


def _spectator_event_summaries(state: dict[str, Any], after: int) -> list[dict[str, Any]]:
    """Keep visual/audio event identities, never arbitrary private event payloads.

    New events are stored once. A quiet frame repeats only its latest event so
    the renderer's sequence cursor does not appear to seek backwards.
    """
    events = [event for event in state.get("events", []) if isinstance(event, dict)]
    fresh = [
        event
        for event in events
        if isinstance(event.get("sequence"), int) and event["sequence"] > after
    ]
    result = []
    for event in fresh or events[-1:]:
        summary = {
            key: deepcopy(event[key])
            for key in ("sequence", "kind", "turnNumber", "step", "playerId", "cardInstanceId")
            if key in event
        }
        detail = event.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("stackId"), str):
            summary["detail"] = {"stackId": detail["stackId"]}
        result.append(summary)
    return result


def _terminal(view: dict[str, Any]) -> bool:
    state = view.get("state", {})
    if not isinstance(state, dict):
        return False
    match_state = view.get("matchState")
    if isinstance(match_state, dict) and match_state.get("winnerPlayerId"):
        return True
    if view.get("error"):
        return True
    if isinstance(match_state, dict):
        # A Legacy session continues through sideboarding. Do not cancel the
        # remaining rounds when the first game has an outcome but no choice yet.
        if match_state.get("phase") == "sideboarding":
            return False
        if isinstance(state.get("outcome"), dict) and state["outcome"].get("winner"):
            return match_state.get("phase") == "complete"
    status = str(state.get("status", ""))
    return view.get("decision") is None and (
        bool(state.get("outcome")) or status in TERMINAL_STATUSES
    )


def _winner(view: dict[str, Any]) -> str | None:
    match_state = view.get("matchState")
    if isinstance(match_state, dict) and match_state.get("winnerPlayerId"):
        return str(match_state["winnerPlayerId"])
    state = view.get("state", {})
    outcome = state.get("outcome", {}) if isinstance(state, dict) else {}
    return str(outcome["winner"]) if isinstance(outcome, dict) and outcome.get("winner") else None


def _selected_decks(spec: dict[str, Any], randomizer: random.Random) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for seat in spec["seats"]:
        decks = seat.get("decks", [])
        if not isinstance(decks, list) or not decks:
            raise ValueError(f"{seat.get('name', 'An agent')} has no playable deck.")
        selected.append(randomizer.choice(decks) if seat.get("randomDeck") else decks[0])
    return selected


def _selected_match_spec(spec: dict[str, Any], randomizer: random.Random) -> dict[str, Any]:
    """Draw each random seat again, from its frozen eligible agent/deck pool."""
    seats = []
    for seat in spec["seats"]:
        pool = seat.get("agentPool")
        if pool is not None:
            if (
                not isinstance(pool, list)
                or not pool
                or any(not isinstance(item, dict) for item in pool)
            ):
                raise ValueError("A random agent seat has no valid frozen candidate pool.")
            seats.append(dict(randomizer.choice(pool)))
        else:
            seats.append(dict(seat))
    return {**spec, "seats": seats}


def _starting_player(spec: dict[str, Any], game_index: int, randomizer: random.Random) -> int:
    setting = spec.get("startingSeat")
    seats = len(spec["seats"])
    if setting == "random":
        return randomizer.randrange(seats)
    if setting == "rotate":
        return (game_index - 1) % seats
    if type(setting) is int and 1 <= setting <= seats:
        return int(setting) - 1
    return game_index % seats  # Preserve old saved batch specifications.


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def _append_results(root: Path, rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    try:
        write_match_results(
            root, [(seat, row, row.pop("advancedProfile", None)) for seat, row in rows]
        )
    except OSError as error:
        note_storage_error(root, error)
        print(f"DEEPDECK_STATISTICS_STORAGE_ERROR {error}", flush=True)


def _save_replay(
    root: Path,
    batch_id: str,
    game_index: int,
    spec: dict[str, Any],
    decks: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    final_view: dict[str, Any],
    duration_seconds: float | None = None,
    *,
    capture_status: str | None = None,
    capture_error: str | None = None,
    decision_audit: dict[str, Any] | None = None,
) -> str:
    replay_id = f"batch-{batch_id}-{game_index:04d}"
    state = final_view.get("state", {})
    state = state if isinstance(state, dict) else {}
    replay: dict[str, Any] = {
        "schemaVersion": "deepdeck-replay/v1",
        "id": replay_id,
        "createdAtUnixMs": int(time.time() * 1000),
        "metadata": {
            "source": "local-table" if spec.get("interactive") else "local-batch",
            "batchId": batch_id,
            "episode": game_index,
            "competitionId": spec.get("competitionId"),
            "competitionMatchId": spec.get("competitionMatchId"),
            "seed": game_seed(spec, game_index),
            "startingSeat": spec.get("resolvedStartingSeat"),
            "playSettings": spec.get("playSettings"),
            "agentTiming": spec.get("agentTiming", []),
            "decisionAudit": decision_audit or summarize_decisions(frames),
            "captureError": str(capture_error or final_view.get("error") or "")[:2000] or None,
            "matchupId": " vs ".join(str(seat["name"]) for seat in spec["seats"]),
            "decks": [str(deck["id"]) for deck in decks],
            "deckNames": [str(deck.get("name", deck["id"])) for deck in decks],
            "participants": [
                {
                    "name": seat["name"],
                    "kind": seat.get("kind", "agent"),
                    "agentId": seat.get("agentId"),
                    "deckId": deck["id"],
                    "playerId": local_player_ids(
                        item.get("kind", "agent") for item in spec["seats"]
                    )[index],
                }
                for index, (seat, deck) in enumerate(zip(spec["seats"], decks, strict=True))
            ],
            "gameMode": spec["format"],
            "gameStatus": capture_status
            or ("error" if final_view.get("error") else state.get("status", "complete")),
            "roundNumber": (final_view.get("matchState") or {}).get("gameNumber", 1),
            "matchState": deepcopy(final_view.get("matchState")),
            "seatSelection": [
                {
                    **(seat.get("selection") or {}),
                    "selectedAgent": seat["agentId"],
                    "selectedDeck": deck["id"],
                }
                for seat, deck in zip(spec["seats"], decks, strict=True)
            ],
            "gameDurationSeconds": duration_seconds,
            "captureMode": "decision-trace"
            if any(frame.get("selectedAction") for frame in frames)
            else "session-summary",
            "spectatorCapture": spec.get("spectatorCapture", "player-observations"),
            "outcome": {
                "winner": None if capture_status or final_view.get("error") else _winner(final_view)
            },
            "agents": [
                str(seat["agentId"]) for seat in spec["seats"] if seat.get("kind") != "human"
            ],
            "agentVersions": list(
                {
                    str(seat["agentId"]): {
                        "agentId": seat["agentId"],
                        "name": seat["name"],
                        "revision": seat.get("policyRevision"),
                    }
                    for seat in spec["seats"]
                    if seat.get("kind") != "human"
                }.values()
            ),
        },
        "frames": frames,
    }
    saved_copies = 0
    replay["metadata"]["matchSummary"] = replay_match_summary(replay)
    agent_ids = list(
        dict.fromkeys(str(seat["agentId"]) for seat in spec["seats"] if seat.get("kind") != "human")
    )
    replay_roots = [root / ".deepdeck" / "agents" / agent_id for agent_id in agent_ids]
    if not replay_roots:
        replay["metadata"]["source"] = "local-human-table"
        replay_roots = [root / ".deepdeck" / "local-tables"]
    for replay_root in replay_roots:
        replay_path = replay_root / "replays" / f"{replay_id}.json"
        try:
            write_replay(root, replay_path, replay)
            saved_copies += 1
        except ReplayStorageError as error:
            spec["replayStorageError"] = str(error)
            print(f"DEEPDECK_REPLAY_STORAGE_ERROR {error}", flush=True)
    spec["replaySaved"] = saved_copies > 0
    # A recording is independent from a completed match. Publish its handle
    # before any later Engine/statistics error can skip batch progress.
    print(
        "DEEPDECK_RECORDING_STATUS "
        + json.dumps(
            {
                "schemaVersion": "deepdeck-recording-status/v1",
                "batchId": batch_id,
                "gameIndex": game_index,
                "saved": saved_copies > 0,
                "replayId": replay_id if saved_copies else None,
                "gameStatus": replay["metadata"]["gameStatus"],
            }
        ),
        flush=True,
    )
    return replay_id


async def _one_game(
    client: httpx.AsyncClient,
    root: Path,
    batch_id: str,
    game_index: int,
    spec: dict[str, Any],
    runners: dict[str, AgentRunner],
    randomizer: random.Random,
    recorder: DecisionRecorder | None = None,
) -> dict[str, Any]:
    if recorder is not None:
        recorder.reset()
    frames = recorder.frames if recorder is not None else []
    started_at = time.monotonic()
    decks = _selected_decks(spec, randomizer)
    metrics = ProfileCollector(tracking_targets(root, spec["seats"], decks))
    if recorder:
        recorder.metrics = metrics
    starting_player = _starting_player(spec, game_index, randomizer)
    spec = {**spec, "resolvedStartingSeat": starting_player + 1}
    starting_life = 40 if spec["format"] == "commander" else 20
    players = []
    controllers: dict[str, str] = {}
    analytics_decks: dict[str, str] = {}
    analytics_pilots: dict[str, str] = {}
    human_ids = []
    player_ids = local_player_ids(seat.get("kind", "agent") for seat in spec["seats"])
    for index, (seat, deck) in enumerate(zip(spec["seats"], decks, strict=True), start=1):
        player_id = player_ids[index - 1]
        players.append(
            {
                "id": player_id,
                "name": str(seat["name"]),
                "startingLife": starting_life,
                "cards": deck["cards"],
            }
        )
        if seat.get("kind") == "human":
            human_ids.append(player_id)
        else:
            controllers[player_id] = runners[str(seat["agentId"])].controller_id
        analytics_decks[player_id] = str(deck["id"])
        analytics_pilots[player_id] = str(seat["agentId"])
    payload = {
        "setup": {
            "openingHandSize": 7,
            "startingPlayer": starting_player,
            "players": players,
        },
        "seed": game_seed(spec, game_index),
        "gameMode": spec["format"],
        "maxTurns": int(spec.get("maxTurns", 200)),
        "humanPlayerIds": human_ids,
        "aiControllerByPlayerId": controllers,
        "analyticsDeckSessionByPlayerId": analytics_decks,
        "analyticsPilotByPlayerId": analytics_pilots,
        "analyticsContextId": "learner-local",
        "recordAnalytics": False,
        "mulliganEnabled": True,
        "freeMulligans": 1 if spec["format"] == "commander" else 0,
    }
    idle_seconds = spec.get("humanDecisionTimeoutSeconds")
    if human_ids and idle_seconds is not None:
        payload["humanDecisionTimeoutMs"] = int(idle_seconds) * 1000
    response = await client.post("/game/sessions", json=payload)
    if response.is_error:
        try:
            detail = response.json().get("error", response.text)
        except ValueError:
            detail = response.text
        raise ValueError(f"The Engine could not start this table: {str(detail)[:2000]}")
    response.raise_for_status()
    view = response.json()
    if not isinstance(view, dict) or not view.get("sessionId"):
        raise RuntimeError("The Engine returned an invalid game session.")
    session_id = str(view["sessionId"])
    spectator_frames: list[dict[str, Any]] = []
    spectator_cursor = 0
    spectator_event_cursor = 0
    spectator_round: int | None = None
    spectator_supported = True

    async def collect_spectator() -> None:
        nonlocal spectator_cursor, spectator_supported, spectator_event_cursor, spectator_round
        if not spectator_supported:
            return
        try:
            for _ in range(4):
                observed = await client.get(
                    f"/game/sessions/{session_id}/spectator/{spectator_cursor}", timeout=0.5
                )
                if observed.status_code in {403, 404}:
                    spectator_supported = False
                    return
                observed.raise_for_status()
                packet = observed.json()
                if not isinstance(packet, dict) or not isinstance(packet.get("frames", []), list):
                    return
                if packet.get("truncated"):
                    spec["spectatorCaptureGap"] = True
                for item in packet.get("frames", []):
                    if not isinstance(item, dict):
                        continue
                    sequence = item.get("sequence", 0)
                    if (
                        not isinstance(sequence, int)
                        or sequence <= spectator_cursor
                        or not isinstance(item.get("state"), dict)
                    ):
                        continue
                    frame = compact_view(item)
                    round_number = (item.get("matchState") or {}).get("gameNumber")
                    if round_number != spectator_round:
                        spectator_event_cursor = 0
                        spectator_round = round_number
                    frame["state"]["events"] = _spectator_event_summaries(
                        item["state"], spectator_event_cursor
                    )
                    spectator_event_cursor = max(
                        (event.get("sequence", 0) for event in frame["state"]["events"]),
                        default=spectator_event_cursor,
                    )
                    frame.update(
                        spectatorSequence=sequence,
                        selectedAction=deepcopy(item.get("selectedAction")),
                    )
                    spectator_frames.append(frame)
                    spectator_cursor = sequence
                if not packet.get("hasMore"):
                    break
        except (httpx.HTTPError, ValueError, TypeError):
            # Observation must not change policy input, results, or match progress.
            return

    def recorded_frames() -> list[dict[str, Any]]:
        if not spectator_frames:
            return frames
        spec["spectatorCapture"] = "partial" if spec.get("spectatorCaptureGap") else "all-hands"
        decisions: dict[str, list[dict[str, Any]]] = {}
        for frame in frames:
            if isinstance(frame.get("agentDecision"), dict):
                decisions.setdefault(str((frame.get("decision") or {}).get("id")), []).append(frame)
        result = deepcopy(spectator_frames)
        for frame in result:
            candidates = decisions.get(str((frame.get("decision") or {}).get("id")), [])
            if candidates:
                audit = candidates.pop(0)
                for key in ("agentDecision", "strategy", "fallbackReason", "agentResponse"):
                    if key in audit:
                        frame[key] = deepcopy(audit[key])
        # Keep the final match result/sideboard phase even if completion followed
        # the last decision before the observer read the new match state.
        final = compact_view(view)
        final["state"]["events"] = _spectator_event_summaries(
            view.get("state") or {}, spectator_event_cursor
        )
        for player in final["state"]["players"]:
            for zone in ("library", "sideboard"):
                if isinstance(player.get(zone), list):
                    player[zone] = [None] * len(player[zone])
        final["spectatorSequence"] = spectator_cursor + 1
        result.append(final)
        return result

    note_engine_analytics(root, view.get("analyticsRecording") is False)
    print(
        "DEEPDECK_PLAYTEST_SESSION "
        + json.dumps(
            {
                "sessionId": session_id,
                "engineUrl": str(client.base_url).rstrip("/"),
                "deckVersionIds": [deck["id"] for deck in decks],
                "deckNames": [deck["name"] for deck in decks],
                "participantNames": [seat["name"] for seat in spec["seats"]],
                "sessionStatus": "playing",
                "humanPlayerIds": human_ids,
                "currentMatchIndex": game_index,
                "matchup": f"{len(decks)} players · "
                + " vs ".join(dict.fromkeys(deck["name"] for deck in decks)),
            }
        ),
        flush=True,
    )
    last_revision: int | None = None
    interrupted = False
    stop_path = root / ".deepdeck" / "batches" / batch_id / "stop.request"
    timeout_seconds = spec.get("gameTimeoutSeconds", 600)
    deadline = (
        None
        if timeout_seconds is None
        else asyncio.get_running_loop().time() + float(timeout_seconds)
    )
    try:
        while True:
            await collect_spectator()
            metrics.observe(view)
            revision = int(view.get("revision", 0) or 0)
            if revision != last_revision and (
                recorder is None or view.get("decision") or _terminal(view) or not frames
            ):
                frames.append(compact_view(view))
                last_revision = revision
            if _terminal(view):
                break
            if stop_path.is_file():
                interrupted = True
                break
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("The local table reached its configured match safety limit.")
            await asyncio.sleep(
                0.25 if (view.get("decision") or {}).get("playerId") in human_ids else 0.05
            )
            response = await client.get(f"/game/sessions/{session_id}")
            response.raise_for_status()
            view = response.json()
        replay_id = _save_replay(
            root,
            batch_id,
            game_index,
            spec,
            decks,
            recorded_frames(),
            view,
            time.monotonic() - started_at,
            capture_status="interrupted" if interrupted else None,
            decision_audit=recorder.summary() if recorder else None,
        )
        available_replay_id = replay_id if spec.get("replaySaved", True) else None
        if interrupted:
            # No result rows: a closed table is not a draw, loss or completed
            # match, even if one round of its best-of-three had already ended.
            return {
                "sessionId": session_id,
                "replayId": available_replay_id,
                "winner": None,
                "decks": [deck["id"] for deck in decks],
                "status": "stopped",
            }
        if view.get("error"):
            recording = (
                f"replay saved as {available_replay_id}"
                if available_replay_id
                else "replay could not be saved"
            )
            raise RuntimeError(f"Engine match failed; {recording}: {view['error']}")
        winner = _winner(view)
        profiles = metrics.finish(time.monotonic() - started_at, recorded_frames())
        result_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for index, (seat, deck) in enumerate(zip(spec["seats"], decks, strict=True), start=1):
            player_id = player_ids[index - 1]
            result_rows.append(
                (
                    seat,
                    {
                        "schemaVersion": "deepdeck-local-result/v1",
                        "createdAt": datetime.now(timezone.utc).isoformat(),
                        "batchId": batch_id,
                        "replayId": available_replay_id,
                        "gameIndex": game_index,
                        "format": spec["format"],
                        "agentId": seat["agentId"],
                        "playerId": player_id,
                        "advancedProfile": profiles.get(player_id),
                        "agentRevision": seat.get("policyRevision"),
                        "decisionAudit": recorder.summary()
                        if recorder
                        else summarize_decisions(frames),
                        "actorKind": "player" if seat.get("kind") == "human" else "agent",
                        "actorName": seat["name"],
                        "deckVersionId": deck["id"],
                        "opponents": [
                            other["agentId"] for other in spec["seats"] if other is not seat
                        ],
                        "result": (
                            "win" if winner == player_id else "draw" if winner is None else "loss"
                        ),
                        "winnerPlayerId": winner,
                        "turnNumber": (view.get("state") or {}).get("turnNumber"),
                    },
                )
            )
        _append_results(root, result_rows)
        return {
            "sessionId": session_id,
            "replayId": available_replay_id,
            "replayStorageError": spec.get("replayStorageError"),
            "winner": winner,
            "decks": [deck["id"] for deck in decks],
            "agents": [seat["agentId"] for seat in spec["seats"]],
            "seed": game_seed(spec, game_index),
            "winsByPlayerId": (view.get("matchState") or {}).get("winsByPlayerId"),
        }
    except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as error:
        interrupted = True
        _save_replay(
            root,
            batch_id,
            game_index,
            spec,
            decks,
            recorded_frames(),
            view,
            time.monotonic() - started_at,
            capture_status="timeout"
            if isinstance(error, TimeoutError)
            else "interrupted"
            if isinstance(error, asyncio.CancelledError)
            else "error",
            capture_error=str(error) or "The local table was interrupted.",
            decision_audit=recorder.summary() if recorder else None,
        )
        raise
    finally:
        if not spec.get("interactive") or interrupted:
            with contextlib.suppress(httpx.HTTPError):
                await client.delete(f"/game/sessions/{session_id}")


async def run(arguments: argparse.Namespace) -> None:
    root = arguments.root.resolve()
    spec = _read_object(arguments.spec.resolve(), label="batch specification")
    seats = spec.get("seats")
    if not isinstance(seats, list) or not 2 <= len(seats) <= 4:
        raise ValueError("A local AI batch requires between two and four seats.")
    batch_id = str(spec.get("id") or arguments.spec.parent.name)
    recorder = DecisionRecorder()
    runners: dict[str, Any] = {}
    serve_tasks: list[asyncio.Task[Any]] = []
    requested_rhythm = str(spec.get("aiSpeed", "1ms"))
    local_interval_ms = {"1ms": 1, "10ms": 10, "100ms": 100}.get(requested_rhythm, 0)
    transport_speed = PlaySpeed.MS_100 if local_interval_ms else PlaySpeed(requested_rhythm)

    async def connect_selected(match_spec: dict[str, Any]) -> None:
        missing = []
        for seat in match_spec["seats"]:
            if seat.get("kind") == "human" or str(seat["agentId"]) in runners:
                continue
            runner = _runner(
                Path(str(seat["policyPath"])),
                arguments.engine_url,
                recorder,
                namespace=batch_id,
                speed=transport_speed,
                decision_interval_ms=local_interval_ms,
            )
            runners[str(seat["agentId"])] = runner
            serve_tasks.append(asyncio.create_task(runner.serve()))
            missing.append(runner.wait_until_connected(timeout=30))
        await asyncio.gather(*missing)
        match_spec["agentTiming"] = [
            {
                "agentId": agent_id,
                "controllerId": runner.controller_id,
                "requestedSpeed": requested_rhythm,
                "localDecisionIntervalMs": local_interval_ms,
                "transportSpeed": runner.speed.value if runner.speed else transport_speed.value,
                "decisionTimeoutMs": registration.decision_timeout_ms,
                "startingAnalysisTimeoutMs": registration.starting_analysis_timeout_ms,
            }
            for agent_id in dict.fromkeys(
                str(seat["agentId"]) for seat in match_spec["seats"] if seat.get("kind") != "human"
            )
            if (runner := runners[agent_id]) is not None
            and (registration := getattr(getattr(runner, "client", None), "registration", None))
            is not None
        ]

    try:
        randomizer = random.Random(int(spec["seed"]))
        completed: list[dict[str, Any]] = []
        interrupted_game: dict[str, Any] | None = None
        stop_path = arguments.spec.parent / "stop.request"
        async with httpx.AsyncClient(
            base_url=arguments.engine_url.rstrip("/"), timeout=45
        ) as client:
            for game_index in range(1, int(spec["games"]) + 1):
                if stop_path.is_file():
                    break
                if spec.get("selectionVersion") == "per-match/v1":
                    # Selection and shuffling for one match can be reproduced
                    # from the seed recorded in that match's replay.
                    randomizer = random.Random(game_seed(spec, game_index))
                match_spec = _selected_match_spec(spec, randomizer)
                await connect_selected(match_spec)
                result = await _one_game(
                    client, root, batch_id, game_index, match_spec, runners, randomizer, recorder
                )
                if result.get("status") == "stopped":
                    interrupted_game = result
                else:
                    completed.append(result)
                print(
                    "DEEPDECK_BATCH_PROGRESS "
                    + json.dumps(
                        {
                            "batchId": batch_id,
                            "completed": len(completed),
                            "total": int(spec["games"]),
                            **result,
                        }
                    ),
                    flush=True,
                )
                if interrupted_game:
                    break
        summary = {
            "schemaVersion": "deepdeck-local-batch/v1",
            "id": batch_id,
            "completed": len(completed),
            "total": int(spec["games"]),
            "games": completed,
            "status": "stopped" if stop_path.is_file() else "completed",
            "interruptedGame": interrupted_game,
            "finishedAt": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(arguments.spec.parent / "summary.json", summary)
    finally:
        for task in serve_tasks:
            task.cancel()
        await asyncio.gather(*serve_tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
