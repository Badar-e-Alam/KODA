"""koda.bridge — the NDJSON stdio protocol that the TypeScript+Ink UI drives.

Covers the two pure pieces the wire depends on: @file attachment expansion
and AgentEvent → JSON serialization (schema the Node client parses)."""

from __future__ import annotations

from koda.agent_api import (
    Done,
    PermissionItem,
    PermissionRequest,
    TextDelta,
    ThinkingDelta,
    ToolResult,
    ToolStart,
    Usage,
)
from koda.bridge import _event_to_json, expand_at_files


# ── @file expansion ──────────────────────────────────────────────────────


def test_expand_at_files_inlines_existing(tmp_path) -> None:
    p = tmp_path / "note.txt"
    p.write_text("hello world")
    out = expand_at_files(f"look at @{p}")
    assert out.startswith("look at @")  # visible token preserved
    assert "hello world" in out
    assert f'<attached-file path="{p}">' in out


def test_expand_at_files_ignores_missing(tmp_path) -> None:
    text = f"see @{tmp_path / 'nope.txt'}"
    assert expand_at_files(text) == text


def test_expand_at_files_no_tokens() -> None:
    assert expand_at_files("plain message with no refs") == "plain message with no refs"


def test_expand_at_files_dedupes(tmp_path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("body")
    out = expand_at_files(f"@{p} and again @{p}")
    assert out.count("<attached-file") == 1


# ── event serialization ──────────────────────────────────────────────────


def test_event_to_json_text_delta() -> None:
    assert _event_to_json(TextDelta("hi")) == {"type": "text_delta", "content": "hi"}


def test_event_to_json_thinking_delta() -> None:
    assert _event_to_json(ThinkingDelta("hmm")) == {"type": "thinking_delta", "content": "hmm"}


def test_event_to_json_tool_start() -> None:
    ev = ToolStart(tool_id="t1", name="ls", arguments={"path": "."})
    assert _event_to_json(ev) == {
        "type": "tool_start",
        "tool_id": "t1",
        "name": "ls",
        "arguments": {"path": "."},
    }


def test_event_to_json_tool_result() -> None:
    d = _event_to_json(ToolResult(tool_id="t1", output="ok", is_error=False))
    assert d == {"type": "tool_result", "tool_id": "t1", "output": "ok", "is_error": False}


def test_event_to_json_done_with_usage() -> None:
    d = _event_to_json(Done(usage=Usage(input_tokens=5, output_tokens=7)))
    assert d["type"] == "done"
    assert d["usage"]["input_tokens"] == 5
    assert d["usage"]["output_tokens"] == 7


def test_event_to_json_done_no_usage() -> None:
    assert _event_to_json(Done(usage=None)) == {"type": "done", "usage": None}


def test_event_to_json_permission_request() -> None:
    item = PermissionItem(
        tool_name="write_file",
        args={"file_path": "x"},
        allowed_decisions=("approve", "reject"),
        description="",
    )
    d = _event_to_json(PermissionRequest(items=[item]))
    assert d["type"] == "permission_request"
    assert d["items"][0]["tool_name"] == "write_file"
    assert d["items"][0]["allowed_decisions"] == ["approve", "reject"]
