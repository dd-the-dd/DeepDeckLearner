from __future__ import annotations

import random
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx

from oracle_ai.decision_choices import expand_policy_actions
from oracle_ai.training.core import DecisionStep
from oracle_ai.training.plackett_luce import (
    PlackettLuceRating,
    ordered_finishers,
    rank_gradient_rewards,
)


@dataclass(frozen=True)
class Matchup:
    id: str
    setup: dict[str, Any]
    learner_player_id: str
    opponent_player_id: str
    max_turns: int = 200
    mulligan_enabled: bool = False
    free_mulligans: int = 0
    max_mulligans: int | None = None
    game_mode: str = "free"
    deck_names: tuple[str, ...] = ()
    deck_session_ids: tuple[str, ...] = ()
    punching_bag_player_ids: tuple[str, ...] = ()
    training_anchor_player_ids: tuple[str, ...] = ()
    anchor_deadline_round: int | None = None
    anchor_opening_hand_pool_size: int | None = None


class RustSessionEnvironment:
    """Gym-like adapter over authoritative Rust `/game/sessions` endpoints.

    The setup is supplied by a versioned matchup manifest. Rust advances all
    non-learner players. The learner receives only the session projection and
    legal options published for its current decision.
    """

    def __init__(
        self,
        base_url: str,
        matchups: dict[str, Matchup],
        timeout_seconds: float = 30.0,
        learner_pilot_id: str = "ia-in-training",
    ) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_seconds)
        self.wait_timeout_ms = max(
            1_000,
            min(600_000, int(timeout_seconds * 1000) - 1_000),
        )
        self.matchups = matchups
        self.session_id: str | None = None
        self.current_view: dict[str, Any] | None = None
        self.learner_player_id: str | None = None
        self.current_matchup: Matchup | None = None
        self.known_decks_by_player_id: dict[str, list[dict[str, Any]]] = {}
        self.pregame_commanders: list[dict[str, Any]] = []
        self.previous_observations_by_player_id: dict[str, dict[str, Any]] = {}
        self.learner_pilot_id = learner_pilot_id
        self.analytics_context_id = f"training:{learner_pilot_id}"
        self.analytics_pilot_override: dict[str, str] | None = None
        self.progress_callback: Callable[[dict[str, Any]], None] | None = None

    def _report_progress(self, view: dict[str, Any]) -> None:
        if self.progress_callback is not None:
            self.progress_callback(view)

    def _remove_session(self) -> None:
        if self.session_id is None:
            return
        session_id = self.session_id
        self.session_id = None
        response = self.client.delete(f"/game/sessions/{session_id}")
        if response.status_code != 404:
            response.raise_for_status()

    def _discard_failed_session(self) -> None:
        try:
            self._remove_session()
        except httpx.HTTPError:
            self.session_id = None

    def close(self) -> None:
        self._remove_session()
        self.client.close()

    def _known_deck(self, player_id: str | None) -> list[dict[str, Any]]:
        if self.current_matchup is None or player_id is None:
            return []
        if player_id in self.known_decks_by_player_id:
            return list(self.known_decks_by_player_id[player_id])
        return next(
            (
                list(player.get("cards", []))
                for player in self.current_matchup.setup.get("players", [])
                if player.get("id") == player_id
                and isinstance(player.get("cards"), list)
            ),
            [],
        )

    def _capture_known_decks(self, view: dict[str, Any]) -> None:
        known_decks: dict[str, list[dict[str, Any]]] = {}
        commanders: list[dict[str, Any]] = []
        for player in view.get("state", {}).get("players", []):
            player_id = player.get("id")
            if not player_id:
                continue
            definitions: list[dict[str, Any]] = []
            seen_instances: set[str] = set()
            for zone_name in (
                "library",
                "hand",
                "battlefield",
                "graveyard",
                "exile",
                "commandZone",
            ):
                for card in player.get(zone_name, []):
                    instance_id = str(card.get("instanceId", ""))
                    definition = card.get("definition")
                    if (
                        instance_id
                        and instance_id not in seen_instances
                        and isinstance(definition, dict)
                    ):
                        seen_instances.add(instance_id)
                        definitions.append(definition)
            known_decks[str(player_id)] = definitions
            for definition in definitions:
                if bool(definition.get("isCommander")):
                    commanders.append(
                        {
                            "playerId": str(player_id),
                            "card": definition,
                        }
                    )
        self.known_decks_by_player_id = known_decks
        self.pregame_commanders = commanders

    @staticmethod
    def _observation_snapshot(state: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(
            {
                key: value
                for key, value in state.items()
                if not str(key).startswith("_")
            }
        )

    def _decorate_observation(
        self,
        state: dict[str, Any],
        player_id: str | None,
    ) -> dict[str, Any]:
        if player_id is None:
            return state
        state["_knownDeck"] = self._known_deck(player_id)
        state["_pregameDeck"] = self._known_deck(player_id)
        state["_pregameCommanders"] = list(self.pregame_commanders)
        previous = self.previous_observations_by_player_id.get(player_id)
        if previous is not None:
            state["_previousObservation"] = previous
        self.previous_observations_by_player_id[player_id] = self._observation_snapshot(state)
        return state

    def _decision_context(self, decision: dict[str, Any]) -> dict[str, Any]:
        matchup = self.current_matchup
        free_mulligans = matchup.free_mulligans if matchup else 0
        maximum_mulligans = matchup.max_mulligans if matchup else None
        mulligans_taken: int | None = None
        if str(decision.get("kind", "")).casefold() in {
            "mulligan",
            "mulliganbottom",
        }:
            try:
                mulligans_taken = int(str(decision.get("id", "")).rsplit(":", 1)[-1])
            except ValueError:
                mulligans_taken = 0
        return {
            "id": decision.get("id"),
            "playerId": decision.get("playerId"),
            "kind": decision.get("kind"),
            "gameMode": matchup.game_mode if matchup else None,
            "mulliganEnabled": matchup.mulligan_enabled if matchup else False,
            "openingHandSize": (
                int(matchup.setup.get("openingHandSize", 7)) if matchup else 7
            ),
            "freeMulligans": free_mulligans,
            "maxMulligans": maximum_mulligans,
            "mulligansTaken": mulligans_taken,
            "freeMulligansRemaining": (
                max(0, free_mulligans - mulligans_taken)
                if mulligans_taken is not None
                else None
            ),
            "paidMulligansTaken": (
                max(0, mulligans_taken - free_mulligans)
                if mulligans_taken is not None
                else None
            ),
            "mulligansRemaining": (
                max(0, maximum_mulligans - mulligans_taken)
                if maximum_mulligans is not None and mulligans_taken is not None
                else None
            ),
        }

    def _to_step(self, view: dict[str, Any], reward: float = 0.0) -> DecisionStep:
        decision = view.get("decision")
        error = view.get("error")
        if error:
            self._remove_session()
            raise RuntimeError(f"Rust session failed: {error}")
        self._report_progress(view)
        terminal = decision is None
        if terminal:
            state = view.get("state", {})
            if view.get("matchState") is not None:
                state = dict(state)
                state["_matchState"] = view.get("matchState")
            outcome = state.get("outcome") or {}
            winners = set(state.get("winnerIds", []))
            if outcome.get("winner"):
                winners.add(outcome["winner"])
            losers = set(outcome.get("losers", []))
            reward = (
                1.0
                if self.learner_player_id in winners
                else (-1.0 if self.learner_player_id in losers else 0.0)
            )
            step = DecisionStep(view.get("state", {}), [], reward, True)
            self._remove_session()
            return step
        if decision.get("playerId") != self.learner_player_id:
            raise RuntimeError(
                "Rust returned a decision for a player not controlled by this learner; "
                "configure every other player as ai-random or a remote opponent"
            )
        state = dict(view.get("state", {}))
        state["_decisionContext"] = self._decision_context(decision)
        state = self._decorate_observation(state, decision.get("playerId"))
        return DecisionStep(
            state,
            expand_policy_actions(decision),
            reward,
            False,
            decision.get("playerId"),
        )

    def reset(self, matchup_id: str, seed: int, seat_swap: bool) -> DecisionStep:
        self._remove_session()
        self.known_decks_by_player_id = {}
        self.pregame_commanders = []
        self.previous_observations_by_player_id = {}
        matchup = self.matchups[matchup_id]
        self.current_matchup = matchup
        setup = dict(matchup.setup)
        if seat_swap:
            players = list(setup.get("players", []))
            setup["players"] = list(reversed(players))
        deck_session_ids = list(matchup.deck_session_ids)
        if seat_swap:
            deck_session_ids.reverse()
        self.learner_player_id = matchup.learner_player_id
        player_ids = [player["id"] for player in setup.get("players", [])]
        analytics_deck_sessions = {
            player_id: deck_session_ids[index]
            for index, player_id in enumerate(player_ids)
            if index < len(deck_session_ids) and deck_session_ids[index]
        }
        analytics_pilots = self.analytics_pilot_override or {
            player_id: (
                self.learner_pilot_id
                if player_id == self.learner_player_id
                else "ai-random"
            )
            for player_id in player_ids
        }
        response = self.client.post(
            "/game/sessions",
            json={
                "setup": setup,
                "seed": seed,
                "gameMode": matchup.game_mode,
                "maxTurns": matchup.max_turns,
                "mulliganEnabled": matchup.mulligan_enabled,
                "freeMulligans": matchup.free_mulligans,
                "maxMulligans": matchup.max_mulligans,
                "waitTimeoutMs": self.wait_timeout_ms,
                "humanPlayerIds": [self.learner_player_id],
                "combatDeclarationRevisionPlayerIds": [],
                "holdPriorityPlayerIds": [],
                "analyticsContextId": self.analytics_context_id,
                "analyticsPilotByPlayerId": analytics_pilots,
                "analyticsDeckSessionByPlayerId": analytics_deck_sessions,
                "punchingBagPlayerIds": list(matchup.punching_bag_player_ids),
                "openingHandSelectionPoolSizeByPlayerId": (
                    {
                        self.learner_player_id: matchup.anchor_opening_hand_pool_size,
                    }
                    if matchup.anchor_opening_hand_pool_size is not None
                    else {}
                ),
                "trainingAnchorDeadlineRoundByPlayerId": {
                    player_id: matchup.anchor_deadline_round
                    for player_id in matchup.training_anchor_player_ids
                    if matchup.anchor_deadline_round is not None
                },
            },
        )
        response.raise_for_status()
        self.current_view = response.json()
        self._capture_known_decks(self.current_view)
        self.session_id = self.current_view["sessionId"]
        return self._to_step(self.current_view)

    def step(self, action_index: int) -> DecisionStep:
        if self.current_view is None or self.session_id is None:
            raise RuntimeError("environment must be reset before step")
        decision = self.current_view["decision"]
        options = expand_policy_actions(decision)
        if action_index < 0 or action_index >= len(options):
            raise IndexError("selected action index is outside the current legal action list")
        try:
            selected_action = options[action_index]
            submission = {
                "revision": self.current_view["revision"],
                "decisionId": decision["id"],
                "actionId": selected_action.get(
                    "_engineActionId",
                    selected_action["id"],
                ),
            }
            if "_numberValue" in selected_action:
                submission["numberValue"] = selected_action["_numberValue"]
            response = self.client.post(
                f"/game/sessions/{self.session_id}/actions",
                json=submission,
            )
        except httpx.HTTPError:
            self._discard_failed_session()
            raise
        if response.is_error:
            selected_action = options[action_index]
            error_text = response.text
            self._discard_failed_session()
            raise RuntimeError(
                "Rust rejected a published legal action "
                f"(status={response.status_code}, revision={self.current_view['revision']}, "
                f"decision={decision.get('id')}, decisionKind={decision.get('kind')}, "
                f"action={selected_action.get('id')}, actionKind={selected_action.get('kind')}): "
                f"{error_text}"
            )
        self.current_view = response.json()
        return self._to_step(self.current_view)


class RustSelfPlayEnvironment(RustSessionEnvironment):
    """Rust session adapter where the shared learner controls every player."""

    def __init__(
        self,
        base_url: str,
        matchups: dict[str, Matchup],
        timeout_seconds: float = 30.0,
        multiplayer_reward_mode: str = "winnerLoser",
        learner_pilot_id: str = "ia-in-training",
        no_winner_reward: float = 0.0,
        legacy_game_win_reward: float = 0.25,
        legacy_match_win_reward: float = 1.0,
        scale_rewards_by_plackett_luce: bool = False,
    ) -> None:
        super().__init__(base_url, matchups, timeout_seconds, learner_pilot_id)
        if multiplayer_reward_mode not in {
            "winnerLoser",
            "centeredWinner",
            "plackettLuce",
            "alphaStarTwoPlayer",
        }:
            raise ValueError("unsupported multiplayer reward mode")
        self.multiplayer_reward_mode = multiplayer_reward_mode
        self.no_winner_reward = float(no_winner_reward)
        self.plackett_luce_ratings_by_player_id: dict[str, PlackettLuceRating] = {}
        self.participant_by_player_id: dict[str, str] = {}
        self.plackett_luce_participant_by_player_id: dict[str, str] = {}
        self.legacy_game_win_reward = float(legacy_game_win_reward)
        self.legacy_match_win_reward = float(legacy_match_win_reward)
        self.scale_rewards_by_plackett_luce = bool(scale_rewards_by_plackett_luce)
        self._match_wins_by_player_id: dict[str, int] = {}
        self._match_reward_emitted = False

    def _two_player_result_rewards(
        self,
        player_ids: list[str],
        winner_id: str,
        reward: float,
    ) -> dict[str, float]:
        loser_id = next(player_id for player_id in player_ids if player_id != winner_id)
        if not self.scale_rewards_by_plackett_luce:
            return {winner_id: reward, loser_id: -reward}

        # Ratings are intentionally keyed by seats here. A mirror can map both
        # seats to the same leaderboard entry, whose aggregate rating update is
        # zero, while its two trajectories still need opposite learning signals.
        seat_ratings = {
            player_id: self.plackett_luce_ratings_by_player_id.get(
                player_id,
                PlackettLuceRating(),
            )
            for player_id in player_ids
        }
        gradients = rank_gradient_rewards(
            [winner_id, loser_id],
            seat_ratings,
        )
        return {player_id: reward * gradients[player_id] for player_id in player_ids}

    def _legacy_boundary_rewards(
        self,
        view: dict[str, Any],
        player_ids: list[str],
    ) -> dict[str, float]:
        if self.multiplayer_reward_mode != "alphaStarTwoPlayer":
            return {}
        if len(player_ids) != 2:
            raise RuntimeError("alphaStarTwoPlayer requires exactly two players")
        match_state = view.get("matchState") or {}
        wins = match_state.get("winsByPlayerId") or {}
        rewards = {player_id: 0.0 for player_id in player_ids}
        for winner_id in player_ids:
            won_games = max(
                0,
                int(wins.get(winner_id, 0))
                - int(self._match_wins_by_player_id.get(winner_id, 0)),
            )
            if not won_games:
                continue
            delta = self.legacy_game_win_reward * won_games
            for player_id, scaled_reward in self._two_player_result_rewards(
                player_ids,
                winner_id,
                delta,
            ).items():
                rewards[player_id] += scaled_reward
        self._match_wins_by_player_id = {
            player_id: int(wins.get(player_id, 0)) for player_id in player_ids
        }
        match_winner = match_state.get("winnerPlayerId")
        if (
            match_state.get("phase") == "complete"
            and match_winner in player_ids
            and not self._match_reward_emitted
        ):
            for player_id, scaled_reward in self._two_player_result_rewards(
                player_ids,
                match_winner,
                self.legacy_match_win_reward,
            ).items():
                rewards[player_id] += scaled_reward
            self._match_reward_emitted = True
        return {player_id: reward for player_id, reward in rewards.items() if reward}

    def _to_step(self, view: dict[str, Any], reward: float = 0.0) -> DecisionStep:
        decision = view.get("decision")
        error = view.get("error")
        self._report_progress(view)
        if error:
            self._remove_session()
            raise RuntimeError(f"Rust session failed: {error}")
        player_ids = [
            player.get("id")
            for player in (self.current_matchup.setup.get("players", []) if self.current_matchup else [])
            if player.get("id")
        ]
        boundary_rewards = self._legacy_boundary_rewards(view, player_ids)
        if decision is not None:
            state = dict(view.get("state", {}))
            state["_decisionContext"] = self._decision_context(decision)
            state = self._decorate_observation(state, decision.get("playerId"))
            return DecisionStep(
                state,
                expand_policy_actions(decision),
                0.0,
                False,
                decision.get("playerId"),
                rewards_by_player=boundary_rewards or None,
            )

        state = view.get("state", {})
        if view.get("matchState") is not None:
            state = dict(state)
            state["_matchState"] = view.get("matchState")
        outcome = state.get("outcome") or {}
        winner = outcome.get("winner")
        losers = set(outcome.get("losers", []))
        loser_reward = -1.0
        if self.multiplayer_reward_mode == "centeredWinner" and losers:
            loser_reward = -1.0 / len(losers)
        if self.multiplayer_reward_mode == "alphaStarTwoPlayer":
            rewards_by_player = boundary_rewards
        elif self.multiplayer_reward_mode == "plackettLuce":
            reward_participant_by_player = (
                self.plackett_luce_participant_by_player_id
                or self.participant_by_player_id
            )
            order = ordered_finishers(state, player_ids)
            # A deterministic anchor deadline is a training loss when the real
            # deck has not won before the configured round.
            if (
                winner is None
                and state.get("status") == "turnLimitReached"
                and self.current_matchup
                and self.current_matchup.anchor_deadline_round
            ):
                anchors = list(self.current_matchup.training_anchor_player_ids)
                order = anchors + [player_id for player_id in order if player_id not in anchors]
            ordered_participants = list(
                dict.fromkeys(
                    reward_participant_by_player.get(player_id, player_id)
                    for player_id in order
                )
            )
            if len(ordered_participants) > 1:
                ratings_by_participant = {
                    participant: next(
                        (
                            self.plackett_luce_ratings_by_player_id.get(
                                player_id,
                                PlackettLuceRating(),
                            )
                            for player_id, candidate in reward_participant_by_player.items()
                            if candidate == participant
                        ),
                        PlackettLuceRating(),
                    )
                    for participant in ordered_participants
                }
                participant_rewards = rank_gradient_rewards(
                    ordered_participants,
                    ratings_by_participant,
                )
                rewards_by_player = {
                    player_id: participant_rewards.get(
                        reward_participant_by_player.get(player_id, player_id),
                        0.0,
                    )
                    for player_id in player_ids
                }
            else:
                ratings = {
                    player_id: self.plackett_luce_ratings_by_player_id.get(
                        player_id,
                        PlackettLuceRating(),
                    )
                    for player_id in player_ids
                }
                rewards_by_player = rank_gradient_rewards(order, ratings)
        else:
            rewards_by_player = {
                player_id: (
                    1.0
                    if player_id == winner
                    else (
                        loser_reward
                        if player_id in losers
                        else (self.no_winner_reward if winner is None else 0.0)
                    )
                )
                for player_id in player_ids
            }
        step = DecisionStep(
            state,
            [],
            0.0,
            True,
            rewards_by_player=rewards_by_player,
        )
        self._remove_session()
        return step

    def reset(self, matchup_id: str, seed: int, seat_swap: bool) -> DecisionStep:
        self._remove_session()
        self._match_wins_by_player_id = {}
        self._match_reward_emitted = False
        self.known_decks_by_player_id = {}
        self.pregame_commanders = []
        self.previous_observations_by_player_id = {}
        matchup = self.matchups[matchup_id]
        self.current_matchup = matchup
        setup = dict(matchup.setup)
        players = list(setup.get("players", []))
        if seat_swap:
            players.reverse()
            setup["players"] = players
        deck_session_ids = list(matchup.deck_session_ids)
        if seat_swap:
            deck_session_ids.reverse()
        player_ids = [player["id"] for player in players]
        analytics_deck_sessions = {
            player_id: deck_session_ids[index]
            for index, player_id in enumerate(player_ids)
            if index < len(deck_session_ids) and deck_session_ids[index]
        }
        analytics_pilots = self.analytics_pilot_override or {
            player_id: self.learner_pilot_id for player_id in player_ids
        }
        response = self.client.post(
            "/game/sessions",
            json={
                "setup": setup,
                "seed": seed,
                "gameMode": matchup.game_mode,
                "maxTurns": matchup.max_turns,
                "mulliganEnabled": matchup.mulligan_enabled,
                "freeMulligans": matchup.free_mulligans,
                "maxMulligans": matchup.max_mulligans,
                "waitTimeoutMs": self.wait_timeout_ms,
                "humanPlayerIds": player_ids,
                "combatDeclarationRevisionPlayerIds": [],
                "holdPriorityPlayerIds": [],
                "analyticsContextId": self.analytics_context_id,
                "analyticsPilotByPlayerId": analytics_pilots,
                "analyticsDeckSessionByPlayerId": analytics_deck_sessions,
                "punchingBagPlayerIds": list(matchup.punching_bag_player_ids),
                "openingHandSelectionPoolSizeByPlayerId": (
                    {
                        matchup.learner_player_id: matchup.anchor_opening_hand_pool_size,
                    }
                    if matchup.anchor_opening_hand_pool_size is not None
                    else {}
                ),
                "trainingAnchorDeadlineRoundByPlayerId": {
                    player_id: matchup.anchor_deadline_round
                    for player_id in matchup.training_anchor_player_ids
                    if matchup.anchor_deadline_round is not None
                },
            },
        )
        response.raise_for_status()
        self.current_view = response.json()
        self._capture_known_decks(self.current_view)
        self.session_id = self.current_view["sessionId"]
        return self._to_step(self.current_view)


class TinySelfPlayEnvironment:
    """Fast deterministic environment used to prove that PPO updates end-to-end."""

    def __init__(self, horizon: int = 8) -> None:
        self.horizon = horizon
        self.turn = 0
        self.target = 0
        self.score = 0

    def reset(self, matchup_id: str, seed: int, seat_swap: bool) -> DecisionStep:
        randomizer = random.Random(f"{matchup_id}:{seed}:{seat_swap}")
        self.turn = 0
        self.target = randomizer.randrange(2)
        self.score = 0
        return self._decision()

    def _decision(self) -> DecisionStep:
        done = self.turn >= self.horizon
        reward = float(self.score) / self.horizon if done else 0.0
        actions = [] if done else [{"id": "left", "kind": 0}, {"id": "right", "kind": 1}]
        state = {"turn": self.turn, "target": self.target, "score": self.score}
        return DecisionStep(state, actions, reward, done)

    def step(self, action_index: int) -> DecisionStep:
        self.score += 1 if action_index == self.target else -1
        self.turn += 1
        self.target = 1 - self.target
        return self._decision()
