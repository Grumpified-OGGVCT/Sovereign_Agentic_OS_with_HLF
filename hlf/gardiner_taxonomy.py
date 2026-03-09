"""
Gardiner Sign-List Taxonomy — Hieroglyphic Reference Integration.

Maps HLF glyphs to the Gardiner classification system used in
Egyptology. This creates a formal academic lineage for the HLF
symbol system, connecting modern AI semantics to ancient
hieroglyphic categories.

Gardiner Categories:
  A: Man and his activities
  D: Parts of the body
  G: Birds
  N: Sky, earth, water
  O: Buildings
  R: Temple furniture
  S: Crowns, staves
  U: Agriculture
  V: Rope, fibre
  Y: Writing, games
  Z: Strokes, geometric
  Aa: Unclassified

Usage:
    registry = GardinerRegistry()
    sign = registry.lookup("Ω")
    category = registry.get_category("Z")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─── Gardiner Sign ──────────────────────────────────────────────────────────

@dataclass
class GardinerSign:
    """A mapping between an HLF glyph and the Gardiner classification."""

    unicode_char: str              # The Unicode character used in HLF
    hlf_name: str                  # HLF semantic name
    gardiner_code: str             # e.g., "Z1", "Aa27"
    gardiner_category: str         # e.g., "Z" (Strokes, geometric)
    gardiner_description: str      # Academic description
    hlf_semantics: str             # What it means in HLF
    token_ids: list[str] = field(default_factory=list)  # BPE token IDs
    rfc_reference: str = ""        # Which RFC defined this glyph

    def to_dict(self) -> dict[str, Any]:
        return {
            "unicode": self.unicode_char,
            "hlf_name": self.hlf_name,
            "gardiner_code": self.gardiner_code,
            "gardiner_category": self.gardiner_category,
            "gardiner_description": self.gardiner_description,
            "hlf_semantics": self.hlf_semantics,
            "rfc_reference": self.rfc_reference,
        }


# ─── Category Definitions ──────────────────────────────────────────────────

GARDINER_CATEGORIES: dict[str, str] = {
    "A": "Man and his activities",
    "B": "Woman and her activities",
    "C": "Anthropomorphic deities",
    "D": "Parts of the human body",
    "E": "Mammals",
    "F": "Parts of mammals",
    "G": "Birds",
    "H": "Parts of birds",
    "I": "Amphibians, reptiles",
    "K": "Fishes and parts of fishes",
    "L": "Invertebrates and lesser animals",
    "M": "Trees and plants",
    "N": "Sky, earth, water",
    "O": "Buildings, parts of buildings",
    "P": "Ships and parts of ships",
    "Q": "Domestic and funerary furniture",
    "R": "Temple furniture and sacred emblems",
    "S": "Crowns, dress, staves",
    "T": "Warfare, hunting, butchery",
    "U": "Agriculture, crafts, professions",
    "V": "Rope, fibre, baskets, bags",
    "W": "Vessels",
    "X": "Loaves and cakes",
    "Y": "Writing, games, music",
    "Z": "Strokes and geometric figures",
    "Aa": "Unclassified signs",
}


# ─── HLF→Gardiner Mappings ─────────────────────────────────────────────────

# Core glyph mappings from the HLF symbol set to Gardiner categories
_CORE_MAPPINGS: list[dict[str, str]] = [
    {
        "unicode_char": "Ω",
        "hlf_name": "Terminal Conclusion",
        "gardiner_code": "Z6",
        "gardiner_category": "Z",
        "gardiner_description": "Terminal stroke — end of sequence marker",
        "hlf_semantics": "Final stdout state, terminates recursive loops",
        "rfc_reference": "RFC 9001",
    },
    {
        "unicode_char": "Δ",
        "hlf_name": "State Diff",
        "gardiner_code": "N29",
        "gardiner_category": "N",
        "gardiner_description": "Hill, mound — change in elevation/state",
        "hlf_semantics": "Only output delta changes, never full files",
        "rfc_reference": "RFC 9001",
    },
    {
        "unicode_char": "Ж",
        "hlf_name": "Reasoning Blocker",
        "gardiner_code": "Aa28",
        "gardiner_category": "Aa",
        "gardiner_description": "Unclassified — paradox or impasse marker",
        "hlf_semantics": "Logical paradox; triggers routing to Arbiter",
        "rfc_reference": "RFC 9001",
    },
    {
        "unicode_char": "⩕",
        "hlf_name": "Gas Metric",
        "gardiner_code": "V28",
        "gardiner_category": "V",
        "gardiner_description": "Wick, rope measure — counting/metering",
        "hlf_semantics": "Maximum recursive steps before OOM timeout",
        "rfc_reference": "RFC 9001",
    },
    {
        "unicode_char": "⌘",
        "hlf_name": "Command Prefix",
        "gardiner_code": "S42",
        "gardiner_category": "S",
        "gardiner_description": "Staff of authority — command/directive",
        "hlf_semantics": "Marks a direct system command invocation",
        "rfc_reference": "RFC 9005",
    },
    {
        "unicode_char": "∇",
        "hlf_name": "Gradient/Goal",
        "gardiner_code": "N31",
        "gardiner_category": "N",
        "gardiner_description": "Road, path — direction toward goal",
        "hlf_semantics": "Optimization gradient or goal direction",
        "rfc_reference": "RFC 9005",
    },
    {
        "unicode_char": "⨝",
        "hlf_name": "Join Operator",
        "gardiner_code": "V24",
        "gardiner_category": "V",
        "gardiner_description": "Knot, binding — joining together",
        "hlf_semantics": "Data join/merge operation between streams",
        "rfc_reference": "RFC 9005",
    },
    {
        "unicode_char": "§",
        "hlf_name": "Section Expression",
        "gardiner_code": "Y1",
        "gardiner_category": "Y",
        "gardiner_description": "Papyrus roll — section/writing marker",
        "hlf_semantics": "Section or expression boundary in HLF scripts",
        "rfc_reference": "RFC 9007",
    },
    {
        "unicode_char": "≡",
        "hlf_name": "Struct Definition",
        "gardiner_code": "Z4",
        "gardiner_category": "Z",
        "gardiner_description": "Dual strokes — identity/definition",
        "hlf_semantics": "Structure definition operator (RFC 9007)",
        "rfc_reference": "RFC 9007",
    },
    {
        "unicode_char": "↦",
        "hlf_name": "Tool Execution",
        "gardiner_code": "T14",
        "gardiner_category": "T",
        "gardiner_description": "Throw stick — action/dispatch",
        "hlf_semantics": "Dispatches a tool call via τ() notation",
        "rfc_reference": "RFC 9005",
    },
]


# ─── Registry ──────────────────────────────────────────────────────────────

class GardinerRegistry:
    """Registry mapping HLF glyphs to Gardiner sign-list entries.

    Provides lookup by Unicode character, Gardiner code, or category.
    """

    def __init__(self) -> None:
        self._signs: dict[str, GardinerSign] = {}  # unicode → sign
        self._by_code: dict[str, GardinerSign] = {}  # gardiner_code → sign
        self._load_core_mappings()

    def _load_core_mappings(self) -> None:
        for m in _CORE_MAPPINGS:
            sign = GardinerSign(**m)
            self._signs[sign.unicode_char] = sign
            self._by_code[sign.gardiner_code] = sign

    @property
    def sign_count(self) -> int:
        return len(self._signs)

    def lookup(self, unicode_char: str) -> GardinerSign | None:
        """Look up a Gardiner mapping by HLF Unicode character."""
        return self._signs.get(unicode_char)

    def lookup_by_code(self, gardiner_code: str) -> GardinerSign | None:
        """Look up by Gardiner code (e.g., 'Z6')."""
        return self._by_code.get(gardiner_code)

    def get_category(self, category: str) -> str | None:
        """Get the Gardiner category description."""
        return GARDINER_CATEGORIES.get(category)

    def list_categories(self) -> dict[str, str]:
        """List all Gardiner categories."""
        return dict(GARDINER_CATEGORIES)

    def list_by_category(self, category: str) -> list[GardinerSign]:
        """List all HLF glyphs in a Gardiner category."""
        return [
            s for s in self._signs.values()
            if s.gardiner_category == category
        ]

    def list_all(self) -> list[dict[str, Any]]:
        """List all registered glyph mappings."""
        return [s.to_dict() for s in self._signs.values()]

    def register(
        self,
        unicode_char: str,
        hlf_name: str,
        gardiner_code: str,
        gardiner_category: str,
        gardiner_description: str,
        hlf_semantics: str,
        rfc_reference: str = "",
    ) -> GardinerSign:
        """Register a new HLF→Gardiner mapping."""
        sign = GardinerSign(
            unicode_char=unicode_char,
            hlf_name=hlf_name,
            gardiner_code=gardiner_code,
            gardiner_category=gardiner_category,
            gardiner_description=gardiner_description,
            hlf_semantics=hlf_semantics,
            rfc_reference=rfc_reference,
        )
        self._signs[unicode_char] = sign
        self._by_code[gardiner_code] = sign
        return sign

    def cross_reference(self, hlf_glyph: str) -> dict[str, Any] | None:
        """Get full cross-reference: HLF semantics + Gardiner academic context."""
        sign = self.lookup(hlf_glyph)
        if not sign:
            return None
        return {
            "hlf": {
                "character": sign.unicode_char,
                "name": sign.hlf_name,
                "semantics": sign.hlf_semantics,
                "rfc": sign.rfc_reference,
            },
            "gardiner": {
                "code": sign.gardiner_code,
                "category": sign.gardiner_category,
                "category_name": GARDINER_CATEGORIES.get(
                    sign.gardiner_category, "Unknown"
                ),
                "description": sign.gardiner_description,
            },
        }

    def get_taxonomy_report(self) -> dict[str, Any]:
        """Full taxonomy report showing coverage across categories."""
        by_cat: dict[str, list[str]] = {}
        for sign in self._signs.values():
            cat = sign.gardiner_category
            by_cat.setdefault(cat, []).append(sign.unicode_char)
        return {
            "total_mappings": self.sign_count,
            "categories_used": len(by_cat),
            "categories_total": len(GARDINER_CATEGORIES),
            "coverage_pct": round(
                len(by_cat) / len(GARDINER_CATEGORIES) * 100, 1
            ),
            "by_category": {
                cat: {"description": GARDINER_CATEGORIES[cat], "glyphs": glyphs}
                for cat, glyphs in sorted(by_cat.items())
            },
        }
