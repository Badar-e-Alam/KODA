"""Model resolution for `coding_agent`.

Picks the chat model spec (or instance) used by the agent. Most provider
specs (``anthropic:…``, ``openai:…``, …) are returned as-is so
``create_deep_agent`` can resolve them. ``kimi:`` and ``ollama:`` specs
are resolved eagerly here so the endpoint and auth header can be
attached.

Kimi / Ollama Cloud routing
---------------------------
Cloud endpoints (`OLLAMA_API_KEY` set with no `OLLAMA_HOST`/
`OLLAMA_BASE_URL`) route to ``https://ollama.com/v1`` via ``ChatOpenAI``
(Ollama Cloud is OpenAI-shaped). Local daemons route through
``ChatOllama`` against the native HTTP API.
"""

from __future__ import annotations

import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

DEFAULT_MODEL = "anthropic:claude-sonnet-4-6"

# Default Ollama model id for a bare `kimi:` / `ollama:` spec.
KIMI_DEFAULT_MODEL = "kimi-k2.6"

# Provider names routed through the Ollama-family resolver below. Matched
# both as a prefix (``ollama:glm-5.1``) and as a bare spec with no model id
# (``ollama`` / ``kimi``) — a bare spec falls back to ``KIMI_DEFAULT_MODEL``
# rather than being passed verbatim to ``init_chat_model``, which can't infer
# a provider from ``"ollama"`` alone and raises ValueError at graph build.
_OLLAMA_PROVIDERS = ("kimi", "ollama")


def _resolve_ollama_endpoint() -> tuple[str, str | None]:
    """Return `(base_url, api_key)` for the Ollama-family endpoint.

    Resolution order (matches the rest of KODA so the TUI's preflight
    and this agent talk to the same endpoint):

      1. Explicit `OLLAMA_BASE_URL` — honored as-is.
      2. `OLLAMA_HOST` — coerced to a URL (adds `http://` if missing).
      3. `OLLAMA_API_KEY` set with no host → Ollama Cloud at
         `https://ollama.com/v1` (OpenAI-shaped).
      4. Otherwise → raises ``ValueError`` (local Ollama daemon is not used).
    """
    api_key = os.environ.get("OLLAMA_API_KEY")
    explicit = os.environ.get("OLLAMA_BASE_URL")
    if explicit:
        return explicit, api_key
    host = os.environ.get("OLLAMA_HOST")
    if host:
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host, api_key
    if api_key:
        return "https://ollama.com/v1", api_key
    raise ValueError(
        "Ollama is not configured. Set OLLAMA_API_KEY for Ollama Cloud, "
        "or set OLLAMA_HOST / OLLAMA_BASE_URL for a custom endpoint."
    )


def _build_ollama_model(spec: str) -> BaseChatModel:
    """Build an Ollama-family chat model.

    Routes Ollama Cloud (and any `.../v1` base URL) through `ChatOpenAI`,
    since the cloud exposes an OpenAI-compatible API rather than the
    native Ollama HTTP shape. Local daemons go through `ChatOllama`.
    """
    _, _, name = spec.partition(":")
    model_name = name.strip() or KIMI_DEFAULT_MODEL
    base_url, api_key = _resolve_ollama_endpoint()

    if base_url.rstrip("/").endswith("/v1") or "/v1/" in base_url:
        return init_chat_model(
            model_name,
            model_provider="openai",
            base_url=base_url,
            api_key=api_key or "ollama",
        )

    return init_chat_model(model_name, model_provider="ollama", base_url=base_url)


def resolve_model(model: str | None) -> str | BaseChatModel:
    """Pick the model spec or instance. `kimi:` / `ollama:` specs are
    resolved eagerly here so we can attach Ollama auth + endpoint; other
    specs stay as strings and `create_deep_agent` handles them.
    """
    spec = model or os.environ.get("KODA_DEFAULT_MODEL") or DEFAULT_MODEL
    head = spec.split(":", 1)[0].strip().lower()
    if head in _OLLAMA_PROVIDERS:
        return _build_ollama_model(spec)
    return spec
