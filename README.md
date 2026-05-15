# java-codebase-rag

A graph-native code intelligence layer for Java microservice estates, exposed to LLM agents via the **Model Context Protocol (MCP)**.

The system extracts a deterministic property graph from Java source (tree-sitter), stores it in **Kuzu** (graph) alongside a **LanceDB** vector index (chunks), and exposes a deliberately small MCP surface — **five tools**: `search`, `find`, `describe`, `neighbors`, `resolve` — that collapse onto three primitive agent operations: **locate**, **inspect**, **walk**.

> **What this MCP is:** a **GPS for code navigation**, not a reasoning engine.
> Agents use a simple loop:
>
> 1. **Locate** entry nodes (`search` / `find`, or identifier-shaped **`resolve`**)
> 2. **Inspect** what a node is (`describe`)
> 3. **Walk** one hop at a time (`neighbors`) until enough evidence is gathered
>
> The MCP exposes structure and adjacency; the agent owns multi-hop reasoning and stop conditions.

For the design rationale, the GPS metaphor, and the full ontology, see [`docs/paper/paper.pdf`](./docs/paper/paper.pdf) (architecture report).

> **Stability disclaimer.** This repo does **not** promise backward compatibility. MCP tool contracts, env vars, Lance/Kuzu schemas, config files, and Python APIs may change without a deprecation period. Track `main` and rebuild indexes when ontology or embedding settings change (see [§6 Graph layer](#6-graph-layer)).

---

## Contents

1. [Install](#1-install)
2. [Environment variables](#2-environment-variables)
3. [MCP host setup](#3-mcp-host-setup) — Claude Code, Claude Desktop
4. [MCP tool reference](#4-mcp-tool-reference)
5. [CLI reference (`java-codebase-rag`)](#5-cli-reference-java-codebase-rag)
6. [Graph layer](#6-graph-layer) — Kuzu schema, edges, capabilities, ranking
7. [Brownfield overrides](#7-brownfield-overrides) — config + in-source annotations
8. [Ignore patterns](#8-ignore-patterns)
9. [Further reading](#9-further-reading)

---

## 1. Install

```bash
cd /path/to/java-codebase-rag
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

- **Python 3.11+** required.
- **Embedding model** must match what the index was built with (default `sentence-transformers/all-MiniLM-L6-v2`).
- The `cocoindex` package is **only** needed for lifecycle commands that run the indexer (`init`, `increment`, `reprocess`, and `erase`). Search and MCP navigation work without it.

For the assumptions this MCP makes about your Java repo (annotations, DI patterns, naming) and a per-file map of where to edit if you can't refactor your codebase to match, see [`CODEBASE_REQUIREMENTS.md`](./CODEBASE_REQUIREMENTS.md).

---

## 2. Environment variables

The operator-facing surface is **five** variables (plus MCP-only `JAVA_CODEBASE_RAG_SOURCE_ROOT` below). Precedence for knobs that also exist as CLI flags or YAML entries is **CLI flag > env var > YAML > built-in default** (see [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md)).

| Variable | Purpose |
|---|---|
| `JAVA_CODEBASE_RAG_INDEX_DIR` | Local filesystem **directory** for Lance tables, the Kuzu file `code_graph.kuzu`, and cocoindex state (`cocoindex.db`). Not a `lancedb://` or cloud URI — use a path. Default: `./.java-codebase-rag/` under the resolved Java tree root. |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. Overridable via `.java-codebase-rag.yml` `embedding.model` and `--embedding-model`. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. Overridable via YAML `embedding.device` and `--embedding-device`. |
| `JAVA_CODEBASE_RAG_DEBUG_CONTEXT` | When truthy, verbose stderr logging for chunk context expansion (diagnostics only). |
| `JAVA_CODEBASE_RAG_RUN_HEAVY` | Test gate: set to `1` / `true` / `yes` to run the slow cocoindex + Lance end-to-end test (`pytest`); not used in normal operator workflows. |

**MCP host launchers** also set `JAVA_CODEBASE_RAG_SOURCE_ROOT` to the Java repository root when it differs from the server process cwd (see `mcp.json.example`).

Only the names in the table above (plus `JAVA_CODEBASE_RAG_SOURCE_ROOT` for MCP hosts) are read as configuration. Project config belongs in **`.java-codebase-rag.yml`** (or `.yaml`).

**Paths and conventions** (for scripts and operators):

- **`JAVA_CODEBASE_RAG_INDEX_DIR`** — filesystem path to the index directory (not a URI). Lance opens this directory; Kuzu is always `<index-dir>/code_graph.kuzu`; cocoindex keeps **`cocoindex.db`** next to them.
- **Java tree root** — CLI: `--source-root` (else cwd). MCP stdio: set `JAVA_CODEBASE_RAG_SOURCE_ROOT` when the Java repo root differs from the server process cwd.
- **`microservice_roots`** — configure only under **`microservice_roots:`** in `.java-codebase-rag.yml` (or `.yaml`).
- **Chunk context diagnostics / heavy tests** — `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`, `JAVA_CODEBASE_RAG_RUN_HEAVY` (see the table above).

Python package: **`java_codebase_rag`** (`python -m java_codebase_rag.cli`).

### Project YAML reference (`.java-codebase-rag.yml`)

A single file at the project root (the directory you pass as `--source-root`, or cwd) holds everything that isn't an environment variable. The two accepted filenames are `.java-codebase-rag.yml` and `.java-codebase-rag.yaml`; if both exist, `.yml` wins.

**All keys are optional.** A project with no YAML at all uses built-in defaults plus env vars. Add only the keys you need.

```yaml
# .java-codebase-rag.yml — full reference, every key annotated.
# Place at the project root (same directory you pass as --source-root).

# -------- Core knobs (mirror env vars; precedence: CLI > env > YAML > default) --------

# Index directory: where Lance tables, code_graph.kuzu, and cocoindex.db live.
# - Tilde (`~`) is expanded; `$VAR` is NOT (use absolute paths or `~`).
# - Relative paths resolve against source_root, not cwd.
# - Env: JAVA_CODEBASE_RAG_INDEX_DIR. CLI: --index-dir. Default: ./.java-codebase-rag/
index_dir: ./.java-codebase-rag

# Embedding configuration. Must match between indexer and reader — if you change
# `embedding.model`, rebuild the index (`java-codebase-rag reprocess`).
embedding:
  # Hub id OR local directory containing the sentence-transformers model files.
  # - Hub id example: `sentence-transformers/all-MiniLM-L6-v2`
  # - Local path examples: `/opt/models/minilm`, `~/models/minilm`, `$MODEL_DIR/minilm`
  # - Resolution applies expanduser + expandvars when the value is path-shaped
  #   (starts with `/`, `./`, `../`, `~`, or contains `$`). Same rule for
  #   `SBERT_MODEL` and `--embedding-model` after precedence picks the string.
  #   Plain `org/name` is treated as a hub id and passed through unchanged.
  #   A relative path without `./` (e.g. `models/minilm`) is ambiguous with
  #   hub-id shape — prepend `./` if you mean a local directory.
  # - Env: SBERT_MODEL. CLI: --embedding-model. Default: sentence-transformers/all-MiniLM-L6-v2
  model: sentence-transformers/all-MiniLM-L6-v2

  # Optional. One of: cpu, cuda, mps, cuda:0, cuda:1, ...
  # When omitted, sentence-transformers picks automatically.
  # Env: SBERT_DEVICE. CLI: --embedding-device.
  device: cpu

# -------- Microservice layout --------

# Explicit microservice roots, relative to source_root. When set, takes priority
# over auto-detection (build markers + outermost source-set folding).
# Each entry is a directory NAME (no leading slash, no `~`). See §7 for the
# auto-detection fallback and the diagnose-microservice CLI verb.
microservice_roots:
  - chat-core
  - chat-orchestrator
  - ranking

# -------- Cross-service edge resolution --------

# How the resolver treats auto-detected cross-service call edges. See §7.2.
# - auto             (default): promote auto-detected callers to cross_service when a route matches.
# - brownfield_only           : only edges where both ends come from brownfield annotations or YAML
#                               stay cross_service; everything else becomes `unresolved`.
cross_service_resolution: auto

# -------- Brownfield overrides (see §7 for full schema and semantics) --------

# Roles & capabilities for custom stereotypes the indexer can't recognise.
role_overrides:
  annotations:
    AcmeService: SERVICE
    CompanyController: CONTROLLER
  capabilities:
    CompanyKafkaTopic: [MESSAGE_LISTENER]
  fqn:
    com.legacy.OrderProcessor:
      role: SERVICE
      capabilities: [MESSAGE_LISTENER]

# Server-side route declarations for endpoints the framework introspector can't see.
route_overrides:
  annotations:
    ann.AcmeRoute:
      framework: spring_mvc
      kind: http_endpoint
      method: GET
      path: /acme
  fqn:
    com.legacy.UserApi:
      framework: spring_mvc
      kind: http_endpoint
      path: /legacy/users

# Caller-side HTTP client overrides (RestTemplate/WebClient wrappers, custom Feign-likes).
http_client_overrides:
  annotations:
    ann.LegacyHttpClient:
      client_kind: rest_template
      target_service: chat-core
      path: /chat/joinOperator
      method: POST
  fqn:
    com.legacy.ChatClient:
      client_kind: feign_method
      target_service: chat-core

# Caller-side async producer overrides (Kafka/RabbitMQ event publishers).
async_producer_overrides:
  annotations:
    ann.LegacyEvent:
      client_kind: kafka_send
      topic: chat.follow-up
      broker: ""
  fqn:
    com.legacy.EventBus:
      client_kind: kafka_send
      topic: chat.follow-up
```

**Path expansion (what gets `~` / `$VAR` treatment):**

| Field | Expanded? | Notes |
|---|---|---|
| `index_dir` | partial | `~` expanded; `$VAR` is NOT expanded. Relative paths resolve against `source_root`. |
| `embedding.model` (when path-shaped) | yes | Path-shape = starts with `/`, `./`, `../`, `~`, or contains `$`. Plain `org/name` is treated as a hub id and passed through. Applies to the value after CLI > env > YAML > default precedence. Long-lived MCP hosts also apply the same expansion when reading `SBERT_MODEL` from the process environment (so table metadata and search agree with `index_common` defaults). |
| `embedding.device` | n/a | Device strings (`cpu`, `cuda`, `mps`) aren't paths. |
| `microservice_roots[*]` | no | Each entry is a directory **name** relative to `source_root`, not an arbitrary path. |
| Brownfield `path:` / `topic:` values | no | These are URL paths and Kafka topic names, not filesystem paths. Literal characters preserved. |

**Tips & gotchas:**

- **The file must be at `source_root`**, not in `$HOME`. The MCP server reads `JAVA_CODEBASE_RAG_SOURCE_ROOT` to find it; the CLI uses `--source-root` (else cwd).
- **Don't commit secrets** into this YAML — it sits next to your source tree and is read by every operator who clones it.
- **Rebuild after editing brownfield overrides.** Run a full `java-codebase-rag reprocess` (no flags) so Lance and Kuzu stay coherent, or use `--graph-only` / `--vectors-only` when you know only one store needs invalidation. Editing `embedding.model` requires a vector rebuild (`reprocess` or `--vectors-only`).
- **Diagnose what's loaded.** `java-codebase-rag meta` prints the resolved config and each value's `*_source` (`cli` / `env` / `yaml` / `default`) — see `embedding_model_source`, `embedding_device_source`, `index_dir_source`.
- **`embedding.model` and `$` in directory names.** `expandvars` treats `$VAR` / `${VAR}` like the shell. HuggingFace hub ids never contain `$`. If a local filesystem path contains a literal `$` in a directory name, use an absolute path that avoids `$`-expansion patterns, or expect `expandvars` to interpret `$` sequences.

Deeper documentation for the brownfield blocks (`role_overrides`, `route_overrides`, `http_client_overrides`, `async_producer_overrides`, `cross_service_resolution`) lives in [§7 Brownfield overrides](#7-brownfield-overrides).

---

## 3. MCP host setup

### Claude Code

**Project scope:** copy `mcp.json.example` to your repo as `.mcp.json`, replace absolute paths, and merge with any existing `mcpServers`.

**Or via CLI:**

```bash
claude mcp add --transport stdio java-codebase-rag -- \
  /path/to/java-codebase-rag/.venv/bin/python \
  /path/to/java-codebase-rag/server.py
```

Set env vars (`JAVA_CODEBASE_RAG_INDEX_DIR`, `JAVA_CODEBASE_RAG_SOURCE_ROOT`, `SBERT_MODEL`, …) in `.mcp.json` or your shell profile. Official docs: [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings).

### Claude Desktop

Edit `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) and add an entry under `mcpServers` with the same `command`, `args`, and `env` as in `mcp.json.example`.

### Driving the MCP from an agent

- **[`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md)** — copy-paste into `QWEN.md` / `CLAUDE.md` / `AGENTS.md`. Covers the five MCP tools, the shared `NodeFilter`, the edge-type taxonomy, required `neighbors` arguments, the ontology glossary (currently **v13**), the recovery playbook, and slash-style aliases.
- **[`docs/skills/java-codebase-explore.md`](./docs/skills/java-codebase-explore.md)** — exploration **strategy** (missions, fallbacks, anti-capabilities, stopping rules); AGENT-GUIDE remains the **operating manual** for tool shapes and recovery.
- **[`docs/MANUAL-VERIFICATION-CHECKLIST.md`](./docs/MANUAL-VERIFICATION-CHECKLIST.md)** — 7-phase agent-driven verification you run after indexing your real project. Each item has a copy-paste prompt and calibration data from `tests/bank-chat-system`.
- **[`automation/cursor_propose_only/README.md`](./automation/cursor_propose_only/README.md)** — optional proposal orchestration workflow (single-command autopilot, planning bundles, and automated execution/review loops).

---

## 4. MCP tool reference

| Tool | Purpose | Args | Example |
|---|---|---|---|
| `search` | Locate nodes by NL/code text. | `query: str`, `table: str="java"`, `hybrid: bool=False`, `limit: int=5`, `offset: int=0`, `path_contains: str \| None`, `filter: NodeFilter \| str \| None` | `{"query":"join operator flow","limit":5}` |
| `find` | Locate nodes by structured filter. | `kind: "symbol"\|"route"\|"client"`, `filter: NodeFilter \| str`, `limit: int=25`, `offset: int=0` | `{"kind":"symbol","filter":{"role":"CONTROLLER"}}` |
| `describe` | Full record + edge counts for one node. For **type** symbols, `edge_summary` may include composed dot-keys (`DECLARES.DECLARES_CLIENT`, `DECLARES.EXPOSES`); for **method** symbols it may include override-axis virtual keys (`OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.EXPOSES`, `OVERRIDES` rollup). Stored `OVERRIDES` edges are also counted like other rel labels. See [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md) (`describe`). | `id: str` | `{"id":"sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)"}` |
| `resolve` | Identifier-shaped node lookup (symbol / route / client). Returns `status` `one`, `many`, or `none`; prefer over `describe(fqn=…)` when an FQN may collide. See [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md) (`resolve`). | `identifier: str`, `hint_kind: "symbol"|"route"|"client" \| null` | `{"identifier":"com.bank.chat.core.api.ChatController","hint_kind":"symbol"}` |
| `neighbors` | One-hop walk. **Required**: `direction` and `edge_types`. | `ids: str \| list[str]`, `direction: "in"\|"out"`, `edge_types: list[str]`, `limit: int=25`, `offset: int=0`, `filter: NodeFilter \| str \| None` | `{"ids":"route:chat-core:POST:/chat/joinOperator","direction":"in","edge_types":["HTTP_CALLS","ASYNC_CALLS"]}` |

**`NodeFilter` notes:**

- `filter` is a JSON object matching the `NodeFilter` schema. Wire types are `object` or, as a fallback, a JSON-encoded string for clients that flatten objects.
- Unknown filter keys and populated fields that are not applicable to the effective node kind fail loudly with `success=false` and a teaching `message` (no silent key dropping).
- For `neighbors`, mixed-kind neighborhoods fail on the first evaluated neighbor row whose kind makes populated filter fields inapplicable.
- Symbol-only keys: `symbol_kind` (single value) and `symbol_kinds` (set membership) for declaration granularity (`class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`).
- `find(kind="symbol", ...)` results include `symbol_kind` so callers can see declaration granularity without a follow-up `describe`.
- For `find`, an empty / whitespace-only filter string or the JSON literal `null` is treated like `{}` (match anything).

Example:

```json
{"kind":"symbol","filter":{"microservice":"chat-core","symbol_kind":"interface"}}
```

---

## 5. CLI reference (`java-codebase-rag`)

Operator playbook with workflows, exit codes, and env alignment: [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md).

Run `java-codebase-rag --help` to list grouped subcommands (lifecycle / introspection / analysis). Output mode is automatic: JSON when piped, pretty text in a TTY. Module entrypoint: `python -m java_codebase_rag.cli`. Lifecycle commands (`init`, `increment`, `reprocess`, `erase`) stream subprocess progress to **stderr** (including any child stdout the tool relays); **`--quiet`** suppresses that human channel; **stdout** remains the machine-readable contract (JSON or pprint).

Shared flags on all subcommands: `--source-root`, `--index-dir`, `--embedding-model`, `--embedding-device` (each optional; see the CLI guide for precedence).

| Group | Subcommand | Role |
|---|---|---|
| Lifecycle | `init` | First-time index; refuses if the index dir already has artifacts. |
| Lifecycle | `increment` | CocoIndex catch-up (Lance only); prints a stderr warning that Kuzu is unchanged until `reprocess`. |
| Lifecycle | `reprocess` | Default: full Lance reprocess + full Kuzu rebuild. Optional `--vectors-only` / `--graph-only` (mutually exclusive) for a single phase. |
| Lifecycle | `erase` | Deletes index artifacts; requires `--yes` or interactive TTY confirm. |
| Introspection | `meta`, `tables`, `diagnose-ignore` | Health, table listing, ignore-layer diagnostics. |
| Analysis | `analyze-pr` | Blast-radius / risk from a unified diff. |

The hidden alias **`refresh`** invokes **`reprocess`** (prefer **`reprocess`** in new scripts).

Examples:

```bash
java-codebase-rag init --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
java-codebase-rag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
java-codebase-rag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | .venv/bin/python -c "import json,sys; print(json.loads(sys.stdin.read())['edge_counts'])"
java-codebase-rag diagnose-ignore .git/HEAD --source-root /path/to/java/repo
java-codebase-rag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

### `analyze-pr` output shape

Pass the same unified diff text you would feed to `patch` (e.g. `git diff` output). Paths in the diff should match project-relative `Symbol.filename` values in the graph (e.g. `chat-assign/src/main/java/.../ChatManagementService.java`). A one-line edit returns:

```json
{
  "success": true,
  "changed_symbols": [
    {
      "symbol_id": "<opaque>",
      "fqn": "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)",
      "kind": "method",
      "change_type": "modified",
      "file": "chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java",
      "hunk_lines": [48, 49, 50, 51, 52]
    }
  ],
  "blast_radius_total": 2,
  "blast_radius_by_symbol": { "<opaque>": 1 },
  "cross_service_callers": 0,
  "routes_touched": [],
  "risk_score": 0.008,
  "risk_band": "low",
  "notes": []
}
```

### Manual search

`--model` defaults from `SBERT_MODEL` (same path-shaped `~` / `$VAR` expansion as MCP and `java-codebase-rag` config). Omit `--model` to use the env default; pass a hub id or local path explicitly when needed.

```bash
# Vector
JAVA_CODEBASE_RAG_INDEX_DIR=/path/to/.java-codebase-rag .venv/bin/python search_lancedb.py "rate limit" --table java --limit 2

# Graph-expanded (requires the Kuzu DB to exist)
JAVA_CODEBASE_RAG_INDEX_DIR=/path/to/.java-codebase-rag .venv/bin/python search_lancedb.py "rate limit" \
  --table java --limit 5 --graph-expand --expand-depth 2

# Role-filtered
JAVA_CODEBASE_RAG_INDEX_DIR=/path/to/.java-codebase-rag .venv/bin/python search_lancedb.py "place order" --table java --role CONTROLLER

# With surrounding context (1 chunk before + 1 chunk after)
JAVA_CODEBASE_RAG_INDEX_DIR=/path/to/.java-codebase-rag .venv/bin/python search_lancedb.py "chat assignment" \
  --table java --limit 3 --context-neighbors 1
```

### Building the graph standalone

`java-codebase-rag reprocess` (default, no flags) runs `cocoindex update` with a full reprocess flag, then invokes `build_ast_graph.py` to rebuild Kuzu under the resolved index directory. For a **graph-only** rebuild from the CLI, prefer `java-codebase-rag reprocess --graph-only` (see [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md)). To invoke the graph builder directly:

```bash
# Scan the current working directory
.venv/bin/python build_ast_graph.py --verbose

# Or point at a specific repo root and graph path
.venv/bin/python build_ast_graph.py --source-root /path/to/repo --kuzu-path /path/to/.java-codebase-rag/code_graph.kuzu --verbose
```

If `--source-root` is omitted, the current working directory is used. The MCP server resolves the Java tree from `JAVA_CODEBASE_RAG_SOURCE_ROOT` when set, otherwise cwd.

For `reprocess`, the pipeline runs `cocoindex` with `cwd` set to the bundle directory (so Python imports resolve), but passes the resolved Java tree root and index dir to the subprocess so indexing targets your project. The Kuzu DB is dropped and rebuilt from scratch on each full reprocess; graph-side incremental rebuilds are future work ([`propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`](./propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md)).

---

## 6. Graph layer

A deterministic property graph derived from tree-sitter Java parsing lives next to the LanceDB tables under the index directory (default `${JAVA_CODEBASE_RAG_INDEX_DIR:-./.java-codebase-rag}/code_graph.kuzu`). Current ontology version: **13**.

### Node kinds

| Kind | Examples |
|---|---|
| `Symbol` | `package`, `file`, `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor` |
| `Route` | HTTP endpoint or async listener (one row per declared route) |
| `Client` | Outbound HTTP / messaging call site |

Unresolved targets become **phantom** nodes (`resolved=false`, FQN guessed from imports / `java.lang`).

### Edge types (10)

| Edge | Direction | Meaning |
|---|---|---|
| `EXTENDS` | type → type | Class- or interface-inheritance. |
| `IMPLEMENTS` | type → interface | Interface implementation. |
| `INJECTS` | type → type | DI: field, constructor, or setter injection (incl. Lombok). |
| `DECLARES` | type → method/constructor | Type declares a callable. |
| `OVERRIDES` | method → method | Subtype instance method overrides a supertype-declared method (same `signature`, one supertype hop via `IMPLEMENTS` / `EXTENDS`). |
| `DECLARES_CLIENT` | type → client | Type declares an outbound call site. |
| `CALLS` | method → method | In-process call (confidence-scored, strategy-tagged). |
| `EXPOSES` | type → route | Type exposes an HTTP/async route. |
| `HTTP_CALLS` | symbol → route | Cross-service HTTP call (caller-side). |
| `ASYNC_CALLS` | symbol → route | Cross-service async (Kafka, Rabbit, JMS, …). |

JDK / Spring / Lombok callees are represented as **phantom** method symbols at index time. Caller/callee traversals default to `exclude_external=true` so those edges are filtered by FQN prefix without dropping them from the graph.

### Call-graph notes

- Receiver typing uses **one scope map per method** (locals shadow fields/parameters), but **not** full nested-block lexical scope. See `CODEBASE_REQUIREMENTS.md` → *Call graph*.
- **Anonymous classes** (`new T() { … }`) are indexed as synthetic nested types (`…<anon:startByte>`); `CALLS` from their methods use that member as the caller so inbound-call traversal reaches the handler body.
- **Lambdas** still attribute inner calls to the enclosing named method (no synthetic callable symbol).
- Unqualified calls from anonymous members fall through to the lexically enclosing type for callee lookup (matches Java compiler scoping).

### Injection mechanisms detected

- Field `@Autowired` / `@Inject` / `@Resource`
- Constructor injection (Spring single-ctor rule and explicit `@Autowired`)
- Setter `@Autowired`
- Lombok `@RequiredArgsConstructor` (final fields) and `@AllArgsConstructor` (all non-static)

### Chunk enrichment (Lance)

Java chunk rows are enriched with `package`, `module`, `microservice`, `primary_type_fqn`, `primary_type_kind`, `role`, `capabilities`, `annotations_on_type`, `symbols`, `ontology_version`. `role` and `capabilities` are inferred in `ast_java` / `graph_enrich`.

### `module` vs `microservice`

Two location fields are tracked per Java symbol / chunk:

- **`module`** — the *innermost* build-marker (`pom.xml`, `build.gradle`, `build.gradle.kts`, `build.sbt`) ancestor's directory name. (Legacy `service` field, renamed.)
- **`microservice`** — the *outermost* build-marker ancestor under the resolved Java tree root. For a single-module project both equal the same name; for a multi-module reactor (e.g. `chat-core/{chat-app,chat-engine,...}`) every child collapses to `microservice='chat-core'` while keeping its own `module='chat-app'`.

Resolution order for `microservice`:

1. Explicit override list — `microservice_roots: [foo, bar]` in `.java-codebase-rag.yml` at the project root (YAML-only).
2. Outermost build marker between `project_root` and the file.
3. First path segment under `project_root`.
4. `""` if nothing matches.

### Re-index required when ontology changes

Current ontology version is **13**. Any index built before this version must be rebuilt via `cocoindex update ... --full-reprocess -f` or a full `java-codebase-rag reprocess` (no selective flags) so vectors and graph stay aligned. Until re-indexed, the server defensively JSON-decodes string-form list columns so nothing explodes, but filters like `array_contains` will not work.

Ontology **13** materializes stored `OVERRIDES` edges between method Symbols (subtype override → supertype declaration, matching `signature` on a direct `IMPLEMENTS` / `EXTENDS` hop). `neighbors(edge_types=["OVERRIDES"])` traverses this relationship; `OVERRIDDEN_BY*` keys in `edge_summary` remain describe-time rollups only.

Ontology **12** renames `@CodebaseClient` to `@CodebaseHttpClient`, types HTTP `method` as the shared `CodebaseHttpMethod` enum on both inbound and outbound stubs, and makes inbound layer-C HTTP routes **replace** same-method built-in Spring rows (no merge). Rebuild after upgrading so `meta_chain` keys and annotation simple names match the extractor.

### Capabilities

In addition to the single primary `role` per Java type, the indexer extracts a multi-tag `capabilities: list[str]` field from method-level annotations, type-level annotations, injected types, and supertypes. A type can carry zero or many capabilities. Capabilities never *replace* the role; they augment it.

| Capability | Trigger |
|---|---|
| `MESSAGE_LISTENER` | `@KafkaListener`, `@RabbitListener`, `@JmsListener`, `@SqsListener`, `@EventListener`, `@StreamListener` on any method. |
| `MESSAGE_PRODUCER` | Type injects `KafkaTemplate`, `RabbitTemplate`, `JmsTemplate`, `StreamBridge`, or `ApplicationEventPublisher`. |
| `HTTP_CLIENT` | Type has `@FeignClient`. |
| `SCHEDULED_TASK` | `@Scheduled` on any method, or class implements `org.quartz.Job`. |
| `EXCEPTION_HANDLER` | `@ControllerAdvice`, `@RestControllerAdvice`, or any method with `@ExceptionHandler`. |

Use `find(kind="symbol", filter={"capability":"..."})` to enumerate types carrying a capability. Use `search(..., filter={"capability":"..."})` or `neighbors(..., filter={"capability":"..."})` for capability-aware narrowing.

### Ranking

Java hits are reweighted after vector / hybrid scoring by their `role`:

| Role | Weight |
|---|---|
| `CONTROLLER` | +0.10 |
| `SERVICE` | +0.08 |
| `CLIENT` | +0.06 |
| `COMPONENT` | +0.03 |
| `REPOSITORY` | +0.02 |
| `MAPPER` / `OTHER` | 0 |
| `ENTITY` | -0.06 |
| `CONFIG` | -0.10 |

This favours orchestrators / entrypoints / integrations over configuration and schema chunks for *what happens when…*-style queries, while keeping repositories and entities reachable. Weights are **skipped** when you pass an explicit `role=` filter; the per-row breakdown is surfaced in `score_components`.

On top of role weights, Java chunks receive a **symbol-match bonus** (exposed as `score_components.symbol_bonus`). Three additive components, all capped:

1. **Method / field overlap** — each declared symbol whose tokens overlap the query earns `+0.03` (capped at `+0.06`).
2. **Action-verb bump** — chunks declaring a method whose name begins with an action verb (`process`, `handle`, `on`, `pick`, `select`, `assign`, `notify`, `dispatch`, `publish`, `consume`, `route`, `trigger`, `enqueue`, `distribute`, …) get a flat `+0.02`.
3. **Type-name overlap** — strongest single lexical signal: when the simple name of `primary_type_fqn` shares tokens with the query, each overlap hit earns `+0.05` (capped at `+0.10`).

Combined, these pull `processClientMessage` / `pickEligibleOperator` / `onOperatorAssigned` chunks — and the classes that own them — above ones that only enqueue or configure. Like role weights, the bonus is **skipped when the caller locks `role=`**.

### Debugging empty `context_before` / `context_after`

If `context_neighbors=1` returns empty context strings, set `JAVA_CODEBASE_RAG_DEBUG_CONTEXT=1` in the MCP server env before launching. The server logs (to stderr) why expansion bailed: missing schema columns, empty bucket scan, chunk not found in bucket, or underlying scan error. Typical causes are (a) a stale server that hasn't reloaded after a reindex, or (b) an index missing `range_start` / `range_end` columns — the code falls back to exact-text matching, so re-running fixes it.

---

## 7. Brownfield overrides

For Spring-centric defaults that don't match your tree (custom wrapper stereotypes, non-Spring stacks, vendored code), you can steer `role`, `capabilities`, routes, and clients without forking the indexer. Three layers, in priority order:

1. **Config** — `.java-codebase-rag.yml` at the project root.
2. **Meta-annotation walk** — automatic discovery of `@interface` chains in your source.
3. **Source stubs** — copy `@CodebaseRole`, `@CodebaseCapability`, `@CodebaseHttpRoute`, `@CodebaseAsyncRoute`, `@CodebaseHttpClient`, `@CodebaseProducer` definitions into any package.

### 7.1 Config: `role_overrides`, `route_overrides`

`.java-codebase-rag.yml` at the project root (same file as `microservice_roots`). `role_overrides` maps annotation simple names and/or per-type FQNs to roles and capabilities:

```yaml
microservice_roots: []

role_overrides:
  annotations:
    AcmeService: SERVICE
    CompanyController: CONTROLLER
  capabilities:
    CompanyKafkaTopic: [MESSAGE_LISTENER]
    AcmeBatch: [SCHEDULED_TASK]
  fqn:
    com.legacy.OrderProcessor:
      role: SERVICE
      capabilities: [MESSAGE_LISTENER]
    com.acme.payments.PaymentEventBus:
      capabilities: [MESSAGE_PRODUCER]
```

Unknown role or capability strings are ignored with a warning on load.

`@FeignClient` interfaces auto-attach `role=CLIENT` and `capability=HTTP_CLIENT`. For `RestTemplate` / `WebClient` wrappers, opt in explicitly with `@CodebaseRole(CodebaseRoleKind.CLIENT)` and `@CodebaseCapability(CodebaseCapabilityKind.HTTP_CLIENT)`.

`route_overrides` maps custom annotation names (or suffixes such as `com.acme.Foo` when usage sites show only `Foo`) and per-type FQNs to `Route` fields for methods that don't otherwise resolve from Spring / Feign / messaging built-ins:

```yaml
route_overrides:
  annotations:
    ann.AcmeRoute:
      framework: spring_mvc
      kind: http_endpoint
      method: GET
      path: /acme
  fqn:
    com.legacy.UserApi:
      framework: spring_mvc
      kind: http_endpoint
      path: /legacy/users
```

Unknown `framework` / `kind` strings are dropped with a stderr warning.

### 7.2 Cross-service resolution mode

Optional top-level key in the same YAML file:

```yaml
cross_service_resolution: auto          # default when omitted
# cross_service_resolution: brownfield_only
```

With `brownfield_only`, the resolver does **not** promote auto-detected call sites to `cross_service` matches: only edges where both the caller strategy and every matched route's `source_layer` come from brownfield (`@CodebaseHttpRoute` / `@CodebaseAsyncRoute`, `@CodebaseHttpClient`, YAML overrides, meta-annotation closure, or FQN maps) stay `cross_service`. Everything else that would have been a cross-service match becomes `unresolved`. `intra_service`, `phantom`, and `ambiguous` behaviour is unchanged. Unknown values log a warning and behave like `auto`.

Resolution order for each method: built-in extraction → annotation map → meta-annotation closure → in-source `@CodebaseHttpRoute` / `@CodebaseAsyncRoute` → per-type FQN map (last writer wins on overlapping fields). On the same method, `@CodebaseAsyncRoute` replaces built-in `@KafkaListener` extraction so brownfield topic names aren't duplicated alongside SpEL or multi-topic listeners. For HTTP, `@CodebaseHttpRoute` replaces same-method built-in Spring mapping rows (brownfield exclusivity); enable `build_ast_graph.py --verbose` to see `brownfield-exclusivity-shadowing` INFO when framework annotations are bypassed.

### 7.3 Source stubs

If config and meta-annotations aren't enough, copy these `@interface` definitions into any package — **simple-name-only** matching means no Maven dependency on this bundle. Verbatim copies live under `tests/fixtures/brownfield_route_stubs/` and `tests/fixtures/brownfield_client_stubs/` for copy-pasting.

#### Roles & capabilities (class-level)

```java
package com.example.rag; // any package

import java.lang.annotation.*;

public enum CodebaseRoleKind {
    CONTROLLER, SERVICE, REPOSITORY, COMPONENT, CONFIG, ENTITY, CLIENT, MAPPER, DTO
}

public enum CodebaseCapabilityKind {
    MESSAGE_LISTENER, MESSAGE_PRODUCER, HTTP_CLIENT, SCHEDULED_TASK, EXCEPTION_HANDLER
}

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseRole { CodebaseRoleKind value(); }

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseCapabilities.class)
public @interface CodebaseCapability { CodebaseCapabilityKind value(); }

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseCapabilities { CodebaseCapability[] value(); }
```

Usage:

```java
@CodebaseRole(CodebaseRoleKind.SERVICE)
@CodebaseCapability(CodebaseCapabilityKind.MESSAGE_LISTENER)
@CodebaseCapability(CodebaseCapabilityKind.MESSAGE_PRODUCER)
public class LegacyChatService { /* ... */ }
```

> Resolver binds `@CodebaseRole(CodebaseRoleKind.…)`; string-literal `@CodebaseRole("…")` forms are ignored.

#### Direction matters: inbound vs outbound

| Direction | Annotation | Purpose |
|---|---|---|
| Inbound | `@CodebaseHttpRoute`, `@CodebaseAsyncRoute` | Declare handlers/listeners your service exposes as `Route` nodes. |
| Outbound | `@CodebaseHttpClient`, `@CodebaseProducer` | Declare call sites/publish sites your service invokes (caller edges). |

`@FeignClient` declarations are outbound (`clientKind=feign_method`), not inbound `Route` rows.

#### Routes (method-level, inbound)

```java
public enum CodebaseHttpMethod {
    GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS
}

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpRoutes.class)
public @interface CodebaseHttpRoute { String path(); CodebaseHttpMethod method(); }

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
public @interface CodebaseHttpRoutes { CodebaseHttpRoute[] value(); }

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseAsyncRoutes.class)
public @interface CodebaseAsyncRoute { String topic(); }

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
public @interface CodebaseAsyncRoutes { CodebaseAsyncRoute[] value(); }
```

Usage:

```java
@CodebaseHttpRoute(path = "/chat/joinOperator", method = CodebaseHttpMethod.POST)
public Reply joinOperator(Request req) { /* ... */ }

@CodebaseAsyncRoute(topic = "chat.follow-up")
public void onFollowUp(Event e) { /* ... */ }
```

`path` / `method` are required for HTTP routes; `topic` is required for async routes.

#### Clients & producers (method-level, outbound)

```java
public enum CodebaseClientKind { feign_method, rest_template, web_client }

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpClients.class)
public @interface CodebaseHttpClient {
    CodebaseClientKind clientKind();
    String targetService() default "";
    String path()          default "";
    CodebaseHttpMethod method();
}

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
public @interface CodebaseHttpClients { CodebaseHttpClient[] value(); }

public enum CodebaseProducerKind { kafka_send, stream_bridge_send }

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseProducers.class)
public @interface CodebaseProducer {
    CodebaseProducerKind producerKind() default CodebaseProducerKind.kafka_send;
    String topic();
}

@Target(ElementType.METHOD) @Retention(RetentionPolicy.SOURCE)
public @interface CodebaseProducers { CodebaseProducer[] value(); }
```

Usage:

```java
@CodebaseHttpClient(
    clientKind    = CodebaseClientKind.rest_template,
    targetService = "chat-core",
    path          = "/chat/joinOperator",
    method        = CodebaseHttpMethod.POST)
public Reply callJoinOperator(Request req) { /* ... */ }

@CodebaseProducer(
    producerKind = CodebaseProducerKind.kafka_send,
    topic        = "chat.follow-up")
public void publishFollowUp(Event e) { /* ... */ }
```

Resolution order in code: built-in inference → config annotation maps → meta-annotation walk → `@CodebaseRole` / `@CodebaseCapability` → `role_overrides.fqn` (highest priority for explicit per-type config). Route composition uses the same first-pass index, then `@CodebaseHttpRoute` / `@CodebaseAsyncRoute`, then `route_overrides.fqn`. Rebuild the affected store (`java-codebase-rag reprocess`, or `--vectors-only` / `--graph-only` when appropriate, or `build_ast_graph.py` for graph-only manual runs) after changing overrides.

### 7.4 Caller-side overrides

```yaml
http_client_overrides:
  annotations:
    ann.LegacyHttpClient:
      client_kind: rest_template
      target_service: chat-core
      path: /chat/joinOperator
      method: POST
  fqn:
    com.legacy.ChatClient:
      client_kind: feign_method
      target_service: chat-core

async_producer_overrides:
  annotations:
    ann.LegacyEvent:
      client_kind: kafka_send
      topic: chat.follow-up
      broker: ""
  fqn:
    com.legacy.EventBus:
      client_kind: kafka_send
      topic: chat.follow-up
```

Unknown `client_kind` values are dropped with a stderr warning. **One intentional divergence** from route layering: if any brownfield layer emits method-level outgoing calls, built-in outgoing calls for that same method are **replaced** (not appended) to avoid double-counting one network call site.

When a brownfield caller override specifies only part of what built-in detection would produce, missing fields are inherited from built-in — partial overrides are non-destructive (tightening, not replacing). Example: built-in produces `client_kind=rest_template`, `method=GET`, `path=/users/{id}`; an override sets only `path=/users/me`; the final call keeps `client_kind=rest_template` and `method=GET` while changing only the path.

### 7.5 Brownfield limitations

- **Duplicate `@interface` simple names across packages.** The meta map keys by simple name. If two distinct types share a name (`com.team1.X` and `com.team2.X`), only the first after **sorted file order** is kept; a stderr message names both FQNs. Resolve by renaming, or use `role_overrides.fqn` / `@CodebaseRole`.
- **Incremental indexing and annotation sources.** The indexer may only reprocess changed files. If you edit an `@interface` declaration (e.g. remove a `@Service` meta-annotation from a wrapper), every class that used it may need re-enrichment; the pipeline does not track that dependency automatically. **Run a full `java-codebase-rag reprocess` after changing any `@interface` used as a custom stereotype.**
- **`Symbol` rows scope.** `role` and `capabilities` on the graph are computed for **type** nodes (classes, interfaces, etc.). Method and constructor `Symbol` rows use defaults `role=OTHER` and `capabilities=[]`.

### 7.6 Lance / Kuzu consistency

Both the Kuzu graph writer and Lance chunk enrichment call **one** function — `graph_enrich.collect_annotation_meta_chain` — which scans the project with sorted `*.java` paths, the same layered ignore rules as `build_ast_graph` / `path_filtering.iter_java_source_files`, parse-error warnings on stderr, and deterministic *first wins* for duplicate annotation simple names. Kuzu and Lance **should** agree; they can still diverge if the same file is handled differently elsewhere in the pipeline (e.g. parse edge cases). If graph tools and `search` disagree on a type, run a full reindex and compare.

---

## 8. Ignore patterns

Java file discovery for the Kuzu graph, annotation meta-chain collection, and the CocoIndex Lance pipeline share the same layered ignore model (`path_filtering.LayeredIgnore`):

1. **Builtin default** — hardcoded patterns applied to every project.
2. **Project root** — optional `<project>/.java-codebase-rag/ignore` (gitignore syntax, including negation with `!`).
3. **Nested** — any `<subdir>/.java-codebase-rag/ignore` on the path from the project root to the file; closer files override farther ones.
4. **Git** — every `.gitignore` from the project root down to the file's directory, merged in order, using `pathspec.GitIgnoreSpec` (same semantics as git). Disable with `LayeredIgnore(..., use_gitignore=False)`.

### Builtin default patterns

The builtin default layer (`path_filtering.COMMON_EXCLUDED_PATH_PATTERNS`) combines two mechanisms.

**a) Glob patterns** (applied during the layered match):

| Pattern | Excludes |
|---|---|
| `**/.*` | Any dot-file or dot-directory at any depth. |
| `**/.git/**` | Git metadata. |
| `**/.idea/**` | IntelliJ project metadata. |
| `**/.venv/**` | Python virtual environments. |
| `**/node_modules/**` | npm/yarn dependency tree. |
| `**/*.class` | Compiled JVM class files. |
| `**/src/test/java/**` | Maven/Gradle test sources (prod-only index by design). |
| `**/src/test/resources/**` | Test resource bundles. |

**b) Build-output directory pruning** (during `os.walk` traversal). Three directory names — `out`, `build`, `target` — are pruned **only** when they sit alongside a build-tool indicator file (`pom.xml`, `build.gradle`, `build.gradle.kts`, `settings.gradle`, `settings.gradle.kts`). This guards against the false-positive where one of these names is a legal Java package (e.g. `com.example.out.api.AssignEndpoint` lives at `src/main/java/com/example/out/api/AssignEndpoint.java`, where `out/` is a package, not a Maven build output).

A few directory names are pruned **unconditionally** because they are never legal Java package names: `.git`, `.idea`, `.venv`, `node_modules` (defined in `path_filtering.UNCONDITIONAL_PRUNE_DIRS`).

To skip a directory the builtin walks (or include one it prunes), add a `.java-codebase-rag/ignore` file at the project root or any subtree root. Use `java-codebase-rag diagnose-ignore <path>` to see which layer decided for a given file.

If no `.java-codebase-rag/ignore` exists anywhere under the project, behaviour matches the builtin list alone (plus git when enabled). When a negation rule could un-ignore paths under directories the CocoIndex walk used to prune globally, the walk switches to a permissive exclude list and each candidate path is filtered again with the full layered rules.

**Monorepo note:** negation detection runs two full-tree `rglob` passes when constructing a `LayeredIgnore` (ignore files and `.gitignore` files). Usually cheap to amortise; extremely large trees should expect that fixed cost per new instance.

**Dependencies:** `pathspec` is pinned in `requirements.txt` and constrained the same way in `pyproject.toml` (loose bundle install vs. wheel metadata).

---

## 9. Further reading

| Document | What's in it |
|---|---|
| [`docs/paper/paper.pdf`](./docs/paper/paper.pdf) | Architecture report — design rationale, GPS metaphor, three-layer architecture, design principles, future work. |
| [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md) | Agent-facing guide. Copy-paste into `QWEN.md` / `CLAUDE.md` / `AGENTS.md`. |
| [`docs/skills/java-codebase-explore.md`](./docs/skills/java-codebase-explore.md) | Agent exploration skill (strategy, missions, fallbacks); packaged zip [`docs/skills/java-codebase-explore.zip`](./docs/skills/java-codebase-explore.zip) via `./scripts/build-explore-skill.sh` for Perplexity-style hosts. |
| [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) | Operator playbook for the CLI: workflows, exit codes, env alignment. |
| [`docs/MANUAL-VERIFICATION-CHECKLIST.md`](./docs/MANUAL-VERIFICATION-CHECKLIST.md) | 7-phase agent-driven verification after indexing your project. |
| [`automation/cursor_propose_only/README.md`](./automation/cursor_propose_only/README.md) | Optional orchestration workflow for single-command proposal pipelines (autopilot), planning/review loops, and automated per-PR execution via command templates. |
| [`CODEBASE_REQUIREMENTS.md`](./CODEBASE_REQUIREMENTS.md) | Assumptions about your Java repo + per-file edit map for non-conforming codebases. |
| [`propose/PRODUCT-VISION.md`](./propose/PRODUCT-VISION.md) | Long-term product direction. |

### Roadmap (graph layer)

- `get_service_topology` — microservice-level summary aggregating `HTTP_CALLS` / `ASYNC_CALLS`.
- Agentic routing layer (query classifier → vector / graph / both).
- Incremental Kuzu updates (per-changed-file) — see [`propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`](./propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md) and [`propose/INDEX-AUTO-MODE-PROPOSE.md`](./propose/INDEX-AUTO-MODE-PROPOSE.md).
- Optional `codegraph_nodes` LanceDB table embedding symbol summaries so the graph itself is vector-searchable.
