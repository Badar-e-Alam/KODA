def chunk(items, size):
    """Split a list into consecutive sublists of length `size`.

    The last chunk may be shorter than `size` if `len(items)` is not a multiple
    of `size`.

    Args:
        items: a list (or any sequence) to be chunked.
        size: positive integer, the chunk size.

    Returns:
        A list of lists.

    Raises:
        ValueError: if size <= 0.

    Examples:
        chunk([1, 2, 3, 4, 5], 2)  -> [[1, 2], [3, 4], [5]]
        chunk([], 3)               -> []
        chunk([1, 2, 3], 5)        -> [[1, 2, 3]]
        chunk([1, 2, 3, 4], 2)     -> [[1, 2], [3, 4]]
    """
    raise NotImplementedError
