import pytest
from math_utils import add


def test_add_integers():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_add_floats():
    assert add(1.5, 2.5) == 4.0
    assert add(-0.1, 0.1) == pytest.approx(0.0)


def test_add_mixed_types():
    assert add(1, 2.5) == 3.5
    assert add(2.0, 3) == 5.0


def test_add_negative_numbers():
    assert add(-5, -3) == -8
    assert add(-10, 5) == -5
