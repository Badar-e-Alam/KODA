from datetime import datetime, timedelta
from inventory import remove_expired

def test_no_expired():
    items = [{"name": "a", "expiry": datetime(2099, 1, 1)}]
    assert remove_expired(items) == items

def test_all_expired():
    items = [
        {"name": "a", "expiry": datetime(2020, 1, 1)},
        {"name": "b", "expiry": datetime(2020, 1, 2)},
    ]
    assert remove_expired(items) == []

def test_mixed():
    now = datetime.now()
    items = [
        {"name": "a", "expiry": now - timedelta(days=1)},
        {"name": "b", "expiry": now + timedelta(days=1)},
        {"name": "c", "expiry": now - timedelta(days=2)},
    ]
    result = remove_expired(items)
    assert [i["name"] for i in result] == ["b"]

def test_consecutive_expired():
    now = datetime.now()
    items = [
        {"name": "a", "expiry": now - timedelta(days=1)},
        {"name": "b", "expiry": now - timedelta(days=2)},
        {"name": "c", "expiry": now + timedelta(days=1)},
    ]
    result = remove_expired(items)
    assert [i["name"] for i in result] == ["c"]
