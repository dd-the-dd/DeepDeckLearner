"""Responses SDK adapter for the documented ChatGPT plan-sharing restrictions."""

from __future__ import annotations

from typing import Any

from agents.models.openai_responses import OpenAIResponsesModel


class ChatGPTPlanModel(OpenAIResponsesModel):
    def _build_response_create_kwargs(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        body = super()._build_response_create_kwargs(*args, **kwargs)
        # Plan sharing is HTTP/SSE only, with explicit context each turn.
        for key in (
            "previous_response_id",
            "conversation",
            "background",
            "max_output_tokens",
            "max_tool_calls",
            "metadata",
            "moderation",
            "multi_agent",
            "prompt",
            "prompt_cache_retention",
            "safety_identifier",
            "temperature",
            "top_logprobs",
            "top_p",
            "truncation",
            "user",
        ):
            body.pop(key, None)
        body["store"] = False
        body["stream"] = True
        tools = body.get("tools")
        if (
            isinstance(tools, list)
            and tools
            and any(
                tool.get("type") != "namespace"
                or any(child.get("type") != "function" for child in tool.get("tools", []))
                for tool in tools
            )
        ):
            raise ValueError("ChatGPT plan sharing requires namespaced DeepDeck function tools.")
        return body
