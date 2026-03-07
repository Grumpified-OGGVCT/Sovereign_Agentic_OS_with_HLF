"""
HLF Bytecode Compiler & Stack-Machine VM.

Implements HLF v0.4 bytecode:
  - Compiler: JSON AST → .hlb binary (constant pool + instruction stream)
  - VM: Executes .hlb bytecode on a gas-metered stack machine
  - Disassembler: .hlb → human-readable instruction listing

Binary format (see governance/bytecode_spec.yaml):
  Header: 24 bytes (magic, version, flags, offsets, CRC32)
  Constant pool: typed entries (int, float, string, bool)
  Code section: opcode (uint8) + operand (uint16 LE) per instruction

Usage:
    from hlf.bytecode import compile_to_bytecode, execute_bytecode, disassemble

    # Compile AST to bytecode
    hlb_bytes = compile_to_bytecode(ast)

    # Execute bytecode
    result = execute_bytecode(hlb_bytes, tier="hearth", max_gas=10)

    # Disassemble for debugging
    text = disassemble(hlb_bytes)
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

# ─── Opcodes ─────────────────────────────────────────────────────────────────

class Op:
    """HLF bytecode opcodes (matching governance/bytecode_spec.yaml)."""

    # Stack
    PUSH_CONST   = 0x01
    POP          = 0x02
    DUP          = 0x03

    # Variables
    STORE        = 0x10
    LOAD         = 0x11
    STORE_IMMUT  = 0x12

    # Arithmetic
    ADD          = 0x20
    SUB          = 0x21
    MUL          = 0x22
    DIV          = 0x23
    MOD          = 0x24
    NEG          = 0x25

    # Comparison
    CMP_EQ       = 0x30
    CMP_NE       = 0x31
    CMP_LT       = 0x32
    CMP_LE       = 0x33
    CMP_GT       = 0x34
    CMP_GE       = 0x35

    # Logic
    AND          = 0x38
    OR           = 0x39
    NOT          = 0x3A

    # Control flow
    JMP          = 0x40
    JZ           = 0x41
    JNZ          = 0x42

    # Calls
    CALL_BUILTIN = 0x50
    CALL_HOST    = 0x51
    CALL_TOOL    = 0x52

    # HLF-specific
    TAG          = 0x60
    INTENT       = 0x61
    RESULT       = 0x62
    MEMORY_STORE = 0x63
    MEMORY_RECALL= 0x64
    OPENCLAW_TOOL= 0x65

    # System
    NOP          = 0xFE
    HALT         = 0xFF


# Reverse map for disassembly
_OP_NAMES: dict[int, str] = {
    v: k for k, v in vars(Op).items()
    if isinstance(v, int) and not k.startswith("_")
}

# Gas costs per opcode
_OP_GAS: dict[int, int] = {
    Op.PUSH_CONST: 1, Op.POP: 1, Op.DUP: 1,
    Op.STORE: 1, Op.LOAD: 1, Op.STORE_IMMUT: 1,
    Op.ADD: 1, Op.SUB: 1, Op.MUL: 1, Op.DIV: 1, Op.MOD: 1, Op.NEG: 1,
    Op.CMP_EQ: 1, Op.CMP_NE: 1, Op.CMP_LT: 1, Op.CMP_LE: 1,
    Op.CMP_GT: 1, Op.CMP_GE: 1,
    Op.AND: 1, Op.OR: 1, Op.NOT: 1,
    Op.JMP: 1, Op.JZ: 1, Op.JNZ: 1,
    Op.CALL_BUILTIN: 2, Op.CALL_HOST: 5, Op.CALL_TOOL: 3,
    Op.TAG: 1, Op.INTENT: 1, Op.RESULT: 1,
    Op.MEMORY_STORE: 3, Op.MEMORY_RECALL: 3, Op.OPENCLAW_TOOL: 5,
    Op.NOP: 0, Op.HALT: 0,
}


# ─── Binary Format Constants ────────────────────────────────────────────────

_MAGIC = bytes([0x48, 0x4C, 0x46, 0x04])    # "HLF\x04"
_VERSION = bytes([0x00, 0x04])                # v0.4
_HEADER_SIZE = 24

# Constant types
_CONST_INT    = 0
_CONST_FLOAT  = 1
_CONST_STRING = 2
_CONST_BOOL   = 3


# ─── Exceptions ──────────────────────────────────────────────────────────────

class HlfBytecodeError(Exception):
    """Raised when bytecode compilation or execution fails."""


class HlfVMGasExhausted(HlfBytecodeError):
    """Raised when the VM runs out of gas."""


class HlfVMStackUnderflow(HlfBytecodeError):
    """Raised on stack underflow."""


# ─── Constant Pool ──────────────────────────────────────────────────────────

class ConstantPool:
    """Typed constant pool for the bytecode binary format."""

    def __init__(self) -> None:
        self._entries: list[tuple[int, Any]] = []
        self._index: dict[tuple[int, Any], int] = {}

    def add(self, value: Any) -> int:
        """Add a constant and return its index. Deduplicates."""
        if isinstance(value, bool):
            ctype = _CONST_BOOL
            key = (_CONST_BOOL, value)
        elif isinstance(value, int):
            ctype = _CONST_INT
            key = (_CONST_INT, value)
        elif isinstance(value, float):
            ctype = _CONST_FLOAT
            key = (_CONST_FLOAT, value)
        elif isinstance(value, str):
            ctype = _CONST_STRING
            key = (_CONST_STRING, value)
        else:
            # Coerce to string
            ctype = _CONST_STRING
            value = str(value)
            key = (_CONST_STRING, value)

        if key in self._index:
            return self._index[key]

        idx = len(self._entries)
        self._entries.append((ctype, value))
        self._index[key] = idx
        return idx

    def get(self, index: int) -> Any:
        """Retrieve a constant by index."""
        return self._entries[index][1]

    def encode(self) -> bytes:
        """Encode the constant pool to binary."""
        buf = bytearray(struct.pack("<H", len(self._entries)))
        for ctype, value in self._entries:
            buf.extend(struct.pack("<B", ctype))
            if ctype == _CONST_INT:
                buf.extend(struct.pack("<q", value))
            elif ctype == _CONST_FLOAT:
                buf.extend(struct.pack("<d", value))
            elif ctype == _CONST_STRING:
                encoded = value.encode("utf-8")
                buf.extend(struct.pack("<H", len(encoded)) + encoded)
            elif ctype == _CONST_BOOL:
                buf.extend(struct.pack("<B", 1 if value else 0))
        return bytes(buf)

    @classmethod
    def decode(cls, data: bytes, offset: int = 0) -> tuple[ConstantPool, int]:
        """Decode a constant pool from binary. Returns (pool, bytes_consumed)."""
        pool = cls()
        pos = offset
        count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        for _ in range(count):
            ctype = struct.unpack_from("<B", data, pos)[0]
            pos += 1
            if ctype == _CONST_INT:
                val = struct.unpack_from("<q", data, pos)[0]
                pos += 8
            elif ctype == _CONST_FLOAT:
                val = struct.unpack_from("<d", data, pos)[0]
                pos += 8
            elif ctype == _CONST_STRING:
                slen = struct.unpack_from("<H", data, pos)[0]
                pos += 2
                val = data[pos:pos + slen].decode("utf-8")
                pos += slen
            elif ctype == _CONST_BOOL:
                val = bool(struct.unpack_from("<B", data, pos)[0])
                pos += 1
            else:
                msg = f"Unknown constant type: {ctype}"
                raise HlfBytecodeError(msg)
            pool._entries.append((ctype, val))
            pool._index[(_CONST_STRING if ctype == _CONST_STRING else ctype, val)] = len(pool._entries) - 1
        return pool, pos - offset

    def __len__(self) -> int:
        return len(self._entries)


# ─── Bytecode Compiler (AST → .hlb) ─────────────────────────────────────────

@dataclass
class _Instruction:
    """A single bytecode instruction."""
    opcode: int
    operand: int = 0


class BytecodeCompiler:
    """Compiles a JSON AST (from hlfc.compile()) into HLF bytecode."""

    def __init__(self) -> None:
        self.pool = ConstantPool()
        self.instructions: list[_Instruction] = []

    def compile(self, ast: dict) -> bytes:
        """Compile the full AST to bytecode bytes."""
        program = ast.get("program", [])
        for node in program:
            if isinstance(node, dict):
                self._compile_node(node)

        # Ensure HALT at end
        if not self.instructions or self.instructions[-1].opcode != Op.HALT:
            self.instructions.append(_Instruction(Op.HALT))

        return self._encode()

    def _compile_node(self, node: dict) -> None:
        """Compile a single AST node into instructions."""
        tag = node.get("tag", "")
        handler = getattr(self, f"_emit_{tag.lower()}", None)
        if handler is not None:
            handler(node)
        else:
            # Generic tag emission — push tag name
            tag_idx = self.pool.add(tag)
            self.instructions.append(_Instruction(Op.TAG, tag_idx))

    def _emit_set(self, node: dict) -> None:
        """Compile [SET] name = value → PUSH_CONST value, STORE_IMMUT name."""
        name = node.get("name", "")
        value = node.get("value", "")
        val_idx = self.pool.add(value)
        name_idx = self.pool.add(name)
        self.instructions.append(_Instruction(Op.PUSH_CONST, val_idx))
        self.instructions.append(_Instruction(Op.STORE_IMMUT, name_idx))

    def _emit_assign(self, node: dict) -> None:
        """Compile assignment → evaluate RHS expression, STORE name."""
        name = node.get("name", "")
        value = node.get("value")
        rhs = node.get("rhs")

        if rhs is not None:
            self._compile_expression(rhs)
        elif value is not None:
            val_idx = self.pool.add(value)
            self.instructions.append(_Instruction(Op.PUSH_CONST, val_idx))
        else:
            val_idx = self.pool.add("")
            self.instructions.append(_Instruction(Op.PUSH_CONST, val_idx))

        name_idx = self.pool.add(name)
        self.instructions.append(_Instruction(Op.STORE, name_idx))

    def _emit_intent(self, node: dict) -> None:
        """Compile [INTENT] action target."""
        args = node.get("args", [])
        action = args[0] if len(args) > 0 else "unknown"
        target = args[1] if len(args) > 1 else ""
        action_idx = self.pool.add(str(action))
        target_idx = self.pool.add(str(target))
        self.instructions.append(_Instruction(Op.PUSH_CONST, action_idx))
        self.instructions.append(_Instruction(Op.PUSH_CONST, target_idx))
        self.instructions.append(_Instruction(Op.INTENT, action_idx))

    def _emit_result(self, node: dict) -> None:
        """Compile [RESULT] code message → PUSH code, PUSH message, RESULT."""
        code = node.get("code")
        message = node.get("message")

        # Normalize non-string message to None for extraction
        if message is not None and not isinstance(message, str):
            message = None

        # Handle args-based format (keyword form) when either field is missing
        if code is None or message is None:
            for arg in node.get("args", []):
                if isinstance(arg, dict):
                    if "code" in arg and code is None:
                        code = int(arg["code"])
                    if "message" in arg and message is None:
                        message = str(arg["message"])

        if code is None:
            code = 0
        if message is None:
            message = "ok"

        code_idx = self.pool.add(int(code))
        msg_idx = self.pool.add(str(message))
        self.instructions.append(_Instruction(Op.PUSH_CONST, code_idx))
        self.instructions.append(_Instruction(Op.PUSH_CONST, msg_idx))
        self.instructions.append(_Instruction(Op.RESULT))

    def _emit_function(self, node: dict) -> None:
        """Compile [FUNCTION] name args → CALL_BUILTIN."""
        name = node.get("name", "")
        args = node.get("args", [])
        # Push args onto stack — preserve original types where possible
        for arg in args:
            arg_idx = self.pool.add(arg if isinstance(arg, (int, float, bool, str)) else str(arg))
            self.instructions.append(_Instruction(Op.PUSH_CONST, arg_idx))
        # Push arg count
        count_idx = self.pool.add(len(args))
        self.instructions.append(_Instruction(Op.PUSH_CONST, count_idx))
        # Call
        name_idx = self.pool.add(name)
        self.instructions.append(_Instruction(Op.CALL_BUILTIN, name_idx))

    def _emit_action(self, node: dict) -> None:
        """Compile [ACTION] verb args → CALL_HOST.

        HLFC AST encodes [ACTION] as:
            {"tag": "ACTION", "args": [verb, arg1, arg2, ...]}

        If an explicit ``verb`` field is present, use it directly.
        Otherwise, derive verb from args[0] matching hlfrun._exec_action().
        """
        raw_args = node.get("args", []) or []
        explicit_verb = node.get("verb")

        if isinstance(explicit_verb, str) and explicit_verb:
            verb = explicit_verb
            args = list(raw_args)
        else:
            if raw_args:
                verb = str(raw_args[0])
                args = list(raw_args[1:])
            else:
                verb = ""
                args = []

        for arg in args:
            arg_idx = self.pool.add(str(arg))
            self.instructions.append(_Instruction(Op.PUSH_CONST, arg_idx))
        count_idx = self.pool.add(len(args))
        self.instructions.append(_Instruction(Op.PUSH_CONST, count_idx))
        verb_idx = self.pool.add(verb)
        self.instructions.append(_Instruction(Op.CALL_HOST, verb_idx))

    def _emit_tool(self, node: dict) -> None:
        """Compile [TOOL] tool args → CALL_TOOL."""
        # HLFC emits tool nodes with a "tool" field, not "name".
        try:
            name = node["tool"]
        except KeyError:
            # Fallback for legacy/alternative ASTs that might use "name".
            name = node.get("name", "")
        args = node.get("args", [])
        for arg in args:
            arg_idx = self.pool.add(arg if isinstance(arg, (int, float, bool, str)) else str(arg))
            self.instructions.append(_Instruction(Op.PUSH_CONST, arg_idx))
        count_idx = self.pool.add(len(args))
        self.instructions.append(_Instruction(Op.PUSH_CONST, count_idx))
        name_idx = self.pool.add(name)
        self.instructions.append(_Instruction(Op.CALL_TOOL, name_idx))

    def _emit_conditional(self, node: dict) -> None:
        """Compile conditional: evaluate condition, JZ to else, then branch, JMP over else."""
        condition = node.get("condition")
        then_body = node.get("then", [])
        else_body = node.get("else", [])

        # Evaluate condition
        if condition is not None:
            self._compile_expression(condition)
        else:
            # Default true
            true_idx = self.pool.add(True)
            self.instructions.append(_Instruction(Op.PUSH_CONST, true_idx))

        # JZ placeholder (will be patched)
        jz_pos = len(self.instructions)
        self.instructions.append(_Instruction(Op.JZ, 0))

        # Then branch
        if isinstance(then_body, list):
            for sub_node in then_body:
                if isinstance(sub_node, dict):
                    self._compile_node(sub_node)
        elif isinstance(then_body, dict):
            self._compile_node(then_body)

        if else_body:
            # JMP over else
            jmp_pos = len(self.instructions)
            self.instructions.append(_Instruction(Op.JMP, 0))
            # Patch JZ to here
            self.instructions[jz_pos].operand = len(self.instructions)
            # Else branch
            if isinstance(else_body, list):
                for sub_node in else_body:
                    if isinstance(sub_node, dict):
                        self._compile_node(sub_node)
            elif isinstance(else_body, dict):
                self._compile_node(else_body)
            # Patch JMP to here
            self.instructions[jmp_pos].operand = len(self.instructions)
        else:
            # Patch JZ to here (no else)
            self.instructions[jz_pos].operand = len(self.instructions)

    def _emit_memory(self, node: dict) -> None:
        """Compile [MEMORY] entity content confidence → MEMORY_STORE.

        Pushes confidence and content onto stack, then MEMORY_STORE with
        entity operand. VM pops content + confidence and stores in scope.
        """
        entity = node.get("entity", "")
        content = node.get("content", "")
        confidence = node.get("confidence", 0.5)
        entity_idx = self.pool.add(entity)
        conf_idx = self.pool.add(confidence)
        content_idx = self.pool.add(content)
        # Stack order: push confidence first, then content (content on top)
        self.instructions.append(_Instruction(Op.PUSH_CONST, conf_idx))
        self.instructions.append(_Instruction(Op.PUSH_CONST, content_idx))
        self.instructions.append(_Instruction(Op.MEMORY_STORE, entity_idx))

    def _emit_recall(self, node: dict) -> None:
        """Compile [RECALL] entity top_k → MEMORY_RECALL.

        Pushes top_k onto stack, then MEMORY_RECALL with entity operand.
        VM pops top_k, retrieves memories, pushes results list.
        """
        entity = node.get("entity", "")
        top_k = node.get("top_k", 5)
        entity_idx = self.pool.add(entity)
        top_k_idx = self.pool.add(top_k)
        self.instructions.append(_Instruction(Op.PUSH_CONST, top_k_idx))
        self.instructions.append(_Instruction(Op.MEMORY_RECALL, entity_idx))

    def _emit_thought(self, node: dict) -> None:
        """Compile [THOUGHT] → TAG + PUSH_CONST (pure, no side effects)."""
        tag_idx = self.pool.add("THOUGHT")
        content = node.get("args", [node.get("content", "")])
        content_str = str(content[0]) if content else ""
        content_idx = self.pool.add(content_str)
        self.instructions.append(_Instruction(Op.PUSH_CONST, content_idx))
        self.instructions.append(_Instruction(Op.TAG, tag_idx))

    def _emit_observation(self, node: dict) -> None:
        """Compile [OBSERVATION] → TAG + PUSH_CONST."""
        tag_idx = self.pool.add("OBSERVATION")
        content = node.get("args", [node.get("content", "")])
        content_str = str(content[0]) if content else ""
        content_idx = self.pool.add(content_str)
        self.instructions.append(_Instruction(Op.PUSH_CONST, content_idx))
        self.instructions.append(_Instruction(Op.TAG, tag_idx))

    def _compile_expression(self, node: Any) -> None:
        """Compile an expression node (recursive)."""
        if isinstance(node, (int, float, str, bool)):
            idx = self.pool.add(node)
            self.instructions.append(_Instruction(Op.PUSH_CONST, idx))
        elif isinstance(node, dict):
            op = node.get("op", "")
            if op in ("ADD", "SUB", "MUL", "DIV", "MOD"):
                self._compile_expression(node.get("left"))
                self._compile_expression(node.get("right"))
                opcode = {"ADD": Op.ADD, "SUB": Op.SUB, "MUL": Op.MUL,
                          "DIV": Op.DIV, "MOD": Op.MOD}[op]
                self.instructions.append(_Instruction(opcode))
            elif op == "COMPARE":
                self._compile_expression(node.get("left"))
                self._compile_expression(node.get("right"))
                cmp_map = {
                    "==": Op.CMP_EQ, "!=": Op.CMP_NE,
                    "<": Op.CMP_LT, "<=": Op.CMP_LE,
                    ">": Op.CMP_GT, ">=": Op.CMP_GE,
                }
                operator = node.get("operator", "==")
                self.instructions.append(_Instruction(cmp_map.get(operator, Op.CMP_EQ)))
            elif op in ("AND", "OR"):
                self._compile_expression(node.get("left"))
                self._compile_expression(node.get("right"))
                self.instructions.append(_Instruction(Op.AND if op == "AND" else Op.OR))
            elif op == "NOT":
                self._compile_expression(node.get("operand"))
                self.instructions.append(_Instruction(Op.NOT))
            elif "ref" in node:
                # Variable reference
                name_idx = self.pool.add(node["ref"])
                self.instructions.append(_Instruction(Op.LOAD, name_idx))
            else:
                # Literal dict — push as string
                idx = self.pool.add(str(node))
                self.instructions.append(_Instruction(Op.PUSH_CONST, idx))
        else:
            idx = self.pool.add(str(node))
            self.instructions.append(_Instruction(Op.PUSH_CONST, idx))

    def _encode(self) -> bytes:
        """Encode instructions + constant pool into the .hlb binary format."""
        pool_bytes = self.pool.encode()
        code_buf = bytearray()
        for instr in self.instructions:
            code_buf.extend(struct.pack("<BH", instr.opcode, instr.operand))
        code_bytes = bytes(code_buf)

        # Calculate offsets
        const_pool_offset = _HEADER_SIZE
        code_offset = const_pool_offset + len(pool_bytes)
        code_length = len(code_bytes)
        checksum = zlib.crc32(code_bytes) & 0xFFFFFFFF

        # Build header
        header = _MAGIC + _VERSION
        header += bytes(2)  # flags (reserved)
        header += struct.pack("<I", const_pool_offset)
        header += struct.pack("<I", code_offset)
        header += struct.pack("<I", code_length)
        header += struct.pack("<I", checksum)

        return header + pool_bytes + code_bytes


# ─── Stack-Machine VM ────────────────────────────────────────────────────────

@dataclass
class VMResult:
    """Result of bytecode execution."""
    code: int = 0
    message: str = "ok"
    gas_used: int = 0
    scope: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict matching hlfrun output format."""
        return {
            "code": self.code,
            "message": self.message,
            "gas_used": self.gas_used,
            "scope": dict(self.scope),
            "trace": list(self.trace),
        }


class HlfVM:
    """
    HLF Stack-Machine Virtual Machine.

    Executes .hlb bytecode with gas metering, variable scope,
    and a value stack. Deterministic and sandboxed.
    """

    def __init__(
        self,
        tier: str = "hearth",
        max_gas: int = 100,
    ) -> None:
        self.tier = tier
        self.max_gas = max_gas
        self.gas_used = 0
        self.stack: list[Any] = []
        self.scope: dict[str, Any] = {}
        self.immutables: set[str] = set()
        self.trace: list[dict[str, Any]] = []
        self._result_code = 0
        self._result_message = "ok"
        self._halted = False

    def execute(self, hlb_data: bytes) -> VMResult:
        """Execute bytecode binary and return result."""
        # Parse header
        if len(hlb_data) < _HEADER_SIZE:
            msg = f"Invalid bytecode: too short ({len(hlb_data)} bytes)"
            raise HlfBytecodeError(msg)

        magic = hlb_data[:4]
        if magic != _MAGIC:
            msg = f"Invalid bytecode magic: {magic!r} (expected {_MAGIC!r})"
            raise HlfBytecodeError(msg)

        const_pool_offset = struct.unpack_from("<I", hlb_data, 8)[0]
        code_offset = struct.unpack_from("<I", hlb_data, 12)[0]
        code_length = struct.unpack_from("<I", hlb_data, 16)[0]
        stored_checksum = struct.unpack_from("<I", hlb_data, 20)[0]

        # Verify checksum
        code_section = hlb_data[code_offset:code_offset + code_length]
        actual_checksum = zlib.crc32(code_section) & 0xFFFFFFFF
        if actual_checksum != stored_checksum:
            msg = f"Bytecode checksum mismatch: {actual_checksum:#x} != {stored_checksum:#x}"
            raise HlfBytecodeError(msg)

        # Decode constant pool
        pool, _consumed = ConstantPool.decode(hlb_data, const_pool_offset)

        # Execute instruction stream
        ip = 0  # instruction pointer (byte offset within code section)
        instr_size = 3  # opcode(1) + operand(2)

        while ip < len(code_section) and not self._halted:
            opcode = code_section[ip]
            operand = struct.unpack_from("<H", code_section, ip + 1)[0]
            ip += instr_size

            # Gas check
            gas_cost = _OP_GAS.get(opcode, 1)
            self.gas_used += gas_cost
            if self.gas_used > self.max_gas:
                msg = f"Gas exhausted: used {self.gas_used}/{self.max_gas}"
                raise HlfVMGasExhausted(msg)

            # Handle control-flow opcodes by updating ip directly
            if opcode == Op.JMP:
                ip = operand * instr_size
                continue
            if opcode == Op.JZ:
                val = self._pop()
                if not val:
                    ip = operand * instr_size
                continue
            if opcode == Op.JNZ:
                val = self._pop()
                if val:
                    ip = operand * instr_size
                continue

            # Dispatch all other opcodes
            self._dispatch(opcode, operand, pool, code_section)

        return VMResult(
            code=self._result_code,
            message=self._result_message,
            gas_used=self.gas_used,
            scope=dict(self.scope),
            trace=list(self.trace),
        )

    def _dispatch(self, opcode: int, operand: int, pool: ConstantPool,
                  code_section: bytes) -> None:
        """Dispatch a single instruction."""
        # Stack operations
        if opcode == Op.PUSH_CONST:
            self.stack.append(pool.get(operand))
        elif opcode == Op.POP:
            self._pop()
        elif opcode == Op.DUP:
            if not self.stack:
                raise HlfVMStackUnderflow("DUP on empty stack")
            self.stack.append(self.stack[-1])

        # Variable operations
        elif opcode == Op.STORE:
            val = self._pop()
            name = pool.get(operand)
            if name in self.immutables:
                msg = f"Cannot reassign immutable variable: {name}"
                raise HlfBytecodeError(msg)
            self.scope[name] = val
        elif opcode == Op.LOAD:
            name = pool.get(operand)
            if name in self.scope:
                self.stack.append(self.scope[name])
            else:
                self.stack.append(name)  # Unresolved → treat as literal
        elif opcode == Op.STORE_IMMUT:
            val = self._pop()
            name = pool.get(operand)
            self.scope[name] = val
            self.immutables.add(name)

        # Arithmetic
        elif opcode == Op.ADD:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) + self._to_num(b))
        elif opcode == Op.SUB:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) - self._to_num(b))
        elif opcode == Op.MUL:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) * self._to_num(b))
        elif opcode == Op.DIV:
            b, a = self._pop(), self._pop()
            divisor = self._to_num(b)
            if divisor == 0:
                raise HlfBytecodeError("Division by zero")
            self.stack.append(self._to_num(a) / divisor)
        elif opcode == Op.MOD:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) % self._to_num(b))
        elif opcode == Op.NEG:
            a = self._pop()
            self.stack.append(-self._to_num(a))

        # Comparison
        elif opcode == Op.CMP_EQ:
            b, a = self._pop(), self._pop()
            self.stack.append(a == b)
        elif opcode == Op.CMP_NE:
            b, a = self._pop(), self._pop()
            self.stack.append(a != b)
        elif opcode == Op.CMP_LT:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) < self._to_num(b))
        elif opcode == Op.CMP_LE:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) <= self._to_num(b))
        elif opcode == Op.CMP_GT:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) > self._to_num(b))
        elif opcode == Op.CMP_GE:
            b, a = self._pop(), self._pop()
            self.stack.append(self._to_num(a) >= self._to_num(b))

        # Logic
        elif opcode == Op.AND:
            b, a = self._pop(), self._pop()
            self.stack.append(bool(a) and bool(b))
        elif opcode == Op.OR:
            b, a = self._pop(), self._pop()
            self.stack.append(bool(a) or bool(b))
        elif opcode == Op.NOT:
            a = self._pop()
            self.stack.append(not bool(a))

        # Control flow — handled in execute() loop, not here
        elif opcode in (Op.JMP, Op.JZ, Op.JNZ):
            pass  # Jumps are dispatched directly in execute() before reaching here

        # Calls
        elif opcode == Op.CALL_BUILTIN:
            func_name = pool.get(operand)
            arg_count = int(self._pop())
            args = [self._pop() for _ in range(arg_count)]
            args.reverse()
            result = self._call_builtin(func_name, args)
            self.scope[f"{func_name}_RESULT"] = result
            self.trace.append({"op": "CALL_BUILTIN", "func": func_name, "result": result})

        elif opcode == Op.CALL_HOST:
            func_name = pool.get(operand)
            arg_count = int(self._pop())
            args = [self._pop() for _ in range(arg_count)]
            args.reverse()
            self.trace.append({"op": "CALL_HOST", "func": func_name, "args": args})

        elif opcode == Op.CALL_TOOL:
            func_name = pool.get(operand)
            arg_count = int(self._pop())
            args = [self._pop() for _ in range(arg_count)]
            args.reverse()
            self.trace.append({"op": "CALL_TOOL", "func": func_name, "args": args})

        # HLF-specific
        elif opcode == Op.TAG:
            tag_name = pool.get(operand)
            # Pop associated data if on stack
            data = self._pop() if self.stack else None
            self.trace.append({"op": "TAG", "tag": tag_name, "data": data})

        elif opcode == Op.INTENT:
            action_name = pool.get(operand)
            # target is on stack; action is carried in the opcode operand
            target = self._pop()
            _action = self._pop()  # consume from stack but use operand name
            self.trace.append({"op": "INTENT", "action": action_name, "target": target})

        elif opcode == Op.RESULT:
            message = self._pop()
            code = self._pop()
            self._result_code = int(code)
            self._result_message = str(message)
            self._halted = True
            self.trace.append({"op": "RESULT", "code": self._result_code, "message": self._result_message})

        elif opcode == Op.MEMORY_STORE:
            entity = pool.get(operand)
            content = self._pop()
            confidence = self._pop()
            # Store in scope as MEMORY_{entity} list, matching hlfrun._exec_memory
            mem_key = f"MEMORY_{entity}"
            existing = self.scope.get(mem_key, [])
            if not isinstance(existing, list):
                existing = [existing]
            existing.append({"content": content, "confidence": confidence})
            self.scope[mem_key] = existing
            self.trace.append({
                "op": "MEMORY_STORE", "entity": entity,
                "content": content, "confidence": confidence,
            })

        elif opcode == Op.MEMORY_RECALL:
            entity = pool.get(operand)
            top_k = int(self._pop())
            # Retrieve from scope, matching hlfrun._exec_recall
            mem_key = f"MEMORY_{entity}"
            stored = self.scope.get(mem_key, [])
            if isinstance(stored, list):
                results = stored[:top_k]
            elif stored:
                results = [stored]
            else:
                results = []
            # Store recall results in scope and push onto stack
            self.scope[f"RECALL_{entity}"] = results
            self.stack.append(results)
            self.trace.append({
                "op": "MEMORY_RECALL", "entity": entity,
                "top_k": top_k, "found": len(results),
            })

        elif opcode == Op.OPENCLAW_TOOL:
            func_name = pool.get(operand)
            arg_count = int(self._pop())
            args = [self._pop() for _ in range(arg_count)]
            args.reverse()

            # Stub result — real dispatch not yet wired in VM path
            result = {"status": "stub", "tool": func_name, "args": args, "simulated": True}
            self.stack.append(result)
            self.scope[f"{func_name}_RESULT"] = result

            self.trace.append({"op": "OPENCLAW_TOOL", "func": func_name, "args": args})

        # System
        elif opcode == Op.NOP:
            pass
        elif opcode == Op.HALT:
            self._halted = True

    def _pop(self) -> Any:
        """Pop from stack, raising on underflow."""
        if not self.stack:
            raise HlfVMStackUnderflow("Stack underflow")
        return self.stack.pop()

    @staticmethod
    def _to_num(value: Any) -> int | float:
        """Coerce a value to a number."""
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return float(value)
                except ValueError:
                    return 0
        if isinstance(value, bool):
            return 1 if value else 0
        return 0

    @staticmethod
    def _call_builtin(name: str, args: list[Any]) -> Any:
        """Execute a built-in pure function."""
        import datetime
        import hashlib
        import uuid as _uuid

        if name == "HASH":
            algo = str(args[0]).lower() if args else "sha256"
            text = str(args[1]) if len(args) > 1 else ""
            if algo == "sha256":
                return hashlib.sha256(text.encode()).hexdigest()
            return hashlib.sha256(text.encode()).hexdigest()
        elif name == "NOW":
            return datetime.datetime.now(datetime.UTC).isoformat()
        elif name == "UUID":
            return str(_uuid.uuid4())
        elif name == "BASE64_ENCODE":
            import base64
            text = str(args[0]) if args else ""
            return base64.b64encode(text.encode()).decode()
        elif name == "BASE64_DECODE":
            import base64
            text = str(args[0]) if args else ""
            return base64.b64decode(text.encode()).decode()
        return None


# ─── Public API ──────────────────────────────────────────────────────────────


def compile_to_bytecode(ast: dict) -> bytes:
    """Compile a JSON AST (from hlfc.compile()) to .hlb bytecode."""
    compiler = BytecodeCompiler()
    return compiler.compile(ast)


def execute_bytecode(
    hlb_data: bytes,
    *,
    tier: str = "hearth",
    max_gas: int = 100,
) -> dict[str, Any]:
    """Execute .hlb bytecode and return result dict."""
    vm = HlfVM(tier=tier, max_gas=max_gas)
    result = vm.execute(hlb_data)
    return result.to_dict()


def disassemble(hlb_data: bytes) -> str:
    """Disassemble .hlb bytecode into human-readable text."""
    if len(hlb_data) < _HEADER_SIZE:
        return "<invalid bytecode: too short>"

    magic = hlb_data[:4]
    if magic != _MAGIC:
        return f"<invalid magic: {magic!r}>"

    try:
        const_pool_offset = struct.unpack_from("<I", hlb_data, 8)[0]
        code_offset = struct.unpack_from("<I", hlb_data, 12)[0]
        code_length = struct.unpack_from("<I", hlb_data, 16)[0]
        checksum = struct.unpack_from("<I", hlb_data, 20)[0]

        pool, _ = ConstantPool.decode(hlb_data, const_pool_offset)
        code_section = hlb_data[code_offset:code_offset + code_length]
    except (struct.error, UnicodeDecodeError, HlfBytecodeError) as exc:
        return f"<invalid bytecode: {exc}>"

    lines = [
        f"HLF Bytecode v0.4 — {code_length} bytes code, "
        f"{len(pool)} constants, CRC32={checksum:#010x}",
        "",
        "Constants:",
    ]
    for i in range(len(pool)):
        val = pool.get(i)
        lines.append(f"  [{i:4d}] {type(val).__name__:6s} {val!r}")

    lines.append("")
    lines.append("Instructions:")

    ip = 0
    instr_size = 3
    idx = 0
    while ip < len(code_section):
        opcode = code_section[ip]
        operand = struct.unpack_from("<H", code_section, ip + 1)[0]
        op_name = _OP_NAMES.get(opcode, f"UNKNOWN_{opcode:#04x}")

        # Show operand context
        extra = ""
        if opcode in (Op.PUSH_CONST, Op.STORE, Op.LOAD, Op.STORE_IMMUT,
                       Op.CALL_BUILTIN, Op.CALL_HOST, Op.CALL_TOOL,
                       Op.TAG, Op.INTENT, Op.MEMORY_STORE, Op.MEMORY_RECALL, Op.OPENCLAW_TOOL):
            try:
                extra = f"  ; {pool.get(operand)!r}"
            except (IndexError, KeyError):
                extra = f"  ; <invalid index {operand}>"

        lines.append(f"  {idx:4d} | {ip:6d}  {op_name:16s} {operand:5d}{extra}")
        ip += instr_size
        idx += 1

    return "\n".join(lines)
