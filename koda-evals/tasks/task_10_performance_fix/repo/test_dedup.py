import time
import random
from dedup import find_duplicates


def test_basic():
    assert find_duplicates([1, 2, 3, 2, 1]) == {1, 2}


def test_no_duplicates():
    assert find_duplicates([1, 2, 3, 4, 5]) == set()


def test_empty():
    assert find_duplicates([]) == set()


def test_all_same():
    assert find_duplicates([7, 7, 7, 7]) == {7}


def test_strings():
    assert find_duplicates(["a", "b", "a", "c", "b", "d"]) == {"a", "b"}


def test_returns_set():
    result = find_duplicates([1, 1, 2, 2])
    assert isinstance(result, set)


def test_single_element():
    assert find_duplicates([42]) == set()


def test_performance_100k():
    """Stress test: 100k items must complete in under 2 seconds."""
    random.seed(0)
    items = [random.randint(0, 50_000) for _ in range(100_000)]

    start = time.perf_counter()
    result = find_duplicates(items)
    elapsed = time.perf_counter() - start

    # Sanity: with 100k items in a 50k range, there must be lots of dups
    assert len(result) > 1000
    assert elapsed < 2.0, f"too slow: took {elapsed:.2f}s (limit 2.0s)"
