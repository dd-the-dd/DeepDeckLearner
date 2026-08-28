from __future__ import annotations

import argparse
import asyncio
import logging
import os

from deepdeck_agent import (
    Agent,
    AgentConfig,
    AgentRunner,
    LocalGame,
    LocalPlayer,
    MatchmakingEntry,
    PlaySpeed,
    ServerTarget,
)
from dotenv import find_dotenv, load_dotenv

from .alexios import AlexiosAgent
from .configuration import alexios_config, deep_learning_config, random_config
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
        choices=("random", "alexios", "v11", "v12"),
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
        "--checkpoint",
        default=os.getenv("DEEPDECK_CHECKPOINT"),
        help="V11/V12 checkpoint directory containing config.json and model.pt.",
    )
    result.add_argument(
        "--device",
        default=os.getenv("DEEPDECK_DEVICE", "cpu"),
        help="PyTorch device used by V11/V12, for example cpu or cuda.",
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
        "--deck-version-id",
        default=os.getenv("DEEPDECK_DECK_VERSION_ID"),
    )
    result.add_argument(
        "--once",
        action="store_true",
        help="Play one public match instead of rejoining matchmaking continuously.",
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
            missing = [
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
            session = bootstrap.get("session", {})
            session_id = session.get("id") if isinstance(session, dict) else None
            logging.getLogger(__name__).info("local game started: %s", session_id or bootstrap)
        await connection
    finally:
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)


async def run(arguments: argparse.Namespace) -> None:
    agent, config = _agent_and_config(arguments)
    target = (
        ServerTarget.local(arguments.engine_url)
        if arguments.target == "local"
        else ServerTarget.deepdeckleague()
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
    matchmaking_ids = {
        "DEEPDECK_COMPETITION_VERSION_ID": arguments.competition_version_id,
        "DEEPDECK_DECK_VERSION_ID": arguments.deck_version_id,
    }
    missing = [name for name, value in matchmaking_ids.items() if not value]
    if missing:
        raise SystemExit(f"Missing public matchmaking configuration: {', '.join(missing)}")
    await runner.serve_matchmaking(
        MatchmakingEntry(
            competition_version_id=arguments.competition_version_id,
            deck_version_id=arguments.deck_version_id,
        ),
        continuous=not arguments.once,
    )


def main() -> None:
    dotenv_path = find_dotenv(usecwd=True)
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
