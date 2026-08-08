"""AI provider abstraction.

A provider only knows how to turn chat messages into text. It knows nothing
about agents, the world, or memory — so adding a new provider never requires
touching the agent system (see README, "Adding new providers").

To add a provider: subclass :class:`AIProvider`, implement ``generate`` and
``health``, then register it in :mod:`app.ai.manager`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx


class ProviderError(Exception):
    """Raised when a provider fails to produce a usable response."""


@dataclass
class LLMRequest:
    messages: List[Dict[str, str]]
    provider: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 512
    fallbacks: List[str] = field(default_factory=list)
    # A pure-Python policy that yields a valid JSON decision string. Used when
    # the agent's provider is `local`, and as the ultimate graceful-degradation
    # fallback when every real provider fails.
    local_policy: Optional[Callable[[], str]] = None
    agent_id: str = ""
    want_json: bool = True


@dataclass
class LLMResult:
    text: str
    provider_used: str
    model_used: str
    latency: float = 0.0
    used_fallback: bool = False
    tokens: Dict[str, int] = field(default_factory=dict)
    error: Optional[str] = None


class AIProvider:
    """Base class. ``name`` must be unique."""

    name: str = "base"
    requires_key: bool = False

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key

    async def generate(
        self, messages: List[Dict[str, str]], model: str, temperature: float,
        max_tokens: int, timeout: float, want_json: bool = True,
    ) -> Tuple[str, Dict[str, int]]:
        raise NotImplementedError

    async def health(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Best-effort reachability check. Never raises."""
        return {"provider": self.name, "status": "unknown"}


def _extract_json(text: str) -> str:
    """Return the first JSON object found in ``text`` (models sometimes wrap it)."""
    text = text.strip()
    if text.startswith("```"):
        # strip code fences
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


# --- OpenAI-compatible (openai, openrouter, ollama, lmstudio, vllm, localai, custom)

class OpenAICompatibleProvider(AIProvider):
    name = "openai_compatible"

    async def generate(self, messages, model, temperature, max_tokens, timeout, want_json=True):
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if want_json:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise ProviderError(f"{self.name} request failed: {exc}") from exc
            if resp.status_code == 400 and want_json:
                # Some servers reject response_format; retry without it.
                payload.pop("response_format", None)
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise ProviderError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"{self.name} malformed response") from exc
        usage = body.get("usage", {}) or {}
        tokens = {
            "prompt": usage.get("prompt_tokens", 0),
            "completion": usage.get("completion_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }
        return _extract_json(text) if want_json else text, tokens

    async def health(self, timeout: float = 5.0):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                resp = await client.get(f"{self.base_url}/models", headers=headers)
                ok = resp.status_code < 500
                return {"provider": self.name, "status": "healthy" if ok else "degraded", "code": resp.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "status": "offline", "error": str(exc)[:120]}


class AnthropicProvider(AIProvider):
    name = "anthropic"
    requires_key = True

    async def generate(self, messages, model, temperature, max_tokens, timeout, want_json=True):
        # Anthropic separates the system prompt from the message list.
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [{"role": ("assistant" if m["role"] == "assistant" else "user"), "content": m["content"]}
                 for m in messages if m["role"] != "system"]
        if not convo:
            convo = [{"role": "user", "content": system or "Respond."}]
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "system": system,
            "messages": convo,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise ProviderError(f"anthropic request failed: {exc}") from exc
            if resp.status_code >= 400:
                raise ProviderError(f"anthropic HTTP {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
        try:
            text = body["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError("anthropic malformed response") from exc
        usage = body.get("usage", {}) or {}
        tokens = {
            "prompt": usage.get("input_tokens", 0),
            "completion": usage.get("output_tokens", 0),
            "total": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        }
        return _extract_json(text) if want_json else text, tokens

    async def health(self, timeout: float = 5.0):
        if not self.api_key:
            return {"provider": self.name, "status": "no_key"}
        return {"provider": self.name, "status": "configured"}


class GeminiProvider(AIProvider):
    name = "gemini"
    requires_key = True

    async def generate(self, messages, model, temperature, max_tokens, timeout, want_json=True):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        parts_text = system + "\n\n" + "\n".join(
            f"{m['role']}: {m['content']}" for m in messages if m["role"] != "system"
        )
        url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key or ''}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": parts_text}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(url, json=payload)
            except httpx.HTTPError as exc:
                raise ProviderError(f"gemini request failed: {exc}") from exc
            if resp.status_code >= 400:
                raise ProviderError(f"gemini HTTP {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError("gemini malformed response") from exc
        usage = body.get("usageMetadata", {}) or {}
        tokens = {
            "prompt": usage.get("promptTokenCount", 0),
            "completion": usage.get("candidatesTokenCount", 0),
            "total": usage.get("totalTokenCount", 0),
        }
        return _extract_json(text) if want_json else text, tokens

    async def health(self, timeout: float = 5.0):
        if not self.api_key:
            return {"provider": self.name, "status": "no_key"}
        return {"provider": self.name, "status": "configured"}
