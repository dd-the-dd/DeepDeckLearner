from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import httpx
from deepdeck_agent import Agent, AgentRunner, MatchmakingEntry, PlaySpeed, ServerTarget
from dotenv import find_dotenv, load_dotenv

from .alexios import AlexiosAgent
from .commanders import build_commander_agent
from .configuration import commander_config
from .omnath import OmnathAgent

OFFICIAL_PLATFORM_URL = "https://www.deepdeckleague.com/api/v1"
COMMANDER_COMPETITION_VERSION_ID = "45000000-0000-0000-0000-000000000004"
POLICY_DECK_NAMES = {
    "alexios": "Alexios",
    "omnath": "Omnath",
    "aang": "Aang",
    "azula": "Azula",
    "katara": "Katara",
    "kellan": "Kellan the kid",
    "tatyova": "Tatyova",
    "toph": "Toph",
    "yenna": "Yenna",
    "sektuar": "Sek'tuar",
}
QUALIFIER_PODS = (
    ("alexios", "aang", "azula", "katara"),
    ("omnath", "kellan", "tatyova", "toph"),
)


@dataclass(frozen=True)
class PodResult:
    name: str
    match_id: str
    winner: str
    entrants: tuple[str, ...]
    watch_url: str

    def mapping(self) -> dict[str, object]:
        return {
            "name": self.name,
            "matchId": self.match_id,
            "winner": self.winner,
            "entrants": list(self.entrants),
            "watchUrl": self.watch_url,
        }


def _agent(key: str) -> Agent:
    if key == "alexios":
        return AlexiosAgent()
    if key == "omnath":
        return OmnathAgent()
    return build_commander_agent(key)


def _deck_versions_from_items(items: object) -> dict[str, str]:
    if not isinstance(items, list):
        raise ValueError("the Commander deck catalog response has no item list")
    by_name = {
        str(item.get("name")): str(item.get("id"))
        for item in items
        if isinstance(item, dict) and item.get("name") and item.get("id")
    }
    missing = [name for name in POLICY_DECK_NAMES.values() if name not in by_name]
    if missing:
        raise ValueError(
            "the official Commander pool is missing: " + ", ".join(sorted(missing))
        )
    return {key: by_name[name] for key, name in POLICY_DECK_NAMES.items()}


async def _resolve_deck_versions(platform_url: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{platform_url}/decks",
            params={"page": 1, "page_size": 100, "format": "commander"},
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    return _deck_versions_from_items(data.get("items") if isinstance(data, dict) else None)


def _match_summary(match: dict[str, Any]) -> dict[str, Any]:
    summary = match.get("summary", match)
    return summary if isinstance(summary, dict) else {}


def _winner_key(match: dict[str, Any], deck_versions: dict[str, str]) -> str:
    participants = _match_summary(match).get("participants", [])
    if not isinstance(participants, list):
        raise RuntimeError("the completed match has no participant results")
    version_to_key = {version_id: key for key, version_id in deck_versions.items()}
    winners = [
        version_to_key.get(str(participant.get("deckVersionId")))
        for participant in participants
        if isinstance(participant, dict) and int(participant.get("placement", 0)) == 1
    ]
    concrete_winners = [winner for winner in winners if winner is not None]
    if len(concrete_winners) != 1:
        raise RuntimeError(f"expected one pod winner, received {concrete_winners!r}")
    return concrete_winners[0]


async def _wait_for_complete_match(
    runner: AgentRunner,
    match_id: str,
    poll_seconds: float,
) -> dict[str, Any]:
    while True:
        match = await runner.match(match_id)
        status = str(_match_summary(match).get("status", ""))
        print(
            "DEEPDECK_JOUST_PROGRESS "
            + json.dumps({"matchId": match_id, "status": status}),
            flush=True,
        )
        if status == "complete":
            return match
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"official match {match_id} ended with status {status}")
        await asyncio.sleep(poll_seconds)


async def _play_pod(
    name: str,
    entrants: tuple[str, str, str, str],
    runners: dict[str, AgentRunner],
    deck_versions: dict[str, str],
    competition_version_id: str,
    platform_url: str,
    poll_seconds: float,
) -> PodResult:
    entries = {
        key: MatchmakingEntry(
            competition_version_id=competition_version_id,
            deck_version_id=deck_versions[key],
            client_seat_id=f"official-joust:{name}:{key}",
        )
        for key in entrants
    }
    tickets: dict[str, dict[str, Any]] = {}
    try:
        joined = await asyncio.gather(
            *(runners[key].join_matchmaking(entries[key]) for key in entrants)
        )
        tickets = dict(zip(entrants, joined, strict=True))
        match_ids = await asyncio.gather(
            *(
                runners[key]._wait_for_match_id(tickets[key], poll_seconds)  # noqa: SLF001
                for key in entrants
            )
        )
        concrete_ids = {match_id for match_id in match_ids if match_id is not None}
        if len(concrete_ids) != 1:
            raise RuntimeError(
                f"pod {name} did not form one official match: {sorted(concrete_ids)}"
            )
        match_id = concrete_ids.pop()
        match = await _wait_for_complete_match(runners[entrants[0]], match_id, poll_seconds)
        winner = _winner_key(match, deck_versions)
        frontend = platform_url.removesuffix("/api/v1")
        result = PodResult(
            name=name,
            match_id=match_id,
            winner=winner,
            entrants=entrants,
            watch_url=f"{frontend}/matches?tab=games&match={match_id}&replay=1&seat=0",
        )
        print("DEEPDECK_JOUST_POD " + json.dumps(result.mapping()), flush=True)
        return result
    finally:
        await asyncio.gather(
            *(
                runners[key].cancel_matchmaking_ticket(str(ticket["id"]))
                for key, ticket in tickets.items()
                if ticket.get("status") == "queued" and ticket.get("id")
            ),
            return_exceptions=True,
        )


async def run_joust(arguments: argparse.Namespace) -> list[PodResult]:
    platform_url = str(arguments.platform_url).rstrip("/")
    if platform_url != OFFICIAL_PLATFORM_URL and not arguments.allow_non_production:
        raise SystemExit(
            f"the official joust requires {OFFICIAL_PLATFORM_URL}; "
            "use --allow-non-production only for an intentional rehearsal"
        )
    deck_versions = await _resolve_deck_versions(platform_url)
    print(
        "DEEPDECK_JOUST_DECKS "
        + json.dumps(
            {
                key: {"name": POLICY_DECK_NAMES[key], "versionId": version_id}
                for key, version_id in deck_versions.items()
            }
        ),
        flush=True,
    )
    if arguments.dry_run:
        return []

    api_key = os.getenv("DEEPDECK_API_KEY") or os.getenv("DEEPDECK_ACCESS_TOKEN")
    if not api_key:
        raise SystemExit(
            "DEEPDECK_API_KEY is required. Create an account agent key on the DDL Account "
            "page and save it only in the local .env file."
        )
    target = ServerTarget.deepdeckleague(platform_url=platform_url, api_key=api_key)
    runners = {
        key: AgentRunner(
            agent=_agent(key),
            config=commander_config(
                key,
                deck_id=deck_versions[key],
                agent_id=f"com.deepdeckleague.official-joust.{key}",
                version=arguments.agent_version,
            ),
            target=target,
            speed=PlaySpeed.SECONDS_10,
        )
        for key in POLICY_DECK_NAMES
    }
    connections = [asyncio.create_task(runner.serve()) for runner in runners.values()]
    try:
        await asyncio.gather(
            *(
                runner.wait_until_connected(timeout=arguments.connection_timeout)
                for runner in runners.values()
            )
        )
        first = await _play_pod(
            "qualifier-a",
            QUALIFIER_PODS[0],
            runners,
            deck_versions,
            arguments.competition_version_id,
            platform_url,
            arguments.poll_seconds,
        )
        second = await _play_pod(
            "qualifier-b",
            QUALIFIER_PODS[1],
            runners,
            deck_versions,
            arguments.competition_version_id,
            platform_url,
            arguments.poll_seconds,
        )
        championship_entrants = ("yenna", "sektuar", first.winner, second.winner)
        if len(set(championship_entrants)) != 4:
            raise RuntimeError(f"championship entrants are not unique: {championship_entrants}")
        championship = await _play_pod(
            "championship",
            championship_entrants,
            runners,
            deck_versions,
            arguments.competition_version_id,
            platform_url,
            arguments.poll_seconds,
        )
        results = [first, second, championship]
        print(
            "DEEPDECK_JOUST_RESULT "
            + json.dumps(
                {
                    "champion": championship.winner,
                    "pods": [result.mapping() for result in results],
                }
            ),
            flush=True,
        )
        return results
    finally:
        for connection in connections:
            connection.cancel()
        with suppress(Exception):
            await asyncio.gather(*connections, return_exceptions=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run the ten-agent programmatic Commander joust on Deep Deck League."
    )
    result.add_argument("--platform-url", default=OFFICIAL_PLATFORM_URL)
    result.add_argument(
        "--competition-version-id",
        default=COMMANDER_COMPETITION_VERSION_ID,
    )
    result.add_argument("--agent-version", default="0.3.0-joust.1")
    result.add_argument("--poll-seconds", type=float, default=5.0)
    result.add_argument("--connection-timeout", type=float, default=45.0)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--allow-non-production", action="store_true")
    return result


def main() -> None:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    asyncio.run(run_joust(parser().parse_args()))


if __name__ == "__main__":
    main()
