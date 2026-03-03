from __future__ import annotations

from .config import TIER_RANK


def build_diff(previous_rows: list[dict[str, str]], current_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    def key(r):
        return (r.get("category", ""), r.get("official_nomenclature", ""))

    prev = {key(r): r for r in previous_rows}
    curr = {key(r): r for r in current_rows}
    out: list[dict[str, str]] = []

    all_keys = sorted(set(prev.keys()) | set(curr.keys()))
    for k in all_keys:
        p = prev.get(k)
        c = curr.get(k)

        if p and not c:
            out.append(
                {
                    "category": k[0],
                    "official_nomenclature": k[1],
                    "change_type": "REMOVED",
                    "prev_tier": p.get("tier", ""),
                    "curr_tier": "",
                    "prev_score": p.get("category_score", ""),
                    "curr_score": "",
                    "prev_confidence": p.get("confidence_score", ""),
                    "curr_confidence": "",
                    "tier_delta": "",
                    "score_delta": "",
                    "confidence_delta": "",
                }
            )
            continue

        if c and not p:
            out.append(
                {
                    "category": k[0],
                    "official_nomenclature": k[1],
                    "change_type": "NEW_ENTRY",
                    "prev_tier": "",
                    "curr_tier": c.get("tier", ""),
                    "prev_score": "",
                    "curr_score": c.get("category_score", ""),
                    "prev_confidence": "",
                    "curr_confidence": c.get("confidence_score", ""),
                    "tier_delta": "",
                    "score_delta": "",
                    "confidence_delta": "",
                }
            )
            continue

        pt = p.get("tier", "")
        ct = c.get("tier", "")
        pr = TIER_RANK.get(pt, 999)
        cr = TIER_RANK.get(ct, 999)

        if cr < pr:
            change = "PROMOTED"
        elif cr > pr:
            change = "DEMOTED"
        else:
            change = "UNCHANGED"

        def to_float(v: str):
            try:
                return float(v)
            except Exception:
                return None

        ps, cs = to_float(p.get("category_score", "")), to_float(c.get("category_score", ""))
        pc, cc = to_float(p.get("confidence_score", "")), to_float(c.get("confidence_score", ""))

        out.append(
            {
                "category": k[0],
                "official_nomenclature": k[1],
                "change_type": change,
                "prev_tier": pt,
                "curr_tier": ct,
                "prev_score": p.get("category_score", ""),
                "curr_score": c.get("category_score", ""),
                "prev_confidence": p.get("confidence_score", ""),
                "curr_confidence": c.get("confidence_score", ""),
                "tier_delta": str((pr - cr) if pr != 999 and cr != 999 else ""),
                "score_delta": str(round(cs - ps, 6)) if ps is not None and cs is not None else "",
                "confidence_delta": str(round(cc - pc, 6)) if pc is not None and cc is not None else "",
            }
        )

    return out
