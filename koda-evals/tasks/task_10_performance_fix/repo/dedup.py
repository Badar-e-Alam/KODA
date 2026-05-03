"""Find duplicate items in a list."""


def find_duplicates(items):
    """Return a set of items that appear more than once in `items`.

    Examples:
        find_duplicates([1, 2, 3, 2, 1]) -> {1, 2}
        find_duplicates([1, 2, 3])       -> set()
    """
    # Current implementation is O(n^2) — needs to be rewritten to O(n).
    dups = set()
    for i, a in enumerate(items):
        for j, b in enumerate(items):
            if i != j and a == b:
                dups.add(a)
    return dups
