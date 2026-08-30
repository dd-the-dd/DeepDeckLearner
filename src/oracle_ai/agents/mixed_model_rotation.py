from __future__ import annotations

import argparse
import json
import logging
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

LOGGER = logging.getLogger("oracle_ai.mixed_model_rotation")


@dataclass(frozen=True)
class RotationModel:
    generation: str
    version: str


def refill_rotation_bag(
    models: tuple[RotationModel, ...],
    randomizer: random.Random,
) -> list[RotationModel]:
    """Return one randomized occurrence of every configured model."""
    bag = list(models)
    randomizer.shuffle(bag)
    return bag


def install_host_override(host: str, address: str) -> None:
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


def run_rotation(args: argparse.Namespace) -> None:
    token = os.getenv("DDL_MATCH_WORKER_TOKEN", "").strip()
    if not token:
        raise ValueError("DDL_MATCH_WORKER_TOKEN is required")
    models = (
        RotationModel("v12", args.v12_version),
        RotationModel("v12.1", args.v12_1_version),
    )
    if any(not model.version.strip() for model in models):
        raise ValueError("both frozen model versions are required")
    platform_url = args.platform_url.rstrip("/")
    platform_host = urllib.parse.urlsplit(platform_url).hostname
    host_ip = os.getenv("DDL_PLATFORM_HOST_IP", "").strip()
    if host_ip and platform_host:
        install_host_override(platform_host, host_ip)
        LOGGER.info("platform host %s pinned to %s", platform_host, host_ip)

    randomizer = random.Random(args.seed)
    bag: list[RotationModel] = []
    terminal_matches = 0
    while args.max_matches <= 0 or terminal_matches < args.max_matches:
        if not bag:
            bag = refill_rotation_bag(models, randomizer)
        model = bag[-1]
        try:
            response = request_json(
                "POST",
                f"{platform_url}/v1/matchmaking/exhibitions/random",
                token=token,
                payload={
                    "format": "legacy",
                    "agentVersion": model.version,
                    "agentGeneration": model.generation,
                    "artifactDigest": model.version,
                },
            )
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            if error.code in {409, 503}:
                LOGGER.info(
                    "%s waiting for capacity: HTTP %s %s",
                    model.generation,
                    error.code,
                    detail,
                )
            else:
                LOGGER.warning(
                    "%s scheduling failed: HTTP %s %s",
                    model.generation,
                    error.code,
                    detail,
                )
            time.sleep(args.poll_seconds)
            continue
        except Exception as error:
            LOGGER.warning(
                "%s could not reach the platform (%s)",
                model.generation,
                error,
            )
            time.sleep(args.poll_seconds)
            continue

        exhibition = response.get("data", {})
        match_id = str(exhibition.get("matchId", ""))
        if not match_id:
            LOGGER.warning("rotation response omitted matchId: %s", response)
            time.sleep(args.poll_seconds)
            continue
        bag.pop()
        LOGGER.info(
            "Legacy match %s selected %s (%s) with decks: %s",
            match_id,
            model.generation,
            model.version,
            ", ".join(str(deck) for deck in exhibition.get("decks", [])),
        )
        while True:
            try:
                match_response = request_json(
                    "GET",
                    f"{platform_url}/v1/matches/{match_id}",
                )
                status = str(
                    match_response.get("data", {})
                    .get("summary", {})
                    .get("status", "unknown")
                )
                if status in {"complete", "failed", "cancelled"}:
                    terminal_matches += 1
                    LOGGER.info(
                        "Legacy match %s (%s) reached %s",
                        match_id,
                        model.generation,
                        status,
                    )
                    break
            except Exception as error:
                LOGGER.warning("could not read match %s (%s)", match_id, error)
            time.sleep(args.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Schedule a balanced random mix of frozen V12 and V12.1 Legacy matches."
    )
    parser.add_argument(
        "--platform-url",
        default=os.getenv(
            "DDL_PLATFORM_URL",
            "https://staging.deepdeckleague.com/api",
        ),
    )
    parser.add_argument(
        "--v12-version",
        default=os.getenv("DDL_V12_VERSION", "v12-step-411247"),
    )
    parser.add_argument(
        "--v12-1-version",
        default=os.getenv("DDL_V12_1_VERSION", "v12.1-step-418148"),
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--seed", type=int, default=1201418148)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_rotation(args)


if __name__ == "__main__":
    main()
