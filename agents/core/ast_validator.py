"""
AST Validator — scans generated Python code for dangerous AST patterns.
Rejects: os.system, subprocess.*, eval(), exec().
"""

from __future__ import annotations

import ast

_FORBIDDEN_CALLS = {
    ("os", "system"),
    ("subprocess", "call"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "check_output"),
    ("subprocess", "check_call"),
}

_FORBIDDEN_BUILTINS = {"eval", "exec"}


class _ViolationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        # eval() / exec()
        if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_BUILTINS:
            self.violations.append(f"Forbidden call: {node.func.id}() at line {node.lineno}")
        # module.func()
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            pair = (node.func.value.id, node.func.attr)
            if pair in _FORBIDDEN_CALLS:
                self.violations.append(f"Forbidden call: {node.func.value.id}.{node.func.attr}() at line {node.lineno}")
        self.generic_visit(node)


def validate_code(source: str) -> tuple[bool, list[str]]:
    """
    Validate Python source code for forbidden patterns.
    Returns (is_safe: bool, violations: list[str]).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, [f"SyntaxError: {exc}"]
    visitor = _ViolationVisitor()
    visitor.visit(tree)
    return len(visitor.violations) == 0, visitor.violations
