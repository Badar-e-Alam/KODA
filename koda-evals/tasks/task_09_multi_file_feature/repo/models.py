"""In-memory todo model."""


class Todo:
    def __init__(self, id: int, title: str):
        self.id = id
        self.title = title
        self.completed = False

    def to_dict(self):
        return {"id": self.id, "title": self.title, "completed": self.completed}


class TodoStore:
    def __init__(self):
        self._items = {}
        self._next_id = 1

    def create(self, title: str) -> Todo:
        todo = Todo(self._next_id, title)
        self._items[self._next_id] = todo
        self._next_id += 1
        return todo

    def get(self, id: int):
        return self._items.get(id)

    def all(self):
        return list(self._items.values())

    def delete(self, id: int) -> bool:
        return self._items.pop(id, None) is not None
