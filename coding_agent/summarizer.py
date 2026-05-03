from agent import CodingAgent
from message import Message


class Summarizer:
    """Wraps a CodingAgent for LLM summarization. Two modes:

    - `summarize(text)` — single text in, single summary out.
    - `summarize_batch(texts)` — list in, list of summaries out (one call per
      item; swap for a true batched API later if your provider supports it).

    `max_output_chars` caps the summary length — pick the model when you build
    the underlying CodingAgent (name, base_url, api_key)."""

    def __init__(self, agent: CodingAgent, max_output_chars: int = 10_000):
        self.agent = agent
        self.max_output_chars = max_output_chars

    def summarize(self, text: str) -> str:
        prompt = (
            f"Summarize the following content in at most {self.max_output_chars} "
            f"characters. Preserve key decisions, file paths, errors, tool results, "
            f"and outstanding TODOs.\n\n"
            f"{text}"
        )
        resp = self.agent.client.chat.completions.create(
            model=self.agent.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "")[: self.max_output_chars]

    def summarize_batch(self, texts: list[str]) -> list[str]:
        return [self.summarize(t) for t in texts]

    def summarize_messages(self, msgs: list[Message]) -> str:
        joined = "\n\n".join(f"[{m.role}] {m.content}" for m in msgs)
        return self.summarize(joined)
