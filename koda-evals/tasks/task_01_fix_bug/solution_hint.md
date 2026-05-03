# Solution

The bug is in `paginate()`:

```python
start = page * page_size      # wrong
start = (page - 1) * page_size  # correct
```

Page 1 should map to `items[0:page_size]`, but `1 * page_size = page_size`
skips the first page entirely.
