"""Tests for Z3 Formal Verification — HLF Constraint and Invariant Prover."""

from __future__ import annotations

from agents.core.formal_verifier import (
    ConstraintKind,
    FallbackSolver,
    FormalVerifier,
    VerificationReport,
    VerificationResult,
    VerificationStatus,
    extract_constraints,
    z3_available,
)

# ─── VerificationResult Tests ───────────────────────────────────────────────


class TestVerificationResult:
    def test_proven(self):
        r = VerificationResult(
            property_name="test",
            status=VerificationStatus.PROVEN,
            kind=ConstraintKind.RANGE_CHECK,
        )
        assert r.is_proven()

    def test_not_proven(self):
        r = VerificationResult(
            property_name="test",
            status=VerificationStatus.COUNTEREXAMPLE,
            kind=ConstraintKind.RANGE_CHECK,
        )
        assert not r.is_proven()

    def test_to_dict(self):
        r = VerificationResult(
            property_name="range",
            status=VerificationStatus.PROVEN,
            kind=ConstraintKind.RANGE_CHECK,
            message="ok",
            solver="fallback",
        )
        d = r.to_dict()
        assert d["property"] == "range"
        assert d["status"] == "proven"
        assert d["kind"] == "range_check"

    def test_counterexample_in_dict(self):
        r = VerificationResult(
            property_name="test",
            status=VerificationStatus.COUNTEREXAMPLE,
            kind=ConstraintKind.RANGE_CHECK,
            counterexample={"x": 5},
        )
        assert r.to_dict()["counterexample"] == {"x": 5}


# ─── VerificationReport Tests ───────────────────────────────────────────────


class TestVerificationReport:
    def test_empty_report(self):
        report = VerificationReport()
        assert report.total_count == 0
        assert report.proven_count == 0
        assert report.failed_count == 0

    def test_add_results(self):
        report = VerificationReport()
        report.add(VerificationResult("a", VerificationStatus.PROVEN, ConstraintKind.RANGE_CHECK))
        report.add(VerificationResult("b", VerificationStatus.PROVEN, ConstraintKind.TYPE_INVARIANT))
        assert report.total_count == 2
        assert report.proven_count == 2
        assert report.all_proven

    def test_failed_report(self):
        report = VerificationReport()
        report.add(VerificationResult("a", VerificationStatus.PROVEN, ConstraintKind.RANGE_CHECK))
        report.add(VerificationResult("b", VerificationStatus.COUNTEREXAMPLE, ConstraintKind.RANGE_CHECK))
        assert report.failed_count == 1
        assert not report.all_proven

    def test_to_dict(self):
        report = VerificationReport()
        report.add(VerificationResult("a", VerificationStatus.PROVEN, ConstraintKind.RANGE_CHECK))
        d = report.to_dict()
        assert d["total"] == 1
        assert d["proven"] == 1
        assert "results" in d

    def test_summary(self):
        report = VerificationReport()
        report.add(VerificationResult("a", VerificationStatus.PROVEN, ConstraintKind.RANGE_CHECK))
        s = report.summary()
        assert "1/1 proven" in s
        assert "All properties verified" in s


# ─── FallbackSolver Tests ───────────────────────────────────────────────────


class TestFallbackSolver:
    def setup_method(self):
        self.solver = FallbackSolver()

    # Range checks
    def test_range_within_bounds(self):
        r = self.solver.check_range(5, low=0, high=10, name="test")
        assert r.is_proven()

    def test_range_below_low(self):
        r = self.solver.check_range(-1, low=0, high=10, name="test")
        assert r.status == VerificationStatus.COUNTEREXAMPLE
        assert r.counterexample is not None

    def test_range_above_high(self):
        r = self.solver.check_range(20, low=0, high=10, name="test")
        assert r.status == VerificationStatus.COUNTEREXAMPLE

    def test_range_no_bounds(self):
        r = self.solver.check_range(100, name="test")
        assert r.is_proven()

    def test_range_non_numeric(self):
        r = self.solver.check_range("abc", low=0, high=10)
        assert r.status == VerificationStatus.ERROR

    def test_range_exact_bounds(self):
        r = self.solver.check_range(0, low=0, high=10)
        assert r.is_proven()
        r = self.solver.check_range(10, low=0, high=10)
        assert r.is_proven()

    # Type checks
    def test_type_number(self):
        r = self.solver.check_type(42, "number")
        assert r.is_proven()

    def test_type_string(self):
        r = self.solver.check_type("hello", "string")
        assert r.is_proven()

    def test_type_mismatch(self):
        r = self.solver.check_type("hello", "number")
        assert r.status == VerificationStatus.COUNTEREXAMPLE

    def test_type_unknown(self):
        r = self.solver.check_type(42, "widget")
        assert r.status == VerificationStatus.UNKNOWN

    def test_type_boolean(self):
        r = self.solver.check_type(True, "boolean")
        assert r.is_proven()

    def test_type_list(self):
        r = self.solver.check_type([1, 2], "list")
        assert r.is_proven()

    def test_type_dict(self):
        r = self.solver.check_type({"a": 1}, "dict")
        assert r.is_proven()

    # Gas budget checks
    def test_gas_within_budget(self):
        r = self.solver.check_gas_budget([100, 200, 300], 1000)
        assert r.is_proven()

    def test_gas_over_budget(self):
        r = self.solver.check_gas_budget([5000, 6000], 10000)
        assert r.status == VerificationStatus.COUNTEREXAMPLE
        assert r.counterexample is not None
        assert r.counterexample["over_by"] == 1000

    def test_gas_exact_budget(self):
        r = self.solver.check_gas_budget([500, 500], 1000)
        assert r.is_proven()


# ─── Constraint Extraction Tests ────────────────────────────────────────────


class TestConstraintExtraction:
    def test_empty_ast(self):
        constraints = extract_constraints({"program": []})
        assert constraints == []

    def test_set_number(self):
        ast = {"program": [{"tag": "SET", "name": "x", "value": 42}]}
        constraints = extract_constraints(ast)
        assert len(constraints) == 1
        assert constraints[0]["kind"] == "type_invariant"
        assert constraints[0]["expected_type"] == "number"

    def test_set_string(self):
        ast = {"program": [{"tag": "SET", "name": "name", "value": "hello"}]}
        constraints = extract_constraints(ast)
        assert len(constraints) == 1
        assert constraints[0]["expected_type"] == "string"

    def test_constraint_node(self):
        ast = {"program": [{"tag": "CONSTRAINT", "name": "max_retry", "args": [5]}]}
        constraints = extract_constraints(ast)
        assert len(constraints) == 1
        assert constraints[0]["kind"] == "range_check"

    def test_spec_gate(self):
        ast = {"program": [{"tag": "SPEC_GATE", "condition": {"op": "COMPARE"}}]}
        constraints = extract_constraints(ast)
        assert len(constraints) == 1
        assert constraints[0]["kind"] == "spec_gate"

    def test_parallel_gas(self):
        ast = {"program": [{"tag": "PARALLEL", "tasks": [{"tag": "TOOL"}, {"tag": "TOOL"}]}]}
        constraints = extract_constraints(ast)
        # 1 gas_bound for the PARALLEL node
        gas = [c for c in constraints if c["kind"] == "gas_bound"]
        assert len(gas) == 1
        assert gas[0]["task_count"] == 2

    def test_nested_constraints(self):
        ast = {"program": [{
            "tag": "CONDITIONAL",
            "then": {"tag": "SET", "name": "y", "value": 100},
            "else": {"tag": "SET", "name": "z", "value": "done"},
        }]}
        constraints = extract_constraints(ast)
        assert len(constraints) == 2

    def test_none_nodes_skipped(self):
        ast = {"program": [None, {"tag": "SET", "name": "a", "value": 1}]}
        constraints = extract_constraints(ast)
        assert len(constraints) == 1


# ─── FormalVerifier Integration Tests ────────────────────────────────────────


class TestFormalVerifier:
    def setup_method(self):
        self.verifier = FormalVerifier()

    def test_solver_name(self):
        assert self.verifier.solver_name in ("z3", "fallback")

    def test_verify_type(self):
        r = self.verifier.verify_type(42, "number")
        assert r.is_proven()

    def test_verify_range(self):
        r = self.verifier.verify_range(5, low=0, high=10)
        assert r.is_proven()

    def test_verify_gas_budget(self):
        r = self.verifier.verify_gas_budget([100, 200, 300], 1000)
        assert r.is_proven()

    def test_verify_gas_budget_fail(self):
        r = self.verifier.verify_gas_budget([5000, 6000], 10000)
        assert not r.is_proven()

    def test_verify_ast_with_types(self):
        ast = {"program": [
            {"tag": "SET", "name": "count", "value": 5},
            {"tag": "SET", "name": "label", "value": "test"},
        ]}
        report = self.verifier.verify_ast(ast)
        assert report.total_count == 2
        assert report.all_proven

    def test_verify_ast_with_parallel(self):
        ast = {"program": [
            {"tag": "PARALLEL", "tasks": [
                {"tag": "TOOL", "tool": "a"},
                {"tag": "TOOL", "tool": "b"},
            ]},
        ]}
        report = self.verifier.verify_ast(ast)
        assert report.proven_count >= 1

    def test_verify_ast_empty(self):
        report = self.verifier.verify_ast({"program": []})
        assert report.total_count == 0

    def test_z3_availability(self):
        # Just verify the function doesn't crash
        result = z3_available()
        assert isinstance(result, bool)

    def test_report_summary_all_proven(self):
        report = VerificationReport()
        report.add(self.verifier.verify_type(42, "number"))
        report.add(self.verifier.verify_range(5, low=0, high=10))
        assert "All properties verified" in report.summary()
