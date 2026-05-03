"""Contract tests for the Flask Posts API.

Run with `pytest test_app.py -q`. The agent is implementing app.py from
spec.md; these tests are the canonical contract.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def client():
    # Re-import app on each test so the in-memory store is fresh.
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


def test_list_empty_initially(client):
    r = client.get("/posts")
    assert r.status_code == 200
    assert r.get_json() == {"posts": []}


def test_create_returns_201_with_id(client):
    r = client.post("/posts", json={"title": "hello", "body": "world"})
    assert r.status_code == 201
    body = r.get_json()
    assert body["id"] == 1
    assert body["title"] == "hello"
    assert body["body"] == "world"
    assert body["tags"] == []


def test_create_missing_title_400(client):
    r = client.post("/posts", json={"body": "no title"})
    assert r.status_code == 400


def test_create_empty_title_400(client):
    r = client.post("/posts", json={"title": "", "body": "x"})
    assert r.status_code == 400


def test_get_one_after_create(client):
    client.post("/posts", json={"title": "a", "body": "b"})
    r = client.get("/posts/1")
    assert r.status_code == 200
    assert r.get_json()["title"] == "a"


def test_get_missing_404(client):
    r = client.get("/posts/999")
    assert r.status_code == 404
    assert r.get_json() == {"error": "not found"}


def test_list_after_creates(client):
    client.post("/posts", json={"title": "p1", "body": ""})
    client.post("/posts", json={"title": "p2", "body": ""})
    r = client.get("/posts")
    assert r.status_code == 200
    posts = r.get_json()["posts"]
    assert len(posts) == 2
    assert {p["title"] for p in posts} == {"p1", "p2"}


def test_delete_returns_204_then_404(client):
    client.post("/posts", json={"title": "x", "body": ""})
    r = client.delete("/posts/1")
    assert r.status_code == 204
    # body is empty for 204
    assert r.data == b""
    # Now it's gone.
    r2 = client.get("/posts/1")
    assert r2.status_code == 404


def test_delete_missing_404(client):
    r = client.delete("/posts/999")
    assert r.status_code == 404


def test_ids_are_monotonic_after_delete(client):
    client.post("/posts", json={"title": "a", "body": ""})  # id 1
    client.post("/posts", json={"title": "b", "body": ""})  # id 2
    client.delete("/posts/2")
    r = client.post("/posts", json={"title": "c", "body": ""})
    # New id must be 3, not 2 (no reuse).
    assert r.get_json()["id"] == 3


def test_tag_filter(client):
    client.post("/posts", json={"title": "a", "body": "", "tags": ["python"]})
    client.post("/posts", json={"title": "b", "body": "", "tags": ["rust"]})
    client.post("/posts", json={"title": "c", "body": "", "tags": ["python", "rust"]})
    r = client.get("/posts?tag=python")
    assert r.status_code == 200
    titles = {p["title"] for p in r.get_json()["posts"]}
    assert titles == {"a", "c"}


def test_tag_filter_no_match(client):
    client.post("/posts", json={"title": "a", "body": "", "tags": ["python"]})
    r = client.get("/posts?tag=nope")
    assert r.status_code == 200
    assert r.get_json() == {"posts": []}


def test_default_tags_is_empty_list(client):
    r = client.post("/posts", json={"title": "no tags", "body": ""})
    assert r.get_json()["tags"] == []


def test_response_is_json(client):
    r = client.get("/healthz")
    assert "application/json" in r.headers["Content-Type"]
