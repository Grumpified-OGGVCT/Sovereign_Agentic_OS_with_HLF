"""
Fractal Summarization Engine
Executes map-reduce summarization using a local lightweight model (e.g. qwen:7b)
to compress massive Rolling_Context chunks down to <1500 tokens to prevent prompt bloat.
"""

from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama-matrix:11434")
_SUMMARIZATION_MODEL = os.environ.get("SUMMARIZATION_MODEL", "qwen:7b")


class FractalSummarizer:
    @staticmethod
    def _call_ollama_summarize(chunk: str) -> str:
        """Synchronous call to the Ollama container for map/reduce summary."""
        try:
            resp = requests.post(
                f"{_OLLAMA_HOST}/api/generate",
                json={
                    "model": _SUMMARIZATION_MODEL,
                    "prompt": (
                        "Summarize the following core operational context tightly, "
                        f"retaining exact factual entities:\n{chunk}"
                    ),
                    "stream": False,
                },
                timeout=45.0,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.exceptions.RequestException as e:
            logger.error(f"Fractal summarize map-op failed: {e}")
            raise RuntimeError(f"Summarization failure: {e}") from e

    @classmethod
    def summarize_context(cls, raw_context: str, target_tokens: int = 1500) -> str:
        """
        Recursively break down and summarize context to fit the target threshold.
        """
        # Fast rough estimation: ~4 chars per token average on typical english text.
        # This provides a 0-dependency baseline heuristic that scales safely.
        max_chars = int(target_tokens * 4)

        if len(raw_context) <= max_chars:
            return raw_context

        logger.info(f"Fractal reduce triggered. Context size {len(raw_context)} chars > Max {max_chars} chars")

        # Split into manageable map-chunks designed to fit into OOM-safe context buffers
        chunk_size = 4000
        chunks = [raw_context[i : i + chunk_size] for i in range(0, len(raw_context), chunk_size)]

        # Map phase
        summaries = []
        for i, c in enumerate(chunks):
            try:
                logger.debug(f"Summarizing chunk {i + 1}/{len(chunks)}...")
                res = cls._call_ollama_summarize(c)
                summaries.append(res)
            except Exception as e:
                # If a chunk fails, we attempt to preserve its raw context to avoid total amnesia
                # but truncated so it doesn't perpetually blow the budget.
                logger.warning(f"Chunk {i + 1} failed ({e}), truncating raw fallback.")
                summaries.append(c[: chunk_size // 4])

        # Reduce phase
        combined = " ".join(summaries)

        # Recursive compression if still too large
        if len(combined) > max_chars:
            return cls.summarize_context(combined, target_tokens)

        return combined

    @classmethod
    def summarize_with_stats(cls, raw_context: str, target_tokens: int = 1500) -> dict[str, object]:
        """
        Summarize context and return the result alongside compression metrics.

        This method is the observability-aware variant of :meth:`summarize_context`.
        It records wall-clock duration and compression ratio so callers can log
        the stats to the ALS Merkle chain or surface them in the GUI dashboard.

        Returns:
            dict with keys:
                - ``summary`` (str): compressed context text
                - ``original_chars`` (int): length of *raw_context* in characters
                - ``summary_chars`` (int): length of *summary* in characters
                - ``compression_ratio`` (float): original / summary (>1 means smaller)
                - ``duration_ms`` (float): wall-clock time spent in milliseconds
                - ``model`` (str): the SUMMARIZATION_MODEL used
        """
        t0 = time.monotonic()
        summary = cls.summarize_context(raw_context, target_tokens=target_tokens)
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        original_chars = len(raw_context)
        summary_chars = len(summary)

        if summary_chars == 0:
            logger.warning("summarize_with_stats: summary is empty — possible model failure; compression_ratio set to 0")
            ratio = 0.0
        else:
            ratio = original_chars / summary_chars

        return {
            "summary": summary,
            "original_chars": original_chars,
            "summary_chars": summary_chars,
            "compression_ratio": round(ratio, 4),
            "duration_ms": round(elapsed_ms, 2),
            "model": _SUMMARIZATION_MODEL,
        }
