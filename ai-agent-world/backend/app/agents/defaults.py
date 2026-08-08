"""Default agents.

Loaded from ``config/agents.json`` if present, otherwise the three built-in
characters. Providers default to ``local`` so the world runs fully offline with
zero configuration; switch any agent to a real provider (Anthropic, OpenAI,
Gemini, Ollama…) by editing ``config/agents.json`` — see the README.
"""
from __future__ import annotations

import json
from typing import List

from ..config import CONFIG_DIR
from ..tools.registry import DEFAULT_ALLOWED
from .agent import Agent, ProviderConfig

BUILTIN = [
    {
        "id": "alex", "name": "Alex", "role": "Explorer", "color": "#f2a65a",
        "personality": ["curious", "social", "risk-taking", "optimistic"],
        "background": "A restless wanderer who is happiest discovering what lies beyond the map.",
        "goals": ["explore the world", "discover resources", "discover unusual locations", "share discoveries"],
        "provider": {"provider": "local", "model": "rule-policy", "temperature": 0.8,
                     "decision_interval": 9, "fallbacks": ["local"]},
    },
    {
        "id": "nova", "name": "Nova", "role": "Engineer", "color": "#5aa9e6",
        "personality": ["logical", "cautious", "practical", "analytical"],
        "background": "A methodical builder who turns raw resources into useful structures.",
        "goals": ["build useful structures", "improve the settlement", "collect resources", "help other agents"],
        "provider": {"provider": "local", "model": "rule-policy", "temperature": 0.5,
                     "decision_interval": 11, "fallbacks": ["local"]},
    },
    {
        "id": "echo", "name": "Echo", "role": "Researcher", "color": "#8ac926",
        "personality": ["quiet", "observant", "analytical", "patient"],
        "background": "A watchful chronicler who studies the world and everyone in it.",
        "goals": ["observe the world", "record important events", "study other agents", "maintain knowledge"],
        "provider": {"provider": "local", "model": "rule-policy", "temperature": 0.6,
                     "decision_interval": 12, "fallbacks": ["local"]},
    },
]


def _to_agent(data: dict) -> Agent:
    p = data.get("provider", {}) or {}
    agent = Agent(
        id=data["id"], name=data["name"], role=data.get("role", ""),
        personality=data.get("personality", []), background=data.get("background", ""),
        goals=data.get("goals", []), color=data.get("color", "#5aa9e6"),
        provider=ProviderConfig(
            provider=p.get("provider", "local"), model=p.get("model", "rule-policy"),
            temperature=p.get("temperature", 0.7), max_tokens=p.get("max_tokens", 600),
            decision_interval=p.get("decision_interval", 10), fallbacks=p.get("fallbacks", ["local"]),
        ),
        allowed_tools=data.get("allowed_tools") or list(DEFAULT_ALLOWED),
    )
    return agent


def default_agents() -> List[Agent]:
    cfg_file = CONFIG_DIR / "agents.json"
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return [_to_agent(d) for d in data]
        except (json.JSONDecodeError, KeyError):
            pass
    return [_to_agent(d) for d in BUILTIN]
