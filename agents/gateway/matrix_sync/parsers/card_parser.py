from __future__ import annotations
import datetime as dt
import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup

from ..config import OLLAMA_LIBRARY_BASE, REQUEST_TIMEOUT, RETRIES, RETRY_BACKOFF, KNOWN_CAP_TAGS
from ..models import CardInfo
from .benchmark_parser import parse_benchmark_mentions

def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

def fetch_text(url: str) -> str:
    last_err = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            time.sleep(RETRY_BACKOFF ** i)
    raise RuntimeError(f"GET failed {url}: {last_err}")

def fetch_card(slug: str, cache_dir: str) -> CardInfo:
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{slug}.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return CardInfo(**json.load(f))

    url = f"{OLLAMA_LIBRARY_BASE}{slug}"
    html = fetch_text(url)
    soup = BeautifulSoup(html, "html.parser")
    raw = soup.get_text("\n", strip=True)
    lines = [x.strip() for x in raw.splitlines() if x.strip()]

    title = lines[0] if lines else slug
    summary = ""
    for ln in lines[:120]:
        ll = ln.lower()
        if len(ln) > 40 and ("model" in ll or "multimodal" in ll or "language" in ll):
            summary = ln
            break

    low = raw.lower()
    cap_tags = sorted([t for t in KNOWN_CAP_TAGS if re.search(rf"\b{re.escape(t)}\b", low)])

    specialties = []
    for ln in lines[:500]:
        ll = ln.lower()
        if any(k in ll for k in [
            "coding","agent","tool","vision","ocr","multilingual","reasoning","thinking",
            "edge","enterprise","function calling","structured output","gui","video","long context"
        ]):
            if 12 <= len(ln) <= 240:
                specialties.append(ln)
    specialties = list(dict.fromkeys(specialties))[:30]

    context_mentions = sorted(set(re.findall(r"\b\d+(?:\.\d+)?\s*(?:k|m|b)?\s*context\b", low, re.IGNORECASE)))

    bench_mentions, bench_structured = parse_benchmark_mentions(raw)

    ci = CardInfo(
        slug=slug,
        title=title[:180],
        summary=summary[:800],
        cap_tags=cap_tags,
        specialties=specialties,
        benchmark_mentions=bench_mentions,
        benchmark_structured=bench_structured,
        context_mentions=context_mentions,
        raw_text=raw[:50000],
        fetched_at_utc=now_utc_iso(),
    )

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(ci.__dict__, f, ensure_ascii=False, indent=2)

    return ci
