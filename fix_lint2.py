with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

old_1 = """        spec = importlib.util.spec_from_file_location("sovereign_mcp_server", str(Path(__file__).parent.parent / "mcp" / "sovereign_mcp_server.py"))"""
new_1 = """        mcp_path = Path(__file__).parent.parent / "mcp" / "sovereign_mcp_server.py"
        spec = importlib.util.spec_from_file_location("sovereign_mcp_server", str(mcp_path))"""

old_2 = """        # Manually alter table since _make_conn might not have our schema modifications if we ran the patching slightly incorrectly"""
new_2 = """        # Manually alter table to ensure it has our schema modifications"""

content = content.replace(old_1, new_1)
content = content.replace(old_2, new_2)

with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
