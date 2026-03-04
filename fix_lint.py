with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

# Fix SIM105
old_try = """        try:
            conn.execute("ALTER TABLE fact_store ADD COLUMN created_at REAL NOT NULL DEFAULT 0.0")
        except: pass
        try:
            conn.execute("ALTER TABLE fact_store ADD COLUMN last_accessed REAL NOT NULL DEFAULT 0.0")
        except: pass"""

new_try = """        import contextlib
        with contextlib.suppress(Exception):
            conn.execute("ALTER TABLE fact_store ADD COLUMN created_at REAL NOT NULL DEFAULT 0.0")
        with contextlib.suppress(Exception):
            conn.execute("ALTER TABLE fact_store ADD COLUMN last_accessed REAL NOT NULL DEFAULT 0.0")"""

content = content.replace(old_try, new_try)

with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
