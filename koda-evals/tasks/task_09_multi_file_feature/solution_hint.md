# Solution

In `models.py`, add:
```python
def mark_completed(self):
    self.completed = True
```
to the `Todo` class.

In `app.py`, add a route handler before the fallthrough:
```python
m = re.match(r"^/todos/(\d+)/complete$", path)
if m and method == "POST":
    todo = store.get(int(m.group(1)))
    if not todo:
        return _json({"error": "not found"}, status=404)
    todo.mark_completed()
    return _json({"id": todo.id, "completed": todo.completed})
```
Note: must come BEFORE the `^/todos/(\d+)$` regex match.
