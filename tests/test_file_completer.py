"""
Tests for the `@` file completer.

- It must work outside a git repository (os.walk fallback).
- It must ignore noisy directories (node_modules, .venv, __pycache__...).
- Results are ranked: name-prefix > name-substring > path-substring.
"""

from __future__ import annotations

import os

import pytest


def test_at_completer_works_outside_git_repo(tmp_path, monkeypatch):
    """Outside a git repo, @-completion must still list files via os.walk."""
    # Build a mini non-git tree
    (tmp_path / "hello.py").write_text("print('hi')")
    (tmp_path / "world.md").write_text("# hi")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.py").write_text("x=1")
    # Noisy dir that must be skipped
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "trash.js").write_text("// skip me")

    monkeypatch.chdir(tmp_path)
    from koda.tui import completers
    completers.invalidate_files_cache()

    files = completers._all_files()
    assert "hello.py" in files
    assert "world.md" in files
    assert "subdir/nested.py" in files
    assert not any(f.startswith("node_modules/") for f in files), (
        f"node_modules should be pruned, got: {files}"
    )


def test_at_completer_name_prefix_ranks_first(tmp_path, monkeypatch):
    """A file whose NAME starts with the fragment ranks above path-only matches."""
    (tmp_path / "app.py").write_text("a")
    (tmp_path / "widgets").mkdir()
    (tmp_path / "widgets" / "app_view.py").write_text("a")
    (tmp_path / "other_app.py").write_text("a")  # path match but name-substring

    monkeypatch.chdir(tmp_path)
    from koda.tui import completers
    completers.invalidate_files_cache()

    from koda.tui.completers import complete
    result = complete("@app", 4)
    assert result is not None
    sugg, _, title = result
    assert title == "Files"
    labels = [s.label for s in sugg]
    assert labels, "expected at least one file suggestion"
    # name-prefix `app.py` and `app_view.py` rank 0; `other_app.py` ranks 1
    assert labels[0] in ("app.py", "widgets/app_view.py"), labels


def test_at_completer_cache_is_invalidatable(tmp_path, monkeypatch):
    """invalidate_files_cache must force a re-scan."""
    (tmp_path / "first.py").write_text("a")
    monkeypatch.chdir(tmp_path)

    from koda.tui import completers
    completers.invalidate_files_cache()
    assert "first.py" in completers._all_files()

    (tmp_path / "second.py").write_text("b")
    # Without invalidation, cache is stale
    assert "second.py" not in completers._all_files()
    completers.invalidate_files_cache()
    assert "second.py" in completers._all_files()


@pytest.mark.asyncio
async def test_at_popup_updates_while_typing(tmp_path, monkeypatch):
    """Typing more characters after `@` must narrow the result set."""
    # Build a deterministic file tree so the test doesn't depend on the repo.
    (tmp_path / "koda_main.py").write_text("x")
    (tmp_path / "koda_util.py").write_text("x")
    (tmp_path / "other.py").write_text("x")
    monkeypatch.chdir(tmp_path)

    from koda.tui import completers
    completers.invalidate_files_cache()

    from koda.tui.app import KodaApp

    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]
        popup = app._popup
        assert popup is not None

        await pilot.press("@")
        await pilot.pause()
        assert popup.is_visible
        assert len(popup._suggestions) == 3  # all three files

        # Narrow to 'koda'
        for c in "koda":
            await pilot.press(c)
        await pilot.pause()
        assert popup.is_visible, "popup should stay visible while narrowing"
        # Only koda_* files remain
        assert len(popup._suggestions) == 2
        for s in popup._suggestions:
            assert "koda" in s.label.lower(), s.label
