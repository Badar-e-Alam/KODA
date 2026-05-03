"""Tiny Flask-like app using stdlib only (so eval has no extra deps)."""
import json
import re
from models import TodoStore

store = TodoStore()


def _json(body, status=200):
    return status, [("Content-Type", "application/json")], json.dumps(body).encode()


def handle(method: str, path: str, body: bytes):
    """Route a request. Returns (status, headers, body_bytes)."""
    if method == "GET" and path == "/todos":
        return _json([t.to_dict() for t in store.all()])

    if method == "POST" and path == "/todos":
        data = json.loads(body or b"{}")
        todo = store.create(data["title"])
        return _json(todo.to_dict(), status=201)

    m = re.match(r"^/todos/(\d+)$", path)
    if m and method == "GET":
        todo = store.get(int(m.group(1)))
        if not todo:
            return _json({"error": "not found"}, status=404)
        return _json(todo.to_dict())

    if m and method == "DELETE":
        if store.delete(int(m.group(1))):
            return _json({"deleted": True})
        return _json({"error": "not found"}, status=404)

    return _json({"error": "not found"}, status=404)
