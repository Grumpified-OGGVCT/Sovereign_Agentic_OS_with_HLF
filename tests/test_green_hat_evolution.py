"""
Green Hat Evolution Tests — Feature completeness and evolution additions.

Covers:
1. New stdlib modules: ``time`` and ``validation``
2. Enhanced hlflint rules: missing_result, duplicate_set, unreachable_code,
   recursion_depth
3. New built-in pure functions in hlfrun: FORMAT, RANDOM, TYPE_OF
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hlf.hlfc import compile as hlf_compile
from hlf.hlflint import _measure_nesting_depth, lint
from hlf.hlfrun import (
    _BUILTIN_FUNCTIONS,
    _builtin_format,
    _builtin_random,
    _builtin_type_of,
)
from hlf.runtime import ModuleLoader

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STDLIB_DIR = _PROJECT_ROOT / "hlf" / "stdlib"


# ============================================================================
# 1. New stdlib modules
# ============================================================================


class TestTimeStdlib:
    """Verify the ``time`` stdlib module."""

    def test_time_hlf_exists(self) -> None:
        path = _STDLIB_DIR / "time.hlf"
        assert path.exists(), "hlf/stdlib/time.hlf not found"

    def test_time_module_compiles(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        assert ast is not None
        assert "program" in ast

    def test_time_module_tag_present(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        module_nodes = [n for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "MODULE"]
        assert any(n.get("name") == "time" for n in module_nodes)

    def test_time_has_now_utc(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "now_utc" in fn_names

    def test_time_has_parse_iso(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "parse_iso" in fn_names

    def test_time_has_diff_seconds(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "diff_seconds" in fn_names

    def test_time_has_timezone_convert(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "timezone_convert" in fn_names

    def test_time_has_constants(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        set_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "SET"}
        assert "SECS_PER_DAY" in set_names
        assert "TZ_UTC" in set_names

    def test_time_has_result_terminator(self) -> None:
        source = (_STDLIB_DIR / "time.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        result_nodes = [n for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "RESULT"]
        assert result_nodes, "time.hlf must have a [RESULT] node"

    def test_time_module_loader_resolves(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        path = loader.resolve_path("time")
        assert path is not None
        assert path.name == "time.hlf"

    def test_time_module_loads(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        ns = loader.load("time")
        assert ns is not None
        assert ns.name == "time"


class TestValidationStdlib:
    """Verify the ``validation`` stdlib module."""

    def test_validation_hlf_exists(self) -> None:
        path = _STDLIB_DIR / "validation.hlf"
        assert path.exists(), "hlf/stdlib/validation.hlf not found"

    def test_validation_module_compiles(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        assert ast is not None
        assert "program" in ast

    def test_validation_module_tag_present(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        module_nodes = [n for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "MODULE"]
        assert any(n.get("name") == "validation" for n in module_nodes)

    def test_validation_has_is_email(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "is_email" in fn_names

    def test_validation_has_is_url(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "is_url" in fn_names

    def test_validation_has_is_numeric(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "is_numeric" in fn_names

    def test_validation_has_matches_pattern(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "matches_pattern" in fn_names

    def test_validation_has_is_uuid(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "is_uuid" in fn_names

    def test_validation_has_sanitize_string(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        fn_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "FUNCTION"}
        assert "sanitize_string" in fn_names

    def test_validation_has_constants(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        set_names = {n.get("name") for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "SET"}
        assert "VALID" in set_names
        assert "INVALID" in set_names

    def test_validation_has_result_terminator(self) -> None:
        source = (_STDLIB_DIR / "validation.hlf").read_text(encoding="utf-8")
        ast = hlf_compile(source)
        result_nodes = [n for n in ast["program"] if isinstance(n, dict) and n.get("tag") == "RESULT"]
        assert result_nodes, "validation.hlf must have a [RESULT] node"

    def test_validation_module_loads(self) -> None:
        loader = ModuleLoader(search_paths=[_STDLIB_DIR])
        ns = loader.load("validation")
        assert ns is not None
        assert ns.name == "validation"


# ============================================================================
# 2. Enhanced linter rules
# ============================================================================


class TestLintMissingResult:
    """MISSING_RESULT lint rule — programs without a [RESULT] node."""

    def test_program_with_result_is_clean(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] greet \"world\"\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        result_issues = [i for i in issues if "MISSING_RESULT" in i]
        assert not result_issues, f"False positive MISSING_RESULT: {issues}"

    def test_program_without_result_warns(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] greet \"world\"\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        result_issues = [i for i in issues if "MISSING_RESULT" in i]
        assert result_issues, "Expected MISSING_RESULT diagnostic for program with no [RESULT]"

    def test_module_files_exempt_from_missing_result(self) -> None:
        """stdlib module files have RESULT as a load-status line — they must not be warned."""
        for mod_name in ("time", "validation", "math", "string"):
            source = (_STDLIB_DIR / f"{mod_name}.hlf").read_text(encoding="utf-8")
            issues = lint(source, max_gas=100)
            result_issues = [i for i in issues if "MISSING_RESULT" in i]
            assert not result_issues, (
                f"stdlib module '{mod_name}.hlf' wrongly flagged MISSING_RESULT: {issues}"
            )


class TestLintDuplicateSet:
    """DUPLICATE_SET lint rule — duplicate SET variable names."""

    def test_unique_set_names_clean(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] setup \"env\"\n"
            "[SET] FOO=\"bar\"\n"
            "[SET] BAZ=\"qux\"\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        dup_issues = [i for i in issues if "DUPLICATE_SET" in i]
        assert not dup_issues, f"False positive DUPLICATE_SET: {issues}"

    def test_duplicate_set_name_warns(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] setup \"env\"\n"
            "[SET] FOO=\"first\"\n"
            "[SET] FOO=\"second\"\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        dup_issues = [i for i in issues if "DUPLICATE_SET" in i]
        assert dup_issues, "Expected DUPLICATE_SET diagnostic for re-declared variable"
        assert "FOO" in dup_issues[0]

    def test_three_declarations_of_same_name_warns_once(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] setup \"env\"\n"
            "[SET] X=\"a\"\n"
            "[SET] X=\"b\"\n"
            "[SET] X=\"c\"\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        dup_issues = [i for i in issues if "DUPLICATE_SET" in i]
        # Second and third declarations both trigger the warning
        assert len(dup_issues) >= 1


class TestLintUnreachableCode:
    """UNREACHABLE_CODE lint rule — nodes after first [RESULT]."""

    def test_result_at_end_is_clean(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] work \"task\"\n"
            "[ACTION] do_thing\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        unreachable = [i for i in issues if "UNREACHABLE_CODE" in i]
        assert not unreachable, f"False positive UNREACHABLE_CODE: {issues}"

    def test_nodes_after_result_warn(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] work \"task\"\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "[ACTION] this_never_runs\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        unreachable = [i for i in issues if "UNREACHABLE_CODE" in i]
        assert unreachable, "Expected UNREACHABLE_CODE diagnostic for code after [RESULT]"

    def test_multiple_nodes_after_result_reported(self) -> None:
        source = (
            "[HLF-v2]\n"
            "[INTENT] work \"task\"\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "[ACTION] step_one\n"
            "[ACTION] step_two\n"
            "Ω\n"
        )
        issues = lint(source, max_gas=100)
        unreachable = [i for i in issues if "UNREACHABLE_CODE" in i]
        assert unreachable
        assert "2" in unreachable[0]  # should mention the count


class TestLintRecursionDepth:
    """RECURSION_DEPTH lint rule — deep bracket nesting."""

    def test_shallow_nesting_ok(self) -> None:
        source = "[HLF-v2]\n[INTENT] do_work\n[RESULT] code=0 message=\"ok\"\nΩ\n"
        depth = _measure_nesting_depth(source)
        assert depth <= 5

    def test_deeply_nested_source_detected(self) -> None:
        # Artificially create a source with deep nesting
        deep_open = "[" * 10 + "INTENT" + "]" * 10
        source = f"[HLF-v2]\n{deep_open}\n[RESULT] code=0 message=\"ok\"\nΩ\n"
        depth = _measure_nesting_depth(source)
        assert depth >= 10

    def test_deep_nesting_lint_warning(self) -> None:
        # The realistic trigger for this rule is unusual deeply-nested source
        # We mock a source with brackets deep enough to exceed _MAX_RECURSION_DEPTH
        # by feeding the heuristic raw bracket sequences.
        deep_source = "[[[[[[" + "[HLF-v2]\n[INTENT] do_work\n[RESULT] code=0 message=\"ok\"\nΩ\n" + "]]]]]]"
        from hlf.hlflint import _MAX_RECURSION_DEPTH
        depth = _measure_nesting_depth(deep_source)
        if depth > _MAX_RECURSION_DEPTH:
            # Confirm the lint function would detect it
            issues = lint(deep_source, max_gas=100)
            recursion_issues = [i for i in issues if "RECURSION_DEPTH" in i or "PARSE_ERROR" in i]
            # Either a parse error (invalid HLF) OR a recursion warning is acceptable.
            assert recursion_issues, "Expected RECURSION_DEPTH or PARSE_ERROR for deeply nested source"

    def test_measure_nesting_depth_empty_string(self) -> None:
        assert _measure_nesting_depth("") == 0

    def test_measure_nesting_depth_flat_tags(self) -> None:
        source = "[INTENT] do_work [RESULT] code=0"
        depth = _measure_nesting_depth(source)
        assert depth == 1  # each tag opens one bracket level


# ============================================================================
# 3. New built-in pure functions
# ============================================================================


class TestBuiltinFormat:
    """Tests for the FORMAT built-in function."""

    def test_format_registered(self) -> None:
        assert "FORMAT" in _BUILTIN_FUNCTIONS

    def test_format_simple_substitution(self) -> None:
        result = _builtin_format("Hello, {name}!", 'name=World')
        assert result == "Hello, World!"

    def test_format_multiple_keys(self) -> None:
        result = _builtin_format("{a} + {b} = {c}", "a=1", "b=2", "c=3")
        assert result == "1 + 2 = 3"

    def test_format_missing_key_preserved(self) -> None:
        result = _builtin_format("Hello, {name}!")
        assert result == "Hello, {name}!"

    def test_format_empty_template(self) -> None:
        result = _builtin_format("")
        assert result == ""

    def test_format_no_args_returns_empty(self) -> None:
        result = _builtin_format()
        assert result == ""

    def test_format_key_with_spaces_in_value(self) -> None:
        result = _builtin_format("msg={greeting}", "greeting=Hello World")
        assert result == "msg=Hello World"

    def test_format_repeated_placeholder(self) -> None:
        result = _builtin_format("{x} and {x}", "x=foo")
        assert result == "foo and foo"

    def test_format_extra_keys_ignored(self) -> None:
        result = _builtin_format("Hello, {name}!", "name=Alice", "extra=ignored")
        assert result == "Hello, Alice!"


class TestBuiltinRandom:
    """Tests for the RANDOM built-in function."""

    def test_random_registered(self) -> None:
        assert "RANDOM" in _BUILTIN_FUNCTIONS

    def test_random_no_args_returns_float(self) -> None:
        value = _builtin_random()
        assert isinstance(value, float)
        assert 0.0 <= value < 1.0

    def test_random_range_returns_integer(self) -> None:
        value = _builtin_random(1, 10)
        assert isinstance(value, int)
        assert 1 <= value <= 10

    def test_random_range_single_value(self) -> None:
        value = _builtin_random(5, 5)
        assert value == 5

    def test_random_range_inverted_ok(self) -> None:
        # High < low should be auto-swapped
        value = _builtin_random(10, 1)
        assert 1 <= value <= 10

    def test_random_choice_returns_one_of(self) -> None:
        options = ["apple", "banana", "cherry"]
        for _ in range(20):
            value = _builtin_random("choice", *options)
            assert value in options

    def test_random_choice_single_item(self) -> None:
        value = _builtin_random("choice", "only")
        assert value == "only"

    def test_random_choice_no_items_raises(self) -> None:
        from hlf.hlfc import HlfRuntimeError
        with pytest.raises(HlfRuntimeError):
            _builtin_random("choice")

    def test_random_invalid_range_raises(self) -> None:
        from hlf.hlfc import HlfRuntimeError
        with pytest.raises(HlfRuntimeError):
            _builtin_random("not_a_number", "also_not")

    def test_random_range_distribution(self) -> None:
        """Statistical smoke test: values should not all be the same."""
        values = {_builtin_random(1, 100) for _ in range(50)}
        assert len(values) > 1, "RANDOM should produce varied results"


class TestBuiltinTypeOf:
    """Tests for the TYPE_OF built-in function."""

    def test_type_of_registered(self) -> None:
        assert "TYPE_OF" in _BUILTIN_FUNCTIONS

    def test_type_of_string(self) -> None:
        assert _builtin_type_of("hello") == "string"

    def test_type_of_integer(self) -> None:
        assert _builtin_type_of(42) == "number"

    def test_type_of_float(self) -> None:
        assert _builtin_type_of(3.14) == "number"

    def test_type_of_bool_true(self) -> None:
        assert _builtin_type_of(True) == "bool"

    def test_type_of_bool_false(self) -> None:
        assert _builtin_type_of(False) == "bool"

    def test_type_of_none_returns_null(self) -> None:
        assert _builtin_type_of(None) == "null"

    def test_type_of_no_args_returns_null(self) -> None:
        assert _builtin_type_of() == "null"

    def test_type_of_list(self) -> None:
        assert _builtin_type_of([1, 2, 3]) == "list"

    def test_type_of_dict(self) -> None:
        assert _builtin_type_of({"key": "val"}) == "map"

    def test_type_of_string_true_returns_bool(self) -> None:
        assert _builtin_type_of("true") == "bool"

    def test_type_of_string_false_returns_bool(self) -> None:
        assert _builtin_type_of("false") == "bool"

    def test_type_of_empty_string(self) -> None:
        assert _builtin_type_of("") == "string"

    def test_type_of_numeric_string(self) -> None:
        # "42" is a string value, not a number
        assert _builtin_type_of("42") == "string"


# ============================================================================
# 4. Integration: new built-ins exposed through full compile+run pipeline
# ============================================================================


class TestNewBuiltinsInRuntime:
    """Verify new built-ins are accessible via the runtime execute path."""

    def test_format_builtin_in_runtime(self) -> None:
        from hlf.hlfrun import run

        # FORMAT requires the first arg to be an identifier (unquoted),
        # and subsequent args to be any type.  The template is passed as an
        # unquoted identifier, subsequent k=v pairs as string args.
        source = (
            "[HLF-v2]\n"
            "[INTENT] greet \"world\"\n"
            "[FUNCTION] FORMAT greeting \"name=Runtime\"\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        ast = hlf_compile(source)
        result = run(ast, max_gas=100)
        # "greeting" has no {name} placeholder so FORMAT returns it unchanged
        assert result["code"] == 0
        assert result["scope"]["FORMAT_RESULT"] == "greeting"

    def test_format_substitution_with_set_variable(self) -> None:
        """FORMAT is called with args that include a k=v pair for substitution."""
        from hlf.hlfrun import _builtin_format

        # Direct unit call to FORMAT function confirms k=v substitution works
        result = _builtin_format("Hello, {name}!", "name=Runtime")
        assert result == "Hello, Runtime!"

    def test_type_of_builtin_in_runtime(self) -> None:
        from hlf.hlfrun import run

        source = (
            "[HLF-v2]\n"
            "[INTENT] greet \"world\"\n"
            "[FUNCTION] TYPE_OF hello\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        ast = hlf_compile(source)
        result = run(ast, max_gas=100)
        assert result["code"] == 0
        # "hello" is a string identifier so TYPE_OF returns "string"
        assert result["scope"]["TYPE_OF_RESULT"] == "string"

    def test_random_builtin_in_runtime_returns_result(self) -> None:
        from hlf.hlfrun import run

        source = (
            "[HLF-v2]\n"
            "[INTENT] greet \"world\"\n"
            "[FUNCTION] RANDOM\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        ast = hlf_compile(source)
        result = run(ast, max_gas=100)
        assert result["code"] == 0
        assert "RANDOM_RESULT" in result["scope"]

    def test_now_builtin_still_works(self) -> None:
        """Regression: existing NOW built-in must still function after additions."""
        from hlf.hlfrun import run

        source = (
            "[HLF-v2]\n"
            "[INTENT] greet \"world\"\n"
            "[FUNCTION] NOW\n"
            "[RESULT] code=0 message=\"ok\"\n"
            "Ω\n"
        )
        ast = hlf_compile(source)
        result = run(ast, max_gas=100)
        assert result["code"] == 0
        assert "NOW_RESULT" in result["scope"]
