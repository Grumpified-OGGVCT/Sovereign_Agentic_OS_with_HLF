from __future__ import annotations
import re
from typing import Dict, List, Tuple, Any

from ..config import BENCHMARK_WEIGHTS, BENCHMARK_MAX

def parse_benchmark_mentions(raw_text: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    mentions: List[str] = []
    structured: List[Dict[str, Any]] = []

    for metric, weight in BENCHMARK_WEIGHTS.items():
        rgx = re.compile(rf"({re.escape(metric)}[^.\n]{{0,120}}?(\d+(?:\.\d+)?)(%?)?)", re.IGNORECASE)
        for m in rgx.finditer(raw_text):
            snippet = m.group(1).strip()
            n = float(m.group(2))
            pct = (m.group(3) == "%")
            maxv = BENCHMARK_MAX.get(metric, 100.0)
            normalized = (n / 100.0) if pct else min(n / maxv, 1.0)
            normalized = max(0.0, min(normalized, 1.0))

            mentions.append(snippet)
            structured.append({
                "metric": metric,
                "raw_snippet": snippet,
                "value": n,
                "is_percent": pct,
                "normalized": round(normalized, 6),
                "weight": weight,
                "weighted_score": round(normalized * weight, 6),
            })

    dedup_mentions = list(dict.fromkeys(mentions))

    best_by_metric: Dict[str, Dict[str, Any]] = {}
    for row in structured:
        m = row["metric"]
        if (m not in best_by_metric) or (row["weighted_score"] > best_by_metric[m]["weighted_score"]):
            best_by_metric[m] = row

    return dedup_mentions, list(best_by_metric.values())

def benchmark_composite(structured: List[Dict[str, Any]]) -> Dict[str, float]:
    if not structured:
        return {"weighted_sum": 0.0, "weight_sum": 0.0, "composite": 0.0, "metric_count": 0}
    weighted_sum = sum(x["weighted_score"] for x in structured)
    weight_sum = sum(x["weight"] for x in structured) or 1e-9
    return {
        "weighted_sum": round(weighted_sum, 6),
        "weight_sum": round(weight_sum, 6),
        "composite": round(weighted_sum / weight_sum, 6),
        "metric_count": len(structured),
    }
