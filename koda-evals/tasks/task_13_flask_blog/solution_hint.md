# task_13_flask_blog — solution hint

A correct `app.py` looks roughly like this:

```python
from flask import Flask, jsonify, request, abort

app = Flask(__name__)

_posts: dict[int, dict] = {}
_next_id = 1


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.post("/posts")
def create_post():
    global _next_id
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    post = {
        "id": _next_id,
        "title": title,
        "body": body.get("body", ""),
        "tags": list(body.get("tags") or []),
    }
    _posts[_next_id] = post
    _next_id += 1
    return jsonify(post), 201


@app.get("/posts")
def list_posts():
    tag = request.args.get("tag")
    posts = list(_posts.values())
    if tag is not None:
        posts = [p for p in posts if tag in p["tags"]]
    return jsonify({"posts": posts})


@app.get("/posts/<int:post_id>")
def get_post(post_id):
    post = _posts.get(post_id)
    if post is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(post)


@app.delete("/posts/<int:post_id>")
def delete_post(post_id):
    if _posts.pop(post_id, None) is None:
        return jsonify({"error": "not found"}), 404
    return "", 204
```

What this task probes:
- Reading TWO source files (spec + tests) to extract the contract.
- Writing a fresh module from scratch with `write_file`.
- Iterating against the test suite (`run_tests` or `run_shell pytest`).
- Edge cases (empty title, monotonic IDs after delete, 204 with empty body).
