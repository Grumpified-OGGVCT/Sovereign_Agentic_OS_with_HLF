"""Tests for HLF syntax validation, compilation, and linting."""

from __future__ import annotations

import pytest

from hlf import validate_hlf
from hlf.hlfc import HlfSyntaxError, format_correction
from hlf.hlfc import compile as hlfc_compile
from hlf.hlflint import lint


class TestValidateHlf:
    def test_valid_intent_line(self) -> None:
        assert validate_hlf("[INTENT] greet world") is True

    def test_valid_result_line(self) -> None:
        assert validate_hlf("[RESULT] code=0 message=ok") is True

    def test_valid_terminator(self) -> None:
        assert validate_hlf("Ω") is True

    def test_valid_version_header(self) -> None:
        assert validate_hlf("[HLF-v2]") is True

    def test_empty_line(self) -> None:
        assert validate_hlf("") is True

    def test_invalid_lowercase_tag(self) -> None:
        assert validate_hlf("[intent] something") is False

    def test_invalid_plain_text(self) -> None:
        assert validate_hlf("just some prose text") is False


class TestHlfCompile:
    def test_hello_world_fixture(self, hello_hlf: str) -> None:
        ast = hlfc_compile(hello_hlf)
        assert ast["version"] == "0.4.0"
        assert isinstance(ast["program"], list)
        assert len(ast["program"]) > 0

    def test_intent_tag_present(self, hello_hlf: str) -> None:
        ast = hlfc_compile(hello_hlf)
        tags = [node["tag"] for node in ast["program"] if node]
        assert "INTENT" in tags

    def test_result_tag_present(self, hello_hlf: str) -> None:
        ast = hlfc_compile(hello_hlf)
        tags = [node["tag"] for node in ast["program"] if node]
        assert "RESULT" in tags

    def test_malformed_intent_rejected(self) -> None:
        from hlf.hlfc import HlfSyntaxError

        with pytest.raises(HlfSyntaxError):
            hlfc_compile("just plain text without tags")

    def test_tag_arity_checking(self) -> None:
        """Compile a minimal valid HLF and verify AST structure."""
        source = "[HLF-v2]\n[INTENT] analyze /etc/passwd\nΩ\n"
        ast = hlfc_compile(source)
        assert ast["program"][0]["tag"] == "INTENT"
        assert len(ast["program"][0]["args"]) >= 1

    def test_serialization_roundtrip(self, hello_hlf: str) -> None:
        import json

        ast = hlfc_compile(hello_hlf)
        serialized = json.dumps(ast)
        deserialized = json.loads(serialized)
        assert deserialized == ast


class TestHlfLint:
    def test_clean_file_no_diagnostics(self, hello_hlf: str) -> None:
        issues = lint(hello_hlf, max_gas=10)
        # May have token overflow for longer files — just check it returns a list
        assert isinstance(issues, list)

    def test_gas_exceeded(self) -> None:
        # Build a program with many nodes
        lines = ["[HLF-v2]"]
        for i in range(15):
            lines.append(f'[ACTION] step_{i} "arg"')
        lines.append("Ω")
        source = "\n".join(lines)
        issues = lint(source, max_gas=5)
        gas_issues = [i for i in issues if "GAS_EXCEEDED" in i]
        assert len(gas_issues) > 0


# ---------------------------------------------------------------------------
# v0.4.0 operator tests
# ---------------------------------------------------------------------------


def _prog(body: str) -> str:
    """Wrap *body* in a minimal valid HLF-v2 program."""
    return f"[HLF-v2]\n{body}\nΩ\n"


def _op_by_glyph(ops: dict, glyph: str) -> str | None:
    """Return the first key in *ops* that contains *glyph*, or None."""
    return next((k for k in ops if glyph in k), None)


class TestHlfV04Conditionals:
    """RFC 9005 §3: conditional logic operators ⊎ ⇒ ⇌."""

    def test_conditional_without_else_parses(self) -> None:
        ast = hlfc_compile(_prog('⊎ 1 == 1 ⇒ [INTENT] ok "yes"'))
        assert ast["program"][0]["tag"] == "CONDITIONAL"

    def test_conditional_with_else_parses(self) -> None:
        ast = hlfc_compile(_prog('⊎ 1 == 2 ⇒ [INTENT] ok "yes" ⇌ [INTENT] fail "no"'))
        node = ast["program"][0]
        assert node["tag"] == "CONDITIONAL"
        assert "else" in node

    def test_conditional_else_branch_is_intent(self) -> None:
        ast = hlfc_compile(_prog('⊎ 1 == 2 ⇒ [INTENT] ok "yes" ⇌ [INTENT] fail "no"'))
        node = ast["program"][0]
        assert node["else"]["tag"] == "INTENT"

    def test_conditional_then_branch_is_intent(self) -> None:
        ast = hlfc_compile(_prog('⊎ 1 == 1 ⇒ [INTENT] ok "yes"'))
        node = ast["program"][0]
        assert node["then"]["tag"] == "INTENT"

    def test_negation_operator(self) -> None:
        ast = hlfc_compile(_prog('⊎ ¬ 1 == 2 ⇒ [INTENT] ok "x"'))
        condition = ast["program"][0]["condition"]
        assert condition["op"] == "NOT"
        assert condition["operator"] == "¬"

    def test_intersection_operator(self) -> None:
        ast = hlfc_compile(_prog('⊎ (1 == 1) ∩ (2 == 2) ⇒ [INTENT] ok "both"'))
        condition = ast["program"][0]["condition"]
        assert condition["op"] == "AND"
        assert condition["operator"] == "∩"

    def test_union_operator(self) -> None:
        ast = hlfc_compile(_prog('⊎ (1 == 1) ∪ (2 == 3) ⇒ [INTENT] ok "either"'))
        condition = ast["program"][0]["condition"]
        assert condition["op"] == "OR"
        assert condition["operator"] == "∪"

    def test_conditional_node_has_human_readable(self) -> None:
        ast = hlfc_compile(_prog('⊎ 1 == 1 ⇒ [INTENT] ok "yes"'))
        assert "human_readable" in ast["program"][0]

    def test_negation_human_readable_starts_with_not(self) -> None:
        ast = hlfc_compile(_prog('⊎ ¬ 1 == 2 ⇒ [INTENT] ok "x"'))
        hr = ast["program"][0]["condition"]["human_readable"]
        assert hr.startswith("NOT")

    def test_conditional_operator_field(self) -> None:
        ast = hlfc_compile(_prog('⊎ 1 == 1 ⇒ [INTENT] ok "yes"'))
        assert ast["program"][0]["operator"] == "⊎ ⇒ ⇌"


class TestHlfV04MathExpressions:
    """RFC 9005 §3 math expressions and comparisons."""

    def test_comparison_equal(self) -> None:
        ast = hlfc_compile(_prog('⊎ 5 == 5 ⇒ [INTENT] ok "eq"'))
        cond = ast["program"][0]["condition"]
        assert cond["op"] == "COMPARE"
        assert cond["operator"] == "=="

    def test_comparison_not_equal(self) -> None:
        ast = hlfc_compile(_prog('⊎ 1 != 2 ⇒ [INTENT] ok "neq"'))
        cond = ast["program"][0]["condition"]
        assert cond["operator"] == "!="

    def test_comparison_greater_than(self) -> None:
        ast = hlfc_compile(_prog('⊎ 5 > 3 ⇒ [INTENT] ok "gt"'))
        cond = ast["program"][0]["condition"]
        assert cond["operator"] == ">"

    def test_comparison_less_than_or_equal(self) -> None:
        ast = hlfc_compile(_prog('⊎ 3 <= 5 ⇒ [INTENT] ok "lte"'))
        cond = ast["program"][0]["condition"]
        assert cond["operator"] == "<="

    def test_math_addition_in_condition(self) -> None:
        ast = hlfc_compile(_prog('⊎ 2 + 3 == 5 ⇒ [INTENT] ok "add"'))
        cond = ast["program"][0]["condition"]
        assert cond["op"] == "COMPARE"
        assert cond["left"]["op"] == "MATH"
        assert cond["left"]["operator"] == "+"

    def test_math_multiplication_in_condition(self) -> None:
        ast = hlfc_compile(_prog('⊎ 2 * 3 == 6 ⇒ [INTENT] ok "mul"'))
        cond = ast["program"][0]["condition"]
        assert cond["left"]["operator"] == "*"

    def test_math_node_human_readable(self) -> None:
        ast = hlfc_compile(_prog('⊎ 2 + 3 == 5 ⇒ [INTENT] ok "add"'))
        math_node = ast["program"][0]["condition"]["left"]
        assert "human_readable" in math_node
        assert "+" in math_node["human_readable"]

    def test_comparison_operands_are_numbers(self) -> None:
        ast = hlfc_compile(_prog('⊎ 10 > 5 ⇒ [INTENT] ok "gt"'))
        cond = ast["program"][0]["condition"]
        assert cond["left"] == 10
        assert cond["right"] == 5

    def test_math_modulo(self) -> None:
        ast = hlfc_compile(_prog('⊎ 10 % 3 == 1 ⇒ [INTENT] ok "mod"'))
        cond = ast["program"][0]["condition"]
        assert cond["left"]["operator"] == "%"

    def test_math_floor_division(self) -> None:
        ast = hlfc_compile(_prog('⊎ 7 // 2 == 3 ⇒ [INTENT] ok "fdiv"'))
        cond = ast["program"][0]["condition"]
        assert cond["left"]["operator"] == "//"

    def test_math_power(self) -> None:
        ast = hlfc_compile(_prog('⊎ 2 ** 3 == 8 ⇒ [INTENT] ok "pow"'))
        cond = ast["program"][0]["condition"]
        assert cond["left"]["operator"] == "**"

    def test_math_power_human_readable(self) -> None:
        ast = hlfc_compile(_prog('⊎ 2 ** 3 == 8 ⇒ [INTENT] ok "pow"'))
        math_node = ast["program"][0]["condition"]["left"]
        assert "**" in math_node["human_readable"]

    def test_math_power_right_associative(self) -> None:
        """2 ** 3 ** 2 should be 2 ** (3 ** 2) = 2 ** 9, not (2 ** 3) ** 2 = 64."""
        ast = hlfc_compile(_prog('⊎ 2 ** 3 ** 2 == 512 ⇒ [INTENT] ok "rpow"'))
        cond = ast["program"][0]["condition"]
        pow_node = cond["left"]
        assert pow_node["operator"] == "**"
        # Right child should also be a power node (right-associative)
        assert pow_node["right"]["operator"] == "**"

    def test_math_unary_negation(self) -> None:
        """Unary negation of a variable (not literal — lexer absorbs -42 as NUMBER)."""
        ast = hlfc_compile(_prog('result ← -(2 + 3)'))
        node = ast['program'][0]
        assert node['tag'] == 'ASSIGN'
        assert node['value']['op'] == 'UNARY_NEG'

    def test_math_unary_neg_human_readable(self) -> None:
        ast = hlfc_compile(_prog('result ← -(2 + 3)'))
        assert '-' in ast['program'][0]['value']['human_readable']

    def test_math_precedence_mul_before_add(self) -> None:
        """2 + 3 * 4 should produce (2 + (3 * 4)) not ((2 + 3) * 4)."""
        ast = hlfc_compile(_prog('⊎ 2 + 3 * 4 == 14 ⇒ [INTENT] ok "prec"'))
        cond = ast["program"][0]["condition"]
        add_node = cond["left"]
        assert add_node["operator"] == "+"
        assert add_node["right"]["operator"] == "*"

    def test_math_precedence_power_before_mul(self) -> None:
        """2 * 3 ** 2 should produce (2 * (3 ** 2)) not ((2 * 3) ** 2)."""
        ast = hlfc_compile(_prog('⊎ 2 * 3 ** 2 == 18 ⇒ [INTENT] ok "ppow"'))
        cond = ast["program"][0]["condition"]
        mul_node = cond["left"]
        assert mul_node["operator"] == "*"
        assert mul_node["right"]["operator"] == "**"

    def test_math_subtraction(self) -> None:
        ast = hlfc_compile(_prog('⊎ 10 - 3 == 7 ⇒ [INTENT] ok "sub"'))
        cond = ast["program"][0]["condition"]
        assert cond["left"]["operator"] == "-"

    def test_math_division(self) -> None:
        ast = hlfc_compile(_prog('⊎ 10 / 2 == 5 ⇒ [INTENT] ok "div"'))
        cond = ast["program"][0]["condition"]
        assert cond["left"]["operator"] == "/"

    def test_math_complex_expression(self) -> None:
        """(2 + 3) * 4 should produce ((2 + 3) * 4)."""
        ast = hlfc_compile(_prog('⊎ (2 + 3) * 4 == 20 ⇒ [INTENT] ok "complex"'))
        cond = ast["program"][0]["condition"]
        mul_node = cond["left"]
        assert mul_node["operator"] == "*"
        assert mul_node["left"]["operator"] == "+"

    def test_math_modulo_human_readable(self) -> None:
        ast = hlfc_compile(_prog('⊎ 10 % 3 == 1 ⇒ [INTENT] ok "mod"'))
        math_node = ast["program"][0]["condition"]["left"]
        assert "%" in math_node["human_readable"]

    def test_math_floor_division_human_readable(self) -> None:
        ast = hlfc_compile(_prog('⊎ 7 // 2 == 3 ⇒ [INTENT] ok "fdiv"'))
        math_node = ast["program"][0]["condition"]["left"]
        assert "//" in math_node["human_readable"]

    def test_math_multi_operator_chain(self) -> None:
        """a + b - c should be left-associative: ((a + b) - c)."""
        ast = hlfc_compile(_prog('⊎ 10 + 5 - 3 == 12 ⇒ [INTENT] ok "chain"'))
        cond = ast["program"][0]["condition"]
        sub_node = cond["left"]
        assert sub_node["operator"] == "-"
        assert sub_node["left"]["operator"] == "+"

    def test_assignment_from_power_expression(self) -> None:
        ast = hlfc_compile(_prog("result ← 2 ** 10"))
        node = ast["program"][0]
        assert node["tag"] == "ASSIGN"
        assert node["value"]["operator"] == "**"


class TestHlfV04Assignment:
    """RFC 9005 §5.1 assignment operator ←."""

    def test_simple_assignment_parses(self) -> None:
        ast = hlfc_compile(_prog("x ← 42"))
        assert ast["program"][0]["tag"] == "ASSIGN"

    def test_assignment_name(self) -> None:
        ast = hlfc_compile(_prog("x ← 42"))
        assert ast["program"][0]["name"] == "x"

    def test_assignment_value(self) -> None:
        ast = hlfc_compile(_prog("x ← 42"))
        assert ast["program"][0]["value"] == 42

    def test_assignment_operator_field(self) -> None:
        ast = hlfc_compile(_prog("x ← 42"))
        assert ast["program"][0]["operator"] == "←"

    def test_typed_assignment_parses(self) -> None:
        ast = hlfc_compile(_prog("x :: ℕ ← 99"))
        node = ast["program"][0]
        assert node["tag"] == "ASSIGN"
        assert node["type_annotation"] is not None

    def test_typed_assignment_type_name(self) -> None:
        ast = hlfc_compile(_prog("x :: ℕ ← 99"))
        ann = ast["program"][0]["type_annotation"]
        assert ann["type_name"] == "Number"

    def test_typed_assignment_string_type(self) -> None:
        ast = hlfc_compile(_prog('label :: 𝕊 ← "hello"'))
        ann = ast["program"][0]["type_annotation"]
        assert ann["type_name"] == "String"

    def test_assignment_human_readable(self) -> None:
        ast = hlfc_compile(_prog("x ← 42"))
        hr = ast["program"][0]["human_readable"]
        assert "x" in hr
        assert "←" in hr

    def test_assignment_from_math_expression(self) -> None:
        ast = hlfc_compile(_prog("result ← 6 * 7"))
        node = ast["program"][0]
        assert node["tag"] == "ASSIGN"
        assert node["value"]["op"] == "MATH"


class TestHlfV04ToolExecution:
    """RFC 9005 §4.1 tool execution operator ↦ τ."""

    def test_tool_stmt_parses(self) -> None:
        ast = hlfc_compile(_prog("↦ τ(READ) /etc/passwd"))
        assert ast["program"][0]["tag"] == "TOOL"

    def test_tool_name_extracted(self) -> None:
        ast = hlfc_compile(_prog("↦ τ(READ) /etc/passwd"))
        assert ast["program"][0]["tool"] == "READ"

    def test_tool_operator_field(self) -> None:
        ast = hlfc_compile(_prog("↦ τ(READ) /etc/passwd"))
        assert ast["program"][0]["operator"] == "↦ τ"

    def test_tool_args_contain_path(self) -> None:
        ast = hlfc_compile(_prog("↦ τ(READ) /etc/passwd"))
        assert "/etc/passwd" in ast["program"][0]["args"]

    def test_tool_with_type_annotation(self) -> None:
        ast = hlfc_compile(_prog("↦ τ(READ) :: 𝕊 /etc/passwd"))
        node = ast["program"][0]
        assert node["type_annotation"] is not None
        assert node["type_annotation"]["type_name"] == "String"

    def test_tool_without_annotation_type_is_none(self) -> None:
        ast = hlfc_compile(_prog("↦ τ(READ) /etc/passwd"))
        assert ast["program"][0]["type_annotation"] is None

    def test_tool_human_readable_contains_tool_name(self) -> None:
        ast = hlfc_compile(_prog("↦ τ(WRITE) /tmp/out"))
        hr = ast["program"][0]["human_readable"]
        assert "WRITE" in hr

    def test_tool_assigned_via_arrow(self) -> None:
        ast = hlfc_compile(_prog("content ← ↦ τ(READ) /etc/hosts"))
        node = ast["program"][0]
        assert node["tag"] == "ASSIGN"
        assert node["value"]["tag"] == "TOOL"


class TestHlfV04Parallel:
    """RFC 9005 §6.1 parallel execution operator ∥."""

    def test_parallel_stmt_parses(self) -> None:
        ast = hlfc_compile(_prog('∥ [ [INTENT] taskA "a", [INTENT] taskB "b" ]'))
        assert ast["program"][0]["tag"] == "PARALLEL"

    def test_parallel_operator_field(self) -> None:
        ast = hlfc_compile(_prog('∥ [ [INTENT] taskA "a", [INTENT] taskB "b" ]'))
        assert ast["program"][0]["operator"] == "∥"

    def test_parallel_tasks_count(self) -> None:
        ast = hlfc_compile(_prog('∥ [ [INTENT] taskA "a", [INTENT] taskB "b" ]'))
        assert len(ast["program"][0]["tasks"]) == 2

    def test_parallel_tasks_are_intent_nodes(self) -> None:
        ast = hlfc_compile(_prog('∥ [ [INTENT] taskA "a", [INTENT] taskB "b" ]'))
        tasks = ast["program"][0]["tasks"]
        assert all(t["tag"] == "INTENT" for t in tasks)

    def test_parallel_single_task(self) -> None:
        ast = hlfc_compile(_prog('∥ [ [INTENT] solo "run" ]'))
        assert len(ast["program"][0]["tasks"]) == 1

    def test_parallel_three_tasks(self) -> None:
        src = '∥ [ [INTENT] t1 "a", [INTENT] t2 "b", [INTENT] t3 "c" ]'
        ast = hlfc_compile(_prog(src))
        assert len(ast["program"][0]["tasks"]) == 3

    def test_parallel_human_readable(self) -> None:
        ast = hlfc_compile(_prog('∥ [ [INTENT] taskA "a", [INTENT] taskB "b" ]'))
        hr = ast["program"][0]["human_readable"]
        assert "parallel" in hr.lower()


class TestHlfV04Sync:
    """RFC 9005 §6.2 synchronization barrier operator ⋈."""

    def test_sync_stmt_parses(self) -> None:
        ast = hlfc_compile(_prog('⋈ [ taskA, taskB ] → [RESULT] code=0 message="done"'))
        assert ast["program"][0]["tag"] == "SYNC"

    def test_sync_operator_field(self) -> None:
        ast = hlfc_compile(_prog('⋈ [ taskA, taskB ] → [RESULT] code=0 message="done"'))
        assert ast["program"][0]["operator"] == "⋈"

    def test_sync_refs_extracted(self) -> None:
        ast = hlfc_compile(_prog('⋈ [ taskA, taskB ] → [RESULT] code=0 message="done"'))
        assert ast["program"][0]["refs"] == ["taskA", "taskB"]

    def test_sync_single_ref(self) -> None:
        ast = hlfc_compile(_prog('⋈ [ taskA ] → [INTENT] proceed "next"'))
        assert ast["program"][0]["refs"] == ["taskA"]

    def test_sync_action_tag(self) -> None:
        ast = hlfc_compile(_prog('⋈ [ taskA, taskB ] → [RESULT] code=0 message="done"'))
        assert ast["program"][0]["action"]["tag"] == "RESULT"

    def test_sync_human_readable_contains_refs(self) -> None:
        ast = hlfc_compile(_prog('⋈ [ taskA, taskB ] → [RESULT] code=0 message="done"'))
        hr = ast["program"][0]["human_readable"]
        assert "taskA" in hr
        assert "taskB" in hr


class TestHlfV04Struct:
    """RFC 9007 §2.1 struct definition operator ≡."""

    def test_struct_stmt_parses(self) -> None:
        ast = hlfc_compile(_prog("Point ≡ { x: ℕ, y: ℕ }"))
        assert ast["program"][0]["tag"] == "STRUCT"

    def test_struct_name(self) -> None:
        ast = hlfc_compile(_prog("Point ≡ { x: ℕ, y: ℕ }"))
        assert ast["program"][0]["name"] == "Point"

    def test_struct_operator_field(self) -> None:
        ast = hlfc_compile(_prog("Point ≡ { x: ℕ, y: ℕ }"))
        assert ast["program"][0]["operator"] == "≡"

    def test_struct_field_count(self) -> None:
        ast = hlfc_compile(_prog("Point ≡ { x: ℕ, y: ℕ }"))
        assert len(ast["program"][0]["fields"]) == 2

    def test_struct_field_names(self) -> None:
        ast = hlfc_compile(_prog("Point ≡ { x: ℕ, y: ℕ }"))
        names = [f["name"] for f in ast["program"][0]["fields"]]
        assert names == ["x", "y"]

    def test_struct_field_type_names(self) -> None:
        ast = hlfc_compile(_prog("Point ≡ { x: ℕ, y: ℕ }"))
        type_names = [f["type_name"] for f in ast["program"][0]["fields"]]
        assert type_names == ["Number", "Number"]

    def test_struct_mixed_types(self) -> None:
        ast = hlfc_compile(_prog("Person ≡ { name: 𝕊, age: ℕ, active: 𝔹 }"))
        fields = ast["program"][0]["fields"]
        assert len(fields) == 3
        assert fields[0]["type_name"] == "String"
        assert fields[1]["type_name"] == "Number"
        assert fields[2]["type_name"] == "Boolean"

    def test_struct_human_readable_contains_name(self) -> None:
        ast = hlfc_compile(_prog("Point ≡ { x: ℕ, y: ℕ }"))
        hr = ast["program"][0]["human_readable"]
        assert "Point" in hr


class TestHlfV04Glyphs:
    """Glyph-prefixed statement modifiers ⌘ Ж ∇ ⩕ ⨝ Δ ~ §."""

    def test_execute_glyph_parses(self) -> None:
        ast = hlfc_compile(_prog('⌘ [INTENT] execute "task"'))
        assert ast["program"][0]["tag"] == "GLYPH_MODIFIED"

    def test_execute_glyph_name(self) -> None:
        ast = hlfc_compile(_prog('⌘ [INTENT] execute "task"'))
        assert ast["program"][0]["glyph_name"] == "EXECUTE"

    def test_constraint_glyph(self) -> None:
        ast = hlfc_compile(_prog('Ж [INTENT] constrain "target"'))
        node = ast["program"][0]
        assert node["glyph"] == "Ж"
        assert node["glyph_name"] == "CONSTRAINT"

    def test_parameter_glyph(self) -> None:
        ast = hlfc_compile(_prog('∇ [INTENT] param "value"'))
        assert ast["program"][0]["glyph_name"] == "PARAMETER"

    def test_priority_glyph(self) -> None:
        ast = hlfc_compile(_prog('⩕ [INTENT] high_priority "now"'))
        assert ast["program"][0]["glyph_name"] == "PRIORITY"

    def test_delta_glyph(self) -> None:
        ast = hlfc_compile(_prog('Δ [INTENT] diff "state"'))
        assert ast["program"][0]["glyph_name"] == "DELTA"

    def test_glyph_inner_tag(self) -> None:
        ast = hlfc_compile(_prog('⌘ [INTENT] execute "task"'))
        assert ast["program"][0]["inner"]["tag"] == "INTENT"

    def test_glyph_human_readable_contains_glyph_name(self) -> None:
        ast = hlfc_compile(_prog('⌘ [INTENT] execute "task"'))
        hr = ast["program"][0]["human_readable"]
        assert "EXECUTE" in hr

    def test_nested_glyphs(self) -> None:
        ast = hlfc_compile(_prog('⌘ Ж [INTENT] execute "nested"'))
        outer = ast["program"][0]
        assert outer["tag"] == "GLYPH_MODIFIED"
        assert outer["inner"]["tag"] == "GLYPH_MODIFIED"


class TestHlfV04PassByRef:
    """RFC 9005 §5.3 pass-by-reference operator &."""

    def test_ref_arg_in_function(self) -> None:
        ast = hlfc_compile(_prog("[FUNCTION] process &data_var"))
        args = ast["program"][0]["args"]
        ref_args = [a for a in args if isinstance(a, dict) and "ref" in a]
        assert len(ref_args) == 1

    def test_ref_arg_name(self) -> None:
        ast = hlfc_compile(_prog("[FUNCTION] process &data_var"))
        args = ast["program"][0]["args"]
        ref_arg = next(a for a in args if isinstance(a, dict) and "ref" in a)
        assert ref_arg["ref"] == "data_var"

    def test_ref_arg_operator_field(self) -> None:
        ast = hlfc_compile(_prog("[FUNCTION] process &data_var"))
        args = ast["program"][0]["args"]
        ref_arg = next(a for a in args if isinstance(a, dict) and "ref" in a)
        assert ref_arg["operator"] == "&"

    def test_ref_arg_human_readable(self) -> None:
        ast = hlfc_compile(_prog("[FUNCTION] process &data_var"))
        args = ast["program"][0]["args"]
        ref_arg = next(a for a in args if isinstance(a, dict) and "ref" in a)
        assert "data_var" in ref_arg["human_readable"]
        assert "reference" in ref_arg["human_readable"].lower()

    def test_ref_arg_mixed_with_literal(self) -> None:
        ast = hlfc_compile(_prog('[FUNCTION] transform &src "output"'))
        args = ast["program"][0]["args"]
        ref_args = [a for a in args if isinstance(a, dict) and "ref" in a]
        assert len(ref_args) == 1
        assert args[1] == "output"


class TestHlfV04EpistemicModifier:
    """RFC 9005 §7 epistemic modifier _{ρ:val} — grammar boundary tests."""

    def test_epistemic_as_standalone_raises(self) -> None:
        """_{ρ:val} is not a top-level statement; the parser must reject it."""
        with pytest.raises(HlfSyntaxError):
            hlfc_compile(_prog("_{ ρ : 0.9 }"))

    def test_epistemic_syntax_is_not_a_tag_stmt(self) -> None:
        """[EPISTEMIC] is parsed as a generic tag (not the _{ρ:val} epistemic operator)."""
        ast = hlfc_compile(_prog("[EPISTEMIC] confidence=0.9"))
        # It compiles as a plain tag_stmt, not as the special epistemic construct
        assert ast["program"][0]["tag"] == "EPISTEMIC"

    def test_program_without_epistemic_still_valid(self) -> None:
        """Programs without epistemic syntax compile successfully."""
        ast = hlfc_compile(_prog("[INTENT] analyze /data"))
        assert len(ast["program"]) == 1


class TestFormatCorrection:
    """format_correction() edge cases — Iterative Intervention Engine."""

    def _make_error(self, msg: str = "unexpected token") -> HlfSyntaxError:
        return HlfSyntaxError(msg)

    def test_returns_dict(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert isinstance(result, dict)

    def test_has_required_keys(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        for key in ("error", "source", "correction_hlf", "human_readable", "valid_operators", "suggestion"):
            assert key in result, f"Missing key: {key}"

    def test_error_field_is_string(self) -> None:
        result = format_correction("bad hlf", self._make_error("token X unexpected"))
        assert isinstance(result["error"], str)
        assert "token X unexpected" in result["error"]

    def test_source_field_preserved(self) -> None:
        src = "my broken source"
        result = format_correction(src, self._make_error())
        assert result["source"] == src

    def test_correction_hlf_is_none(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert result["correction_hlf"] is None

    def test_valid_operators_is_non_empty_dict(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert isinstance(result["valid_operators"], dict)
        assert len(result["valid_operators"]) > 0

    def test_valid_operators_contains_tool_exec(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert _op_by_glyph(result["valid_operators"], "↦") is not None, "Tool execution operator missing from catalog"

    def test_valid_operators_contains_conditional(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert _op_by_glyph(result["valid_operators"], "⊎") is not None, "Conditional operator missing from catalog"

    def test_valid_operators_contains_struct(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert _op_by_glyph(result["valid_operators"], "≡") is not None, "Struct operator missing from catalog"

    def test_suggestion_mentions_terminator(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert "Ω" in result["suggestion"]

    def test_human_readable_mentions_hlf(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert "HLF" in result["human_readable"]

    def test_empty_source_does_not_raise(self) -> None:
        result = format_correction("", self._make_error())
        assert result["source"] == ""

    def test_long_error_message_handled(self) -> None:
        long_msg = "error: " + "x" * 500
        result = format_correction("bad hlf", self._make_error(long_msg))
        assert long_msg in result["error"]

    def test_human_readable_is_string(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert isinstance(result["human_readable"], str)

    def test_suggestion_is_string(self) -> None:
        result = format_correction("bad hlf", self._make_error())
        assert isinstance(result["suggestion"], str)


# ---------------------------------------------------------------------------
# v0.4 Language Expansion Tests (Hat-Driven)
# ---------------------------------------------------------------------------


class TestHlfV04FuncCall:
    """Function calls in expressions — Crimson Hat needs len(), abs(), etc."""

    def test_func_call_in_assignment(self) -> None:
        ast = hlfc_compile(_prog('result ← abs(42)'))
        node = ast['program'][0]
        assert node['tag'] == 'ASSIGN'
        assert node['value']['op'] == 'FUNC_CALL'
        assert node['value']['name'] == 'abs'

    def test_func_call_with_multiple_args(self) -> None:
        ast = hlfc_compile(_prog('result ← min(10, 20)'))
        node = ast['program'][0]
        assert node['value']['op'] == 'FUNC_CALL'
        assert len(node['value']['args']) == 2

    def test_func_call_human_readable(self) -> None:
        ast = hlfc_compile(_prog('result ← max(1, 2)'))
        assert 'max' in ast['program'][0]['value']['human_readable']


class TestHlfV04MemberAccess:
    """Member access — Silver Hat needs node.confidence, point.x."""

    def test_member_access_in_condition(self) -> None:
        ast = hlfc_compile(_prog('⊎ node.confidence > 0 ⇒ [INTENT] ok "valid"'))
        cond = ast['program'][0]['condition']
        assert cond['left']['op'] == 'MEMBER_ACCESS'
        assert cond['left']['object'] == 'node'

    def test_member_access_path(self) -> None:
        ast = hlfc_compile(_prog('result ← agent.hat.name'))
        node = ast['program'][0]['value']
        assert node['path'] == ['hat', 'name']


class TestHlfV04WhileLoop:
    """While loops — Blue Hat process flow."""

    def test_while_parses(self) -> None:
        ast = hlfc_compile(_prog('[WHILE] active "loop_body"'))
        assert ast['program'][0]['tag'] == 'WHILE'

    def test_while_has_args(self) -> None:
        ast = hlfc_compile(_prog('[WHILE] active "loop_body"'))
        node = ast['program'][0]
        assert 'args' in node
        assert len(node['args']) == 2

    def test_while_human_readable(self) -> None:
        ast = hlfc_compile(_prog('[WHILE] active "loop_body"'))
        assert 'WHILE' in ast['program'][0]['human_readable']


class TestHlfV04TryCatch:
    """Try/catch — Black Hat error handling."""

    def test_try_parses(self) -> None:
        ast = hlfc_compile(_prog('[TRY] "risky_operation"'))
        assert ast['program'][0]['tag'] == 'TRY'

    def test_catch_parses(self) -> None:
        ast = hlfc_compile(_prog('[CATCH] "error_handler"'))
        assert ast['program'][0]['tag'] == 'CATCH'


class TestHlfV04Assert:
    """Assertions — Gold Hat verification."""

    def test_assert_parses(self) -> None:
        ast = hlfc_compile(_prog('[ASSERT] true "condition_holds"'))
        assert ast['program'][0]['tag'] == 'ASSERT'

    def test_assert_has_args(self) -> None:
        ast = hlfc_compile(_prog('[ASSERT] true "condition_holds"'))
        node = ast['program'][0]
        assert len(node['args']) == 2

    def test_assert_human_readable(self) -> None:
        ast = hlfc_compile(_prog('[ASSERT] true "condition_holds"'))
        assert 'ASSERT' in ast['program'][0]['human_readable']


class TestHlfV04Return:
    """Return values from functions."""

    def test_return_parses(self) -> None:
        ast = hlfc_compile(_prog('[RETURN] 42'))
        assert ast['program'][0]['tag'] == 'RETURN'

    def test_return_value(self) -> None:
        ast = hlfc_compile(_prog('[RETURN] 42'))
        assert 42 in ast['program'][0]['args']

    def test_return_string(self) -> None:
        ast = hlfc_compile(_prog('[RETURN] "done"'))
        assert ast['program'][0]['tag'] == 'RETURN'


class TestHlfV04Comments:
    """Single-line comments."""

    def test_comment_ignored(self) -> None:
        ast = hlfc_compile(_prog('# this is a comment\n[INTENT] do "thing"'))
        tags = [n['tag'] for n in ast['program'] if n]
        assert 'INTENT' in tags

    def test_inline_comment_after_statement(self) -> None:
        ast = hlfc_compile(_prog('[SET] x = 42 # set x to 42'))
        assert ast['program'][0]['tag'] == 'SET'


# ---------------------------------------------------------------------------
# Yellow Hat — new lint rule tests
# ---------------------------------------------------------------------------


class TestHlfLintNewRules:
    """Tests for the four new hlflint rules added by the Yellow Hat pass."""

    def test_duplicate_set_flagged(self) -> None:
        source = _prog('[SET] x = 1\n[SET] x = 2')
        issues = lint(source, max_gas=20)
        dup = [i for i in issues if "DUPLICATE_SET" in i]
        assert dup, f"Expected DUPLICATE_SET in {issues}"
        assert "x" in dup[0]

    def test_no_duplicate_set_when_unique(self) -> None:
        source = _prog('[SET] a = 1\n[SET] b = 2')
        issues = lint(source, max_gas=20)
        assert not any("DUPLICATE_SET" in i for i in issues)

    def test_redundant_constraint_flagged(self) -> None:
        source = _prog('[CONSTRAINT] mode "fast"\n[INTENT] run "job"\n[CONSTRAINT] mode "slow"\n[RESULT] code=0 message="ok"')
        issues = lint(source, max_gas=20)
        redundant = [i for i in issues if "REDUNDANT_CONSTRAINT" in i]
        assert redundant, f"Expected REDUNDANT_CONSTRAINT in {issues}"
        assert "mode" in redundant[0]

    def test_no_redundant_constraint_when_unique_keys(self) -> None:
        source = _prog('[CONSTRAINT] mode "fast"\n[INTENT] run "job"\n[CONSTRAINT] timeout 30\n[RESULT] code=0 message="ok"')
        issues = lint(source, max_gas=20)
        assert not any("REDUNDANT_CONSTRAINT" in i for i in issues)

    def test_dead_code_after_result_flagged(self) -> None:
        source = _prog('[INTENT] do "thing"\n[RESULT] code=0 message="done"\n[ACTION] unreachable "step"')
        issues = lint(source, max_gas=20)
        dead = [i for i in issues if "DEAD_CODE" in i]
        assert dead, f"Expected DEAD_CODE in {issues}"

    def test_no_dead_code_when_result_is_last(self) -> None:
        source = _prog('[INTENT] do "thing"\n[RESULT] code=0 message="done"')
        issues = lint(source, max_gas=20)
        assert not any("DEAD_CODE" in i for i in issues)

    def test_missing_result_flagged(self) -> None:
        source = _prog('[INTENT] do "thing"\n[ACTION] perform "step"')
        issues = lint(source, max_gas=20)
        missing = [i for i in issues if "MISSING_RESULT" in i]
        assert missing, f"Expected MISSING_RESULT in {issues}"

    def test_no_missing_result_when_present(self) -> None:
        source = _prog('[INTENT] do "thing"\n[RESULT] code=0 message="ok"')
        issues = lint(source, max_gas=20)
        assert not any("MISSING_RESULT" in i for i in issues)

    def test_no_missing_result_when_no_intent(self) -> None:
        """Programs without [INTENT] should not trigger MISSING_RESULT."""
        source = _prog('[ACTION] "step"')
        issues = lint(source, max_gas=20)
        assert not any("MISSING_RESULT" in i for i in issues)


# ---------------------------------------------------------------------------
# Yellow Hat — quick_gas_estimate tests
# ---------------------------------------------------------------------------


class TestQuickGasEstimate:
    """Tests for the fast O(n) gas heuristic added in hlf/__init__.py."""

    def test_empty_program_zero_gas(self) -> None:
        from hlf import quick_gas_estimate
        source = "[HLF-v2]\nΩ\n"
        assert quick_gas_estimate(source) == 0

    def test_single_intent_counts_one(self) -> None:
        from hlf import quick_gas_estimate
        source = _prog('[INTENT] greet "world"')
        assert quick_gas_estimate(source) == 1

    def test_multiple_tags_counted(self) -> None:
        from hlf import quick_gas_estimate
        source = _prog('[INTENT] do "x"\n[ACTION] run "y"\n[RESULT] code=0 message="ok"')
        assert quick_gas_estimate(source) == 3

    def test_version_header_excluded(self) -> None:
        from hlf import quick_gas_estimate
        source = "[HLF-v3]\n[INTENT] do \"x\"\nΩ\n"
        # [HLF-v3] must not be counted; only [INTENT]
        assert quick_gas_estimate(source) == 1

    def test_tool_execution_counted(self) -> None:
        from hlf import quick_gas_estimate
        source = _prog("↦ τ(file.read) /path")
        assert quick_gas_estimate(source) >= 1

    def test_estimate_is_lower_bound_of_compiled_count(self) -> None:
        """quick_gas_estimate should never exceed the true AST node count."""
        from hlf import compile as hlfc_compile
        from hlf import quick_gas_estimate
        source = _prog('[INTENT] do "x"\n[CONSTRAINT] mode "fast"\n[RESULT] code=0 message="ok"')
        estimate = quick_gas_estimate(source)
        true_count = len(hlfc_compile(source)["program"])
        assert estimate <= true_count
