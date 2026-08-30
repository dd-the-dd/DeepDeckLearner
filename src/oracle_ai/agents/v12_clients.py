from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from oracle_ai.agents.client import AgentClient
from oracle_ai.agents.model_agent import OracleModelAgent
from oracle_ai.agents.protocol import (
    AgentAuthor,
    AgentCapabilities,
    AgentCompatibility,
    AgentManifest,
    AgentRepository,
    DeckSelection,
    GameSharing,
    ObservationStream,
    TimeoutCategory,
)
from oracle_ai.app import runtime

LOGGER = logging.getLogger("oracle_ai.v12_clients")
SEATS = ("a", "b", "c", "d")
ROTATION_FORMATS = ("commander", "legacy")


@dataclass(frozen=True)
class ModelProfile:
    generation: str
    family: str
    agent_prefix: str
    name: str
    game_modes: tuple[str, ...]


V11_PROFILE = ModelProfile(
    generation="v11",
    family="structured-v11",
    agent_prefix="deepdeck-v11",
    name="Deep Deck V11",
    game_modes=("commander",),
)
V12_PROFILE = ModelProfile(
    generation="v12",
    family="structured-v12",
    agent_prefix="deepdeck-v12",
    name="Deep Deck V12",
    game_modes=ROTATION_FORMATS,
)
V12_1_PROFILE = ModelProfile(
    generation="v12.1",
    family="structured-v12",
    agent_prefix="deepdeck-v12-1",
    name="Deep Deck V12.1",
    game_modes=("legacy",),
)
MODEL_PROFILES = {
    "structured-v11": V11_PROFILE,
    "structured-v12": V12_PROFILE,
}
MODEL_PROFILE_OVERRIDES = {
    "v11": V11_PROFILE,
    "v12": V12_PROFILE,
    "v12.1": V12_1_PROFILE,
    "v12-1": V12_1_PROFILE,
}


def install_host_override(host: str, address: str) -> None:
    """Resolve one HTTPS host to a pinned address while preserving TLS SNI."""
    original_getaddrinfo = socket.getaddrinfo
    normalized_host = host.strip().lower()
    normalized_address = address.strip()
    if not normalized_host or not normalized_address:
        return

    def getaddrinfo(
        query_host: str | bytes | None,
        *args: object,
        **kwargs: object,
    ) -> list[tuple[object, ...]]:
        resolved_host: str | bytes | None = query_host
        if isinstance(query_host, str) and query_host.lower() == normalized_host:
            resolved_host = normalized_address
        return original_getaddrinfo(resolved_host, *args, **kwargs)

    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]


def build_manifest(
    seat: str,
    version: str,
    profile: ModelProfile,
    timeout_category: TimeoutCategory = TimeoutCategory.STANDARD,
) -> AgentManifest:
    return AgentManifest(
        agent_id=f"{profile.agent_prefix}-seat-{seat}",
        name=f"{profile.name} — seat {seat.upper()}",
        version=version,
        description=(
            "A dedicated Deep Deck League client identity backed by the shared "
            f"IA {profile.generation.upper()} inference runtime."
        ),
        authors=[AgentAuthor(name="Deep Deck League")],
        repository=AgentRepository(
            url="https://github.com/deepdeckleague/mtg-oracle-engine"
        ),
        compatibility=AgentCompatibility(
            game_modes=list(profile.game_modes),
            decks=DeckSelection(selection="all"),
            time_controls=[timeout_category],
            observation_streams=[ObservationStream.FULL],
            game_sharing=[GameSharing.PUBLIC_REPLAY],
        ),
        capabilities=AgentCapabilities(
            starting_situation_analysis=True,
            stateful_memory=True,
        ),
    )


async def run_seat(
    seat: str,
    *,
    url: str,
    api_key: str | None,
    account_token: str | None,
    version: str,
    profile: ModelProfile,
    timeout_category: TimeoutCategory,
    retry_seconds: float,
    ready: asyncio.Event,
) -> None:
    while True:
        client = AgentClient(
            url,
            build_manifest(seat, version, profile, timeout_category),
            OracleModelAgent(runtime),
            observation_stream=ObservationStream.FULL,
            timeout_category=timeout_category,
            game_sharing=GameSharing.PUBLIC_REPLAY,
            api_key=api_key,
            account_token=account_token,
        )
        task = asyncio.create_task(
            client.run(), name=f"{profile.generation}-seat-{seat}"
        )
        try:
            while client.registration is None and not task.done():
                await asyncio.sleep(0.1)
            if client.registration is not None:
                ready.set()
                LOGGER.info(
                    "seat %s registered as %s on %s",
                    seat.upper(),
                    client.registration.controller_id,
                    url,
                )
            await task
            raise ConnectionError("the engine closed the agent connection")
        except asyncio.CancelledError:
            ready.clear()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Exception as error:
            ready.clear()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            LOGGER.warning(
                "seat %s disconnected (%s); retrying in %.1fs",
                seat.upper(),
                error,
                retry_seconds,
            )
            await asyncio.sleep(retry_seconds)


def request_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


async def wait_until_ready(ready_by_seat: dict[str, asyncio.Event]) -> None:
    await asyncio.gather(*(ready.wait() for ready in ready_by_seat.values()))


async def run_random_rotation(
    *,
    platform_url: str,
    token: str,
    version: str,
    generation: str,
    rotation_formats: tuple[str, ...],
    max_completed_matches: int | None,
    ready_by_seat: dict[str, asyncio.Event],
    poll_seconds: float,
) -> None:
    platform_url = platform_url.rstrip("/")
    format_index = 0
    completed_matches = 0
    while max_completed_matches is None or completed_matches < max_completed_matches:
        await wait_until_ready(ready_by_seat)
        game_format = rotation_formats[format_index]
        try:
            response = await asyncio.to_thread(
                request_json,
                "POST",
                f"{platform_url}/v1/matchmaking/exhibitions/random",
                token=token,
                payload={
                    "format": game_format,
                    "agentVersion": version,
                    "agentGeneration": generation,
                },
            )
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code in {409, 503}:
                LOGGER.info("rotation waiting for capacity: HTTP %s %s", error.code, detail)
            else:
                LOGGER.warning("rotation request failed: HTTP %s %s", error.code, detail)
            await asyncio.sleep(poll_seconds)
            continue
        except Exception as error:
            LOGGER.warning("rotation could not reach the platform (%s)", error)
            await asyncio.sleep(poll_seconds)
            continue

        exhibition = response.get("data", {})
        match_id = str(exhibition.get("matchId", ""))
        if not match_id:
            LOGGER.warning("rotation response omitted matchId: %s", response)
            await asyncio.sleep(poll_seconds)
            continue
        scheduled_format = str(exhibition.get("format", game_format)).lower()
        LOGGER.info(
            "%s match %s scheduled with decks: %s",
            scheduled_format.title(),
            match_id,
            ", ".join(str(deck) for deck in exhibition.get("decks", [])),
        )

        while True:
            try:
                match_response = await asyncio.to_thread(
                    request_json,
                    "GET",
                    f"{platform_url}/v1/matches/{match_id}",
                )
                status = str(
                    match_response.get("data", {})
                    .get("summary", {})
                    .get("status", "unknown")
                )
                if status in {"complete", "failed", "cancelled"}:
                    LOGGER.info("match %s reached terminal status %s", match_id, status)
                    if status == "complete":
                        completed_matches += 1
                    break
            except Exception as error:
                LOGGER.warning("rotation could not read match %s (%s)", match_id, error)
            await asyncio.sleep(poll_seconds)
        if scheduled_format in rotation_formats:
            format_index = (
                rotation_formats.index(scheduled_format) + 1
            ) % len(rotation_formats)
        else:
            format_index = (format_index + 1) % len(rotation_formats)

    LOGGER.info("rotation completed %s successful matches", completed_matches)


async def run_clients(args: argparse.Namespace) -> None:
    public_agent_auth = args.public_agent_auth
    if public_agent_auth:
        credential = args.api_key or os.getenv("DEEPDECK_API_KEY")
        if not credential:
            raise ValueError(
                "DEEPDECK_API_KEY is required for public account-owned matchmaking"
            )
        url = args.url or "wss://staging.deepdeckleague.com/api/v1/agents/connect"
        engine_api_key = None
        account_token = credential
        timeout_category = TimeoutCategory.EXTENDED
    else:
        credential = args.api_key or os.getenv("MTG_ENGINE_API_KEY")
        if not credential:
            raise ValueError("MTG_ENGINE_API_KEY is required")
        url = args.url or "ws://127.0.0.1:8787/ai/agents/ws"
        engine_api_key = credential
        account_token = None
        timeout_category = TimeoutCategory.STANDARD
    family = getattr(runtime.model, "model_family", None)
    profile = MODEL_PROFILES.get(family)
    if profile is None:
        raise ValueError(
            "the configured checkpoint is not V11 or V12 "
            f"(model family: {family!r})"
        )
    requested_profile = args.model_profile.strip().lower()
    if requested_profile:
        profile = MODEL_PROFILE_OVERRIDES.get(requested_profile)
        if profile is None:
            raise ValueError(
                "--model-profile must be v11, v12 or v12.1"
            )
        if profile.family != family:
            raise ValueError(
                f"{requested_profile} requires {profile.family}, "
                f"but the checkpoint is {family}"
            )
    rotation_formats = tuple(
        value.strip().lower()
        for value in args.rotation_formats.split(",")
        if value.strip()
    )
    if not rotation_formats:
        raise ValueError("--rotation-formats must include at least one format")
    unsupported = set(rotation_formats) - set(profile.game_modes)
    if unsupported:
        raise ValueError(
            f"{profile.generation} does not accept: {', '.join(sorted(unsupported))}"
        )
    version = args.version or f"{profile.generation}-step-{runtime.training_step}"
    platform_host_ip = os.getenv("DDL_PLATFORM_HOST_IP", "").strip()
    platform_host = urllib.parse.urlsplit(args.platform_url).hostname
    if platform_host_ip and platform_host:
        install_host_override(platform_host, platform_host_ip)
        LOGGER.info("platform host %s pinned to %s", platform_host, platform_host_ip)
    additional_urls = tuple(
        value.strip()
        for value in args.additional_urls.split(",")
        if value.strip() and value.strip() != url
    )
    agent_urls = tuple(dict.fromkeys((url, *additional_urls)))
    LOGGER.info(
        "starting four %s clients on %s engine endpoint(s) with one shared %s model at step %s on %s",
        "account-owned public" if public_agent_auth else "private-engine",
        len(agent_urls),
        family,
        runtime.training_step,
        runtime.device,
    )
    ready_by_seat = {seat: asyncio.Event() for seat in SEATS}
    seat_tasks = []
    for endpoint_index, agent_url in enumerate(agent_urls):
        endpoint_ready = (
            ready_by_seat
            if endpoint_index == 0
            else {seat: asyncio.Event() for seat in SEATS}
        )
        seat_tasks.extend(
            run_seat(
                seat,
                url=agent_url,
                api_key=engine_api_key,
                account_token=account_token,
                version=version,
                profile=profile,
                timeout_category=timeout_category,
                retry_seconds=args.retry_seconds,
                ready=endpoint_ready[seat],
            )
            for seat in SEATS
        )
    if args.rotate_random_matches:
        rotation_token = (
            credential if public_agent_auth else os.getenv("DDL_MATCH_WORKER_TOKEN")
        )
        if not rotation_token:
            raise ValueError("DDL_MATCH_WORKER_TOKEN is required for random match rotation")
        # Each format owns its own bounded scheduler.  A single alternating loop
        # left one format idle for the whole duration of the other match.
        rotation_tasks = [run_random_rotation(
                platform_url=args.platform_url,
                token=rotation_token,
                version=version,
                generation=profile.generation,
                rotation_formats=(game_format,),
                max_completed_matches=(
                    args.max_random_matches if args.max_random_matches > 0 else None
                ),
                ready_by_seat=ready_by_seat,
                poll_seconds=args.rotation_poll_seconds,
            )
            for game_format in rotation_formats
        ]
        if args.exit_after_rotation:
            seat_handles = [asyncio.create_task(task) for task in seat_tasks]
            try:
                await asyncio.gather(*rotation_tasks)
            finally:
                for task in seat_handles:
                    task.cancel()
                await asyncio.gather(*seat_handles, return_exceptions=True)
            return
        await asyncio.gather(*seat_tasks, *rotation_tasks)
        return
    await asyncio.gather(*seat_tasks)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Connect four IA V11 or V12 identities to the MTG engine."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("MTG_ENGINE_AGENT_URL") or None,
    )
    parser.add_argument(
        "--additional-urls",
        default=os.getenv("MTG_ENGINE_ADDITIONAL_AGENT_URLS", ""),
        help="Comma-separated additional engine WebSocket endpoints served by the same loaded model.",
    )
    parser.add_argument("--api-key", help=argparse.SUPPRESS)
    parser.add_argument(
        "--public-agent-auth",
        action="store_true",
        default=os.getenv("DEEPDECK_PUBLIC_AGENT_AUTH", "").lower()
        in {"1", "true", "yes"},
        help="Connect through the public DDL agent gateway with DEEPDECK_API_KEY.",
    )
    parser.add_argument("--version")
    parser.add_argument(
        "--model-profile",
        default=os.getenv("DDL_MODEL_PROFILE", ""),
        help="Public model identity override (v11, v12 or frozen v12.1).",
    )
    parser.add_argument("--retry-seconds", type=float, default=2.0)
    parser.add_argument(
        "--rotate-random-matches",
        action="store_true",
        default=os.getenv("DDL_V12_RANDOM_ROTATION", "").lower() in {"1", "true", "yes"},
    )
    parser.add_argument(
        "--platform-url",
        default=os.getenv("DDL_PLATFORM_URL", "https://staging.deepdeckleague.com/api"),
    )
    parser.add_argument("--rotation-poll-seconds", type=float, default=5.0)
    parser.add_argument(
        "--rotation-formats",
        default=os.getenv("DDL_RANDOM_ROTATION_FORMATS", ",".join(ROTATION_FORMATS)),
        help="Comma-separated rotation formats (commander,legacy).",
    )
    parser.add_argument("--max-random-matches", type=int, default=0)
    parser.add_argument("--exit-after-rotation", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_clients(args))


if __name__ == "__main__":
    main()
