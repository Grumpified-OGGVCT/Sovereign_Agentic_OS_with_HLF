## Auto-generated Tasks from Jules

*   [ ] **Refactor Project Janus Integration**: The current integration of `Project_Janus` directly into `mcp/sovereign_mcp_server.py` mixes heavy ML dependencies and blocks the async event loop during initialization. It should be refactored to launch `project_janus/src/mcp_server/server.py` as a completely independent subprocess, managed by `gui/tray_manager.py`, with its own isolated virtual environment. This prevents bloating the core OS requirements and maintains ACFS confinement principles. See the 11-Hat review logs for details.
