from __future__ import annotations

from deepdeck_learner.resources import active_games


def test_active_games_includes_running_league_matches(tmp_path) -> None:
    games = active_games(
        tmp_path,
        [
            {
                "id": "league-job",
                "kind": "matchmaking.agent",
                "status": "running",
                "model_id": "model-1",
                "label": "My V12",
                "details": {
                    "leagueMatches": [
                        {
                            "matchId": "match-1",
                            "gameId": "game-1",
                            "status": "running",
                            "decks": ["Deck A", "Deck B"],
                            "players": 2,
                            "turnNumber": 4,
                            "roundNumber": 1,
                            "startedAtUnixMs": 1_788_428_592_000,
                            "updatedAtUnixMs": 1_788_428_593_000,
                            "watchUrl": "https://example.test/matches?match=match-1",
                        }
                    ]
                },
            }
        ],
    )

    assert games == [
        {
            "id": "match-1",
            "sessionId": "game-1",
            "source": "league",
            "jobId": "league-job",
            "modelId": "model-1",
            "modelName": "My V12",
            "worker": 0,
            "status": "running",
            "mode": "League match",
            "decks": ["Deck A", "Deck B"],
            "players": 2,
            "playersState": [],
            "turnNumber": 4,
            "roundNumber": 1,
            "decisions": 0,
            "startedAtUnixMs": 1_788_428_592_000,
            "updatedAtUnixMs": 1_788_428_593_000,
            "canCancel": False,
            "watchUrl": "https://example.test/matches?match=match-1",
        }
    ]
