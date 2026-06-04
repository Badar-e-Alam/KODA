import time

def _slow_lookup(user_id):
    time.sleep(0.01)  # simulate network
    return {"id": user_id, "name": f"User {user_id}"}

def fetch_user(user_id):
    """Fetch user — currently always slow."""
    return _slow_lookup(user_id)
