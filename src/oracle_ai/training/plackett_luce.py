from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class PlackettLuceRating:
    mu: float = 25.0
    sigma: float = 25.0 / 3.0
    games: int = 0
    wins: int = 0
    game_wins: int = 0
    game_losses: int = 0

    @property
    def ordinal(self) -> float:
        return self.mu - 3.0 * self.sigma


@dataclass(frozen=True)
class AnchorChallenge:
    deadline_round: int
    opening_hand_pool_size: int
    player_count: int

    @property
    def opponent_count(self) -> int:
        return max(1, self.player_count - 1)

    @property
    def participant_id(self) -> str:
        return anchor_participant_id(
            self.deadline_round,
            self.opening_hand_pool_size,
            self.player_count,
        )

    @property
    def label(self) -> str:
        opponent_label = "ancre" if self.opponent_count == 1 else "ancres"
        return (
            f"Ancre M{self.deadline_round}/N{self.opening_hand_pool_size}/"
            f"P{self.player_count} · victoire avant la ronde {self.deadline_round}, "
            f"main 7 parmi {self.opening_hand_pool_size}, "
            f"contre {self.opponent_count} {opponent_label} en équipe"
        )

    @property
    def ranking_key(self) -> tuple[int, int, int]:
        # Faster wins rank first. At the same deadline, choosing from fewer
        # cards is the harder challenge. A larger table wins the final tie.
        return (
            self.deadline_round,
            self.opening_hand_pool_size,
            -self.player_count,
        )


def anchor_participant_id(
    deadline_round: int,
    opening_hand_pool_size: int,
    player_count: int,
) -> str:
    return (
        f"anchor-m{deadline_round:02d}-n{opening_hand_pool_size:03d}-"
        f"p{player_count}"
    )


def anchor_challenges(
    deadline_rounds: list[int] | tuple[int, ...],
    opening_hand_pool_sizes: list[int] | tuple[int, ...],
    player_counts: list[int] | tuple[int, ...],
) -> list[AnchorChallenge]:
    return [
        AnchorChallenge(deadline_round, pool_size, player_count)
        for deadline_round in deadline_rounds
        for pool_size in opening_hand_pool_sizes
        for player_count in player_counts
    ]


def _strength(rating: PlackettLuceRating, beta: float) -> float:
    return math.exp(max(-30.0, min(30.0, rating.mu / beta)))


def expected_first_scores(
    ratings: list[PlackettLuceRating],
    *,
    beta: float = 25.0 / 6.0,
) -> list[float]:
    if not ratings:
        return []
    strengths = [_strength(rating, beta) for rating in ratings]
    total = sum(strengths)
    return [strength / total for strength in strengths]


def hypothetical_first_place_deltas(
    ratings: list[PlackettLuceRating],
    *,
    beta: float = 25.0 / 6.0,
    learning_rate: float = 1.0,
) -> list[dict[str, float]]:
    """Preview each rating's mu delta for a win, complete draw, or loss."""

    if len(ratings) <= 1:
        return [{"win": 0.0, "draw": 0.0, "loss": 0.0} for _ in ratings]
    expected = expected_first_scores(ratings, beta=beta)
    draw_score = 1.0 / len(ratings)
    return [
        {
            "win": learning_rate * (1.0 - probability),
            "draw": learning_rate * (draw_score - probability),
            "loss": learning_rate * -probability,
        }
        for probability in expected
    ]


def complete_tie_gradient_rewards(
    participant_ids: list[str],
    ratings_by_id: dict[str, PlackettLuceRating],
    *,
    beta: float = 25.0 / 6.0,
) -> dict[str, float]:
    """Return a zero-sum gradient when every participant shares first place."""

    unique_ids = list(dict.fromkeys(participant_ids))
    previews = hypothetical_first_place_deltas(
        [
            ratings_by_id.get(participant_id, PlackettLuceRating())
            for participant_id in unique_ids
        ],
        beta=beta,
    )
    return {
        participant_id: preview["draw"]
        for participant_id, preview in zip(unique_ids, previews)
    }


def rank_gradient_rewards(
    ordered_ids: list[str],
    ratings_by_id: dict[str, PlackettLuceRating],
    *,
    beta: float = 25.0 / 6.0,
) -> dict[str, float]:
    """Return the normalized score gradient of a Plackett-Luce ranking.

    Each placement is a choice from the remaining field.  This creates a
    zero-sum, strength-adjusted reward for every player instead of treating all
    losses as equally informative.  A complete tie has no ranking information.
    """

    if len(ordered_ids) <= 1:
        return {player_id: 0.0 for player_id in ordered_ids}
    rewards = {player_id: 0.0 for player_id in ordered_ids}
    remaining = list(ordered_ids)
    stages = 0
    while len(remaining) > 1:
        expected = expected_first_scores(
            [ratings_by_id.get(player_id, PlackettLuceRating()) for player_id in remaining],
            beta=beta,
        )
        selected = remaining[0]
        for player_id, probability in zip(remaining, expected):
            rewards[player_id] -= probability
        rewards[selected] += 1.0
        remaining.pop(0)
        stages += 1
    scale = max(1, stages)
    return {
        player_id: max(-1.0, min(1.0, reward / scale))
        for player_id, reward in rewards.items()
    }


def ordered_finishers(state: dict[str, Any], player_ids: list[str]) -> list[str]:
    outcome = state.get("outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    winner = outcome.get("winner")
    loss_sequence: dict[str, int] = {}
    events = state.get("events")
    for index, event in enumerate(events if isinstance(events, list) else []):
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind", "")).casefold()
        if "lost" not in kind and kind not in {"playerloss", "playereliminated"}:
            continue
        player_id = str(event.get("playerId", ""))
        if player_id in player_ids:
            try:
                loss_sequence[player_id] = int(event.get("sequence", index))
            except (TypeError, ValueError):
                loss_sequence[player_id] = index
    players = {
        str(player.get("id")): player
        for player in state.get("players", [])
        if isinstance(player, dict) and player.get("id") is not None
    }
    surviving = [
        player_id
        for player_id in player_ids
        if player_id != winner and not bool(players.get(player_id, {}).get("hasLost"))
    ]
    eliminated = [
        player_id
        for player_id in player_ids
        if player_id != winner and player_id not in surviving
    ]
    eliminated.sort(key=lambda player_id: loss_sequence.get(player_id, -1), reverse=True)
    return ([str(winner)] if winner in player_ids else []) + surviving + eliminated


class TrainingLeaderboard:
    schema_version = "oracle-ai-training-leaderboard/v4"

    def __init__(
        self,
        path: Path,
        labels: dict[str, str],
        *,
        beta: float = 25.0 / 6.0,
        learning_rate: float = 1.0,
    ) -> None:
        self.path = path
        self.labels = dict(labels)
        self.beta = beta
        self.learning_rate = learning_rate
        self.ratings = {
            participant_id: PlackettLuceRating()
            for participant_id in self.labels
        }
        self.deck_ratings: dict[str, PlackettLuceRating] = {}
        self.deck_entries: dict[str, tuple[str, str]] = {}
        self.anchor_calibration: dict[str, Any] | None = None
        source_schema_version = ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            source_schema_version = str(payload.get("schemaVersion", ""))
            calibration = payload.get("anchorCalibration")
            if isinstance(calibration, dict):
                self.anchor_calibration = dict(calibration)
            for participant in payload.get("participants", []):
                participant_id = str(participant.get("id", ""))
                if participant_id in self.labels:
                    self.ratings[participant_id] = PlackettLuceRating(
                        mu=float(participant.get("mu", 25.0)),
                        sigma=float(participant.get("sigma", 25.0 / 3.0)),
                        games=int(participant.get("games", 0)),
                        wins=int(participant.get("wins", 0)),
                        game_wins=int(participant.get("gameWins", 0)),
                        game_losses=int(participant.get("gameLosses", 0)),
                    )
            for participant in payload.get("deckParticipants", []):
                participant_id = str(participant.get("participantId", "")).strip()
                deck_name = str(participant.get("deckName", "")).strip()
                if not participant_id or not deck_name:
                    continue
                if participant_id.startswith("anchor-") and participant_id not in self.labels:
                    # Anchor identity includes every difficulty dimension. Drop
                    # obsolete aggregate identities instead of displaying and
                    # matching against incomparable ratings.
                    continue
                entry_id = self._deck_entry_id(participant_id, deck_name)
                self.deck_entries[entry_id] = (participant_id, deck_name)
                self.deck_ratings[entry_id] = PlackettLuceRating(
                    mu=float(participant.get("mu", 25.0)),
                    sigma=float(participant.get("sigma", 25.0 / 3.0)),
                    games=int(participant.get("games", 0)),
                    wins=int(participant.get("wins", 0)),
                    game_wins=int(participant.get("gameWins", 0)),
                    game_losses=int(participant.get("gameLosses", 0)),
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        if source_schema_version and source_schema_version != self.schema_version:
            self._backfill_non_anchor_wins(path.with_name("training.jsonl"))
        self.save()

    @staticmethod
    def _deck_entry_id(participant_id: str, deck_name: str) -> str:
        return json.dumps(
            [participant_id, deck_name],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _update_rating_group(
        self,
        unique_ids: list[str],
        ordered_ids: list[str],
        ratings: dict[str, PlackettLuceRating],
        winner_id: str | None,
        *,
        complete_tie: bool = False,
    ) -> bool:
        if len(unique_ids) <= 1:
            return False
        for entry_id in unique_ids:
            ratings.setdefault(entry_id, PlackettLuceRating())
            if entry_id not in ordered_ids:
                ordered_ids.append(entry_id)
        gradients = (
            complete_tie_gradient_rewards(unique_ids, ratings, beta=self.beta)
            if complete_tie
            else rank_gradient_rewards(ordered_ids, ratings, beta=self.beta)
        )
        for entry_id in unique_ids:
            rating = ratings[entry_id]
            rating.mu += self.learning_rate * gradients.get(entry_id, 0.0)
            rating.sigma = max(1.0, rating.sigma * 0.9975)
            rating.games += 1
            if entry_id == winner_id:
                rating.wins += 1
        return True

    def _backfill_non_anchor_wins(self, history_path: Path) -> None:
        """Recover wins added in v4 from the append-only training history."""

        for participant_id, rating in self.ratings.items():
            if not participant_id.startswith("anchor-"):
                rating.wins = 0
        for entry_id, rating in self.deck_ratings.items():
            participant_id, _ = self.deck_entries[entry_id]
            if not participant_id.startswith("anchor-"):
                rating.wins = 0
        try:
            lines = history_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                record = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            raw_participants = record.get("participantsByPlayer")
            if not isinstance(raw_participants, dict):
                continue
            participant_by_player = {
                str(player_id): str(participant_id)
                for player_id, participant_id in raw_participants.items()
                if participant_id is not None
            }
            outcome = record.get("outcome")
            outcome = outcome if isinstance(outcome, dict) else {}
            winner_player_id = str(outcome.get("winner", ""))
            if winner_player_id not in participant_by_player:
                winner_player_id = ""
            if (
                not winner_player_id
                and record.get("anchorDeadlineRound")
                and record.get("gameStatus") == "turnLimitReached"
            ):
                winner_player_id = next(
                    (
                        player_id
                        for player_id, participant_id in participant_by_player.items()
                        if participant_id.startswith("anchor-")
                    ),
                    "",
                )
            winner_participant_id = participant_by_player.get(winner_player_id, "")
            if (
                winner_participant_id
                and not winner_participant_id.startswith("anchor-")
                and len(set(participant_by_player.values())) > 1
                and winner_participant_id in self.ratings
            ):
                self.ratings[winner_participant_id].wins += 1

            decks = record.get("decks")
            if not isinstance(decks, list):
                continue
            deck_entry_by_player = {
                player_id: self._deck_entry_id(participant_by_player[player_id], str(deck_name))
                for player_id, deck_name in zip(participant_by_player, decks)
                if str(deck_name).strip()
            }
            winner_entry_id = deck_entry_by_player.get(winner_player_id, "")
            if (
                winner_entry_id in self.deck_ratings
                and not winner_participant_id.startswith("anchor-")
                and len(set(deck_entry_by_player.values())) > 1
            ):
                self.deck_ratings[winner_entry_id].wins += 1

    def update(
        self,
        participant_by_player: dict[str, str],
        terminal_state: dict[str, Any],
        *,
        ordered_players: list[str] | None = None,
        deck_by_player: dict[str, str] | None = None,
        save: bool = True,
    ) -> None:
        unique_participants = list(dict.fromkeys(participant_by_player.values()))
        explicit_order = ordered_players is not None
        outcome = terminal_state.get("outcome")
        outcome = outcome if isinstance(outcome, dict) else {}
        winner_player_id = str(outcome.get("winner", ""))
        if winner_player_id not in participant_by_player:
            winner_player_id = ""
        if not winner_player_id and explicit_order and ordered_players:
            winner_player_id = ordered_players[0]
        if ordered_players is None:
            ordered_players = ordered_finishers(
                terminal_state,
                list(participant_by_player),
            )
        players = {
            str(player.get("id")): player
            for player in terminal_state.get("players", [])
            if isinstance(player, dict) and player.get("id") is not None
        }
        complete_tie = (
            not winner_player_id
            and not explicit_order
            and all(
                not bool(players.get(player_id, {}).get("hasLost"))
                for player_id in participant_by_player
            )
        )
        ordered_participants = list(
            dict.fromkeys(
                participant_by_player[player_id]
                for player_id in ordered_players
                if player_id in participant_by_player
            )
        )
        updated = self._update_rating_group(
            unique_participants,
            ordered_participants,
            self.ratings,
            participant_by_player.get(winner_player_id),
            complete_tie=complete_tie,
        )

        deck_entry_by_player: dict[str, str] = {}
        for player_id, participant_id in participant_by_player.items():
            deck_name = str((deck_by_player or {}).get(player_id, "")).strip()
            if not deck_name:
                continue
            entry_id = self._deck_entry_id(participant_id, deck_name)
            self.deck_entries[entry_id] = (participant_id, deck_name)
            deck_entry_by_player[player_id] = entry_id
        self._record_game_totals(participant_by_player, terminal_state, deck_by_player)
        unique_deck_entries = list(dict.fromkeys(deck_entry_by_player.values()))
        ordered_deck_entries = list(
            dict.fromkeys(
                deck_entry_by_player[player_id]
                for player_id in ordered_players
                if player_id in deck_entry_by_player
            )
        )
        updated = self._update_rating_group(
            unique_deck_entries,
            ordered_deck_entries,
            self.deck_ratings,
            deck_entry_by_player.get(winner_player_id),
            complete_tie=complete_tie,
        ) or updated
        if updated and save:
            self.save()

    def _record_game_totals(
        self,
        participant_by_player: dict[str, str],
        terminal_state: dict[str, Any],
        deck_by_player: dict[str, str] | None,
    ) -> None:
        """Record individual games separately from the completed-set rating."""
        match_state = terminal_state.get("_matchState")
        if not isinstance(match_state, dict):
            return
        scores = match_state.get("winsByPlayerId")
        if not isinstance(scores, dict):
            return
        player_ids = list(participant_by_player)
        for player_id in player_ids:
            participant_id = participant_by_player[player_id]
            wins = max(0, int(scores.get(player_id, 0) or 0))
            losses = sum(
                max(0, int(scores.get(other_id, 0) or 0))
                for other_id in player_ids
                if other_id != player_id
            )
            rating = self.ratings.setdefault(participant_id, PlackettLuceRating())
            rating.game_wins += wins
            rating.game_losses += losses
            deck_name = str((deck_by_player or {}).get(player_id, "")).strip()
            if deck_name:
                entry_id = self._deck_entry_id(participant_id, deck_name)
                deck_rating = self.deck_ratings.setdefault(entry_id, PlackettLuceRating())
                deck_rating.game_wins += wins
                deck_rating.game_losses += losses

    def calibrate_anchors(
        self,
        challenges: list[AnchorChallenge],
        *,
        games: int,
        seed: int,
    ) -> dict[str, Any]:
        if games <= 0:
            raise ValueError("anchor calibration games must be positive")
        ordered_challenges = sorted(
            dict.fromkeys(challenges),
            key=lambda challenge: challenge.ranking_key,
        )
        if len(ordered_challenges) < 4:
            raise ValueError("anchor calibration requires at least four challenges")

        anchor_ids = {challenge.participant_id for challenge in ordered_challenges}
        self.labels.update(
            {
                challenge.participant_id: challenge.label
                for challenge in ordered_challenges
            }
        )
        stale_deck_entries = [
            entry_id
            for entry_id, (participant_id, _) in self.deck_entries.items()
            if participant_id.startswith("anchor-")
        ]
        for entry_id in stale_deck_entries:
            self.deck_entries.pop(entry_id, None)
            self.deck_ratings.pop(entry_id, None)
        prior_span = 30.0
        for index, challenge in enumerate(ordered_challenges):
            rank_fraction = index / max(1, len(ordered_challenges) - 1)
            initial_mu = 25.0 + prior_span * (0.5 - rank_fraction)
            self.ratings[challenge.participant_id] = PlackettLuceRating(mu=initial_mu)
            entry_id = self._deck_entry_id(challenge.participant_id, "Anchor")
            self.deck_entries[entry_id] = (challenge.participant_id, "Anchor")
            self.deck_ratings[entry_id] = PlackettLuceRating(mu=initial_mu)

        # Three-way matches fit Plackett-Luce directly and make 5,000 games
        # exactly 15,000 anchor appearances. With the configured 375 anchors,
        # every challenge therefore receives exactly 40 calibration results.
        table_size = 3
        if len(ordered_challenges) % table_size:
            raise ValueError(
                "anchor challenge count must be divisible by three for balanced calibration"
            )
        games_per_epoch = len(ordered_challenges) // table_size
        normal_learning_rate = self.learning_rate
        self.learning_rate = min(normal_learning_rate, 0.1)
        try:
            for game_index in range(games):
                epoch = game_index // games_per_epoch
                group_index = game_index % games_per_epoch
                if epoch % 2:
                    group_index = games_per_epoch - group_index - 1
                rotation = (seed + epoch) % table_size
                start = rotation + group_index * table_size
                selected = [
                    ordered_challenges[(start + offset) % len(ordered_challenges)]
                    for offset in range(table_size)
                ]
                selected.sort(key=lambda challenge: challenge.ranking_key)
                participant_by_player = {
                    f"player-{index + 1}": challenge.participant_id
                    for index, challenge in enumerate(selected)
                }
                ordered_players = list(participant_by_player)
                self.update(
                    participant_by_player,
                    {},
                    ordered_players=ordered_players,
                    deck_by_player={player_id: "Anchor" for player_id in ordered_players},
                    save=False,
                )
        finally:
            self.learning_rate = normal_learning_rate

        anchor_ratings = [self.ratings[participant_id] for participant_id in anchor_ids]
        deck_anchor_ratings = [
            rating
            for entry_id, rating in self.deck_ratings.items()
            if self.deck_entries[entry_id][0] in anchor_ids
        ]
        self.anchor_calibration = {
            "schemaVersion": "oracle-ai-anchor-calibration/v1",
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "games": games,
            "seed": seed,
            "challengeCount": len(ordered_challenges),
            "deadlineRounds": sorted(
                {challenge.deadline_round for challenge in ordered_challenges}
            ),
            "openingHandPoolSizes": sorted(
                {challenge.opening_hand_pool_size for challenge in ordered_challenges}
            ),
            "playerCounts": sorted(
                {challenge.player_count for challenge in ordered_challenges}
            ),
            "anchorOpponentCounts": sorted(
                {challenge.opponent_count for challenge in ordered_challenges}
            ),
            "tieBreakOrder": [
                "deadlineRoundAscending",
                "openingHandPoolSizeAscending",
                "anchorOpponentCountDescending",
            ],
            "initialMuSpan": prior_span,
            "minimumGamesPerAnchor": min(rating.games for rating in anchor_ratings),
            "maximumGamesPerAnchor": max(rating.games for rating in anchor_ratings),
            "minimumDeckGamesPerAnchor": min(
                rating.games for rating in deck_anchor_ratings
            ),
            "maximumDeckGamesPerAnchor": max(
                rating.games for rating in deck_anchor_ratings
            ),
        }
        self.save()
        return dict(self.anchor_calibration)

    def player_ratings(
        self,
        participant_by_player: dict[str, str],
    ) -> dict[str, PlackettLuceRating]:
        return {
            player_id: self.ratings.setdefault(participant, PlackettLuceRating())
            for player_id, participant in participant_by_player.items()
        }

    def deck_participants(
        self,
        participant_by_player: dict[str, str],
        deck_by_player: dict[str, str],
    ) -> dict[str, str]:
        entries: dict[str, str] = {}
        for player_id, participant_id in participant_by_player.items():
            deck_name = str(deck_by_player.get(player_id, "")).strip()
            if not deck_name:
                continue
            entry_id = self._deck_entry_id(participant_id, deck_name)
            self.deck_entries[entry_id] = (participant_id, deck_name)
            self.deck_ratings.setdefault(entry_id, PlackettLuceRating())
            entries[player_id] = entry_id
        return entries

    def player_deck_ratings(
        self,
        participant_by_player: dict[str, str],
        deck_by_player: dict[str, str],
    ) -> dict[str, PlackettLuceRating]:
        return {
            player_id: self.deck_ratings[entry_id]
            for player_id, entry_id in self.deck_participants(
                participant_by_player,
                deck_by_player,
            ).items()
        }

    def deck_matchmaking_stats(
        self,
        participant_id: str,
        deck_names: list[str] | tuple[str, ...],
    ) -> dict[str, dict[str, float | int | None]]:
        ranked_ids = sorted(
            (
                entry_id
                for entry_id, rating in self.deck_ratings.items()
                if rating.games > 0
            ),
            key=lambda entry_id: (
                -self.deck_ratings[entry_id].ordinal,
                self.deck_entries[entry_id],
            ),
        )
        rank_by_id = {
            entry_id: rank for rank, entry_id in enumerate(ranked_ids, start=1)
        }
        stats: dict[str, dict[str, float | int | None]] = {}
        for deck_name in deck_names:
            entry_id = self._deck_entry_id(participant_id, deck_name)
            rating = self.deck_ratings.get(entry_id, PlackettLuceRating())
            stats[deck_name] = {
                "mu": rating.mu,
                "sigma": rating.sigma,
                "ordinal": rating.ordinal,
                "games": rating.games,
                "gameWins": rating.game_wins,
                "gameLosses": rating.game_losses,
                "rank": rank_by_id.get(entry_id),
            }
        return stats

    def payload(self) -> dict[str, Any]:
        participants = [
            {
                "id": participant_id,
                "label": self.labels.get(participant_id, participant_id),
                **asdict(rating),
                "gameWins": rating.game_wins,
                "gameLosses": rating.game_losses,
                "ordinal": rating.ordinal,
            }
            for participant_id, rating in self.ratings.items()
            if rating.games > 0 or not participant_id.startswith("anchor-")
        ]
        participants.sort(key=lambda item: (-item["ordinal"], item["label"]))
        for rank, participant in enumerate(participants, start=1):
            participant["rank"] = rank
        deck_participants = [
            {
                "id": entry_id,
                "participantId": participant_id,
                "participantLabel": self.labels.get(participant_id, participant_id),
                "deckName": deck_name,
                "label": f"{self.labels.get(participant_id, participant_id)} × {deck_name}",
                **asdict(rating),
                "gameWins": rating.game_wins,
                "gameLosses": rating.game_losses,
                "ordinal": rating.ordinal,
            }
            for entry_id, rating in self.deck_ratings.items()
            for participant_id, deck_name in [self.deck_entries[entry_id]]
            if rating.games > 0
        ]
        deck_participants.sort(
            key=lambda item: (
                -item["ordinal"],
                item["participantLabel"],
                item["deckName"],
            )
        )
        for rank, participant in enumerate(deck_participants, start=1):
            participant["rank"] = rank
        payload = {
            "schemaVersion": self.schema_version,
            "ratingSystem": "plackett-luce",
            "participants": participants,
            "deckParticipants": deck_participants,
        }
        if self.anchor_calibration is not None:
            payload["anchorCalibration"] = self.anchor_calibration
        return payload

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(self.payload(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            for attempt in range(20):
                try:
                    temporary.replace(self.path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(min(0.05 * (attempt + 1), 0.5))
        finally:
            temporary.unlink(missing_ok=True)
