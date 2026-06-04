# Posts API spec

A tiny Flask service exposing a CRUD-ish API for blog posts. Storage is
in-memory (a module-level dict is fine — there is no persistence
requirement for this exercise).

## Module layout

The application lives in `app.py` and exposes a Flask `app` instance at
module scope (so tests can do `from app import app`).

## Posts model

Each post is a dict with keys:

| field   | type | notes                                              |
|---------|------|----------------------------------------------------|
| `id`    | int  | server-assigned, monotonically increasing from 1   |
| `title` | str  | non-empty                                          |
| `body`  | str  | may be empty                                       |
| `tags`  | list | list of strings; default `[]` if omitted on create |

## Endpoints

### `POST /posts`
Create a post. JSON body: `{title, body, tags?}`.

* **400** if `title` is missing or empty.
* **201** with the full post (including server-assigned `id`).

### `GET /posts`
List all posts. JSON body: `{posts: [...]}`. Empty list when none exist.
Status 200 always.

Optional query param `tag=<name>`: when present, filter posts to those
whose `tags` contains `<name>` (exact match).

### `GET /posts/<id>`
Fetch one. **404** with `{"error": "not found"}` if missing.

### `DELETE /posts/<id>`
Remove one. **404** with `{"error": "not found"}` if missing. **204**
(no body) on successful delete.

### `GET /healthz`
Returns `{"ok": true}` with status 200.

## Notes

* All JSON responses use `Content-Type: application/json`.
* IDs never get reused even after delete (so `next_id` is monotonic).
* No auth, no rate limiting, no persistence.
