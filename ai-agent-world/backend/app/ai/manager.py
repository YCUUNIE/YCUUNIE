"""LLM request manager.

Owns the whole request lifecycle: provider selection, concurrency limiting,
timeouts, retries, provider fallback, request cancellation, and latency/token
tracking. Agents never talk to providers directly — they submit an
:class:`LLMRequest` here.

The ``local`` provider needs no network: it runs the agent's own rule-based
policy (``request.local_policy``). That same policy is also the final fallback
if every real provider fails, so the simulation degrades gracefully rather than
freezing — a core part of the self-healing story.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

from ..config import Config, get_config
from ..events import event_bus
from .base import (
    AIProvider,
    AnthropicProvider,
    GeminiProvider,
    LLMRequest,
    LLMResult,
    OpenAICompatibleProvider,
    ProviderError,
)

# Which concrete provider class + whether the base_url is OpenAI-compatible.
OPENAI_COMPATIBLE = {"openai", "openrouter", "ollama", "lmstudio", "vllm", "localai", "custom"}


class AIManager:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()
        self._sem = asyncio.Semaphore(self.config.get("llm", "concurrency", default=3))
        self._timeout = self.config.get("llm", "timeout_seconds", default=30)
        self._max_retries = self.config.get("llm", "max_retries", default=2)
        # rolling stats per provider
        self._latencies: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=50))
        self._tokens: Dict[str, int] = defaultdict(int)
        self._errors: Dict[str, int] = defaultdict(int)
        self._calls: Dict[str, int] = defaultdict(int)
        # simulated outages for the failure demo (provider_name -> until_ts)
        self._forced_failures: Dict[str, float] = {}

    # --- provider factory --------------------------------------------------
    def _build_provider(self, name: str) -> Optional[AIProvider]:
        base_url = self.config.provider_base_url(name)
        api_key = self.config.api_key(name)
        if name in OPENAI_COMPATIBLE:
            return OpenAICompatibleProvider(base_url=base_url, api_key=api_key)
        if name == "anthropic":
            return AnthropicProvider(base_url=base_url, api_key=api_key)
        if name == "gemini":
            return GeminiProvider(base_url=base_url, api_key=api_key)
        return None

    def provider_enabled(self, name: str) -> bool:
        return bool(self.config.get("providers", name, "enabled", default=False))

    # --- failure injection (for the self-healing demo) ---------------------
    def force_failure(self, provider: str, seconds: float = 20.0) -> None:
        self._forced_failures[provider] = time.time() + seconds

    def _is_forced_down(self, provider: str) -> bool:
        until = self._forced_failures.get(provider)
        if until is None:
            return False
        if time.time() > until:
            self._forced_failures.pop(provider, None)
            return False
        return True

    # --- core call ---------------------------------------------------------
    async def complete(self, request: LLMRequest) -> LLMResult:
        """Run a request through the provider chain with fallback."""
        chain: List[str] = [request.provider] + [p for p in request.fallbacks if p != request.provider]
        last_error: Optional[str] = None
        attempted_real = False  # did we actually make a network call that failed?

        for idx, provider_name in enumerate(chain):
            used_fallback = idx > 0

            # `local` provider == run the agent's own policy, no network.
            if provider_name == "local":
                text = request.local_policy() if request.local_policy else "{}"
                # Only announce recovery if a real provider actually failed first;
                # skipping a keyless/disabled provider is normal config, not a fault.
                if used_fallback and attempted_real:
                    await event_bus.emit(
                        "system.recovery", scope="llm", agent_id=request.agent_id,
                        message=f"Fell back to local policy after {request.provider} failed",
                    )
                return LLMResult(text=text, provider_used="local", model_used="rule-policy",
                                 latency=0.0, used_fallback=used_fallback and attempted_real)

            if not self.provider_enabled(provider_name):
                last_error = f"{provider_name} disabled"
                continue
            if self._is_forced_down(provider_name):
                last_error = f"{provider_name} (simulated outage)"
                self._errors[provider_name] += 1
                attempted_real = True
                await event_bus.emit("agent.error", agent_id=request.agent_id, scope="provider",
                                     provider=provider_name, message=last_error)
                continue

            provider = self._build_provider(provider_name)
            if provider is None:
                last_error = f"unknown provider {provider_name}"
                continue
            if provider.requires_key and not provider.api_key:
                last_error = f"{provider_name} missing API key"
                continue

            attempted_real = True
            result = await self._call_with_retries(provider, request, provider_name, used_fallback)
            if result is not None:
                if used_fallback:
                    await event_bus.emit(
                        "system.recovery", scope="llm", agent_id=request.agent_id,
                        provider=provider_name,
                        message=f"Fallback provider {provider_name} handled the request",
                    )
                return result
            last_error = f"{provider_name} exhausted retries"

        # Everything failed — graceful degradation to the local policy.
        if request.local_policy is not None:
            await event_bus.emit(
                "system.recovery", scope="llm", agent_id=request.agent_id,
                message="All providers failed; used local policy to keep agent alive",
            )
            return LLMResult(text=request.local_policy(), provider_used="local(recovered)",
                             model_used="rule-policy", used_fallback=True, error=last_error)
        return LLMResult(text="{}", provider_used="none", model_used="", used_fallback=True, error=last_error)

    async def _call_with_retries(
        self, provider: AIProvider, request: LLMRequest, provider_name: str, used_fallback: bool,
    ) -> Optional[LLMResult]:
        for attempt in range(self._max_retries + 1):
            start = time.time()
            try:
                async with self._sem:
                    text, tokens = await asyncio.wait_for(
                        provider.generate(
                            request.messages, request.model, request.temperature,
                            request.max_tokens, self._timeout, request.want_json,
                        ),
                        timeout=self._timeout + 2,
                    )
                latency = time.time() - start
                self._latencies[provider_name].append(latency)
                self._tokens[provider_name] += tokens.get("total", 0)
                self._calls[provider_name] += 1
                return LLMResult(text=text, provider_used=provider_name, model_used=request.model,
                                 latency=latency, used_fallback=used_fallback, tokens=tokens)
            except (ProviderError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                self._errors[provider_name] += 1
                if attempt < self._max_retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                await event_bus.emit("agent.error", agent_id=request.agent_id, scope="provider",
                                     provider=provider_name, message=str(exc)[:200])
                return None
        return None

    # --- stats / health ----------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        out = {}
        for name in self.config.get("providers", default={}):
            lat = self._latencies.get(name)
            avg = round(sum(lat) / len(lat), 3) if lat else None
            out[name] = {
                "enabled": self.provider_enabled(name),
                "calls": self._calls.get(name, 0),
                "errors": self._errors.get(name, 0),
                "tokens": self._tokens.get(name, 0),
                "avg_latency": avg,
                "forced_down": self._is_forced_down(name),
                "has_key": bool(self.config.api_key(name)) if name not in ("local",) else True,
            }
        return out

    async def provider_status(self) -> Dict[str, Any]:
        status: Dict[str, Any] = {"local": {"status": "healthy", "enabled": True}}
        for name in self.config.get("providers", default={}):
            if name == "local":
                continue
            if not self.provider_enabled(name):
                status[name] = {"status": "disabled", "enabled": False}
                continue
            provider = self._build_provider(name)
            if provider is None:
                status[name] = {"status": "unknown", "enabled": True}
                continue
            try:
                health = await provider.health(timeout=4.0)
            except Exception as exc:  # noqa: BLE001
                health = {"status": "offline", "error": str(exc)[:100]}
            health["enabled"] = True
            status[name] = health
        return status

    async def test_connection(self, provider_name: str) -> Dict[str, Any]:
        provider = self._build_provider(provider_name)
        if provider is None:
            return {"provider": provider_name, "status": "unknown"}
        return await provider.health(timeout=6.0)


_manager: Optional[AIManager] = None


def get_ai_manager() -> AIManager:
    global _manager
    if _manager is None:
        _manager = AIManager()
    return _manager
