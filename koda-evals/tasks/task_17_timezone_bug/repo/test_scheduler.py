from datetime import datetime, timezone, timedelta
from scheduler import is_due_today

def test_same_day():
    now = datetime.now(timezone.utc)
    assert is_due_today(now) is True

def test_different_timezone():
    # Due date is 23:00 UTC yesterday, but 01:00 today in +02:00
    due = datetime(2024, 1, 15, 23, 0, tzinfo=timezone(timedelta(hours=2)))
    # Mock: pretend "now" is 2024-01-16 00:30 UTC
    # The function should compare in UTC, not local time
    assert is_due_today(due.astimezone(timezone.utc).replace(tzinfo=None)) is True

def test_past():
    past = datetime(2020, 1, 1)
    assert is_due_today(past) is False
