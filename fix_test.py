with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

old_insert = """        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, confidence_score, created_at, last_accessed) "
            "VALUES (?, NULL, ?, ?, ?, ?)",
            ("test_query", "test_rel", 0.9, old_time, old_time),
        )"""

new_insert = """        conn.execute(
            "INSERT INTO fact_store (entity_id, vector_embedding, semantic_relationship, "
            "confidence_score, created_at, last_accessed) "
            "VALUES (?, NULL, ?, ?, ?, ?)",
            ("test_query", "test_rel", 0.9, old_time, old_time),
        )"""

content = content.replace(old_insert, new_insert)
with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
