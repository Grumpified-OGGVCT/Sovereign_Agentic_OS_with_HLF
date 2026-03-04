with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

# Ah! `query_memory` calls `conn.close()` in its `finally` block!
# So the mock connection gets closed by the tested function.
# I need to test via reopening it, or avoid the mock closing it. Since it's a test db I can just reconnect to tmp_path / "mem.db".

old_check = """            # Verify the DB row has an updated last_accessed timestamp
            row = conn.execute("SELECT last_accessed FROM fact_store WHERE entity_id = 'test_query'").fetchone()"""

new_check = """            # Verify the DB row has an updated last_accessed timestamp
            import sqlite3
            conn2 = sqlite3.connect(str(tmp_path / "mem.db"))
            row = conn2.execute("SELECT last_accessed FROM fact_store WHERE entity_id = 'test_query'").fetchone()
            conn2.close()"""

content = content.replace(old_check, new_check)
with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
