"""LLM client implementations using LangChain chat adapters.

This module provides a unified interface for different LLM providers
through LangChain's chat model adapters.
"""

from __future__ import annotations

import os
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel


def build_chat_model(spec: str, **kwargs: Any) -> BaseChatModel:
    """Build a LangChain chat model from a ``"provider:model[:tag]"`` spec.

    Routes by provider prefix:

    * ``openai:X``     → ``ChatOpenAI`` (uses ``OPENAI_API_KEY`` /
      ``OPENAI_BASE_URL``; pass ``base_url=`` / ``api_key=`` to override).
    * ``anthropic:X``  → ``ChatAnthropic``
    * ``google:X``     → ``ChatGoogleGenerativeAI``
    * ``ollama:X``     → ``ChatOpenAI`` against ``OLLAMA_BASE_URL`` when that
      URL ends in ``/v1`` (Ollama Cloud is OpenAI-compatible). Otherwise
      ``ChatOllama`` against the native Ollama HTTP API.

    Anything else falls through to ``init_chat_model`` so any provider
    LangChain natively supports works without a code change.

    The model tag can include colons (``ollama:kimi-k2.5``,
    ``ollama:qwen3-coder:480b``) — only the first colon separates provider
    from model name.
    """
    provider, sep, name = spec.partition(":")
    if not sep:
        raise ValueError(
            f"model spec must be 'provider:model[:tag]', got {spec!r}"
        )

    # Ollama Cloud is OpenAI-compatible; the openai-agents-style code we're
    # replacing already routed it through an OpenAI client, so do the same
    # here. Local Ollama (``http://host:11434``) still uses ChatOllama.
    if provider == "ollama":
        base_url = kwargs.pop("base_url", None) or os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        api_key = kwargs.pop("api_key", None) or os.getenv("OLLAMA_API_KEY", "ollama")
        if base_url.rstrip("/").endswith("/v1") or "/v1/" in base_url:
            return ChatOpenAI(
                model=name, base_url=base_url, api_key=api_key, **kwargs
            )
        return ChatOllama(model=name, base_url=base_url, **kwargs)

    # init_chat_model handles openai/anthropic/google/cohere/groq/etc.
    return init_chat_model(spec, **kwargs)


class LLMClient:
    """Unified LLM client using LangChain chat adapters.

    Supports multiple providers: openai, anthropic, google, ollama, and any
    OpenAI-compatible API via base_url configuration.

    Example:
        # OpenAI
        client = LLMClient(provider="openai", model_name="gpt-4o")

        # Anthropic
        client = LLMClient(provider="anthropic", model_name="claude-sonnet-4-6")

        # Custom OpenAI-compatible API
        client = LLMClient(
            provider="openai",
            model_name="llama-3.1",
            base_url="https://api.example.com/v1",
            api_key="your-key"
        )

        # Ollama (local)
        client = LLMClient(provider="ollama", model_name="llama3.1")
    """

    def __init__(
        self,
        provider: str,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the LLM client.

        Args:
            provider: The LLM provider ("openai", "anthropic", "google", "ollama").
            model_name: The model name to use.
            api_key: Optional API key. If not provided, will try environment variables.
            base_url: Optional base URL for OpenAI-compatible APIs.
            temperature: Sampling temperature (0.0 to 1.0).
            max_tokens: Maximum tokens to generate.
            **kwargs: Additional provider-specific parameters.
        """
        self.provider = provider.lower()
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._chat_model = self._create_chat_model(api_key, base_url, **kwargs)

    def _create_chat_model(
        self,
        api_key: str | None,
        base_url: str | None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Create the appropriate LangChain chat model based on provider.

        Args:
            api_key: Optional API key.
            base_url: Optional base URL for OpenAI-compatible APIs.
            **kwargs: Additional provider-specific parameters.

        Returns:
            Configured LangChain chat model instance.
        """
        common_params = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if self.provider == "openai":
            return ChatOpenAI(
                model=self.model_name,
                api_key=api_key or os.getenv("OPENAI_API_KEY"),
                base_url=base_url,
                **common_params,
                **kwargs,
            )

        if self.provider == "anthropic":
            return ChatAnthropic(
                model=self.model_name,
                api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
                base_url=base_url,
                **common_params,
                **kwargs,
            )

        if self.provider == "google":
            return ChatGoogleGenerativeAI(
                model=self.model_name,
                api_key=api_key or os.getenv("GOOGLE_API_KEY"),
                **common_params,
                **kwargs,
            )

        if self.provider == "ollama":
            return ChatOllama(
                model=self.model_name,
                base_url=base_url or "http://localhost:11434",
                **common_params,
                **kwargs,
            )

        raise ValueError(
            f"Unsupported provider: {self.provider}. "
            "Supported providers: openai, anthropic, google, ollama"
        )

    def invoke(
        self,
        messages: list[BaseMessage] | str,
        **kwargs: Any,
    ) -> BaseMessage:
        """Invoke the model with messages.

        Args:
            messages: Either a list of LangChain messages or a string prompt.
            **kwargs: Additional invocation parameters.

        Returns:
            The model's response message.
        """
        if isinstance(messages, str):
            messages = [HumanMessage(content=messages)]

        return self._chat_model.invoke(messages, **kwargs)

    async def ainvoke(
        self,
        messages: list[BaseMessage] | str,
        **kwargs: Any,
    ) -> BaseMessage:
        """Async invoke the model with messages.

        Args:
            messages: Either a list of LangChain messages or a string prompt.
            **kwargs: Additional invocation parameters.

        Returns:
            The model's response message.
        """
        if isinstance(messages, str):
            messages = [HumanMessage(content=messages)]

        return await self._chat_model.ainvoke(messages, **kwargs)

    def stream(
        self,
        messages: list[BaseMessage] | str,
        **kwargs: Any,
    ):
        """Stream responses from the model.

        Args:
            messages: Either a list of LangChain messages or a string prompt.
            **kwargs: Additional invocation parameters.

        Yields:
            Streaming message chunks.
        """
        if isinstance(messages, str):
            messages = [HumanMessage(content=messages)]

        yield from self._chat_model.stream(messages, **kwargs)

    @property
    def chat_model(self) -> BaseChatModel:
        """Access the underlying LangChain chat model."""
        return self._chat_model


def create_client_from_config(config: dict[str, Any]) -> LLMClient:
    """Create an LLMClient from a configuration dictionary.

    Args:
        config: Configuration dict with keys: provider, model_name, api_key,
                base_url, temperature, max_tokens, etc.

    Returns:
        Configured LLMClient instance.

    Example:
        config = {
            "provider": "openai",
            "model_name": "gpt-4o",
            "temperature": 0.5,
        }
        client = create_client_from_config(config)
    """
    return LLMClient(
        provider=config.get("provider", "openai"),
        model_name=config.get("model_name", "gpt-3.5-turbo"),
        api_key=config.get("api_key"),
        base_url=config.get("base_url"),
        temperature=config.get("temperature", 0.7),
        max_tokens=config.get("max_tokens"),
        **{k: v for k, v in config.items() if k not in {
            "provider", "model_name", "api_key", "base_url",
            "temperature", "max_tokens"
        }},
    )


if __name__ == "__main__":
    # Example usage and testing
    import sys

    # Simple test with different providers
    print("LLM Client Examples\n" + "=" * 50)

    # Example 1: OpenAI
    print("\n1. OpenAI Client:")
    try:
        openai_client = LLMClient(
            provider="openai",
            model_name="gpt-4o",
        )
        print(f"   Provider: {openai_client.provider}")
        print(f"   Model: {openai_client.model_name}")
        print(f"   Chat model type: {type(openai_client.chat_model).__name__}")
    except Exception as e:
        print(f"   Skipped (requires OPENAI_API_KEY): {e}")

    # Example 2: Anthropic
    print("\n2. Anthropic Client:")
    try:
        anthropic_client = LLMClient(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
        )
        print(f"   Provider: {anthropic_client.provider}")
        print(f"   Model: {anthropic_client.model_name}")
        print(f"   Chat model type: {type(anthropic_client.chat_model).__name__}")
    except Exception as e:
        print(f"   Skipped (requires ANTHROPIC_API_KEY): {e}")

    # Example 3: Custom OpenAI-compatible API
    print("\n3. Custom OpenAI-compatible API:")
    try:
        custom_client = LLMClient(
            provider="openai",
            model_name="llama-3.1",
            base_url="https://api.example.com/v1",
            api_key="test-key",
        )
        print(f"   Provider: {custom_client.provider}")
        print(f"   Model: {custom_client.model_name}")
        # Note: base_url is stored in openai_api_base for ChatOpenAI
        print(f"   Base URL: {getattr(custom_client._chat_model, 'openai_api_base', 'N/A')}")
    except Exception as e:
        print(f"   Error: {e}")

    # Example 4: Ollama (local)
    print("\n4. Ollama Client:")
    try:
        ollama_client = LLMClient(
            provider="ollama",
            model_name="llama3.1",
        )
        print(f"   Provider: {ollama_client.provider}")
        print(f"   Model: {ollama_client.model_name}")
        print(f"   Base URL: {ollama_client._chat_model.base_url}")
    except Exception as e:
        print(f"   Error: {e}")

    # Example 5: From config dict
    print("\n5. From Configuration:")
    try:
        config = {
            "provider": "openai",
            "model_name": "gpt-4o",
            "temperature": 0.5,
            "max_tokens": 2048,
        }
        config_client = create_client_from_config(config)
        print(f"   Provider: {config_client.provider}")
        print(f"   Model: {config_client.model_name}")
        print(f"   Temperature: {config_client.temperature}")
    except Exception as e:
        print(f"   Skipped (requires OPENAI_API_KEY): {e}")

    print("\n" + "=" * 50)
    print("Client examples completed!")
    print("\nNote: To actually invoke models, set appropriate API keys")
    print("in environment variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)")