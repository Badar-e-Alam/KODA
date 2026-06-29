"""Onboarding modal: client selection, key entry, Ollama cloud-vs-local, and
.env persistence."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, RadioButton, Static

from koda.env_file import update_env_file
from koda.tui.onboarding import (
    CLIENTS,
    ONBOARDED_FLAG,
    SERVICES,
    OnboardingResult,
    OnboardingScreen,
    _service_input_id,
    needs_onboarding,
)


@pytest.fixture(autouse=True)
def _clean_service_env(monkeypatch):
    """Service inputs prefill from os.environ — clear them for deterministic tests."""
    for svc in SERVICES:
        monkeypatch.delenv(svc.env, raising=False)
    monkeypatch.delenv(ONBOARDED_FLAG, raising=False)


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Static()


async def _run_onboarding(
    client_id: str, cred: str, services: dict[str, str] | None = None
) -> OnboardingResult | None:
    """Push the screen, select a client, type a credential + services, save."""
    app = _Host()
    captured: dict[str, OnboardingResult | None] = {}
    async with app.run_test() as pilot:
        await app.push_screen(OnboardingScreen(), lambda r: captured.update(r=r))
        await pilot.pause()
        screen = app.screen
        idx = next(i for i, c in enumerate(CLIENTS) if c.id == client_id)
        list(screen.query(RadioButton))[idx].value = True
        await pilot.pause()
        screen.query_one("#onb-cred", Input).value = cred
        for env, value in (services or {}).items():
            screen.query_one("#" + _service_input_id(env), Input).value = value
        screen._submit()
        await pilot.pause()
    return captured.get("r")


@pytest.mark.asyncio
async def test_ollama_cloud_sets_flag_and_key() -> None:
    res = await _run_onboarding("ollama-cloud", "sk-cloud")
    assert res is not None
    assert res.env["OLLAMA_API_KEY"] == "sk-cloud"
    assert res.env["OLLAMA_USE_CLOUD"] == "1"
    assert res.model == "ollama:gpt-oss:120b-cloud"


@pytest.mark.asyncio
async def test_ollama_local_disables_cloud_and_uses_host() -> None:
    res = await _run_onboarding("ollama-local", "http://localhost:11434")
    assert res is not None
    assert res.env["OLLAMA_HOST"] == "http://localhost:11434"
    assert res.env["OLLAMA_USE_CLOUD"] == "0"
    assert res.model == "ollama:llama3.1"


@pytest.mark.asyncio
async def test_anthropic_key_and_marker() -> None:
    res = await _run_onboarding("anthropic", "sk-ant")
    assert res is not None
    assert res.env["ANTHROPIC_API_KEY"] == "sk-ant"
    assert res.env[ONBOARDED_FLAG] == "1"  # setup marked complete
    assert res.model == "anthropic:claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_service_keys_are_collected() -> None:
    res = await _run_onboarding(
        "anthropic", "sk-ant",
        services={
            "JINA_API_KEY": "jina_abc",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-1",
            "LANGFUSE_SECRET_KEY": "sk-lf-1",
        },
    )
    assert res is not None
    assert res.env["JINA_API_KEY"] == "jina_abc"
    assert res.env["LANGFUSE_PUBLIC_KEY"] == "pk-lf-1"
    assert res.env["LANGFUSE_SECRET_KEY"] == "sk-lf-1"


@pytest.mark.asyncio
async def test_blank_services_are_omitted() -> None:
    res = await _run_onboarding("anthropic", "sk-ant")
    assert res is not None
    # Untouched service inputs (incl. the default Langfuse host) aren't written.
    assert "JINA_API_KEY" not in res.env
    assert "LANGFUSE_HOST" not in res.env


@pytest.mark.asyncio
async def test_local_blank_host_falls_back_to_default() -> None:
    res = await _run_onboarding("ollama-local", "")
    assert res is not None
    assert res.env["OLLAMA_HOST"] == "http://localhost:11434"


def test_needs_onboarding(monkeypatch) -> None:
    for c in CLIENTS:
        monkeypatch.delenv(c.cred_env, raising=False)
    monkeypatch.delenv(ONBOARDED_FLAG, raising=False)
    assert needs_onboarding() is True
    # A configured credential suppresses it…
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    assert needs_onboarding() is False


def test_marker_suppresses_onboarding_even_without_keys(monkeypatch) -> None:
    for c in CLIENTS:
        monkeypatch.delenv(c.cred_env, raising=False)
    monkeypatch.setenv(ONBOARDED_FLAG, "1")
    assert needs_onboarding() is False  # set once → never auto-shown again


def test_update_env_file_preserves_and_appends() -> None:
    d = Path(tempfile.mkdtemp())
    p = d / ".env"
    p.write_text("# cfg\nANTHROPIC_API_KEY=old\nKEEP=1\n")
    update_env_file({"ANTHROPIC_API_KEY": "new", "OLLAMA_API_KEY": "sk"}, p)
    text = p.read_text()
    assert "# cfg" in text and "KEEP=1" in text
    assert "ANTHROPIC_API_KEY=new" in text and "ANTHROPIC_API_KEY=old" not in text
    assert "OLLAMA_API_KEY=sk" in text
