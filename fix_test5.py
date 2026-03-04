with open("tests/test_dream_state.py", "r") as f:
    content = f.read()

old_import = """        import sys
        mcp_path = Path(__file__).parent.parent / "mcp"
        sys.path.insert(0, str(mcp_path.parent))
        from mcp.sovereign_mcp_server import query_memory"""

# Okay wait, if it's hitting a runtime error because of mcp version it's actually loading it but the pip installed version of `mcp` doesn't support `version`.
# Wait, `ModuleNotFoundError: No module named 'mcp.sovereign_mcp_server'`
# `mcp` is a PyPI package (which provides FastMCP). If our folder is also named `mcp/`, then `from mcp.sovereign_mcp_server import query_memory` will look inside the PyPI `mcp` package and fail!

new_import = """        import importlib.util
        import sys
        import mcp

        # We must load it directly without using the 'mcp' namespace prefix because it shadows the pypi package
        spec = importlib.util.spec_from_file_location("sovereign_mcp_server", str(Path(__file__).parent.parent / "mcp" / "sovereign_mcp_server.py"))
        mcp_module = importlib.util.module_from_spec(spec)

        # Patch mcp_module FastMCP to not crash on version kwarg
        import mcp.server.fastmcp
        old_init = mcp.server.fastmcp.FastMCP.__init__
        def new_init(self, *args, **kwargs):
            kwargs.pop("version", None)
            kwargs.pop("description", None)
            old_init(self, *args, **kwargs)
        mcp.server.fastmcp.FastMCP.__init__ = new_init

        sys.modules["sovereign_mcp_server"] = mcp_module
        spec.loader.exec_module(mcp_module)
        query_memory = mcp_module.query_memory"""

content = content.replace(old_import, new_import)

with open("tests/test_dream_state.py", "w") as f:
    f.write(content)
