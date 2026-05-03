import pytest
from chunks import chunk


def test_even_split():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_uneven_split():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_size_larger_than_list():
    assert chunk([1, 2, 3], 5) == [[1, 2, 3]]


def test_empty_list():
    assert chunk([], 3) == []


def test_size_one():
    assert chunk([1, 2, 3], 1) == [[1], [2], [3]]


def test_zero_size_raises():
    with pytest.raises(ValueError):
        chunk([1, 2, 3], 0)


def test_negative_size_raises():
    with pytest.raises(ValueError):
        chunk([1, 2, 3], -1)


def test_does_not_mutate_input():
    src = [1, 2, 3, 4]
    chunk(src, 2)
    assert src == [1, 2, 3, 4]


def test_strings():
    assert chunk(["a", "b", "c"], 2) == [["a", "b"], ["c"]]
