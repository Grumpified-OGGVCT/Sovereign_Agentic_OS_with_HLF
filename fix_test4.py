with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

old_import = """        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location("sovereign_mcp_server", str(Path(__file__).parent.parent / "mcp" / "sovereign_mcp_server.py"))
        mcp_module = importlib.util.module_from_spec(spec)
        sys.modules["sovereign_mcp_server"] = mcp_module
        spec.loader.exec_module(mcp_module)
        query_memory = mcp_module.query_memory"""

new_import = """        import sys
        mcp_path = Path(__file__).parent.parent / "mcp"
        sys.path.insert(0, str(mcp_path.parent))
        from mcp.sovereign_mcp_server import query_memory"""

content = content.replace(old_import, new_import)

with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
