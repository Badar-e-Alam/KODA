from pagination import paginate, total_pages


def test_first_page():
    items = list(range(25))
    assert paginate(items, page=1, page_size=10) == list(range(0, 10))


def test_second_page():
    items = list(range(25))
    assert paginate(items, page=2, page_size=10) == list(range(10, 20))


def test_last_partial_page():
    items = list(range(25))
    assert paginate(items, page=3, page_size=10) == list(range(20, 25))


def test_page_beyond_end():
    items = list(range(25))
    assert paginate(items, page=4, page_size=10) == []


def test_total_pages():
    assert total_pages(list(range(25)), 10) == 3
    assert total_pages([], 10) == 0
    assert total_pages(list(range(10)), 10) == 1
