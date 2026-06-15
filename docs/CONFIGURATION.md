# Configuration reference

Everything that didn't fit in the README's 5-minute walkthrough lives here: environment variables, the project YAML, the graph layer (ontology, edges, capabilities, ranking), brownfield overrides, and the ignore-pattern layers.

For the architecture rationale (the GPS metaphor, three-layer design, future work), see [`paper/paper.pdf`](./paper/paper.pdf). For agent-facing tool shapes and recovery moves, see [`AGENT-GUIDE.md`](./AGENT-GUIDE.md). For the CLI playbook, see [`JAVA-CODEBASE-RAG-CLI.md`](./JAVA-CODEBASE-RAG-CLI.md).

> **Stability disclaimer.** MCP tool contracts, env vars, Lance/LadybugDB schemas, config files, and Python APIs may change without a deprecation period. Track `main` and rebuild indexes when ontology or embedding settings change (see [Re-index required when ontology changes](#re-index-required-when-ontology-changes)).

---

## Contents

1. [Environment variables](#1-environment-variables)
2. [Project YAML reference (`.java-codebase-rag.yml`)](#2-project-yaml-reference-java-codebase-ragyml)
3. [Graph layer — LadybugDB schema, edges, capabilities, ranking](#3-graph-layer)
4. [Brownfield overrides — config + in-source annotations](#4-brownfield-overrides)
5. [Ignore patterns](#5-ignore-patterns)

---

## 1. Environment variables

The operator-facing surface is **six** variables (plus MCP-only `JAVA_CODEBASE_RAG_SOURCE_ROOT` below). Precedence for knobs that also exist as CLI flags or YAML entries is **CLI flag > env var > YAML > built-in default** (see [`JAVA-CODEBASE-RAG-CLI.md`](./JAVA-CODEBASE-RAG-CLI.md)).

### Config file discovery (walk-up)

The tool automatically walks up the directory tree from the current working directory to find `.java-codebase-rag.yml` (or `.yaml`), similar to how Git finds `.git`. This means you can run CLI commands and MCP queries from any subdirectory within your project — the tool will locate the config file automatically.

**Walk-up behavior:**
- Starts from the current working directory and walks up the directory tree
- Stops at `$HOME` (inclusive — checks `$HOME` itself but doesn't walk past it)
- First match wins (closest config to cwd, not "most specific" or "deepest")
- If no config is found, falls back to using the current directory

**Precedence for source root resolution:**
1. CLI flag `--source-root` (highest priority)
2. Environment variable `JAVA_CODEBASE_RAG_SOURCE_ROOT`
3. YAML field `source_root` (resolved relative to config directory)
4. Walk-up discovery result (config directory itself)
5. Current working directory (fallback)

This walk-up behavior means you no longer need to set environment variables or pass flags when working from within a project — the tool finds the config automatically.

| Variable | Purpose |
|---|---|
| `JAVA_CODEBASE_RAG_INDEX_DIR` | Local filesystem **directory** for Lance tables, the LadybugDB file `code_graph.lbug`, and cocoindex state (`cocoindex.db`). Not a `lancedb://` or cloud URI — use a path. Default: `./.java-codebase-rag/` under the resolved Java tree root. |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. Overridable via `.java-codebase-rag.yml` `embedding.model` and `--embedding-model`. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. Overridable via YAML `embedding.device` and `--embedding-device`. |
| `JAVA_CODEBASE_RAG_DEBUG_CONTEXT` | When truthy, verbose stderr logging for chunk context expansion (diagnostics only). |
| `JAVA_CODEBASE_RAG_RUN_HEAVY` | Test gate: set to `1` / `true` / `yes` to run the slow cocoindex + Lance end-to-end test (`pytest`); not used in normal operator workflows. |
| `JAVA_CODEBASE_RAG_HINTS_ENABLED` | When `0` / `false` / `no`, suppress `hints_structured` and `advisories` from all MCP tool responses. Overridable via `.java-codebase-rag.yml` `hints.enabled`. Default: enabled. |

**MCP host launchers** also set `JAVA_CODEBASE_RAG_SOURCE_ROOT` to the Java repository root when it differs from the server process cwd (see `mcp.json.example` in the repo root).

Only the names in the table above (plus `JAVA_CODEBASE_RAG_SOURCE_ROOT` for MCP hosts) are read as configuration. Project config belongs in **`.java-codebase-rag.yml`** (or `.yaml`).

**Paths and conventions** (for scripts and operators):

- **`JAVA_CODEBASE_RAG_INDEX_DIR`** — filesystem path to the index directory (not a URI). Lance opens this directory; LadybugDB is always `<index-dir>/code_graph.lbug`; cocoindex keeps **`cocoindex.db`** next to them.
- **Java tree root** — CLI: `--source-root` (else cwd). MCP stdio: set `JAVA_CODEBASE_RAG_SOURCE_ROOT` when the Java repo root differs from the server process cwd.
- **`microservice_roots`** — configure only under **`microservice_roots:`** in `.java-codebase-rag.yml` (or `.yaml`).
- **Chunk context diagnostics / heavy tests** — `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`, `JAVA_CODEBASE_RAG_RUN_HEAVY` (see the table above).

Python package: **`java_codebase_rag`** (`python -m java_codebase_rag.cli`).

---

## 2. Project YAML reference (`.java-codebase-rag.yml`)

A single file at the project root (the directory you pass as `--source-root`, or cwd) holds everything that isn't an environment variable. The two accepted filenames are `.java-codebase-rag.yml` and `.java-codebase-rag.yaml`; if both exist, `.yml` wins.

**All keys are optional.** A project with no YAML at all uses built-in defaults plus env vars. Add only the keys you need.

```yaml
# .java-codebase-rag.yml — full reference, every key annotated.
# Place at the project root (same directory you pass as --source-root).

# -------- Core knobs (mirror env vars; precedence: CLI > env > YAML > default) --------

# Source root: the Java project root. Useful when the config file lives
# separately from the Java source code (e.g., monorepo with configs at repo root).
# - Tilde (`~`) is expanded; `$VAR` is NOT (use absolute paths or `~`).
# - Relative paths resolve against the config file's parent directory, not cwd.
# - Env: JAVA_CODEBASE_RAG_SOURCE_ROOT. CLI: --source-root.
# - Default: the directory containing this config file (for walk-up discovery).
# source_root: ../java-project

# Index directory: where Lance tables, code_graph.lbug, and cocoindex.db live.
# - Tilde (`~`) is expanded; `$VAR` is NOT (use absolute paths or `~`).
# - Relative paths resolve against the config file's parent directory (same
#   base as source_root), not cwd. The bare default ./.java-codebase-rag
#   (when this key is omitted) still sits beside the resolved source_root.
# - Env: JAVA_CODEBASE_RAG_INDEX_DIR. CLI: --index-dir. Default: ./.java-codebase-rag/
index_dir: ./.java-codebase-rag

# Embedding configuration. Must match between indexer and reader — if you change
# `embedding.model`, rebuild the index (`java-codebase-rag reprocess`).
embedding:
  # Hub id OR local directory containing the sentence-transformers model files.
  # - Hub id example: `sentence-transformers/all-MiniLM-L6-v2`
  # - Local path examples: `/opt/models/minilm`, `~/models/minilm`, `$MODEL_DIR/minilm`, `./models/minilm`
  # - Resolution applies expanduser + expandvars when the value is path-shaped
  #   (starts with `/`, `./`, `../`, `~`, or contains `$`); a result still
  #   `./` / `../`-prefixed after that expansion is then resolved to absolute.
  #   Same rule for `SBERT_MODEL` and `--embedding-model` after precedence picks
  #   the string. Plain `org/name` is treated as a hub id and passed through
  #   unchanged. A relative path without `./` (e.g. `models/minilm`) is
  #   ambiguous with hub-id shape — prepend `./` if you mean a local directory.
  # - Relative base (mirrors `index_dir`): a YAML `model` resolves against THIS
  #   config file's directory; `SBERT_MODEL` / `--embedding-model` resolve
  #   against the resolved `source_root`. So a committed `model: ./models/minilm`
  #   is portable across machines and across the CLI indexer vs the MCP reader,
  #   regardless of process CWD.
  # - Env: SBERT_MODEL. CLI: --embedding-model. Default: sentence-transformers/all-MiniLM-L6-v2
  model: sentence-transformers/all-MiniLM-L6-v2

  # Optional. One of: cpu, cuda, mps, cuda:0, cuda:1, ...
  # When omitted, sentence-transformers picks automatically.
  # Env: SBERT_DEVICE. CLI: --embedding-device.
  device: cpu

# -------- Microservice layout --------

# Explicit microservice roots, relative to source_root. When set, takes priority
# over auto-detection (build markers + outermost source-set folding).
# Each entry is a directory NAME (no leading slash, no `~`). See §4 for the
# auto-detection fallback and the diagnose-microservice CLI verb.
microservice_roots:
  - chat-core
  - chat-orchestrator
  - ranking

# Automatic microservice scope for queries (MCP server only)
# When working from a microservice subdirectory, queries automatically scope
# to that microservice — no manual filter needed. This provides correct
# codebase boundaries for agents working on specific microservices.
#
# Behavior:
# - At microservice root or inside a microservice subdirectory:
#   → Queries automatically scoped to that microservice
# - At project root (above all microservices):
#   → Queries span all microservices with an advisory message
# - Explicit microservice filters always override auto-detected scope
#
# The MCP server logs scope detection at startup:
#   [scope] Detected microservice: chat-core
#   [scope] Queries scoped to chat-core
# Or at system level:
#   [scope] No microservice detected (at project root)
#   [scope] Queries will span all microservices

# -------- Cross-service edge resolution --------

# How the resolver treats auto-detected cross-service call edges. See §4.2.
# - auto             (default): promote auto-detected callers to cross_service when a route matches.
# - brownfield_only           : only edges where both ends come from brownfield annotations or YAML
#                               stay cross_service; everything else becomes `unresolved`.
cross_service_resolution: auto

# -------- Hints (tool-call suggestions in MCP responses) --------

# When enabled (default), successful tool responses include `hints_structured`
# (next-action suggestions with ready-to-use tool args) and `advisories`
# (informational text). Disable to save tokens for capable models that don't
# need navigation guidance.
# Env: JAVA_CODEBASE_RAG_HINTS_ENABLED (1/true/yes or 0/false/no).
hints:
  enabled: true  # set to false to suppress hints and advisories

# -------- Brownfield overrides (see §4 for full schema and semantics) --------

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
| `index_dir` | partial | `~` expanded; `$VAR` is NOT expanded. A YAML relative path resolves against the config file's directory (same base as `source_root`); the default `./.java-codebase-rag` sits beside the resolved `source_root`. |
| `embedding.model` (when path-shaped) | yes | Path-shape = starts with `/`, `./`, `../`, `~`, or contains `$`; `~` / `$VAR` are expanded, then a result still `./` / `../`-prefixed is resolved to absolute. Plain `org/name` is treated as a hub id and passed through. Relative base (mirrors `index_dir`): a YAML `model` resolves against the config file's directory; `SBERT_MODEL` / `--embedding-model` resolve against `source_root`. Applies after CLI > env > YAML > default precedence. Long-lived MCP hosts also apply the same expansion when reading `SBERT_MODEL` from the process environment (so table metadata and search agree with `index_common` defaults). |
| `embedding.device` | n/a | Device strings (`cpu`, `cuda`, `mps`) aren't paths. |
| `microservice_roots[*]` | no | Each entry is a directory **name** relative to `source_root`, not an arbitrary path. |
| Brownfield `path:` / `topic:` values | no | These are URL paths and Kafka topic names, not filesystem paths. Literal characters preserved. |

**Tips & gotchas:**

- **The config file may live anywhere under your project, including a subdirectory of the Java tree.** Both the CLI (`init` / `increment` / `reprocess`) and the MCP server walk up from cwd to find `.java-codebase-rag.yml`, then resolve `source_root` and `index_dir` relative to the config file's directory. So a config living in `my-context/` next to `source_root: ../` and `index_dir: ../.java-codebase-rag` resolves identically for the CLI and the MCP server. Keep the file under your project (not `$HOME`); set `JAVA_CODEBASE_RAG_SOURCE_ROOT` (MCP) or `--source-root` (CLI) only to override the discovered location.
- **Don't commit secrets** into this YAML — it sits next to your source tree and is read by every operator who clones it.
- **Rebuild after editing brownfield overrides.** Run a full `java-codebase-rag reprocess` (no flags) so Lance and LadybugDB stay coherent, or use `--graph-only` / `--vectors-only` when you know only one store needs invalidation. Editing `embedding.model` requires a vector rebuild (`reprocess` or `--vectors-only`).
- **Diagnose what's loaded.** `java-codebase-rag meta` prints the resolved config and each value's `*_source` (`cli` / `env` / `yaml` / `default`) — see `embedding_model_source`, `embedding_device_source`, `index_dir_source`.
- **`embedding.model` and `$` in directory names.** `expandvars` treats `$VAR` / `${VAR}` like the shell. HuggingFace hub ids never contain `$`. If a local filesystem path contains a literal `$` in a directory name, use an absolute path that avoids `$`-expansion patterns, or expect `expandvars` to interpret `$` sequences.

Deeper documentation for the brownfield blocks (`role_overrides`, `route_overrides`, `http_client_overrides`, `async_producer_overrides`, `cross_service_resolution`) lives in [§4 Brownfield overrides](#4-brownfield-overrides).

---

## 3. Graph layer

A deterministic property graph derived from tree-sitter Java parsing lives next to the LanceDB tables under the index directory (default `${JAVA_CODEBASE_RAG_INDEX_DIR:-./.java-codebase-rag}/code_graph.lbug`). Current ontology version: **17** (see [`EDGE-NAVIGATION.md`](./EDGE-NAVIGATION.md) for MCP-traversable edge shapes).

### Node kinds

| Kind | Examples |
|---|---|
| `Symbol` | `package`, `file`, `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor` |
| `Route` | HTTP endpoint or async listener (one row per declared route) |
| `Client` | Outbound HTTP / messaging call site |
| `UnresolvedCallSite` | Receiver-failure call site (`chained_receiver`, `phantom_unresolved_receiver`) — not a `Symbol`; ids use the `ucs:` prefix |

Known-receiver-external JDK / Spring / Lombok callees stay on **`CALLS`** as phantom **method** symbols (`resolved=false`). Receiver-failure sites (unresolved receiver or chained receiver) are **`UnresolvedCallSite`** nodes linked by **`UNRESOLVED_AT`** (not in `EDGE_SCHEMA`; use `describe(method_id).unresolved_call_sites`, `neighbors(..., include_unresolved=True)`, or `java-codebase-rag unresolved-calls`).

### Edge types (MCP-traversable)

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
| `HTTP_CALLS` | client → route | Cross-service HTTP call (caller-side Client to target Route). |
| `ASYNC_CALLS` | producer → route | Cross-service async (Kafka, Rabbit, JMS, …). |

Caller/callee traversals default to `exclude_external=true` on **`find_callers`** so library FQN prefixes are filtered without dropping edges from the graph.

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

Current ontology version is **17**. Any index built before this version must be rebuilt via `cocoindex update ... --full-reprocess -f` or a full `java-codebase-rag reprocess` (no selective flags) so vectors and graph stay aligned. Until re-indexed, the server defensively JSON-decodes string-form list columns so nothing explodes, but filters like `array_contains` will not work.

Ontology **15** (CALLS-NOISE) adds `CALLS.callee_declaring_role`, `GraphMeta.pass3_unresolved_phantom_receiver` / `pass3_unresolved_chained`, and **supertype-walk dedup** at build time. PR-2 adds `edge_filter` on `neighbors`. **PR-3 (breaking):** receiver-failure sites (`chained_receiver`, unresolved-receiver `phantom`) are no longer `CALLS` rows — they live on `UnresolvedCallSite` + `UNRESOLVED_AT`. Default `neighbors(..., ['CALLS'])` returns fewer rows; use `include_unresolved=True` for a source-ordered interleaved transcript (`row_kind`), `describe(method_id).unresolved_call_sites` (capped), or `java-codebase-rag unresolved-calls list|stats`. Known-receiver-external JDK rows stay on `CALLS` with `resolved=false`.

Ontology **14** introduces `EDGE_SCHEMA` in `java_ontology.py` as the canonical edge navigation schema (see [`EDGE-NAVIGATION.md`](./EDGE-NAVIGATION.md)). **`HTTP_CALLS` is `Client → Route`** (SCHEMA-V2 PR-B). **`ASYNC_CALLS` is `Producer → Route`** with `DECLARES_PRODUCER` (SCHEMA-V2 PR-C). Run one full reprocess after upgrading through the SCHEMA-V2 sequence (or when you need the v14 ontology gate).

Ontology **13** materializes stored `OVERRIDES` edges between method Symbols (subtype override → supertype declaration, matching `signature` on a direct `IMPLEMENTS` / `EXTENDS` hop). `neighbors(edge_types=["OVERRIDES"])` traverses this relationship; `OVERRIDDEN_BY*` dot-keys in `edge_summary` are also navigable on method Symbol origins (`out` only).

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

## 4. Brownfield overrides

For Spring-centric defaults that don't match your tree (custom wrapper stereotypes, non-Spring stacks, vendored code), you can steer `role`, `capabilities`, routes, and clients without forking the indexer. Three layers, in priority order:

1. **Config** — `.java-codebase-rag.yml` at the project root.
2. **Meta-annotation walk** — automatic discovery of `@interface` chains in your source.
3. **Source stubs** — copy `@CodebaseRole`, `@CodebaseCapability`, `@CodebaseHttpRoute`, `@CodebaseAsyncRoute`, `@CodebaseHttpClient`, `@CodebaseProducer` definitions into any package.

### 4.1 Config: `role_overrides`, `route_overrides`

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

### 4.2 Cross-service resolution mode

Optional top-level key in the same YAML file:

```yaml
cross_service_resolution: auto          # default when omitted
# cross_service_resolution: brownfield_only
```

With `brownfield_only`, the resolver does **not** promote auto-detected call sites to `cross_service` matches: only edges where both the caller strategy and every matched route's `source_layer` come from brownfield (`@CodebaseHttpRoute` / `@CodebaseAsyncRoute`, `@CodebaseHttpClient`, YAML overrides, meta-annotation closure, or FQN maps) stay `cross_service`. Everything else that would have been a cross-service match becomes `unresolved`. `intra_service`, `phantom`, and `ambiguous` behaviour is unchanged. Unknown values log a warning and behave like `auto`.

Resolution order for each method: built-in extraction → annotation map → meta-annotation closure → in-source `@CodebaseHttpRoute` / `@CodebaseAsyncRoute` → per-type FQN map (last writer wins on overlapping fields). On the same method, `@CodebaseAsyncRoute` replaces built-in `@KafkaListener` extraction so brownfield topic names aren't duplicated alongside SpEL or multi-topic listeners. For HTTP, `@CodebaseHttpRoute` replaces same-method built-in Spring mapping rows (brownfield exclusivity); enable `build_ast_graph.py --verbose` to see `brownfield-exclusivity-shadowing` INFO when framework annotations are bypassed.

### 4.3 Source stubs

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

### 4.4 Caller-side overrides

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

### 4.5 Brownfield limitations

- **Duplicate `@interface` simple names across packages.** The meta map keys by simple name. If two distinct types share a name (`com.team1.X` and `com.team2.X`), only the first after **sorted file order** is kept; a stderr message names both FQNs. Resolve by renaming, or use `role_overrides.fqn` / `@CodebaseRole`.
- **Incremental indexing and annotation sources.** The indexer may only reprocess changed files. If you edit an `@interface` declaration (e.g. remove a `@Service` meta-annotation from a wrapper), every class that used it may need re-enrichment; the pipeline does not track that dependency automatically. **Run a full `java-codebase-rag reprocess` after changing any `@interface` used as a custom stereotype.**
- **`Symbol` rows scope.** `role` and `capabilities` on the graph are computed for **type** nodes (classes, interfaces, etc.). Method and constructor `Symbol` rows use defaults `role=OTHER` and `capabilities=[]`.

### 4.6 Lance / LadybugDB consistency

Both the LadybugDB graph writer and Lance chunk enrichment call **one** function — `graph_enrich.collect_annotation_meta_chain` — which scans the project with sorted `*.java` paths, the same layered ignore rules as `build_ast_graph` / `path_filtering.iter_java_source_files`, parse-error warnings on stderr, and deterministic *first wins* for duplicate annotation simple names. LadybugDB and Lance **should** agree; they can still diverge if the same file is handled differently elsewhere in the pipeline (e.g. parse edge cases). If graph tools and `search` disagree on a type, run a full reindex and compare.

---

## 5. Ignore patterns

Java file discovery for the LadybugDB graph, annotation meta-chain collection, and the CocoIndex Lance pipeline share the same layered ignore model (`path_filtering.LayeredIgnore`):

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
