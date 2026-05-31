"""Regression tests for ``_PathNormalizingBackend``.

OS-absolute paths that fall inside the backend root should be rewritten
to virtual-absolute form. Everything else passes through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from coding_agent.backend import _PathNormalizingBackend


class _RecordingBackend:
    """Captures the path each delegated call receives, returns a stub."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> str:
            self.calls.append((name, args, kwargs))
            return "ok"

        return _record


def _make(root: str = "/Users/me/proj"):
    inner = _RecordingBackend()
    # ``Path(root).resolve()`` would touch the FS; skip by patching
    # _root_prefix directly so the test stays hermetic.
    wrapper = _PathNormalizingBackend.__new__(_PathNormalizingBackend)
    wrapper._inner = inner  # type: ignore[attr-defined]
    wrapper._root_prefix = root  # type: ignore[attr-defined]
    return wrapper, inner


def test_os_absolute_path_under_root_is_rewritten() -> None:
    """`/Users/me/proj/foo.py` → `/foo.py`."""
    wrapper, inner = _make()
    wrapper.read("/Users/me/proj/coding_agent/backend.py")
    assert inner.calls == [("read", ("/coding_agent/backend.py",), {})]


def test_virtual_absolute_path_unchanged() -> None:
    """`/coding_agent/backend.py` passes through as-is."""
    wrapper, inner = _make()
    wrapper.read("/coding_agent/backend.py")
    assert inner.calls == [("read", ("/coding_agent/backend.py",), {})]


def test_relative_path_unchanged() -> None:
    """`coding_agent/backend.py` (no leading `/`) passes through."""
    wrapper, inner = _make()
    wrapper.read("coding_agent/backend.py")
    assert inner.calls == [("read", ("coding_agent/backend.py",), {})]


def test_os_absolute_path_outside_root_unchanged() -> None:
    """``/etc/passwd`` is not under our root — leave it alone so the
    inner backend can return its normal "not found" error rather than
    silently mapping the path."""
    wrapper, inner = _make()
    wrapper.read("/etc/passwd")
    assert inner.calls == [("read", ("/etc/passwd",), {})]


def test_root_exactly_maps_to_virtual_root() -> None:
    """Passing the project root itself should normalize to `/`, not `""`."""
    wrapper, inner = _make()
    wrapper.ls("/Users/me/proj")
    # tail starts empty → branch returns "/" + tail = "/"
    assert inner.calls == [("ls", ("/",), {})]


def test_glob_normalizes_path_arg_not_pattern() -> None:
    """The pattern arg is search syntax — never normalize it."""
    wrapper, inner = _make()
    wrapper.glob("**/*.py", "/Users/me/proj/koda")
    assert inner.calls == [("glob", ("**/*.py", "/koda"), {})]


def test_download_files_normalizes_each_entry() -> None:
    """List-of-paths args (``adownload_files``) normalize element-wise."""
    wrapper, inner = _make()
    wrapper.download_files(
        ["/Users/me/proj/a.py", "/b.py", "/Users/me/proj/sub/c.py"]
    )
    assert inner.calls == [
        ("download_files", (["/a.py", "/b.py", "/sub/c.py"],), {})
    ]


def test_non_string_path_passes_through() -> None:
    """``ls`` is required to take a string, but if some future caller
    passes a Path object we shouldn't crash — just delegate untouched."""
    wrapper, inner = _make()
    p = Path("/Users/me/proj/koda")
    wrapper.ls(p)
    # Path object is not a str → _norm returns it unchanged.
    assert inner.calls == [("ls", (p,), {})]


def test_pathnorm_is_used_in_build_backend(tmp_path: Path) -> None:
    """``build_backend`` actually returns the wrapped backend, not the
    raw composite — otherwise the normalizer is dead code."""
    from coding_agent.backend import build_backend

    be = build_backend(tmp_path)
    assert isinstance(be, _PathNormalizingBackend)


@pytest.mark.asyncio
async def test_async_methods_also_normalize() -> None:
    """``aread`` / ``awrite`` / etc. share the same normalization path
    so behavior is identical on the async side."""
    wrapper, inner = _make()

    class _AsyncInner:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple, dict]] = []

        def __getattr__(self, name: str) -> Any:
            async def _record(*args: Any, **kwargs: Any) -> str:
                self.calls.append((name, args, kwargs))
                return "ok"

            return _record

    async_inner = _AsyncInner()
    wrapper._inner = async_inner  # type: ignore[attr-defined]

    await wrapper.aread("/Users/me/proj/foo.py")
    assert async_inner.calls == [("aread", ("/foo.py",), {})]
