"""
LangChain-based branch summarization for KODA.

Uses the same provider:model format as the agent to create a LangChain
chat model and summarize conversation branches during tree navigation.

The /backend command sets which model is used here.
If unset, the agent's main /model is used.
"""

from __future__ import annotations


SUMMARIZE_PROMPT = """\
Summarize the following conversation branch concisely.
Focus on: key decisions, findings, code discussed, unresolved questions,
and important context the user would need if returning later.
Keep the summary under 300 words.

Conversation:
{conversation}
"""


def create_chat_model(model_str: str):
    """
    Create a LangChain chat model from a 'provider:model' string.

    Supported providers: anthropic, openai, ollama, google.
    If no colon, defaults to anthropic.
    """
    if ":" not in model_str:
        provider, model_name = "anthropic", model_str
    else:
        provider, model_name = model_str.split(":", 1)

    provider = provider.lower().strip()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name)

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model_name)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model_name)

    raise ValueError(
        f"Unknown provider '{provider}'. "
        "Supported: anthropic, openai, ollama, google"
    )


async def summarize_messages(
    messages: list[dict[str, str]],
    model_str: str,
) -> str:
    """
    Summarize conversation messages using a LangChain chat model.

    Args:
        messages: List of {role, content} dicts to summarize.
        model_str: Provider:model string (e.g. 'anthropic:claude-sonnet-4-6').

    Returns:
        A concise summary string.
    """
    if not messages:
        return "(empty conversation)"

    lines: list[str] = []
    for msg in messages:
        role = "User" if msg.get("role") == "user" else "Assistant"
        content = msg.get("content", "").strip()
        if len(content) > 2000:
            content = content[:2000] + "... [truncated]"
        lines.append(f"{role}: {content}")

    conversation = "\n\n".join(lines)
    prompt = SUMMARIZE_PROMPT.format(conversation=conversation)

    llm = create_chat_model(model_str)
    response = await llm.ainvoke(prompt)
    return response.content
