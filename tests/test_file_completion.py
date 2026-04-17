"""
Test that @ file completion includes untracked files.

The upstream FuzzyFileController uses ``git ls-files`` which only returns
tracked files.  In a fresh repo (no commits), this means @ shows nothing.
KODA patches ``_get_files`` to use ``--cached --others --exclude-standard``
so both tracked and untracked files appear.
"""

from __future__ import annotations

import pytest

from koda.app import KodaApp


@pytest.mark.asyncio
async def test_file_completion_includes_untracked():
    """After on_mount, the file controller should list project files
    even when they are untracked (not yet committed)."""
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]

        ci = app._chat_input
        assert ci is not None, "ChatInput not mounted"

        fc = getattr(ci, "_file_controller", None)
        assert fc is not None, "FuzzyFileController not found on ChatInput"

        files = fc._get_files()
        assert len(files) > 0, (
            "@ file completion returned no files — patch for untracked files "
            "is not working. git ls-files --cached --others should list files."
        )

        # Sanity: at least koda/app.py should be listed
        assert any("app.py" in f for f in files), (
            f"Expected koda/app.py in file list, got: {files[:10]}"
        )


@pytest.mark.asyncio
async def test_file_completion_fuzzy_suggestions():
    """Typing @app should produce suggestions containing app.py."""
    async with KodaApp().run_test() as pilot:
        app: KodaApp = pilot.app  # type: ignore[assignment]

        fc = app._chat_input._file_controller
        suggestions = fc._get_fuzzy_suggestions("app")

        assert len(suggestions) > 0, "No suggestions for 'app'"
        labels = [label for label, _ in suggestions]
        assert any("app.py" in label for label in labels), (
            f"Expected @...app.py in suggestions, got: {labels}"
        )
