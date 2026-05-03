import pytest
from stack import Stack


def test_push_pop():
    s = Stack()
    s.push(1); s.push(2)
    assert s.pop() == 2
    assert s.pop() == 1


def test_peek():
    s = Stack()
    s.push(42)
    assert s.peek() == 42
    assert len(s) == 1


def test_empty():
    s = Stack()
    assert s.is_empty()
    with pytest.raises(IndexError):
        s.pop()


def test_peek_n_basic():
    s = Stack()
    for x in [1, 2, 3]:
        s.push(x)
    assert s.peek_n(2) == [3, 2]


def test_peek_n_zero():
    s = Stack()
    s.push(1)
    assert s.peek_n(0) == []


def test_peek_n_more_than_size():
    s = Stack()
    for x in [1, 2, 3]:
        s.push(x)
    assert s.peek_n(10) == [3, 2, 1]


def test_peek_n_does_not_mutate():
    s = Stack()
    for x in [1, 2, 3]:
        s.push(x)
    s.peek_n(2)
    assert len(s) == 3


def test_peek_n_empty_stack():
    s = Stack()
    assert s.peek_n(5) == []
