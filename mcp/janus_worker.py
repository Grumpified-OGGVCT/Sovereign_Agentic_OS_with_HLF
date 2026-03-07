"""
Janus Worker — Isolated subprocess for heavy ML operations.

Accepts JSON commands on stdin, returns JSON on stdout.
Loads SentenceTransformer and ChromaDB lazily on first use.
Self-terminates after 5 minutes of inactivity.

Commands:
  {"cmd": "search_archives", "query": "..."}
  {"cmd": "view_thread_history", "url": "..."}
  {"cmd": "deep_recall", "query": "...", "n_results": 10, "scope": null}
  {"cmd": "vault_similar", "text_or_url": "...", "n_results": 5}
  {"cmd": "vault_stats"}
  {"cmd": "web_search", "query": "...", "max_results": 10}
  {"cmd": "advanced_search", "query": "...", "max_results": 10,
   "region": "wt-wt", "time_range": null, "site": null}
  {"cmd": "extract_page", "url": "...", "max_chars": 8000}
  {"cmd": "ingest_url", "url": "...", "depth": 1, "search_after": null}
  {"cmd": "summarize_text", "text": "...", "max_sentences": 5}
  {"cmd": "shutdown"}

Protocol:
  - One JSON object per line (newline-delimited JSON)
  - Response includes {"status": "ok", "result": ...} or {"status": "error", "error": "..."}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import select  # noqa: F401 — needed for non-blocking read timeout (Copilot #16)
import sqlite3
import sys
import time
from pathlib import Path

_BASE_DIR = Path(os.environ.get("BASE_DIR", str(Path(__file__).resolve().parent.parent)))
_IDLE_TIMEOUT = 300  # 5 minutes

# Janus data paths
_JANUS_DIR = _BASE_DIR.parent / "project_janus"
_DB_PATH = _JANUS_DIR / "data" / "vault.db"
_CHROMA_PATH = _JANUS_DIR / "data" / "chroma_db"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Lazy-loaded ML state
_embedder = None
_chroma_client = None
_collection = None

# Optional deps
_httpx = None
_bs4 = None
_ddgs = None


def _init_ml():
    """Lazy-load SentenceTransformer and ChromaDB."""
    global _embedder, _chroma_client, _collection
    if _embedder is not None:
        return

    import chromadb
    from sentence_transformers import SentenceTransformer

    _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    _chroma_client = chromadb.PersistentClient(path=str(_CHROMA_PATH))
    _collection = _chroma_client.get_or_create_collection(name="stolen_history")


def _init_web():
    """Lazy-load httpx and BeautifulSoup."""
    global _httpx, _bs4, _ddgs
    if _httpx is not None:
        return

    import httpx
    from bs4 import BeautifulSoup

    _httpx = httpx
    _bs4 = BeautifulSoup

    try:
        from duckduckgo_search import DDGS
        _ddgs = DDGS
    except ImportError:
        _ddgs = None


def _get_janus_db():
    if not _DB_PATH.exists():
        return None
    return sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def handle_search_archives(query: str) -> dict:
    """Semantic search over archival data."""
    _init_ml()
    if _collection is None:
        return {"status": "error", "error": "ML models not initialized"}

    vector = _embedder.encode(query).tolist()
    results = _collection.query(query_embeddings=[vector], n_results=5)

    output = "--- RAW ARCHIVAL DATA ---\n"
    for doc, meta in zip(results["documents"][0], results["metadatas"][0], strict=False):
        output += f"[Author: {meta.get('author', 'N/A')} | Source: {meta.get('source', 'N/A')}]\n"
        output += f"{doc}\n-------------------------\n"

    return {"status": "ok", "result": [{"result": output}]}


def handle_view_thread_history(url: str) -> dict:
    """Reconstruct thread history from snapshots."""
    conn = _get_janus_db()
    if conn is None:
        return {"status": "ok", "result": [{"result": "Vault database not found."}]}

    try:
        rows = conn.execute(
            """
            SELECT p.author, p.content_clean, p.source_type, p.snapshot_date,
                   p.is_deleted
            FROM posts p
            JOIN threads t ON p.thread_id = t.id
            WHERE t.url = ?
            ORDER BY p.post_external_id, p.snapshot_date
            """,
            (url,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"status": "ok", "result": [{"result": f"No history found for thread: {url}"}]}

    output = f"--- THREAD RECONSTRUCTION: {url} ---\n"
    for author, content, source, date, is_deleted in rows:
        deleted_marker = " [DELETED]" if is_deleted else ""
        output += f"[{source.upper()} | {date}]{deleted_marker} {author}: {content}\n"

    return {"status": "ok", "result": [{"result": output}]}


def handle_deep_recall(query: str, n_results: int = 10, scope: str | None = None) -> dict:
    """Deep semantic retrieval — Infinite RAG pattern."""
    _init_ml()
    if _collection is None:
        return {"status": "error", "error": "ML models not initialized"}

    n_results = min(n_results, 100)
    vector = _embedder.encode(query).tolist()

    where_filter = None
    if scope:
        where_filter = {"source": {"$contains": scope}}

    results = _collection.query(
        query_embeddings=[vector], n_results=n_results, where=where_filter,
    )

    if not results["documents"][0]:
        return {"status": "ok", "result": [{"result": f"No deep recall results for: '{query}'"}]}

    output = f"--- DEEP RECALL: '{query}' ({len(results['documents'][0])} results) ---\n"
    for i, (doc, meta) in enumerate(
        zip(results["documents"][0], results["metadatas"][0], strict=False), 1
    ):
        output += (
            f"[{i}] Author: {meta.get('author', 'N/A')} "
            f"| Source: {meta.get('source', 'N/A')}\n"
            f"{doc}\n{'─' * 60}\n"
        )
    return {"status": "ok", "result": [{"result": output}]}


def handle_vault_similar(text_or_url: str, n_results: int = 5) -> dict:
    """Find content semantically similar to given text or a URL's content."""
    _init_ml()
    if _collection is None:
        return {"status": "error", "error": "ML models not initialized"}

    n_results = min(n_results, 30)
    text = text_or_url

    if text_or_url.startswith(("http://", "https://")):
        _init_web()
        try:
            resp = _httpx.get(text_or_url, timeout=15, follow_redirects=True,
                              headers={"User-Agent": USER_AGENT})
            soup = _bs4(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)[:3000]
        except Exception as exc:
            return {"status": "error", "error": f"Could not fetch URL: {exc}"}

    vector = _embedder.encode(text).tolist()
    results = _collection.query(query_embeddings=[vector], n_results=n_results)

    if not results["documents"][0]:
        return {"status": "ok", "result": [{"result": "No similar content found."}]}

    output = f"--- SIMILAR ({len(results['documents'][0])} results) ---\n"
    for i, (doc, meta) in enumerate(
        zip(results["documents"][0], results["metadatas"][0], strict=False), 1
    ):
        output += (
            f"[{i}] Author: {meta.get('author', 'N/A')} "
            f"| Source: {meta.get('source', 'N/A')}\n"
            f"{doc[:500]}...\n{'─' * 60}\n"
        )
    return {"status": "ok", "result": [{"result": output}]}


def handle_vault_stats() -> dict:
    """Stats: threads, posts, embeddings, date ranges."""
    output = "--- VAULT STATISTICS ---\n"

    conn = _get_janus_db()
    if conn:
        try:
            cur = conn.cursor()
            threads = cur.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            posts = cur.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            sources = cur.execute(
                "SELECT source_type, COUNT(*) FROM posts GROUP BY source_type"
            ).fetchall()
            dates = cur.execute(
                "SELECT MIN(snapshot_date), MAX(snapshot_date) FROM posts"
            ).fetchone()
            deleted = cur.execute(
                "SELECT COUNT(*) FROM posts WHERE is_deleted = 1"
            ).fetchone()[0]

            output += f"Threads:    {threads}\n"
            output += f"Posts:      {posts} ({deleted} flagged deleted)\n"
            for src_type, count in sources:
                output += f"  {src_type}: {count}\n"
            if dates[0]:
                output += f"Date range: {dates[0]} → {dates[1]}\n"
        finally:
            conn.close()
    else:
        output += "SQLite vault: not found\n"

    _init_ml()
    if _collection:
        count = _collection.count()
        output += f"Embeddings: {count} vectors\n"
    else:
        output += "Embeddings: not available\n"

    return {"status": "ok", "result": [{"result": output}]}


def handle_web_search(query: str, max_results: int = 10) -> dict:
    """Web search via DuckDuckGo."""
    _init_web()
    max_results = min(max_results, 25)

    if _ddgs:
        try:
            with _ddgs() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return {"status": "ok", "result": [{"result": f"No results for: '{query}'"}]}

            output = f"--- WEB SEARCH: '{query}' ({len(results)} results) ---\n"
            for i, r in enumerate(results, 1):
                output += (
                    f"[{i}] {r.get('title', 'No title')}\n"
                    f"    URL: {r.get('href', r.get('link', 'N/A'))}\n"
                    f"    {r.get('body', r.get('snippet', ''))}\n\n"
                )
            return {"status": "ok", "result": [{"result": output}]}
        except Exception as exc:
            return {"status": "error", "error": f"Search error: {exc}"}

    return {"status": "error", "error": "duckduckgo_search not installed"}


def handle_advanced_search(
    query: str, max_results: int = 10,
    region: str = "wt-wt", time_range: str | None = None, site: str | None = None,
) -> dict:
    """Advanced web search with filters."""
    _init_web()
    max_results = min(max_results, 25)
    search_query = f"site:{site} {query}" if site else query

    if not _ddgs:
        return {"status": "error", "error": "duckduckgo_search not installed"}

    try:
        with _ddgs() as ddgs:
            results = list(ddgs.text(
                search_query, max_results=max_results,
                region=region, timelimit=time_range,
            ))
        if not results:
            return {"status": "ok", "result": [{"result": f"No results for: '{search_query}'"}]}

        output = f"--- ADVANCED SEARCH: '{query}' ---\n"
        if site:
            output += f"Site: {site}\n"
        if time_range:
            output += f"Time: {time_range}\n"
        output += f"Region: {region} | {len(results)} results\n\n"

        for i, r in enumerate(results, 1):
            output += (
                f"[{i}] {r.get('title', 'No title')}\n"
                f"    URL: {r.get('href', r.get('link', 'N/A'))}\n"
                f"    {r.get('body', r.get('snippet', ''))}\n\n"
            )
        return {"status": "ok", "result": [{"result": output}]}
    except Exception as exc:
        return {"status": "error", "error": f"Advanced search error: {exc}"}


def handle_extract_page(url: str, max_chars: int = 8000) -> dict:
    """Extract clean readable text from any URL."""
    _init_web()
    try:
        resp = _httpx.get(url, timeout=20, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except Exception as exc:
        return {"status": "error", "error": f"Failed to fetch: {exc}"}

    soup = _bs4(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside",
                      "form", "iframe", "noscript", "svg", "button"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.find("body")
    if main is None:
        return {"status": "ok", "result": [{"result": "Could not extract content."}]}

    text = main.get_text(separator="\n", strip=True)
    title = soup.find("title")
    title_text = title.get_text(strip=True) if title else "Unknown"

    output = f"--- PAGE: {title_text} ---\nURL: {url}\n{'─' * 60}\n"
    output += text[:max_chars]
    if len(text) > max_chars:
        output += f"\n...[truncated at {max_chars} of {len(text)} chars]"

    return {"status": "ok", "result": [{"result": output}]}


def handle_ingest_url(url: str, depth: int = 1, search_after: str | None = None) -> dict:
    """Crawl a URL, ingest into vault, optionally search immediately."""
    import datetime

    _init_ml()
    _init_web()
    if _collection is None:
        return {"status": "error", "error": "ML models not initialized"}

    try:
        resp = _httpx.get(url, timeout=20, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except Exception as exc:
        return {"status": "error", "error": f"Failed to crawl: {exc}"}

    soup = _bs4(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    title = soup.find("title")
    title_text = title.get_text(strip=True) if title else url
    text = soup.get_text(separator="\n", strip=True)

    if not text:
        return {"status": "ok", "result": [{"result": f"No text content at {url}"}]}

    # Chunk and ingest
    all_pages = [(url, text)]
    total_chunks = 0

    for page_url, page_text in all_pages:
        chunk_size = 1000
        chunks = [page_text[i: i + chunk_size] for i in range(0, len(page_text), chunk_size)]
        ids = []
        documents = []
        metadatas = []
        for i, chunk in enumerate(chunks):
            doc_id = f"ingest_{hashlib.md5(page_url.encode()).hexdigest()[:12]}_{i}"
            ids.append(doc_id)
            documents.append(chunk)
            metadatas.append({
                "source": page_url, "author": "janus_ingest",
                "title": title_text, "chunk_index": i,
                "total_chunks": len(chunks),
                "ingested_at": datetime.datetime.now(datetime.UTC).isoformat(),
            })

        embeddings = [_embedder.encode(doc).tolist() for doc in documents]
        _collection.upsert(ids=ids, documents=documents,
                           metadatas=metadatas, embeddings=embeddings)
        total_chunks += len(chunks)

    output = f"--- INGESTED: {title_text} ---\n"
    output += f"URL: {url}\nChunks: {total_chunks} | Chars: {len(text)}\n"

    if search_after:
        vector = _embedder.encode(search_after).tolist()
        results = _collection.query(query_embeddings=[vector], n_results=5)
        output += f"\n--- SEARCH: '{search_after}' ---\n"
        for i, (doc, meta) in enumerate(
            zip(results["documents"][0], results["metadatas"][0], strict=False), 1
        ):
            output += f"[{i}] {meta.get('source', 'N/A')}: {doc[:400]}...\n"

    return {"status": "ok", "result": [{"result": output}]}


def handle_summarize_text(text: str, max_sentences: int = 5) -> dict:
    """Extractive summarization — no LLM needed."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    if len(sentences) <= max_sentences:
        return {"status": "ok", "result": [{"result": text}]}

    words = re.findall(r"\w+", text.lower())
    freq: dict[str, int] = {}
    for w in words:
        if len(w) > 3:
            freq[w] = freq.get(w, 0) + 1

    scored = []
    for i, s in enumerate(sentences):
        score = sum(freq.get(w, 0) for w in re.findall(r"\w+", s.lower()))
        score *= 1.0 + (0.5 / (i + 1))
        scored.append((score, i, s))

    top = sorted(scored, key=lambda x: x[0], reverse=True)[:max_sentences]
    top = sorted(top, key=lambda x: x[1])

    output = "--- SUMMARY ---\n"
    output += " ".join(s for _, _, s in top)
    output += f"\n\n[Extracted {max_sentences} of {len(sentences)} sentences]"
    return {"status": "ok", "result": [{"result": output}]}


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "search_archives": lambda cmd: handle_search_archives(cmd.get("query", "")),
    "view_thread_history": lambda cmd: handle_view_thread_history(cmd.get("url", "")),
    "deep_recall": lambda cmd: handle_deep_recall(
        cmd.get("query", ""), cmd.get("n_results", 10), cmd.get("scope")),
    "vault_similar": lambda cmd: handle_vault_similar(
        cmd.get("text_or_url", ""), cmd.get("n_results", 5)),
    "vault_stats": lambda cmd: handle_vault_stats(),
    "web_search": lambda cmd: handle_web_search(
        cmd.get("query", ""), cmd.get("max_results", 10)),
    "advanced_search": lambda cmd: handle_advanced_search(
        cmd.get("query", ""), cmd.get("max_results", 10),
        cmd.get("region", "wt-wt"), cmd.get("time_range"), cmd.get("site")),
    "extract_page": lambda cmd: handle_extract_page(
        cmd.get("url", ""), cmd.get("max_chars", 8000)),
    "ingest_url": lambda cmd: handle_ingest_url(
        cmd.get("url", ""), cmd.get("depth", 1), cmd.get("search_after")),
    "summarize_text": lambda cmd: handle_summarize_text(
        cmd.get("text", ""), cmd.get("max_sentences", 5)),
}


def main():
    """Main loop: read JSON commands from stdin, write responses to stdout."""
    last_activity = time.time()

    while True:
        if time.time() - last_activity > _IDLE_TIMEOUT:
            sys.stderr.write("janus_worker: idle timeout, shutting down\n")
            break

        try:
            line = sys.stdin.readline()
        except EOFError:
            break

        if not line:
            break

        line = line.strip()
        if not line:
            continue

        last_activity = time.time()

        try:
            cmd = json.loads(line)
        except json.JSONDecodeError as e:
            response = {"status": "error", "error": f"Invalid JSON: {e}"}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        cmd_name = cmd.get("cmd", "")

        if cmd_name == "shutdown":
            response = {"status": "ok", "result": "shutting down"}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            break

        handler = COMMANDS.get(cmd_name)
        if handler:
            try:
                response = handler(cmd)
            except Exception as e:
                response = {"status": "error", "error": str(e)}
        else:
            response = {"status": "error", "error": f"Unknown command: {cmd_name}"}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

