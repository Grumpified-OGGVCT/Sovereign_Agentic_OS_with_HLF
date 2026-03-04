with open("mcp/sovereign_mcp_server.py", "r") as f:
    content = f.read()

old_query = """        rows = conn.execute(
            "SELECT entity_id, semantic_relationship, confidence_score "
            "FROM fact_store "
            "WHERE entity_id LIKE ? OR semantic_relationship LIKE ? "
            "ORDER BY confidence_score DESC LIMIT ?",
            (f"%{search_term}%", f"%{search_term}%", limit),
        ).fetchall()
        return [
            {
                "entity_id": r[0],
                "relationship": r[1],
                "confidence": r[2],
            }
            for r in rows
        ]"""

new_query = """        rows = conn.execute(
            "SELECT rowid, entity_id, semantic_relationship, confidence_score "
            "FROM fact_store "
            "WHERE entity_id LIKE ? OR semantic_relationship LIKE ? "
            "ORDER BY confidence_score DESC LIMIT ?",
            (f"%{search_term}%", f"%{search_term}%", limit),
        ).fetchall()

        if rows:
            import time
            rowids = [r[0] for r in rows]
            placeholders = ",".join("?" * len(rowids))
            conn.execute(
                f"UPDATE fact_store SET last_accessed = ? WHERE rowid IN ({placeholders})",
                [time.time(), *rowids]
            )
            conn.commit()

        return [
            {
                "entity_id": r[1],
                "relationship": r[2],
                "confidence": r[3],
            }
            for r in rows
        ]"""

if old_query in content:
    content = content.replace(old_query, new_query)
    with open("mcp/sovereign_mcp_server.py", "w") as f:
        f.write(content)
else:
    print("Failed to replace query_memory")
