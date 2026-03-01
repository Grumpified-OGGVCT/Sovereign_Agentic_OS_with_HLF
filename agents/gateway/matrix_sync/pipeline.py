from __future__ import annotations
import argparse
import json
import os
import time
from dataclasses import asdict
from typing import Dict, List

from .artifacts import ensure_dir, make_manifest, make_versioned_output_dir
from .config import CLOUD_OLLAMA, LOCAL_OLLAMA, DEFAULT_FAMILIES
from .models import CardInfo
from .clients.ollama_client import (
    fetch_tags, model_root, normalize_model_id, compute_sync_actions, run_pull, best_entry_by_norm
)
from .clients.openrouter_client import fetch_rankings, find_rank_for_root
from .clients.sheets_client import upload_tabs
from .parsers.card_parser import fetch_card
from .parsers.benchmark_parser import benchmark_composite
from .scoring import (
    category_scores, score_to_tier, confidence_score, highest_strength_use_case, official_capabilities_json
)
from .diffing import build_diff
from .io.csv_io import write_csv, read_csv
from .io.json_io import write_json, write_jsonl


# ---------------------------------------------------------------------------
# Registry bridge — persist pipeline results to registry.db
# ---------------------------------------------------------------------------

def _try_import_db():
    """Lazy-import the db module to avoid hard dependency."""
    try:
        from agents.core import db
        return db
    except ImportError:
        return None


def _persist_to_registry(
    catalog_rows: List[Dict],
    matrix_rows: List[Dict],
    dup_rows: List[Dict],
    families: list[str],
    promote: bool = False,
    db_path: str | None = None,
) -> dict:
    """Persist pipeline results to registry.db.

    Creates a snapshot, upserts each cloud model with its best tier,
    upserts local inventory from dup_rows, and optionally promotes the
    snapshot to active so route_request() picks it up.

    Returns a summary dict for inclusion in the manifest.
    """
    db = _try_import_db()
    if db is None:
        return {"registry_persisted": False, "reason": "agents.core.db not importable"}

    path = db_path or str(db.db_path())
    db.init_db(path)

    # Build a lookup: model_id → best tier from matrix_rows
    # matrix_rows has per-category tiers; pick the best (lowest index in tier walk)
    _tier_rank = {"S": 0, "A+": 1, "A": 2, "A-": 3, "B+": 4, "B": 5, "C": 6, "D": 7}
    best_tier: Dict[str, str] = {}
    best_score: Dict[str, float] = {}
    for row in matrix_rows:
        mid = row["official_nomenclature"]
        tier = row["tier"]
        score = float(row.get("category_score", 0))
        if mid not in best_tier or _tier_rank.get(tier, 99) < _tier_rank.get(best_tier[mid], 99):
            best_tier[mid] = tier
            best_score[mid] = score

    with db.get_db(path) as conn:
        # 1. Create snapshot
        snap_id = db.create_snapshot(conn, families=families)

        # 2. Upsert cloud models from catalog
        upserted = 0
        for row in catalog_rows:
            mid = row["official_nomenclature"]
            tier = best_tier.get(mid, "D")
            raw_score = best_score.get(mid, 0.0)
            size_bytes = row.get("size_bytes", 0)
            try:
                param_b = float(size_bytes) / 1e9 if size_bytes else 0.0
            except (ValueError, TypeError):
                param_b = 0.0

            db.upsert_model(
                conn,
                snap_id,
                mid,
                family=row.get("family", ""),
                param_b=param_b,
                quant="",
                raw_score=raw_score,
                tier=tier,
                context_length=0,
            )
            upserted += 1

        # 3. Update snapshot model count
        db.update_snapshot_model_count(conn, snap_id, upserted)

        # 4. Upsert local inventory
        local_synced = 0
        for row in dup_rows:
            mid = row["local_model"]
            size_bytes = row.get("size_bytes", 0)
            try:
                size_gb = float(size_bytes) / 1e9 if size_bytes else 0.0
            except (ValueError, TypeError):
                size_gb = 0.0
            db.upsert_local_inventory(conn, mid, size_gb=size_gb)
            # Also persist extended metadata if available
            db.upsert_local_metadata(
                conn, mid,
                digest=row.get("digest", ""),
                modified_at=row.get("modified_at", ""),
                quantization_level="",
            )
            local_synced += 1

        # 5. Optionally promote to active
        if promote:
            db.promote_snapshot(conn, snap_id)

        conn.commit()

    return {
        "registry_persisted": True,
        "db_path": path,
        "snapshot_id": snap_id,
        "models_upserted": upserted,
        "local_synced": local_synced,
        "promoted": promote,
    }

def run_pipeline_scheduled(promote: bool = True) -> None:
    """Run the pipeline programmatically (no sys.argv).

    Equivalent to: ``python -m ... --registry-db [--promote]``
    Used by the APScheduler background job and the GitHub Actions cron trigger.
    """
    args = argparse.Namespace(
        output_dir="./out",
        versioned=False,
        cache_dir="./.cache_cards",
        families="",
        families_file="./families.txt",
        auto_discover_families=False,
        include_all_cloud=False,
        apply_pulls=False,
        dry_run=False,
        sleep_cards=0.25,
        previous_matrix="",
        gsheet_id="",
        gcp_creds="",
        registry_db=True,
        promote=promote,
    )
    run_pipeline(args)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="./out")
    ap.add_argument("--versioned", action="store_true", help="Write into out/run-<UTCSTAMP>")
    ap.add_argument("--cache-dir", default="./.cache_cards")
    ap.add_argument(
        "--families",
        default="",
        help="Comma-separated families to include (unioned with defaults + families file).",
    )
    ap.add_argument(
        "--families-file",
        default="./families.txt",
        help="Path to newline-delimited families file (supports # comments).",
    )
    ap.add_argument(
        "--auto-discover-families",
        action="store_true",
        help="Add all discovered cloud model roots to family scope.",
    )
    ap.add_argument("--include-all-cloud", action="store_true")
    ap.add_argument("--apply-pulls", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep-cards", type=float, default=0.25)
    ap.add_argument("--previous-matrix", default="")
    ap.add_argument("--gsheet-id", default="")
    ap.add_argument("--gcp-creds", default="")
    ap.add_argument(
        "--registry-db",
        action="store_true",
        help="Persist pipeline results to registry.db (SQL model catalog).",
    )
    ap.add_argument(
        "--promote",
        action="store_true",
        help="Promote the new snapshot to active (used with --registry-db).",
    )
    return ap.parse_args()

def parse_families_csv(value: str) -> set[str]:
    if not value:
        return set()
    return {x.strip().lower() for x in value.split(",") if x.strip()}

def load_families_file(path: str) -> set[str]:
    if not path or not os.path.exists(path):
        return set()
    out: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            s = s.split("#", 1)[0].strip().lower()
            if s:
                out.add(s)
    return out

def run_pipeline(args: argparse.Namespace | None = None) -> None:
    if args is None:
        args = parse_args()
    ensure_dir(args.output_dir)
    ensure_dir(args.cache_dir)
    out_dir = make_versioned_output_dir(args.output_dir) if args.versioned else args.output_dir
    ensure_dir(out_dir)

    api_key = os.getenv("OLLAMA_API_KEY")

    print("[1/9] Fetch cloud/local tags")
    cloud = fetch_tags(CLOUD_OLLAMA, api_key=api_key)
    local = fetch_tags(LOCAL_OLLAMA, api_key=None)

    default_families = {f.strip().lower() for f in DEFAULT_FAMILIES if f.strip()}
    file_families = load_families_file(args.families_file)
    cli_families = parse_families_csv(args.families)
    discovered_families = set(model_root(m.name) for m in cloud) if args.auto_discover_families else set()

    families = sorted(default_families | file_families | cli_families | discovered_families)
    include_all_cloud = args.include_all_cloud or len(families) == 0

    if include_all_cloud and not args.include_all_cloud and len(families) == 0:
        print("WARN family scope empty; falling back to --include-all-cloud behavior")

    print(
        "[scope] families: "
        f"default={len(default_families)} file={len(file_families)} "
        f"cli={len(cli_families)} discovered={len(discovered_families)} "
        f"effective={len(families)} include_all_cloud={include_all_cloud}"
    )

    def in_scope(name: str) -> bool:
        if include_all_cloud:
            return True
        root = model_root(name)
        return any(root.startswith(f) for f in families)

    cloud_scope = [m for m in cloud if in_scope(m.name)]
    local_scope = [m for m in local if in_scope(m.name)]

    print("[2/9] OpenRouter rankings")
    try:
        rankings = fetch_rankings()
    except Exception as e:
        print(f"WARN rankings failed: {e}")
        rankings = {}

    print("[3/9] Card cache")
    roots = sorted(set(model_root(m.name) for m in (cloud_scope + local_scope)))
    cards: Dict[str, CardInfo] = {}
    for i, r in enumerate(roots, 1):
        try:
            cards[r] = fetch_card(r, cache_dir=args.cache_dir)
        except Exception as e:
            cards[r] = CardInfo(
                slug=r, title=r, summary="", cap_tags=[], specialties=[],
                benchmark_mentions=[], benchmark_structured=[], context_mentions=[],
                raw_text=f"ERROR fetching card: {e}", fetched_at_utc=""
            )
        if i % 20 == 0:
            print(f"  cached {i}/{len(roots)}")
        time.sleep(args.sleep_cards)

    print("[4/9] Sync actions")
    def pull_target_fn(cloud_name: str) -> str:
        root = model_root(cloud_name)
        card = cards.get(root)
        if card and "cloud" in card.cap_tags:
            return f"{root}:cloud"
        return cloud_name

    sync_actions = compute_sync_actions(cloud_scope, local_scope, pull_target_fn=pull_target_fn)

    if args.apply_pulls and not args.dry_run:
        print("[5/9] Apply pulls")
        for a in sync_actions:
            if a.action in ("NEW_PULL", "REPULL"):
                rc, out = run_pull(a.pull_target)
                a.reason = f"{a.reason}; pull_rc={rc}; pull_log={out[:180].replace(chr(10), ' ')}"

    print("[6/9] Build outputs")
    combined_best = best_entry_by_norm(cloud_scope + local_scope)

    catalog_rows: List[Dict] = []
    matrix_rows: List[Dict] = []
    bench_long_rows: List[Dict] = []
    raw_rows: List[Dict] = []
    dup_rows: List[Dict] = []

    for l in local_scope:
        dup_rows.append({
            "local_model": l.name,
            "normalized_id": normalize_model_id(l.name),
            "family_guess": model_root(l.name),
            "is_community_fork": str("/" in l.name).lower(),
            "canonical_official_family_guess": model_root(l.name),
            "digest": l.digest,
            "modified_at": l.modified_at,
            "size_bytes": l.size or "",
        })

    for _, m in combined_best.items():
        root = model_root(m.name)
        card = cards[root]
        rank_hit = find_rank_for_root(rankings, root)
        comp = benchmark_composite(card.benchmark_structured)
        cat_scores = category_scores(m.name, card, rank_hit)
        conf = confidence_score(card, rank_hit, m.modified_at)
        use_case = highest_strength_use_case(cat_scores, card, m.name)
        cap_json = official_capabilities_json(card)

        catalog_rows.append({
            "official_nomenclature": m.name,
            "family": root,
            "is_community_fork": str("/" in m.name).lower(),
            "digest": m.digest,
            "modified_at": m.modified_at,
            "size_bytes": m.size or "",
            "official_capabilities": cap_json,
            "highest_strength_use_case": use_case,
            "benchmark_composite": comp["composite"],
            "benchmark_metric_count": comp["metric_count"],
            "confidence_score": conf,
            "openrouter_rank": rank_hit["rank"] if rank_hit else "",
            "openrouter_tokens": rank_hit["tokens"] if rank_hit else "",
            "openrouter_delta_or_share": rank_hit["delta_or_share"] if rank_hit else "",
            "source_card_slug": card.slug,
            "source_fetched_at_utc": card.fetched_at_utc,
        })

        for cat, sc in sorted(cat_scores.items(), key=lambda x: (-x[1], x[0])):
            if sc < 2.0:
                continue
            matrix_rows.append({
                "category": cat,
                "tier": score_to_tier(sc),
                "official_nomenclature": m.name,
                "official_capabilities": cap_json,
                "highest_strength_use_case": use_case,
                "category_score": round(sc, 6),
                "confidence_score": conf,
                "benchmark_composite": comp["composite"],
                "benchmark_metric_count": comp["metric_count"],
                "benchmark_evidence": " | ".join(card.benchmark_mentions[:30]),
                "specialty_notes": " | ".join(card.specialties[:30]),
                "openrouter_rank": rank_hit["rank"] if rank_hit else "",
                "openrouter_tokens": rank_hit["tokens"] if rank_hit else "",
                "openrouter_delta_or_share": rank_hit["delta_or_share"] if rank_hit else "",
                "modified_at": m.modified_at,
                "digest": m.digest,
            })

        for b in card.benchmark_structured:
            bench_long_rows.append({
                "official_nomenclature": m.name,
                "metric": b["metric"],
                "value": b["value"],
                "is_percent": b["is_percent"],
                "normalized": b["normalized"],
                "weight": b["weight"],
                "weighted_score": b["weighted_score"],
                "raw_snippet": b["raw_snippet"],
                "source_card_slug": card.slug,
            })

        raw_rows.append({
            "official_nomenclature": m.name,
            "raw_text": card.raw_text,
            "benchmark_mentions": card.benchmark_mentions,
            "benchmark_structured": card.benchmark_structured,
            "specialties": card.specialties,
            "fetched_at_utc": card.fetched_at_utc,
        })

    print("[7/9] Diff")
    diff_rows, promoted_rows, demoted_rows = [], [], []
    if args.previous_matrix and os.path.exists(args.previous_matrix):
        prev = read_csv(args.previous_matrix)
        diff_rows = build_diff(prev, matrix_rows)
        promoted_rows = [r for r in diff_rows if r["change_type"] == "PROMOTED"]
        demoted_rows = [r for r in diff_rows if r["change_type"] == "DEMOTED"]

    print("[8/9] Write artifacts")
    write_csv(
        os.path.join(out_dir, "sync_actions.csv"),
        [asdict(a) for a in sync_actions],
        ["normalized_id","cloud_name","local_name","action","reason","pull_target","cloud_digest","local_digest","cloud_modified_at","local_modified_at"]
    )
    write_csv(
        os.path.join(out_dir, "model_catalog.csv"),
        catalog_rows,
        ["official_nomenclature","family","is_community_fork","digest","modified_at","size_bytes","official_capabilities","highest_strength_use_case","benchmark_composite","benchmark_metric_count","confidence_score","openrouter_rank","openrouter_tokens","openrouter_delta_or_share","source_card_slug","source_fetched_at_utc"]
    )
    write_csv(
        os.path.join(out_dir, "matrix.csv"),
        matrix_rows,
        ["category","tier","official_nomenclature","official_capabilities","highest_strength_use_case","category_score","confidence_score","benchmark_composite","benchmark_metric_count","benchmark_evidence","specialty_notes","openrouter_rank","openrouter_tokens","openrouter_delta_or_share","modified_at","digest"]
    )
    write_csv(
        os.path.join(out_dir, "benchmark_long.csv"),
        bench_long_rows,
        ["official_nomenclature","metric","value","is_percent","normalized","weight","weighted_score","raw_snippet","source_card_slug"]
    )
    write_csv(
        os.path.join(out_dir, "duplicates_map.csv"),
        dup_rows,
        ["local_model","normalized_id","family_guess","is_community_fork","canonical_official_family_guess","digest","modified_at","size_bytes"]
    )
    write_jsonl(os.path.join(out_dir, "model_catalog.raw.jsonl"), raw_rows)

    if diff_rows:
        write_csv(
            os.path.join(out_dir, "matrix_diff.csv"),
            diff_rows,
            ["category","official_nomenclature","change_type","prev_tier","curr_tier","prev_score","curr_score","prev_confidence","curr_confidence","tier_delta","score_delta","confidence_delta"]
        )
        write_csv(
            os.path.join(out_dir, "promoted.csv"),
            promoted_rows,
            ["category","official_nomenclature","change_type","prev_tier","curr_tier","prev_score","curr_score","prev_confidence","curr_confidence","tier_delta","score_delta","confidence_delta"]
        )
        write_csv(
            os.path.join(out_dir, "demoted.csv"),
            demoted_rows,
            ["category","official_nomenclature","change_type","prev_tier","curr_tier","prev_score","curr_score","prev_confidence","curr_confidence","tier_delta","score_delta","confidence_delta"]
        )

    manifest = make_manifest(
        output_dir=os.path.abspath(out_dir),
        cloud_scope_count=len(cloud_scope),
        local_scope_count=len(local_scope),
        cards_count=len(cards),
        sync_actions_count=len(sync_actions),
        catalog_rows=len(catalog_rows),
        matrix_rows=len(matrix_rows),
        benchmark_long_rows=len(bench_long_rows),
        diff_rows=len(diff_rows),
        promoted_rows=len(promoted_rows),
        demoted_rows=len(demoted_rows),
        args=vars(args),
    )
    write_json(os.path.join(out_dir, "run_manifest.json"), manifest)

    if args.gsheet_id and args.gcp_creds:
        print("[9/9] Upload Sheets")
        upload_tabs(args.gsheet_id, args.gcp_creds, {
            "Matrix": matrix_rows,
            "Catalog": catalog_rows,
            "BenchmarkLong": bench_long_rows,
            "SyncActions": [asdict(a) for a in sync_actions],
            "Diff": diff_rows if diff_rows else [{"note": "No diff rows"}],
            "Promoted": promoted_rows if promoted_rows else [{"note": "No promoted rows"}],
            "Demoted": demoted_rows if demoted_rows else [{"note": "No demoted rows"}],
            "Manifest": [manifest],
        })

    # ── Registry Bridge ──────────────────────────────────────────────────
    registry_summary = {"registry_persisted": False, "reason": "--registry-db not set"}
    if args.registry_db and not args.dry_run:
        print("[9.5/9] Persist to registry.db")
        registry_summary = _persist_to_registry(
            catalog_rows=catalog_rows,
            matrix_rows=matrix_rows,
            dup_rows=dup_rows,
            families=families,
            promote=args.promote,
        )
        print(f"  → snapshot #{registry_summary.get('snapshot_id', '?')}: "
              f"{registry_summary.get('models_upserted', 0)} models, "
              f"{registry_summary.get('local_synced', 0)} local, "
              f"promoted={registry_summary.get('promoted', False)}")
    manifest["registry"] = registry_summary

    print("Done.")
    print(json.dumps(manifest, indent=2))
