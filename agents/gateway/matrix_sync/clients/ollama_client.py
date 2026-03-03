from __future__ import annotations

import datetime as dt
import time
from collections import defaultdict

import requests

from ..config import REQUEST_TIMEOUT, RETRIES, RETRY_BACKOFF
from ..models import ModelTag, SyncAction


def parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def normalize_model_id(name: str) -> str:
    s = (name or "").strip().lower()
    return s.replace(":latest", "")


def model_root(name: str) -> str:
    return (name or "").split(":")[0].strip().lower()


def retry_get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    last_err = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, headers=headers or {}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(RETRY_BACKOFF**i)
    raise RuntimeError(f"GET failed {url}: {last_err}")


def fetch_tags(url: str, api_key: str | None = None) -> list[ModelTag]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = retry_get_json(url, headers=headers)
    out: list[ModelTag] = []
    for m in data.get("models", []):
        out.append(
            ModelTag(
                name=m.get("name", ""),
                model=m.get("model", ""),
                modified_at=m.get("modified_at", ""),
                size=m.get("size"),
                digest=m.get("digest", ""),
                details=m.get("details", {}) or {},
            )
        )
    return out


def best_entry_by_norm(tags: list[ModelTag]) -> dict[str, ModelTag]:
    bucket = defaultdict(list)
    for t in tags:
        bucket[normalize_model_id(t.name)].append(t)
    out = {}
    for k, vals in bucket.items():
        vals = sorted(
            vals, key=lambda x: parse_iso(x.modified_at) or dt.datetime.min.replace(tzinfo=dt.UTC), reverse=True
        )
        out[k] = vals[0]
    return out


def compute_sync_actions(cloud: list[ModelTag], local: list[ModelTag], pull_target_fn) -> list[SyncAction]:
    local_map = best_entry_by_norm(local)
    actions: list[SyncAction] = []
    for c in cloud:
        nid = normalize_model_id(c.name)
        l = local_map.get(nid)
        pull_target = pull_target_fn(c.name)

        if l is None:
            actions.append(
                SyncAction(
                    normalized_id=nid,
                    cloud_name=c.name,
                    local_name="",
                    action="NEW_PULL",
                    reason="exists in cloud, missing local",
                    pull_target=pull_target,
                    cloud_digest=c.digest,
                    local_digest="",
                    cloud_modified_at=c.modified_at,
                    local_modified_at="",
                )
            )
            continue

        cdt, ldt = parse_iso(c.modified_at), parse_iso(l.modified_at)
        if c.digest and l.digest and c.digest != l.digest:
            action, reason = "REPULL", "digest changed"
        elif cdt and ldt and cdt > ldt:
            action, reason = "REPULL", "cloud newer modified_at"
        else:
            action, reason = "NOOP", "up-to-date"

        actions.append(
            SyncAction(
                normalized_id=nid,
                cloud_name=c.name,
                local_name=l.name,
                action=action,
                reason=reason,
                pull_target=pull_target,
                cloud_digest=c.digest,
                local_digest=l.digest,
                cloud_modified_at=c.modified_at,
                local_modified_at=l.modified_at,
            )
        )
    return actions


def run_pull(model: str) -> tuple[int, str]:
    import subprocess

    p = subprocess.run(["ollama", "pull", model], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout
