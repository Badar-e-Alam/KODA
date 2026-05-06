from fib import fib

def test_base():
    assert fib(0) == 0
    assert fib(1) == 1

def test_small():
    assert fib(10) == 55

def test_large():
    assert fib(100) == 354224848179261915075
