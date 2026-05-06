from query import find_user

def test_normal():
    assert find_user("Alice") == [(1, "Alice")]

def test_injection_blocked():
    # This should NOT return all rows
    result = find_user("' OR '1'='1")
    assert len(result) == 0  # no user with that literal name
