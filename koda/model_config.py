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
    # Kimi K2 routes through Ollama Cloud (OpenAI-compatible); the coding
    # agent's build_chat_model honors OLLAMA_BASE_URL/OLLAMA_API_KEY (or a
    # KIMI_API_KEY override), so we treat the key as optional here too.
    "kimi": None,
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


# Local providers that speak HTTP — we can probe them before committing a
# model switch so the user gets a clean error instead of an httpx traceback
# surfacing mid-turn.
_LOCAL_PROVIDER_PROBES: dict[str, tuple[str, str]] = {
    # provider → (default_url, hint)
    "ollama": (
        "http://localhost:11434/api/tags",
        "Ollama server not reachable. Start it with: ollama serve",
    ),
    "lmstudio": (
        "http://localhost:1234/v1/models",
        "LM Studio server not reachable. Start the local server in the LM Studio app.",
    ),
}


def probe_provider(provider: str, timeout: float = 1.5) -> tuple[bool, str | None]:
    """Check whether a local provider's HTTP endpoint is reachable.

    Returns ``(reachable, hint_if_not)``. Non-local providers (openai,
    anthropic, google, ...) short-circuit to ``(True, None)`` — their
    reachability is effectively whether the API key works, and we only
    find that out on the first request.

    Ollama resolution order:
      1. ``OLLAMA_HOST`` / ``OLLAMA_BASE_URL`` if set (accepts host:port or URL)
      2. ``http://localhost:11434`` (default local daemon)
      3. Ollama Cloud (``https://ollama.com``) **if ``OLLAMA_API_KEY`` is
         set** — lets users run cloud-hosted models without a local
         ``ollama serve`` process.
    """
    provider = provider.lower()
    if provider not in _LOCAL_PROVIDER_PROBES:
        return True, None

    default_url, hint = _LOCAL_PROVIDER_PROBES[provider]
    candidates: list[tuple[str, dict[str, str]]] = []

    if provider == "ollama":
        # When an API key is configured, probe Ollama Cloud directly and
        # skip the local daemon entirely. Localhost is only probed when the
        # user explicitly points to it via OLLAMA_HOST / OLLAMA_BASE_URL.
        if key := os.environ.get("OLLAMA_API_KEY"):
            cloud_host = os.environ.get("OLLAMA_CLOUD_HOST", "https://ollama.com")
            candidates.append((
                f"{cloud_host.rstrip('/')}/v1/models",
                {"Authorization": f"Bearer {key}"},
            ))
        else:
            host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_BASE_URL")
            if host:
                base = host if host.startswith("http") else f"http://{host}"
                candidates.append((f"{base.rstrip('/')}/api/tags", {}))
            else:
                # No API key and no explicit host — don't fall back to
                # localhost. Report that Ollama is not configured.
                return False, (
                    "Ollama is not configured. Set OLLAMA_API_KEY for Ollama "
                    "Cloud, or set OLLAMA_HOST / OLLAMA_BASE_URL for a custom endpoint."
                )
    else:
        candidates.append((default_url, {}))

    try:
        import httpx
    except ImportError:
        return False, hint

    for url, headers in candidates:
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            if resp.status_code < 500:
                return True, None
        except Exception:
            continue
    return False, hint


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
