"""LLM Client — OpenAI-compatible API wrapper for Infrastructure Agent.

Supports any OpenAI-compatible endpoint (OpenAI, Azure, local vLLM, etc.).
Config via environment variables:
  - OPENAI_API_KEY    (required)
  - OPENAI_BASE_URL   (optional, defaults to https://api.openai.com/v1)
  - LLM_MODEL         (optional, defaults to gpt-4o-mini)

Graceful degradation: if LLM is unavailable (no key / network error / timeout),
generate() returns None so callers fall back to rule-based logic.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Auto-load .env from project root for local development.
# In containerised deployments, .env is deliberately absent — credentials
# are injected via K8S Secrets / Docker env.  load_dotenv is silently skipped.
_env_file = Path(__file__).resolve().parent.parent.parent.parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


class LLMClient:
    """Async LLM client with graceful degradation."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.timeout = timeout

        if self.api_key:
            self._client: AsyncOpenAI | None = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=1,
            )
        else:
            self._client = None

    @property
    def available(self) -> bool:
        """Whether the LLM client is configured and ready."""
        return self._client is not None

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 1000,
    ) -> str | None:
        """Generate a completion from the LLM.

        Returns:
            The LLM response text, or None if the LLM is unavailable.
        """
        if self._client is None:
            return None

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content

        except Exception as exc:
            # Log instead of silently swallowing — callers fall back to rules,
            # but the failure reason must be visible in server logs.
            logger.warning(
                "LLM call failed (model=%s, base_url=%s): %s: %s",
                self.model, self.base_url, type(exc).__name__, exc,
            )
            return None

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 1000,
    ) -> dict | None:
        """Generate a JSON-structured completion from the LLM.

        Returns:
            Parsed dict, or None if LLM unavailable or JSON parse fails.
        """
        text = await self.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if text is None:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # LLM sometimes wraps JSON in markdown fences or prose. Try to
            # extract the first {...} block before giving up.
            extracted = _extract_json_object(text)
            if extracted is not None:
                return extracted
            logger.warning(
                "LLM returned non-JSON (model=%s): %.200s...", self.model, text,
            )
            return None


# Module-level singleton — created once, shared across all callers.
_client: LLMClient | None = None


def _extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of the first {...} object from LLM output.

    Handles markdown-fenced JSON and prose-wrapped JSON.
    """
    start = text.find("{")
    if start == -1:
        return None
    # Track brace depth to find the matching close brace
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def get_llm_client() -> LLMClient:
    """Get or create the shared LLMClient instance.

    If the cached client has no API key, re-attempt creation so a
    late-configured .env takes effect without a process restart.
    """
    global _client
    if _client is None or not _client.available:
        _client = LLMClient()
    return _client
