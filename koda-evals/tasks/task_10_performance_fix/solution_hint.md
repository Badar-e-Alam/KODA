# Solution

Use a Counter or a single-pass set-tracking approach:

```python
from collections import Counter

def find_duplicates(items):
    counts = Counter(items)
    return {x for x, c in counts.items() if c > 1}
```

Or, for one pass without Counter:

```python
def find_duplicates(items):
    seen, dups = set(), set()
    for x in items:
        if x in seen:
            dups.add(x)
        else:
            seen.add(x)
    return dups
```

Both are O(n) average time.
