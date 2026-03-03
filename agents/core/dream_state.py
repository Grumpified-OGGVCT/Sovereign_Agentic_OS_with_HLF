"""
Dream State — Full HLF workflow + Hat analysis pipeline.

Can be triggered:
  1. Scheduled via cron at 03:00 (``python -m agents.core.dream_state``)
  2. Manually from the GUI via ``run_dream_cycle(manual=True)``

Pipeline stages:
  1. Context compression — map-reduce rolling context via FractalSummarizer
  2. Trace archival — move old traces to cold storage
  3. HLF Practice Round — generate sample HLF intents, validate through parser
  4. Hat Analysis — run Eleven Thinking Hats on current system state
  5. Results persistence — store cycle results and hat findings in SQLite
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agents.core.logger import ALSLogger as _ALSLogger

logger = logging.getLogger(__name__)

_DB_PATH = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "sqlite" / "memory.db"
_COLD_ARCHIVE = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "cold_archive"


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


@dataclass
class DreamCycleReport:
    """Structured output from a complete dream cycle."""

    cycle_type: str = "scheduled"  # 'scheduled' | 'manual'
    start_time: float = 0.0
    duration_seconds: float = 0.0
    # Context compression
    context_compressed_chars: int = 0
    context_result_chars: int = 0
    # HLF practice
    hlf_practiced: int = 0
    hlf_passed: int = 0
    hlf_details: list[dict] = field(default_factory=list)
    # Hat analysis
    hat_reports: list[dict] = field(default_factory=list)
    hat_findings_count: int = 0
    # Summary
    summary: str = ""
    error: str | None = None


# ---------------------------------------------------------------------------
# Stage 1: Context Compression
# ---------------------------------------------------------------------------


def compress_rolling_context(conn: sqlite3.Connection) -> dict:
    """Map-reduce summarize day's rolling context. Returns stats dict."""
    from agents.core.fractal_summarization import FractalSummarizer

    cutoff = time.time() - 86400  # 24 hours
    rows = conn.execute("SELECT session_id, fifo_blob FROM rolling_context WHERE timestamp > ?", (cutoff,)).fetchall()
    if not rows:
        return {"compressed": 0, "result": 0, "ratio": 0.0}

    combined = " ".join(r[1] for r in rows)
    original_len = len(combined)

    # Use real map-reduce summarization
    try:
        summary = FractalSummarizer.summarize_context(combined, target_tokens=1500)
    except Exception as e:
        logger.warning(f"Fractal summarization failed, using truncation: {e}")
        summary = combined[:6000]  # fallback: keep first 6000 chars

    # Fast token count estimation (approx. 4 chars per token)
    token_count = len(summary) // 4

    conn.execute(
        "INSERT INTO rolling_context (session_id, timestamp, fifo_blob, token_count) VALUES (?, ?, ?, ?)",
        ("dream_summary", time.time(), summary, token_count),
    )
    conn.commit()

    result_len = len(summary)
    ratio = round(1 - (result_len / original_len), 2) if original_len > 0 else 0.0

    return {"compressed": original_len, "result": result_len, "ratio": ratio}


# ---------------------------------------------------------------------------
# Stage 2: Trace Archival
# ---------------------------------------------------------------------------


def archive_old_traces(conn: sqlite3.Connection) -> None:
    """
    Move traces older than 7 days to cold_archive as highly-compressed Parquet files,
    then delete the raw rows to prevent disk exhaustion (Phase 4.3 Log Storage Truncator).
    Falls back to JSON if pyarrow is unavailable.
    """
    cold_archive = Path(os.environ.get("BASE_DIR", "/app")) / "data" / "cold_archive"
    cutoff = time.time() - 7 * 86400
    rows = conn.execute(
        "SELECT rowid, session_id, timestamp, fifo_blob FROM rolling_context WHERE timestamp < ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        return
    cold_archive.mkdir(parents=True, exist_ok=True)
    ts_tag = int(time.time())

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table(
            {
                "session_id": [r[1] for r in rows],
                "timestamp": [r[2] for r in rows],
                "fifo_blob": [r[3] for r in rows],
            }
        )
        archive_file = cold_archive / f"archive_{ts_tag}.parquet"
        pq.write_table(table, str(archive_file), compression="snappy")
    except Exception:
        # Fallback: JSON archive (handles ImportError, codec errors, filesystem errors)
        _ALSLogger(agent_role="dream-state", goal_id="archive").log(
            "PARQUET_FALLBACK", {"ts": ts_tag}, anomaly_score=0.2
        )
        archive_file = cold_archive / f"archive_{ts_tag}.json"
        archive_file.write_text(json.dumps([{"session_id": r[1], "timestamp": r[2], "blob": r[3]} for r in rows]))

    rowids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(rowids))
    conn.execute(f"DELETE FROM rolling_context WHERE rowid IN ({placeholders})", rowids)
    conn.commit()


# ---------------------------------------------------------------------------
# Stage 3: HLF Practice Round
# ---------------------------------------------------------------------------

_HLF_PRACTICE_PROMPTS = [
    "Generate an HLF intent to read the file /security/seccomp.json and verify its checksum.",
    "Generate an HLF intent to run a health check on the gateway-node service.",
    "Generate an HLF intent to compress old log files in /data/logs/ directory.",
    "Generate an HLF intent to check Redis connectivity and report status.",
    "Generate an HLF intent to scan ALIGN rules and count active blocks.",
]


def _practice_hlf(conn: sqlite3.Connection | None = None, count: int = 5) -> dict:
    """
    Generate HLF intents via Ollama and validate them through the parser.
    Returns practice stats.
    """
    import urllib.error
    import urllib.request

    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if ollama_host and not ollama_host.startswith("http"):
        ollama_host = f"http://{ollama_host}"
    if "0.0.0.0" in ollama_host:
        ollama_host = ollama_host.replace("0.0.0.0", "localhost")

    # Load system prompt for HLF generation
    base_dir = Path(os.environ.get("BASE_DIR", "."))
    system_prompt_path = base_dir / "governance" / "templates" / "system_prompt.txt"
    if system_prompt_path.exists():
        system_prompt = system_prompt_path.read_text()
    else:
        system_prompt = (
            "You are an HLF compiler. Generate valid HLF programs using tags like "
            "[INTENT], [CONSTRAINT], [EXPECT], [SET], [FUNCTION], [RESULT], and the Ω terminator."
        )

    # Read analysis model from settings
    settings_path = base_dir / "config" / "settings.json"
    model = "kimi-k2.5:cloud"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            model = settings.get("dream_mode", {}).get("analysis_model", model)
        except Exception:
            pass

    prompts = (_HLF_PRACTICE_PROMPTS * ((count // len(_HLF_PRACTICE_PROMPTS)) + 1))[:count]
    results = {"practiced": 0, "passed": 0, "details": []}

    for prompt in prompts:
        results["practiced"] += 1
        detail = {"prompt": prompt, "passed": False, "hlf": "", "error": ""}

        try:
            payload = json.dumps(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                }
            ).encode()

            req = urllib.request.Request(
                f"{ollama_host}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
                hlf_response = data.get("message", {}).get("content", "")

            detail["hlf"] = hlf_response[:500]  # Cap for storage

            # Validate: check for required HLF markers
            has_tag = any(
                f"[{tag}]" in hlf_response
                for tag in ["INTENT", "CONSTRAINT", "EXPECT", "SET", "ACTION", "FUNCTION", "RESULT"]
            )
            has_terminator = "Ω" in hlf_response or "END" in hlf_response

            if has_tag and has_terminator:
                detail["passed"] = True
                results["passed"] += 1
            else:
                detail["error"] = "Missing required HLF tags or Ω terminator"

        except Exception as e:
            detail["error"] = str(e)[:200]
            logger.warning(f"HLF practice failed: {e}")

        results["details"].append(detail)

    return results


# ---------------------------------------------------------------------------
# Stage 4: Hat Analysis (delegates to hat_engine)
# ---------------------------------------------------------------------------


def _run_hat_analysis(conn: sqlite3.Connection | None = None) -> dict:
    """Run all hats and return summary."""
    try:
        from agents.core.hat_engine import run_all_hats

        # Read which hats are enabled from settings
        base_dir = Path(os.environ.get("BASE_DIR", "."))
        settings_path = base_dir / "config" / "settings.json"
        hats = None
        model = None
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
                dm = settings.get("dream_mode", {})
                hats = dm.get("hats_enabled")
                model = dm.get("analysis_model")
            except Exception:
                pass

        reports = run_all_hats(conn=conn, hats=hats, model=model)
        total_findings = sum(len(r.findings) for r in reports)

        return {
            "reports": [
                {
                    "hat": r.hat,
                    "emoji": r.emoji,
                    "focus": r.focus,
                    "findings_count": len(r.findings),
                    "error": r.error,
                }
                for r in reports
            ],
            "total_findings": total_findings,
            "_raw_reports": reports,  # Keep for persistence
        }
    except Exception as e:
        logger.error(f"Hat analysis failed: {e}")
        return {"reports": [], "total_findings": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Stage 5: Persistence
# ---------------------------------------------------------------------------


def _ensure_dream_tables(conn: sqlite3.Connection) -> None:
    """Create dream tables if they don't exist (for standalone runs)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dream_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            cycle_type TEXT NOT NULL DEFAULT 'scheduled',
            hlf_practiced INTEGER DEFAULT 0,
            hlf_passed INTEGER DEFAULT 0,
            context_compressed_chars INTEGER DEFAULT 0,
            context_result_chars INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            summary TEXT
        );
        CREATE TABLE IF NOT EXISTS hat_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dream_cycle_id INTEGER REFERENCES dream_results(id),
            hat TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            recommendation TEXT,
            resolved INTEGER DEFAULT 0,
            timestamp REAL NOT NULL
        );
    """)
    conn.commit()


def _persist_report(conn: sqlite3.Connection, report: DreamCycleReport) -> int:
    """Save dream cycle report to database. Returns the cycle ID."""
    _ensure_dream_tables(conn)

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO dream_results "
        "(timestamp, cycle_type, hlf_practiced, hlf_passed, "
        "context_compressed_chars, context_result_chars, duration_seconds, summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            report.start_time,
            report.cycle_type,
            report.hlf_practiced,
            report.hlf_passed,
            report.context_compressed_chars,
            report.context_result_chars,
            report.duration_seconds,
            report.summary,
        ),
    )
    cycle_id = cur.lastrowid
    conn.commit()
    return cycle_id


# ---------------------------------------------------------------------------
# Main Dream Cycle
# ---------------------------------------------------------------------------


def run_dream_cycle(manual: bool = False) -> DreamCycleReport:
    """
    Execute the full Dream State cycle.

    Args:
        manual: True if triggered by user from GUI, False if scheduled.

    Returns:
        DreamCycleReport with all results.
    """
    report = DreamCycleReport(
        cycle_type="manual" if manual else "scheduled",
        start_time=time.time(),
    )

    # Open DB connection
    conn: sqlite3.Connection | None = None
    if _DB_PATH.exists():
        conn = sqlite3.connect(str(_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        # Create the DB if it doesn't exist
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")

    _ensure_dream_tables(conn)

    try:
        logger.info(f"🌙 Dream Cycle starting ({report.cycle_type})...")

        # Stage 1: Context Compression
        logger.info("  Stage 1: Compressing rolling context...")
        try:
            compression = compress_rolling_context(conn)
            report.context_compressed_chars = compression["compressed"]
            report.context_result_chars = compression["result"]
        except Exception as e:
            logger.warning(f"  Context compression failed: {e}")

        # Stage 2: Trace Archival
        logger.info("  Stage 2: Archiving old traces...")
        try:
            archive_old_traces(conn)
        except Exception as e:
            logger.warning(f"  Trace archival failed: {e}")

        # Stage 3: HLF Practice Round
        logger.info("  Stage 3: Practicing HLF generation...")
        try:
            # Read practice count from settings
            base_dir = Path(os.environ.get("BASE_DIR", "."))
            settings_path = base_dir / "config" / "settings.json"
            practice_count = 5
            if settings_path.exists():
                try:
                    settings = json.loads(settings_path.read_text())
                    practice_count = settings.get("dream_mode", {}).get("hlf_practice_count", 5)
                except Exception:
                    pass

            hlf_results = _practice_hlf(conn, count=practice_count)
            report.hlf_practiced = hlf_results["practiced"]
            report.hlf_passed = hlf_results["passed"]
            report.hlf_details = hlf_results["details"]
        except Exception as e:
            logger.warning(f"  HLF practice failed: {e}")

        # Stage 4: Hat Analysis
        logger.info("  Stage 4: Running Hat analysis...")
        try:
            hat_results = _run_hat_analysis(conn)
            report.hat_reports = hat_results.get("reports", [])
            report.hat_findings_count = hat_results.get("total_findings", 0)
        except Exception as e:
            logger.warning(f"  Hat analysis failed: {e}")

        # Finalize
        report.duration_seconds = round(time.time() - report.start_time, 2)
        report.summary = (
            f"Dream cycle completed in {report.duration_seconds}s. "
            f"HLF: {report.hlf_passed}/{report.hlf_practiced} passed. "
            f"Hats: {report.hat_findings_count} findings. "
            f"Compression: {report.context_compressed_chars} → {report.context_result_chars} chars."
        )

        # Stage 5: Persist results
        logger.info("  Stage 5: Persisting results...")
        cycle_id = _persist_report(conn, report)

        # Persist hat findings
        if hat_results.get("_raw_reports"):
            try:
                from agents.core.hat_engine import persist_findings

                persist_findings(conn, cycle_id, hat_results["_raw_reports"])
            except Exception as e:
                logger.warning(f"  Hat findings persistence failed: {e}")

        logger.info(f"🌙 Dream Cycle complete: {report.summary}")

    except Exception as e:
        report.error = str(e)
        report.duration_seconds = round(time.time() - report.start_time, 2)
        report.summary = f"Dream cycle failed: {e}"
        logger.error(f"Dream cycle error: {e}")

    finally:
        if conn:
            conn.close()

    return report


def get_last_dream_result(conn: sqlite3.Connection) -> dict | None:
    """Fetch the most recent dream cycle result for GUI display."""
    try:
        row = conn.execute(
            "SELECT id, timestamp, cycle_type, hlf_practiced, hlf_passed, "
            "context_compressed_chars, context_result_chars, duration_seconds, summary "
            "FROM dream_results ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                "id": row[0],
                "timestamp": row[1],
                "cycle_type": row[2],
                "hlf_practiced": row[3],
                "hlf_passed": row[4],
                "context_compressed_chars": row[5],
                "context_result_chars": row[6],
                "duration_seconds": row[7],
                "summary": row[8],
            }
    except Exception:
        pass
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    report = run_dream_cycle()
    print(json.dumps(asdict(report), indent=2, default=str))
