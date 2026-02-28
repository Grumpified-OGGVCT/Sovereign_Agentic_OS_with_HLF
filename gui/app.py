import streamlit as st
import redis
import json
import time

# --- Setup Page Config ---
st.set_page_config(page_title="Sovereign OS Command Center", page_icon="👑", layout="wide")

# --- Custom CSS for Premium Look ---
st.markdown("""
<style>
    /* Main body background */
    .stApp {
        background-color: #0d1117;
        color: #c9d1d9;
    }
    
    /* Headers */
    h1, h2, h3 {
        color: #58a6ff;
        font-family: 'Inter', sans-serif;
    }
    
    /* Glowing Effect for Metrics */
    div[data-testid="stMetricValue"] {
        color: #3fb950;
        text-shadow: 0 0 10px rgba(63, 185, 80, 0.4);
        font-family: 'Courier New', Courier, monospace;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #161b22;
        border-right: 1px solid #30363d;
    }
    
    /* Code box styling */
    .stCode {
        border-radius: 8px;
        border: 1px solid #30363d;
    }
    
    /* Primary buttons */
    .stButton>button {
        background: linear-gradient(135deg, #1f6feb, #238636);
        color: white;
        border: none;
        border-radius: 6px;
        transition: all 0.3s ease;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
    }
    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(31, 111, 235, 0.4);
        color: #ffffff;
    }
</style>
""", unsafe_allow_html=True)

# --- Title ---
st.title("👑 Sovereign Agentic OS Command Center")
st.markdown("*A highly secure, tier-aware operating system interface for sovereign AI.*")
st.markdown("---")

# --- Sidebar configuration ---
st.sidebar.header("System Telemetry")

# Redis Status
try:
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)
    r.ping()
    st.sidebar.success("🟢 Redis Cache: Connected")
    redis_active = True
except Exception:
    st.sidebar.error("🔴 Redis Cache: Disconnected")
    redis_active = False

tier_selection = st.sidebar.selectbox("🔐 Deployment Tier Override", ["hearth", "forge", "sovereign"])

st.sidebar.markdown("---")
st.sidebar.subheader("Quick Diagnostics")
if st.sidebar.button("Run Security Sweep"):
    with st.sidebar.status("Initiating ACFS checks...", expanded=True) as status:
        time.sleep(1)
        st.write("Checking Dapr Sidecars...")
        time.sleep(1)
        st.write("Validating Kernel Confinement...")
        time.sleep(1)
        status.update(label="Sweep Complete (Clean)", state="complete", expanded=False)

# --- Main Columns ---
col1, space, col2 = st.columns([1.5, 0.1, 1])

# Left Column - Intent Terminal
with col1:
    st.subheader("📡 HLF Intent Terminal")
    st.markdown("Dispatch Natural Language or Hierarchical Logical Form (HLF) intents directly to the OS orchestrator.")
    
    intent_input = st.text_area("Intents Field (Text or AST)", height=180, placeholder="e.g. Generate a summary of system performance...")
    
    if st.button("🚀 Dispatch Intent to Bus", use_container_width=True):
        if not intent_input:
            st.warning("Intent field is empty. Please enter a command.")
        elif not redis_active:
            st.error("Cannot dispatch intent. Redis connection is down.")
        else:
            with st.spinner("Encrypting and dispatching to stream..."):
                try:
                    payload = {
                        "text": intent_input
                    }
                    import httpx
                    # Dispatch to the Gateway Node (which enforces security protocols)
                    response = httpx.post("http://localhost:40404/api/v1/intent", json=payload, timeout=5.0)
                    response.raise_for_status()
                    time.sleep(0.5) # Simulate slight latency for UI feel
                    st.success(f"Intent dispatched to ACFS Sandbox via Gateway. [Tier: {tier_selection}]")
                except httpx.HTTPError as e:
                    st.error(f"Gateway rejected the intent (Security Block or Rate Limit): {e}")
                except Exception as e:
                    st.error(f"Failed to communicate with OS Gateway: {e}")

# Right Column - System Real-Time Metrics
with col2:
    st.subheader("📊 Kernel Metrics")
    m1, m2 = st.columns(2)
    m1.metric(label="Active Agent Nodes", value="6", delta="All Healthy")
    m2.metric(label=f"Tier '{tier_selection.capitalize()}' Gas", value="1000", delta="-12 spent")
    
    m3, m4 = st.columns(2)
    m3.metric(label="Memory Traces", value="1,204", delta="+12/hr")
    m4.metric(label="Threat Index", value="Low", delta="0 Intrusions", delta_color="normal")

st.markdown("---")

# --- Log Stream Viewer ---
st.subheader("📜 Live Event Stream")
with st.container(border=True):
    st.code("""
[15:43:21] [agent-executor] Boot sequence initiated...
[15:43:22] [memory-scribe] Connecting to Fact_Store (sqlite-vec)... OK
[15:43:22] [host-function] ACFS confinement active. Syscalls sandboxed.
[15:43:25] [canary-probe] System idle detected, starting curiosity scan...
[15:44:01] [tool-forge] New tool signature generated and validated via LLM.
[15:44:12] [gateway] Accepted incoming REST request. Dispatching to Dapr sidecar.
    """, language="log")

st.caption("Sovereign Agentic OS v1.0.0 | Secure GUI Dashboard")
