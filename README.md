# LanceDB code search MCP (export bundle)

Self-contained **stdio MCP server** for semantic search over a LanceDB index (Java / SQL / YAML) produced by CocoIndex `java_index_flow_lancedb.py`, *plus* a deterministic AST-derived graph (Kuzu sidecar) for structural code queries.

**Breaking changes:** This repository does not promise backward compatibility for downstream users or integrations. MCP tool contracts, env vars, Lance/Kuzu schemas, config files, and Python APIs may change at any time without a deprecation period. Upgrade by following current `main` and rebuilding or re-indexing when the docs or bundle require it.

The product vision for this tooling is proposed in [`propose/PRODUCT-VISION.md`](./propose/PRODUCT-VISION.md).

**No `cocoindex` Python package is required to run search or MCP** — only `sentence-transformers`, `lancedb`, `kuzu`, `tree_sitter` + `tree_sitter_java`, and `mcp`. CocoIndex is optional and only needed if you use the `refresh_code_index` tool.

> **Tuning for your codebase:** see [`CODEBASE_REQUIREMENTS.md`](./CODEBASE_REQUIREMENTS.md)
> for the assumptions this MCP makes about a Java repo (annotations, DI patterns,
> service layout, naming) and a per-file map of where to edit the bundle if you
> can't or don't want to refactor your codebase to match.
>
> **Driving this MCP from an agent:**
> - [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md) — copy-paste-into-`QWEN.md` /
>   `CLAUDE.md` block. Forced reasoning preamble, decision tree, full
>   reference for all 22 tools, ontology glossary (v10), recovery playbook,
>   slash-style aliases. Engineered for weak / mid models that otherwise
>   pick the wrong tool.
> - [`docs/MANUAL-VERIFICATION-CHECKLIST.md`](./docs/MANUAL-VERIFICATION-CHECKLIST.md)
>   — 7-phase agent-driven verification you run after indexing your real
>   project. Each item has a copy-paste prompt and calibration data from
>   `tests/bank-chat-system`.

## 1. Install

```bash
cd mcp_lancedb_bundle
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Use **Python 3.11+**. The embedding model must match the one used when the index was built (default: `sentence-transformers/all-MiniLM-L6-v2`).

## 2. Environment

| Variable | Purpose |
|----------|---------|
| `LANCEDB_URI` | **Required for real use:** absolute path to the `lancedb_data` directory (or remote LanceDB URI). |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. |
| `LANCEDB_MCP_PROJECT_ROOT` | **Java project root** to index and to resolve `module` / `microservice` (search tools, `build_ast_graph.py`, and the CocoIndex flow when run via `refresh_code_index`). You do **not** need a copy of `java_index_flow_lancedb.py` in that tree—the bundled flow is used when the file is missing there. If unset, the MCP process working directory is used (see graph section below). |
| `LANCEDB_MCP_ALLOW_REFRESH` | Set to `1` to enable the heavy `refresh_code_index` tool. |
| `KUZU_DB_PATH` | Absolute path to the Kuzu graph DB. Defaults to `${LANCEDB_URI}/code_graph.kuzu`. |
| `LANCEDB_MCP_GRAPH_ENABLED` | `1`/`0` to force on/off; auto-on when the Kuzu DB exists. |
| `LANCEDB_MCP_MICROSERVICE_ROOTS` | Optional comma-separated directory names that should be treated as microservice roots (overrides structural inference). Same effect as listing them under `microservice_roots:` in `.lancedb-mcp.yml` at the project root. |

## 3. Claude Code

**Project scope:** copy `mcp.json.example` to your repo as `.mcp.json`, replace absolute paths, merge with existing `mcpServers` if any.

Or use the CLI:

```bash
claude mcp add --transport stdio lancedb-code -- \
  /path/to/mcp_lancedb_bundle/.venv/bin/python \
  /path/to/mcp_lancedb_bundle/server.py
```

Then set env vars in `.mcp.json` or your shell profile as needed (`LANCEDB_URI`, `KUZU_DB_PATH`, etc.).

Official docs: [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings) (see MCP / `.mcp.json`).

## 4. Claude Desktop

Edit `claude_desktop_config.json` (e.g. macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) and add an entry under `mcpServers` with the same `command`, `args`, and `env` as in `mcp.json.example`.

## 5. AST Graph layer (Kuzu)

A sidecar deterministic graph derived from Tree-sitter Java parsing lives next to the LanceDB files (default `${LANCEDB_URI}/code_graph.kuzu`).

**Node types:** `package`, `file`, `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`. Unresolved targets become **phantom** nodes (`resolved=false`, FQN guessed from imports / `java.lang`).

**Edge types (Phase 1 + Phase 3):** `EXTENDS`, `IMPLEMENTS`, `INJECTS` capture type-level wiring.
**Phase 3 (call graph):** `CALLS` (method → method, confidence-scored, strategy-tagged) and
`DECLARES` (type → own method or constructor).
**Phase 5 (caller edges):** `HTTP_CALLS` (`Symbol` → `Route`) and `ASYNC_CALLS` (`Symbol` → `Route`).
JDK / Spring / Lombok callees are represented as
phantom method symbols (`resolved=false`) at index time; `find_callers` / `find_callees` default to
`exclude_external=true` so those edges can be filtered by FQN prefix without dropping them from the graph.
Call-site receiver typing uses **one scope map per method** (locals shadow fields/parameters), but **not** full nested-block lexical scope; see **Call graph** under `CODEBASE_REQUIREMENTS.md`.
**Anonymous classes** (`new T() { … }`) are indexed as synthetic nested types (`…<anon:startByte>`); `CALLS` from their methods use that member as the caller so `find_callers` reaches the handler body. **Lambdas** still attribute inner calls to the enclosing named method (no synthetic callable symbol). Unqualified calls from anonymous members fall through to the lexically enclosing type for callee lookup (same as the Java compiler’s access rules).

Injection mechanisms detected:
- field `@Autowired` / `@Inject` / `@Resource`
- constructor injection (Spring single-ctor rule and explicit `@Autowired`)
- setter `@Autowired`
- Lombok `@RequiredArgsConstructor` (final fields) and `@AllArgsConstructor` (all non-static)

**Java chunk rows are enriched** with `package`, `module`, `microservice`, `primary_type_fqn`, `primary_type_kind`, `role`, `capabilities`, `annotations_on_type`, `symbols`, `ontology_version`. `role` and `capabilities` are inferred in `ast_java` / `graph_enrich` (see **Brownfield overrides** below for per-project customisation).

**Two location fields are tracked per Java symbol / chunk:**

- `module` — the *innermost* build-marker (`pom.xml`, `build.gradle`, `build.gradle.kts`, `build.sbt`) ancestor's directory name. This is the legacy `service` field, renamed.
- `microservice` — the *outermost* build-marker ancestor under `LANCEDB_MCP_PROJECT_ROOT`. For a single-module project both equal the same name; for a multi-module reactor (e.g. `chat-core/{chat-app,chat-engine,...}`) every child module collapses to `microservice='chat-core'` while keeping its own `module='chat-app'` etc.

Resolution order for `microservice`:
1. explicit override list — `LANCEDB_MCP_MICROSERVICE_ROOTS=foo,bar` env var or
   `microservice_roots: [foo, bar]` in `.lancedb-mcp.yml` at the project root;
2. outermost build marker between `project_root` and the file;
3. first path segment under `project_root`;
4. `""` if nothing matches.

> **Re-index required.** The `JavaLanceChunk` schema evolves with this bundle:
> 1. it gained enrichment columns (first cut of the graph work); and
> 2. `annotations_on_type` / `symbols` are now native PyArrow `list<string>` instead of
>    JSON-encoded strings (previous builds caused char-array output — see below).
> 3. **`ontology_version` 5** adds `Route` / `EXPOSES` and route counters on `GraphMeta` —
> 4. **`ontology_version` 7** adds caller-side edge extraction (`HTTP_CALLS`, `ASYNC_CALLS`) and
>    brownfield caller composition (`http_client_overrides`, `async_producer_overrides`,
>    `@CodebaseClient`, `@CodebaseProducer`) — rebuild Kuzu after upgrading.
> 5. **`ontology_version` 8** adds `GraphMeta.cross_service_resolution` (from
>    `cross_service_resolution` in `.lancedb-mcp.yml`) — rebuild the Kuzu graph
>    (`build_ast_graph.py` or `refresh_code_index`) after upgrading.
> 6. **`ontology_version` 9** renames role `FEIGN_CLIENT` to `CLIENT` and adds
>    capability `HTTP_CLIENT` for `@FeignClient` interfaces — rebuild to refresh
>    stored role/capability literals.
> 7. **`ontology_version` 10** adds first-class outbound `Client` nodes and
>    `DECLARES_CLIENT` edges, plus `GraphMeta` client counters — rebuild the Kuzu
>    graph after upgrading.
>
> Any index built before these changes must be rebuilt via
> `cocoindex update ... --full-reprocess -f` or `refresh_code_index`. Until
> re-indexed, the server defensively JSON-decodes string-form list columns so
> nothing explodes, but filters like `array_contains` will not work.

### Building the graph

Via MCP: `refresh_code_index` (with `LANCEDB_MCP_ALLOW_REFRESH=1`) first runs `cocoindex update` to rebuild chunks, then invokes `build_ast_graph.py` to rebuild Kuzu.

Standalone:

```bash
# scan the current working directory
.venv/bin/python build_ast_graph.py --verbose

# or point at a specific repo root
.venv/bin/python build_ast_graph.py --source-root /path/to/repo --verbose
```

> If `--source-root` is omitted, the current working directory is used. The same convention applies to the MCP server: when `LANCEDB_MCP_PROJECT_ROOT` is unset, the process's current working directory is used as the project root.

For `refresh_code_index`, the server runs `cocoindex` with `cwd` set to the bundle directory (so Python imports resolve), but sets `LANCEDB_MCP_PROJECT_ROOT` on that subprocess to the same resolved project root as above so indexing targets your Java tree—not the bundle.

The DB is dropped and rebuilt from scratch on each run (Phase 1 is a full rebuild; incremental updates are future work).

### Tools exposed by the server

| Tool | Purpose |
|------|---------|
| `codebase_search` | Vector / hybrid / graph-expanded search. Supports `role`, `module`, `microservice`, `package_prefix` filters, `graph_expand=true` + `expand_depth=1..3` for Kuzu-BFS fusion (RRF), and `context_neighbors=1..2` to attach adjacent chunks as `context_before`/`context_after`. Java hits return `score_components` (`distance`, `hybrid_rrf`, `role_weight`, `symbol_bonus`, `import_penalty`) so callers can see why a row ranked where it did. |
| `trace_flow` | Behavioural trace from a natural-language query. Seeds via vector search, then walks CONTROLLER -> SERVICE/COMPONENT -> CLIENT/REPOSITORY/MAPPER in the Kuzu graph and returns staged chains. Defaults to `follow_calls=true` (merges DECLARES+CALLS paths with INJECTS/EXTENDS/IMPLEMENTS — structural edges fill `stage_limit` first per hop, CALLS tops up the remainder); set `follow_calls=false` for type-only wiring. Defaults to `exclude_external=true` on that CALLS hop (same external FQN prefixes as `find_callees` / `expand_methods`: discovered types reached via CALLS, not the caller-only filter used by `find_callers`). |
| `list_code_index_tables` | Lance tables + Kuzu graph metadata. |
| `refresh_code_index` | Rebuild LanceDB + Kuzu graph. |
| `find_implementors` | Classes implementing an interface. |
| `find_subclasses` | Types extending a class/interface. |
| `find_injectors` | Classes that inject the given type, incl. mechanism/annotation/field. |
| `find_callers` | Inbound `CALLS` closure: who invokes a method (or any method of a type via DECLARES). Same `min_confidence`; `exclude_external` filters caller (src) FQNs, not the needle. |
| `find_callees` | Outbound `CALLS` closure: callees of a method or type. Same `min_confidence`; `exclude_external` filters callee (dst) FQNs, not the needle. |
| `list_by_role` | Symbols with a given role (CONTROLLER, SERVICE, ...). |
| `list_by_annotation` | Symbols whose annotation list contains the given simple name. |
| `graph_neighbors` | Generic BFS over `EXTENDS|IMPLEMENTS|INJECTS|DECLARES|CALLS`, directional. |
| `impact_analysis` | Reverse closure: what breaks if this changes. |
| `analyze_pr` | Map a unified diff (`diff_unified`) to overlapping indexed symbols, sum type-level `impact_analysis` blast, count cross-microservice `CALLS`, list touched `Route` ids (`EXPOSES`), and return a v1 `risk_score` / `risk_band` plus `notes` (binary hunks and renames are skipped for symbol mapping). |
| `diagnose_ignore` | Explain whether a path is excluded for indexing / graph walks and which rule layer won (`builtin_default`, `project_root`, `nested`, `gitignore`). |
| `graph_meta` | Counts, ontology version, build timestamp, parse errors; route totals / `routes_by_framework` / `routes_resolved_pct` (v5+); `routes_from_brownfield_pct` / `routes_by_layer` (v6+). |
| `list_routes` | Filterable listing of `Route` nodes (`microservice`, `framework`, `path_prefix`, `method`). |
| `list_clients` | Filterable listing of outbound `Client` nodes (`microservice`, `client_kind`, `target_service`, `path_prefix`, `method`). |
| `find_route_handlers` | Endpoint symbols that `EXPOSES` a route id (confidence + resolution strategy on the edge); Feign consumer routes return empty. |
| `get_route_by_path` | Lookup one `Route` by `microservice` + normalised `path_template` + optional HTTP method. |
| `find_route_callers` | Callers that reach a route via `HTTP_CALLS` / `ASYNC_CALLS` (by route id or exact route tuple). |
| `trace_request_flow` | Inbound caller + outbound handler flow around one route entrypoint. |

HTTP mappings from literals are fully resolved (non-empty `path_template` / `path_regex`). Values containing Spring ``${…}`` SpEL, or non-string annotation arguments (constant references), are still stored as routes with lower confidence and empty template fields. Caller-side edges are now shipped via `HTTP_CALLS` / `ASYNC_CALLS` and exposed through `find_route_callers` and `trace_request_flow`.
Use `list_routes` for inbound service exposures and `list_clients` for outbound HTTP declarations (Feign methods and annotated imperative clients). `list_clients` requires graphs rebuilt with `ontology_version` 10+.

**Example — `analyze_pr`:** pass the same unified diff text you would feed to `patch` (e.g. `git diff` output). Paths in the diff should match project-relative `Symbol.filename` values in the graph (e.g. `chat-assign/src/main/java/.../ChatManagementService.java`). A one-line edit inside `assign` returns JSON shaped like:

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

### Manual test

```bash
# Vector
LANCEDB_URI=/path/to/lancedb_data .venv/bin/python search_lancedb.py "rate limit" --table java --limit 2

# Graph-expanded (requires the Kuzu DB to exist)
LANCEDB_URI=/path/to/lancedb_data .venv/bin/python search_lancedb.py "rate limit" \
  --table java --limit 5 --graph-expand --expand-depth 2

# Role-filtered
.venv/bin/python search_lancedb.py "place order" --table java --role CONTROLLER

# With surrounding context (1 chunk before + 1 chunk after)
.venv/bin/python search_lancedb.py "chat assignment" \
  --table java --limit 3 --context-neighbors 1
```

### Ranking behaviour

Java hits are reweighted after vector / hybrid scoring by their `role`:

| Role | Weight |
|------|--------|
| `CONTROLLER` | +0.10 |
| `SERVICE` | +0.08 |
| `CLIENT` | +0.06 |
| `COMPONENT` | +0.03 |
| `REPOSITORY` | +0.02 |
| `MAPPER` / `OTHER` | 0 |
| `ENTITY` | -0.06 |
| `CONFIG` | -0.10 |

This favours orchestrators / entrypoints / integrations over configuration and
schema chunks for "what happens when..."-style queries while keeping repositories
and entities reachable. The weights are **skipped** when you pass an explicit
`role=` filter, and the per-row breakdown is surfaced in `score_components`.

### Capabilities

In addition to the single primary `role` per Java type, the indexer
extracts a multi-tag `capabilities: list[str]` field from method-level
annotations, type-level annotations, injected types, and supertypes.
A type can carry zero or many capabilities. Capabilities never
*replace* the role; they augment it.

| Capability | Trigger |
|---|---|
| `MESSAGE_LISTENER` | `@KafkaListener`, `@RabbitListener`, `@JmsListener`, `@SqsListener`, `@EventListener`, `@StreamListener` on any method |
| `MESSAGE_PRODUCER` | type injects `KafkaTemplate`, `RabbitTemplate`, `JmsTemplate`, `StreamBridge`, or `ApplicationEventPublisher` |
| `HTTP_CLIENT` | type has `@FeignClient` |
| `SCHEDULED_TASK`   | `@Scheduled` on any method, or class implements `org.quartz.Job` |
| `EXCEPTION_HANDLER`| `@ControllerAdvice`, `@RestControllerAdvice`, or any method with `@ExceptionHandler` |

Use `list_by_capability` to enumerate types carrying a capability, or
pass `capability=...` to `codebase_search` / `list_by_role` /
`list_by_annotation` / `find_*` to AND-filter results.

### Brownfield overrides

For Spring-centric defaults that do not match your tree (custom wrapper
stereotypes, non-Spring stacks, vendored code), you can steer `role` and
`capabilities` without forking the indexer.

**1. Config (`.lancedb-mcp.yml` at the project root, same file as
`microservice_roots`)** — `role_overrides` maps annotation simple names
and/or per-type FQNs to roles and capabilities:

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

`@FeignClient` interfaces now auto-attach `role=CLIENT` and
`capability=HTTP_CLIENT`. For `RestTemplate`/`WebClient` wrappers, opt in
explicitly with `@CodebaseRole(CodebaseRoleKind.CLIENT)` and
`@CodebaseCapability(CodebaseCapabilityKind.HTTP_CLIENT)`.

**Route overrides (`route_overrides`)** — same `.lancedb-mcp.yml` file; maps
custom annotation names or qualified names (or suffixes such as `com.acme.Foo`
when usage sites show only `Foo`) and per-type FQNs to `Route` fields for
methods that do not otherwise resolve from Spring / Feign / messaging
built-ins. Shape:

```yaml
route_overrides:
  annotations:
    ann.AcmeRoute:          # or simple name `AcmeRoute` when that matches usage
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

**Cross-service resolution mode** — optional top-level key in the same file:

```yaml
cross_service_resolution: auto          # default when omitted
# cross_service_resolution: brownfield_only
```

With `brownfield_only`, pass 6 does not promote auto-detected call sites to
`cross_service` matches: only edges where both the caller strategy and every
matched route’s `source_layer` come from brownfield (`@CodebaseHttpRoute` / `@CodebaseAsyncRoute`,
`@CodebaseClient`, YAML overrides, meta-annotation closure, or FQN maps) stay
`cross_service`. Everything else that would have been a cross-service match
becomes `unresolved`. `intra_service`, `phantom`, and `ambiguous` behaviour is
unchanged. Unknown values log a warning and behave like `auto`.

Resolution order for each method mirrors role brownfield: built-in extraction,
then annotation map, then meta-annotation closure (same `collect_annotation_meta_chain`
index as roles — see `plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`),
then in-source `@CodebaseHttpRoute` / `@CodebaseAsyncRoute`, then per-type FQN map
(last writer wins on overlapping fields). For the in-source form, copy the
`@CodebaseHttpRoute` / `@CodebaseAsyncRoute` stubs shown under
**3. Last resort — source stubs**
below into any package — no Maven dependency needed.

**2. Meta-annotation walk (automatic)** — `@interface` definitions in your
source can carry meta-annotations; Layer A resolves chains to built-in
stereotype and capability trigger names (e.g. `@Service`, `@KafkaListener`)
via `graph_enrich.collect_annotation_meta_chain` (single index for both
Kuzu and Lance — see below).

**3. Last resort — source stubs** — copy the `@interface` definitions below
into your project (any package) and annotate your classes/methods. All
stubs are matched by **simple name only** (no Maven dependency on this
bundle). The route and client/producer stubs also live verbatim under
`tests/fixtures/brownfield_route_stubs/com/example/rag/` and
`tests/fixtures/brownfield_client_stubs/com/example/rag/` for copy-pasting.

**3a. Roles & capabilities** — class-level. Apply
`@CodebaseRole(CodebaseRoleKind.SERVICE)` /
`@CodebaseCapability(CodebaseCapabilityKind.MESSAGE_LISTENER)` on a class:

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
public @interface CodebaseRole {
    CodebaseRoleKind value();
}

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseCapabilities.class)
public @interface CodebaseCapability {
    CodebaseCapabilityKind value();
}

@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseCapabilities {
    CodebaseCapability[] value();
}
```

Usage:

```java
@CodebaseRole(CodebaseRoleKind.SERVICE)
@CodebaseCapability(CodebaseCapabilityKind.MESSAGE_LISTENER)
@CodebaseCapability(CodebaseCapabilityKind.MESSAGE_PRODUCER)
public class LegacyChatService { /* ... */ }
```

Legacy string-literal forms (`@CodebaseRole("SERVICE")`,
`@CodebaseCapability("MESSAGE_LISTENER")`) are a breaking change and are no
longer applied by the resolver.

**Direction matters (inbound vs outbound):**

| Direction | Annotation | Purpose | Feign handling |
|---|---|---|---|
| Inbound | `@CodebaseHttpRoute`, `@CodebaseAsyncRoute` | Declare handlers/listeners your service exposes as `Route` nodes. | Not used for Feign. |
| Outbound | `@CodebaseClient`, `@CodebaseProducer` | Declare call sites/publish sites your service invokes (pass 5/6 caller edges). | `@FeignClient` declarations are outbound (`clientKind=feign_method`), not inbound `Route` rows. |

**3b. Routes** — method-level inbound annotations are split by channel:
`@CodebaseHttpRoute` for HTTP handlers and `@CodebaseAsyncRoute` for async
listeners.

```java
package com.example.rag; // any package

import java.lang.annotation.*;

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseHttpRoutes.class)
public @interface CodebaseHttpRoute {
    String path();
    String method();
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseHttpRoutes {
    CodebaseHttpRoute[] value();
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseAsyncRoutes.class)
public @interface CodebaseAsyncRoute {
    String topic();
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseAsyncRoutes {
    CodebaseAsyncRoute[] value();
}
```

Usage:

```java
// HTTP endpoint on a legacy framework the built-in extractor doesn't know
@CodebaseHttpRoute(path = "/chat/joinOperator", method = "POST")
public Reply joinOperator(Request req) { /* ... */ }

// Kafka consumer
@CodebaseAsyncRoute(topic = "chat.follow-up")
public void onFollowUp(Event e) { /* ... */ }
```

`path` / `method` are required for HTTP routes; `topic` is required for async
routes. Repeatable containers are `@CodebaseHttpRoutes` and
`@CodebaseAsyncRoutes`.

**3c. Clients & producers** — method-level. Apply `@CodebaseClient` on
outbound HTTP call sites and `@CodebaseProducer` on outbound message-publish
calls so caller-side resolution (pass 6) can register them. Both use enum
typing (`CodebaseClientKind` and `CodebaseProducerKind`) for compile-time
validation.

```java
package com.example.rag; // any package

import java.lang.annotation.*;

public enum CodebaseClientKind {
    feign_method, rest_template, web_client
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseClients.class)
public @interface CodebaseClient {
    CodebaseClientKind clientKind();
    String targetService() default "";
    String path()          default "";
    String method()        default "";
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseClients {
    CodebaseClient[] value();
}

public enum CodebaseProducerKind {
    kafka_send, stream_bridge_send
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
@Repeatable(CodebaseProducers.class)
public @interface CodebaseProducer {
    CodebaseProducerKind producerKind() default CodebaseProducerKind.kafka_send;
    String topic();
}

@Target(ElementType.METHOD)
@Retention(RetentionPolicy.SOURCE)
public @interface CodebaseProducers {
    CodebaseProducer[] value();
}
```

Usage:

```java
// Outbound HTTP call to another service
@CodebaseClient(
    clientKind    = CodebaseClientKind.rest_template,
    targetService = "chat-core",
    path          = "/chat/joinOperator",
    method        = "POST")
public Reply callJoinOperator(Request req) { /* ... */ }

// Kafka publisher
@CodebaseProducer(
    producerKind = CodebaseProducerKind.kafka_send,
    topic        = "chat.follow-up")
public void publishFollowUp(Event e) { /* ... */ }
```

As with inbound annotations, multiple `@CodebaseClient` / `@CodebaseProducer`
annotations on the same method are wrapped in `@CodebaseClients` /
`@CodebaseProducers` automatically. Partial overrides are non-destructive
(see *Caller-side brownfield overrides* above).

Resolution order in code: built-in inference, then config annotation maps,
then meta-annotation walk, then `@CodebaseRole` / `@CodebaseCapability`, then
`role_overrides.fqn` (highest priority for explicit per-type config). Route
composition uses the same Layer A index, then `@CodebaseHttpRoute` / `@CodebaseAsyncRoute`,
then `route_overrides.fqn`. Rebuild Lance + Kuzu (`refresh_code_index` or
`build_ast_graph.py`) after changing overrides.

**Caller-side brownfield overrides (`http_client_overrides` / `async_producer_overrides`)**:

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

Unknown `client_kind` values are dropped with a stderr warning. Caller-side
layering mirrors routes (built-in, layer B annotations, layer A meta, layer C
source stubs, layer B FQN), with one intentional divergence: if any brownfield
layer emits method-level outgoing calls, built-in outgoing calls for that same
method are replaced (not appended) to avoid double-counting one network call
site.

When a brownfield caller override specifies only part of what built-in detection
would produce, missing fields are inherited from the built-in result. Partial
overrides are therefore non-destructive (tightening instead of replacing). To
fully replace the built-in result for a method, supply all relevant fields in
the override; otherwise unspecified fields default to built-in values.
Example: if built-in detection produces `client_kind=rest_template`, `method=GET`,
`path=/users/{id}`, and an override sets only `path=/users/me`, the final call
keeps `client_kind=rest_template` and `method=GET` while changing only the path.

For in-source stubs, see **3c. Clients & producers** above for the full
`@CodebaseClient` / `@CodebaseClients` / `@CodebaseProducer` /
`@CodebaseProducers` `@interface` definitions and usage examples (same
"simple-name only" matching as brownfield route stubs).

**Kuzu vs Lance (Layer A consistency):** both the Kuzu graph writer and Lance
chunk enrichment call **one** function, `graph_enrich.collect_annotation_meta_chain`,
which scans the project with sorted `*.java` paths, the same layered ignore rules as
`build_ast_graph` / `path_filtering.iter_java_source_files`, parse-error warnings on stderr, and
deterministic “first wins” for duplicate annotation simple names. Kuzu and Lance
**should** agree; they can still diverge if the same file is handled differently
elsewhere in the pipeline (e.g. parse edge cases). If graph tools and
`codebase_search` disagree on a type, run a full reindex and compare.

**Limitations (Layer A / brownfield):**

1. **Duplicate `@interface` simple names across packages.** Layer A keys the
   meta map by simple name (usage sites do not always have import-resolved
   FQNs). If two distinct types share a name (e.g. `com.team1.X` and
   `com.team2.X`), only the first after **sorted** file order is kept; a stderr
   message names both FQNs. Resolve by renaming, or use `role_overrides.fqn` /
   `@CodebaseRole` on affected types.
2. **Incremental indexing and annotation sources.** The indexer may only
   reprocess changed files. If you edit an `@interface` declaration (e.g. remove
   a `@Service` meta-annotation from a wrapper), every class that used that
   annotation may need re-enrichment; the pipeline does not track that dependency
   automatically. **When to do a full rebuild:** after changing any
   `@interface` used as a custom stereotype, run a full
   `refresh_code_index(confirm=true)` (or full cocoindex reprocess and rebuild
   Kuzu) so all dependents pick up the new `meta_chain`.

**Kuzu `Symbol` rows (scope):** `role` and `capabilities` on the graph are
computed for **type** nodes (classes, interfaces, etc.). Method and constructor
`Symbol` rows are not passed through the brownfield resolver; they use default
`role=OTHER` and `capabilities=[]`.

On top of role weights, java chunks receive a **symbol-match bonus** (exposed as
`score_components.symbol_bonus`). It has three additive components, all capped:

1. **Method / field overlap** — each declared symbol whose tokens overlap the
   query earns `+0.03` (capped at `+0.06`).
2. **Action-verb bump** — chunks declaring a method whose name begins with an
   action verb (`process`, `handle`, `on`, `pick`, `select`, `assign`, `notify`,
   `dispatch`, `publish`, `consume`, `route`, `trigger`, `enqueue`,
   `distribute`, ...) get a flat `+0.02`.
3. **Type-name overlap** — the strongest single lexical signal: when the simple
   name of `primary_type_fqn` (e.g. `DistributionChunkService`,
   `OperatorSessionService`, `JoinOperatorController`) shares tokens with the
   query, each overlap hit earns `+0.05` (capped at `+0.10`). Class naming in
   this codebase encodes the domain concept, so this pulls the "right class"
   above chunks that merely mention the concept in a comment or enqueue path.

Combined, these pull `processClientMessage` / `pickEligibleOperator` /
`onOperatorAssigned` chunks — and the classes that own them — above ones that
only enqueue or configure. Like role weights, the bonus is **skipped when the
caller locks `role=`**.

### Ignore patterns

Java file discovery for the Kuzu graph, annotation meta-chain collection, and
the CocoIndex Lance pipeline share the same layered ignore model
(`path_filtering.LayeredIgnore`):

1. **Builtin default** — the historical hardcoded list (build dirs, `*.class`,
   `src/test/java`, dot-directories, …).
2. **Project root** — optional `<project>/.lancedb-mcp/ignore` (gitignore syntax,
   including negation with `!`).
3. **Nested** — any `<subdir>/.lancedb-mcp/ignore` on the path from the project
   root to the file; closer files override farther ones.
4. **Git** — every `.gitignore` from the project root down to the file’s
   directory, merged in order, using `pathspec.GitIgnoreSpec` (same semantics as
   git). Disable with `LayeredIgnore(..., use_gitignore=False)` (used where the
   legacy walker did not consult git).

If no `.lancedb-mcp/ignore` exists anywhere under the project, behaviour matches
the pre-B5 builtin list alone (plus git when enabled). When a negation rule
could un-ignore paths under directories the CocoIndex walk used to prune
globally, the walk switches to a permissive exclude list and each candidate
path is filtered again with the full layered rules.

Use the `diagnose_ignore` MCP tool (or `LayeredIgnore.diagnose_dict`) to see
which file and line decided for a given path.

**Monorepo note:** negation detection runs two full-tree ``rglob`` passes when
constructing a `LayeredIgnore` (ignore files and `.gitignore` files). That is
usually cheap to amortise; extremely large trees should expect that fixed cost
per new instance.

**Dependencies:** `pathspec` is pinned in `requirements.txt` and constrained
the same way in `pyproject.toml` (loose bundle install vs. wheel metadata).

### Debugging empty `context_before` / `context_after`

If `context_neighbors=1` returns empty context strings, set
`LANCEDB_MCP_DEBUG_CONTEXT=1` in the MCP server env before launching. The
server then logs (to stderr) why expansion bailed: missing schema columns,
empty bucket scan, chunk not found in bucket, or underlying scan error.
Typical causes are (a) a stale server that hasn't reloaded after a reindex,
or (b) a legacy index without `range_start` / `range_end` — the code falls
back to exact-text matching in that case, so re-running the flow fixes it.

## 6. Deferred (beyond static call graph)

**Static intra-JVM `CALLS` / `DECLARES` are shipped** — see §5 edge types
and `find_callers` / `find_callees` / `trace_flow(follow_calls)`.
**Cross-service caller edges (`HTTP_CALLS` / `ASYNC_CALLS`) are shipped
too**, with `find_route_callers`, `trace_request_flow`, and the
brownfield composition layer documented under §5 "Brownfield overrides".
Remaining graph work:

- `get_service_topology` (microservice-level summary view aggregating `HTTP_CALLS` / `ASYNC_CALLS`).
- Agentic routing layer (query classifier → vector / graph / both) from the DKB paper §4.1.
- Incremental Kuzu updates (per-changed-file) to avoid full rebuild —
  see `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md` and
  `propose/REFRESH-CODE-INDEX-AUTO-MODE-PROPOSE.md`.
- Optional `codegraph_nodes` LanceDB table embedding symbol summaries so the graph itself is vector-searchable.

## 7. Syncing from the main repo

If you develop in `chat-test`, copy these files into `mcp_lancedb_bundle/` when you change behavior:

- `chunk_heuristics.py`
- `ast_java.py`
- `java_ontology.py`
- `graph_enrich.py`
- `kuzu_queries.py`
- `build_ast_graph.py`
- `search_lancedb.py` (switch imports to `index_common` as in this bundle)
- `server.py` (from `mcp_lancedb_server.py`, with bundle imports)

`index_common.py` stays bundle-specific (no CocoIndex import).
