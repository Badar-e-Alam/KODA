"""
KODA agent adapters.

Each adapter wraps a backend-specific agent (LangGraph, Anthropic SDK,
OpenAI, HTTP/SSE, ...) and exposes the `koda.agent_api.KodaAgent` Protocol
so the TUI can talk to it uniformly.
"""
