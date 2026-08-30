from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from oracle_ai.training.behavior import (
    DecisionTrace,
    build_decision_trace,
    summarize_decision_traces,
)
from oracle_ai.training.environments import Matchup, RustSelfPlayEnvironment
from oracle_ai.training.gameplay_metrics import round_number

SCHEMA_VERSION = "ai-decision/v1"


def analytics_pilot_for_champion(
    champion_version: str,
    initial_ground_truth_pilot_id: str | None,
) -> str:
    if champion_version == "ia-gt-0" and initial_ground_truth_pilot_id:
        return initial_ground_truth_pilot_id
    return champion_version


@dataclass(frozen=True)
class EvaluationScenario:
    matchup: Matchup
    candidate_player_id: str
    candidate_deck: str


class PolicyHttpClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        controller_id: str | None = None,
    ) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self.controller_id = controller_id

    def close(self) -> None:
        self.client.close()

    def health(self) -> dict[str, Any]:
        response = self.client.get("/health")
        response.raise_for_status()
        return response.json()

    def choose(
        self,
        state: dict[str, Any],
        decision: dict[str, Any],
        context_id: str | None = None,
    ) -> tuple[int, float]:
        index, latency, _ = self.choose_detailed(state, decision, context_id)
        return index, latency

    def choose_detailed(
        self,
        state: dict[str, Any],
        decision: dict[str, Any],
        context_id: str | None = None,
    ) -> tuple[int, float, dict[str, Any]]:
        started = time.perf_counter()
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "requestId": decision["id"],
            "playerId": decision["playerId"],
            "state": state,
            "decision": decision,
            "deterministic": True,
        }
        if self.controller_id is not None:
            payload["controllerId"] = self.controller_id
        if context_id is not None:
            payload["contextId"] = context_id
        response = self.client.post(
            "/v1/decisions",
            json=payload,
        )
        response.raise_for_status()
        payload = response.json()
        action_id = payload["actionId"]
        for index, option in enumerate(decision["options"]):
            if option["id"] == action_id:
                return index, time.perf_counter() - started, payload
        raise RuntimeError(f"policy returned unpublished action {action_id}")


class PolicyService:
    def __init__(
        self,
        port: int,
        model_name: str,
        log_dir: Path,
        device: str,
        checkpoint: Path | None = None,
        registry: Path | None = None,
        random_seed: int = 0,
    ) -> None:
        self.port = port
        self.model_name = model_name
        self.log_dir = log_dir
        self.device = device
        self.checkpoint = checkpoint
        self.registry = registry
        self.random_seed = random_seed
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout_seconds: float = 60.0) -> dict[str, Any]:
        if self.process is not None:
            raise RuntimeError(f"policy service {self.model_name} is already running")
        try:
            response = httpx.get(f"{self.url}/health", timeout=1.0)
            if response.status_code == 200:
                health = response.json()
                if self.registry is not None:
                    if health.get("registryPath") != str(self.registry.resolve()):
                        raise RuntimeError(
                            f"port {self.port} hosts a different model registry"
                        )
                    catalog = httpx.get(f"{self.url}/v1/models", timeout=2.0)
                    catalog.raise_for_status()
                    model_ids = {
                        str(model["id"])
                        for model in catalog.json().get("models", [])
                    }
                    if self.model_name in model_ids:
                        return health
                elif self.checkpoint is not None:
                    if health.get("checkpointPath") != str(self.checkpoint.resolve()):
                        raise RuntimeError(
                            f"port {self.port} hosts a different model checkpoint"
                        )
                    if health.get("model") == self.model_name:
                        return health
                elif health.get("model") == self.model_name:
                    return health
                raise RuntimeError(
                    f"port {self.port} hosts {health.get('model', 'another policy')}"
                )
        except httpx.RequestError:
            pass

        package_root = Path(__file__).resolve().parents[2]
        environment = os.environ.copy()
        environment["ORACLE_AI_DEVICE"] = self.device
        environment["ORACLE_AI_MODEL_NAME"] = self.model_name
        if self.registry is not None:
            environment["ORACLE_AI_POLICY"] = "model"
            environment["ORACLE_AI_MODEL_REGISTRY"] = str(self.registry.resolve())
            environment.pop("ORACLE_AI_CHECKPOINT", None)
        elif self.checkpoint is None:
            environment["ORACLE_AI_POLICY"] = "random"
            environment["ORACLE_AI_RANDOM_SEED"] = str(self.random_seed)
            environment.pop("ORACLE_AI_CHECKPOINT", None)
            environment.pop("ORACLE_AI_MODEL_REGISTRY", None)
        else:
            environment["ORACLE_AI_POLICY"] = "model"
            environment["ORACLE_AI_CHECKPOINT"] = str(self.checkpoint.resolve())
            environment.pop("ORACLE_AI_MODEL_REGISTRY", None)

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_handle = (self.log_dir / f"{self.model_name}.log").open(
            "a",
            encoding="utf-8",
        )
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "oracle_ai.app:app",
                "--app-dir",
                str(package_root),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            env=environment,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creation_flags,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"policy service {self.model_name} exited with {self.process.returncode}"
                )
            try:
                response = httpx.get(f"{self.url}/health", timeout=2.0)
                if response.status_code == 200:
                    return response.json()
            except httpx.RequestError:
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"policy service {self.model_name} did not become healthy")

    def reload(self, timeout_seconds: float = 60.0) -> dict[str, Any]:
        if self.process is not None and self.process.poll() is not None:
            raise RuntimeError(f"policy service {self.model_name} is not running")
        response = httpx.post(f"{self.url}/v1/reload", timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
            self.process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def __enter__(self) -> PolicyService:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


class EvaluationRunner:
    def __init__(
        self,
        engine_url: str,
        scenarios: dict[str, EvaluationScenario],
        timeout_seconds: float,
        candidate_pilot_id: str = "ia-in-training",
        initial_ground_truth_pilot_id: str | None = None,
    ) -> None:
        self.scenarios = scenarios
        self.candidate_pilot_id = candidate_pilot_id
        self.initial_ground_truth_pilot_id = initial_ground_truth_pilot_id
        self.environment = RustSelfPlayEnvironment(
            engine_url,
            {scenario_id: scenario.matchup for scenario_id, scenario in scenarios.items()},
            timeout_seconds,
        )
        self.environment.analytics_context_id = "multi-model-evaluation"
        self.candidate_traces: list[DecisionTrace] = []

    def close(self) -> None:
        self.environment.close()

    def run_game(
        self,
        scenario_id: str,
        seed: int,
        candidate: PolicyHttpClient,
        champion: PolicyHttpClient,
        champion_version: str,
    ) -> dict[str, Any]:
        scenario = self.scenarios[scenario_id]
        started = time.perf_counter()
        candidate_latencies: list[float] = []
        champion_latencies: list[float] = []
        candidate_decisions = 0
        champion_decisions = 0
        candidate_traces: list[DecisionTrace] = []
        self.environment.analytics_pilot_override = {
            player["id"]: (
                self.candidate_pilot_id
                if player["id"] == scenario.candidate_player_id
                else analytics_pilot_for_champion(
                    champion_version,
                    self.initial_ground_truth_pilot_id,
                )
            )
            for player in scenario.matchup.setup.get("players", [])
        }
        step = self.environment.reset(scenario_id, seed, seat_swap=False)
        while not step.done:
            decision = self.environment.current_view["decision"]
            state = self.environment.current_view["state"]
            policy_state = dict(state)
            policy_state["_decisionContext"] = {
                "id": decision.get("id"),
                "playerId": decision.get("playerId"),
                "kind": decision.get("kind"),
                "freeMulligans": scenario.matchup.free_mulligans,
                "maxMulligans": scenario.matchup.max_mulligans,
            }
            is_candidate = step.player_id == scenario.candidate_player_id
            client = candidate if is_candidate else champion
            action_index, latency, policy = client.choose_detailed(
                policy_state,
                decision,
                f"{self.environment.session_id}:{decision['playerId']}",
            )
            if is_candidate:
                candidate_decisions += 1
                candidate_latencies.append(latency)
                candidate_traces.append(
                    build_decision_trace(
                        state,
                        decision["options"],
                        action_index,
                        decision=decision,
                        confidence=policy.get("confidence"),
                        entropy=policy.get("policyEntropy"),
                        free_mulligans=scenario.matchup.free_mulligans,
                    )
                )
            else:
                champion_decisions += 1
                champion_latencies.append(latency)
            step = self.environment.step(action_index)

        state = self.environment.current_view["state"]
        outcome = state.get("outcome") or {}
        winner = outcome.get("winner")
        if winner == scenario.candidate_player_id:
            result = "candidateWin"
        elif winner:
            result = "championWin"
        else:
            result = "draw"
        players = scenario.matchup.setup.get("players", [])
        candidate_player = next(
            player for player in players if player["id"] == scenario.candidate_player_id
        )
        self.candidate_traces.extend(candidate_traces)
        return {
            "scenarioId": scenario_id,
            "seed": seed,
            "gameMode": scenario.matchup.game_mode,
            "players": len(players),
            "candidatePlayerId": scenario.candidate_player_id,
            "candidateSeat": next(
                index
                for index, player in enumerate(players)
                if player["id"] == scenario.candidate_player_id
            ),
            "candidateDeck": scenario.candidate_deck,
            "candidatePlayerName": candidate_player.get("name"),
            "startingPlayer": scenario.matchup.setup.get("startingPlayer"),
            "championVersion": champion_version,
            "result": result,
            "winner": winner,
            "turnNumber": state.get("turnNumber"),
            "roundNumber": round_number(
                scenario.matchup.setup,
                int(state.get("turnNumber") or 0),
            ),
            "gameStatus": state.get("status"),
            "outcomeReason": outcome.get("reason"),
            "candidateDecisions": candidate_decisions,
            "championDecisions": champion_decisions,
            "candidateMeanDecisionMs": (
                statistics.fmean(candidate_latencies) * 1000.0
                if candidate_latencies
                else None
            ),
            "candidateBehavior": summarize_decision_traces(candidate_traces),
            "championMeanDecisionMs": (
                statistics.fmean(champion_latencies) * 1000.0
                if champion_latencies
                else None
            ),
            "gameSeconds": time.perf_counter() - started,
        }


def summarize_evaluation(
    games: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_wins = [game for game in games if game["result"] == "candidateWin"]
    champion_wins = [game for game in games if game["result"] == "championWin"]
    draws = [game for game in games if game["result"] == "draw"]
    win_rounds = [
        game["roundNumber"]
        for game in candidate_wins
        if game["roundNumber"] is not None
    ]
    completed = len(games)
    expected = completed + len(errors)
    by_scenario: dict[str, dict[str, Any]] = {}
    for scenario_id in sorted({game["scenarioId"] for game in games}):
        scenario_games = [game for game in games if game["scenarioId"] == scenario_id]
        scenario_wins = sum(game["result"] == "candidateWin" for game in scenario_games)
        by_scenario[scenario_id] = {
            "games": len(scenario_games),
            "candidateWins": scenario_wins,
            "candidateWinRate": scenario_wins / len(scenario_games),
            "meanRounds": statistics.fmean(
                game["roundNumber"] for game in scenario_games
            ),
            "meanRoundsToCandidateWin": (
                statistics.fmean(
                    game["roundNumber"]
                    for game in scenario_games
                    if game["result"] == "candidateWin"
                )
                if scenario_wins
                else None
            ),
        }
    by_format: dict[str, dict[str, Any]] = {}
    for game_mode in sorted({game.get("gameMode", "free") for game in games}):
        format_games = [
            game for game in games if game.get("gameMode", "free") == game_mode
        ]
        format_wins = sum(game["result"] == "candidateWin" for game in format_games)
        by_format[game_mode] = {
            "games": len(format_games),
            "candidateWins": format_wins,
            "candidateWinRate": format_wins / len(format_games),
            "draws": sum(game["result"] == "draw" for game in format_games),
            "meanRounds": statistics.fmean(
                game["roundNumber"] for game in format_games
            ),
        }
    return {
        "expectedGames": expected,
        "completedGames": completed,
        "errors": len(errors),
        "candidateWins": len(candidate_wins),
        "championWins": len(champion_wins),
        "draws": len(draws),
        "candidateWinRate": len(candidate_wins) / expected if expected else 0.0,
        "perfect": expected > 0 and len(candidate_wins) == expected and not errors,
        "meanRounds": (
            statistics.fmean(game["roundNumber"] for game in games) if games else None
        ),
        "meanRoundsToCandidateWin": statistics.fmean(win_rounds) if win_rounds else None,
        "medianRoundsToCandidateWin": statistics.median(win_rounds) if win_rounds else None,
        "minRoundsToCandidateWin": min(win_rounds) if win_rounds else None,
        "maxRoundsToCandidateWin": max(win_rounds) if win_rounds else None,
        "totalGameSeconds": sum(game["gameSeconds"] for game in games),
        "byFormat": by_format,
        "byScenario": by_scenario,
    }
