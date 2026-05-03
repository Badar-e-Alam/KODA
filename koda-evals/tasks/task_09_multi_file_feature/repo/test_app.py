import json
import pytest
import app


@pytest.fixture(autouse=True)
def reset_store():
    """Fresh store for each test."""
    from models import TodoStore
    app.store = TodoStore()
    yield


def _decode(resp):
    status, _headers, body = resp
    return status, json.loads(body)


def test_list_empty():
    status, data = _decode(app.handle("GET", "/todos", b""))
    assert status == 200 and data == []


def test_create_then_list():
    _decode(app.handle("POST", "/todos", json.dumps({"title": "buy milk"}).encode()))
    status, data = _decode(app.handle("GET", "/todos", b""))
    assert status == 200
    assert len(data) == 1
    assert data[0]["title"] == "buy milk"
    assert data[0]["completed"] is False


def test_get_missing():
    status, data = _decode(app.handle("GET", "/todos/99", b""))
    assert status == 404
    assert data == {"error": "not found"}


def test_delete():
    app.handle("POST", "/todos", json.dumps({"title": "x"}).encode())
    status, data = _decode(app.handle("DELETE", "/todos/1", b""))
    assert status == 200 and data == {"deleted": True}


# --- new tests for the feature being added ---

def test_complete_endpoint_marks_completed():
    app.handle("POST", "/todos", json.dumps({"title": "task"}).encode())
    status, data = _decode(app.handle("POST", "/todos/1/complete", b""))
    assert status == 200
    assert data == {"id": 1, "completed": True}


def test_complete_persists():
    app.handle("POST", "/todos", json.dumps({"title": "task"}).encode())
    app.handle("POST", "/todos/1/complete", b"")
    status, data = _decode(app.handle("GET", "/todos/1", b""))
    assert status == 200 and data["completed"] is True


def test_complete_missing_returns_404():
    status, data = _decode(app.handle("POST", "/todos/42/complete", b""))
    assert status == 404
    assert data == {"error": "not found"}


def test_mark_completed_method_exists():
    """Requirement: Todo class must have a mark_completed() method."""
    from models import Todo
    t = Todo(1, "x")
    assert hasattr(t, "mark_completed"), "Todo.mark_completed() not implemented"
    t.mark_completed()
    assert t.completed is True
