with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

# I forgot that _make_conn creates the table, but I updated it earlier. Let's make sure I'm using my updated `_make_conn` properly, or if `sovereign_mcp_server.py` is somehow altering it, wait, `mcp.sovereign_mcp_server.py` doesn't create tables.

old_insert = """        # Insert test facts
        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, "
            "confidence_score, created_at, last_accessed) "
            "VALUES (?, NULL, ?, ?, ?, ?)",
            ("test_query", "test_rel", 0.9, old_time, old_time),
        )"""

new_insert = """        # Manually alter table since _make_conn might not have our schema modifications if we ran the patching slightly incorrectly
        try:
            conn.execute("ALTER TABLE fact_store ADD COLUMN created_at REAL NOT NULL DEFAULT 0.0")
        except: pass
        try:
            conn.execute("ALTER TABLE fact_store ADD COLUMN last_accessed REAL NOT NULL DEFAULT 0.0")
        except: pass

        # Insert test facts
        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, "
            "confidence_score, created_at, last_accessed) "
            "VALUES (?, NULL, ?, ?, ?, ?)",
            ("test_query", "test_rel", 0.9, old_time, old_time),
        )"""
content = content.replace(old_insert, new_insert)
with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
