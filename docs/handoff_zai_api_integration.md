# Handoff: z.AI Full Capability Assessment & Sovereign OS Integration

**Date:** 2026-03-08
**From:** Antigravity
**To:** Next agent working on the Sovereign OS build
**Status:** ✅ OS-level integration complete — `zai_client.py`, `zai_tools.py`, 5 ToolRegistry entries, 4 τ() host functions, `settings.json` provider config

---

## 1. Complete z.AI Capability Catalog

### 1A. MCP Server: Vision (`@z_ai/mcp-server` — stdio)

**Installed in Antigravity:** ✅ Yes
**Powered by:** GLM-4.6V (128K context, native multimodal tool use)

| Tool | Description | Use Case |
|------|-------------|----------|
| `ui_to_artifact` | Turn UI screenshots into code, prompts, specs, or descriptions | Generate HTML/CSS/JS from a mockup screenshot. Frontend replication. |
| `extract_text_from_screenshot` | OCR optimized for code, terminals, docs, and general text | Read code from terminal screenshots, extract text from document images |
| `diagnose_error_screenshot` | Analyze error snapshots and propose actionable fixes | Screenshot a stack trace or error dialog → get root cause + fix |
| `understand_technical_diagram` | Interpret architecture, flow, UML, ER, and system diagrams | Feed in an architecture diagram → get structured understanding |
| `analyze_data_visualization` | Read charts and dashboards to surface insights and trends | Analyze a Grafana dashboard screenshot for anomalies |
| `ui_diff_check` | Compare two UI screenshots to flag visual or implementation drift | Visual regression testing between versions |
| `image_analysis` | General-purpose image understanding (fallback) | Anything not covered by specialized tools above |
| `video_analysis` | Inspect videos (local/remote ≤8MB; MP4/MOV/M4V) | Describe scenes, moments, entities in video clips |

**GLM-4.6V Advanced Capabilities (backing these tools):**
- **Native Multimodal Tool Use:** Images/screenshots passed directly as tool parameters — no text conversion needed
- **Pixel-Level UI Replication:** Upload a screenshot → generates high-fidelity HTML/CSS/JS code
- **Visual Debugging:** Circle an area on a screenshot, give natural language instructions ("move this button left") → auto-locates and fixes code
- **Complex Document Understanding:** Text, charts, figures, formulas in structured documents
- **Financial Analysis:** Process multiple financial reports simultaneously, extract and compare metrics
- **Video Understanding:** Global summarization + fine-grained temporal reasoning (timestamps, events)
- **Visual Web Search:** Autonomously calls search tools to find candidate images during generation

---

### 1B. MCP Server: Web Search (HTTP only)

**Installed in Antigravity:** ❌ No (HTTP transport not supported)
**Endpoint:** `https://api.z.ai/api/mcp/web_search_prime/mcp`

| Tool | Description |
|------|-------------|
| `webSearchPrime` | Search web information, returning page titles, URLs, summaries, site names, site icons |

**Capabilities:**
- Latest technical documentation and API changes
- Open-source project updates
- Solutions and best practices lookup
- Returns structured results (title, URL, summary, site name, icon)

**Quota (Lite Plan):** Shares pool of 100 calls/month with Web Reader and Zread
**Note:** Bundled into `@z_ai/mcp-server` stdio package as well, so accessible via Vision MCP server install.

---

### 1C. MCP Server: Web Reader (HTTP only)

**Installed in Antigravity:** ❌ No (HTTP transport not supported)
**Endpoint:** `https://api.z.ai/api/mcp/web_reader/mcp`

| Tool | Description |
|------|-------------|
| `webReader` | Fetch webpage content for a URL. Returns title, main content, metadata, links |

**Scenarios:**
- API documentation reading and summarization
- Open-source project page parsing
- Technical article knowledge extraction
- Bug resolution using reference documentation
- Knowledge base construction and synchronization

**Quota:** Shares the 100/month pool with Web Search and Zread
**Note:** Also bundled into `@z_ai/mcp-server` stdio package.

---

### 1D. MCP Server: Zread (HTTP only)

**Installed in Antigravity:** ❌ No (HTTP transport not supported)
**Endpoint:** `https://api.z.ai/api/mcp/zread/mcp`

| Tool | Description |
|------|-------------|
| `search_doc` | Search knowledge docs for a GitHub repo — understand repo knowledge, news, recent issues, PRs, contributors |
| `get_repo_structure` | Get directory structure and file list of a GitHub repo — understand module splitting and organization |
| `read_file` | Read complete code content of specified files in a GitHub repo — deep analysis of implementation details |

**Scenarios:**
- Quick start with open-source libraries (understand structure before using)
- Issue troubleshooting and history lookup
- Deep source code analysis of dependencies
- Dependency library research

**Limitations:** Public repositories only. Visit zread.ai to check if a repo is indexed.
**Quota:** Shares the 100/month pool with Web Search and Web Reader

---

### 1E. Extensions Toolbox

#### Coding Tool Helper (`npx @z_ai/coding-helper` or `chelper`)

**What it is:** A CLI wizard for configuring z.AI across coding tools.

| Command | What It Does |
|---------|-------------|
| `coding-helper init` | Launch initialization wizard (interactive setup) |
| `coding-helper auth` | Configure API key interactively |
| `coding-helper auth glm_coding_plan_global <token>` | Set key directly for Global plan |
| `coding-helper auth revoke` | Remove stored key |
| `coding-helper auth reload claude` | Load latest plan into Claude Code |
| `coding-helper doctor` | Check system config and tool status |
| `coding-helper lang set en_US` | Switch language |
| `coding-helper --version` | Show version |

**Supports:** Claude Code, OpenCode, Crush, Factory Droid
**For Antigravity:** Not directly useful (Antigravity isn't a supported tool), but `doctor` can verify API key validity.

#### Usage Query Plugin (`glm-plan-usage`)

**What it is:** A Claude Code plugin to query Coding Plan quota/usage.

| Command | What It Does |
|---------|-------------|
| `/glm-plan-usage:usage-query` | Show remaining prompts, quota status, cycle timing |

**Install:** `claude plugin marketplace add zai-org/zai-coding-plugins` → `claude plugin install glm-plan-usage@zai-coding-plugins`
**For Antigravity:** Not applicable (Claude Code plugin only)

---

## 2. Full API Capabilities (OpenAI-Compatible)

### Endpoint & Authentication
```python
from openai import OpenAI
client = OpenAI(
    api_key="1fa442f2272e4dd8af7448dadc874f5a.HwB8cAZluy8tz0IS",
    base_url="https://api.z.ai/api/paas/v4/"
)
```

### Complete Model Catalog

#### Text/Language Models
| Model | Level | Context | Concurrency | Best For |
|-------|-------|---------|-------------|----------|
| `glm-5` | Opus-level | Large | 5 | Complex reasoning, agentic engineering, large-scale tasks |
| `glm-4.7` | Sonnet-level | 200K | 3 | Daily dev, routine coding, 55+ tok/s |
| `glm-4.7-flash` | — | — | 1 | Quick responses |
| `glm-4.7-flashx` | — | — | 3 | Extended quick responses |
| `glm-4.6` | — | — | 3 | Previous gen |
| `glm-4.5` | — | — | 10 | High-concurrency tasks |
| `glm-4.5-air` | Haiku-level | — | 5 | Lightweight, cost-efficient |
| `glm-4.5-airx` | — | — | 5 | Extended Air |
| `glm-4.5-flash` | — | — | 2 | Budget option |
| `glm-4-plus` | — | — | 20 | Highest concurrency |
| `glm-4-32b-0414-128k` | — | 128K | 15 | Long-context tasks |

#### Vision Models
| Model | Context | Concurrency | Best For |
|-------|---------|-------------|----------|
| `glm-4.6v` | 128K | 10 | SOTA vision understanding, multimodal tool use |
| `glm-4.6v-flash` | — | 1 | Fast vision |
| `glm-4.6v-flashx` | — | 3 | Extended fast vision |
| `glm-4.5v` | — | 10 | Previous gen vision |

#### Image Generation Models
| Model | Concurrency | Best For |
|-------|-------------|----------|
| `glm-image` | 1 | Text-to-image, SOTA in complex scenarios |
| `cogview-4-250304` | 5 | Commercial posters, social media, portraits |

#### Video Generation Models
| Model | Concurrency | Best For |
|-------|-------------|----------|
| `cogvideox-3` | 1 | Text-to-video, improved stability/clarity |
| `viduq1-text` | 5 | Text-to-video |
| `viduq1-image` | 5 | Image-to-video |
| `viduq1-start-end` | 5 | Start/end frame video |
| `vidu2-image` | 5 | Image-to-video v2 |
| `vidu2-start-end` | 5 | Start/end frame v2 |
| `vidu2-reference` | 5 | Reference-guided video |

#### Specialized Models
| Model | Concurrency | Best For |
|-------|-------------|----------|
| `glm-ocr` | 2 | Document text extraction |
| `glm-asr-2512` | 5 | Real-time audio-video (ASR) |
| `autoglm-phone-multilingual` | 5 | Phone automation agent |

### Advanced API Features

#### Thinking Mode (3 types)
```python
# Enable thinking — model shows reasoning chain
extra_body={"thinking": {"type": "enabled"}}

# Preserved thinking — keeps reasoning across multi-turn
extra_body={"thinking": {"type": "enabled", "clear_thinking": False}}

# Disabled (default) — fastest, no reasoning overhead
extra_body={"thinking": {"type": "disabled"}}
```
- **Turn-level control:** Enable thinking for complex decisions, disable for quick tool calls
- **Interleaved thinking + tool calling:** Model reasons, calls tools, reasons about results

#### Function Calling
Full OpenAI-compatible tool definitions:
```python
tools = [{
    "type": "function",
    "function": {
        "name": "your_function",
        "description": "Description",
        "parameters": {"type": "object", "properties": {...}, "required": [...]}
    }
}]
response = client.chat.completions.create(
    model="glm-5", messages=[...], tools=tools, tool_choice="auto"
)
```

#### Other API Capabilities
- **Structured JSON Output:** Force JSON-formatted responses
- **Context Caching:** Automatic for repeated long contexts (cost savings)
- **Streaming:** Full `stream=True` support with `delta.reasoning_content`

---

## 3. What's Already Covered vs What's Unique

| z.AI Capability | Existing Antigravity Tool | Verdict |
|----------------|--------------------------|---------|
| UI screenshots → code | None | **🟢 UNIQUE — high value** |
| Error screenshot diagnosis | None | **🟢 UNIQUE — high value** |
| UI diff / visual regression | None | **🟢 UNIQUE — high value** |
| Video analysis | None | **🟢 UNIQUE** |
| Code/terminal OCR | Gemini `analyze_image` (generic) | **🟡 SPECIALIZED — z.AI is code-optimized** |
| Architecture diagram reading | Gemini `analyze_image` (generic) | **🟡 SPECIALIZED — z.AI is architecture-focused** |
| Chart/dashboard analysis | Gemini `analyze_image` (generic) | **🟡 SPECIALIZED — z.AI extracts data insights** |
| General image analysis | Gemini `analyze_image` | 🔴 Overlap |
| Web search | Gemini `gemini_chat` (grounding) | 🔴 Overlap (different engine) |
| Web reader | Antigravity `read_url_content` | 🔴 Overlap |
| GitHub repo analysis (Zread) | GitHub MCP `get_file_contents` | **🟡 DIFFERENT — Zread has knowledge search** |
| LLM chat completions | Claude (native), Gemini | 🔴 Overlap |
| Image generation | Gemini `generate_image` | 🔴 Overlap (diff model: CogView vs Gemini) |
| Video generation | None in Antigravity | **🟢 UNIQUE (API only)** |
| Thinking mode | Claude native reasoning | 🔴 Overlap |
| Function calling | Claude native tools | 🔴 Overlap |

---

## 4. Integration Plan for Sovereign OS

### What to Integrate via API (pay-as-you-go)

| Priority | Capability | Model | Why |
|----------|-----------|-------|-----|
| **High** | Alt LLM backbone for agents | `glm-5`, `glm-4.7` | Third brain alongside Ollama/Claude. Diversifies AI reasoning. |
| **High** | Image generation | `glm-image`, `cogview-4` | Alt image gen engine for social media, posters |
| **Medium** | Video generation | `cogvideox-3`, `vidu2-*` | Automated video content creation |
| **Medium** | OCR | `glm-ocr` | Document processing pipeline |
| **Low** | Audio/ASR | `glm-asr-2512` | Speech-to-text if needed |

### Where to Integrate in the OS

| File | What to Do |
|------|-----------|
| `config/settings.json` | Add z.AI provider: `api_key`, `base_url: https://api.z.ai/api/paas/v4/`, default model `glm-4.7` |
| Agent base classes | Add `zai` as provider option in LLM call routing (OpenAI SDK compatible) |
| `hlf/infinite_rag.py` | Consider z.AI as alt completion source for RAG queries |
| `.env` or env config | Add `ZAI_API_KEY=1fa442f2272e4dd8af7448dadc874f5a.HwB8cAZluy8tz0IS` |

### Minimal Integration Code
```python
from openai import OpenAI

client = OpenAI(
    api_key="1fa442f2272e4dd8af7448dadc874f5a.HwB8cAZluy8tz0IS",
    base_url="https://api.z.ai/api/paas/v4/"
)

# Basic completion
response = client.chat.completions.create(
    model="glm-5",
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
    extra_body={"thinking": {"type": "enabled"}}
)

# With function calling
response = client.chat.completions.create(
    model="glm-5",
    messages=[{"role": "user", "content": "..."}],
    tools=[...],
    tool_choice="auto"
)
```

---

## 5. Billing & Quota Summary

| Access Method | Billing | Quota |
|--------------|---------|-------|
| MCP Vision tools (Antigravity) | Coding Plan 5hr prompt pool | ~80 prompts/5hrs (Lite) |
| MCP Search + Reader + Zread | Coding Plan monthly MCP pool | 100 calls/month (Lite) |
| API calls (Sovereign OS) | Pay-as-you-go account balance | Per-token pricing |

**Important:** Coding Plan quota ≠ API quota. They are completely separate billing systems.

---

## 6. References

- API Reference: https://docs.z.ai/api-reference/introduction
- OpenAI SDK Migration: https://docs.z.ai/guides/develop/openai/python
- GLM-5 Guide: https://docs.z.ai/guides/llm/glm-5
- GLM-4.6V Vision: https://docs.z.ai/guides/vlm/glm-4.6v
- Function Calling: https://docs.z.ai/guides/capabilities/function-calling
- Thinking Mode: https://docs.z.ai/guides/capabilities/thinking-mode
- GLM-Image: https://docs.z.ai/guides/image/glm-image
- CogVideoX-3: https://docs.z.ai/guides/video/cogvideox-3
- Vision MCP: https://docs.z.ai/devpack/mcp/vision-mcp-server
- Web Search MCP: https://docs.z.ai/devpack/mcp/search-mcp-server
- Web Reader MCP: https://docs.z.ai/devpack/mcp/reader-mcp-server
- Zread MCP: https://docs.z.ai/devpack/mcp/zread-mcp-server
- Coding Tool Helper: https://docs.z.ai/devpack/extension/coding-tool-helper
- Usage Query Plugin: https://docs.z.ai/devpack/extension/usage-query-plugin
- Pricing: https://docs.z.ai/guides/overview/pricing
- Rate Limits: https://z.ai/manage-apikey/rate-limits
