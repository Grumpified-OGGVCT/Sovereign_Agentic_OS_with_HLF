"""
HLF Runtime Interpreter — Phase 3.1 Built-in Functions + Phase 5.1 Host Functions.

Executes a compiled JSON AST (output of hlfc.compile()) against the registered
built-in pure functions and the host-function backends defined in
governance/host_functions.json.

Built-in pure functions (no I/O, deterministic):
  [FUNCTION] HASH sha256 <text>         → scope["HASH_RESULT"]
  [FUNCTION] BASE64_ENCODE <text>       → scope["BASE64_ENCODE_RESULT"]
  [FUNCTION] BASE64_DECODE <text>       → scope["BASE64_DECODE_RESULT"]
  [FUNCTION] NOW                        → scope["NOW_RESULT"]  (ISO-8601 UTC)
  [FUNCTION] UUID                       → scope["UUID_RESULT"]

Host functions (I/O — mediated via host_function_dispatcher):
  [ACTION] READ <path>
  [ACTION] WRITE <path> <data>
  [ACTION] SPAWN <image>
  [ACTION] SLEEP <ms>
  [ACTION] HTTP_GET <url>
  [ACTION] WEB_SEARCH <query>
  [ACTION] OPENCLAW_SUMMARIZE <path>
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import re
import uuid
from typing import Any

from hlf.hlfc import HlfRuntimeError

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
# Runtime interpreter
# --------------------------------------------------------------------------- #


class HLFInterpreter:
    """
    Walks a compiled HLF AST node-by-node and executes each statement.

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

    def execute(self, ast: dict) -> dict:
        """
        Execute the program in *ast* and return a result dict::

            {
                "code":     int,   # 0 = success
                "message":  str,
                "scope":    dict,  # final variable bindings
                "gas_used": int,
                "result":   Any,   # last ACTION/FUNCTION return value
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
        }

    # ------------------------------------------------------------------ #
    # Node dispatchers
    # ------------------------------------------------------------------ #

    def _execute_node(self, node: dict) -> None:
        tag = node.get("tag", "")
        if tag == "FUNCTION":
            self._exec_function(node)
        elif tag == "ACTION":
            self._exec_action(node)
        elif tag == "SET":
            # Runtime SET evaluation (compile Pass 2 already expanded ${VAR}
            # references, but we still track the binding for runtime introspection)
            self.scope[node.get("name", "")] = self._expand(node.get("value", ""))
        elif tag == "RESULT":
            self._exec_result(node)
        # INTENT, CONSTRAINT, EXPECT, MODULE, IMPORT: structural/declarative —
        # they carry no side-effects at runtime; the interpreter acknowledges them.

    def _exec_function(self, node: dict) -> None:
        name = (node.get("name") or "").upper()
        args = self._flatten_args(node.get("args", []))
        fn = _BUILTIN_FUNCTIONS.get(name)
        if fn is None:
            raise HlfRuntimeError(f"Unknown built-in function: {name}")
        result = fn(*args)
        # Store as ${NAME_RESULT} so subsequent tags can reference it
        self.scope[f"{name}_RESULT"] = result
        self._result_value = result

    def _exec_action(self, node: dict) -> None:
        args = self._flatten_args(node.get("args", []))
        if not args:
            raise HlfRuntimeError("[ACTION] requires at least one argument (verb)")
        verb = str(args[0]).upper()
        rest = args[1:]
        from agents.core.host_function_dispatcher import dispatch

        result = dispatch(verb, rest, self.tier)
        self.scope[f"{verb}_RESULT"] = result
        self._result_value = result

    def _exec_result(self, node: dict) -> None:
        code = 0
        message = "ok"
        for arg in node.get("args", []):
            if isinstance(arg, dict):
                if "code" in arg:
                    code = int(arg["code"])
                if "message" in arg:
                    message = str(arg["message"])
        self._result_code = code
        self._result_message = message

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _flatten_args(self, args: list) -> list:
        """Flatten args list, expanding runtime ${VAR} refs from scope."""
        result = []
        for arg in args:
            if isinstance(arg, dict):
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
