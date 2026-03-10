"""
Tests for AnythingLLM and MSTY Studio host function dispatch.

Covers:
  - Registry completeness: 9 new functions exist with correct fields
  - Tier enforcement: hearth tier rejected for forge/sovereign-only functions
  - AnythingLLM backend: mocked httpx calls, correct URL routing, auth headers
  - MSTY bridge: mocked httpx calls, Ollama-compatible API usage
  - Vibe CLI Proxy catalog: dynamic model classification, caching, family clusters
  - Unavailability fallback: structured error strings on connection failure
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.core.host_function_dispatcher import dispatch

# ─── Constants ───────────────────────────────────────────────────────────────

ALLM_FUNCTIONS = [
    "ALLM_WORKSPACE_CHAT",
    "ALLM_VECTOR_SEARCH",
    "ALLM_LIST_WORKSPACES",
    "ALLM_ADD_DOCUMENT",
]
MSTY_FUNCTIONS = [
    "MSTY_KNOWLEDGE_QUERY",
    "MSTY_PERSONA_RUN",
    "MSTY_LIST_MODELS",
    "MSTY_SPLIT_CHAT",
    "MSTY_VIBE_CATALOG",
]
ALL_NEW_FUNCTIONS = ALLM_FUNCTIONS + MSTY_FUNCTIONS


# ─── Registry Completeness ───────────────────────────────────────────────────


class TestRegistryCompleteness:
    """All 9 new functions exist in host_functions.json with correct fields."""

    def test_registry_contains_all_new_functions(self) -> None:
        """All ALLM_ and MSTY_ functions are registered."""
        registry_path = "governance/host_functions.json"
        with open(registry_path) as f:
            registry = json.load(f)

        names = [fn["name"] for fn in registry["functions"]]
        for fn_name in ALL_NEW_FUNCTIONS:
            assert fn_name in names, f"{fn_name} missing from host_functions.json"

    def test_registry_version_bumped(self) -> None:
        """Version should be at least 1.4.0."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        assert registry["version"] == "1.4.0"

    def test_all_new_functions_have_required_fields(self) -> None:
        """Each new function has name, args, returns, tier, gas, backend, sensitive."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)

        required_fields = {"name", "args", "returns", "tier", "gas", "backend", "sensitive"}
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        for fn_name in ALL_NEW_FUNCTIONS:
            fn = fn_map[fn_name]
            missing = required_fields - set(fn.keys())
            assert not missing, f"{fn_name} missing fields: {missing}"

    def test_anythingllm_uses_correct_backend(self) -> None:
        """All ALLM_ functions use the anythingllm_api backend."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        for fn_name in ALLM_FUNCTIONS:
            assert fn_map[fn_name]["backend"] == "anythingllm_api"

    def test_msty_uses_correct_backend(self) -> None:
        """All MSTY_ functions use the msty_bridge backend."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        for fn_name in MSTY_FUNCTIONS:
            assert fn_map[fn_name]["backend"] == "msty_bridge"

    def test_total_function_count(self) -> None:
        """Registry now has 28 functions (19 existing + 9 new)."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        # Flexible lower bound — at least 28 after this PR
        assert len(registry["functions"]) >= 28


# ─── Tier Enforcement ────────────────────────────────────────────────────────


class TestTierEnforcement:
    """Tier restrictions are enforced for new functions."""

    def test_hearth_can_list_workspaces(self) -> None:
        """ALLM_LIST_WORKSPACES allows hearth tier."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert "hearth" in fn_map["ALLM_LIST_WORKSPACES"]["tier"]

    def test_hearth_blocked_from_workspace_chat(self) -> None:
        """ALLM_WORKSPACE_CHAT rejects hearth tier."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert "hearth" not in fn_map["ALLM_WORKSPACE_CHAT"]["tier"]

    def test_split_chat_sovereign_only(self) -> None:
        """MSTY_SPLIT_CHAT is sovereign-only due to resource cost."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert fn_map["MSTY_SPLIT_CHAT"]["tier"] == ["sovereign"]

    def test_hearth_can_list_msty_models(self) -> None:
        """MSTY_LIST_MODELS allows hearth tier."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert "hearth" in fn_map["MSTY_LIST_MODELS"]["tier"]


# ─── AnythingLLM Backend ─────────────────────────────────────────────────────


class TestAnythingLLMBackend:
    """Test _anythingllm_api dispatch with mocked httpx."""

    @patch.dict("os.environ", {"ANYTHINGLLM_HOST": "http://test:3001", "ANYTHINGLLM_API_KEY": "test-key"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_list_workspaces_calls_correct_endpoint(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.text = '[{"slug":"my-workspace"}]'
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = dispatch("ALLM_LIST_WORKSPACES", [], tier="hearth")
        mock_httpx.get.assert_called_once()
        call_args = mock_httpx.get.call_args
        assert "/api/v1/workspaces" in call_args[0][0]
        assert "Bearer test-key" in call_args[1]["headers"]["Authorization"]

    @patch.dict("os.environ", {"ANYTHINGLLM_HOST": "http://test:3001", "ANYTHINGLLM_API_KEY": "test-key"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_workspace_chat_posts_with_mode(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"textResponse": "Hello!", "sources": []}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        result = dispatch("ALLM_WORKSPACE_CHAT", ["my-ws", "hi", "agent"], tier="forge")
        mock_httpx.post.assert_called_once()
        call_args = mock_httpx.post.call_args
        assert "/api/v1/workspace/my-ws/chat" in call_args[0][0]
        assert call_args[1]["json"]["mode"] == "agent"
        assert result == "Hello!"

    @patch.dict("os.environ", {"ANYTHINGLLM_HOST": "http://test:3001", "ANYTHINGLLM_API_KEY": "test-key"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_workspace_chat_returns_sources_when_present(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "textResponse": "Answer",
            "sources": [{"title": "doc.pdf", "text": "chunk"}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        result = dispatch("ALLM_WORKSPACE_CHAT", ["ws", "q", "chat"], tier="sovereign")
        parsed = json.loads(result)
        assert parsed["response"] == "Answer"
        assert len(parsed["sources"]) == 1

    @patch.dict("os.environ", {"ANYTHINGLLM_HOST": "http://test:3001", "ANYTHINGLLM_API_KEY": "key"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_vector_search_posts_query(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.text = '{"results":[{"text":"chunk"}]}'
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        result = dispatch("ALLM_VECTOR_SEARCH", ["my-ws", "search term"], tier="forge")
        call_args = mock_httpx.post.call_args
        assert "/api/v1/workspace/my-ws/vector-search" in call_args[0][0]

    @patch.dict("os.environ", {"ANYTHINGLLM_HOST": "http://test:3001", "ANYTHINGLLM_API_KEY": "key"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_add_document_returns_true(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        result = dispatch("ALLM_ADD_DOCUMENT", ["my-ws", "Doc Title", "content here"], tier="forge")
        assert result is True

    @patch.dict("os.environ", {"ANYTHINGLLM_HOST": "http://test:3001", "ANYTHINGLLM_API_KEY": "key"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_unavailable_returns_structured_error(self, mock_httpx: MagicMock) -> None:
        """Connection failure returns ALLM_UNAVAILABLE string."""
        import httpx as real_httpx
        mock_httpx.get.side_effect = real_httpx.RequestError("Connection refused")
        mock_httpx.RequestError = real_httpx.RequestError
        mock_httpx.HTTPStatusError = real_httpx.HTTPStatusError

        result = dispatch("ALLM_LIST_WORKSPACES", [], tier="hearth")
        assert "ALLM_UNAVAILABLE" in str(result)


# ─── MSTY Bridge Backend ────────────────────────────────────────────────────


class TestMSTYBridgeBackend:
    """Test _msty_bridge dispatch with mocked httpx."""

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_list_models_calls_api_tags(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.text = '{"models":[{"name":"qwen3:32b"}]}'
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = dispatch("MSTY_LIST_MODELS", [], tier="hearth")
        call_args = mock_httpx.get.call_args
        assert "/api/tags" in call_args[0][0]

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434", "MSTY_DEFAULT_MODEL": "test-model"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_knowledge_query_injects_stack_context(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Stack answer"}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        result = dispatch("MSTY_KNOWLEDGE_QUERY", ["my-stack", "what is X?"], tier="forge")
        call_args = mock_httpx.post.call_args
        body = call_args[1]["json"]
        assert "my-stack" in body["system"]
        assert body["model"] == "test-model"
        assert result == "Stack answer"

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_persona_run_sets_character_system_prompt(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "In character reply"}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        result = dispatch("MSTY_PERSONA_RUN", ["Architect", "design a system"], tier="forge")
        call_args = mock_httpx.post.call_args
        body = call_args[1]["json"]
        assert "Architect" in body["system"]
        assert "persona" in body["system"].lower()

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_split_chat_fans_out_to_multiple_models(self, mock_httpx: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "model reply"}
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        result = dispatch("MSTY_SPLIT_CHAT", [["model-a", "model-b"], "compare answers"], tier="sovereign")
        parsed = json.loads(result)
        assert "model-a" in parsed
        assert "model-b" in parsed
        # Should have made 2 POST calls (one per model)
        assert mock_httpx.post.call_count == 2

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_split_chat_no_models_returns_error(self, mock_httpx: MagicMock) -> None:
        result = dispatch("MSTY_SPLIT_CHAT", [[], "hello"], tier="sovereign")
        assert "MSTY_SPLIT_CHAT_ERROR" in str(result)

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_unavailable_returns_structured_error(self, mock_httpx: MagicMock) -> None:
        """Connection failure returns MSTY_UNAVAILABLE string."""
        import httpx as real_httpx
        mock_httpx.get.side_effect = real_httpx.RequestError("Connection refused")
        mock_httpx.RequestError = real_httpx.RequestError
        mock_httpx.HTTPStatusError = real_httpx.HTTPStatusError

        result = dispatch("MSTY_LIST_MODELS", [], tier="hearth")
        assert "MSTY_UNAVAILABLE" in str(result)


# ─── Gas Cost Verification ───────────────────────────────────────────────────


class TestGasCosts:
    """Verify gas costs match the operational weight expectations."""
    # Expansion note: When adding ALLM_AGENT_FLOW (Phase 2), gas cost
    # should be ~7 (between chat at 5 and forge_tool at 10) since agent
    # flows can chain multiple tool calls internally.

    def test_list_operations_are_cheap(self) -> None:
        """List operations should cost 1 gas."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert fn_map["ALLM_LIST_WORKSPACES"]["gas"] == 1
        assert fn_map["MSTY_LIST_MODELS"]["gas"] == 1

    def test_split_chat_is_expensive(self) -> None:
        """MSTY_SPLIT_CHAT cost=8, as it fans out to N models."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert fn_map["MSTY_SPLIT_CHAT"]["gas"] == 8

    def test_chat_operations_mid_cost(self) -> None:
        """Chat and generation functions cost 5."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert fn_map["ALLM_WORKSPACE_CHAT"]["gas"] == 5
        assert fn_map["MSTY_KNOWLEDGE_QUERY"]["gas"] == 5
        assert fn_map["MSTY_PERSONA_RUN"]["gas"] == 5


# ─── Vibe CLI Proxy Catalog ──────────────────────────────────────────────────


# Fake MSTY API response matching the structure seen in live probe (84 models).
# Cloud models include remote_model/remote_host as discovered from live API.
_FAKE_CATALOG = {
    "models": [
        # Cloud models — have remote_model + remote_host (primary cloud signal)
        {"name": "gemini-3-flash-preview:latest", "remote_model": "gemini-3-flash-preview", "remote_host": "https://ollama.com:443",
         "details": {"format": "", "family": "", "parameter_size": "", "quantization_level": ""}},
        {"name": "qwen3.5:397b-cloud", "remote_model": "qwen3.5:397b-cloud", "remote_host": "https://ollama.com:443",
         "details": {"format": "", "family": "", "parameter_size": "", "quantization_level": ""}},
        {"name": "deepseek-v3.2:cloud", "remote_model": "deepseek-v3.2:cloud", "remote_host": "https://ollama.com:443",
         "details": {"format": "", "family": "deepseek3.2", "parameter_size": "671B", "quantization_level": "fp8"}},
        {"name": "mistral-large-3:675b-cloud", "remote_model": "mistral-large-3:675b-cloud", "remote_host": "https://ollama.com:443",
         "details": {"format": "", "family": "mistral3", "parameter_size": "675000000000", "quantization_level": "fp8"}},
        {"name": "kimi-k2:1t-cloud", "remote_model": "kimi-k2:1t-cloud", "remote_host": "https://ollama.com:443",
         "details": {"format": "", "family": "deepseek2", "parameter_size": "1T", "quantization_level": "FP8"}},
        # Local GGUF models — no remote fields
        {"name": "llama3.2:latest",
         "details": {"format": "gguf", "family": "llama", "parameter_size": "3.2B", "quantization_level": "Q4_K_M"}},
        {"name": "gemma3:12b",
         "details": {"format": "gguf", "family": "gemma3", "parameter_size": "12.2B", "quantization_level": "Q4_K_M"}},
        # Local image model — safetensors format, no remote fields
        {"name": "x/flux2-klein:latest",
         "details": {"format": "safetensors", "family": "", "parameter_size": "", "quantization_level": ""}},
    ]
}


class TestVibeCatalog:
    """Test MSTY_VIBE_CATALOG dynamic model classification and caching."""

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_catalog_classifies_models_dynamically(self, mock_httpx: MagicMock) -> None:
        """Models are classified by remote_model/remote_host signals."""
        from agents.core.host_function_dispatcher import _msty_bridge
        _msty_bridge._catalog_cache = None  # Clear cache

        mock_resp = MagicMock()
        mock_resp.json.return_value = _FAKE_CATALOG
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = dispatch("MSTY_VIBE_CATALOG", [False], tier="hearth")
        catalog = json.loads(result)

        assert catalog["summary"]["total"] == 8
        assert catalog["summary"]["local_count"] == 2  # llama3.2, gemma3
        assert catalog["summary"]["cloud_count"] == 5  # gemini, qwen, deepseek, mistral, kimi
        assert catalog["summary"]["image_count"] == 1  # flux2

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_cloud_entries_carry_provider_hint(self, mock_httpx: MagicMock) -> None:
        """Cloud entries include provider_hint extracted from remote_host."""
        from agents.core.host_function_dispatcher import _msty_bridge
        _msty_bridge._catalog_cache = None

        mock_resp = MagicMock()
        mock_resp.json.return_value = _FAKE_CATALOG
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = dispatch("MSTY_VIBE_CATALOG", [False], tier="hearth")
        catalog = json.loads(result)

        # Every cloud entry should carry remote_host + provider_hint
        for entry in catalog["cloud"]:
            assert entry["remote_host"] == "https://ollama.com:443"
            assert entry["provider_hint"] == "ollama.com"
            assert entry["source"] == "cloud"

        # Local entries should NOT have provider_hint
        for entry in catalog["local"]:
            assert "provider_hint" not in entry
            assert entry["source"] == "local"

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_catalog_returns_cached_on_second_call(self, mock_httpx: MagicMock) -> None:
        """Second call within TTL returns cached data without API call."""
        from agents.core.host_function_dispatcher import _msty_bridge
        _msty_bridge._catalog_cache = None  # Clear cache

        mock_resp = MagicMock()
        mock_resp.json.return_value = _FAKE_CATALOG
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        # First call — hits API
        dispatch("MSTY_VIBE_CATALOG", [False], tier="hearth")
        assert mock_httpx.get.call_count == 1

        # Second call — should use cache
        dispatch("MSTY_VIBE_CATALOG", [False], tier="hearth")
        assert mock_httpx.get.call_count == 1  # No additional API call

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_catalog_force_refresh_bypasses_cache(self, mock_httpx: MagicMock) -> None:
        """force_refresh=True bypasses the TTL cache."""
        from agents.core.host_function_dispatcher import _msty_bridge
        _msty_bridge._catalog_cache = None  # Clear cache

        mock_resp = MagicMock()
        mock_resp.json.return_value = _FAKE_CATALOG
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        dispatch("MSTY_VIBE_CATALOG", [False], tier="hearth")
        dispatch("MSTY_VIBE_CATALOG", [True], tier="hearth")  # Force refresh
        assert mock_httpx.get.call_count == 2

    @patch.dict("os.environ", {"MSTY_HOST": "http://test:11434"})
    @patch("agents.core.host_function_dispatcher.httpx")
    def test_catalog_clusters_cloud_by_family(self, mock_httpx: MagicMock) -> None:
        """Cloud models are clustered by family for provider detection."""
        from agents.core.host_function_dispatcher import _msty_bridge
        _msty_bridge._catalog_cache = None  # Clear cache

        mock_resp = MagicMock()
        mock_resp.json.return_value = _FAKE_CATALOG
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.get.return_value = mock_resp

        result = dispatch("MSTY_VIBE_CATALOG", [False], tier="hearth")
        catalog = json.loads(result)
        clusters = catalog["summary"]["cloud_family_clusters"]

        # Should have family clusters from the fake data
        assert len(clusters) >= 3  # deepseek2, deepseek3.2, mistral3, (unknown)
        assert "deepseek2" in clusters or "mistral3" in clusters

    def test_vibe_catalog_available_at_hearth(self) -> None:
        """MSTY_VIBE_CATALOG is cheap and available at all tiers."""
        with open("governance/host_functions.json") as f:
            registry = json.load(f)
        fn_map = {fn["name"]: fn for fn in registry["functions"]}
        assert "hearth" in fn_map["MSTY_VIBE_CATALOG"]["tier"]
        assert fn_map["MSTY_VIBE_CATALOG"]["gas"] == 2
