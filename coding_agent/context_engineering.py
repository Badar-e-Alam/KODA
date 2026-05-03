import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from message import Message, Role
from summarizer import Summarizer

Middleware = Callable[["ContextAgent"], None]


class OffloadMiddleware:
    """When `total_chars(message_history) > max_chars`, slice off everything
    older than the last `keep_recent` messages, write the slice to a JSONL
    file under `offload_dir`, and replace it in-place with a marker message
    `[offloaded:<filename>]` so a downstream middleware can pick it up."""

    def __init__(
        self,
        max_chars: int = 50_000,
        keep_recent: int = 4,
        offload_dir: str | Path = "./context_offload",
    ):
        self.max_chars = max_chars
        self.keep_recent = keep_recent
        self.offload_dir = Path(offload_dir)
        self.offload_dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, ctx: "ContextAgent") -> None:
        total = sum(len(m) for m in ctx.message_history)
        if total <= self.max_chars or len(ctx.message_history) <= self.keep_recent:
            return

        old = ctx.message_history[: -self.keep_recent]
        recent = ctx.message_history[-self.keep_recent :]

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.offload_dir / f"offload-{ts}.jsonl"
        with path.open("w") as f:
            for m in old:
                f.write(json.dumps({"role": m.role, "content": m.content}) + "\n")

        ctx.offloaded_paths.append(path)
        ctx.message_history = [
            Message(role="system", content=f"[offloaded:{path.name}]"),
            *recent,
        ]


class SummarizeMiddleware:
    """Reads each offload file in `chunk_chars` blocks (default 50_000), runs
    each block through the Summarizer (50k → ~`summarizer.max_output_chars`),
    and replaces the `[offloaded:...]` marker in the message history with
    a single summary message. Set `batch=True` to use `summarize_batch`."""

    def __init__(
        self,
        summarizer: Summarizer,
        chunk_chars: int = 50_000,
        batch: bool = False,
    ):
        self.summarizer = summarizer
        self.chunk_chars = chunk_chars
        self.batch = batch

    def __call__(self, ctx: "ContextAgent") -> None:
        for path in list(ctx.offloaded_paths):
            chunks = self._read_in_chunks(path)
            summaries = (
                self.summarizer.summarize_batch(chunks)
                if self.batch
                else [self.summarizer.summarize(c) for c in chunks]
            )
            combined = "\n\n".join(summaries)

            marker = f"[offloaded:{path.name}]"
            for i, m in enumerate(ctx.message_history):
                if m.content == marker:
                    ctx.message_history[i] = Message(
                        role="system",
                        content=f"[summary of {path.name}]\n{combined}",
                    )
                    break
            ctx.offloaded_paths.remove(path)

    def _read_in_chunks(self, path: Path) -> list[str]:
        text = "\n".join(
            f"[{json.loads(l)['role']}] {json.loads(l)['content']}"
            for l in path.read_text().splitlines()
            if l.strip()
        )
        if not text:
            return [""]
        return [text[i : i + self.chunk_chars] for i in range(0, len(text), self.chunk_chars)]


class ContextAgent:
    """Holds the system prompt, the running message history, and the tool
    responses. Middleware (offload, summarize, ...) runs after every `add`
    in declared order — that's the deepagents-style hook the user asked for."""

    def __init__(
        self,
        system_prompt: str,
        middleware: list[Middleware] | None = None,
    ):
        self.system_prompt = system_prompt
        self.message_history: list[Message] = []
        self.tool_responses: list[Message] = []
        self.offloaded_paths: list[Path] = []
        self.middleware: list[Middleware] = list(middleware or [])

    def add(self, role: Role, content: str) -> None:
        self.message_history.append(Message(role=role, content=content))
        self.run_middleware()

    def add_tool_response(self, content: str) -> None:
        msg = Message(role="tool", content=content)
        self.tool_responses.append(msg)
        self.message_history.append(msg)
        self.run_middleware()

    def run_middleware(self) -> None:
        for mw in self.middleware:
            mw(self)

    def total_chars(self) -> int:
        return sum(len(m) for m in self.message_history)


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    from agent import CodingAgent

    load_dotenv()

    summarizer = Summarizer(
        agent=CodingAgent(
            base_url=os.getenv("OLLAMA_BASE_URL"),
            api_key=os.getenv("OLLAMA_API_KEY"),
            model="qwen3-coder:480b",
        ),
        max_output_chars=400,
    )

    ctx = ContextAgent(
        system_prompt="You are a coding assistant.",
        middleware=[
            OffloadMiddleware(max_chars=2_000, keep_recent=2),
            SummarizeMiddleware(summarizer, chunk_chars=2_000, batch=False),
        ],
    )

    for i in range(8):
        ctx.add("assistant", f"chunk {i}: " + ("x" * 400))

    print(f"messages={len(ctx.message_history)} chars={ctx.total_chars()}")
    for m in ctx.message_history:
        print(f"--- {m.role} ({len(m)} chars) ---")
        print(m.content[:200])
