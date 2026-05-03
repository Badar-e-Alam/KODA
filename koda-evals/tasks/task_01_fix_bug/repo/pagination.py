"""Simple pagination utility."""


def paginate(items, page, page_size):
    """Return the slice of `items` for the given 1-indexed page.

    page=1, page_size=10 -> items[0:10]
    page=2, page_size=10 -> items[10:20]
    """
    # BUG: off-by-one — `page` is 1-indexed but used as if 0-indexed
    start = page * page_size
    end = start + page_size
    return items[start:end]


def total_pages(items, page_size):
    """Return total number of pages needed for `items`."""
    if not items:
        return 0
    return (len(items) + page_size - 1) // page_size
