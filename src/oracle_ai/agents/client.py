from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from websockets.asyncio.client import connect

from oracle_ai.agents.base import MagicAgent
from oracle_ai.agents.protocol import (
    AgentManifest,
    DecisionRequest,
    DecisionResolvedRequest,
    DecisionResponse,
    FullObservation,
    GameEndedRequest,
    GameEventRequest,
    GameSharing,
    ObservationStream,
    RegistrationAccepted,
    StartingSituationRequest,
    TimeoutCategory,
)
from oracle_ai.agents.state import ObservationReplica


class AgentClient:
    def __init__(
        self,
        url: str,
        manifest: AgentManifest,
        agent: MagicAgent,
        *,
        observation_stream: ObservationStream,
        timeout_category: TimeoutCategory,
        game_sharing: GameSharing = GameSharing.PRIVATE,
        api_key: str | None = None,
        account_token: str | None = None,
    ) -> None:
        compatibility = manifest.compatibility
        if observation_stream not in compatibility.observation_streams:
            raise ValueError("observation stream is not declared by the manifest")
        if timeout_category not in compatibility.time_controls:
            raise ValueError("timeout category is not declared by the manifest")
        if game_sharing not in compatibility.game_sharing:
            raise ValueError("sharing mode is not declared by the manifest")
        self.url = url
        self.manifest = manifest
        self.agent = agent
        self.observation_stream = observation_stream
        self.timeout_category = timeout_category
        self.game_sharing = game_sharing
        self.api_key = api_key
        self.account_token = account_token
        self._replicas: dict[str, ObservationReplica] = {}
        self.registration: RegistrationAccepted | None = None
        self._socket: Any = None
        self._send_lock = asyncio.Lock()

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._socket is None:
            raise RuntimeError("agent is not connected")
        async with self._send_lock:
            await self._socket.send(json.dumps(payload, separators=(",", ":")))

    async def _starting(self, payload: dict[str, Any]) -> None:
        request = StartingSituationRequest.model_validate(payload)
        await self.agent.analyze_starting_situation(request)
        await self._send({
            "type": "startingSituationCompleted",
            "requestId": request.request_id,
        })

    async def _decision(self, payload: dict[str, Any]) -> None:
        request = DecisionRequest.model_validate(payload)
        replica = self._replicas.setdefault(request.context_id, ObservationReplica())
        update = request.observation_update
        if isinstance(update, FullObservation):
            observation = replica.replace(update.sequence, update.observation)
            await self.agent.receive_full_observation(update)
        else:
            observation = replica.apply(
                update.sequence,
                update.previous_sequence,
                update.patch,
            )
            await self.agent.apply_observation_delta(update)
        request = request.model_copy(update={"observation": observation})
        remaining = max(0.001, request.deadline_unix_ms / 1000 - time.time())
        response = await asyncio.wait_for(
            self.agent.make_decision(request),
            timeout=remaining,
        )
        if not isinstance(response, DecisionResponse):
            response = DecisionResponse.model_validate(response)
        await self._send({
            "type": "decisionSubmitted",
            "requestId": request.request_id,
            **response.model_dump(by_alias=True, exclude_none=True),
        })

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        message_type = payload.get("type")
        if message_type == "startingSituationRequested":
            await self._starting(payload)
        elif message_type == "decisionRequested":
            await self._decision(payload)
        elif message_type == "gameEvent":
            await self.agent.receive_game_event(GameEventRequest.model_validate(payload))
        elif message_type == "decisionResolved":
            await self.agent.decision_resolved(DecisionResolvedRequest.model_validate(payload))
        elif message_type == "gameEnded":
            await self.agent.game_ended(GameEndedRequest.model_validate(payload))
        elif message_type == "ping":
            await self._send({"type": "pong", "requestId": payload.get("requestId")})

    async def run(self) -> None:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["x-mtg-api-key"] = self.api_key
        if self.account_token:
            headers["Authorization"] = f"Bearer {self.account_token}"
        async with connect(
            self.url,
            ping_interval=20,
            ping_timeout=20,
            additional_headers=headers or None,
            # Four-player Commander observations can exceed the Legacy envelope,
            # especially when several 100-card libraries are projected at startup.
            max_size=32 * 1024 * 1024,
        ) as socket:
            self._socket = socket
            await self._send({
                "type": "registerAgent",
                "protocolVersion": "mtg-agent/v1",
                "manifest": self.manifest.model_dump(by_alias=True, mode="json"),
                "observationStream": self.observation_stream.value,
                "timeoutCategory": self.timeout_category.value,
                "gameSharing": self.game_sharing.value,
            })
            first = json.loads(await socket.recv())
            self.registration = RegistrationAccepted.model_validate(first)
            async for raw in socket:
                payload = json.loads(raw)
                # Deltas are ordered within one agent connection. Processing them
                # sequentially keeps the local observation replica on that same order.
                await self._dispatch(payload)
        self._socket = None
