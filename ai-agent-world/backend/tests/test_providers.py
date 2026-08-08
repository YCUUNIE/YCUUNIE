"""AI providers: selection, fallback, local policy, invalid handling."""
import json

import pytest

from backend.app.ai import LLMRequest, get_ai_manager
from backend.app.ai.manager import AIManager


@pytest.mark.asyncio
async def test_local_provider_uses_policy():
    mgr = AIManager()
    req = LLMRequest(messages=[], provider="local", model="rule-policy",
                     fallbacks=["local"], local_policy=lambda: json.dumps({"summary": "hi"}))
    res = await mgr.complete(req)
    assert res.provider_used == "local"
    assert json.loads(res.text)["summary"] == "hi"


@pytest.mark.asyncio
async def test_forced_failure_falls_back_to_local():
    mgr = AIManager()
    mgr.force_failure("anthropic", seconds=30)
    called = {"n": 0}

    def policy():
        called["n"] += 1
        return json.dumps({"summary": "recovered"})

    req = LLMRequest(messages=[{"role": "user", "content": "x"}], provider="anthropic",
                     model="m", fallbacks=["local"], local_policy=policy, agent_id="a")
    res = await mgr.complete(req)
    # Anthropic forced down -> falls back to local policy.
    assert res.provider_used in ("local", "local(recovered)")
    assert called["n"] >= 1
    assert json.loads(res.text)["summary"] == "recovered"


@pytest.mark.asyncio
async def test_missing_key_skips_to_local_without_error_flag():
    mgr = AIManager()
    # anthropic requires a key; with none set it should be skipped silently.
    req = LLMRequest(messages=[{"role": "user", "content": "x"}], provider="anthropic",
                     model="m", fallbacks=["local"], local_policy=lambda: "{}", agent_id="a")
    res = await mgr.complete(req)
    assert res.provider_used == "local"


def test_provider_stats_shape():
    mgr = get_ai_manager()
    stats = mgr.stats()
    assert "local" in stats
    assert "anthropic" in stats
    assert "calls" in stats["local"]
