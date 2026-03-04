with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

old_import = "from mcp.sovereign_mcp_server import query_memory"
new_import = """import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from mcp.sovereign_mcp_server import query_memory"""

content = content.replace(old_import, new_import)

with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
