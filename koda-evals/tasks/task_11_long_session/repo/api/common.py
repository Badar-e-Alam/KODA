"""Shared utilities for the API modules."""
class _Stub:
    def __getattr__(self, k): return self
    def __call__(self, *a, **kw): return self
router = _Stub()
def current_user(): return _Stub()
def persistence_for(name): return _Stub()
