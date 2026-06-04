# Solution
Add a module-level dict cache and Lock. Check cache before calling `_slow_lookup`, store result, evict oldest if >100 entries (or use `collections.OrderedDict` / simple dict with tracking).
