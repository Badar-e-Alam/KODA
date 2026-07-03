"""Claude-Code-style session store: project slug, session-id filenames,
listing for /resume, and prefix resolution."""

from __future__ import annotations

from koda import session_store
from koda.session import SessionTree


def test_project_slug_claude_format(tmp_path):
    assert session_store.project_slug("/Users/x/Desktop/KODA") == "-Users-x-Desktop-KODA"
    assert session_store.project_slug(tmp_path).startswith("-")


def test_new_session_filename_is_session_id(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store.Path, "home", staticmethod(lambda: tmp_path))
    tree = session_store.new_session("/some/proj")
    assert tree.session_id == tree._path.stem  # filename IS the id
    assert tree._path.parent.name == "-some-proj"
    assert tree._path.parent.parent.name == "projects"


def test_session_tree_accepts_external_id(tmp_path):
    t = SessionTree(path=tmp_path / "abc.jsonl", session_id="abc")
    assert t.session_id == "abc"
    # persisted in the header and recovered on reload
    t2 = SessionTree(path=tmp_path / "abc.jsonl")
    assert t2.session_id == "abc"


def test_list_and_find_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store.Path, "home", staticmethod(lambda: tmp_path))
    a = session_store.new_session("/p")
    a.add_message("user", "hello world question")
    a.add_message("assistant", "answer")
    b = session_store.new_session("/p")  # empty — must be skipped
    infos = session_store.list_sessions("/p")
    assert [s.id for s in infos] == [a.session_id]
    assert infos[0].messages == 2
    assert "hello world" in infos[0].preview
    # prefix resolution
    found = session_store.find_session(a.session_id[:8], "/p")
    assert found is not None and found.stem == a.session_id
    assert session_store.find_session("zzzz", "/p") is None
    assert b.session_id not in [s.id for s in infos]
