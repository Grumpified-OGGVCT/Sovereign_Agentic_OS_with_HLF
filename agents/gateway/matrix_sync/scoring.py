from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from .models import CardInfo
from .parsers.benchmark_parser import benchmark_composite


def model_root(name: str) -> str:
    return (name or "").split(":")[0].strip().lower()


def category_scores(model_name: str, card: CardInfo, rank_info: dict[str, Any] | None) -> dict[str, float]:
    txt = " ".join([card.summary] + card.specialties + card.benchmark_mentions).lower()
    caps = set(card.cap_tags)
    comp = benchmark_composite(card.benchmark_structured)["composite"]

    s = defaultdict(float)

    for kw in ["coding", "code", "swe", "terminal", "repo", "multi-file", "developer", "software engineering"]:
        if kw in txt:
            s["Programming & Code Engineering"] += 1.2
    if "tools" in caps:
        s["Programming & Code Engineering"] += 1.0
    s["Programming & Code Engineering"] += comp * 5.0

    for kw in ["reasoning", "thinking", "aime", "gpqa", "math", "long-horizon", "planning"]:
        if kw in txt:
            s["Reasoning & Long-Horizon Planning"] += 1.0
    if "thinking" in caps:
        s["Reasoning & Long-Horizon Planning"] += 1.2
    s["Reasoning & Long-Horizon Planning"] += comp * 4.5

    for kw in ["vision", "vl", "ocr", "image", "video", "gui", "multimodal", "document"]:
        if kw in txt:
            s["Vision & Multimodal Intelligence"] += 1.0
    if "vision" in caps:
        s["Vision & Multimodal Intelligence"] += 1.3
    s["Vision & Multimodal Intelligence"] += comp * 3.8

    for kw in ["tool", "function", "json", "structured output", "enterprise", "production", "workflow", "agent"]:
        if kw in txt:
            s["Tool Use & Enterprise Orchestration"] += 0.9
    if "tools" in caps:
        s["Tool Use & Enterprise Orchestration"] += 1.0
    s["Tool Use & Enterprise Orchestration"] += comp * 3.5

    for kw in ["multilingual", "translation", "localization", "languages", "global"]:
        if kw in txt:
            s["Multilingual & Localization"] += 1.0
    s["Multilingual & Localization"] += comp * 2.2

    for kw in ["edge", "single gpu", "on-device", "efficient", "compact", "small"]:
        if kw in txt:
            s["Edge & Efficient Deployment"] += 0.8
    root = model_root(model_name)
    if root.startswith(("ministral-3", "gemma3", "rnj-1", "nemotron-3-nano")):
        s["Edge & Efficient Deployment"] += 1.5
    s["Edge & Efficient Deployment"] += comp * 2.0

    for kw in ["open-weight", "mit license", "commercial use", "developer use", "open source"]:
        if kw in txt:
            s["Open-Weight Developer Stack"] += 0.9
    if root.startswith(("gpt-oss", "cogito", "rnj-1")):
        s["Open-Weight Developer Stack"] += 1.2
    s["Open-Weight Developer Stack"] += comp * 2.0

    s["Generalist Frontier Assistant"] += len(caps) * 0.9 + comp * 4.0
    if rank_info:
        r = rank_info.get("rank", 999)
        if r <= 3:
            s["Generalist Frontier Assistant"] += 2.0
        elif r <= 10:
            s["Generalist Frontier Assistant"] += 1.0

    return dict(s)


def score_to_tier(score: float) -> str:
    if score >= 10.5:
        return "S"
    if score >= 8.5:
        return "A+"
    if score >= 7.0:
        return "A"
    if score >= 5.8:
        return "A-"
    if score >= 4.5:
        return "B+"
    if score >= 3.2:
        return "B"
    if score >= 2.0:
        return "C"
    return "D"


def confidence_score(card: CardInfo, rank_info: dict[str, Any] | None, modified_at: str) -> float:
    bench_count = len(card.benchmark_structured)
    bench_component = min(bench_count / 8.0, 1.0) * 0.40
    cap_component = min(len(card.cap_tags) / 4.0, 1.0) * 0.15
    spec_component = min(len(card.specialties) / 12.0, 1.0) * 0.15
    rank_component = 0.15 if rank_info else 0.0

    rec = 0.0
    try:
        m = dt.datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
        days = (dt.datetime.now(dt.UTC) - m).days
        if days <= 14:
            rec = 0.15
        elif days <= 45:
            rec = 0.11
        elif days <= 90:
            rec = 0.08
        elif days <= 180:
            rec = 0.05
        else:
            rec = 0.02
    except Exception:
        rec = 0.02

    return round(max(0.0, min(1.0, bench_component + cap_component + spec_component + rank_component + rec)), 4)


def highest_strength_use_case(cat_scores: dict[str, float], card: CardInfo, model_name: str) -> str:
    top = max(cat_scores.items(), key=lambda x: x[1])[0] if cat_scores else "Generalist Frontier Assistant"
    text = " ".join(card.specialties + card.benchmark_mentions).lower()
    root = model_root(model_name)

    if top == "Programming & Code Engineering":
        if "swe-bench" in text:
            return "Repository-scale coding agents (plan/edit/test/fix) with SWE-oriented reliability"
        return "Multi-file coding, refactoring, and tool-driven debugging"
    if top == "Vision & Multimodal Intelligence":
        if any(k in text for k in ["ocr", "document", "gui"]):
            return "Document/OCR/UI-grounded multimodal automation"
        return "General multimodal reasoning across image-text/video inputs"
    if top == "Reasoning & Long-Horizon Planning":
        return "Deep multi-step reasoning and long-horizon task decomposition"
    if top == "Tool Use & Enterprise Orchestration":
        return "Function/tool-calling orchestration with structured outputs"
    if top == "Edge & Efficient Deployment":
        return "Low-latency constrained-hardware inference"
    if top == "Multilingual & Localization":
        return "Cross-language localization and multilingual assistant workloads"

    if root.startswith("qwen3-vl"):
        return "Vision-language agents for OCR, documents, GUI navigation, and extraction"
    if root.startswith("mistral-large-3"):
        return "Production-grade enterprise assistant with robust tool orchestration"
    return "Balanced general-purpose assistant across reasoning, tools, and multimodal tasks"


def official_capabilities_json(card: CardInfo) -> str:
    import json

    payload = {
        "tags": card.cap_tags,
        "specialties_top": card.specialties[:10],
        "context_mentions": card.context_mentions,
    }
    return json.dumps(payload, ensure_ascii=False)
