# Solution

```python
def chunk(items, size):
    if size <= 0:
        raise ValueError("size must be positive")
    return [list(items[i:i + size]) for i in range(0, len(items), size)]
```
