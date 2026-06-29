"""Ollama Cloud routing is opt-in: only a ``:cloud``/``-cloud`` model tag or
the ``OLLAMA_USE_CLOUD`` flag sends an ``ollama:`` model to the cloud."""

from __future__ import annotations

import pytest

from koda.adapters import deep


@pytest.mark.parametrize(
    "name, expected",
    [
        ("gpt-oss:120b-cloud", True),
        ("glm-4.6:cloud", True),
        ("llama3.1", False),
        ("kimi-k2.7-code", False),
        ("gpt-oss:20b", False),
    ],
)
def test_cloud_tag_detection(name: str, expected: bool, monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_USE_CLOUD", raising=False)
    assert deep._wants_ollama_cloud(name) is expected


def test_env_flag_forces_cloud_for_untagged(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_USE_CLOUD", "1")
    assert deep._wants_ollama_cloud("llama3.1") is True
    monkeypatch.setenv("OLLAMA_USE_CLOUD", "off")
    assert deep._wants_ollama_cloud("llama3.1") is False


def test_untagged_local_model_does_not_route_to_cloud(monkeypatch) -> None:
    """A global API key must NOT hijack a plain local model."""
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-test")
    monkeypatch.delenv("OLLAMA_USE_CLOUD", raising=False)
    called = {"cloud": False}

    def _fake_init(model):
        return ("init_chat_model", model)

    monkeypatch.setattr(deep, "init_chat_model", _fake_init)
    result = deep._build_chat_model("ollama:llama3.1")
    assert result == ("init_chat_model", "ollama:llama3.1")
    assert called["cloud"] is False


def test_cloud_tagged_model_routes_to_cloud(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-test")
    cm = deep._build_chat_model("ollama:gpt-oss:120b-cloud")
    # ChatOllama pointed at the cloud host, model name keeps its full tag.
    assert type(cm).__name__ == "ChatOllama"
    assert cm.model == "gpt-oss:120b-cloud"
    assert str(cm.base_url).rstrip("/") == "https://ollama.com"


def test_cloud_tag_without_key_falls_back(monkeypatch) -> None:
    """No key → can't reach cloud → defer to init_chat_model, not crash here."""
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(deep, "init_chat_model", lambda model: ("fallback", model))
    assert deep._build_chat_model("ollama:gpt-oss:120b-cloud") == (
        "fallback",
        "ollama:gpt-oss:120b-cloud",
    )
