"""
HLF Runtime Interpreter — Full Execution Engine.

Executes a compiled JSON AST (output of hlfc.compile()) against the registered
built-in pure functions and the host-function backends defined in
governance/host_functions.json.

Supports ALL 13+ statement types:
  - Tag stmts (INTENT, CONSTRAINT, EXPECT, etc.) — structural acknowledgement
  - SET — immutable variable binding
  - ASSIGN (←) — mutable variable assignment, supports math expressions
  - FUNCTION — pure built-in function call
  - ACTION — host function dispatch
  - RESULT — error-code propagation + program termination
  - CONDITIONAL (⊎ ⇒ ⇌) — if/then/else branching
  - PARALLEL (∥) — concurrent task execution
  - SYNC (⋈) — synchronization barrier
  - STRUCT (≡) — struct type definition
  - GLYPH_MODIFIED (⌘ Ж ∇ ⩕ ⨝ Δ ~ §) — modifier dispatch + inner execution
  - TOOL (↦ τ) — host function routing
  - IMPORT — module loading (delegated to runtime.py)

Built-in pure functions (no I/O, deterministic):
  [FUNCTION] HASH sha256 <text>         → scope["HASH_RESULT"]
  [FUNCTION] BASE64_ENCODE <text>       → scope["BASE64_ENCODE_RESULT"]
  [FUNCTION] BASE64_DECODE <text>       → scope["BASE64_DECODE_RESULT"]
  [FUNCTION] NOW                        → scope["NOW_RESULT"]  (ISO-8601 UTC)
  [FUNCTION] UUID                       → scope["UUID_RESULT"]
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from hlf.hlfc import HlfRuntimeError

logger = logging.getLogger(__name__)

# Regex for ${VAR} expansion at runtime (supplements compile-time Pass 2)
_VAR_RE = re.compile(r"\$\{(\w+)\}")


# --------------------------------------------------------------------------- #
# Built-in pure functions
# --------------------------------------------------------------------------- #


def _builtin_hash(*args: Any) -> str:
    """HASH <algorithm> <text> → hex digest. Only sha256 supported."""
    parts = [str(a) for a in args]
    algo = parts[0].lower() if len(parts) >= 2 else "sha256"
    text = parts[1] if len(parts) >= 2 else (parts[0] if parts else "")
    if algo != "sha256":
        raise HlfRuntimeError(f"HASH: unsupported algorithm '{algo}' (only sha256 supported)")
    return hashlib.sha256(text.encode()).hexdigest()


def _builtin_base64_encode(*args: Any) -> str:
    text = str(args[0]) if args else ""
    return base64.b64encode(text.encode()).decode()


def _builtin_base64_decode(*args: Any) -> str:
    text = str(args[0]) if args else ""
    return base64.b64decode(text.encode()).decode()


def _builtin_now(*_args: Any) -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _builtin_uuid(*_args: Any) -> str:
    return str(uuid.uuid4())


_BUILTIN_FUNCTIONS: dict[str, Any] = {
    "HASH": _builtin_hash,
    "BASE64_ENCODE": _builtin_base64_encode,
    "BASE64_DECODE": _builtin_base64_decode,
    "NOW": _builtin_now,
    "UUID": _builtin_uuid,
}


# --------------------------------------------------------------------------- #
# Expression evaluator — math, comparisons, and logic
# --------------------------------------------------------------------------- #


def _eval_expr(node: Any, scope: dict[str, Any]) -> Any:
    """Recursively evaluate an expression node from the HLF AST.

    Handles:
      - Literal values (int, float, str, bool)
      - Variable references (strings that exist in scope)
      - ${VAR} string expansion
      - Math nodes: {"op": "MATH", "operator": "+", "left": ..., "right": ...}
      - Comparison nodes: {"op": "COMPARE", "operator": "==", ...}
      - Logic nodes: {"op": "AND"/"OR"/"NOT", ...}
      - Pass-by-reference nodes: {"ref": "name", "operator": "&"}
    """
    if node is None:
        return None

    # Literal values pass through
    if isinstance(node, (int, float, bool)):
        return node

    # String — expand ${VAR} refs from scope
    if isinstance(node, str):
        def _replace(m: re.Match) -> str:
            return str(scope.get(m.group(1), m.group(0)))

        expanded = _VAR_RE.sub(_replace, node)
        # If it exactly matches a scope key, return the actual value (not stringified)
        if expanded in scope:
            return scope[expanded]
        return expanded

    # List — evaluate each element
    if isinstance(node, list):
        return [_eval_expr(item, scope) for item in node]

    if not isinstance(node, dict):
        return node

    op = node.get("op", "")

    # Pass-by-reference: dereference from scope
    if "ref" in node and node.get("operator") == "&":
        ref_name = node["ref"]
        if ref_name in scope:
            return scope[ref_name]
        return node  # return as-is if not yet bound

    # Math expression: +, -, *, /
    if op == "MATH":
        left = _eval_expr(node.get("left"), scope)
        right = _eval_expr(node.get("right"), scope)
        operator = node.get("operator", "+")
        # Coerce to numeric
        left = _to_number(left)
        right = _to_number(right)
        if operator == "+":
            return left + right
        elif operator == "-":
            return left - right
        elif operator == "*":
            return left * right
        elif operator == "/":
            if right == 0:
                raise HlfRuntimeError("Division by zero")
            return left / right
        else:
            raise HlfRuntimeError(f"Unknown math operator: {operator}")

    # Comparison: ==, !=, >, <, >=, <=
    if op == "COMPARE":
        left = _eval_expr(node.get("left"), scope)
        right = _eval_expr(node.get("right"), scope)
        operator = node.get("operator", "==")
        if operator == "==":
            return left == right
        elif operator == "!=":
            return left != right
        elif operator == ">":
            return _to_number(left) > _to_number(right)
        elif operator == "<":
            return _to_number(left) < _to_number(right)
        elif operator == ">=":
            return _to_number(left) >= _to_number(right)
        elif operator == "<=":
            return _to_number(left) <= _to_number(right)
        else:
            raise HlfRuntimeError(f"Unknown comparison operator: {operator}")

    # Logic: AND (∩), OR (∪)
    if op == "AND":
        left = _eval_expr(node.get("left"), scope)
        right = _eval_expr(node.get("right"), scope)
        return bool(left) and bool(right)

    if op == "OR":
        left = _eval_expr(node.get("left"), scope)
        right = _eval_expr(node.get("right"), scope)
        return bool(left) or bool(right)

    # Logic: NOT (¬)
    if op == "NOT":
        operand = _eval_expr(node.get("operand"), scope)
        return not bool(operand)

    # If it has a "tag", it's a sub-statement node — return as-is for execution
    if "tag" in node:
        return node

    # Fallback — return as-is
    return node


def _to_number(value: Any) -> int | float:
    """Coerce a value to a number for math operations."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                raise HlfRuntimeError(f"Cannot coerce '{value}' to number") from None
    if isinstance(value, bool):
        return int(value)
    raise HlfRuntimeError(f"Cannot coerce {type(value).__name__} to number")


# --------------------------------------------------------------------------- #
# Glyph modifier semantics
# --------------------------------------------------------------------------- #

# Glyph modifiers and their semantic effects on execution
_GLYPH_SEMANTICS: dict[str, dict[str, Any]] = {
    "EXECUTE": {"glyph": "⌘", "priority": "high", "description": "Orchestrator directive — high-priority execution"},
    "CONSTRAINT": {"glyph": "Ж", "priority": "normal", "description": "Reasoning blocker — constraint enforcement"},
    "PARAMETER": {"glyph": "∇", "priority": "normal", "description": "Parameter/gradient binding"},
    "PRIORITY": {"glyph": "⩕", "priority": "critical", "description": "Gas metric — priority override"},
    "JOIN": {"glyph": "⨝", "priority": "normal", "description": "Matrix consensus — multi-agent sync"},
    "DELTA": {"glyph": "Δ", "priority": "normal", "description": "State diff — delta-only output"},
    "TILDE": {"glyph": "~", "priority": "normal", "description": "Aesthetic modifier"},
    "SECTION": {"glyph": "§", "priority": "normal", "description": "Expression section"},
}


# --------------------------------------------------------------------------- #
# Runtime interpreter
# --------------------------------------------------------------------------- #


class HLFInterpreter:
    """
    Walks a compiled HLF AST node-by-node and executes each statement.

    Supports all 13+ statement types including conditionals, parallel execution,
    sync barriers, struct definitions, assignment, glyph modifiers, and tool dispatch.

    :param scope:    Pre-seeded variable bindings (SET values from compile Pass 1
                     are already expanded by hlfc.compile(); the runtime scope is
                     used for FUNCTION result storage and runtime SET evaluation).
    :param tier:     Deployment tier — enforced by the host function dispatcher.
    :param max_gas:  Per-intent hard gas cap (AST node executions).
    """

    def __init__(
        self,
        scope: dict[str, Any] | None = None,
        tier: str = "hearth",
        max_gas: int = 10,
    ) -> None:
        self.scope: dict[str, Any] = dict(scope or {})
        self.tier = tier
        self.max_gas = max_gas
        self.gas_used: int = 0
        self._result_code: int | None = None
        self._result_message: str = ""
        self._result_value: Any = None
        # Struct type registry: name → {fields: [...]}
        self._structs: dict[str, dict[str, Any]] = {}
        # Macro registry: name → {body: [ast_nodes]}
        self._macros: dict[str, list[dict]] = {}
        # Parallel task results: task_name → result
        self._parallel_results: dict[str, Any] = {}
        # Glyph modifier stack for nested glyph tracking
        self._active_glyphs: list[str] = []
        # Memory engine (optional — inject for Infinite RAG integration)
        self._memory_engine: Any = None
        # Execution trace for debugging
        self._trace: list[dict[str, Any]] = []
        # Living Spec registry (Instinct integration)
        self._spec_registry: dict[str, dict[str, Any]] = {}
        self._spec_sealed: bool = False

    def execute(self, ast: dict) -> dict:
        """
        Execute the program in *ast* and return a result dict::

            {
                "code":     int,   # 0 = success
                "message":  str,
                "scope":    dict,  # final variable bindings
                "gas_used": int,
                "result":   Any,   # last ACTION/FUNCTION return value
                "structs":  dict,  # registered struct types
                "parallel_results": dict,  # parallel task outputs
                "trace":    list,  # execution trace
            }
        """
        program = ast.get("program", [])
        for node in program:
            if node is None:
                continue
            if self.gas_used >= self.max_gas:
                raise HlfRuntimeError(f"Gas limit exceeded: {self.gas_used}/{self.max_gas}")
            self.gas_used += 1
            self._execute_node(node)
            if self._result_code is not None:
                break  # [RESULT] terminates execution

        return {
            "code": self._result_code if self._result_code is not None else 0,
            "message": self._result_message or "ok",
            "scope": dict(self.scope),
            "gas_used": self.gas_used,
            "result": self._result_value,
            "structs": dict(self._structs),
            "macros": list(self._macros.keys()),
            "parallel_results": dict(self._parallel_results),
            "trace": list(self._trace),
            "spec_registry": dict(self._spec_registry),
            "spec_sealed": self._spec_sealed,
        }

    # ------------------------------------------------------------------ #
    # Node dispatchers
    # ------------------------------------------------------------------ #

    def _execute_node(self, node: dict) -> Any:
        """Dispatch execution based on the node's tag."""
        tag = node.get("tag", "")
        result = None

        if tag == "FUNCTION":
            result = self._exec_function(node)
        elif tag == "ACTION":
            result = self._exec_action(node)
        elif tag == "SET":
            self._exec_set(node)
        elif tag == "ASSIGN":
            self._exec_assign(node)
        elif tag == "RESULT":
            self._exec_result(node)
        elif tag == "CONDITIONAL":
            result = self._exec_conditional(node)
        elif tag == "PARALLEL":
            result = self._exec_parallel(node)
        elif tag == "SYNC":
            result = self._exec_sync(node)
        elif tag == "STRUCT":
            self._exec_struct(node)
        elif tag == "GLYPH_MODIFIED":
            result = self._exec_glyph_modified(node)
        elif tag == "TOOL":
            result = self._exec_tool(node)
        elif tag == "OPENCLAW_TOOL":
            result = self._exec_openclaw_tool(node)
        elif tag == "IMPORT":
            self._exec_import(node)
        elif tag == "MEMORY":
            result = self._exec_memory(node)
        elif tag == "RECALL":
            result = self._exec_recall(node)
        elif tag == "DEFINE":
            self._exec_define(node)
        elif tag == "CALL":
            result = self._exec_call(node)
        elif tag in (
            "INTENT", "CONSTRAINT", "EXPECT", "OBSERVATION",
            "THOUGHT", "PLAN", "DELEGATE", "VOTE", "ASSERT",
            "MODULE", "DATA", "EPISTEMIC",
        ):
            # Structural/declarative tags — log and continue
            self._trace.append({
                "tag": tag,
                "human_readable": node.get("human_readable", ""),
                "action": "acknowledged",
            })
        elif tag == "SPEC_DEFINE":
            self._exec_spec_define(node)
        elif tag == "SPEC_GATE":
            self._exec_spec_gate(node)
        elif tag == "SPEC_UPDATE":
            self._exec_spec_update(node)
        elif tag == "SPEC_SEAL":
            self._exec_spec_seal(node)
        else:
            # Unknown tag — log warning but don't crash
            logger.warning(f"HLF Runtime: unrecognized tag '{tag}', skipping")
            self._trace.append({"tag": tag, "action": "skipped_unknown"})

        return result

    # ------------------------------------------------------------------ #
    # Statement executors
    # ------------------------------------------------------------------ #

    def _exec_set(self, node: dict) -> None:
        """Execute SET — immutable variable binding."""
        name = node.get("name", "")
        value = _eval_expr(node.get("value", ""), self.scope)
        self.scope[name] = value
        self._trace.append({"tag": "SET", "name": name, "value": value})

    def _exec_assign(self, node: dict) -> None:
        """Execute ASSIGN (←) — mutable variable assignment with expression evaluation."""
        name = node.get("name", "")
        raw_value = node.get("value")
        value = _eval_expr(raw_value, self.scope)
        self.scope[name] = value
        self._trace.append({"tag": "ASSIGN", "name": name, "value": value, "operator": "←"})

    def _exec_function(self, node: dict) -> Any:
        """Execute FUNCTION — pure built-in function call."""
        name = (node.get("name") or "").upper()
        args = self._flatten_args(node.get("args", []))
        fn = _BUILTIN_FUNCTIONS.get(name)
        if fn is None:
            raise HlfRuntimeError(f"Unknown built-in function: {name}")
        result = fn(*args)
        # Store as ${NAME_RESULT} so subsequent tags can reference it
        self.scope[f"{name}_RESULT"] = result
        self._result_value = result
        self._trace.append({"tag": "FUNCTION", "name": name, "result": result})
        return result

    def _exec_action(self, node: dict) -> Any:
        """Execute ACTION — host function dispatch."""
        args = self._flatten_args(node.get("args", []))
        if not args:
            raise HlfRuntimeError("[ACTION] requires at least one argument (verb)")
        verb = str(args[0]).upper()
        rest = args[1:]
        try:
            from agents.core.host_function_dispatcher import dispatch
            result = dispatch(verb, rest, self.tier)
        except ImportError:
            # Fallback: log the action without dispatch (for standalone use)
            result = {"action": verb, "args": rest, "status": "dispatch_unavailable"}
        self.scope[f"{verb}_RESULT"] = result
        self._result_value = result
        self._trace.append({"tag": "ACTION", "verb": verb, "result": result})
        return result

    def _exec_result(self, node: dict) -> None:
        """Execute RESULT — set result code/message and terminate execution."""
        code = node.get("code")
        message = node.get("message")

        # Handle args-based RESULT format (keyword form: code=0 message="ok")
        if code is None or message is None:
            for arg in node.get("args", []):
                if isinstance(arg, dict):
                    if "code" in arg and code is None:
                        code = int(arg["code"])
                    if "message" in arg and message is None:
                        message = str(arg["message"])

        # Apply defaults after all extraction attempts
        if code is None:
            code = 0
        if message is None:
            message = "ok"

        self._result_code = int(code)
        self._result_message = str(message)
        self._trace.append({"tag": "RESULT", "code": code, "message": message})

    def _exec_conditional(self, node: dict) -> Any:
        """Execute CONDITIONAL (⊎ ⇒ ⇌) — evaluate condition and branch."""
        condition = node.get("condition")
        then_branch = node.get("then")
        else_branch = node.get("else")

        # Evaluate the condition expression
        condition_result = _eval_expr(condition, self.scope)

        self._trace.append({
            "tag": "CONDITIONAL",
            "condition_result": bool(condition_result),
            "operator": "⊎ ⇒ ⇌",
        })

        if condition_result:
            if then_branch:
                self.gas_used += 1
                return self._execute_node(then_branch)
        else:
            if else_branch:
                self.gas_used += 1
                return self._execute_node(else_branch)

        return None

    def _exec_parallel(self, node: dict) -> list[Any]:
        """Execute PARALLEL (∥) — run tasks concurrently."""
        tasks = node.get("tasks", [])
        results = []

        self._trace.append({
            "tag": "PARALLEL",
            "task_count": len(tasks),
            "operator": "∥",
        })

        if not tasks:
            return results

        def _run_task(task_node: dict) -> Any:
            """Execute a single task in the parallel set."""
            # Each task gets a copy of scope (isolation)
            sub = HLFInterpreter(
                scope=dict(self.scope),
                tier=self.tier,
                max_gas=self.max_gas - self.gas_used,
            )
            sub_result = sub.execute({"program": [task_node]})
            return sub_result

        # Use ThreadPoolExecutor for parallel execution
        with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as executor:
            futures = {executor.submit(_run_task, task): i for i, task in enumerate(tasks)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    task_result = future.result()
                    results.append(task_result)
                    # Track gas from sub-interpreters
                    self.gas_used += task_result.get("gas_used", 0)
                    # Store result by task tag/name if available
                    task_tag = tasks[idx].get("tag", f"task_{idx}")
                    task_name = tasks[idx].get("args", [task_tag])[0] if tasks[idx].get("args") else task_tag
                    self._parallel_results[str(task_name)] = task_result
                except Exception as e:
                    results.append({"error": str(e), "task_index": idx})

        self._result_value = results
        return results

    def _exec_sync(self, node: dict) -> Any:
        """Execute SYNC (⋈) — wait for referenced parallel tasks, then execute action."""
        refs = node.get("refs", [])
        action = node.get("action")

        self._trace.append({
            "tag": "SYNC",
            "refs": refs,
            "operator": "⋈",
        })

        # Check that all referenced tasks have completed
        missing = [ref for ref in refs if ref not in self._parallel_results]
        if missing:
            # Refs not found — they may reference future parallel tasks or
            # are symbolic references. Log as warning, continue with action.
            logger.warning(f"SYNC: refs not found in parallel results: {missing}")

        # Merge all referenced task scopes into current scope
        for ref in refs:
            if ref in self._parallel_results:
                task_result = self._parallel_results[ref]
                if isinstance(task_result, dict) and "scope" in task_result:
                    # Prefix with ref name to avoid collisions
                    for key, value in task_result["scope"].items():
                        self.scope[f"{ref}.{key}"] = value

        # Execute the continuation action
        if action:
            self.gas_used += 1
            return self._execute_node(action)

        return None

    def _exec_struct(self, node: dict) -> None:
        """Execute STRUCT (≡) — register struct type definition."""
        name = node.get("name", "")
        fields = node.get("fields", [])

        self._structs[name] = {
            "fields": fields,
            "field_names": [f.get("name", "") for f in fields],
            "field_types": {f.get("name", ""): f.get("type_name", "Any") for f in fields},
        }

        self._trace.append({
            "tag": "STRUCT",
            "name": name,
            "fields": [f.get("name", "") for f in fields],
            "operator": "≡",
        })

    def _exec_glyph_modified(self, node: dict) -> Any:
        """Execute GLYPH_MODIFIED — apply modifier semantics, then execute inner statement."""
        glyph = node.get("glyph", "")
        glyph_name = node.get("glyph_name", "")
        inner = node.get("inner")

        self._active_glyphs.append(glyph_name)

        self._trace.append({
            "tag": "GLYPH_MODIFIED",
            "glyph": glyph,
            "glyph_name": glyph_name,
            "action": "modifier_applied",
        })

        # Apply glyph-specific semantics
        semantics = _GLYPH_SEMANTICS.get(glyph_name, {})  # noqa: F841 — pre-staged for glyph dispatch

        # PRIORITY (⩕) — override gas budget if specified
        if glyph_name == "PRIORITY":
            # Priority tasks get a gas bonus
            self.max_gas = max(self.max_gas, self.max_gas + 5)

        # CONSTRAINT (Ж) — enforce constraint on inner execution
        # (Constraint enforcement is declarative — logged, inner still executes)

        # JOIN (⨝) — mark that consensus is required
        if glyph_name == "JOIN":
            self.scope["_consensus_required"] = True

        # DELTA (Δ) — mark delta-only mode
        if glyph_name == "DELTA":
            self.scope["_delta_mode"] = True

        # Execute inner statement (may itself be another GLYPH_MODIFIED for nesting)
        result = None
        if inner:
            self.gas_used += 1
            result = self._execute_node(inner)

        self._active_glyphs.pop()
        return result

    def _exec_openclaw_tool(self, node: dict) -> Any:
        tool_name = node.get("tool", "")
        args = node.get("args", [])

        self._trace.append({
            "tag": "OPENCLAW_TOOL",
            "tool": tool_name,
            "args": args,
            "operator": "↦ 🗲",
        })

        # Dispatch to OpenClaw orchestrator plugin via configurable endpoint
        import os

        import httpx
        openclaw_url = os.environ.get("OPENCLAW_ENDPOINT")
        if not openclaw_url:
            raise HlfRuntimeError(
                "OPENCLAW_ENDPOINT environment variable is not set; "
                "cannot execute OPENCLAW_TOOL in this deployment."
            )
        try:
            response = httpx.post(
                openclaw_url,
                json={"tool": tool_name, "args": args},
                timeout=10.0,
            )
            if response.status_code == 200:
                result = response.json()
            else:
                result = {
                    "status": "error",
                    "tool": tool_name,
                    "args": args,
                    "error": f"HTTP {response.status_code}",
                }
        except httpx.RequestError as e:
            result = {
                "status": "error",
                "tool": tool_name,
                "args": args,
                "error": str(e),
            }

        self.scope[f"{tool_name}_RESULT"] = result
        return result

    def _exec_tool(self, node: dict) -> Any:
        """Execute TOOL (↦ τ) — dispatch to host function registry."""
        tool_name = node.get("tool", "")
        args = node.get("args", [])
        type_annotation = node.get("type_annotation")  # noqa: F841 — pre-staged for v4 type dispatch

        self._trace.append({
            "tag": "TOOL",
            "tool": tool_name,
            "args": args,
            "operator": "↦ τ",
        })

        # Try to dispatch via runtime.py HostFunctionRegistry first
        try:
            from hlf.runtime import HostFunctionRegistry
            registry = HostFunctionRegistry.from_json()
            result_obj = registry.dispatch(
                tool_name,
                {"args": args},
                tier=self.tier,
            )
            result = result_obj.value if hasattr(result_obj, 'value') else result_obj
        except ImportError:
            # Fallback: try legacy dispatcher
            try:
                from agents.core.host_function_dispatcher import dispatch
                result = dispatch(tool_name, args, self.tier)
            except ImportError:
                result = {"tool": tool_name, "args": args, "status": "dispatch_unavailable"}

        self.scope[f"{tool_name}_RESULT"] = result
        self._result_value = result
        return result

    def _exec_import(self, node: dict) -> None:
        """Execute IMPORT — delegate to runtime.py ModuleLoader if available."""
        module_name = node.get("name", "")

        self._trace.append({
            "tag": "IMPORT",
            "module": module_name,
            "action": "import_requested",
        })

        # Module loading is handled by HLFRuntime in runtime.py which
        # wraps this interpreter. If running standalone, log and continue.
        logger.info(f"IMPORT requested: {module_name} (handled by HLFRuntime)")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _flatten_args(self, args: list) -> list:
        """Flatten args list, expanding runtime ${VAR} refs from scope."""
        result = []
        for arg in args:
            if isinstance(arg, dict):
                # Pass-by-reference
                if "ref" in arg and arg.get("operator") == "&":
                    ref_name = arg["ref"]
                    result.append(self.scope.get(ref_name, arg))
                else:
                    # kv_pair dict — extract values
                    for v in arg.values():
                        result.append(self._expand(v))
            else:
                result.append(self._expand(arg))
        return result

    def _expand(self, value: Any) -> Any:
        """Expand ${VAR} references from the current runtime scope."""
        if isinstance(value, str):

            def _replace(m: re.Match) -> str:
                return str(self.scope.get(m.group(1), m.group(0)))

            return _VAR_RE.sub(_replace, value)
        if isinstance(value, list):
            return [self._expand(v) for v in value]
        if isinstance(value, dict):
            return {k: self._expand(v) for k, v in value.items()}
        return value

    # ------------------------------------------------------------------ #
    # Memory operations (Infinite RAG integration)
    # ------------------------------------------------------------------ #

    def _exec_memory(self, node: dict) -> None:
        """Execute MEMORY — store an HLF-anchored memory node.

        If a memory engine is injected (_memory_engine), delegates to it.
        Otherwise stores in scope as MEMORY_<entity>.
        """
        entity = self._expand(node.get("entity", ""))
        content = self._expand(node.get("content", ""))
        confidence = node.get("confidence", 0.5)

        # Store in scope for immediate access
        mem_key = f"MEMORY_{entity}"
        existing = self.scope.get(mem_key, [])
        if not isinstance(existing, list):
            existing = [existing]
        existing.append({"content": content, "confidence": confidence})
        self.scope[mem_key] = existing

        # Delegate to Infinite RAG engine if available
        if self._memory_engine is not None:
            try:
                from hlf.memory_node import HLFMemoryNode
                mem_node = HLFMemoryNode.from_ast(
                    ast=node,
                    entity_id=entity,
                    agent=self.scope.get("_agent", "runtime"),
                    confidence=confidence,
                    source=str(content),
                )
                self._memory_engine.store(mem_node)
            except Exception as e:
                logger.warning(f"MEMORY store to engine failed: {e}")

        self._trace.append({
            "tag": "MEMORY", "entity": entity,
            "confidence": confidence, "action": "stored",
        })

    def _exec_recall(self, node: dict) -> list:
        """Execute RECALL — retrieve memories for an entity.

        Returns list of memory dicts stored in scope under RECALL_<entity>.
        """
        entity = self._expand(node.get("entity", ""))
        top_k = node.get("top_k", 5)
        results = []

        # Try Infinite RAG engine first
        if self._memory_engine is not None:
            try:
                nodes = self._memory_engine.retrieve(entity, top_k=top_k)
                results = [{"content": n.hlf_source or str(n.hlf_ast), "confidence": n.confidence} for n in nodes]
            except Exception as e:
                logger.warning(f"RECALL from engine failed: {e}")

        # Fallback: check scope
        if not results:
            mem_key = f"MEMORY_{entity}"
            stored = self.scope.get(mem_key, [])
            if isinstance(stored, list):
                results = stored[:top_k]
            elif stored:
                results = [stored]

        # Store recall results in scope
        self.scope[f"RECALL_{entity}"] = results
        self._result_value = results

        self._trace.append({
            "tag": "RECALL", "entity": entity,
            "top_k": top_k, "found": len(results),
        })
        return results

    # ------------------------------------------------------------------ #
    # Macro system (Σ [DEFINE] / ⌘ [CALL])
    # ------------------------------------------------------------------ #

    def _exec_define(self, node: dict) -> None:
        """Execute DEFINE — register a macro in the macro registry."""
        name = node.get("name", "")
        body = node.get("body", [])
        self._macros[name] = body
        self._trace.append({
            "tag": "DEFINE", "name": name,
            "statements": len(body), "action": "registered",
        })

    def _exec_call(self, node: dict) -> Any:
        """Execute CALL — expand and execute a previously-defined macro.

        Positional arguments ($1, $2, ...) are substituted into the
        macro body before execution.
        """
        name = node.get("name", "")
        args = self._flatten_args(node.get("args", []))

        if name not in self._macros:
            raise HlfRuntimeError(f"CALL: macro '{name}' not defined")

        macro_body = self._macros[name]
        result = None

        # Execute each statement in the macro body
        for stmt in macro_body:
            if stmt is None:
                continue
            # Substitute positional $1, $2, etc.
            expanded_stmt = self._substitute_params(stmt, args)
            if self.gas_used >= self.max_gas:
                raise HlfRuntimeError(f"Gas limit exceeded in macro '{name}': {self.gas_used}/{self.max_gas}")
            self.gas_used += 1
            result = self._execute_node(expanded_stmt)
            if self._result_code is not None:
                break

        self._trace.append({
            "tag": "CALL", "name": name,
            "args_count": len(args), "action": "executed",
        })
        return result

    def _substitute_params(self, node: Any, args: list) -> Any:
        """Recursively substitute $1, $2, ... in a node with actual args."""
        if node is None:
            return None
        if isinstance(node, str):
            import re as _re
            def _replace_param(m: _re.Match) -> str:
                idx = int(m.group(1)) - 1
                return str(args[idx]) if idx < len(args) else m.group(0)
            return _re.sub(r'\$(\d+)', _replace_param, node)
        if isinstance(node, list):
            return [self._substitute_params(item, args) for item in node]
        if isinstance(node, dict):
            return {k: self._substitute_params(v, args) for k, v in node.items()}
        return node

    # ------------------------------------------------------------------ #
    # Living Spec lifecycle (Instinct integration)
    # ------------------------------------------------------------------ #

    def _exec_spec_define(self, node: dict) -> None:
        """Execute SPEC_DEFINE — register a Living Spec section with constraints."""
        if self._spec_sealed:
            raise HlfRuntimeError("SPEC_DEFINE: spec is sealed — no further modifications allowed")
        section = node.get("section", "")
        constraints = node.get("constraints", [])
        # Accept args as constraints if constraints is empty (tag_stmt fallback)
        if not constraints:
            args = node.get("args", [])
            constraints = args[1:] if len(args) > 1 else args
            if not section and args:
                section = str(args[0])
        self._spec_registry[section] = {
            "constraints": constraints,
            "updates": [],
            "status": "active",
        }
        self._trace.append({
            "tag": "SPEC_DEFINE", "section": section,
            "constraints": len(constraints), "action": "registered",
        })

    def _exec_spec_gate(self, node: dict) -> None:
        """Execute SPEC_GATE — assert a spec constraint; halt if violated."""
        condition = node.get("condition")
        if condition is None:
            # Fallback: check args for simple gate
            args = node.get("args", [])
            if args:
                condition = args[0]
        result = _eval_expr(condition, self.scope)
        self._trace.append({
            "tag": "SPEC_GATE",
            "condition_result": bool(result),
            "action": "asserted",
        })
        if not result:
            raise HlfRuntimeError(
                f"SPEC_GATE violation: condition evaluated to {result!r}"
            )

    def _exec_spec_update(self, node: dict) -> None:
        """Execute SPEC_UPDATE — record a spec mutation + ALIGN ledger entry."""
        if self._spec_sealed:
            raise HlfRuntimeError("SPEC_UPDATE: spec is sealed — no further modifications allowed")
        section = node.get("section", "")
        updates = node.get("updates", [])
        # Accept args as updates if updates is empty (tag_stmt fallback)
        if not updates:
            args = node.get("args", [])
            updates = args[1:] if len(args) > 1 else args
            if not section and args:
                section = str(args[0])
        # Record the update in the registry
        entry = self._spec_registry.get(section)
        if entry is None:
            # Auto-register if section doesn't exist yet
            entry = {"constraints": [], "updates": [], "status": "active"}
            self._spec_registry[section] = entry
        entry["updates"].append(updates)
        # Try to log to ALIGN ledger
        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log("SPEC_UPDATED", {
                "section": section,
                "updates": updates,
            })
        except ImportError:
            pass  # Standalone mode — no ALIGN ledger available
        self._trace.append({
            "tag": "SPEC_UPDATE", "section": section,
            "update_count": len(updates), "action": "recorded",
        })

    def _exec_spec_seal(self, node: dict) -> None:
        """Execute SPEC_SEAL — lock spec, compute SHA-256 checksum."""
        if self._spec_sealed:
            raise HlfRuntimeError("SPEC_SEAL: spec is already sealed")
        self._spec_sealed = True
        # Compute deterministic checksum of the spec registry
        import json as _json
        canonical = _json.dumps(self._spec_registry, sort_keys=True, default=str)
        checksum = hashlib.sha256(canonical.encode()).hexdigest()
        self.scope["SPEC_CHECKSUM"] = checksum
        # Try to log to ALIGN ledger
        try:
            from agents.core.als_logger import ALSLogger
            als = ALSLogger()
            als.log("SPEC_SEALED", {
                "sections": list(self._spec_registry.keys()),
                "checksum": checksum,
            })
        except ImportError:
            pass  # Standalone mode
        self._trace.append({
            "tag": "SPEC_SEAL", "checksum": checksum,
            "sections": list(self._spec_registry.keys()),
            "action": "sealed",
        })


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def run(
    ast: dict,
    scope: dict[str, Any] | None = None,
    tier: str = "hearth",
    max_gas: int = 10,
) -> dict:
    """
    Convenience wrapper — compile-to-execute an already-compiled HLF AST.

    Returns the result dict from :meth:`HLFInterpreter.execute`.
    """
    interpreter = HLFInterpreter(scope=scope, tier=tier, max_gas=max_gas)
    return interpreter.execute(ast)
