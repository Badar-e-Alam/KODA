"""
Tests for stripping ANSI escape sequences out of tool output before it
lands in a ToolCallMessage. Raw cursor-up / clear-line / color codes in
the ``execute`` tool's output (e.g. ``make`` with color) corrupt Textual's
repaint and the user sees the input bar duplicated across the screen.
"""

from __future__ import annotations

import pytest

from koda.tui.widgets.messages import (
    ToolCallMessage,
    _sanitize_tool_output,
)


def test_strip_color_codes():
    raw = "\x1b[31merror:\x1b[0m something failed"
    assert _sanitize_tool_output(raw) == "error: something failed"


def test_strip_cursor_movement_codes():
    # Cursor up / clear line / save / restore
    raw = "hello\x1b[2A\x1b[Kworld\x1b[s\x1b[u"
    assert _sanitize_tool_output(raw) == "helloworld"


def test_strip_osc_sequences():
    # OSC 0 ; title BEL  (set window title) and OSC with ST terminator
    raw = "\x1b]0;terminal title\x07before\x1b]8;;https://example.com\x1b\\link"
    assert _sanitize_tool_output(raw) == "beforelink"


def test_carriage_return_keeps_last_overwrite():
    """Progress bars: 'Loading... 10%\\rLoading... 100%' → final 100%."""
    raw = "Loading... 10%\rLoading... 50%\rLoading... 100%\n"
    out = _sanitize_tool_output(raw)
    assert "Loading... 100%" in out
    assert "Loading... 10%" not in out
    assert "Loading... 50%" not in out


def test_crlf_normalized_to_lf():
    raw = "line one\r\nline two\r\nline three"
    assert _sanitize_tool_output(raw) == "line one\nline two\nline three"


def test_empty_and_none_handled():
    assert _sanitize_tool_output("") == ""


def test_plain_text_untouched():
    raw = "nothing special here"
    assert _sanitize_tool_output(raw) == raw


def test_tool_call_message_stores_sanitized_output():
    """End-to-end: set_result on a ToolCallMessage must strip escapes."""
    tc = ToolCallMessage(tool_id="t1", name="execute", arguments={"command": "make"})
    raw = "\x1b[32mok\x1b[0m\x1b[1A\x1b[K"
    tc.set_result(raw, is_error=False)
    assert tc._full_output == "ok"
    # Preview must not leak any escape bytes either
    assert "\x1b" not in str(tc._full_output)


def test_preview_still_truncates_after_sanitize():
    tc = ToolCallMessage(tool_id="t1", name="execute", arguments={"command": "make"})
    # Many lines of coloured output — preview is 1 line + "(+N lines)"
    raw = "\n".join(f"\x1b[33mstep {i}\x1b[0m" for i in range(10))
    tc.set_result(raw, is_error=False)
    # Sanitized output must not contain escape bytes
    assert "\x1b" not in tc._full_output
    # The on-screen render should be header + 1 preview line
    rendered = str(tc.render())
    # Rendered content should show at most 2 lines of actual text
    lines = [ln for ln in rendered.split("\n") if ln.strip()]
    assert len(lines) <= 2, f"expected ≤ 2 lines, got {len(lines)}: {lines!r}"
