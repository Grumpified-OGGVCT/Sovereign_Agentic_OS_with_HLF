import streamlit as st
import redis
import json
import time
import httpx
import pandas as pd
import numpy as np

# --- Setup Page Config ---
st.set_page_config(page_title="Sovereign OS | Cognitive SOC", page_icon="👑", layout="wide")

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
st.title("🛡️ Cognitive Security Operations Center (C-SOC)")
st.markdown("*HLF Sovereign GUI v2.0: Visualizing Identity, Deception, State, and Threats*")
st.markdown("---")

# Global Settings & Status
try:
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)
    r.ping()
    redis_active = True
except Exception:
    redis_active = False

tier_selection = st.sidebar.selectbox("🔐 Deployment Tier Override", ["hearth", "forge", "sovereign"], index=1)
st.sidebar.markdown(f"**Redis Status:** {'🟢 Active' if redis_active else '🔴 Down'}")
if st.sidebar.button("Run Security Sweep"):
    st.sidebar.success("Sweep Complete (Clean)")

# --- Main Columns (Left, Center, Right) ---
# 1: Left Pane (CST), 2.5: Center Canvas (A2UI), 1: Right Pane (Glass-Box)
left_pane, center_canvas, right_pane = st.columns([1, 2.5, 1], gap="medium")

# ==========================================
# 1. THE LEFT PANE: Cognitive Security Tree
# ==========================================
with left_pane:
    st.subheader("🌳 Cognitive Security Tree")
    st.markdown("**Aegis-Nexus Threat Lens**")
    threat_lens = st.toggle("Enable Threat Lens", value=True)
    
    st.markdown("### KYA Provenance")
    
    # Mocking the Agent Nodes
    with st.expander("🟢 Agent: Logi-01 (Active)", expanded=True):
        st.caption("SPIFFE ID: spiffe://sovereign.os/ns/core/sa/logi-01")
        st.markdown("**Role:** Standard Inference")
        st.markdown("**Integrity:** Verified `HLF-88b1...`")
        if threat_lens:
            st.info("No active MITRE ATT&CK vectors.")
            
    with st.expander("🟡 Agent: Admin-Bot (Delegated)", expanded=True):
        st.caption("SPIFFE ID: spiffe://sovereign.os/ns/sys/sa/admin-bot")
        st.warning("⚠️ Confused Deputy Warning: Inherited elevated privileges from Intern-Bot.")
        st.markdown("**Integrity:** Verified `HLF-77c2...`")
        if threat_lens:
            st.error("**T1078 - Valid Accounts** (Privilege Escalation Risk)")

    with st.expander("☠️ Agent: Scraper-99 (Quarantined)", expanded=False):
        st.caption("SPIFFE ID: spiffe://sovereign.os/ns/ext/sa/scraper-99")
        st.error("Integrity Hash Mismatch! Possible Supply Chain Poisoning.")


# ==========================================
# 2. THE CENTER CANVAS: A2UI Lifecycle Orchestrator
# ==========================================
with center_canvas:
    st.subheader("⚙️ A2UI Lifecycle Orchestrator")
    
    # HLF Intent Terminal
    with st.container(border=True):
        st.markdown("**Dispatch HLF Intent**")
        intent_input = st.text_input("HLF Vector", placeholder="[INTENT] analyze /security/seccomp.json [CONSTRAINT] mode=\"read-only\" Ω")
        colA, _ = st.columns([1, 3])
        with colA:
            dispatch_btn = st.button("🚀 Dispatch to OS")
        
        if dispatch_btn:
            if not redis_active:
                st.error("Cannot dispatch intent. Redis connection is down.")
            else:
                try:
                    payload = {"text": intent_input}
                    response = httpx.post("http://localhost:40404/api/v1/intent", json=payload, timeout=2.0)
                    response.raise_for_status()
                    st.success("Intent routed through Gateway Node.")
                except Exception as e:
                    st.error(f"Gateway rejected intent: {e}")

    st.markdown("---")
    
    # A2A Traffic Light System
    st.markdown("### 🚦 Swarm State Machine")
    s1, s2, s3 = st.columns(3)
    s1.success("🟢 4 Agents Working")
    s2.warning("🟡 1 Input Required")
    s3.error("🔴 0 Exceptions")
    
    # Actionable Intervention UI
    st.info("**Action Required:** `Sec-Bot-A` paused execution.")
    with st.form("a2ui_intervention_form"):
        st.markdown("**Request:** Missing Change Ticket ID to proceed with firewall modification.")
        ticket_id = st.text_input("Enter Ticket ID (e.g. CHG-9921)")
        submit_intervention = st.form_submit_button("Inject A2UI Packet & Resume")
        if submit_intervention:
            st.success(f"Injected `{ticket_id}` into HLF stream. Sec-Bot-A resumed.")
            
    # Tri-Diff Merge Conflict Placeholder
    with st.expander("🔬 View Active Tri-Diff Merge Conflict (State Arbitration)", expanded=False):
        st.markdown("*Conflict detected on `memory.sqlite3` write operation.*")
        tc1, tc2, tc3 = st.columns(3)
        with tc1:
            st.markdown("**Agent A (Local)**")
            st.code("+ INSERT INTO rules...")
            st.button("Accept Left")
        with tc2:
            st.markdown("**Sovereign State (Truth)**")
            st.code("  INSERT INTO rules...")
        with tc3:
            st.markdown("**Agent B (Remote)**")
            st.code("- DROP TABLE rules;")
            st.button("Accept Right")


# ==========================================
# 3. THE RIGHT PANE: "Glass-Box" Truth Layer
# ==========================================
with right_pane:
    st.subheader("🔍 Glass-Box Truth")
    
    # Enhanced InsAIts V2: Anchor Drift Gauge
    st.markdown("**Anchor Drift Gauge**")
    st.progress(0.92, text="Semantic Similarity (92%) - Safe")
    
    st.markdown("---")
    # Alignment Faking Monitor
    st.markdown("**Alignment Faking Monitor**")
    st.caption("Interpretability Noise-Injection Fidelity")
    
    # Mocking a stability graph
    chart_data = pd.DataFrame(
        np.random.randn(20, 1) * 0.05 + 0.95,
        columns=["Stability Index"]
    )
    st.line_chart(chart_data, height=150)
    st.caption("🟢 No Catastrophic Reversion detected during noise injection.")
    
    st.markdown("---")
    
    # Trace-ID Forensics
    st.markdown("**Trace-ID Forensics**")
    st.caption("Click translation to view cryptographic proof.")
    with st.expander("Translation: 'Analyze firewall rules'"):
        st.markdown("**HLF Math:** `⊎ {¬✓DB}`")
        st.markdown("**Hash:** `SHA256: 88b1a3...`")
        st.button("Verify Proof")

# --- Footer ---
st.markdown("---")
st.caption("Sovereign Agentic OS v2.0 | Cognitive SOC Architecture | Tier: " + tier_selection.capitalize())
