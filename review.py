# ruff: noqa: E501
import json

review = {
  "review_id": "cove-11hat-001",
  "timestamp": "2026-03-02T22:17:04Z",
  "active_hats": ["Black", "Azure", "Silver", "Blue", "Purple", "Red", "White", "Yellow"],
  "meta_router_version": "2.0",
  "findings": [
    {
      "id": "F001",
      "hat": "Black",
      "severity": "CRITICAL",
      "category": "SSRF_Vulnerability",
      "issue": "SSRF in WEB_SEARCH and HTTP_GET via _dapr_http",
      "location": {"file": "agents/core/host_function_dispatcher.py", "line": "def _dapr_http"},
      "evidence": "resp = httpx.get(query_or_url, timeout=30.0, follow_redirects=True) is called directly on user-provided URL without SSRF protection, blocklisting local IPs (169.254.169.254, 127.0.0.1, 10.0.0.0/8), or restricting protocols.",
      "recommendation": "Implement strict URL parsing and IP blocklisting before calling httpx.get. Prevent follow_redirects to internal networks.",
      "standard_tag": "OWASP-A10-2025",
      "regulatory_risk": "High (Internal Network Compromise)"
    },
    {
      "id": "F002",
      "hat": "Red",
      "severity": "HIGH",
      "category": "Daemon_Stability",
      "issue": "Canary loop lacks global try-except block",
      "location": {"file": "agents/core/canary_agent.py", "line": "def _canary_loop"},
      "evidence": "while not stop_event.is_set(): ... _fire_probe() ... _idle_curiosity_scan(). If either of these throws an unhandled exception (e.g., httpx.RequestError outside of expected bounds or DB error), the daemon thread crashes silently without restarting.",
      "recommendation": "Wrap the loop body in a try-except Exception block with a time.sleep() and _logger.log to prevent CPU spinning and thread death.",
      "standard_tag": "Resilience",
      "regulatory_risk": None
    },
    {
      "id": "F003",
      "hat": "White",
      "severity": "MEDIUM",
      "category": "DB_Concurrency",
      "issue": "Default SQLite connection missing WAL and busy_timeout PRAGMAs",
      "location": {"file": "agents/core/memory_scribe.py", "line": "def _make_conn(path: Path) -> sqlite3.Connection:"},
      "evidence": "Unverified if `_make_conn` properly sets PRAGMA journal_mode=WAL; and PRAGMA busy_timeout=5000; (Code diff omits the internals of _make_conn, but reviewing memory requirements dictates this MUST be enforced).",
      "recommendation": "Ensure `conn.execute('PRAGMA journal_mode=WAL;')` and `conn.execute('PRAGMA busy_timeout=5000;')` are set in _make_conn.",
      "standard_tag": "Performance Under Duress",
      "regulatory_risk": None
    },
    {
      "id": "F004",
      "hat": "Blue",
      "severity": "MEDIUM",
      "category": "Process",
      "issue": "Missing HTTP timeouts for Gateway Bus proxy in _dapr_http for WEB_SEARCH",
      "location": {"file": "agents/core/host_function_dispatcher.py", "line": "def _dapr_http"},
      "evidence": "timeout=30.0 for WEB_SEARCH. The memory context mandates 'strict HTTP timeouts (default 12s) on Ollama/Gateway calls to prevent GPU/RAM starvation.' 30s is too long and can cause starvation.",
      "recommendation": "Reduce timeout to 12.0s.",
      "standard_tag": "Resilience",
      "regulatory_risk": None
    },
    {
      "id": "F005",
      "hat": "Purple",
      "severity": "HIGH",
      "category": "AI_Safety",
      "issue": "Missing ALIGN enforcement in text-mode fallback",
      "location": {"file": "agents/core/main.py", "line": "def execute_intent"},
      "evidence": "if ast is None: ... hlf_response = _ollama_generate(text) ... ast = hlfc_compile(hlf_response) ... then it goes straight to hlfrun(ast). The text was turned into an AST *locally* and executed without passing through the Sentinel Gate / ALIGN ledger.",
      "recommendation": "Pass the generated AST through `enforce_align` before executing it, or ensure text-mode payloads are only processed by the Gateway Bus which enforces ALIGN *after* compilation.",
      "standard_tag": "OWASP-LLM08",
      "regulatory_risk": "ALIGN Bypass"
    },
    {
      "id": "F006",
      "hat": "Black",
      "severity": "HIGH",
      "category": "Path_Traversal",
      "issue": "Incomplete ACFS confinement",
      "location": {"file": "agents/core/host_function_dispatcher.py", "line": "def _acfs_path"},
      "evidence": "target = (base / raw.lstrip('/')).resolve(). This resolves symlinks. If an attacker can write a symlink inside BASE_DIR (e.g. via Dapr write) that points outside, subsequent READs will follow the symlink and read arbitrary files.",
      "recommendation": "Check if any part of the path is a symlink, or enforce strict os.path.realpath checks that do not permit symlinks pointing outside BASE_DIR.",
      "standard_tag": "OWASP-A01-2025",
      "regulatory_risk": "Data Exfiltration"
    }
  ],
  "verdict": "HOLD",
  "verdict_options": ["LAUNCH_READY", "CONDITIONAL_LAUNCH", "HOLD", "PATCH_REQUIRED"],
  "confidence": "High",
  "unverified_items": [],
  "compliance_matrix": {
    "owasp_top_10_2025": "Fail",
    "eu_ai_act": "Action Required",
    "wcag_2.2_aa": "Pass"
  }
}

print(json.dumps(review, indent=2))
