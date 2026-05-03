# Solution

```python
def peek_n(self, n):
    if n <= 0:
        return []
    return list(reversed(self._items[-n:]))
```
