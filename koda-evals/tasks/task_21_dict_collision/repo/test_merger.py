from merger import merge_records

def test_no_collision():
    assert merge_records([{"a": 1}, {"b": 2}]) == {"a": 1, "b": 2}

def test_collision():
    assert merge_records([{"a": 1}, {"a": 2}]) == {"a": [1, 2]}

def test_mixed():
    assert merge_records([{"a": 1}, {"a": 2}, {"b": 3}, {"a": 4}]) == {"a": [1, 2, 4], "b": 3}

def test_single():
    assert merge_records([{"a": 1}]) == {"a": 1}

def test_empty():
    assert merge_records([]) == {}
