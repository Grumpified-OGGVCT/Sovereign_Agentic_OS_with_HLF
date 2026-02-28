from __future__ import annotations
import datetime as dt
import os
from typing import Dict, Any

def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def make_versioned_output_dir(base_out: str, run_id: str | None = None) -> str:
    stamp = utc_stamp() if run_id is None else run_id
    out = os.path.join(base_out, f"run-{stamp}")
    ensure_dir(out)
    return out

def make_manifest(**kwargs: Any) -> Dict[str, Any]:
    m = {"run_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    m.update(kwargs)
    return m
