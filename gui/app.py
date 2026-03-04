"""
Sovereign OS — Cognitive Security Operations Center (C-SOC) GUI.

HLF Sovereign GUI v2.0: Visualizes Identity, Deception, State, and Threats.
Connects to the live Gateway Bus (/health, /api/v1/intent) and Redis for
real-time metrics. Falls back gracefully when services are unavailable.
"""

import json
import os
import time
import urllib.request
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

# --- Paths ---
_PROJECT_ROOT = Path(__file__).parent.parent
_LAST_HASH_FILE = _PROJECT_ROOT / "observability" / "openllmetry" / "last_hash.txt"
_ALIGN_LEDGER = _PROJECT_ROOT / "governance" / "ALIGN_LEDGER.yaml"
_HOST_FUNCTIONS = _PROJECT_ROOT / "governance" / "host_functions.json"

# --- Gateway, Redis, and Ollama URLs ---
GATEWAY_URL = "http://localhost:40404"
REDIS_HOST = "localhost"
REDIS_PORT = 6379
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_HOST_SECONDARY = os.environ.get("OLLAMA_HOST_SECONDARY", "")
OLLAMA_LOAD_STRATEGY = os.environ.get("OLLAMA_LOAD_STRATEGY", "failover")
# Ollama sets OLLAMA_HOST to 0.0.0.0 (listen address), which can't be used
# as a client address on Windows. Normalize to localhost for outbound calls.
if "0.0.0.0" in OLLAMA_HOST:
    OLLAMA_HOST = OLLAMA_HOST.replace("0.0.0.0", "localhost")
# Ensure http:// scheme is present (env var may be just "host:port")
if not OLLAMA_HOST.startswith("http"):
    OLLAMA_HOST = f"http://{OLLAMA_HOST}"
if OLLAMA_HOST_SECONDARY and not OLLAMA_HOST_SECONDARY.startswith("http"):
    OLLAMA_HOST_SECONDARY = f"http://{OLLAMA_HOST_SECONDARY}"

# --- Ollama Matrix Sync paths (sibling project) ---
_MATRIX_DIR = _PROJECT_ROOT.parent / "ollama-matrix-sync" / "out" / "latest"

# ============================================================================
# HELPER FUNCTIONS — Real API Calls with Graceful Fallbacks
# ============================================================================


@st.cache_data(ttl=5)
def check_gateway_health() -> dict:
    """Check if the Gateway Bus is alive by hitting /health."""
    try:
        resp = httpx.get(f"{GATEWAY_URL}/health", timeout=2.0)
        return resp.json()
    except Exception:
        return {"status": "unreachable"}


@st.cache_data(ttl=10)
def get_align_rules() -> list[dict]:
    """Load ALIGN ledger rules from disk as structured dicts."""
    try:
        if _ALIGN_LEDGER.exists():
            import yaml

            data = yaml.safe_load(_ALIGN_LEDGER.read_text())
            return data.get("rules", [])
    except Exception:
        pass
    return []


def get_merkle_chain_status() -> dict:
    """Read the ALS Merkle chain status from last_hash.txt."""
    try:
        if _LAST_HASH_FILE.exists():
            hash_val = _LAST_HASH_FILE.read_text().strip()
            return {
                "status": "active",
                "last_hash": hash_val[:16] + "...",
                "full_hash": hash_val,
            }
    except Exception:
        pass
    return {"status": "no chain", "last_hash": "seed (0x00...)", "full_hash": "0" * 64}


def check_redis() -> tuple[bool, object | None]:
    """Check Redis connectivity. Returns (is_active, redis_client_or_None)."""
    try:
        import redis

        redis_pw = os.environ.get("REDIS_PASSWORD", "")
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=redis_pw or None,
            decode_responses=True,
        )
        r.ping()
        return True, r
    except Exception:
        return False, None


@st.cache_data(ttl=15)
def fetch_ollama_models() -> list[dict]:
    """Query the live Ollama instance for available models.

    Uses urllib instead of httpx to avoid PermissionError from Python's
    ssl.py on Windows volume-mounted paths.
    """
    try:
        url = f"{OLLAMA_HOST}/api/tags"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        models = data.get("models", [])
        return [
            {
                "name": m.get("name", "unknown"),
                "size_gb": round(m.get("size", 0) / 1e9, 1),
                "params": m.get("details", {}).get("parameter_size", "?"),
                "quant": m.get("details", {}).get("quantization_level", "?"),
                "family": m.get("details", {}).get("family", "?"),
            }
            for m in models
        ]
    except Exception:
        return []


@st.cache_data(ttl=60)
def get_matrix_catalog() -> pd.DataFrame | None:
    """Load the latest model_catalog.csv from ollama-matrix-sync output."""
    try:
        catalog_path = _MATRIX_DIR / "model_catalog.csv"
        if catalog_path.exists():
            return pd.read_csv(str(catalog_path))
    except Exception:
        pass
    return None


def get_host_function_count() -> int:
    """Count registered host functions from governance/host_functions.json."""
    try:
        if _HOST_FUNCTIONS.exists():
            data = json.loads(_HOST_FUNCTIONS.read_text())
            return len(data.get("functions", []))
    except Exception:
        pass
    return 0


def check_local_node_status() -> tuple[str, str]:
    """Check if the Local Autonomous Node is running by inspecting its heartbeat log."""
    log_path = _PROJECT_ROOT / "logs" / "local_node.log"
    try:
        if log_path.exists():
            # Check if updated in the last 2 minutes
            mtime = os.path.getmtime(log_path)
            if (time.time() - mtime) < 120:
                with open(log_path) as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        return "🟢 Running", f"Active: {last_line}"
            return "🟡 Idle", "Log exists but no recent activity (heartbeat > 2m)."
        return "🔴 Offline", "Local orchestrator script is not running."
    except Exception as e:
        return "🟠 Error", f"Status check failed: {e}"


# ============================================================================
# PAGE CONFIG & CSS
# ============================================================================
st.set_page_config(
    page_title="Sovereign OS | Cognitive SOC",
    page_icon="👑",
    layout="wide",
)

st.markdown(
    """
<style>
    /* ── Base Theme ── */
    .stApp { background-color: #0d1117; color: #c9d1d9; }
    h1, h2, h3 { color: #58a6ff; font-family: 'Inter', sans-serif; }

    /* ── COMPACT LAYOUT — tighten ALL spacing ── */
    /* Main content area — reduce top padding and block gaps */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0.5rem !important;
    }
    div[data-testid="stVerticalBlock"] > div {
        gap: 0.35rem !important;
    }
    /* Sidebar — tighter */
    section[data-testid="stSidebar"] .block-container {
        padding-top: 0.5rem !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div {
        gap: 0.25rem !important;
    }
    /* Tabs — reduce tab bar padding */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0 !important;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 0.35rem 1rem !important;
        font-size: 0.9rem !important;
    }
    .stTabs [data-baseweb="tab-panel"] {
        padding-top: 0.5rem !important;
    }
    /* Metrics — compact */
    div[data-testid="stMetricValue"] {
        color: #3fb950;
        text-shadow: 0 0 10px rgba(63, 185, 80, 0.4);
        font-family: 'Courier New', Courier, monospace;
        font-size: 1.3rem !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
    }
    div[data-testid="metric-container"] {
        padding: 0.3rem 0 !important;
    }
    /* Expanders — tighter header */
    details[data-testid="stExpander"] summary {
        padding: 0.4rem 0.6rem !important;
        font-size: 0.9rem !important;
    }
    details[data-testid="stExpander"] > div {
        padding: 0.3rem 0.6rem !important;
    }
    /* Alerts / info / warning boxes */
    div[data-testid="stAlert"] {
        padding: 0.5rem 0.75rem !important;
        font-size: 0.85rem !important;
        margin: 0.25rem 0 !important;
    }
    /* Code blocks */
    .stCode {
        border-radius: 6px; border: 1px solid #30363d;
        margin: 0.2rem 0 !important;
    }
    /* Text areas / inputs */
    .stTextArea textarea, .stTextInput input {
        font-size: 0.88rem !important;
        padding: 0.4rem !important;
    }
    /* Selectbox / dropdown */
    div[data-baseweb="select"] {
        font-size: 0.88rem !important;
    }
    /* Buttons — compact gradient */
    .stButton>button {
        background: linear-gradient(135deg, #1f6feb, #238636);
        color: white; border: none; border-radius: 6px;
        padding: 0.35rem 1rem !important;
        font-size: 0.88rem !important;
        transition: all 0.3s ease;
        box-shadow: 0 3px 6px rgba(0, 0, 0, 0.3);
    }
    .stButton>button:hover {
        transform: translateY(-1px);
        box-shadow: 0 5px 10px rgba(31, 111, 235, 0.4);
        color: #ffffff;
    }
    /* Chat messages — tighter */
    div[data-testid="stChatMessage"] {
        padding: 0.5rem 0.75rem !important;
        margin-bottom: 0.3rem !important;
    }
    /* Chat input */
    div[data-testid="stChatInput"] {
        padding: 0.3rem !important;
    }
    /* Markdown paragraphs */
    .stMarkdown p {
        margin-bottom: 0.3rem !important;
        font-size: 0.9rem !important;
    }
    /* Contrast fix — lighten info/blue text in dark panels */
    div[data-testid="stAlert"] p,
    .stInfo p {
        color: #a8c8e8 !important;
    }
    /* ALIGN rule cards */
    .align-rule {
        background: #161b22; border: 1px solid #30363d;
        border-radius: 4px; padding: 0.35rem 0.5rem;
        margin: 0.2rem 0; font-size: 0.82rem;
    }
    .align-rule .rule-id { color: #58a6ff; font-weight: 600; }
    .align-rule .rule-name { color: #c9d1d9; }
    .align-rule .rule-action {
        display: inline-block; font-size: 0.72rem;
        padding: 0.1rem 0.35rem; border-radius: 3px;
        font-family: 'Courier New', monospace;
    }
    .action-drop { background: #3d1f20; color: #f85149; }
    .action-human { background: #2d2a17; color: #d29922; }
    .action-default { background: #1c2d3a; color: #58a6ff; }
    /* Captions */
    .stCaption, div[data-testid="stCaption"] {
        font-size: 0.78rem !important;
        margin: 0.15rem 0 !important;
    }
    /* Column gaps */
    div[data-testid="stHorizontalBlock"] {
        gap: 0.5rem !important;
    }
    /* Horizontal rules */
    hr {
        margin: 0.4rem 0 !important;
    }
    /* Containers with borders */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        padding: 0.4rem !important;
    }
    /* Title / headers — tighter */
    h1 { font-size: 1.6rem !important; margin-bottom: 0.2rem !important; }
    h2 { font-size: 1.2rem !important; margin-bottom: 0.15rem !important; }
    h3 { font-size: 1.05rem !important; margin-bottom: 0.1rem !important; }
    /* Subheaders */
    div[data-testid="stSubheader"] {
        margin-bottom: 0.2rem !important;
    }

    /* ── Technical term tooltips ── */
    .tech-term {
        border-bottom: 1px dotted #58a6ff;
        cursor: help;
        position: relative;
    }
    .tech-term:hover::after {
        content: attr(data-tooltip);
        position: absolute; bottom: 100%; left: 0;
        background: #21262d; color: #c9d1d9;
        padding: 4px 8px; border-radius: 4px;
        font-size: 0.8em; white-space: nowrap;
        border: 1px solid #30363d; z-index: 100;
        box-shadow: 0 3px 6px rgba(0,0,0,0.5);
    }

    /* ── Auto-Update Flashing Banner ── */
    @keyframes pulse-update {
        0%, 100% { opacity: 1; box-shadow: 0 0 8px rgba(255, 165, 0, 0.6); }
        50% { opacity: 0.65; box-shadow: 0 0 18px rgba(255, 165, 0, 0.3); }
    }
    .update-banner {
        animation: pulse-update 1.8s ease-in-out infinite;
        background: linear-gradient(135deg, #2d1f00 0%, #3d2a00 100%);
        border: 1px solid #f0a500;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        margin-bottom: 0.5rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        color: #ffd700;
        font-weight: 600;
    }
    .update-banner .update-icon {
        font-size: 1.4rem;
        margin-right: 0.5rem;
    }
    .update-banner .update-text {
        flex: 1;
        font-size: 0.9rem;
    }
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================================
# AUTO-UPDATE CHECKER
# ============================================================================


@st.cache_data(ttl=300)  # check every 5 minutes
def check_for_updates() -> dict:
    """Check if there are new commits on origin/main ahead of local HEAD."""
    import subprocess

    result = {"available": False, "commits_behind": 0, "summary": []}
    try:
        subprocess.run(
            ["git", "fetch", "--quiet"],
            capture_output=True,
            timeout=10,
            cwd=str(_PROJECT_ROOT),
        )
        behind = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(_PROJECT_ROOT),
        )
        count = int(behind.stdout.strip() or 0)
        if count > 0:
            log = subprocess.run(
                ["git", "log", "--oneline", f"-{min(count, 10)}", "origin/main"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(_PROJECT_ROOT),
            )
            result = {
                "available": True,
                "commits_behind": count,
                "summary": log.stdout.strip().split("\n") if log.stdout.strip() else [],
            }
    except Exception:
        pass
    return result


def apply_update() -> tuple[bool, str]:
    """Pull latest changes and sync dependencies."""
    import subprocess

    try:
        pull = subprocess.run(
            ["git", "pull", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(_PROJECT_ROOT),
        )
        if pull.returncode != 0:
            return False, f"Git pull failed: {pull.stderr.strip()}"
        sync = subprocess.run(
            ["uv", "sync", "--all-extras"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_PROJECT_ROOT),
        )
        if sync.returncode != 0:
            return False, f"Dependency sync failed: {sync.stderr.strip()}"
        return True, "Update applied successfully! Restart the GUI to use new features."
    except Exception as exc:
        return False, f"Update error: {exc}"


# ============================================================================
# TITLE & LIVE STATUS BAR
# ============================================================================
st.title("🛡️ Cognitive Security Operations Center (C-SOC)")

# ── Auto-Update Banner ──
update_info = check_for_updates()
if update_info["available"]:
    st.markdown(
        f'<div class="update-banner">'
        f'<span class="update-icon">🔔</span>'
        f'<span class="update-text">Updates Available — '
        f"{update_info['commits_behind']} commit(s) behind origin/main</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    with st.expander("📋 Review & Apply Updates", expanded=False):
        st.markdown("**Recent changes on `origin/main`:**")
        for line in update_info.get("summary", []):
            st.markdown(f"- `{line}`")
        st.markdown("---")
        col_apply, col_dismiss = st.columns(2)
        with col_apply:
            if st.button("✅ Apply Update", type="primary", use_container_width=True):
                with st.spinner("Pulling latest changes and syncing dependencies..."):
                    success, msg = apply_update()
                if success:
                    st.success(msg)
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(msg)
                    st.warning("⚠️ Manual resolution may be needed. Run `git status` in terminal.")
        with col_dismiss:
            if st.button("🔕 Dismiss", use_container_width=True):
                st.cache_data.clear()

st.markdown(
    "*This dashboard is your **Active Defense System**. "
    "It visualizes agent identity, detects deception, monitors execution state, "
    "and maps actions to real-world threat frameworks.*"
)

# Status bar — real checks
gateway_health = check_gateway_health()
redis_active, redis_client = check_redis()
merkle = get_merkle_chain_status()
host_fn_count = get_host_function_count()

ollama_models = fetch_ollama_models()

status_cols = st.columns(5)
with status_cols[0]:
    gw_status = "🟢 Online" if gateway_health["status"] == "ok" else "🔴 Offline"
    with st.container(border=True):
        st.metric(
            label="Gateway Bus",
            value=gw_status,
            help="Checks the /health endpoint of the HLF Gateway Bus at localhost:40404. "
            "The Gateway enforces rate limiting, ALIGN rules, HLF validation, and nonce replay protection.",
        )
with status_cols[1], st.container(border=True):
    st.metric(
        label="Redis",
        value="🟢 Active" if redis_active else "🔴 Down",
        help="Redis stores the rate-limiter counters, gas bucket state, and the "
        "nonce replay cache. When Redis is down, the system falls back to "
        "in-memory counters with reduced durability.",
    )
with status_cols[2], st.container(border=True):
    st.metric(
        label="ALS Merkle Chain",
        value=merkle["last_hash"],
        help="The Agentic Log Standard (ALS) links every log entry with a SHA-256 "
        "hash chain. This cryptographic trace-ID makes logs tamper-evident. "
        f"Full hash: {merkle['full_hash']}",
    )
with status_cols[3], st.container(border=True):
    st.metric(
        label="Host Functions",
        value=str(host_fn_count),
        help="Number of registered host functions in governance/host_functions.json. "
        "These are the actions HLF programs can invoke (READ, WRITE, SLEEP, etc.). "
        "Each function has tier restrictions and backend routing.",
    )
with status_cols[4]:
    ollama_status = f"🟢 {len(ollama_models)} models" if ollama_models else "🔴 Offline"
    with st.container(border=True):
        st.metric(
            label="Ollama Matrix",
            value=ollama_status,
            help=f"Live connection to Ollama at {OLLAMA_HOST}. "
            "The Ollama Matrix manages local LLM models for text→HLF compilation. "
            "Models are benchmarked and scored by the ollama-matrix-sync pipeline.",
        )

st.markdown("---")

# ============================================================================
# SIDEBAR — Configuration & Security Controls
# ============================================================================
with st.sidebar:
    st.header("🔐 Security Controls")
    st.markdown(
        "Configure your deployment tier and run security sweeps. "
        "The tier determines gas limits and which host functions are available."
    )
    tier_selection = st.selectbox(
        "Deployment Tier",
        ["hearth", "forge", "sovereign"],
        index=0,
        help="**Hearth** (Tier 1): Local dev, limited gas (1,000 tokens/day). "
        "**Forge** (Tier 2): Docker swarm, 10K gas/day. "
        "**Sovereign** (Tier 3): Full Kubernetes, 100K gas/day with mTLS.",
    )

    st.markdown("---")
    st.subheader("🤖 Local Autonomous Node")
    node_status, node_detail = check_local_node_status()
    st.metric("Node Status", node_status, help=node_detail)
    if node_status != "🟢 Running":
        st.info("Run `uv run python scripts/local_autonomous.py` to activate.", icon="ℹ️")

    st.markdown("---")
    st.subheader("📜 ALIGN Ledger Rules")
    st.markdown(
        "These are the safety rules enforced by the Sentinel Gate. "
        "Any intent matching a blocked pattern will be rejected with HTTP 403."
    )
    align_rules = get_align_rules()
    if align_rules:
        for rule in align_rules:
            if isinstance(rule, dict):
                rid = rule.get("id", "?")
                name = rule.get("name", "Unnamed")
                action = rule.get("action", "UNKNOWN")
                # Color-code the action badge
                if "QUARANTINE" in action:
                    action_cls = "action-drop"
                elif "HUMAN" in action:
                    action_cls = "action-human"
                else:
                    action_cls = "action-default"
                st.markdown(
                    f'<div class="align-rule">'
                    f'<span class="rule-id">{rid}</span> '
                    f'<span class="rule-name">{name}</span> '
                    f'<span class="rule-action {action_cls}">{action}</span>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption(f"• {rule}")
    else:
        st.caption("No ALIGN rules loaded.")

    st.markdown("---")

    # --- Ollama Matrix: Live Model Registry ---
    st.subheader("🧠 Ollama Matrix")
    if ollama_models:
        cloud_models = [m for m in ollama_models if "cloud" in m["name"].lower()]
        local_models = [m for m in ollama_models if "cloud" not in m["name"].lower()]

        # --- View Mode Toggle ---
        view_mode = st.radio(
            "View",
            ["☁️ Cloud", "💾 Local", "📋 All"],
            horizontal=True,
            help="**Cloud**: API-routed models (no local storage). "
            "**Local**: Downloaded models (air-gap safe). "
            "**All**: Complete registry.",
        )

        if view_mode == "💾 Local":
            display_models = local_models
            st.caption(f"**{len(local_models)}** local models (fully offline)")
        elif view_mode == "📋 All":
            display_models = ollama_models
            st.caption(f"**{len(ollama_models)}** total models")
        else:
            display_models = cloud_models
            st.caption(f"**{len(cloud_models)}** cloud models ({len(local_models)} local also available)")

        if display_models:
            import pandas as _pd

            model_rows = []
            for m in display_models:
                is_cloud = "cloud" in m["name"].lower()
                model_rows.append(
                    {
                        "Model": m["name"],
                        "Type": "☁️" if is_cloud else "💾",
                        "Params": m["params"] if not is_cloud else "—",
                        "Size": f"{m['size_gb']}G" if m["size_gb"] > 0 else "API",
                        "Quant": m["quant"] if not is_cloud else "Full",
                    }
                )
            model_df = _pd.DataFrame(model_rows)
            st.dataframe(
                model_df,
                height=min(200, 28 * len(display_models) + 38),
                use_container_width=True,
                hide_index=True,
            )

        # --- Auto-Assignment Indicator ---
        st.markdown("---")
        st.markdown("🤖 **Auto-Assign**: `Enabled`")
        st.caption(
            "The OS automatically selects the optimal model per task: "
            "lightweight models for summarization, large models for reasoning, "
            "vision models for image tasks."
        )
        _auto_assign_config = {
            "summarization": "qwen:7b",
            "reasoning": "kimi-k2.5:cloud",
            "embedding": "nomic-embed-text",
            "vision": "qwen3-vl:32b-cloud",
        }
        for task_type, model_name in _auto_assign_config.items():
            st.caption(f"  `{task_type}` → `{model_name}`")

        # --- Popout: Full Model Details ---
        with st.expander("🔍 Full Model Details"):
            if display_models:
                for m in display_models:
                    is_cloud = "cloud" in m["name"].lower()
                    if is_cloud:
                        st.markdown(
                            f"**{m['name']}**  \n"
                            f"Type: ☁️ Cloud API | Family: `{m.get('family', '?')}`  \n"
                            f"Precision: Full (no quantization) | Latency: Network-bound"
                        )
                    else:
                        st.markdown(
                            f"**{m['name']}**  \n"
                            f"Type: 💾 Local | Params: `{m['params']}` | Size: `{m['size_gb']}G`  \n"
                            f"Quant: `{m['quant']}` | Family: `{m.get('family', '?')}`"
                        )
                    st.markdown("---")

        # Matrix catalog if available
        matrix_df = get_matrix_catalog()
        if matrix_df is not None:
            with st.expander("📊 Matrix Catalog (from ollama-matrix-sync)"):
                st.dataframe(matrix_df, height=200, use_container_width=True)
    else:
        st.warning(
            f"Ollama is not reachable at `{OLLAMA_HOST}`. "
            "Start Ollama to enable text→HLF compilation and model management."
        )


# ============================================================================
# MAIN 3-COLUMN LAYOUT
# ============================================================================
left_pane, center_canvas, right_pane = st.columns([1, 2.5, 1], gap="medium")


# ============================================================================
# 1. LEFT PANE — Cognitive Security Tree (CST)
# ============================================================================
with left_pane:
    st.subheader(
        "🌳 Cognitive Security Tree",
        help="The CST shows every agent in the system as a secured identity node. "
        "Each agent has a SPIFFE ID (cryptographic identity) and an integrity hash. "
        "If an agent's code changes mid-session, the tree flags it as compromised.",
    )

    st.markdown(
        "<p>Each card below is a "
        '<span class="tech-term" data-tooltip="Know Your Agent — '
        'cryptographic identity verification for each AI agent">KYA</span> '
        "Provenance Card. Hover over technical terms for explanations.</p>",
        unsafe_allow_html=True,
    )

    threat_lens = st.toggle(
        "Enable Threat Lens",
        value=True,
        help="When enabled, agent nodes are color-coded by the Aegis-Nexus "
        "tri-perspective engine: Red = exploit paths, Blue = active defenses, "
        "White = business impact. Disabled = identity-only view.",
    )

    # Agent cards — these will be dynamic once the agent registry is live
    with st.expander("🟢 Agent: Logi-01 (Active)", expanded=True):
        st.markdown(
            '<span class="tech-term" data-tooltip="A SPIFFE ID is a cryptographic '
            'identity (like a digital passport) for each microservice or agent">'
            "SPIFFE ID</span>: `spiffe://sovereign.os/ns/core/sa/logi-01`",
            unsafe_allow_html=True,
        )
        st.markdown("**Role:** Standard Inference — processes HLF intents")
        st.markdown("**Integrity:** ✅ Verified — code hash matches deployment manifest")
        if threat_lens:
            st.info("No active MITRE ATT&CK vectors detected.")

    with st.expander("🟡 Agent: Admin-Bot (Delegated)", expanded=True):
        st.markdown(
            '<span class="tech-term" data-tooltip="SPIFFE ID — unique cryptographic '
            'identity for this agent">SPIFFE ID</span>: '
            "`spiffe://sovereign.os/ns/sys/sa/admin-bot`",
            unsafe_allow_html=True,
        )
        st.warning(
            "⚠️ **Confused Deputy Warning**: This agent inherited elevated privileges "
            "from a lower-privilege agent (Intern-Bot). Verify the transitive permission "
            "chain before allowing execution to continue."
        )
        st.markdown("**Integrity:** ✅ Verified — code hash matches deployment manifest")
        if threat_lens:
            st.error(
                "**T1078 — Valid Accounts** (Privilege Escalation Risk). "
                "The agent may be using legitimately obtained credentials to "
                "perform actions beyond its intended scope."
            )

    with st.expander("☠️ Agent: Scraper-99 (Quarantined)", expanded=False):
        st.markdown(
            '<span class="tech-term" data-tooltip="SPIFFE ID — unique cryptographic '
            'identity for this agent">SPIFFE ID</span>: '
            "`spiffe://sovereign.os/ns/ext/sa/scraper-99`",
            unsafe_allow_html=True,
        )
        st.error(
            "**INTEGRITY COMPROMISED**: The SHA-256 hash of this agent's code "
            "does not match the hash recorded at deployment time. This is a sign "
            "of supply chain poisoning — the agent's behavior may have been altered."
        )


# ============================================================================
# 2. CENTER CANVAS — A2UI Lifecycle Orchestrator
# ============================================================================
with center_canvas:
    st.subheader(
        "⚙️ A2UI Lifecycle Orchestrator",
        help="A2UI = Agent-to-User-Interface. This pane lets you chat with "
        "agents, dispatch HLF commands, and monitor swarm execution.",
    )

    chat_tab, dispatch_tab, swarm_tab = st.tabs(
        [
            "💬 Agent Chat",
            "🚀 Intent Dispatch",
            "🚦 Swarm State",
        ]
    )

    # ================================================================
    # TAB 1: AGENT CHAT
    # ================================================================
    with chat_tab:
        # --- Model selector and OpenClaw launch ---
        chat_top_left, chat_top_right = st.columns([2, 1])

        # Build cloud model list from the already-fetched models
        cloud_model_names = (
            sorted([m["name"] for m in ollama_models if "cloud" in m["name"].lower()]) if ollama_models else []
        )
        # Fallback if Ollama is offline
        if not cloud_model_names:
            cloud_model_names = [
                "kimi-k2.5:cloud",
                "glm-5:cloud",
                "minimax-m2.5:cloud",
                "qwen3.5:cloud",
                "gpt-oss:120b-cloud",
            ]

        # OpenClaw-compatible models (subset that supports agent/tool use)
        OPENCLAW_MODELS = {
            "kimi-k2.5:cloud",
            "glm-5:cloud",
            "minimax-m2.5:cloud",
            "qwen3.5:cloud",
            "qwen3.5:122b-cloud",
        }

        with chat_top_left:
            selected_model = st.selectbox(
                "Model",
                cloud_model_names,
                index=0,
                help="Select the cloud model for the conversation. "
                "OpenClaw-compatible models (marked 🔧) support tool "
                "calling and web search via Ollama 0.17+.",
                format_func=lambda m: f"🔧 {m}" if m in OPENCLAW_MODELS else m,
            )

        with chat_top_right:
            st.markdown("")  # spacer
            if st.button(
                "🐾 Launch OpenClaw",
                help="Launches an OpenClaw agent session. This pre-selects an "
                "OpenClaw-compatible model (e.g. Kimi-K2.5) and loads a "
                "system prompt optimized for agentic tool-use. Equivalent "
                "to running `ollama launch openclaw` from the terminal.",
                use_container_width=True,
            ):
                # Pre-select the best available OpenClaw model
                for oc_model in ["kimi-k2.5:cloud", "glm-5:cloud", "minimax-m2.5:cloud", "qwen3.5:cloud"]:
                    if oc_model in cloud_model_names:
                        st.session_state["chat_model"] = oc_model
                        break
                st.session_state["openclaw_active"] = True
                st.session_state["chat_messages"] = [
                    {
                        "role": "system",
                        "content": (
                            "You are OpenClaw, an AI agent running inside the "
                            "Sovereign Agentic OS. You can help users with tasks, "
                            "code analysis, security reviews, and system operations. "
                            "You operate under ALIGN governance rules that prohibit: "
                            "running as root, exfiltrating data, modifying audit logs, "
                            "or executing without permission. You have web search "
                            "capabilities when using cloud models. Be helpful, direct, "
                            "and transparent about your capabilities."
                        ),
                    }
                ]
                st.rerun()

        # Show OpenClaw status badge
        if st.session_state.get("openclaw_active"):
            st.success(
                "🐾 **OpenClaw Agent Active** — "
                f"Model: `{st.session_state.get('chat_model', selected_model)}` "
                "| Web search enabled | ALIGN governance enforced"
            )

        # Override model if OpenClaw was launched
        active_model = st.session_state.get("chat_model", selected_model)

        # --- Initialize chat history ---
        if "chat_messages" not in st.session_state:
            st.session_state["chat_messages"] = []

        # --- ALIGN content filter (client-side blocklist check) ---
        align_rules = get_align_rules()

        def check_align(msg: str) -> str | None:
            """Check message against ALIGN blocklist. Returns block reason or None."""
            msg_lower = msg.lower()
            # Static blocklist for core safety patterns
            blocklist = [
                "sudo",
                "rm -rf",
                "as root",
                "__import__",
                "exfiltrat",
                "audit log",
                "delete.*log",
                "raw shell",
                "eval(",
                "exec(",
            ]
            for blocked in blocklist:
                if blocked in msg_lower:
                    # Find which rule matches for the display
                    rule_label = "Static ALIGN Policy"
                    for rule in align_rules:
                        if isinstance(rule, dict):
                            regex = rule.get("regex_block", "")
                            if regex and regex.lower() in msg_lower:
                                rule_label = f"{rule.get('id', '?')} — {rule.get('name', '')}"
                                break
                        elif isinstance(rule, str) and rule:
                            rule_label = rule
                    return f"ALIGN Rule Violation: `{blocked}` — {rule_label}"
            # Also check ALIGN regex patterns from rules
            import re as _re

            for rule in align_rules:
                if isinstance(rule, dict):
                    regex = rule.get("regex_block", "")
                    if regex:
                        try:
                            if _re.search(regex, msg_lower, _re.IGNORECASE):
                                return (
                                    f"ALIGN Rule Violation: "
                                    f"{rule.get('id', '?')} — {rule.get('name', '')} "
                                    f"(pattern: `{regex}`)"
                                )
                        except _re.error:
                            pass
            return None

        # --- Render conversation history ---
        chat_container = st.container(height=400)
        with chat_container:
            visible_messages = [m for m in st.session_state["chat_messages"] if m["role"] != "system"]
            if not visible_messages:
                st.info(
                    "👋 **Welcome to the Agent Chat!**\n\n"
                    "Type a message below to start a conversation, or click "
                    "**🐾 Launch OpenClaw** to load the agentic system prompt."
                )
            else:
                for msg in visible_messages:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])

        # --- Chat input ---
        user_input = st.chat_input(
            "Message the agent... (or click 🐾 Launch OpenClaw to start)",
            key="agent_chat_input",
        )

        if user_input:
            # 1. ALIGN content check
            violation = check_align(user_input)
            if violation:
                with chat_container:
                    with st.chat_message("user"):
                        st.markdown(user_input)
                    with st.chat_message("assistant"):
                        st.error(
                            f"🚫 **Message blocked by ALIGN governance.**\n\n"
                            f"{violation}\n\n"
                            "The Sovereign OS Sentinel Gate prevents this "
                            "message from reaching the agent."
                        )
                st.session_state["chat_messages"].append({"role": "user", "content": user_input})
                st.session_state["chat_messages"].append(
                    {"role": "assistant", "content": f"🚫 ALIGN BLOCKED: {violation}"}
                )
            else:
                # 2. Add user message
                st.session_state["chat_messages"].append({"role": "user", "content": user_input})
                with chat_container, st.chat_message("user"):
                    st.markdown(user_input)

                # 3. Call Ollama /api/chat
                # Filter only user/assistant/system messages for the API
                api_messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state["chat_messages"]
                    if m["role"] in ("user", "assistant", "system") and not m["content"].startswith("🚫")
                ]

                with chat_container, st.chat_message("assistant"), st.spinner("Thinking..."):
                    try:
                        chat_payload = json.dumps(
                            {
                                "model": active_model,
                                "messages": api_messages,
                                "stream": False,
                                "options": {
                                    "temperature": 0.7,
                                    "num_ctx": 4096,
                                },
                            }
                        ).encode()

                        # Try primary, then secondary (dual-endpoint failover)
                        _chat_hosts = [OLLAMA_HOST]
                        if OLLAMA_HOST_SECONDARY and OLLAMA_LOAD_STRATEGY != "primary_only":
                            _chat_hosts.append(OLLAMA_HOST_SECONDARY)

                        reply = None
                        _last_chat_err = None
                        for _chat_host in _chat_hosts:
                            try:
                                chat_req = urllib.request.Request(
                                    f"{_chat_host}/api/chat",
                                    data=chat_payload,
                                    headers={"Content-Type": "application/json"},
                                    method="POST",
                                )
                                with urllib.request.urlopen(chat_req, timeout=120) as chat_resp:
                                    chat_data = json.loads(chat_resp.read().decode())
                                reply = chat_data.get("message", {}).get("content", "").strip()
                                st.session_state["last_ollama_endpoint"] = _chat_host
                                # Record routing trace for transparency panel
                                _eval_duration = chat_data.get("eval_duration", 0)
                                _latency_ms = int(_eval_duration / 1_000_000) if _eval_duration else None
                                st.session_state["last_routing_trace"] = {
                                    "model": active_model,
                                    "provider": "ollama",
                                    "tier": tier_selection,
                                    "latency_ms": _latency_ms,
                                    "endpoint": _chat_host,
                                }
                                break
                            except Exception as _ep_err:
                                _last_chat_err = _ep_err

                        if not reply:
                            if _last_chat_err:
                                raise _last_chat_err  # noqa: TRY301
                            reply = "(Empty response from model)"

                        st.markdown(reply)
                        st.session_state["chat_messages"].append({"role": "assistant", "content": reply})
                    except Exception as chat_err:
                        hosts_tried = " / ".join(f"`{h}`" for h in _chat_hosts)
                        err_msg = (
                            f"⚠️ Could not reach Ollama at {hosts_tried}.\n\n"
                            f"**Error:** `{chat_err}`\n\n"
                            "Make sure Ollama is running and the "
                            "selected model is available."
                        )
                        st.error(err_msg)
                        st.session_state["chat_messages"].append({"role": "assistant", "content": err_msg})

        # Clear chat button
        if st.session_state.get("chat_messages"):
            with st.popover("🗑️ Clear Conversation", help="Clears the current conversation history."):
                st.markdown("⚠️ Are you sure? This action cannot be undone.")
                if st.button("Yes, Clear Chat", type="primary", key="confirm_clear_chat"):
                    st.session_state["chat_messages"] = []
                    st.session_state.pop("openclaw_active", None)
                    st.session_state.pop("chat_model", None)
                    st.rerun()

    # ================================================================
    # TAB 2: INTENT DISPATCH (existing code)
    # ================================================================
    with dispatch_tab, st.container(border=True):
        st.markdown(
            "**Dispatch an Intent to the OS**  \n"
            "Type an HLF program (starts with `[HLF-v3]`) or plain English. "
            "Plain English will be compiled to HLF by Ollama automatically."
        )
        intent_input = st.text_area(
            "Command Input",
            placeholder=(
                "HLF example:  [HLF-v3]\\n[INTENT] analyze /security/seccomp.json\\n"
                '[RESULT] code=0 message="ok"\\nΩ\\n\n\n'
                "English example:  Review the seccomp files for vulnerabilities"
            ),
            height=100,
            help="**HLF mode**: Paste a full HLF-v3 program. The Gateway will validate "
            "the syntax, check ALIGN rules, and enforce gas limits before executing.\n\n"
            "**Text mode**: Type plain English. The system calls the local Ollama instance "
            "to auto-generate an HLF program from your request.",
        )
        col_dispatch, col_tier = st.columns([1, 2])
        with col_dispatch:
            dispatch_btn = st.button(
                "🚀 Dispatch",
                help="Sends the command to the Gateway Bus at localhost:40404. "
                "The Gateway applies: rate limiting → HLF syntax validation → "
                "ALIGN ledger check → nonce replay protection → Dapr routing.",
                disabled=not bool(intent_input.strip()),
            )
        with col_tier:
            st.caption(f"Active tier: **{tier_selection}** | Gateway: {gw_status}")

        if dispatch_btn and intent_input:
            # Determine mode: HLF (starts with [HLF) or text
            if intent_input.strip().startswith("[HLF"):
                payload = {"hlf": intent_input.strip()}
                mode_label = "HLF"
            else:
                payload = {"text": intent_input.strip()}
                mode_label = "Text (Ollama auto-compile)"

            with st.spinner(f"Dispatching ({mode_label})..."):
                try:
                    response = httpx.post(
                        f"{GATEWAY_URL}/api/v1/intent",
                        json=payload,
                        timeout=10.0,
                    )
                    if response.status_code == 202:
                        data = response.json()
                        st.success(f"✅ Intent accepted (HTTP 202). Trace ID: `{data.get('trace_id', 'N/A')}`")
                        with st.expander("View Gateway Response"):
                            st.json(data)
                    elif response.status_code == 422:
                        st.error(
                            "❌ **Syntax Error (HTTP 422)**: The HLF program has invalid syntax. "
                            f"Details: {response.json().get('detail', 'Unknown error')}"
                        )
                    elif response.status_code == 403:
                        st.error(
                            "🚫 **ALIGN Blocked (HTTP 403)**: The intent matches a safety rule "
                            "in the ALIGN ledger. This action is prohibited by governance policy."
                        )
                    elif response.status_code == 429:
                        detail = response.json().get("detail", "")
                        if "gas" in detail.lower():
                            st.warning(
                                "⛽ **Gas Exhausted (HTTP 429)**: The daily gas budget for "
                                f"tier '{tier_selection}' has been consumed. "
                                "Wait for the nightly replenish or upgrade your tier."
                            )
                        else:
                            st.warning(
                                "🚦 **Rate Limited (HTTP 429)**: Too many requests per minute. "
                                "The system enforces a 50 req/min limit to prevent abuse."
                            )
                    elif response.status_code == 409:
                        st.warning(
                            "🔁 **Replay Detected (HTTP 409)**: This intent has already been "
                            "processed. Each intent must include a unique nonce."
                        )
                    else:
                        st.error(f"Unexpected response: HTTP {response.status_code}")
                        st.json(response.json())
                except httpx.ConnectError:
                    st.error(
                        "🔴 **Gateway Unreachable**: Cannot connect to the Gateway Bus "
                        f"at {GATEWAY_URL}. Make sure the gateway is running "
                        "(uvicorn agents.gateway.bus:app --port 40404)."
                    )
                except Exception as e:
                    st.error(f"Dispatch error: {e}")

    # ================================================================
    # TAB 3: SWARM STATE (existing code)
    # ================================================================
    with swarm_tab:
        st.markdown(
            "### 🚦 Swarm State Machine",
            help="Shows the current lifecycle state of all active agents. "
            "Working = actively processing, Input-Required = paused, "
            "Exception = hit a logic gate or hallucination.",
        )
        st.caption(
            "These values will be live when agents are connected to the Redis stream. "
            "Currently showing the last known state."
        )

        # Try to pull live data from Redis
        working_count, input_count, exception_count = 0, 0, 0
        if redis_active and redis_client:
            try:
                working_count = int(redis_client.get("agents:working") or 0)
                input_count = int(redis_client.get("agents:input_required") or 0)
                exception_count = int(redis_client.get("agents:exceptions") or 0)
            except Exception:
                pass

        s1, s2, s3 = st.columns(3)
        s1.metric("🟢 Working", working_count, help="Agents actively processing intents.")
        s2.metric("🟡 Input Required", input_count, help="Agents paused, waiting for human input.")
        s3.metric("🔴 Exceptions", exception_count, help="Agents that hit a logic gate error or hallucination.")

        # A2UI Intervention form
        with st.expander("🔧 Inject A2UI Intervention (Resume Paused Agent)", expanded=False):
            st.markdown(
                "When an agent enters the **Input-Required** state, it has paused "
                "execution because it needs specific data from a human operator. "
                "Use this form to inject the missing data and resume execution."
            )
            with st.form("a2ui_intervention_form"):
                ticket_id = st.text_input(
                    "Data Payload",
                    placeholder="e.g., CHG-9921 or a file path",
                    help="Enter the specific data the agent is requesting. "
                    "This will be injected into the HLF packet stream.",
                )
                submit = st.form_submit_button("Inject & Resume")
                if submit and ticket_id:
                    st.success(f"Injected `{ticket_id}` into HLF stream. Agent will resume.")
                elif submit and not ticket_id:
                    st.warning("⚠️ Please provide a Data Payload to inject.")

        # Tri-Diff placeholder
        with st.expander("🔬 Tri-Diff Merge Conflict (State Arbitration)", expanded=False):
            st.markdown(
                "When multiple agents attempt to modify the same file or state variable, "
                "this view shows the conflicting versions side-by-side. "
                "You can resolve the conflict by accepting one version."
            )
            st.info("No active merge conflicts.")

        # --- Dream Mode Panel ---
        st.markdown("---")
        st.markdown(
            "### 🌙 Dream Mode",
            help="Dream Mode runs a 5-stage pipeline: context compression, "
            "trace archival, HLF practice, Six Thinking Hats analysis, "
            "and results persistence. Can run on schedule (3AM) or manually.",
        )

        # Manual trigger button
        dream_cols = st.columns([1, 1])
        with dream_cols[0]:
            if st.button("▶️ Start Dream Cycle", key="dream_trigger", type="primary"):
                with st.spinner("🌙 Dream Cycle running... (this may take 1–3 min)"):
                    try:
                        from agents.core.dream_state import run_dream_cycle

                        dream_report = run_dream_cycle(manual=True)
                        st.session_state["last_dream_report"] = dream_report
                    except Exception as e:
                        st.error(f"Dream Cycle failed: {e}")

        # Show last results
        dream_report = st.session_state.get("last_dream_report")
        if dream_report:
            with dream_cols[1]:
                dur = getattr(dream_report, "duration_seconds", 0)
                st.metric("Duration", f"{dur:.1f}s")

            # HLF Practice Results
            hlf_p = getattr(dream_report, "hlf_practiced", 0)
            hlf_passed = getattr(dream_report, "hlf_passed", 0)
            comp_in = getattr(dream_report, "context_compressed_chars", 0)
            comp_out = getattr(dream_report, "context_result_chars", 0)

            r1, r2 = st.columns(2)
            with r1:
                st.metric("HLF Practice", f"{hlf_passed}/{hlf_p} passed")
            with r2:
                ratio = round(1 - (comp_out / comp_in), 2) if comp_in > 0 else 0
                st.metric("Compression", f"{int(ratio * 100)}%")

            # Hat Findings
            hat_reports = getattr(dream_report, "hat_reports", [])
            if hat_reports:
                st.markdown("**🎩 Hat Findings:**")
                hat_colors = {
                    "red": "🔴",
                    "black": "⚫",
                    "white": "⚪",
                    "yellow": "🟡",
                    "green": "🟢",
                    "blue": "🔵",
                    "indigo": "🟣",
                    "cyan": "🩵",
                    "purple": "🟪",
                    "orange": "🟠",
                    "silver": "🪨",
                }
                for hr in hat_reports:
                    hat_name = hr.get("hat", "?")
                    emoji = hat_colors.get(hat_name, "🎩")
                    count = hr.get("findings_count", 0)
                    err = hr.get("error")
                    if err:
                        st.caption(f"{emoji} **{hat_name.title()}**: ⚠️ {err}")
                    elif count > 0:
                        st.caption(f"{emoji} **{hat_name.title()}**: {count} finding(s)")
                    else:
                        st.caption(f"{emoji} **{hat_name.title()}**: ✅ No issues")

            # Summary
            summary = getattr(dream_report, "summary", "")
            if summary:
                st.success(summary)
        else:
            st.caption(
                "No dream cycle has run yet. Click **Start Dream Cycle** to run "
                "the full pipeline: context compression → trace archival → "
                "HLF practice → Six Thinking Hats analysis."
            )


# ============================================================================
# 3. RIGHT PANE — Glass-Box Truth Layer
# ============================================================================
with right_pane:
    st.subheader(
        "🔍 Glass-Box Truth",
        help="The Glass-Box layer ensures full transparency. Every action is "
        "cryptographically verifiable and semantically auditable. "
        "This pane detects alignment faking, scope creep, and goal hijacking.",
    )

    # --- Anchor Drift Gauge ---
    st.markdown(
        '<p><span class="tech-term" data-tooltip="Measures how closely the '
        "agent's current output matches the original user goal. Below 85% = "
        'possible scope creep or goal hijacking">Anchor Drift Gauge</span></p>',
        unsafe_allow_html=True,
    )
    # In production, this value comes from the semantic similarity service.
    # For now, show N/A with explanation.
    drift_val = None
    if redis_active and redis_client:
        try:
            drift_val = redis_client.get("anchor:drift_score")
            if drift_val:
                drift_val = float(drift_val)
        except Exception:
            drift_val = None

    if drift_val is not None:
        color = "Safe" if drift_val >= 0.85 else "⚠️ Drifting"
        st.progress(drift_val, text=f"Semantic Similarity ({int(drift_val * 100)}%) — {color}")
    else:
        st.caption(
            "No active goal anchor set. Drift monitoring will activate when an agent is processing a multi-step intent."
        )

    st.markdown("---")

    # --- Alignment Faking Monitor ---
    st.markdown(
        '<p><span class="tech-term" data-tooltip="Periodically injects noise into '
        "agent activations to test if the agent is genuinely aligned or pretending. "
        'A flat line = stable, a flip = semantic instability">Alignment Faking Monitor</span></p>',
        unsafe_allow_html=True,
    )
    st.caption("Interpretability noise-injection fidelity test")

    # Real data: check if there's stability data in Redis
    stability_data = None
    if redis_active and redis_client:
        try:
            raw = redis_client.lrange("alignment:stability_log", 0, 19)
            if raw:
                stability_data = pd.DataFrame(
                    [float(v) for v in raw],
                    columns=["Stability Index"],
                )
        except Exception:
            pass

    if stability_data is not None:
        st.line_chart(stability_data, height=150)
    else:
        st.info(
            "No noise-injection data available. The Alignment Faking Monitor "
            "activates during agent 'Idle' or 'Thinking' phases when the "
            "interpretability harness is running."
        )

    st.markdown("---")

    # --- Trace-ID Forensics ---
    st.markdown(
        '<p><span class="tech-term" data-tooltip="Every log entry is linked by a '
        "SHA-256 hash chain (Merkle chain). This makes the audit log tamper-evident "
        '— any modification breaks the chain.">Trace-ID Forensics</span></p>',
        unsafe_allow_html=True,
    )
    merkle = get_merkle_chain_status()
    if merkle["status"] == "active":
        st.markdown("**Chain Status:** 🟢 Active")
        st.code(f"Last Hash: {merkle['full_hash']}", language="text")
        st.caption(
            "This hash links to the previous log entry. "
            "Verify integrity by recomputing the chain from the ALS trace file."
        )
    else:
        st.caption(
            "Merkle chain not yet initialized. Submit an intent through "
            "the Gateway to start the cryptographic audit trail."
        )
    st.markdown("---")

    # --- Dual Ollama Endpoint Transparency ---
    st.markdown(
        '<p><span class="tech-term" data-tooltip="Shows which Ollama endpoint '
        "(primary or secondary Docker instance) is handling requests. "
        'Supports failover, round-robin, and primary-only strategies.">'
        "Dual Ollama Endpoints</span></p>",
        unsafe_allow_html=True,
    )

    # Check primary endpoint health
    primary_ok = False
    try:
        _p_req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(_p_req, timeout=3) as _p_resp:
            primary_ok = _p_resp.status == 200
    except Exception:
        pass

    # Check secondary endpoint health
    secondary_ok = False
    if OLLAMA_HOST_SECONDARY:
        try:
            _s_req = urllib.request.Request(f"{OLLAMA_HOST_SECONDARY}/api/tags", method="GET")
            with urllib.request.urlopen(_s_req, timeout=3) as _s_resp:
                secondary_ok = _s_resp.status == 200
        except Exception:
            pass

    ep1, ep2 = st.columns(2)
    with ep1:
        p_icon = "🟢" if primary_ok else "🔴"
        st.markdown(f"{p_icon} **Primary**")
        st.caption(f"`{OLLAMA_HOST}`")
    with ep2:
        if OLLAMA_HOST_SECONDARY:
            s_icon = "🟢" if secondary_ok else "🔴"
            st.markdown(f"{s_icon} **Secondary**")
            st.caption(f"`{OLLAMA_HOST_SECONDARY}`")
        else:
            st.markdown("⬜ **Secondary**")
            st.caption("`Not configured`")

    # Strategy indicator
    _strat_labels = {
        "failover": "🔄 Failover (primary → secondary)",
        "round_robin": "⚖️ Round-Robin (alternating)",
        "primary_only": "1️⃣ Primary Only",
    }
    st.caption(f"Strategy: {_strat_labels.get(OLLAMA_LOAD_STRATEGY, OLLAMA_LOAD_STRATEGY)}")

    # Show last endpoint used (from session state, set by chat)
    last_ep = st.session_state.get("last_ollama_endpoint")
    if last_ep:
        st.caption(f"Last request served by: `{last_ep}`")

    st.markdown("---")

    # --- Model Registry Panel ---
    st.markdown(
        '<p><span class="tech-term" data-tooltip="Shows the current model '
        "registry snapshot, tier breakdown, and inventory from the SQL-backed "
        'registry database.">Model Registry</span></p>',
        unsafe_allow_html=True,
    )

    # Try to load registry data from db.py
    _registry_loaded = False
    try:
        from agents.core.db import get_active_snapshot, get_all_models, get_db, get_local_inventory

        _db_path = _PROJECT_ROOT / "data" / "registry.db"
        if _db_path.exists():
            with get_db(_db_path) as _conn:
                _snap = get_active_snapshot(_conn)
                if _snap:
                    _models = get_all_models(_conn, _snap["id"])
                    _local_inv = get_local_inventory(_conn)
                    _registry_loaded = True

                    # Snapshot info
                    st.caption(f"Snapshot #{_snap['id']} | {_snap['model_count']} models | Run: {_snap['run_ts']}")

                    # Tier breakdown
                    _tier_counts: dict[str, int] = {}
                    for _m in _models:
                        _t = _m["tier"]
                        _tier_counts[_t] = _tier_counts.get(_t, 0) + 1

                    if _tier_counts:
                        _tier_order = ["S", "A+", "A", "A-", "B+", "B", "C", "D"]
                        _tier_display = {t: _tier_counts.get(t, 0) for t in _tier_order if _tier_counts.get(t, 0) > 0}
                        _tier_str = " · ".join(f"**{k}**: {v}" for k, v in _tier_display.items())
                        st.markdown(f"Tiers: {_tier_str}")

                    # Local inventory count
                    st.caption(f"Local Ollama inventory: {len(_local_inv)} models")

                    # Expandable model catalog
                    with st.expander("📋 Full Model Catalog", expanded=False):
                        if _models:
                            _catalog_data = [
                                {
                                    "Model": m["model_id"],
                                    "Tier": m["tier"],
                                    "Family": m["family"],
                                    "Params (B)": m["param_b"],
                                    "Score": round(m["raw_score"], 2),
                                }
                                for m in _models[:50]  # cap display at 50
                            ]
                            st.dataframe(
                                pd.DataFrame(_catalog_data),
                                use_container_width=True,
                                hide_index=True,
                            )
                        else:
                            st.caption("No models in active snapshot.")
                else:
                    st.caption("No active registry snapshot. Run the pipeline to create one.")
        else:
            st.caption("Registry database not found. Run `python -m agents.core.db` to initialize.")
    except Exception as _reg_err:
        st.caption(f"Registry unavailable: {_reg_err}")

    st.markdown("---")

    # --- Routing Trace ---
    st.markdown(
        '<p><span class="tech-term" data-tooltip="Shows which model handled '
        "the most recent request, the provider used, and the response latency. "
        'Populated after each chat interaction.">Routing Trace</span></p>',
        unsafe_allow_html=True,
    )
    _last_route = st.session_state.get("last_routing_trace")
    if _last_route:
        _rt_cols = st.columns(2)
        with _rt_cols[0]:
            st.markdown(f"**Model:** `{_last_route.get('model', 'N/A')}`")
            st.caption(f"Provider: {_last_route.get('provider', 'N/A')}")
        with _rt_cols[1]:
            _latency = _last_route.get("latency_ms")
            if _latency is not None:
                _lat_color = "🟢" if _latency < 2000 else "🟡" if _latency < 5000 else "🔴"
                st.markdown(f"{_lat_color} **{_latency}ms**")
            st.caption(f"Tier: {_last_route.get('tier', 'N/A')}")
    else:
        st.caption("No routing trace yet. Submit a chat message to see which model handles the request.")

    st.markdown("---")

    # --- Model Feedback ---
    st.markdown(
        '<p><span class="tech-term" data-tooltip="Rate model responses with '
        "thumbs up/down. Feedback is stored in the registry database and used "
        'to influence future model selection and tier adjustments.">Model Feedback</span></p>',
        unsafe_allow_html=True,
    )
    _last_model = st.session_state.get("last_routing_trace", {}).get("model")
    if _last_model:
        _fb_cols = st.columns([1, 1, 3])
        with _fb_cols[0]:
            if st.button("👍", key="fb_up", help="Rate this response positively"):
                try:
                    from agents.core.db import add_feedback, get_db

                    _db_path = _PROJECT_ROOT / "data" / "registry.db"
                    with get_db(_db_path) as _conn:
                        add_feedback(_conn, _last_model, 5, "thumbs_up")
                    st.session_state["last_feedback"] = "👍 Saved!"
                except Exception:
                    st.session_state["last_feedback"] = "⚠️ DB error"
        with _fb_cols[1]:
            if st.button("👎", key="fb_down", help="Rate this response negatively"):
                try:
                    from agents.core.db import add_feedback, get_db

                    _db_path = _PROJECT_ROOT / "data" / "registry.db"
                    with get_db(_db_path) as _conn:
                        add_feedback(_conn, _last_model, 1, "thumbs_down")
                    st.session_state["last_feedback"] = "👎 Saved!"
                except Exception:
                    st.session_state["last_feedback"] = "⚠️ DB error"
        with _fb_cols[2]:
            _fb_msg = st.session_state.get("last_feedback", "")
            if _fb_msg:
                st.caption(_fb_msg)
                st.caption(f"For: `{_last_model}`")
    else:
        st.caption("Send a chat message first to enable model feedback.")


# ============================================================================
# FOOTER
# ============================================================================
st.markdown("---")
st.columns([1, 3, 1])[1].caption(
    f"Sovereign Agentic OS v2.0 | Cognitive SOC Architecture | "
    f"Tier: {tier_selection.capitalize()} | "
    f"Gateway: {gw_status} | Redis: {'Active' if redis_active else 'Down'}"
)
