from __future__ import annotations

import json

import httpx
import pytest
from agents import Agent, FunctionTool, ModelSettings, RunConfig, Runner, tool_namespace
from agents.exceptions import ModelBehaviorError
from openai import AsyncOpenAI

from deepdeck_learner.chatgpt_model import ChatGPTPlanModel


@pytest.mark.asyncio
async def test_real_sdk_subscription_stream_calls_local_tools_and_requires_completion():
    requests = []
    invocations = []

    async def invoke(_context, arguments):
        invocations.append(json.loads(arguments))
        return "Saved locally"

    tool = FunctionTool(
        name="save_plan",
        description="Save a bounded plan",
        params_json_schema={
            "type": "object",
            "properties": {"weight": {"type": "number"}},
            "required": ["weight"],
            "additionalProperties": False,
        },
        on_invoke_tool=invoke,
    )

    def transport(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer subscription-private"
        assert body["store"] is False and body["stream"] is True
        assert not {
            "temperature",
            "previous_response_id",
            "conversation",
            "max_output_tokens",
            "metadata",
        }.intersection(body)
        assert body["tools"][0]["type"] == "namespace"
        output = (
            [
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "save_plan",
                    "namespace": "deepdeck",
                    "arguments": '{"weight":1.2}',
                    "status": "completed",
                }
            ]
            if len(requests) == 1
            else [
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "Plan saved.", "annotations": []}],
                }
            ]
        )
        response = {
            "id": f"resp_{len(requests)}",
            "object": "response",
            "created_at": 1,
            "model": "contract-model",
            "status": "completed",
            "output": output,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content="data: "
            + json.dumps({"type": "response.completed", "response": response, "sequence_number": 1})
            + "\n\n",
        )

    client = AsyncOpenAI(
        api_key="subscription-private",
        base_url="https://api.openai.com/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        max_retries=0,
    )
    try:
        agent = Agent(
            name="DeepDeck",
            instructions="Use local tools.",
            model=ChatGPTPlanModel(model="contract-model", openai_client=client),
            model_settings=ModelSettings(store=False),
            tools=tool_namespace(name="deepdeck", description="Local tools", tools=[tool]),
        )
        run = Runner.run_streamed(
            agent,
            [{"role": "user", "content": "Save this plan."}],
            run_config=RunConfig(tracing_disabled=True),
        )
        async for _event in run.stream_events():
            pass
        assert run.final_output == "Plan saved."
        assert invocations == [{"weight": 1.2}] and len(requests) == 2
        assert any(item.get("type") == "function_call_output" for item in requests[1]["input"])
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["response.failed", "response.incomplete", "error"])
async def test_streamed_failure_is_not_success(event):
    def transport(request):
        response = {
            "id": "resp_failure",
            "object": "response",
            "created_at": 1,
            "model": "contract-model",
            "status": "failed" if event == "response.failed" else "incomplete",
            "output": [],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "error": {
                "code": "subscription_sharing_usage_limit_exceeded",
                "message": "Plan limit reached.",
            },
        }
        data = (
            {"type": event, "response": response, "sequence_number": 1}
            if event != "error"
            else {
                "type": "error",
                "code": "subscription_sharing_usage_limit_exceeded",
                "message": "Plan limit reached.",
                "param": None,
                "sequence_number": 1,
            }
        )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content="data: " + json.dumps(data) + "\n\n",
        )

    client = AsyncOpenAI(
        api_key="fake",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        max_retries=0,
    )
    try:
        agent = Agent(
            name="DeepDeck", model=ChatGPTPlanModel(model="contract-model", openai_client=client)
        )
        run = Runner.run_streamed(agent, "Hello", run_config=RunConfig(tracing_disabled=True))
        with pytest.raises(ModelBehaviorError):
            async for _event in run.stream_events():
                pass
    finally:
        await client.close()
