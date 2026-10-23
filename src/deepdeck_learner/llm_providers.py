"""Documented connection presets, not promises of account or model access."""

from __future__ import annotations

PROVIDERS = [
    {
        "id": "chatgpt",
        "name": "ChatGPT subscription",
        "baseUrl": "https://api.openai.com/v1",
        "auth": "chatgpt",
        "note": "Eligible ChatGPT plans. Browser sign-in and explicit plan-usage consent; shared plan limits apply.",
        "docs": "https://developers.openai.com/siwc/token-sharing-open-source",
    },
    {
        "id": "openai",
        "name": "OpenAI API",
        "baseUrl": "https://api.openai.com/v1",
        "auth": "apiKey",
        "note": "API billing, not your ChatGPT subscription.",
        "docs": "https://developers.openai.com/api/docs/quickstart",
    },
    {
        "id": "anthropic",
        "name": "Claude · Anthropic",
        "baseUrl": "https://api.anthropic.com/v1",
        "auth": "apiKey",
        "note": "Claude API key. Pro/Max sign-in is not offered to third-party apps without Anthropic approval. Compatibility mode; strict tool schemas are validated locally.",
        "docs": "https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk",
    },
    {
        "id": "gemini",
        "name": "Gemini · Google",
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "auth": "apiKey",
        "note": "Google AI Studio API key. Free tier and eligible subscription/developer credits depend on your Google account and project; check its billing before use.",
        "docs": "https://ai.google.dev/gemini-api/docs/openai",
    },
    {
        "id": "mistral",
        "name": "Mistral",
        "baseUrl": "https://api.mistral.ai/v1",
        "auth": "apiKey",
        "note": "Mistral API key. Current Mistral plans share included usage across API, Studio and Vibe; additional usage depends on your plan settings.",
        "docs": "https://docs.mistral.ai/admin/billing-usage/subscriptions",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "baseUrl": "https://api.deepseek.com/v1",
        "auth": "apiKey",
        "note": "DeepSeek API key and API balance. No subscription sign-in is assumed.",
        "docs": "https://api-docs.deepseek.com/",
    },
    {
        "id": "xai",
        "name": "Grok · xAI",
        "baseUrl": "https://api.x.ai/v1",
        "auth": "apiKey",
        "note": "xAI API key. No X or Grok consumer subscription is assumed.",
        "docs": "https://docs.x.ai/developers/rest-api-reference/inference",
    },
    {
        "id": "groq",
        "name": "Groq",
        "baseUrl": "https://api.groq.com/openai/v1",
        "auth": "apiKey",
        "note": "Groq API key. Choose a model with tool calling.",
        "docs": "https://console.groq.com/docs/openai",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "baseUrl": "https://openrouter.ai/api/v1",
        "auth": "apiKey",
        "note": "One API key for multiple providers. Availability, privacy and billing depend on the selected model and route.",
        "docs": "https://openrouter.ai/docs/quickstart",
    },
    {
        "id": "ollama",
        "name": "Ollama · local",
        "baseUrl": "http://127.0.0.1:11434/v1",
        "auth": "local",
        "note": "Run Ollama on this computer and choose an installed tool-capable model. No subscription required.",
        "docs": "https://docs.ollama.com/api/openai-compatibility",
    },
    {
        "id": "lmstudio",
        "name": "LM Studio · local",
        "baseUrl": "http://127.0.0.1:1234/v1",
        "auth": "local",
        "note": "Start LM Studio's local server and load a tool-capable model. No subscription required.",
        "docs": "https://lmstudio.ai/docs/developer/openai-compat",
    },
    {
        "id": "custom",
        "name": "Custom / LiteLLM gateway",
        "baseUrl": "",
        "auth": "custom",
        "note": "Any compatible Chat Completions endpoint with function calling. No silent fallback to another provider.",
        "docs": "",
    },
]


def provider_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in PROVIDERS]
