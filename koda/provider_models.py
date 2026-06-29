"""Generic LLM provider model discovery with daily caching.

Each provider is defined as a ``ProviderSpec`` — a small struct that says
*where* to fetch models, *how* to parse the response, and *what* fallback
list to use when the server is unreachable.

Adding a new provider is one dict entry — no new modules, no subclasses.

Cache lives in ``~/.koda/models/<provider>.json`` with a configurable TTL
(default 24 h).  On startup KODA reads from cache (instant) and refreshes
stale entries in a background thread.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

_log = logging.getLogger("koda.providers")

_CACHE_DIR = Path.home() / ".koda" / "models"
_DEFAULT_TTL = 86400  # 24 hours


# ── Provider spec ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderSpec:
    """Everything KODA needs to discover models from one provider."""

    name: str
    """Provider key used in ``provider:model`` strings (e.g. ``"ollama"``)."""

    default_url: str
    """Base URL when no env override is set."""

    endpoint: str
    """Path appended to base URL (e.g. ``"/api/tags"``, ``"/models"``)."""

    parse: Callable[[dict[str, Any]], list[str]]
    """Extract a sorted model-name list from the JSON response body."""

    env_urls: tuple[str, ...] = ()
    """Env vars checked (in order) to override *default_url*."""

    auth_env: str | None = None
    """Env var holding a Bearer token / API key.  ``None`` = no auth."""

    fallback: tuple[str, ...] = ()
    """Models shown when the server is unreachable and no cache exists."""

    needs_key: bool = False
    """If ``True`` and *auth_env* is unset, skip this provider entirely."""

    ttl: int = _DEFAULT_TTL
    """Cache time-to-live in seconds."""

    cloud_url: str | None = None
    """Fallback URL tried if the primary URL fails (e.g. Ollama Cloud)."""

    cloud_env_urls: tuple[str, ...] = ()
    """Env vars that override *cloud_url* (checked in order)."""

    cloud_needs_key: bool = True
    """If ``True`` the cloud fallback is skipped when *auth_env* is unset."""

    cloud_endpoint: str | None = None
    """Endpoint path for the cloud fallback.  Defaults to *endpoint* if unset."""

    cloud_parse: Callable[[dict[str, Any]], list[str]] | None = None
    """Parser for the cloud fallback response.  Defaults to *parse* if unset."""


# ── Response parsers (reusable across many providers) ─────────────────

def _parse_ollama(data: dict[str, Any]) -> list[str]:
    """``GET /api/tags`` → ``{models: [{name: "llama3.1:latest"}]}``."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in data.get("models", []):
        name = entry.get("name", "").removesuffix(":latest")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    out.sort()
    return out


def _parse_openai_compat(data: dict[str, Any]) -> list[str]:
    """``GET /v1/models`` → ``{data: [{id: "gpt-4o"}]}``."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in data.get("data", []):
        name = entry.get("id", "")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    out.sort()
    return out


# ── Built-in provider specs ──────────────────────────────────────────
#
# To add a provider: insert one entry here.  That's it.

PROVIDERS: dict[str, ProviderSpec] = {
    "ollama": ProviderSpec(
        name="ollama",
        env_urls=("OLLAMA_HOST", "OLLAMA_BASE_URL"),
        default_url="http://localhost:11434",
        endpoint="/api/tags",
        auth_env="OLLAMA_API_KEY",
        parse=_parse_ollama,
        fallback=(
            "codellama", "deepseek-coder-v2", "gemma2",
            "kimi-k2.6", "llama3.1", "llama3.2", "mistral", "phi3", "qwen2.5-coder",
        ),
        needs_key=False,
        cloud_url="https://ollama.com",
        cloud_env_urls=("OLLAMA_CLOUD_HOST",),
        cloud_needs_key=True,
        cloud_endpoint="/v1/models",
        cloud_parse=_parse_openai_compat,
    ),
    "lmstudio": ProviderSpec(
        name="lmstudio",
        env_urls=("LMSTUDIO_BASE_URL",),
        default_url="http://localhost:1234/v1",
        endpoint="/models",
        parse=_parse_openai_compat,
        needs_key=False,
        cloud_env_urls=("LMSTUDIO_CLOUD_BASE_URL",),
        cloud_needs_key=False,
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        env_urls=("OPENROUTER_BASE_URL",),
        default_url="https://openrouter.ai/api",
        endpoint="/v1/models",
        auth_env="OPENROUTER_API_KEY",
        parse=_parse_openai_compat,
        needs_key=True,
    ),
    "openai": ProviderSpec(
        name="openai",
        env_urls=("OPENAI_BASE_URL",),
        default_url="https://api.openai.com",
        endpoint="/v1/models",
        auth_env="OPENAI_API_KEY",
        parse=_parse_openai_compat,
        fallback=(
            "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini",
            "gpt-5", "gpt-5-mini", "gpt-5-nano", "o1", "o1-mini", "o3-mini",
        ),
        needs_key=True,
    ),
    "anthropic": ProviderSpec(
        name="anthropic",
        env_urls=("ANTHROPIC_BASE_URL",),
        default_url="https://api.anthropic.com",
        endpoint="/v1/models",
        auth_env="ANTHROPIC_API_KEY",
        parse=_parse_openai_compat,  # Anthropic's /v1/models uses {data: [{id: ...}]}
        fallback=(
            "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5",
            "claude-opus-4-6", "claude-sonnet-4-5", "claude-haiku-4-0",
        ),
        needs_key=True,
    ),
    "google": ProviderSpec(
        name="google",
        env_urls=("GOOGLE_BASE_URL",),
        default_url="https://generativelanguage.googleapis.com",
        endpoint="/v1beta/models",
        auth_env="GOOGLE_API_KEY",
        parse=lambda d: sorted({
            m.get("name", "").removeprefix("models/")
            for m in d.get("models", [])
            if m.get("name")
        }),
        fallback=(
            "gemini-2.5-flash", "gemini-2.5-pro",
            "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro",
        ),
        needs_key=True,
    ),
}


# ── Generic fetch / cache / inject ───────────────────────────────────

def _resolve_url(spec: ProviderSpec) -> str:
    for var in spec.env_urls:
        val = os.environ.get(var)
        if val:
            return val.rstrip("/")
    return spec.default_url.rstrip("/")


def _cache_path(provider: str) -> Path:
    return _CACHE_DIR / f"{provider}.json"


def _read_cache(provider: str, ttl: int) -> list[str] | None:
    try:
        data = json.loads(_cache_path(provider).read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) < ttl:
            models = data["models"]
            # An empty list cached by a broken discovery run is useless;
            # treat it as a miss so we re-fetch.
            if models:
                return models
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        pass
    return None


def _write_cache(provider: str, models: list[str]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(provider).write_text(
            json.dumps({"models": models, "ts": time.time()}),
            encoding="utf-8",
        )
    except OSError as exc:
        _log.debug("Could not write %s cache: %s", provider, exc)


def _resolve_cloud_url(spec: ProviderSpec) -> str | None:
    """Return the configured cloud fallback URL, or None."""
    for var in spec.cloud_env_urls:
        val = os.environ.get(var)
        if val:
            return val.rstrip("/")
    if spec.cloud_url:
        return spec.cloud_url.rstrip("/")
    return None


def _try_fetch(spec: ProviderSpec, url: str) -> list[str] | None:
    """Hit a specific URL. Returns None on failure or empty result.

    An empty list (e.g. local Ollama daemon with no models pulled) is
    treated as a miss so the cloud fallback can be tried.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if spec.auth_env:
        key = os.environ.get(spec.auth_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    try:
        req = Request(url, headers=headers, method="GET")
        with urlopen(req, timeout=3) as resp:  # noqa: S310
            data = json.loads(resp.read())
        parsed = spec.parse(data)
        if not parsed:
            return None
        return parsed
    except (URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        _log.debug("%s fetch failed (%s): %s", spec.name, url, exc)
        return None


def _fetch(spec: ProviderSpec) -> list[str] | None:
    """Try primary, then cloud fallback (if configured). Returns None on all failures."""
    have_key = bool(spec.auth_env and os.environ.get(spec.auth_env))
    cloud_url: str | None = None

    # Cloud first when credentials are present so users with OLLAMA_API_KEY
    # (or another provider's cloud key) don't pay a timeout waiting for a
    # local daemon they aren't running.
    cloud_base = _resolve_cloud_url(spec)
    if cloud_base is not None and (not spec.cloud_needs_key or have_key):
        cloud_endpoint = spec.cloud_endpoint or spec.endpoint
        cloud_url = f"{cloud_base}{cloud_endpoint}"
        _log.debug("%s trying cloud endpoint: %s", spec.name, cloud_url)
        if spec.cloud_parse:
            from dataclasses import replace

            cloud_spec = replace(spec, endpoint=cloud_endpoint, parse=spec.cloud_parse)
            live = _try_fetch(cloud_spec, cloud_url)
        else:
            live = _try_fetch(spec, cloud_url)
        if live is not None:
            return live

    # Primary: skip only if the provider strictly needs a key we don't have
    primary_url = f"{_resolve_url(spec)}{spec.endpoint}"
    if cloud_url == primary_url:
        return None
    if not (spec.needs_key and spec.auth_env and not have_key):
        live = _try_fetch(spec, primary_url)
        if live is not None:
            return live

    return None


def get_models(provider: str) -> list[str]:
    """Models for *provider*: cache → live fetch → fallback list.

    Blocks on network up to *spec.ttl*-expiry. Use `get_models_cached_only`
    from hot UI paths.
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        return []

    cached = _read_cache(provider, spec.ttl)
    if cached is not None:
        return cached

    live = _fetch(spec)
    if live is not None:
        _write_cache(provider, live)
        return live

    if spec.fallback:
        _log.debug("Using fallback model list for %s", provider)
    return list(spec.fallback)


def get_models_cached_only(provider: str) -> list[str]:
    """Non-blocking: disk cache → fallback list. Never hits the network.

    Safe to call from UI hot paths like the /model completer on every
    keystroke. A background thread (see model_config.warm_cache_in_background)
    is responsible for keeping the disk cache warm.
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        return []
    cached = _read_cache(provider, spec.ttl)
    if cached is not None:
        return cached
    return list(spec.fallback)


def refresh_stale() -> None:
    """Re-fetch every provider whose cache has expired.

    Meant for a background thread — never blocks the UI.
    """
    for name, spec in PROVIDERS.items():
        if _read_cache(name, spec.ttl) is not None:
            continue
        if spec.needs_key and spec.auth_env and not os.environ.get(spec.auth_env):
            continue
        live = _fetch(spec)
        if live is not None:
            _write_cache(name, live)
            _log.info("Refreshed %s model cache: %d models", name, len(live))


def _eligible_providers() -> list[str]:
    """Providers we can actually serve models for right now."""
    out: list[str] = []
    for name, spec in PROVIDERS.items():
        if spec.needs_key and spec.auth_env and not os.environ.get(spec.auth_env):
            continue
        out.append(name)
    return out


