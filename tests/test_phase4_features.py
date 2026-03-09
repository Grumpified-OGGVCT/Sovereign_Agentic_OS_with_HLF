"""
Tests for Phase 4 aspirational features:
  1. Complexity-based routing in route_request()
  2. Model allowlist enforcement (is_model_allowed)
  3. OllamaDispatcher round-robin host ordering
  4. Janus subprocess worker protocol
"""

import json
import unittest
from unittest.mock import patch

from agents.gateway.ollama_dispatch import OllamaDispatcher
from agents.gateway.router import is_model_allowed, route_request


class TestComplexityRouting(unittest.TestCase):
    """Complexity score should short-circuit model selection."""

    @patch("agents.gateway.router._try_import_db", return_value=None)
    def test_low_complexity_selects_slm(self, _mock_db):
        """complexity < 0.3 should select the summarization (SLM) model."""
        profile = route_request("simple hello", {}, complexity=0.1)
        # Should short-circuit to SLM
        assert any(
            t.get("step") == "complexity_shortcircuit" and t.get("target") == "slm" for t in profile.routing_trace
        ), f"Expected complexity_shortcircuit/slm in trace, got: {profile.routing_trace}"

    @patch("agents.gateway.router._try_import_db", return_value=None)
    def test_high_complexity_selects_frontier(self, _mock_db):
        """complexity > 0.7 should select the primary (frontier) model."""
        profile = route_request("complex multi-step reasoning", {}, complexity=0.85)
        assert any(
            t.get("step") == "complexity_shortcircuit" and t.get("target") == "frontier" for t in profile.routing_trace
        ), f"Expected complexity_shortcircuit/frontier in trace, got: {profile.routing_trace}"

    @patch("agents.gateway.router._try_import_db", return_value=None)
    def test_mid_complexity_proceeds_to_tier_walk(self, _mock_db):
        """0.3–0.7 complexity should proceed with normal routing (no shortcircuit)."""
        profile = route_request("medium difficulty query", {}, complexity=0.5)
        assert any(t.get("step") == "complexity_midrange" for t in profile.routing_trace), (
            f"Expected complexity_midrange in trace, got: {profile.routing_trace}"
        )

    @patch("agents.gateway.router._try_import_db", return_value=None)
    def test_negative_complexity_skips_shortcircuit(self, _mock_db):
        """Default complexity=-1.0 should skip Phase 0 entirely."""
        profile = route_request("anything", {})
        assert not any(t.get("step", "").startswith("complexity") for t in profile.routing_trace), (
            f"Unexpected complexity step in trace: {profile.routing_trace}"
        )


class TestModelAllowlist(unittest.TestCase):
    """Model allowlist enforcement per deployment tier."""

    @patch("agents.gateway.router._load_allowed_models")
    def test_allowed_model_passes(self, mock_load):
        """Ollama cloud format 'qwen:7b' should match allowlist entry 'qwen-7b'."""
        mock_load.return_value = {"qwen-7b", "glm-5"}
        assert is_model_allowed("qwen:7b", "hearth") is True

    @patch("agents.gateway.router._load_allowed_models")
    def test_cloud_model_variant_matches(self, mock_load):
        """Cloud suffix models like 'kimi-k2.5:cloud' should match 'kimi-k2.5'."""
        mock_load.return_value = {"kimi-k2.5", "qwen-7b"}
        assert is_model_allowed("kimi-k2.5:cloud", "sovereign") is True

    @patch("agents.gateway.router._load_allowed_models")
    def test_disallowed_model_blocked(self, mock_load):
        """Model not in allowlist should be blocked."""
        mock_load.return_value = {"qwen-7b"}
        assert is_model_allowed("deepseek-v3", "hearth") is False

    @patch("agents.gateway.router._load_allowed_models")
    def test_cloud_suffix_stripped(self, mock_load):
        mock_load.return_value = {"qwen-7b"}
        assert is_model_allowed("qwen-7b:cloud", "hearth") is True
        assert is_model_allowed("qwen-7b-cloud", "hearth") is True

    @patch("agents.gateway.router._load_allowed_models")
    def test_empty_allowlist_failopen(self, mock_load):
        mock_load.return_value = set()
        assert is_model_allowed("any-model", "hearth") is True


class TestRoundRobinDispatcher(unittest.TestCase):
    """OllamaDispatcher round-robin host ordering."""

    def test_round_robin_alternates(self):
        d = OllamaDispatcher(
            ollama_host="http://primary:11434",
            ollama_secondary="http://secondary:11435",
            strategy="round_robin",
        )
        # First call: counter becomes 1 (odd) → secondary first
        hosts1 = d._get_ordered_hosts()
        assert hosts1 == ["http://secondary:11435", "http://primary:11434"]

        # Second call: counter becomes 2 (even) → primary first
        hosts2 = d._get_ordered_hosts()
        assert hosts2 == ["http://primary:11434", "http://secondary:11435"]

        # Third call: alternates again
        hosts3 = d._get_ordered_hosts()
        assert hosts3 == ["http://secondary:11435", "http://primary:11434"]

    def test_failover_always_primary_first(self):
        d = OllamaDispatcher(
            ollama_host="http://primary:11434",
            ollama_secondary="http://secondary:11435",
            strategy="failover",
        )
        for _ in range(5):
            hosts = d._get_ordered_hosts()
            assert hosts[0] == "http://primary:11434"

    def test_primary_only_excludes_secondary(self):
        d = OllamaDispatcher(
            ollama_host="http://primary:11434",
            ollama_secondary="http://secondary:11435",
            strategy="primary_only",
        )
        hosts = d._get_ordered_hosts()
        assert hosts == ["http://primary:11434"]

    def test_no_secondary_returns_primary_only(self):
        d = OllamaDispatcher(
            ollama_host="http://primary:11434",
            ollama_secondary="",
            strategy="round_robin",
        )
        hosts = d._get_ordered_hosts()
        assert hosts == ["http://primary:11434"]


class TestJanusWorkerProtocol(unittest.TestCase):
    """Janus subprocess worker JSON protocol."""

    def test_worker_module_exists(self):
        """janus_worker.py should be importable as module-level validation."""
        from pathlib import Path

        worker = Path(__file__).parent.parent / "mcp" / "janus_worker.py"
        assert worker.exists(), f"janus_worker.py not found at {worker}"

    def test_worker_handles_unknown_command(self):
        """Worker should return error for unknown commands."""
        import subprocess
        import sys
        from pathlib import Path

        worker = Path(__file__).parent.parent / "mcp" / "janus_worker.py"

        proc = subprocess.Popen(
            [sys.executable, str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write(json.dumps({"cmd": "bogus_command"}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
        proc.stdin.flush()
        proc.wait(timeout=5)

        response = json.loads(line)
        assert response["status"] == "error"
        assert "Unknown command" in response["error"]

    def test_worker_handles_invalid_json(self):
        """Worker should return error for malformed input."""
        import subprocess
        import sys
        from pathlib import Path

        worker = Path(__file__).parent.parent / "mcp" / "janus_worker.py"

        proc = subprocess.Popen(
            [sys.executable, str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write("not json at all\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
        proc.stdin.flush()
        proc.wait(timeout=5)

        response = json.loads(line)
        assert response["status"] == "error"
        assert "Invalid JSON" in response["error"]
