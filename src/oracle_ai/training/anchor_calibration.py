from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from oracle_ai.training.plackett_luce import (
    AnchorChallenge,
    TrainingLeaderboard,
    anchor_challenges,
)


def _configured_challenges(config: dict[str, Any]) -> list[AnchorChallenge]:
    randomizer = config.get("trainingScenarioRandomizer") or {}
    return anchor_challenges(
        tuple(int(value) for value in config.get("anchorDeadlineRounds", range(1, 26))),
        tuple(
            int(value)
            for value in config.get(
                "anchorOpeningHandPoolSizes",
                [20, 40, 60, 80, 100],
            )
        ),
        tuple(
            int(value)
            for value in config.get(
                "anchorPlayerCounts",
                randomizer.get("playerCounts", [2, 3, 4]),
            )
        ),
    )


def _configured_labels(
    config: dict[str, Any],
    challenges: list[AnchorChallenge],
) -> dict[str, str]:
    return {
        "v10": "V10 · AlphaZero + VQ-VAE",
        "v11": "V11 · AlphaStar",
        **{challenge.participant_id: challenge.label for challenge in challenges},
        **{
            str(key): str(value)
            for key, value in config.get("trainingLeaderboardLabels", {}).items()
        },
    }


def _inversion_count(
    challenges: list[AnchorChallenge],
    leaderboard: TrainingLeaderboard,
) -> int:
    expected = sorted(challenges, key=lambda challenge: challenge.ranking_key)
    actual = sorted(
        challenges,
        key=lambda challenge: (
            -leaderboard.deck_matchmaking_stats(
                challenge.participant_id,
                ["Anchor"],
            )["Anchor"]["ordinal"],
            challenge.ranking_key,
        ),
    )
    actual_position = {
        challenge.participant_id: index for index, challenge in enumerate(actual)
    }
    inversions = 0
    for left_index, left in enumerate(expected):
        for right in expected[left_index + 1 :]:
            if actual_position[left.participant_id] > actual_position[right.participant_id]:
                inversions += 1
    return inversions


def calibrate_from_config(
    config: dict[str, Any],
    *,
    games: int | None = None,
    seed: int | None = None,
    create_backup: bool = True,
) -> dict[str, Any]:
    challenges = _configured_challenges(config)
    if not challenges:
        raise ValueError("anchor calibration has no configured challenges")
    output = Path(config.get("outputDir", "runs/oracle-ai-league"))
    leaderboard_path = output / "training-leaderboard.json"
    backup_path: Path | None = None
    if create_backup and leaderboard_path.is_file():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = output / f"training-leaderboard.before-anchor-calibration-{timestamp}.json"
        shutil.copy2(leaderboard_path, backup_path)

    leaderboard = TrainingLeaderboard(
        leaderboard_path,
        _configured_labels(config, challenges),
    )
    report = leaderboard.calibrate_anchors(
        challenges,
        games=int(games if games is not None else config.get("anchorCalibrationGames", 5000)),
        seed=int(seed if seed is not None else config.get("anchorCalibrationSeed", 20260815)),
    )
    report["rankingInversions"] = _inversion_count(challenges, leaderboard)
    report["leaderboardPath"] = str(leaderboard_path)
    report["backupPath"] = str(backup_path) if backup_path is not None else None

    rows = {
        row["participantId"]: row
        for row in leaderboard.payload()["deckParticipants"]
        if row["participantId"].startswith("anchor-")
    }
    expected = sorted(challenges, key=lambda challenge: challenge.ranking_key)
    report["strongest"] = [
        {
            "participantId": challenge.participant_id,
            "ordinal": rows[challenge.participant_id]["ordinal"],
            "games": rows[challenge.participant_id]["games"],
        }
        for challenge in expected[:5]
    ]
    report["weakest"] = [
        {
            "participantId": challenge.participant_id,
            "ordinal": rows[challenge.participant_id]["ordinal"],
            "games": rows[challenge.participant_id]["games"],
        }
        for challenge in expected[-5:]
    ]
    report_path = output / "anchor-calibration.json"
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate deterministic MTG training anchors with Plackett-Luce matches"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--games", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    report = calibrate_from_config(
        config,
        games=args.games,
        seed=args.seed,
        create_backup=not args.no_backup,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
