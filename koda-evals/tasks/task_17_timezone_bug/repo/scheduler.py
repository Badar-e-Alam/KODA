from datetime import datetime, timezone

def is_due_today(due_date):
    """Return True if due_date is today (UTC)."""
    now = datetime.now()
    return due_date.date() == now.date()
