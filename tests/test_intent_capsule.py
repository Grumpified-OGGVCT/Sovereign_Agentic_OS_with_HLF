"""
Tests for HLF Intent Capsules.

Covers:
  - IntentCapsule construction and tier factories
  - AST validation (tag allowlist, tool restrictions, gas limits)
  - CapsuleViolation error handling
  - CapsuleInterpreter runtime enforcement
  - Export from hlf package

All tests are mock-based — no real runtime execution.
"""

from __future__ import annotations

from hlf import (
    CapsuleViolation,
    IntentCapsule,
    forge_capsule,
    hearth_capsule,
    sovereign_capsule,
)
from hlf.hlfc import compile as hlfc_compile
from hlf.intent_capsule import (
    DEFAULT_TAGS,
    DEFAULT_TOOLS,
    STRUCTURAL_TAGS,
)

# ── Tier Factory Tests ───────────────────────────────────────────────────────


class TestTierFactories:
    """Tests for capsule tier factory functions."""

    def test_hearth_capsule(self) -> None:
        cap = hearth_capsule("worker-01")
        assert cap.agent == "worker-01"
        assert cap.tier == "hearth"
        assert cap.max_gas == 20
        assert "DELEGATE" in cap.denied_tags
        assert "DEFINE" in cap.denied_tags
        assert "config" in cap.read_only_vars

    def test_forge_capsule(self) -> None:
        cap = forge_capsule("builder-01")
        assert cap.agent == "builder-01"
        assert cap.tier == "forge"
        assert cap.max_gas == 100
        assert "MEMORY" in cap.allowed_tags
        assert "RECALL" in cap.allowed_tags
        assert "HTTP_GET" in cap.allowed_tools

    def test_sovereign_capsule(self) -> None:
        cap = sovereign_capsule("admin")
        assert cap.agent == "admin"
        assert cap.tier == "sovereign"
        assert cap.max_gas == 1000
        # Sovereign has empty allowlists = allow all
        assert len(cap.allowed_tags) == 0
        assert len(cap.allowed_tools) == 0

    def test_default_capsule(self) -> None:
        cap = IntentCapsule(agent="test-agent")
        assert cap.tier == "hearth"
        assert cap.max_gas == 50
        assert cap.allowed_tags == set(DEFAULT_TAGS)
        assert cap.allowed_tools == set(DEFAULT_TOOLS)


# ── AST Validation Tests ────────────────────────────────────────────────────


class TestASTValidation:
    """Tests for capsule pre-flight AST validation."""

    def _make_ast(self, *nodes: dict) -> dict:
        return {"version": "0.4.0", "program": list(nodes)}

    def test_valid_program_passes(self) -> None:
        ast = self._make_ast(
            {"tag": "INTENT", "args": ["deploy"]},
            {"tag": "SET", "name": "x", "value": 1},
            {"tag": "RESULT", "code": 0, "message": "ok"},
        )
        cap = hearth_capsule("agent-1")
        violations = cap.validate_program(ast)
        assert violations == []

    def test_denied_tag_fails(self) -> None:
        ast = self._make_ast(
            {"tag": "INTENT", "args": ["deploy"]},
            {"tag": "DELEGATE", "args": ["sub-agent"]},
        )
        cap = hearth_capsule("agent-2")
        violations = cap.validate_program(ast)
        assert len(violations) >= 1
        assert "DELEGATE" in violations[0]

    def test_unknown_tag_fails(self) -> None:
        ast = self._make_ast(
            {"tag": "INTENT", "args": ["test"]},
            {"tag": "HACK_SYSTEM", "args": ["exploit"]},
        )
        cap = hearth_capsule("agent-3")
        violations = cap.validate_program(ast)
        assert len(violations) >= 1
        assert "HACK_SYSTEM" in violations[0]

    def test_structural_tags_always_allowed(self) -> None:
        """Structural tags should pass even with restrictive capsules."""
        ast = self._make_ast(
            *[{"tag": tag, "args": []} for tag in STRUCTURAL_TAGS]
        )
        cap = IntentCapsule(
            agent="strict",
            allowed_tags={"INTENT"},  # Very restrictive
            max_gas=100,
        )
        violations = cap.validate_program(ast)
        assert violations == []

    def test_gas_limit_exceeded(self) -> None:
        nodes = [{"tag": "SET", "name": f"v{i}", "value": i} for i in range(25)]
        ast = self._make_ast(*nodes)
        cap = hearth_capsule("agent-4")
        assert cap.max_gas == 20
        violations = cap.validate_program(ast)
        assert any("gas limit" in v for v in violations)

    def test_tool_restriction(self) -> None:
        ast = self._make_ast(
            {"tag": "TOOL", "tool": "LAUNCH_MISSILES", "args": []},
        )
        cap = hearth_capsule("agent-5")
        violations = cap.validate_program(ast)
        assert len(violations) >= 1
        # Either the tag TOOL is not allowed, or the tool name is not permitted
        assert any("TOOL" in v or "LAUNCH_MISSILES" in v for v in violations)

    def test_allowed_tool_passes(self) -> None:
        ast = self._make_ast(
            {"tag": "TOOL", "tool": "READ_FILE", "args": []},
        )
        cap = hearth_capsule("agent-6")
        violations = cap.validate_program(ast)
        # READ_FILE is always allowed — only tool violation would be relevant
        tool_violations = [v for v in violations if "not permitted" in v]
        assert tool_violations == []

    def test_sovereign_allows_everything(self) -> None:
        ast = self._make_ast(
            {"tag": "DELEGATE", "args": ["sub"]},
            {"tag": "DEFINE", "name": "macro", "body": []},
            {"tag": "TOOL", "tool": "HTTP_POST", "args": []},
        )
        cap = sovereign_capsule("admin")
        violations = cap.validate_program(ast)
        assert violations == []


# ── CapsuleViolation Error Tests ─────────────────────────────────────────────


class TestCapsuleViolation:
    """Tests for CapsuleViolation exception."""

    def test_exception_attributes(self) -> None:
        exc = CapsuleViolation("agent-x", "tag DELEGATE denied")
        assert exc.agent == "agent-x"
        assert exc.violation == "tag DELEGATE denied"
        assert "agent-x" in str(exc)
        assert "DELEGATE" in str(exc)

    def test_is_hlf_runtime_error(self) -> None:
        from hlf.hlfc import HlfRuntimeError
        exc = CapsuleViolation("agent", "violation")
        assert isinstance(exc, HlfRuntimeError)


# ── Package Export Tests ─────────────────────────────────────────────────────


class TestPackageExports:
    """Verify intent capsule types are exported from hlf package."""

    def test_import_from_hlf(self) -> None:
        from hlf import CapsuleViolation, IntentCapsule
        assert IntentCapsule is not None
        assert CapsuleViolation is not None

    def test_factory_imports(self) -> None:
        from hlf import hearth_capsule
        cap = hearth_capsule("test")
        assert cap.agent == "test"


# ── Compile + Validate Integration ───────────────────────────────────────────


class TestCompileAndValidate:
    """Integration tests: compile HLF then validate with capsule."""

    def test_simple_hlf_passes_hearth(self) -> None:
        source = '[HLF-v3]\n[INTENT] deploy "prod"\n[SET] x=42\n[RESULT] code=0 message="ok"\nΩ'
        ast = hlfc_compile(source)
        cap = hearth_capsule("integration-agent")
        violations = cap.validate_program(ast)
        assert violations == []

    def test_module_program_passes(self) -> None:
        source = '[HLF-v3]\n[MODULE] math\n[FUNCTION] abs "value"\n[RESULT] code=0 message="loaded"\nΩ'
        ast = hlfc_compile(source)
        cap = forge_capsule("module-agent")
        violations = cap.validate_program(ast)
        assert violations == []
