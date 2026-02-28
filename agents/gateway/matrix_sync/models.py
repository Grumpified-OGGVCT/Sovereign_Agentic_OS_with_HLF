from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

@dataclass
class ModelTag:
    name: str
    model: str
    modified_at: str
    size: Optional[int]
    digest: str
    details: Dict[str, Any]

@dataclass
class SyncAction:
    normalized_id: str
    cloud_name: str
    local_name: str
    action: str
    reason: str
    pull_target: str
    cloud_digest: str
    local_digest: str
    cloud_modified_at: str
    local_modified_at: str

@dataclass
class CardInfo:
    slug: str
    title: str
    summary: str
    cap_tags: List[str]
    specialties: List[str]
    benchmark_mentions: List[str]
    benchmark_structured: List[Dict[str, Any]]
    context_mentions: List[str]
    raw_text: str
    fetched_at_utc: str
