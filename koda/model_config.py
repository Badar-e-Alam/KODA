"""
KODA's own model/provider config.

Replaces the `deepagents_cli.model_config` monkey-patch.

Provides:
  ModelSpec.try_parse("provider:model")
  has_provider_credentials("anthropic")
  get_available_models()  — dict[provider, list[model_name]]
"""

from __future__ import annotations

import os
from dataclasses import dataclass


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


def get_available_models() -> dict[str, list[str]]:
    """Discover models we can actually reach right now.

    Pulls from `koda.provider_models` (cached). Only includes providers we
    have credentials for.
    """
    from koda.provider_models import PROVIDERS, get_models

    out: dict[str, list[str]] = {}
    for name, spec in PROVIDERS.items():
        if spec.needs_key and spec.auth_env and not os.environ.get(spec.auth_env):
            continue
        models = get_models(name)
        if models:
            out[name] = models
    return out
