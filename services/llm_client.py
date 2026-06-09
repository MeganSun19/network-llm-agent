"""LLM client wrapper.

Thin facade over the official OpenAI Python SDK. Returns the raw response
shape so existing tool-calling agents can stay unchanged.

Configuration via environment variables:
    OPENAI_API_KEY      - Required. API key for the LLM provider.
    OPENAI_BASE_URL     - Optional. Override for OpenAI-compatible endpoints
                          (Azure OpenAI, vLLM, OpenRouter, local Ollama, etc.).
    OPENAI_MODEL        - Optional. Model name. Defaults to "gpt-4o-mini".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI


@dataclass
class LLMConfig:
    """LLM client configuration."""

    api_key: str
    model: str = "gpt-4o-mini"
    base_url: Optional[str] = None
    timeout: int = 60

    @classmethod
    def from_env(cls) -> "LLMConfig":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it in your shell or .env file."
            )
        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            timeout=int(os.getenv("OPENAI_TIMEOUT", "60")),
        )


class LLMClient:
    """Minimal chat-completion client compatible with OpenAI-style endpoints."""

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or LLMConfig.from_env()
        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
        )

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        stop: Optional[List[str]] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """Send a chat-completion request and return the raw response dict."""
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            stop=stop,
            temperature=temperature,
        )
        # The SDK returns a Pydantic model; normalize to dict for downstream
        # code that expects ["choices"][0]["message"]["content"] indexing.
        return response.model_dump()
