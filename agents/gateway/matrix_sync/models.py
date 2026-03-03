from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ModelTag:
    name: str
    model: str
    modified_at: str
    size: int | None
    digest: str
    details: dict[str, Any]


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
    cap_tags: list[str]
    specialties: list[str]
    benchmark_mentions: list[str]
    benchmark_structured: list[dict[str, Any]]
    context_mentions: list[str]
    raw_text: str
    fetched_at_utc: str
