"""First-run onboarding modal.

Lets the user pick a client (provider), enter the matching API key (or a host
URL for local backends), and — for Ollama — explicitly choose **Cloud** vs
**Local**. Values already present in the environment / ``.env`` are pre-filled
so the user can just confirm.

The screen returns an ``OnboardingResult`` (or ``None`` if skipped). Applying
it — setting ``os.environ``, persisting ``.env``, switching the model — is the
caller's job (see ``KodaApp.run_onboarding``), keeping this widget pure UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RadioButton, RadioSet, Static

# Marker persisted to .env once the user completes setup, so onboarding never
# auto-shows again (they can still reopen it with /setup).
ONBOARDED_FLAG = "KODA_ONBOARDED"


@dataclass(frozen=True)
class Client:
    """A selectable provider option in the onboarding list."""

    id: str
    label: str
    cred_env: str          # env var the credential/host is written to
    default_model: str     # full ``provider:model`` id seeded on selection
    field: str             # "key" → secret API key, "host" → URL
    extra_env: dict[str, str] = field(default_factory=dict)


# Order matters — first with credentials present becomes the default selection.
CLIENTS: list[Client] = [
    Client("anthropic", "Anthropic — Claude", "ANTHROPIC_API_KEY",
           "anthropic:claude-sonnet-4-6", "key"),
    Client("openai", "OpenAI — GPT", "OPENAI_API_KEY",
           "openai:gpt-4o", "key"),
    Client("google", "Google — Gemini", "GOOGLE_API_KEY",
           "google:gemini-2.5-flash", "key"),
    Client("openrouter", "OpenRouter", "OPENROUTER_API_KEY",
           "openrouter:openai/gpt-4o", "key"),
    # Ollama is split so the cloud/local choice is explicit. Cloud forces the
    # OLLAMA_USE_CLOUD flag so routing is unambiguous (see adapters/deep.py).
    Client("ollama-cloud", "Ollama — Cloud", "OLLAMA_API_KEY",
           "ollama:gpt-oss:120b-cloud", "key", {"OLLAMA_USE_CLOUD": "1"}),
    Client("ollama-local", "Ollama — Local", "OLLAMA_HOST",
           "ollama:llama3.1", "host", {"OLLAMA_USE_CLOUD": "0"}),
    Client("lmstudio", "LM Studio — Local", "LMSTUDIO_BASE_URL",
           "lmstudio:local-model", "host"),
]

_HOST_DEFAULTS = {
    "OLLAMA_HOST": "http://localhost:11434",
    "LMSTUDIO_BASE_URL": "http://localhost:1234/v1",
}


@dataclass(frozen=True)
class Service:
    """An optional non-model API key collected during onboarding."""

    env: str
    label: str
    secret: bool = True
    placeholder: str = ""
    default: str = ""


# Auxiliary services. These don't pick the model — they enable extra features
# (web search, tracing) when present. All optional; blanks are ignored.
SERVICES: list[Service] = [
    Service("JINA_API_KEY", "Web search — Jina (s.jina.ai / r.jina.ai)",
            placeholder="jina_… (optional, raises rate limits)"),
    Service("LANGFUSE_PUBLIC_KEY", "Langfuse — public key",
            secret=False, placeholder="pk-lf-…"),
    Service("LANGFUSE_SECRET_KEY", "Langfuse — secret key",
            placeholder="sk-lf-…"),
    Service("LANGFUSE_HOST", "Langfuse — host", secret=False,
            placeholder="https://cloud.langfuse.com",
            default="https://cloud.langfuse.com"),
]


def _service_input_id(env: str) -> str:
    return f"onb-svc-{env}"


@dataclass(frozen=True)
class OnboardingResult:
    client_id: str
    model: str
    env: dict[str, str]


class OnboardingScreen(ModalScreen[OnboardingResult | None]):
    """Pick a client, enter a key/host, choose Ollama cloud-vs-local."""

    BINDINGS = [
        Binding("escape", "skip", "Skip", show=True),
    ]

    CSS = """
    OnboardingScreen { align: center middle; }

    #onb-container {
        width: 72;
        height: auto;
        max-height: 90%;
        border: round $success 60%;
        background: $surface;
        padding: 1 2;
    }
    #onb-title { text-style: bold; color: $success; height: 1; }
    #onb-sub { color: $text-muted; height: auto; margin: 0 0 1 0; }
    #onb-body { height: auto; max-height: 26; }
    #onb-svc-title {
        height: 1; text-style: bold; color: $success; margin: 1 0 0 0;
    }
    .onb-svc-label { height: 1; color: $text-muted; }
    .onb-svc { margin: 0 0 1 0; }
    #onb-clients { height: auto; margin: 0 0 1 0; }
    #onb-cred-label { height: 1; color: $text; text-style: bold; }
    #onb-found { height: 1; color: $success; }
    #onb-cred { margin: 0 0 1 0; }
    #onb-model-label { height: 1; color: $text; text-style: bold; }
    #onb-model { margin: 0 0 1 0; }
    #onb-buttons { height: auto; align-horizontal: right; }
    #onb-buttons Button { margin: 0 0 0 2; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = _default_client()

    def compose(self) -> ComposeResult:
        with Vertical(id="onb-container"):
            yield Static("Welcome to KODA — setup", id="onb-title")
            yield Static(
                "Choose a client and add your API key (or a host for local "
                "models). Existing values from .env are pre-filled.",
                id="onb-sub",
            )
            with VerticalScroll(id="onb-body"):
                with RadioSet(id="onb-clients"):
                    for c in CLIENTS:
                        yield RadioButton(c.label, value=(c.id == self._client.id))
                yield Static("", id="onb-cred-label")
                yield Static("", id="onb-found")
                yield Input(id="onb-cred")
                yield Static("Model", id="onb-model-label")
                yield Input(id="onb-model")

                yield Static("Optional services", id="onb-svc-title")
                for svc in SERVICES:
                    yield Static(svc.label, classes="onb-svc-label")
                    yield Input(
                        value=os.environ.get(svc.env, svc.default),
                        placeholder=svc.placeholder,
                        password=svc.secret,
                        id=_service_input_id(svc.env),
                        classes="onb-svc",
                    )
            with Horizontal(id="onb-buttons"):
                yield Button("Skip", id="onb-skip", variant="default")
                yield Button("Save & Continue", id="onb-save", variant="success")

    def on_mount(self) -> None:
        self._sync_fields(self._client)

    # ── interaction ──────────────────────────────────────────────────

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        idx = event.radio_set.pressed_index
        if 0 <= idx < len(CLIENTS):
            self._client = CLIENTS[idx]
            self._sync_fields(self._client)

    def _sync_fields(self, client: Client) -> None:
        cred = self.query_one("#onb-cred", Input)
        label = self.query_one("#onb-cred-label", Static)
        found = self.query_one("#onb-found", Static)
        model = self.query_one("#onb-model", Input)

        is_host = client.field == "host"
        cred.password = not is_host
        if is_host:
            label.update(f"Host URL  ({client.cred_env})")
            cred.placeholder = _HOST_DEFAULTS.get(client.cred_env, "http://…")
        else:
            label.update(f"API key  ({client.cred_env})")
            cred.placeholder = "paste your key…"

        current = os.environ.get(client.cred_env, "")
        cred.value = current
        if current:
            found.update("✓ found in environment / .env")
        elif is_host:
            found.update("optional — leave blank for the default host")
        else:
            found.update("")
        model.value = client.default_model

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "onb-save":
            self._submit()
        else:
            self.action_skip()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        client = self._client
        cred = self.query_one("#onb-cred", Input).value.strip()
        model = self.query_one("#onb-model", Input).value.strip() or client.default_model

        env: dict[str, str] = dict(client.extra_env)
        if cred:
            env[client.cred_env] = cred
        elif client.field == "host":
            # Blank host → fall back to the conventional local default so the
            # local daemon is actually reachable.
            env[client.cred_env] = _HOST_DEFAULTS.get(client.cred_env, "")

        # Optional service keys — only carry the ones the user actually filled.
        for svc in SERVICES:
            value = self.query_one("#" + _service_input_id(svc.env), Input).value.strip()
            if value and value != svc.default:
                env[svc.env] = value

        # Mark setup complete so we never auto-prompt again.
        env[ONBOARDED_FLAG] = "1"

        self.dismiss(OnboardingResult(client_id=client.id, model=model, env=env))

    def action_skip(self) -> None:
        self.dismiss(None)


def _default_client() -> Client:
    """First client whose credential/host is already configured, else the first."""
    for c in CLIENTS:
        if os.environ.get(c.cred_env):
            return c
    return CLIENTS[0]


def needs_onboarding() -> bool:
    """Whether to auto-show onboarding on launch.

    Shows only on a genuine first run: not once the user has completed setup
    (the ``KODA_ONBOARDED`` marker, persisted to .env), and not when any
    provider credential/host is already configured (existing users are never
    nagged). ``/setup`` reopens it on demand regardless.
    """
    if os.environ.get(ONBOARDED_FLAG):
        return False
    return not any(os.environ.get(c.cred_env) for c in CLIENTS)
