from __future__ import annotations

import os

LOCAL_OLLAMA = "http://localhost:11434/api/tags"
CLOUD_OLLAMA = "https://ollama.com/api/tags"
OLLAMA_LIBRARY_BASE = "https://ollama.com/library/"
OPENROUTER_RANKINGS = "https://openrouter.ai/rankings"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
RETRIES = 3
RETRY_BACKOFF = 1.4

DEFAULT_FAMILIES = [
    "qwen3.5","glm-5","minimax-m2.5","qwen3-coder-next","kimi-k2.5","glm-4.7",
    "minimax-m2.1","gemini-3-flash-preview","nemotron-3-nano","devstral-small-2",
    "rnj-1","deepseek-v3.2","devstral-2","qwen3-next","mistral-large-3","ministral-3",
    "cogito-2.1","kimi-k2-thinking","minimax-m2","glm-4.6","qwen3-vl","kimi-k2",
    "deepseek-v3.1","gpt-oss","qwen3-coder","gemma3",
]

KNOWN_CAP_TAGS = {"vision", "tools", "thinking", "cloud"}

BENCHMARK_WEIGHTS = {
    "SWE-Bench Verified": 1.00,
    "SWE-Bench": 0.90,
    "Multi-SWE-Bench": 0.85,
    "LiveCodeBench": 0.80,
    "Terminal-Bench": 0.75,
    "BrowseComp": 0.80,
    "AIME": 0.85,
    "GPQA": 0.80,
    "MMLU-Pro": 0.70,
    "MMLU": 0.60,
    "MMMU": 0.70,
    "HLE": 0.75,
    "HumanEval": 0.60,
    "MBPP": 0.50,
    "OSWorld": 0.70,
    "GSM8K": 0.40,
}
BENCHMARK_MAX = {k: 100.0 for k in BENCHMARK_WEIGHTS}

TIER_ORDER = ["S", "A+", "A", "A-", "B+", "B", "C", "D"]
TIER_RANK = {t: i for i, t in enumerate(TIER_ORDER)}
