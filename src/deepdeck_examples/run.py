from __future__ import annotations

import argparse
import asyncio
import logging
import os

from deepdeck_agent import Agent, AgentRunner, PlaySpeed, ServerTarget

from .alexios import AlexiosAgent
from .configuration import alexios_config, random_config
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
        choices=("random", "alexios"),
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
    result.add_argument("--log-level", default="INFO")
    return result


async def run(arguments: argparse.Namespace) -> None:
    if arguments.example == "random":
        agent: Agent = build_random_agent(arguments.seed)
        config = random_config()
    else:
        agent = AlexiosAgent()
        config = alexios_config()
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
    await runner.serve()


def main() -> None:
    arguments = parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, arguments.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(arguments))


if __name__ == "__main__":
    main()
