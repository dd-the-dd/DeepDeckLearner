from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from deepdeck_agent import (
    Agent,
    AgentConfig,
    AgentRunner,
    LeagueEventEntry,
    LocalGame,
    LocalPlayer,
    MatchmakingEntry,
    PlaySpeed,
    ServerTarget,
)
from dotenv import find_dotenv, load_dotenv

from deepdeck_learner.league_connection import DEFAULT_PLATFORM_URL
from deepdeck_learner.league_connection import agent_url as league_agent_url
from deepdeck_learner.league_connection import platform_url as league_platform_url
from deepdeck_learner.league_lifecycle import (
    LeagueRunBudget,
    LeagueRunLimits,
    league_failure_message,
    serve_event_cycles,
    watch_registration,
)
from deepdeck_learner.league_rotation_io import stop_requested, write_result
from deepdeck_learner.runtime import frozen

from .alexios import AlexiosAgent
from .configuration import (
    alexios_config,
    deep_learning_config,
    forge_reference_config,
    programmatic_config,
    random_config,
)
from .forge_reference import ForgeReferenceProfile, build_forge_reference_agent
from .oracle_checkpoint_agent import OracleCheckpointAgent, is_oracle_checkpoint
from .programmatic import load_programmatic_agent
from .random_baseline import build_random_agent


def _speed(value: str) -> PlaySpeed:
    try:
        return PlaySpeed(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("speed must be 100ms, 1s, or 10s") from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run a public Deep Deck example agent.")
    result.add_argument(
        "example",
        nargs="?",
        choices=(
            "random",
            "alexios",
            "forge-cautious",
            "forge-balanced",
            "forge-aggressive",
            "programmatic",
            "v11",
            "v12",
            "v13",
            "v15",
        ),
        default=os.getenv("DEEPDECK_EXAMPLE", "alexios"),
    )
    result.add_argument(
        "--target",
        choices=("local", "ddl"),
        default=os.getenv("DEEPDECK_TARGET", "local"),
    )
    result.add_argument(
        "--speed",
        type=_speed,
        default=_speed(os.getenv("DEEPDECK_SPEED", "1s")),
    )
    result.add_argument(
        "--engine-url",
        default=os.getenv("MTG_ENGINE_URL", "http://127.0.0.1:8787"),
    )
    result.add_argument("--seed", type=int, default=1)
    result.add_argument(
        "--clock-initial-ms",
        type=int,
        default=int(os.getenv("DEEPDECK_CLOCK_INITIAL_MS", "900000")),
    )
    result.add_argument(
        "--clock-increment-ms",
        type=int,
        default=int(os.getenv("DEEPDECK_CLOCK_INCREMENT_MS", "2000")),
    )
    result.add_argument(
        "--clock-delay-ms",
        type=int,
        default=int(os.getenv("DEEPDECK_CLOCK_DELAY_MS", "250")),
    )
    result.add_argument(
        "--clock-max-decision-ms",
        type=int,
        default=int(os.getenv("DEEPDECK_CLOCK_MAX_DECISION_MS", "30000")),
    )
    result.add_argument(
        "--clock-reserve-ms",
        type=int,
        default=int(os.getenv("DEEPDECK_CLOCK_RESERVE_MS", "30000")),
    )
    result.add_argument(
        "--checkpoint",
        default=os.getenv("DEEPDECK_CHECKPOINT"),
        help=(
            "V11/V12 checkpoint directory, V13 directory containing rl-model.pt, "
            "or V15 directory containing v15-model.pt."
        ),
    )
    result.add_argument(
        "--agent-config",
        help="Directory containing agent.json, or the agent.json file itself.",
    )
    result.add_argument(
        "--league-deck-id-map",
        type=Path,
        help="Deployment-only map from local deck identities to published League versions.",
    )
    result.add_argument(
        "--device",
        default=os.getenv("DEEPDECK_DEVICE", "cpu"),
        help="PyTorch device used by V11/V12/V13/V15, for example cpu or cuda.",
    )
    result.add_argument(
        "--allow-untrained",
        action="store_true",
        help="Allow random V11/V12 weights for protocol and connectivity testing only.",
    )
    result.add_argument(
        "--competition-version-id",
        default=os.getenv("DEEPDECK_COMPETITION_VERSION_ID"),
    )
    result.add_argument(
        "--league-format",
        choices=("legacy", "commander"),
        default=os.getenv("DEEPDECK_LEAGUE_FORMAT"),
        help="Competition format; required for multi-format agents.",
    )
    result.add_argument("--league-event-id", help="Pin one specific League event UUID.")
    result.add_argument(
        "--deck-version-id",
        default=os.getenv("DEEPDECK_DECK_VERSION_ID"),
    )
    result.add_argument(
        "--additional-deck-version-id",
        action="append",
        default=[],
        help="Additional published deck; one deck is selected and pinned per competition.",
    )
    result.add_argument(
        "--matchmaking-concurrency",
        type=int,
        default=1,
        help="Compatibility option; each worker registers one agent, within the account's server-owned allowance.",
    )
    result.add_argument(
        "--replay-root", type=Path, help="Learner workspace for local League replays."
    )
    result.add_argument("--replay-model-id", help="Local agent owning the collected replays.")
    result.add_argument(
        "--league-max-matches",
        type=int,
        help=(
            "Stop enrolling after this many completed matches; "
            "finish the entire current competition."
        ),
    )
    result.add_argument(
        "--league-duration-seconds",
        type=int,
        help="Stop enrolling after this duration; finish the entire current competition.",
    )
    result.add_argument(
        "--league-run-id",
        help="Unique local identity of this worker execution, separate from agent identity.",
    )
    result.add_argument(
        "--league-stop-file",
        type=Path,
        help="Private rotation stop watermark; assigned matches still finish.",
    )
    result.add_argument("--league-dispatch-sequence", type=int, default=1)
    result.add_argument(
        "--league-result-file",
        type=Path,
        help="Private durable receipt after this worker closes its connection.",
    )
    result.add_argument(
        "--once",
        action="store_true",
        help="Serve one whole League competition (season and playoffs), then disconnect.",
    )
    result.add_argument(
        "--start-local-game",
        action="store_true",
        help="After connecting locally, create one game against a configured controller.",
    )
    result.add_argument(
        "--local-deck-session-id",
        default=os.getenv("DEEPDECK_LOCAL_DECK_SESSION_ID"),
    )
    result.add_argument(
        "--local-opponent-deck-session-id",
        default=os.getenv("DEEPDECK_LOCAL_OPPONENT_DECK_SESSION_ID"),
    )
    result.add_argument(
        "--local-opponent-controller",
        default=os.getenv("DEEPDECK_LOCAL_OPPONENT_CONTROLLER", "ai-random"),
    )
    result.add_argument(
        "--local-game-setup",
        help="Path to an inline local GameSetup prepared by DeepDeckLearner.",
    )
    result.add_argument(
        "--local-format",
        choices=("legacy", "commander"),
        default=os.getenv("DEEPDECK_LOCAL_FORMAT"),
    )
    result.add_argument(
        "--local-max-turns",
        type=int,
        default=int(os.getenv("DEEPDECK_LOCAL_MAX_TURNS", "200")),
    )
    result.add_argument("--log-level", default="INFO")
    return result


def _agent_and_config(arguments: argparse.Namespace) -> tuple[Agent, AgentConfig]:
    if arguments.example == "random":
        return build_random_agent(arguments.seed), random_config()
    if arguments.example == "alexios":
        return AlexiosAgent(), alexios_config()
    if arguments.example.startswith("forge-"):
        profile = ForgeReferenceProfile(arguments.example.removeprefix("forge-"))
        return build_forge_reference_agent(profile), forge_reference_config(profile.value)
    if arguments.example == "programmatic":
        if not arguments.agent_config:
            raise SystemExit("Programmatic agents require --agent-config")
        try:
            programmatic_agent, policy = load_programmatic_agent(Path(arguments.agent_config))
            mapping_path = getattr(arguments, "league_deck_id_map", None)
            if mapping_path:
                from uuid import UUID

                bindings = json.loads(mapping_path.read_text(encoding="utf-8"))
                if not isinstance(bindings, dict):
                    raise ValueError("Invalid League deck bindings")
                published = []
                for deck_id in policy.get("deckVersionIds", []):
                    version_id = str(bindings[deck_id])
                    UUID(version_id)
                    published.append(version_id)
                policy = {**policy, "deckVersionIds": list(dict.fromkeys(published))}
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise SystemExit(f"Unable to load programmatic agent: {error}") from error
        return programmatic_agent, programmatic_config(policy)
    if arguments.example == "v13":
        if not arguments.checkpoint:
            raise SystemExit("V13 requires --checkpoint")
        try:
            from .v13_agent import load_v13_agent

            v13_agent = load_v13_agent(arguments.checkpoint, device=arguments.device)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise SystemExit(str(error)) from error
        return v13_agent, deep_learning_config("v13")
    if arguments.example == "v15":
        if not arguments.checkpoint:
            raise SystemExit("V15 requires --checkpoint")
        try:
            from .v15_agent import load_v15_agent

            v15_agent = load_v15_agent(arguments.checkpoint, device=arguments.device)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise SystemExit(str(error)) from error
        return v15_agent, deep_learning_config("v15")
    if is_oracle_checkpoint(arguments.checkpoint):
        return (
            OracleCheckpointAgent(arguments.checkpoint, device=arguments.device),
            deep_learning_config(arguments.example),
        )
    try:
        from .deep_learning import build_deep_learning_agent
    except ModuleNotFoundError as error:
        if error.name != "torch":
            raise
        raise SystemExit(
            'V11/V12 requires the optional dependency: pip install -e ".[deep-learning]"'
        ) from error
    try:
        agent = build_deep_learning_agent(
            arguments.example,
            checkpoint=arguments.checkpoint,
            device=arguments.device,
            allow_untrained=arguments.allow_untrained,
            seed=arguments.seed,
        )
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error
    return agent, deep_learning_config(arguments.example)


async def _serve_local(runner: AgentRunner, arguments: argparse.Namespace) -> None:
    connection = asyncio.create_task(runner.serve())
    try:
        if arguments.start_local_game:
            local_game_setup = getattr(arguments, "local_game_setup", None)
            missing = (
                []
                if local_game_setup
                else [
                    name
                    for name, value in (
                        ("DEEPDECK_LOCAL_DECK_SESSION_ID", arguments.local_deck_session_id),
                        (
                            "DEEPDECK_LOCAL_OPPONENT_DECK_SESSION_ID",
                            arguments.local_opponent_deck_session_id,
                        ),
                    )
                    if not value
                ]
            )
            if missing:
                raise SystemExit(f"Missing local game configuration: {', '.join(missing)}")
            controller_id = await runner.wait_until_connected(timeout=30)
            game_format = arguments.local_format or runner.config.formats[0]
            if game_format not in runner.config.formats:
                supported = ", ".join(runner.config.formats)
                raise SystemExit(
                    f"{runner.config.name} does not declare {game_format}; supported: {supported}"
                )
            starting_life = 40 if game_format == "commander" else 20
            if local_game_setup:
                payload = json.loads(Path(local_game_setup).read_text(encoding="utf-8"))
                payload["aiControllerByPlayerId"] = {"local-agent": controller_id}
                payload["decisionClock"] = {
                    "initialTimeMs": arguments.clock_initial_ms,
                    "incrementMs": arguments.clock_increment_ms,
                    "delayMs": arguments.clock_delay_ms,
                    "maxDecisionMs": arguments.clock_max_decision_ms,
                    "reserveMs": arguments.clock_reserve_ms,
                }
                headers = (
                    {"x-mtg-api-key": runner.target.engine_api_key}
                    if runner.target.engine_api_key
                    else None
                )
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        f"{arguments.engine_url.rstrip('/')}/game/sessions",
                        headers=headers,
                        json=payload,
                    )
                    if response.is_error:
                        raise RuntimeError(
                            f"Engine rejected the local game ({response.status_code}): "
                            f"{response.text}"
                        )
                    bootstrap = response.json()
            else:
                bootstrap = await runner.start_local_game(
                    LocalGame(
                        format=game_format,
                        seed=arguments.seed,
                        max_turns=arguments.local_max_turns,
                        free_mulligans=1 if game_format == "commander" else 0,
                        players=(
                            LocalPlayer(
                                player_id="example-agent",
                                deck_session_id=arguments.local_deck_session_id,
                                controller_id=controller_id,
                                name=runner.config.name,
                                starting_life=starting_life,
                            ),
                            LocalPlayer(
                                player_id="local-opponent",
                                deck_session_id=arguments.local_opponent_deck_session_id,
                                controller_id=arguments.local_opponent_controller,
                                name="Local opponent",
                                starting_life=starting_life,
                            ),
                        ),
                    )
                )
            session = bootstrap.get("session", bootstrap)
            session_id = (
                session.get("id") or session.get("sessionId") if isinstance(session, dict) else None
            )
            if session_id:
                print(
                    "DEEPDECK_PLAYTEST_SESSION "
                    + json.dumps({"sessionId": session_id, "engineUrl": arguments.engine_url}),
                    flush=True,
                )
            logging.getLogger(__name__).info("local game started: %s", session_id or bootstrap)
        await connection
    finally:
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)


async def run(arguments: argparse.Namespace) -> None:
    limits = LeagueRunLimits(
        getattr(arguments, "league_max_matches", None),
        getattr(arguments, "league_duration_seconds", None),
    )
    if limits.argv() and (
        arguments.target != "ddl" or arguments.once and limits.max_matches is not None
    ):
        raise ValueError(
            "League run limits require the ddl target; "
            "a match-count limit cannot be combined with --once."
        )

    def stopped() -> bool:
        return stop_requested(arguments.league_stop_file, arguments.league_dispatch_sequence)

    if arguments.target == "ddl" and stopped():
        write_result(
            arguments.league_result_file,
            run_id=arguments.league_run_id,
            completed=0,
            elapsed=0.0,
            outcome="cancelled",
            match_id=None,
        )
        return
    agent, config = _agent_and_config(arguments)
    target = (
        ServerTarget.local(arguments.engine_url)
        if arguments.target == "local"
        else ServerTarget.deepdeckleague(
            platform_url=league_platform_url(), agent_url=league_agent_url()
        )
    )
    runner = AgentRunner(agent=agent, config=config, target=target, speed=arguments.speed)
    logging.getLogger(__name__).info(
        "connecting %s to %s as %s",
        config.name,
        target.agent_url,
        config.agent_id,
    )
    if arguments.target == "local":
        await _serve_local(runner, arguments)
        return
    if not arguments.deck_version_id:
        raise ValueError("Choose a published League deck version.")
    game_format = arguments.league_format
    if game_format is None:
        if len(config.formats) != 1:
            raise ValueError(
                "Choose --league-format legacy or commander for this multi-format agent."
            )
        game_format = config.formats[0]
    if game_format not in config.formats:
        raise ValueError("This agent does not support the selected League format.")
    entry = LeagueEventEntry(
        format=game_format,
        competition_version_id=arguments.competition_version_id,
        event_id=arguments.league_event_id,
        deck_version_ids=tuple(
            dict.fromkeys(
                [
                    arguments.deck_version_id,
                    *arguments.additional_deck_version_id,
                ]
            )
        ),
    )
    budget = LeagueRunBudget(limits, stop_requested=stopped)
    if budget.expired():
        write_result(
            arguments.league_result_file,
            run_id=arguments.league_run_id,
            completed=0,
            elapsed=0.0,
            outcome="cancelled",
            match_id=None,
        )
        return
    last_outcome: str | None = None
    last_match_id: str | None = None
    registration_state: dict[str, Any] = {
        "serverRegistered": False,
        "registrationGeneration": 0,
        "eventRegistered": False,
        "eventId": None,
        "phase": None,
        "waitingForRegistration": True,
        "draining": False,
    }

    def report_run() -> None:
        print(
            "DEEPDECK_LEAGUE_RUN "
            + json.dumps(
                {
                    "completedEntries": budget.completed,
                    "activeEntries": budget.reserved,
                    "maxMatches": limits.max_matches,
                    "durationSeconds": limits.duration_seconds,
                    **registration_state,
                }
            ),
            flush=True,
        )

    report_run()

    def registration_changed(registered: bool, generation: int) -> None:
        registration_state.update(serverRegistered=registered, registrationGeneration=generation)
        report_run()

    connection = asyncio.create_task(runner.serve())
    registration_monitor = asyncio.create_task(watch_registration(runner, registration_changed))
    observed_matches: dict[str, str] = {}
    collector = None
    replay_wake = asyncio.Event()
    replay_task = None
    if arguments.replay_root and arguments.replay_model_id:
        from deepdeck_learner.league_replays import LeagueReplayCollector

        collector = LeagueReplayCollector(arguments.replay_root, arguments.replay_model_id)

    async def sync_replays() -> None:
        from deepdeck_learner.experience import experience_settings

        assert collector is not None
        while True:
            replay_wake.clear()
            try:
                result = await asyncio.to_thread(collector.collect)
                print("DEEPDECK_REPLAY_SYNC " + json.dumps(result), flush=True)
            except (OSError, ValueError) as error:
                logging.getLogger(__name__).warning("Replay collection will retry: %s", error)
            delay = experience_settings(collector.root)["replayPollSeconds"]
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(replay_wake.wait(), timeout=delay)

    if collector is not None:
        replay_task = asyncio.create_task(sync_replays())

    async def event_changed(event: dict[str, Any] | None) -> None:
        nonlocal last_outcome, last_match_id
        if event is None:
            registration_state.update(
                eventRegistered=False,
                eventId=None,
                phase=None,
                waitingForRegistration=True,
                draining=False,
            )
            report_run()
            return
        phase = event["phase"]
        registration_state.update(
            eventRegistered=True,
            eventId=event["id"],
            phase=phase,
            registrationClosesAt=event.get("registrationClosesAt"),
            serverNow=event.get("serverNow"),
            waitingForRegistration=False,
            draining=not budget.may_enroll() and phase not in {"complete", "cancelled"},
            selectedDeckVersionId=event["ownEntry"]["deckVersionId"],
            mutationUncertain=False,
            lastError=None,
            statusReason=event.get("statusReason"),
        )
        report_run()
        for own_match in event["ownMatches"]:
            match_id = own_match["matchId"]
            status = own_match.get("status")
            if status == "complete":
                last_outcome, last_match_id = "complete", match_id
            if observed_matches.get(match_id) == status and status in {
                "complete",
                "failed",
                "cancelled",
            }:
                continue
            try:
                match = await runner.match(match_id)
            except (httpx.HTTPError, ValueError):
                # Match presentation/replay availability must not disconnect an
                # enrolled competitor. ownMatches remains the result authority.
                continue
            snapshot = _league_match_snapshot(match_id, match, runner.target.platform_url)
            snapshot.update(eventId=event["id"], stage=own_match.get("stage"))
            print("DEEPDECK_LEAGUE_MATCH " + json.dumps(snapshot), flush=True)
            if (
                collector is not None
                and own_match.get("stage") == "playoffs"
                and collector.record_match(snapshot)
            ):
                replay_wake.set()
            observed_matches[match_id] = status
        if phase == "cancelled" and last_outcome is None:
            last_outcome = "cancelled"

    def enrollment_uncertain(error: BaseException) -> None:
        registration_state.update(mutationUncertain=True, lastError=league_failure_message(error))
        report_run()

    try:
        await asyncio.sleep(0)
        await runner.wait_until_connected(timeout=30)
        # One entry for this agent; other owned agents have independent workers.
        # Server-side capacity is authoritative; never multiply enrollment POSTs.
        await serve_event_cycles(
            runner,
            entry,
            budget,
            once=arguments.once,
            on_event=event_changed,
            on_uncertain=enrollment_uncertain,
        )
    except Exception as error:
        registration_state["lastError"] = league_failure_message(error)
        report_run()
        raise
    finally:
        registration_monitor.cancel()
        await asyncio.gather(registration_monitor, return_exceptions=True)
        if replay_task is not None:
            replay_task.cancel()
            await asyncio.gather(replay_task, return_exceptions=True)
        if collector is not None and (arguments.once or limits.argv()):
            result = await asyncio.to_thread(collector.collect)
            print("DEEPDECK_REPLAY_SYNC " + json.dumps(result), flush=True)
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        registration_state["serverRegistered"] = False
        report_run()
    write_result(
        arguments.league_result_file,
        run_id=arguments.league_run_id,
        completed=budget.completed,
        elapsed=budget.clock() - budget.started,
        outcome=last_outcome,
        match_id=last_match_id,
        completed_match_ids=sorted(budget.completed_match_ids),
    )


def _unix_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _league_match_snapshot(
    match_id: str,
    match: dict[str, Any],
    platform_url: str | None,
) -> dict[str, Any]:
    summary = match.get("summary")
    if not isinstance(summary, dict):
        summary = match
    participants = summary.get("participants", [])
    if not isinstance(participants, list):
        participants = []
    games = match.get("games", [])
    if not isinstance(games, list):
        games = []
    current_game = next(
        (game for game in games if isinstance(game, dict) and game.get("status") == "running"),
        next((game for game in reversed(games) if isinstance(game, dict)), {}),
    )
    status = str(summary.get("status", "scheduled"))
    frontend_url = (platform_url or DEFAULT_PLATFORM_URL).removesuffix("/api/v1")
    return {
        "matchId": match_id,
        "platformUrl": platform_url or DEFAULT_PLATFORM_URL,
        "gameId": current_game.get("id"),
        "status": status,
        "format": summary.get("format"),
        "games": [
            {key: game.get(key) for key in ("id", "status", "number", "startedAt", "completedAt")}
            for game in games
            if isinstance(game, dict)
        ],
        "decks": [
            participant.get("deck") for participant in participants if isinstance(participant, dict)
        ],
        "deckDetails": [
            {"id": participant.get("deckVersionId"), "name": participant.get("deck")}
            for participant in participants
            if isinstance(participant, dict)
        ],
        "players": len(participants),
        "turnNumber": current_game.get("turnCount"),
        "roundNumber": current_game.get("number"),
        "decisions": 0,
        "startedAtUnixMs": _unix_ms(current_game.get("startedAt") or summary.get("startedAt")),
        "updatedAtUnixMs": int(datetime.now().timestamp() * 1000),
        "watchUrl": f"{frontend_url}/matches?tab=games&match={match_id}&replay=1&seat=0",
    }


def _matchmaking_entries(arguments: argparse.Namespace) -> list[MatchmakingEntry]:
    """Legacy helper for older integrations; the active DDL path never uses tickets."""
    concurrency = max(1, min(32, int(arguments.matchmaking_concurrency)))
    deck_ids = tuple(
        dict.fromkeys([arguments.deck_version_id, *arguments.additional_deck_version_id])
    )
    return [
        MatchmakingEntry(
            competition_version_id=arguments.competition_version_id,
            client_seat_id=f"learner:seat-{index + 1}",
            deck_version_ids=deck_ids,
        )
        for index in range(concurrency)
    ]


def main() -> None:
    dotenv_path = None if frozen() else find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    arguments = parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, arguments.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(arguments))


if __name__ == "__main__":
    main()
