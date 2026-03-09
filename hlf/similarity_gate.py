"""
Semantic Similarity Gate — Round-trip verification for HLF compilation.

Per the Genesis spec: >0.95 cosine similarity between the original
natural-language intent and the HLF output's decompressed meaning.
Detects semantic drift where the compiler produces technically valid
but semantically wrong output.

Uses a lightweight character-level n-gram approach for similarity
checking (no external ML deps). Can be upgraded to embeddings later.

Usage:
    gate = SemanticSimilarityGate(threshold=0.95)
    result = gate.check(
        original="Deploy the application to production",
        compiled="[INTENT] Deploy application → production",
    )
    if not result.passed:
        print(f"Semantic drift: {result.similarity:.3f}")
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


# ─── Result Types ───────────────────────────────────────────────────────────

@dataclass
class SimilarityResult:
    """Result of a similarity check."""
    similarity: float = 0.0
    threshold: float = 0.95
    passed: bool = False
    original_digest: str = ""
    compiled_digest: str = ""
    method: str = "ngram_cosine"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "similarity": round(self.similarity, 4),
            "threshold": self.threshold,
            "passed": self.passed,
            "method": self.method,
            "original_digest": self.original_digest,
            "compiled_digest": self.compiled_digest,
        }


@dataclass
class DriftAlert:
    """Alert for detected semantic drift."""
    original_preview: str
    compiled_preview: str
    similarity: float
    gap: float  # how far below threshold
    timestamp: float = field(default_factory=time.time)


# ─── Similarity Functions ───────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip tags, collapse spaces."""
    text = text.lower()
    # Remove HLF tag brackets
    text = re.sub(r"\[/?[A-Z_]+\]", " ", text)
    # Remove special HLF operators
    text = re.sub(r"[↦⊎⇒⇌←∥⋈≡τ→]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _char_ngrams(text: str, n: int = 3) -> Counter:
    """Extract character n-grams from text."""
    if len(text) < n:
        return Counter({text: 1}) if text else Counter()
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def _word_tokens(text: str) -> Counter:
    """Tokenize into word-level features."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return Counter(words)


def cosine_similarity(a: Counter, b: Counter) -> float:
    """Cosine similarity between two Counter vectors."""
    if not a or not b:
        return 0.0
    intersection = set(a.keys()) & set(b.keys())
    dot = sum(a[k] * b[k] for k in intersection)
    mag_a = sum(v ** 2 for v in a.values()) ** 0.5
    mag_b = sum(v ** 2 for v in b.values()) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def jaccard_similarity(a: set, b: set) -> float:
    """Jaccard index between two sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ─── Similarity Gate ────────────────────────────────────────────────────────

class SemanticSimilarityGate:
    """Round-trip semantic similarity gate for HLF compilation.

    Checks that compiled HLF output preserves the semantic intent
    of the original natural-language input.
    """

    def __init__(
        self,
        *,
        threshold: float = 0.95,
        ngram_size: int = 3,
        blend_word_weight: float = 0.6,
        blend_char_weight: float = 0.4,
    ) -> None:
        self._threshold = threshold
        self._ngram_size = ngram_size
        self._word_weight = blend_word_weight
        self._char_weight = blend_char_weight
        self._results: list[SimilarityResult] = []
        self._drift_alerts: list[DriftAlert] = []
        # Cache: hash(original+compiled) → SimilarityResult
        self._cache: dict[str, SimilarityResult] = {}

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def check_count(self) -> int:
        return len(self._results)

    def check(
        self, original: str, compiled: str
    ) -> SimilarityResult:
        """Check semantic similarity between original and compiled text.

        Uses a blended approach: word-level + character n-gram cosine.
        """
        cache_key = hashlib.md5(
            (original + "|" + compiled).encode()
        ).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        norm_orig = _normalize(original)
        norm_comp = _normalize(compiled)

        # Word-level similarity
        word_sim = cosine_similarity(
            _word_tokens(norm_orig), _word_tokens(norm_comp)
        )

        # Character n-gram similarity
        char_sim = cosine_similarity(
            _char_ngrams(norm_orig, self._ngram_size),
            _char_ngrams(norm_comp, self._ngram_size),
        )

        # Blended score
        sim = self._word_weight * word_sim + self._char_weight * char_sim

        result = SimilarityResult(
            similarity=sim,
            threshold=self._threshold,
            passed=sim >= self._threshold,
            original_digest=hashlib.sha256(original.encode()).hexdigest()[:12],
            compiled_digest=hashlib.sha256(compiled.encode()).hexdigest()[:12],
        )

        self._results.append(result)
        self._cache[cache_key] = result

        # Track drift
        if not result.passed:
            self._drift_alerts.append(DriftAlert(
                original_preview=original[:80],
                compiled_preview=compiled[:80],
                similarity=sim,
                gap=self._threshold - sim,
            ))

        return result

    def get_drift_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent drift alerts."""
        return [
            {
                "original": a.original_preview,
                "compiled": a.compiled_preview,
                "similarity": round(a.similarity, 4),
                "gap": round(a.gap, 4),
            }
            for a in reversed(self._drift_alerts[-limit:])
        ]

    def get_stats(self) -> dict[str, Any]:
        """Get gate statistics."""
        total = len(self._results)
        passed = sum(1 for r in self._results if r.passed)
        avg_sim = (
            sum(r.similarity for r in self._results) / max(1, total)
        )
        return {
            "total_checks": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate_pct": round(passed / max(1, total) * 100, 1),
            "avg_similarity": round(avg_sim, 4),
            "drift_alerts": len(self._drift_alerts),
            "threshold": self._threshold,
        }
