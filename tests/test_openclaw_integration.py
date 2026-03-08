import json
import os
from unittest.mock import MagicMock, patch

from hlf.hlfrun import HLFInterpreter


def test_openclaw_opcode_dispatch():
    """Verify that the OPENCLAW_TOOL opcode executes and populates trace and scope."""
    rt = HLFInterpreter(tier="hearth")
    node = {
        "tool": "openclaw_test_tool",
        "args": ["arg1", "arg2"]
    }

    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "tool": "openclaw_test_tool", "data": "real"}
        mock_post.return_value = mock_resp

        result = rt._exec_openclaw_tool(node)

        assert result["status"] == "success"
        assert result["tool"] == "openclaw_test_tool"
        assert result["data"] == "real"
        assert rt.scope["openclaw_test_tool_RESULT"] == result

        assert len(rt._trace) == 1
        assert rt._trace[0]["tag"] == "OPENCLAW_TOOL"
        assert rt._trace[0]["tool"] == "openclaw_test_tool"


def test_openclaw_dispatch_reachable():
    """Verify that _execute_node routes OPENCLAW_TOOL tag to _exec_openclaw_tool."""
    rt = HLFInterpreter(tier="hearth", max_gas=10)
    node = {
        "tag": "OPENCLAW_TOOL",
        "tool": "test_reachable",
        "args": ["x"],
    }

    with patch("httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "tool": "test_reachable"}
        mock_post.return_value = mock_resp

        result = rt._execute_node(node)

        assert result["status"] == "success"
        assert rt.scope["test_reachable_RESULT"] == result


def test_openclaw_error_not_masked():
    """Verify that connection errors return error status, not fake success."""
    import httpx

    rt = HLFInterpreter(tier="hearth")
    node = {
        "tool": "failing_tool",
        "args": [],
    }

    with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")):
        result = rt._exec_openclaw_tool(node)

        assert result["status"] == "error"
        assert "Connection refused" in result["error"]


def test_config_openclaw_exists():
    """Verify the hardened config baseline exists and has correct deny list."""
    assert os.path.exists("config/openclaw/openclaw.json")
    with open("config/openclaw/openclaw.json") as f:
        config = json.load(f)
    assert "group:fs" in config["tools"]["deny"]
    assert config["sandbox"]["workspaceAccess"] == "ro"


def test_plugin_scaffold_exists():
    """Verify the orchestration plugin scaffold exists."""
    assert os.path.exists("plugins/openclaw-sovereign/package.json")
    assert os.path.exists("plugins/openclaw-sovereign/index.js")
    with open("plugins/openclaw-sovereign/package.json") as f:
        pkg = json.load(f)
    assert "js-yaml" in pkg["dependencies"]


def test_openclaw_audit_log_and_crypto():
    """Verify that the OpenClaw plugin uses cryptographic hashing and proper audit log target."""
    with open("plugins/openclaw-sovereign/index.js") as f:
        js = f.read()
    assert "crypto.createHash('sha256')" in js
    assert "openclaw_audit.log" in js
    assert "toolGasCost" in js
    # ALIGN_LEDGER.yaml must NOT be written to by the plugin
    assert "ALIGN_LEDGER" not in js


def test_openclaw_audit_log_directory_exists():
    """Verify the observability directory exists for audit log output."""
    assert os.path.isdir("observability")
    assert os.path.exists("observability/.gitkeep")
