"""
KODA's own model/provider config.

Replaces the `deepagents_cli.model_config` monkey-patch.

Provides:
  ModelSpec.try_parse("provider:model")
  has_provider_credentials("anthropic")
  get_available_models()  — dict[provider, list[model_name]]

Discovery is cache-first: the completer calls `get_available_models()` on
every keystroke in `/model xxx`, so we keep a process-wide in-memory cache
and never block on network inside the hot path. `warm_cache_in_background()`
kicks off file-cache refresh in a daemon thread at app startup.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

_log = logging.getLogger("koda.model_config")


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @classmethod
    def try_parse(cls, spec: str) -> "ModelSpec | None":
        """Accepts 'provider:model' or bare 'model' (returns None if bare)."""
        if not spec:
            return None
        if ":" not in spec:
            return None
        provider, _, model = spec.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if not provider or not model:
            return None
        return cls(provider=provider, model=model)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.provider}:{self.model}"


# Provider → env var that proves we have credentials. None = no key required.
_PROVIDER_KEYS: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "ollama": None,  # local; uses OLLAMA_HOST/OLLAMA_API_KEY but both optional
    "openrouter": "OPENROUTER_API_KEY",
    "lmstudio": None,  # local
    "groq": "GROQ_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
}


def has_provider_credentials(provider: str) -> bool | None:
    """True if we have creds, False if key is required but missing, None if unknown."""
    key = _PROVIDER_KEYS.get(provider.lower())
    if key is None and provider.lower() in _PROVIDER_KEYS:
        return True  # local providers
    if key is None:
        return None
    return bool(os.environ.get(key))


_MODELS_CACHE: tuple[float, dict[str, list[str]]] | None = None
_MODELS_TTL = 300  # 5 minutes — the in-memory cache lifetime


def get_available_models(force_refresh: bool = False) -> dict[str, list[str]]:
    """Discover models we can actually reach right now.

    Two-layer cache:
      - Process-wide in-memory cache with 5-min TTL (the hot path for /model)
      - Disk cache at ~/.koda/models/<provider>.json (24h TTL, in provider_models)

    Only includes providers we have credentials for.
    """
    global _MODELS_CACHE

    if not force_refresh and _MODELS_CACHE is not None:
        ts, cached = _MODELS_CACHE
        if time.time() - ts < _MODELS_TTL:
            return cached

    from koda.provider_models import PROVIDERS, get_models, get_models_cached_only

    fetch = get_models if force_refresh else get_models_cached_only

    out: dict[str, list[str]] = {}
    for name, spec in PROVIDERS.items():
        if spec.needs_key and spec.auth_env and not os.environ.get(spec.auth_env):
            continue
        models = fetch(name)
        if models:
            out[name] = models

    _MODELS_CACHE = (time.time(), out)
    return out


def invalidate_models_cache() -> None:
    """Clear the in-memory cache. Next call will re-scan disk / network."""
    global _MODELS_CACHE
    _MODELS_CACHE = None


def warm_cache_in_background() -> None:
    """Kick off model discovery in a daemon thread so the first /model
    popup is instant. Called once from KodaApp.on_mount.
    """

    def _worker() -> None:
        try:
            from koda.provider_models import refresh_stale

            refresh_stale()  # re-fetch any provider whose disk cache is stale
            get_available_models(force_refresh=True)  # warm the in-memory cache
            _log.debug("model cache warmed")
        except Exception as e:
            _log.warning("background model-cache warm failed: %s", e)

    t = threading.Thread(target=_worker, name="koda-model-warm", daemon=True)
    t.start()
