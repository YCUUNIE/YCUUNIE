"""Configuration loading.

Configuration comes from three layers, later ones overriding earlier ones:

1. built-in defaults (this file)
2. ``config/config.yaml`` (paths, providers, simulation tuning)
3. environment variables / ``.env`` (secrets — API keys only)

API keys live *only* in the environment. They are never written to yaml,
never sent to the frontend, and never logged.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Repo layout:  <root>/backend/app/config.py  -> root is parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"


DEFAULTS: Dict[str, Any] = {
    "simulation": {
        "tick_rate": 10,          # world ticks per second
        "default_speed": 1,       # simulation speed multiplier
        "max_agents": 20,
        "autostart": True,
    },
    "world": {
        "width": 1600,
        "height": 1200,
    },
    "memory": {
        "provider": "obsidian",   # obsidian | sqlite (obsidian falls back to sqlite if no vault)
    },
    "obsidian": {
        "vault_path": "",         # empty -> uses <root>/obsidian as a local demo vault
        "root_folder": "AI-Agents",
    },
    "llm": {
        "concurrency": 3,         # max simultaneous LLM calls across all agents
        "timeout_seconds": 30,
        "max_retries": 2,
    },
    "providers": {
        # Enabled providers. `local` is always available and requires no keys,
        # so the whole simulation runs fully offline out of the box.
        "local": {"enabled": True},
        "openai": {"enabled": True, "base_url": "https://api.openai.com/v1"},
        "anthropic": {"enabled": True, "base_url": "https://api.anthropic.com/v1"},
        "gemini": {"enabled": True, "base_url": "https://generativelanguage.googleapis.com/v1beta"},
        "openrouter": {"enabled": True, "base_url": "https://openrouter.ai/api/v1"},
        "ollama": {"enabled": True, "base_url": "http://localhost:11434/v1"},
        "lmstudio": {"enabled": True, "base_url": "http://localhost:1234/v1"},
        "vllm": {"enabled": True, "base_url": "http://localhost:8000/v1"},
        "localai": {"enabled": True, "base_url": "http://localhost:8080/v1"},
        "custom": {"enabled": True, "base_url": "http://localhost:8000/v1"},
    },
}

# Maps a provider name to the environment variable holding its API key.
PROVIDER_ENV_KEYS: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "CUSTOM_API_KEY",
    # local runtimes need no key
    "ollama": "",
    "lmstudio": "",
    "vllm": "",
    "localai": "",
    "local": "",
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_dotenv() -> None:
    """Minimal .env loader (avoids importing python-dotenv at import time)."""
    for candidate in (ROOT_DIR / ".env", BACKEND_DIR / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


class Config:
    """Parsed configuration with helpers. Secrets stay out of ``as_public``."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    def api_key(self, provider: str) -> Optional[str]:
        env = PROVIDER_ENV_KEYS.get(provider, "")
        if not env:
            return None
        return os.environ.get(env) or None

    def provider_base_url(self, provider: str) -> Optional[str]:
        # Environment override wins, e.g. OLLAMA_BASE_URL.
        env_override = os.environ.get(f"{provider.upper()}_BASE_URL")
        if env_override:
            return env_override
        return self.get("providers", provider, "base_url")

    @property
    def vault_path(self) -> Path:
        raw = self.get("obsidian", "vault_path") or ""
        raw = os.environ.get("OBSIDIAN_VAULT_PATH", raw)
        if raw:
            return Path(raw).expanduser().resolve()
        return (ROOT_DIR / "obsidian").resolve()

    def as_public(self) -> Dict[str, Any]:
        """Config safe to expose to the frontend: no secrets, only *status*."""
        providers = {}
        for name, pdata in self.get("providers", default={}).items():
            providers[name] = {
                "enabled": bool(pdata.get("enabled", False)),
                "base_url": self.provider_base_url(name),
                "has_key": bool(self.api_key(name)) if PROVIDER_ENV_KEYS.get(name) else True,
                "requires_key": bool(PROVIDER_ENV_KEYS.get(name)),
            }
        return {
            "simulation": self.get("simulation", default={}),
            "world": self.get("world", default={}),
            "memory": self.get("memory", default={}),
            "obsidian": {"vault_path": str(self.vault_path), "root_folder": self.get("obsidian", "root_folder")},
            "providers": providers,
        }


@lru_cache(maxsize=1)
def get_config() -> Config:
    _load_dotenv()
    data = dict(DEFAULTS)
    cfg_file = CONFIG_DIR / "config.yaml"
    if cfg_file.exists():
        loaded = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, loaded)
    return Config(data)
