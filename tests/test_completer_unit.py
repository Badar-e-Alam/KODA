"""
Focused unit tests for koda.tui.completers — the logic that decides which
suggestion bucket is active for a given (value, cursor) pair.
"""

from __future__ import annotations

from koda.tui.completers import complete, _find_at_token


def test_bare_slash_returns_commands():
    result = complete("/", 1)
    assert result is not None
    sugg, rng, title = result
    assert title == "Commands"
    assert rng == (0, 1)
    assert any(s.label == "/clear" for s in sugg)


def test_slash_model_with_space_switches_to_models():
    result = complete("/model ", 7)
    assert result is not None
    _, rng, title = result
    assert title == "Models"
    assert rng == (7, 7)


def test_slash_theme_with_space_switches_to_themes():
    result = complete("/theme ", 7)
    assert result is not None
    _, rng, title = result
    assert title == "Themes"
    assert rng == (7, 7)


def test_slash_exact_match_filters_out_self():
    """Typing '/tree' exactly should not suggest '/tree' again."""
    sugg, _, _ = complete("/tree", 5)
    labels = [s.label for s in sugg]
    assert "/tree" not in labels


def test_at_empty_fragment_lists_files():
    result = complete("@", 1)
    assert result is not None
    sugg, rng, title = result
    assert title == "Files"
    assert rng == (0, 1)


def test_at_token_in_middle_of_string():
    value = "please look at @src/main"
    result = complete(value, len(value))
    assert result is not None
    _, rng, title = result
    assert title == "Files"
    # @src/main starts at index 15
    assert rng == (15, len(value))


def test_no_trigger_returns_none():
    assert complete("hello world", 5) is None
    assert complete("", 0) is None
    # Lone whitespace
    assert complete("   ", 2) is None


def test_find_at_token_stops_at_whitespace():
    # `@foo bar` — cursor at end should find `@foo`, not `@foo bar`
    assert _find_at_token("@foo bar", 8) is None  # cursor is on 'r', not inside @-token
    # cursor inside @foo
    assert _find_at_token("@foo bar", 3) == (0, 4)


def test_theme_completion_insert_is_bare_name():
    """Regression: the /theme completer must return just the theme name,
    not '/theme <name>', otherwise accepting the suggestion with a
    replace_range of (7, …) duplicates the prefix into '/theme /theme …'.
    """
    result = complete("/theme sol", 10)
    assert result is not None
    sugg, rng, _ = result
    assert rng[0] == 7
    # Simulate the ChatInput accept-suggestion path.
    value = "/theme sol"
    for s in sugg:
        assert not s.insert.startswith("/theme"), (
            f"insert should be bare name, got {s.insert!r}"
        )
        new_value = value[: rng[0]] + s.insert + value[rng[1] :]
        assert not new_value.startswith("/theme /theme"), (
            f"accepting suggestion produced duplicated prefix: {new_value!r}"
        )


def test_model_completion_insert_is_bare_identifier():
    """Regression: same as above for /model."""
    from koda.model_config import get_available_models
    if not any(get_available_models().values()):
        return

    result = complete("/model gpt", 10)
    assert result is not None
    sugg, rng, _ = result
    assert rng[0] == 7
    value = "/model gpt"
    for s in sugg:
        assert not s.insert.startswith("/model"), (
            f"insert should be bare provider:model, got {s.insert!r}"
        )
        new_value = value[: rng[0]] + s.insert + value[rng[1] :]
        assert not new_value.startswith("/model /model"), (
            f"accepting suggestion produced duplicated prefix: {new_value!r}"
        )


def test_slash_model_substring_match():
    """/model claude should match any claude-* entry."""
    from koda.model_config import get_available_models
    # Only assert this if we actually have models cached
    if not any(get_available_models().values()):
        return
    result = complete("/model claude", 13)
    assert result is not None
    sugg, _, _ = result
    # Every label should contain 'claude' (case-insensitive)
    for s in sugg:
        assert "claude" in s.label.lower(), s.label
