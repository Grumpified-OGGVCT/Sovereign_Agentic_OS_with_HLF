from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from ..config import OPENROUTER_RANKINGS, REQUEST_TIMEOUT, RETRIES, RETRY_BACKOFF


def fetch_text(url: str) -> str:
    last_err = None
    for i in range(RETRIES):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            time.sleep(RETRY_BACKOFF**i)
    raise RuntimeError(f"GET failed {url}: {last_err}")


def normalize_model_id(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def fetch_rankings() -> dict[str, dict[str, str]]:
    html = fetch_text(OPENROUTER_RANKINGS)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    pat = re.compile(
        r"(?P<rank>\d+)\.\s+(?P<name>.+?)\s+by\s+.+?\s+(?P<tokens>[0-9.]+[TMB])\s+tokens\s+(?P<delta>[0-9.]+%)",
        re.IGNORECASE | re.DOTALL,
    )

    out: dict[str, dict[str, str]] = {}
    for m in pat.finditer(text):
        display = " ".join(m.group("name").split())
        norm = normalize_model_id(display)
        out[norm] = {
            "rank": int(m.group("rank")),
            "display_name": display,
            "tokens": m.group("tokens"),
            "delta_or_share": m.group("delta"),
        }
    return out


def find_rank_for_root(rankings: dict[str, dict[str, str]], root: str) -> dict[str, str] | None:
    rr = root.replace(".", "").replace("-", "")
    for k, v in rankings.items():
        kk = k.replace(".", "").replace("-", "")
        if rr in kk or kk in rr:
            return v
    return None
