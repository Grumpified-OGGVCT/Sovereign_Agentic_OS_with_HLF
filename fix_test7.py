with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

old_patch = """        # Mock the `_get_db` function inside mcp to return our test connection
        with patch("mcp.sovereign_mcp_server._get_db", return_value=conn):"""

new_patch = """        # Mock the `_get_db` function inside mcp to return our test connection
        with patch("sovereign_mcp_server._get_db", return_value=conn):"""

content = content.replace(old_patch, new_patch)
with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
