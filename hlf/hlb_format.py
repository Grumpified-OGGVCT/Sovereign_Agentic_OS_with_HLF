"""
.hlb Binary Format — Serialized HLF Bytecode.

Converts compiled HLF bytecode into a portable binary format
for distribution and efficient loading.

Binary Format:
  Header (16 bytes):
    - Magic: b"HLFv04" (6 bytes)
    - Version: uint16 LE
    - Flags: uint16 LE
    - Checksum: uint32 LE (CRC32 of payload)
    - Payload size: uint32 LE

  Payload:
    - Instruction count: uint32 LE
    - Instructions: [opcode(uint8) + operand_count(uint8) + operands]
    - Constants pool: length-prefixed strings
    - Source map: (optional) line number mappings

Usage:
    writer = HlbWriter()
    data = writer.encode(bytecode_instructions, constants)
    Path("program.hlb").write_bytes(data)

    reader = HlbReader()
    instructions, constants = reader.decode(data)
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any


# ─── Constants ──────────────────────────────────────────────────────────────

HLB_MAGIC = b"HLFv04"
HLB_VERSION = 1
HLB_HEADER_SIZE = 18

# Flags
FLAG_HAS_SOURCE_MAP = 0x0001
FLAG_COMPRESSED = 0x0002
FLAG_DEBUG_SYMBOLS = 0x0004


# ─── Instruction ────────────────────────────────────────────────────────────

@dataclass
class HlbInstruction:
    """A single bytecode instruction."""
    opcode: int
    operands: list[int] = field(default_factory=list)
    label: str = ""  # Optional debug label

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"opcode": self.opcode, "operands": self.operands}
        if self.label:
            d["label"] = self.label
        return d


# ─── HLB Writer ─────────────────────────────────────────────────────────────

class HlbWriter:
    """Encodes bytecode instructions into .hlb binary format."""

    def encode(
        self,
        instructions: list[HlbInstruction],
        constants: list[str] | None = None,
        *,
        source_map: dict[int, int] | None = None,
        compress: bool = False,
    ) -> bytes:
        """Encode instructions + constants into .hlb binary.

        Args:
            instructions: List of bytecode instructions.
            constants: String constants pool.
            source_map: Optional instruction_index → line_number mapping.
            compress: Whether to zlib-compress the payload.

        Returns:
            Complete .hlb binary data.
        """
        consts = constants or []
        payload = self._encode_payload(instructions, consts, source_map)

        if compress:
            payload = zlib.compress(payload, level=6)

        flags = 0
        if source_map:
            flags |= FLAG_HAS_SOURCE_MAP
        if compress:
            flags |= FLAG_COMPRESSED

        checksum = zlib.crc32(payload) & 0xFFFFFFFF

        header = struct.pack(
            "<6sHHII",
            HLB_MAGIC,
            HLB_VERSION,
            flags,
            checksum,
            len(payload),
        )

        return header + payload

    def _encode_payload(
        self,
        instructions: list[HlbInstruction],
        constants: list[str],
        source_map: dict[int, int] | None,
    ) -> bytes:
        buf = bytearray()

        # Instruction count
        buf += struct.pack("<I", len(instructions))

        # Instructions
        for instr in instructions:
            buf += struct.pack("<B", instr.opcode)
            buf += struct.pack("<B", len(instr.operands))
            for op in instr.operands:
                buf += struct.pack("<i", op)  # signed 32-bit operands

        # Constants pool
        buf += struct.pack("<I", len(constants))
        for const in constants:
            encoded = const.encode("utf-8")
            buf += struct.pack("<H", len(encoded))
            buf += encoded

        # Source map (optional)
        if source_map:
            buf += struct.pack("<I", len(source_map))
            for idx, line in sorted(source_map.items()):
                buf += struct.pack("<II", idx, line)

        return bytes(buf)


# ─── HLB Reader ─────────────────────────────────────────────────────────────

class HlbFormatError(Exception):
    """Raised when .hlb binary is malformed."""


class HlbReader:
    """Decodes .hlb binary format back into instructions."""

    def decode(
        self, data: bytes
    ) -> tuple[list[HlbInstruction], list[str], dict[int, int]]:
        """Decode .hlb binary into instructions, constants, and source map.

        Returns:
            (instructions, constants, source_map)
        """
        if len(data) < HLB_HEADER_SIZE:
            raise HlbFormatError("Data too short for header")

        magic, version, flags, checksum, payload_size = struct.unpack(
            "<6sHHII", data[:HLB_HEADER_SIZE]
        )

        if magic != HLB_MAGIC:
            raise HlbFormatError(f"Invalid magic: {magic!r}")
        if version != HLB_VERSION:
            raise HlbFormatError(f"Unsupported version: {version}")

        payload_data = data[HLB_HEADER_SIZE:]
        if len(payload_data) < payload_size:
            raise HlbFormatError(
                f"Payload truncated: expected {payload_size}, got {len(payload_data)}"
            )
        payload = payload_data[:payload_size]

        # Verify checksum
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != checksum:
            raise HlbFormatError(
                f"Checksum mismatch: expected {checksum:#x}, got {actual_crc:#x}"
            )

        # Decompress if needed
        if flags & FLAG_COMPRESSED:
            payload = zlib.decompress(payload)

        has_source_map = bool(flags & FLAG_HAS_SOURCE_MAP)

        return self._decode_payload(payload, has_source_map)

    def _decode_payload(
        self, payload: bytes, has_source_map: bool
    ) -> tuple[list[HlbInstruction], list[str], dict[int, int]]:
        offset = 0

        # Instruction count
        (instr_count,) = struct.unpack_from("<I", payload, offset)
        offset += 4

        # Instructions
        instructions: list[HlbInstruction] = []
        for _ in range(instr_count):
            (opcode,) = struct.unpack_from("<B", payload, offset)
            offset += 1
            (op_count,) = struct.unpack_from("<B", payload, offset)
            offset += 1
            operands = []
            for _ in range(op_count):
                (operand,) = struct.unpack_from("<i", payload, offset)
                offset += 4
                operands.append(operand)
            instructions.append(HlbInstruction(opcode=opcode, operands=operands))

        # Constants pool
        (const_count,) = struct.unpack_from("<I", payload, offset)
        offset += 4
        constants: list[str] = []
        for _ in range(const_count):
            (str_len,) = struct.unpack_from("<H", payload, offset)
            offset += 2
            constants.append(payload[offset:offset + str_len].decode("utf-8"))
            offset += str_len

        # Source map
        source_map: dict[int, int] = {}
        if has_source_map and offset < len(payload):
            (map_count,) = struct.unpack_from("<I", payload, offset)
            offset += 4
            for _ in range(map_count):
                idx, line = struct.unpack_from("<II", payload, offset)
                offset += 8
                source_map[idx] = line

        return instructions, constants, source_map

    def get_info(self, data: bytes) -> dict[str, Any]:
        """Get header info without full decode."""
        if len(data) < HLB_HEADER_SIZE:
            raise HlbFormatError("Data too short")
        magic, version, flags, checksum, payload_size = struct.unpack(
            "<6sHHII", data[:HLB_HEADER_SIZE]
        )
        return {
            "magic": magic.decode("ascii", errors="replace"),
            "version": version,
            "flags": flags,
            "has_source_map": bool(flags & FLAG_HAS_SOURCE_MAP),
            "compressed": bool(flags & FLAG_COMPRESSED),
            "checksum": f"{checksum:#010x}",
            "payload_size": payload_size,
            "total_size": HLB_HEADER_SIZE + payload_size,
        }
