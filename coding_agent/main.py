"""Coding-agent CLI: streaming output, persistent shell, approval modes,
project-aware context, conversation persistence."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agents import Runner
from dotenv import load_dotenv

from agent import coding_agent
from tools import set_approval_mode

load_dotenv("/home/b.alam/KODA/.env")


SESSION_DIR = Path.home() / ".coding-agent"
SESSION_FILE = SESSION_DIR / "session.json"


# ── Project context injection ───────────────────────────────────────────


def _project_context() -> str:
    """Snapshot the current project so the agent doesn't burn tool calls
    discovering basics (what repo, what stack, what's pending)."""
    cwd = Path.cwd().resolve()
    parts: list[str] = [f"# Project context\nWorking directory: {cwd}"]

    for fname in ("AGENTS.md", "CLAUDE.md", "README.md"):
        f = cwd / fname
        if f.is_file():
            try:
                content = f.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            snippet = content[:2000]
            ellipsis = "\n..." if len(content) > 2000 else ""
            parts.append(f"## {fname}\n{snippet}{ellipsis}")
            break

    hints: list[str] = []
    for marker, label in [
        ("pyproject.toml", "Python (pyproject)"),
        ("package.json", "Node"),
        ("Cargo.toml", "Rust"),
        ("go.mod", "Go"),
        ("requirements.txt", "Python (requirements)"),
    ]:
        if (cwd / marker).is_file():
            hints.append(label)
    if hints:
        parts.append(f"## Stack\n{', '.join(hints)}")

    try:
        status = subprocess.run(
            ["git", "status", "--short", "--branch"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if status.returncode == 0 and status.stdout.strip():
            parts.append(f"## git status\n{status.stdout.strip()}")
        log = subprocess.run(
            ["git", "log", "--oneline", "-10"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if log.returncode == 0 and log.stdout.strip():
            parts.append(f"## recent commits\n{log.stdout.strip()}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        entries = sorted(
            p.name + ("/" if p.is_dir() else "")
            for p in cwd.iterdir()
            if not p.name.startswith(".")
        )
        parts.append(f"## tree (depth 1)\n{'  '.join(entries[:40])}")
    except OSError:
        pass

    return "\n\n".join(parts)


# ── Session persistence ─────────────────────────────────────────────────


def _save_history(history: list) -> None:
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(history, default=str))
    except OSError:
        pass


def _load_history() -> list:
    if not SESSION_FILE.exists():
        return []
    try:
        data = json.loads(SESSION_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# ── Streaming printer ───────────────────────────────────────────────────


def _short(s: str, n: int = 100) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


async def _run_streamed(history: list, user_msg: str) -> list:
    history.append({"role": "user", "content": user_msg})

    result = Runner.run_streamed(coding_agent, history, max_turns=50)
    in_text = False  # are we currently printing a text delta block?

    try:
        async for ev in result.stream_events():
            etype = getattr(ev, "type", None)

            if etype == "raw_response_event":
                data = getattr(ev, "data", None)
                if getattr(data, "type", None) == "response.output_text.delta":
                    delta = getattr(data, "delta", "") or ""
                    if delta:
                        if not in_text:
                            print("\nagent> ", end="", flush=True)
                            in_text = True
                        print(delta, end="", flush=True)

            elif etype == "run_item_stream_event":
                name = getattr(ev, "name", None)
                item = getattr(ev, "item", None)
                if name == "tool_called" and item is not None:
                    if in_text:
                        print()
                        in_text = False
                    tool = getattr(item, "tool_name", None) or "tool"
                    raw = getattr(item, "raw_item", None)
                    args_str = ""
                    if isinstance(raw, dict):
                        args_str = str(raw.get("arguments", ""))
                    else:
                        args_str = str(getattr(raw, "arguments", "") or "")
                    print(f"  → {tool}({_short(args_str, 100)})", flush=True)
                elif name == "tool_output" and item is not None:
                    out: Any = getattr(item, "output", "")
                    if not isinstance(out, str):
                        try:
                            out = json.dumps(out, default=str)
                        except Exception:
                            out = str(out)
                    print(f"  ← {_short(out, 120)}", flush=True)
    except asyncio.CancelledError:
        try:
            result.cancel()
        except Exception:
            pass
        print("\n[interrupted]", flush=True)
        # Don't propagate — let the outer loop continue.
        return history

    if in_text:
        print()  # newline after streamed text

    return result.to_input_list()


# ── Main loop ───────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Coding agent CLI")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--yolo", action="store_true",
                   help="Auto-approve all tool calls (dangerous).")
    g.add_argument("--safe", action="store_true",
                   help="Prompt before any write or shell command.")
    p.add_argument("--resume", action="store_true",
                   help="Resume the previous session from ~/.coding-agent/session.json.")
    p.add_argument("--no-context", action="store_true",
                   help="Skip project-context injection on session start.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.yolo:
        set_approval_mode("yolo")
    elif args.safe:
        set_approval_mode("safe")
    else:
        set_approval_mode("default")

    history: list = _load_history() if args.resume else []
    if args.resume and history:
        print(f"resumed session ({len(history)} messages)")

    inject_context = not args.no_context and not history

    print("coding agent ready. type 'exit' to quit. Ctrl-C to interrupt a turn.\n")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user in {"", "exit", "quit"}:
                break

            if inject_context:
                user = f"{_project_context()}\n\n---\n\n{user}"
                inject_context = False

            task = loop.create_task(_run_streamed(history, user))
            try:
                history = loop.run_until_complete(task)
            except KeyboardInterrupt:
                task.cancel()
                try:
                    history = loop.run_until_complete(task)
                except (asyncio.CancelledError, KeyboardInterrupt):
                    pass

            _save_history(history)
            print()
    finally:
        try:
            loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
