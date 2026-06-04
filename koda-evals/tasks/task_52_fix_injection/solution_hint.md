# Solution
Use parameterized query: `conn.execute("SELECT * FROM users WHERE name = ?", (name,))`.
