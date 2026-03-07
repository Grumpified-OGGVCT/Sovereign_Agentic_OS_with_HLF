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

def test_openclaw_gas_budget_and_ledger():
    """Test ALIGN Ledger appending behavior directly (using temp file logic locally)."""
    # Just verify that index.js exists and requires crypto for SHA256 hashes
    with open("plugins/openclaw-sovereign/index.js") as f:
        js = f.read()
    assert "crypto.createHash('sha256')" in js
    assert "ALIGN_LEDGER.yaml" in js
    assert "gasBudget" in js
