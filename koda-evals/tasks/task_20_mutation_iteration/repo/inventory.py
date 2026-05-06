from datetime import datetime

def remove_expired(items):
    """Remove expired items from list. Each item is a dict with 'expiry' datetime."""
    now = datetime.now()
    for item in items:
        if item["expiry"] < now:
            items.remove(item)
    return items
