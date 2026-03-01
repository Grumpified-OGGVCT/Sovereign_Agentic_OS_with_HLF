# Sovereign Agentic OS — Repository Scaffold Prompt

You are initializing `Agent_OS_with_HLF`. This is a Spec-Driven Development (SDD) project for a Sovereign Agentic OS with a custom DSL called HLF (Hieroglyphic Logic Framework). Scaffold the ENTIRE repo structure and populate all files. Follow these rules precisely.

## MODEL-AGNOSTIC POLICY (READ FIRST — MANDATORY)

This project uses **Ollama-first inference** with **OpenRouter as cloud fallback**.
The MoMA Router auto-selects from a **model matrix** defined in `config/settings.json`
per deployment tier. Users who fork this project may use ANY models.

### Absolute Rules
1. **NEVER hardcode specific model names** in code, docs, tests, or comments.
   - ❌ BAD: `model = "GPT-4o"`, `"use deepseek-v3"`, `"Claude can..."`, `"Llama-3..."`
   - ✅ GOOD: `settings["models"]["primary"]`, `$PRIMARY_MODEL`, `"the tier's model matrix"`
2. **Always reference models via config**: `PRIMARY_MODEL`, `REASONING_MODEL`,
   `SUMMARIZATION_MODEL`, `EMBEDDING_MODEL` from `.env` / `settings.json`.
3. **The allowed model matrix** is in `config/settings.json` → `ollama_allowed_models`.
   Never assume which models are available — always check the matrix.
4. **Cloud fallback**: Models with `:cloud` suffix route through OpenRouter.
   Local models route through Ollama. The router handles this automatically.

### Banned Patterns (CI will fail if these appear as project model references)
`GPT-4o`, `GPT-4`, `GPT-3.5`, `Claude`, `deepseek`, `Llama`, `Mistral`, `Gemini`, `Grok`,
`Anthropic`, `OpenAI` (as a model provider this project uses — comparison docs are OK).

## ANTI-DEVOLUTION RULES (CRITICAL)

Code quality must NEVER decrease. Every PR must satisfy:
1. **No test removals** — tests can only be added or strengthened, never deleted.
2. **No type hint removals** — all public functions must have type hints.
3. **No security bypass** — ALIGN Ledger rules are immutable at runtime.
4. **No hardcoded values** — all config flows through `.env` → `settings.json`.
5. **No memory budget violations** — services must stay under 4GB RAM.
6. **HLF intents must parse** — `hlfc.compile()` is the only valid parser, no raw regex.
7. **All PRs must pass CI** — `pytest`, `ruff`, `hlf_token_lint.py`, model whitelist.
8. **No import of banned modules** — `os.system`, `subprocess.call`, `eval()`, `exec()`.

## RULES
1. NO HALLUCINATION — do not invent tools, dirs, or libs not listed here.
2. Use `uv` for Python (pyproject.toml + uv.lock), `pnpm` for Node.js.
3. NEVER hardcode secrets/paths. Use `.env` + `pydantic.BaseSettings`.
4. No default ports (80/8080/5000). Exception: Ollama 11434.
5. Read `DEPLOYMENT_TIER` env var. Docker Compose profiles: `hearth`(home), `forge`(pro), `sovereign`(enterprise). Route conditionals through `config/settings.json`.

## STEP 1: ACFS Directory Tree — Create this exact layout:

```
.
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── README.md
├── pyproject.toml
├── acfs.manifest.yaml
├── bootstrap_all_in_one.sh
├── docker-compose.yml
├── Dockerfile.base
├── config/
│   ├── settings.json
│   └── redis.conf
├── security/
│   └── seccomp.json
├── agents/
│   ├── __init__.py
│   ├── gateway/
│   │   ├── __init__.py
│   │   ├── bus.py
│   │   ├── router.py
│   │   └── sentinel_gate.py
│   └── core/
│       ├── __init__.py
│       ├── main.py
│       ├── logger.py
│       ├── memory_scribe.py
│       ├── legacy_bridge.py
│       ├── ast_validator.py
│       ├── dream_state.py
│       ├── vault_decrypt.py
│       └── tool_forge.py
├── hlf/
│   ├── __init__.py
│   ├── hlfc.py
│   ├── hlffmt.py
│   └── hlflint.py
├── dapr/
│   ├── config.yaml
│   └── components/
│       ├── pubsub.yaml
│       ├── statestore.yaml
│       └── web_proxy.yaml
├── data/
│   ├── sqlite/.gitkeep
│   ├── cold_archive/.gitkeep
│   ├── quarantine_dumps/.gitkeep
│   └── align_staging/.gitkeep
├── governance/
│   ├── ALIGN_LEDGER.yaml
│   ├── host_functions.json
│   ├── hls.yaml
│   ├── service_contracts.yaml
│   ├── bytecode_spec.yaml
│   ├── dapr_grpc.proto
│   ├── module_import_rules.yaml
│   ├── openclaw_strategies.yaml
│   ├── kya_init.sh
│   └── templates/
│       ├── dictionary.json
│       └── system_prompt.txt
├── tests/
│   ├── conftest.py
│   ├── test_acfs.py
│   ├── test_hlf.py
│   ├── test_bootstrap.py
│   ├── test_memory.py
│   ├── test_policy.py
│   ├── test_grammar_roundtrip.py
│   ├── test_openclaw_summarize.py
│   └── fixtures/hello_world.hlf
├── scripts/
│   ├── hlf_token_lint.py
│   ├── ollama-audit.sh
│   ├── generate_tm_grammar.py
│   ├── verify_chain.py
│   └── build/parser-build.sh
├── tools/grammar-generator.py
├── syntaxes/hlf.tmLanguage.json
├── observability/
│   ├── openllmetry/.gitkeep
│   ├── openllmetry/last_hash.txt
│   └── snapshot_merkle.sh
├── ci/
│   ├── validate_models.yml
│   └── ensure-no-sandbox-in-prod.yml
├── docs/
│   ├── gen_from_spec.py
│   ├── openclaw_integration.md
│   └── getting_started.md
└── .github/workflows/
    ├── ci.yml
    ├── test-grammar.yml
    └── release.yml
```

## STEP 2: Core Configuration

### `.env.example`
```env
DEPLOYMENT_TIER=hearth
BASE_DIR=/app
OLLAMA_HOST=http://ollama-matrix:11434
PRIMARY_MODEL=qwen3-vl:32b-cloud
REASONING_MODEL=qwen-max
SUMMARIZATION_MODEL=qwen:7b
EMBEDDING_MODEL=nomic-embed-text
OLLAMA_NOAGENT=1
OLLAMA_ALLOWED_MODELS=qwen-7b
REDIS_URL=redis://redis-broker:6379/0
MAX_GAS_LIMIT=10
MAX_CONTEXT_TOKENS=8192
WEBSEARCH_RATELIMIT_CAPACITY=10
VAULT_ADDR=http://vault:8200
VAULT_ROLE_ID=
VAULT_SECRET_ID=
```

### `config/settings.json`
```json
{
  "deployment_tier": "hearth",
  "models": {
    "primary": "qwen3-vl:32b-cloud",
    "reasoning": "qwen-max",
    "summarization": "qwen:7b",
    "embedding": "nomic-embed-text"
  },
  "features": {
    "enable_mtls": false,
    "enable_honeypot": false,
    "enable_air_gap": false,
    "enable_merkle_logging": false,
    "enable_dreaming_state": true,
    "enable_ebpf": false,
    "enable_tpm": false,
    "enable_vault": false,
    "enable_worm": false
  },
  "gas_buckets": { "hearth": 1000, "forge": 10000, "sovereign": 100000 },
  "max_context_tokens": { "hearth": 8192, "forge": 16384, "sovereign": 32768 },
  "rate_limits": { "external_rpm": 50, "websearch_capacity": 10 },
  "circuit_breaker": { "timeout_ms": 12000, "dead_man_threshold": 3, "dead_man_window_sec": 300 },
  "semantic_cache": { "similarity_threshold": 0.99, "ttl_seconds": 3600, "min_confidence": 0.95 },
  "ollama_allowed_models": {
    "hearth": ["qwen-7b"],
    "forge": ["qwen-7b", "glm-5", "minimax-m2.5"],
    "sovereign": ["kimi-k2.5", "glm-5", "minimax-m2.5", "qwen-3.5", "qwen-7b"]
  }
}
```

### `config/redis.conf`
```
bind 0.0.0.0
port 6379
appendonly yes
appendfsync everysec
maxmemory 1gb
maxmemory-policy allkeys-lru
```

### `acfs.manifest.yaml`
```yaml
version: "1.0.0"
directories:
  - { path: "/data", permissions: "700" }
  - { path: "/data/sqlite", permissions: "700" }
  - { path: "/data/cold_archive", permissions: "700" }
  - { path: "/data/quarantine_dumps", permissions: "700" }
  - { path: "/governance", permissions: "555" }
  - { path: "/agents", permissions: "555" }
  - { path: "/security", permissions: "555" }
modules: {}
active_sha256_checksums:
  bootstrap_all_in_one.sh: ""
  security/seccomp.json: ""
  governance/ALIGN_LEDGER.yaml: ""
```

## STEP 3: Docker Infrastructure

### `Dockerfile.base`
```dockerfile
FROM python:3.12-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git openssl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN useradd -l -u 1000 -m -s /usr/sbin/nologin builder
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen
COPY . .
USER builder
ENTRYPOINT ["bash", "-c", "trap 'python -c \"from agents.core.main import quarantine_dump; quarantine_dump()\"' SIGUSR1; exec \"$@\"", "--"]
```

### `docker-compose.yml`
```yaml
version: "3.9"
networks:
  sovereign-net:
    driver: bridge
    internal: true
  external-net:
    driver: bridge

services:
  gateway-node:
    build: { context: ., dockerfile: Dockerfile.base }
    container_name: gateway-node
    command: ["python", "-m", "uvicorn", "agents.gateway.bus:app", "--host", "0.0.0.0", "--port", "40404"]
    ports: ["40404:40404"]
    networks: [sovereign-net, external-net]
    environment:
      - DEPLOYMENT_TIER=${DEPLOYMENT_TIER:-hearth}
    env_file: .env
    volumes:
      - ./config:/app/config:ro
      - ./governance:/app/governance:ro
    security_opt: ["seccomp=security/seccomp.json"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:40404/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits: { cpus: "2.0", memory: 4G }
    depends_on:
      redis-broker: { condition: service_healthy }

  agent-executor:
    build: { context: ., dockerfile: Dockerfile.base }
    container_name: agent-executor
    command: ["python", "-m", "agents.core.main"]
    networks: [sovereign-net]
    environment:
      - DEPLOYMENT_TIER=${DEPLOYMENT_TIER:-hearth}
    env_file: .env
    volumes:
      - ./agents/core:/app/agents/core:ro
      - ./governance:/app/governance:ro
      - ./data/quarantine_dumps:/app/data/quarantine_dumps
    security_opt: ["seccomp=security/seccomp.json"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:40405/ready"]
      interval: 15s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits: { cpus: "2.0", memory: 4G }
    depends_on:
      gateway-node: { condition: service_healthy }

  redis-broker:
    image: redis:7-alpine
    container_name: redis-broker
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
    networks: [sovereign-net]
    volumes:
      - redis-data:/data
      - ./config/redis.conf:/usr/local/etc/redis/redis.conf:ro
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

  memory-core:
    build: { context: ., dockerfile: Dockerfile.base }
    container_name: memory-core
    command: ["python", "-m", "agents.core.memory_scribe"]
    networks: [sovereign-net]
    volumes:
      - ./data/sqlite:/app/data/sqlite
      - ./data/cold_archive:/app/data/cold_archive
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:40406/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    depends_on:
      redis-broker: { condition: service_healthy }

  ollama-matrix:
    image: ollama/ollama:latest
    container_name: ollama-matrix
    networks: [sovereign-net, external-net]
    ports: ["11434:11434"]
    environment:
      - OLLAMA_NOAGENT=1
      - OLLAMA_ALLOWED_MODELS=${OLLAMA_ALLOWED_MODELS:-qwen-7b}
    volumes:
      - ollama-models:/root/.ollama
      - ./config:/app/config:ro
      - ./scripts/ollama-audit.sh:/usr/local/bin/ollama-audit.sh:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits: { cpus: "4.0", memory: 16G }

  docker-orchestrator:
    image: docker:23-dind-rootless
    container_name: docker-orchestrator
    networks: [sovereign-net]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./governance:/app/governance:ro
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:40407/health"]
      interval: 15s
      timeout: 5s
      retries: 3
    profiles: [forge, sovereign]

volumes:
  redis-data:
  ollama-models:
```

## STEP 4: Security

### `security/seccomp.json`
Standard seccomp profile: defaultAction SCMP_ACT_ERRNO. ALLOW standard syscalls (read, write, open, close, stat, fstat, mmap, mprotect, munmap, brk, socket, connect, sendto, recvfrom, bind, listen, accept, clone, fork, execve, exit, fcntl, openat, futex, epoll_*, getrandom, pipe2, dup). Explicitly DENY: ptrace, kexec_load, mount, umount2, pivot_root, reboot, sethostname, init_module, delete_module, acct. Architectures: x86_64 and aarch64.

## STEP 5: Governance Files

### `governance/ALIGN_LEDGER.yaml`
```yaml
version: "1.0-genesis"
rules:
  - { id: "R-001", name: "ACFS Confinement", regex_block: '(\/bin\/sh|\/bin\/bash|rm -rf \/|curl.*\| bash)', action: "DROP_AND_QUARANTINE" }
  - { id: "R-002", name: "Self-Modification Freeze", condition: "intent.targets.includes('sentinel_gate.py')", action: "ROUTE_TO_HUMAN_APPROVAL" }
  - { id: "R-003", name: "Docker Socket Block", regex_block: '(docker\.sock|/var/run/docker)', action: "DROP_AND_QUARANTINE" }
  - { id: "R-004", name: "Env Exfiltration Block", regex_block: '(\.env|VAULT_SECRET|API_KEY)', action: "DROP_AND_QUARANTINE" }
  - { id: "R-005", name: "Outbound Network Block", regex_block: '(curl|wget|nc |ncat)', action: "DROP" }
  - { id: "R-006", name: "Privilege Escalation Block", regex_block: '(sudo|su -|chmod 777)', action: "DROP_AND_QUARANTINE" }
  - { id: "R-007", name: "Process Injection Block", regex_block: '(os\.system|subprocess\.call|eval\(|exec\()', action: "DROP_AND_QUARANTINE" }
  - { id: "R-008", name: "Block raw OpenClaw keys", regex_block: 'openclaw:', action: "DROP" }
```

### `governance/host_functions.json`
```json
{
  "version": "1.0.0",
  "functions": [
    { "name": "READ", "args": [{"name":"path","type":"path"}], "returns": "string", "tier": ["hearth","forge","sovereign"], "gas": 1, "backend": "dapr_file_read", "sensitive": false },
    { "name": "WRITE", "args": [{"name":"path","type":"path"},{"name":"data","type":"string"}], "returns": "bool", "tier": ["hearth","forge","sovereign"], "gas": 2, "backend": "dapr_file_write", "sensitive": false },
    { "name": "SPAWN", "args": [{"name":"image","type":"string"},{"name":"env","type":"map"}], "returns": "string", "tier": ["forge","sovereign"], "gas": 5, "backend": "docker_orchestrator", "sensitive": false },
    { "name": "SLEEP", "args": [{"name":"ms","type":"int"}], "returns": "bool", "tier": ["hearth","forge","sovereign"], "gas": 0, "backend": "builtin", "sensitive": false },
    { "name": "HTTP_GET", "args": [{"name":"url","type":"string"}], "returns": "string", "tier": ["forge","sovereign"], "gas": 3, "backend": "dapr_http_proxy", "sensitive": false },
    { "name": "WEB_SEARCH", "args": [{"name":"query","type":"string"}], "returns": "string", "tier": ["forge","sovereign"], "gas": 5, "backend": "dapr_http_proxy", "sensitive": true },
    { "name": "OPENCLAW_SUMMARIZE", "args": [{"name":"path","type":"path"}], "returns": "string", "tier": ["forge","sovereign"], "gas": 7, "backend": "dapr_container_spawn", "sensitive": true, "binary_path": "/opt/openclaw/bin/openclaw_summarize", "binary_sha256": "" }
  ]
}
```

### `governance/templates/dictionary.json`
```json
{
  "version": "0.2.0",
  "token_map": {
    "->": ["cl100k", "sentencepiece", "qwen"],
    "Ω": ["cl100k", "sentencepiece", "qwen"],
    "fallback_terminator": "END"
  },
  "glyphs": {
    "Ω": {"name": "Terminal Conclusion", "enforces": "Final stdout state, terminates recursive loops"},
    "Δ": {"name": "State Diff", "enforces": "Only output delta changes"},
    "Ж": {"name": "Reasoning Blocker", "enforces": "Logical paradox; route to Arbiter"},
    "⩕": {"name": "Gas Metric", "enforces": "Max recursive steps before OOM"}
  },
  "tags": [
    { "name": "INTENT", "args": [{"name":"action","type":"string"},{"name":"target","type":"path"}] },
    { "name": "CONSTRAINT", "args": [{"name":"key","type":"string"},{"name":"value","type":"any"}] },
    { "name": "EXPECT", "args": [{"name":"outcome","type":"string"}] },
    { "name": "ACTION", "args": [{"name":"verb","type":"string"},{"name":"args","type":"any","repeat":true}] },
    { "name": "SET", "args": [{"name":"name","type":"identifier"},{"name":"value","type":"any"}], "immutable": true },
    { "name": "FUNCTION", "args": [{"name":"name","type":"identifier"},{"name":"args","type":"any","repeat":true}], "pure": true },
    { "name": "RESULT", "args": [{"name":"code","type":"int"},{"name":"message","type":"string"}], "terminator": true },
    { "name": "MODULE", "args": [{"name":"name","type":"identifier"}] },
    { "name": "IMPORT", "args": [{"name":"name","type":"identifier"}] },
    { "name": "DATA", "args": [{"name":"id","type":"string"}] }
  ]
}
```

### `governance/hls.yaml`
```yaml
version: "0.2.0"
tokens:
  LBRACK: "["
  RBRACK: "]"
  TERMINATOR: "Ω"
  ARROW: "->"
  COLON: ":"
  COMMA: ","
  ASSIGN: "="
  DOLLAR_L: "${"
  RBRACE: "}"
  STRING: /\"([^\"\\]|\\.)*\"/
  NUMBER: /-?\d+(\.\d+)?/
  BOOL: /true|false/
  LIST: /\[([^\]]*)\]/
  MAP: /\{([^}]*)\}/
  IDENT: /[A-Za-z_][A-Za-z0-9_]*/
rules:
  program: "statement+ TERMINATOR"
  statement: "tag_stmt | set_stmt | function_stmt | result_stmt"
  tag_stmt: "LBRACK TAG RBRACK arglist"
  set_stmt: "LBRACK 'SET' RBRACK IDENT ASSIGN literal"
  function_stmt: "LBRACK 'FUNCTION' RBRACK IDENT arglist"
  result_stmt: "LBRACK 'RESULT' RBRACK arglist"
  arglist: "literal (COMMA literal)* | EMPTY"
  literal: "STRING | NUMBER | BOOL | LIST | MAP | DOLLAR_L IDENT RBRACE"
  TAG: "/[A-Z_]+/"
```

### `governance/templates/system_prompt.txt`
Zero-Shot Grammar prompt: declare all allowed HLF tags ([INTENT], [CONSTRAINT], [EXPECT], [ACTION], [SET], [FUNCTION], [RESULT], [MODULE], [IMPORT]). Enforce: every message ends with Ω, first line must be [INTENT], one tag per line, no conversational prose, [HLF-v2] version prefix. Include 3-line example: `[INTENT] analyze /security/seccomp.json`, `[CONSTRAINT] mode="read-only"`, `[EXPECT] vulnerability_report`, `Ω`.

### `governance/openclaw_strategies.yaml`
Document Strategy B (whitelisted pure tools: SHA-256 verified binaries, gas 5-10, Tier 2/3, sensitive:true) and Strategy C (sandbox Docker Compose profile, OLLAMA_ALLOW_OPENCLAW=1 for dev only, resource caps memory=256M pids_limit=50 network=none, CI guard blocks production use).

## STEP 6: Python Source Files

### `agents/gateway/bus.py`
FastAPI app on port 40404. Endpoints: `POST /api/v1/intent` (dual-mode: accepts `{"text":"..."}` or `{"hlf":"..."}`), `GET /health` (returns 200). Middleware chain: (1) Token bucket rate limiter 50rpm via Redis INCR+EXPIRE, (2) HLF linter `validate_hlf()` from hlf/__init__.py (reject 422), (3) ALIGN enforcer `enforce_align()` from sentinel_gate.py (reject 403), (4) ULID nonce replay protection via Redis SETNX TTL=600s (reject 409 on duplicate). Parse validated HLF through hlfc.compile() to JSON AST. Stamp with request_id (ULID) and timestamp. Publish to Redis Stream "intents". All schemas via Pydantic BaseModel.

### `agents/gateway/router.py`
MoMA Router. Load config via pydantic.BaseSettings. Route by complexity: visual/OCR intent → qwen3-vl:32b-cloud, code/symbolic → qwen-max, simple → qwen:7b. Dynamic Model Downshifting: query Redis hash for similar past intents, if >95% solved by qwen:7b → downshift. VRAM Threshold Gate: query Ollama GET /api/ps, compute required VRAM, reject 429 if >80% (local models only — cloud models with `:cloud` suffix skip entirely). Circuit breaker: 12s timeout, fail-closed, log to OpenLLMetry. Gas middleware: verify_gas_limit() counts AST nodes, per-intent MAX_GAS_LIMIT=10 + per-tier Redis token bucket with Lua atomic decrement. Web Search Mediation: strip `web_search:true` from Ollama API calls, reroute through WEB_SEARCH host function via Dapr.

### `agents/gateway/sentinel_gate.py`
Load ALIGN_LEDGER.yaml at startup, compile all regex_block patterns. `enforce_align(payload: str) -> tuple[bool, str]` scans HLF body against compiled patterns, returns (blocked, rule_id). `LLM_Judge` class evaluates proposed file diffs against ALIGN constraints before committing. Deterministic non-LLM gate.

### `agents/core/main.py`
Agent executor entrypoint. Init OpenLLMetry TracerProvider BEFORE other imports. Register SIGUSR1 handler that dumps active memory + last 5000 traces to data/quarantine_dumps/. Connect to Dapr sidecar for pub/sub. Consumer group on Redis Stream "intents". Process each intent: parse HLF → validate → execute against host functions → return [RESULT].

### `agents/core/logger.py`
ALS (Agentic Log Standard) logger. Every log is JSON: trace_id (SHA-256 hash including parent), parent_trace_hash, timestamp (ISO-8601), goal_id, agent_role, confidence_score (0.0-1.0), anomaly_score (0.0-1.0), token_cost. Merkle chain: each trace_id = SHA-256(parent_hash + payload). Read/write `observability/openllmetry/last_hash.txt`. Seed with 64 zeros if missing. Semantic Outlier Trap: if anomaly_score > 0.85, fire webhook to sentinel_gate for quarantine.

### `agents/core/memory_scribe.py`
Async SQLite writer. Single-threaded Redis XREADGROUP consumer. Initialize SQLite with WAL mode (PRAGMA journal_mode=WAL). Three tables: identity_core (id, directive_hash, immutable_constraint_blob), rolling_context (session_id, timestamp, fifo_blob, token_count), fact_store (entity_id, vector_embedding FLOAT32[768], semantic_relationship, confidence_score). Install sqlite-vec extension for HNSW vector index. SHA-256 embedding cache: hash raw text, check Redis, bypass ML model if cached. Vector race protection: >0.98 cosine similarity within 5s → UPDATE (merge confidence) not INSERT. Fractal summarization: use qwen:7b for map-reduce to 1500 tokens before injecting context. Dead Letter Queue: after 3 failures, move to memory_scribe_dlq for human review.

### `agents/core/legacy_bridge.py`
`decompress_hlf_to_rest(hlf_payload: str) -> dict` — parse HLF tags, map [INTENT] to action/target, [CONSTRAINT] to body params, stop at Ω. Wrap in try/except, return error dict on failure.

### `agents/core/ast_validator.py`
Use Python `ast` module to scan generated code. Reject if AST contains calls to os.system, subprocess.*, eval(), exec(). Return bool + violation details.

### `agents/core/dream_state.py`
Cron job at 03:00. Compress day's Rolling_Context via map-reduce summarization. DSPy regression: test new rules against past intents, merge to Fact_Store only if tokens decrease AND success rate = 1.0. Auto-Immune: extract Honeypot attack payloads as negative DSPy constraints to harden ALIGN. Log truncation: traces >7 days → compress to .parquet in cold_archive, delete raw JSON.

### `agents/core/tool_forge.py`
If agent loops 3x on same task due to missing utility: auto-generate Python script + pytest test → run through LLM_Judge → if passes, register tool dynamically to router API.

## STEP 7: HLF Toolkit

### `hlf/__init__.py`
`validate_hlf(line: str) -> bool` — regex `^\s*\[[A-Z_]+\]` check. Export for bus.py middleware.

### `hlf/hlfc.py`
Lark LALR(1) compiler. Load hls.yaml as grammar, dictionary.json for tag signatures. Parse .hlf → validate typed tag signatures (arity + type checking) → emit JSON AST dict `{"version": "0.2.0", "program": [...]}`. HLFTransformer class handles tag_stmt, set_stmt (immutable check), function_stmt (pure check), result_stmt. CLI: `hlfc input.hlf [output.json]`.

### `hlf/hlffmt.py`
Canonical formatter. Uses hlfc.compile() to get AST, then pretty-prints: uppercase tags, single space after ], mandatory trailing Ω, no trailing spaces. Supports `--in-place` flag.

### `hlf/hlflint.py`
Static analyzer using tiktoken cl100k_base. Checks: unused variables (SET without ${} reference), gas budget (AST node count vs MAX_GAS_LIMIT), recursion depth, per-intent token count (fail if >30 tokens). Returns list of diagnostic messages.

## STEP 8: Tests

### `tests/fixtures/hello_world.hlf`
```
[HLF-v2]
[INTENT] greet "world"
[EXPECT] "Hello, world!"
[RESULT] code=0 message="ok"
Ω
```

- **test_acfs.py**: Verify all ACFS dirs exist. Check permissions match acfs.manifest.yaml. Validate manifest YAML schema.
- **test_hlf.py**: HLF syntax validation (valid/invalid), serialization round-trip, tag arity checking, malformed intent rejection (→422).
- **test_bootstrap.py**: Minimal compose stack healthchecks (mock or subprocess).
- **test_memory.py**: SQLite insert with FLOAT32[768] vector column, WAL mode verification, sqlite-vec extension load.
- **test_policy.py**: Synthetic intent replay matrix — valid→202, malformed→422, ALIGN-blocked→403, gas-exhausted→429, replayed nonce→409.
- **test_grammar_roundtrip.py**: Parse .hlf→JSON AST→hlffmt pretty-print→re-parse→assert structural equality of both ASTs.
- **test_openclaw_summarize.py**: Validate OPENCLAW_SUMMARIZE in host_functions.json: exists, gas 5-10, sensitive=true, binary_sha256 field.

## STEP 9: CI/CD

### `.github/workflows/ci.yml`
Jobs: (1) `ensure-no-sandbox-in-prod` — grep docker-compose.yml for OLLAMA_ALLOW_OPENCLAW=1, fail if found. (2) `lint-and-test` (needs: ensure-no-sandbox-in-prod) — checkout, setup uv, install frozen, run hlf_token_lint.py, pre-commit run --all-files, pytest tests/ -v. (3) `validate-models` — python script validates settings.json has hearth/forge/sovereign model whitelists.

### `.github/workflows/test-grammar.yml`
Triggered on changes to hls.yaml, dictionary.json, hlf/**. Build parser via parser-build.sh, then run pytest test_grammar_roundtrip.py.

### `.github/workflows/release.yml`
On tag push: build Docker images, sign with cosign (Sigstore), generate SBOM via syft, publish MkDocs docs.

## STEP 10: pyproject.toml
```toml
[project]
name = "sovereign-os"
version = "0.1.0"
description = "Sovereign Agentic OS with HLF"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0", "uvicorn[standard]>=0.30.0",
    "pydantic>=2.9.0", "pydantic-settings>=2.5.0",
    "redis>=5.2.0", "dspy-ai>=2.5.0", "lark>=1.2.0",
    "tiktoken>=0.8.0", "ulid-py>=1.1.0", "httpx>=0.27.0",
    "traceloop-sdk>=0.30.0", "alembic>=1.13.0",
    "sqlalchemy>=2.0.0", "typer>=0.12.0", "docker>=7.1.0",
]
[project.optional-dependencies]
dev = ["pytest>=8.3.0", "pytest-asyncio>=0.24.0", "pre-commit>=4.0.0", "ruff>=0.7.0"]
[project.scripts]
hlfc = "hlf.hlfc:main"
hlffmt = "hlf.hlffmt:main"
hlflint = "hlf.hlflint:main"
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
[tool.ruff]
target-version = "py312"
line-length = 120
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

## STEP 11: Bootstrap & Support Scripts

### `bootstrap_all_in_one.sh`
Genesis Block boot sequence: (1) Check Docker daemon, (2) Run pytest, (3) Generate KYA certs via governance/kya_init.sh, (4) Run alembic upgrade head for SQLite migrations, (5) Docker compose build, (6) Docker compose up -d with DEPLOYMENT_TIER profile, (7) Wait for healthchecks, (8) Verify Ollama models via API. Output: `[SOVEREIGN OS GENESIS BLOCK INITIALIZED. AWAITING INTENT.]`. Include shutdown_sequence() trap on EXIT/SIGTERM: drain Redis streams, SQLite WAL checkpoint (PRAGMA wal_checkpoint(TRUNCATE)), final ALS trace, docker compose down.

### `observability/openllmetry/last_hash.txt`
```
0000000000000000000000000000000000000000000000000000000000000000
```

### `scripts/hlf_token_lint.py`
Scan all .hlf and system prompt files. Use tiktoken cl100k_base. Exit 1 if any intent >30 tokens.

### `scripts/ollama-audit.sh`
Every 30s: GET /api/show per model, export num_ctx as Prometheus gauge. Skip :cloud suffix models. Warn if local model exceeds MAX_CONTEXT_TOKENS.

### `scripts/build/parser-build.sh`
Pre-compile governance/hls.yaml via Lark into serialized Python parser module for consistent parsing.

## STEP 12: README.md
Professional README: title "Sovereign Agentic OS with HLF". Mermaid architecture diagram (Gateway→ASB→MoMA Router→Agent Executor→Memory Core, with Redis, Ollama, Docker Orchestrator). Quick start: `cp .env.example .env && bash bootstrap_all_in_one.sh`. Three deployment tiers table. HLF overview with example. Security features bullet list. Tech stack: Python 3.12, FastAPI, Redis, SQLite, Docker, Dapr, Ollama, DSPy, Lark.

## CRITICAL BUILD NOTES
1. **NO docker.sock on agent-executor.** Only docker-orchestrator mounts it.
2. **OLLAMA_NOAGENT=1** mandatory in all production profiles.
3. **Cloud models (`:cloud` suffix)** skip VRAM/context checks entirely.
4. **`sensitive: true` host functions** have returns SHA-256 hashed before Merkle log.
5. **ALIGN Ledger is immutable** at runtime. governance/ mounted :ro in all containers.
6. **Gas = AST node count**, not raw tokens.
7. **Phase 5 (language evolution) and GUI (Phase 5.4)** are post-Genesis — not scaffolded beyond placeholders.
8. **Dapr mTLS** enforced on Tier 2/3.
9. Focus on **Phases 1-4** for initial build.
