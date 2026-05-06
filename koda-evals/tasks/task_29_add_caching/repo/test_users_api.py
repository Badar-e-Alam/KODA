import time
from users_api import fetch_user

def test_returns_user():
    u = fetch_user(1)
    assert u["id"] == 1

def test_caching():
    start = time.time()
    fetch_user(42)
    fetch_user(42)
    fetch_user(42)
    elapsed = time.time() - start
    assert elapsed < 0.03  # should be ~0.01s, not 0.03s

def test_different_users():
    assert fetch_user(1)["name"] == "User 1"
    assert fetch_user(2)["name"] == "User 2"
