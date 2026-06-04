import sqlite3

def find_user(name):
    """Find user by name — VULNERABLE to SQL injection."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'Alice')")
    conn.execute("INSERT INTO users VALUES (2, 'Bob')")
    # Vulnerable:
    cursor = conn.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cursor.fetchall()
