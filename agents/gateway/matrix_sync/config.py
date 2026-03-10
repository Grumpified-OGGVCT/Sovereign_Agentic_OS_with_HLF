from __future__ import annotations
import os

LOCAL_OLLAMA = "http://localhost:11434/api/tags"
CLOUD_OLLAMA = "https://ollama.com/api/tags"
OLLAMA_LIBRARY_BASE = "https://ollama.com/library/"
OPENROUTER_RANKINGS = "https://openrouter.ai/rankings"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
RETRIES = 3
RETRY_BACKOFF = 1.4

# ── Cloud Model Catalog ──────────────────────────────────────────────────────
# Verified against individual model tag pages on ollama.com (March 2026).
# "cloud_tags" = exact tag names from ollama.com/library/<family>/tags
# "caps" = capability badges shown on the model card
# "ctx" = context window in tokens (from tag page)
# "pulls" = approximate total pull count at snapshot time

CLOUD_CATALOG: dict[str, dict] = {
    # ── Frontier / Latest (updated ≤ 2 months) ──────────────────────────────
    "qwen3.5": {
        "desc": "Open-source multimodal; exceptional utility and performance",
        "caps": {"vision", "tools", "thinking", "cloud"},
        "cloud_tags": ["qwen3.5:cloud"],
        "local_tags": ["qwen3.5:0.8b", "qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b",
                        "qwen3.5:27b", "qwen3.5:35b", "qwen3.5:122b"],
        "max_b": 122, "ctx": 256_000, "pulls": 1_100_000, "tags": 30,
    },
    "glm-5": {
        "desc": "Strong reasoning/agentic from Z.ai; 744B total (40B active)",
        "caps": {"cloud"},
        "cloud_tags": ["glm-5:cloud"],
        "local_tags": [],
        "max_b": 744, "ctx": 198_000, "pulls": 92_000, "tags": 1,
    },
    "minimax-m2.5": {
        "desc": "SOTA LLM for real-world productivity and coding tasks",
        "caps": {"cloud"},
        "cloud_tags": ["minimax-m2.5:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 102_900, "tags": 1,
    },
    "qwen3-coder-next": {
        "desc": "Coding-focused; optimized for agentic coding workflows",
        "caps": {"tools", "cloud"},
        "cloud_tags": ["qwen3-coder-next:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 749_600, "tags": 4,
    },
    "kimi-k2.5": {
        "desc": "Native multimodal agentic; vision+language, thinking modes",
        "caps": {"cloud"},
        "cloud_tags": ["kimi-k2.5:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 132_000, "tags": 1,
    },
    "glm-4.7": {
        "desc": "Advancing the coding capability",
        "caps": {"cloud"},
        "cloud_tags": ["glm-4.7:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 61_500, "tags": 1,
    },
    "minimax-m2.1": {
        "desc": "Exceptional multilingual capabilities for code engineering",
        "caps": {"cloud"},
        "cloud_tags": ["minimax-m2.1:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 22_500, "tags": 1,
    },

    # ── Established (updated 2-3 months) ─────────────────────────────────────
    "gemini-3-flash-preview": {
        "desc": "Frontier intelligence built for speed at fraction of cost",
        "caps": {"cloud"},
        "cloud_tags": ["gemini-3-flash-preview:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 76_000, "tags": 2,
    },
    "nemotron-3-nano": {
        "desc": "Efficient, open, intelligent agentic models from NVIDIA",
        "caps": {"tools", "thinking", "cloud"},
        "cloud_tags": ["nemotron-3-nano:30b-cloud"],
        "local_tags": ["nemotron-3-nano:30b"],
        "max_b": 30, "ctx": 1_000_000, "pulls": 201_100, "tags": 6,
    },
    "devstral-small-2": {
        "desc": "24B; excels at tool use, codebase exploration, multi-file editing",
        "caps": {"vision", "tools", "cloud"},
        "cloud_tags": ["devstral-small-2:24b-cloud"],
        "local_tags": ["devstral-small-2:24b"],
        "max_b": 24, "ctx": None, "pulls": 396_500, "tags": 6,
    },
    "rnj-1": {
        "desc": "8B dense by Essential AI; optimized for code and STEM",
        "caps": {"tools", "cloud"},
        "cloud_tags": ["rnj-1:8b-cloud"],
        "local_tags": ["rnj-1:8b"],
        "max_b": 8, "ctx": None, "pulls": 343_800, "tags": 6,
    },
    "deepseek-v3.2": {
        "desc": "High efficiency with superior reasoning and agent performance",
        "caps": {"cloud"},
        "cloud_tags": ["deepseek-v3.2:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 43_300, "tags": 1,
    },
    "devstral-2": {
        "desc": "123B; excels at tool use, codebase exploration, multi-file editing",
        "caps": {"tools", "cloud"},
        "cloud_tags": ["devstral-2:123b-cloud"],
        "local_tags": ["devstral-2:123b"],
        "max_b": 123, "ctx": None, "pulls": 104_700, "tags": 6,
    },
    "ministral-3": {
        "desc": "Edge deployment; runs on wide range of hardware",
        "caps": {"vision", "tools", "cloud"},
        "cloud_tags": ["ministral-3:3b-cloud", "ministral-3:8b-cloud", "ministral-3:14b-cloud"],
        "local_tags": ["ministral-3:3b", "ministral-3:8b", "ministral-3:14b"],
        "max_b": 14, "ctx": None, "pulls": 562_000, "tags": 16,
    },
    "gemma3": {
        "desc": "Most capable model that runs on a single GPU",
        "caps": {"vision", "cloud"},
        "cloud_tags": ["gemma3:27b-cloud"],
        "local_tags": ["gemma3:270m", "gemma3:1b", "gemma3:4b", "gemma3:12b", "gemma3:27b"],
        "max_b": 27, "ctx": None, "pulls": 32_900_000, "tags": 29,
    },

    # ── Mature (updated 3-5 months) ──────────────────────────────────────────
    "qwen3-next": {
        "desc": "Strong param efficiency and inference speed; tools+thinking",
        "caps": {"tools", "thinking", "cloud"},
        "cloud_tags": ["qwen3-next:80b-cloud"],
        "local_tags": ["qwen3-next:80b"],
        "max_b": 80, "ctx": None, "pulls": 364_300, "tags": 10,
    },
    "mistral-large-3": {
        "desc": "General-purpose multimodal MoE for production/enterprise",
        "caps": {"cloud"},
        "cloud_tags": ["mistral-large-3:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 23_300, "tags": 1,
    },
    "cogito-2.1": {
        "desc": "Instruction-tuned generative models; MIT licensed",
        "caps": {"cloud"},
        "cloud_tags": ["cogito-2.1:671b-cloud"],
        "local_tags": ["cogito-2.1:671b"],
        "max_b": 671, "ctx": 160_000, "pulls": 85_400, "tags": 6,
    },
    "kimi-k2-thinking": {
        "desc": "Moonshot AI's best open-source thinking model",
        "caps": {"cloud"},
        "cloud_tags": ["kimi-k2-thinking:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 35_200, "tags": 1,
    },
    "minimax-m2": {
        "desc": "High-efficiency LLM for coding and agentic workflows",
        "caps": {"cloud"},
        "cloud_tags": ["minimax-m2:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 74_000, "tags": 1,
    },
    "glm-4.6": {
        "desc": "Advanced agentic, reasoning and coding capabilities",
        "caps": {"cloud"},
        "cloud_tags": ["glm-4.6:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 80_500, "tags": 1,
    },
    "qwen3-vl": {
        "desc": "Most powerful vision-language model in the Qwen family",
        "caps": {"vision", "tools", "thinking", "cloud"},
        "cloud_tags": ["qwen3-vl:235b-cloud", "qwen3-vl:235b-instruct-cloud"],
        "local_tags": ["qwen3-vl:2b", "qwen3-vl:4b", "qwen3-vl:8b",
                        "qwen3-vl:30b", "qwen3-vl:32b", "qwen3-vl:235b"],
        "max_b": 235, "ctx": 256_000, "pulls": 1_900_000, "tags": 59,
    },
    "kimi-k2": {
        "desc": "SOTA MoE language model; strong on coding agent tasks",
        "caps": {"cloud"},
        "cloud_tags": ["kimi-k2:cloud"],
        "local_tags": [],
        "max_b": None, "ctx": None, "pulls": 43_100, "tags": 1,
    },
    "deepseek-v3.1": {
        "desc": "Hybrid thinking/non-thinking model with tool support",
        "caps": {"tools", "thinking", "cloud"},
        "cloud_tags": ["deepseek-v3.1:671b-cloud"],
        "local_tags": ["deepseek-v3.1:671b"],
        "max_b": 671, "ctx": 160_000, "pulls": 423_600, "tags": 8,
    },
    "gpt-oss": {
        "desc": "OpenAI open-weight; powerful reasoning and agentic tasks",
        "caps": {"tools", "thinking", "cloud"},
        "cloud_tags": ["gpt-oss:20b-cloud", "gpt-oss:120b-cloud"],
        "local_tags": ["gpt-oss:20b", "gpt-oss:120b"],
        "max_b": 120, "ctx": 128_000, "pulls": 7_500_000, "tags": 5,
    },
    "qwen3-coder": {
        "desc": "Performant long-context models for agentic and coding tasks",
        "caps": {"tools", "cloud"},
        "cloud_tags": ["qwen3-coder:480b-cloud"],
        "local_tags": ["qwen3-coder:30b", "qwen3-coder:480b"],
        "max_b": 480, "ctx": 256_000, "pulls": 3_400_000, "tags": 10,
    },
}

DEFAULT_FAMILIES = list(CLOUD_CATALOG.keys())

KNOWN_CAP_TAGS = {"vision", "tools", "thinking", "cloud"}

# ── Capability-based model recommendations ───────────────────────────────────

def models_with_caps(*caps: str) -> list[str]:
    """Return family names that have ALL the requested capabilities."""
    required = set(caps)
    return [f for f, info in CLOUD_CATALOG.items() if required <= info["caps"]]

def best_cloud_tag(family: str) -> str | None:
    """Return the strongest (largest) cloud tag for a given family."""
    info = CLOUD_CATALOG.get(family)
    if not info:
        return None
    tags = info.get("cloud_tags", [])
    return tags[-1] if tags else None

def all_cloud_tags() -> list[str]:
    """Return all known cloud tags across all families."""
    tags = []
    for info in CLOUD_CATALOG.values():
        tags.extend(info.get("cloud_tags", []))
    return tags

# Convenience groupings
VISION_MODELS = models_with_caps("vision", "cloud")
TOOL_MODELS = models_with_caps("tools", "cloud")
THINKING_MODELS = models_with_caps("thinking", "cloud")
FULL_STACK_MODELS = models_with_caps("vision", "tools", "thinking", "cloud")

# ── Cloud model rankings by max parameter count ──────────────────────────────

CLOUD_BY_STRENGTH = sorted(
    [(f, info) for f, info in CLOUD_CATALOG.items() if info.get("max_b")],
    key=lambda x: x[1]["max_b"],
    reverse=True,
)

# ── Cloud model rankings by popularity ───────────────────────────────────────

CLOUD_BY_POPULARITY = sorted(
    CLOUD_CATALOG.items(),
    key=lambda x: x[1]["pulls"],
    reverse=True,
)

# ── Benchmark weights ────────────────────────────────────────────────────────

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
