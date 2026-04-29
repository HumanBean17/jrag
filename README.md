# LanceDB code search MCP (export bundle)

Self-contained **stdio MCP server** for semantic search over a LanceDB index (Java / SQL / YAML) produced by CocoIndex `java_index_flow_lancedb.py`, *plus* a deterministic AST-derived graph (Kuzu sidecar) for structural code queries.

The product vision for this tooling is proposed in [`propose/PRODUCT-VISION.md`](./propose/PRODUCT-VISION.md).

**No `cocoindex` Python package is required to run search or MCP** — only `sentence-transformers`, `lancedb`, `kuzu`, `tree_sitter` + `tree_sitter_java`, and `mcp`. CocoIndex is optional and only needed if you use the `refresh_code_index` tool.

> **Tuning for your codebase:** see [`CODEBASE_REQUIREMENTS.md`](./CODEBASE_REQUIREMENTS.md)
> for the assumptions this MCP makes about a Java repo (annotations, DI patterns,
> service layout, naming) and a per-file map of where to edit the bundle if you
> can't or don't want to refactor your codebase to match.

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
| `LANCEDB_MCP_PROJECT_ROOT` | Repo root containing `java_index_flow_lancedb.py` (for `refresh_code_index`). Defaults to this bundle directory. |
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

**Edge types (Phase 1):** `EXTENDS`, `IMPLEMENTS`, `INJECTS`. Injection mechanisms detected:
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

The DB is dropped and rebuilt from scratch on each run (Phase 1 is a full rebuild; incremental updates are future work).

### Tools exposed by the server

| Tool | Purpose |
|------|---------|
| `codebase_search` | Vector / hybrid / graph-expanded search. Supports `role`, `module`, `microservice`, `package_prefix` filters, `graph_expand=true` + `expand_depth=1..3` for Kuzu-BFS fusion (RRF), and `context_neighbors=1..2` to attach adjacent chunks as `context_before`/`context_after`. Java hits return `score_components` (`distance`, `hybrid_rrf`, `role_weight`, `symbol_bonus`, `import_penalty`) so callers can see why a row ranked where it did. |
| `trace_flow` | Behavioural trace from a natural-language query. Seeds via vector search, then walks CONTROLLER -> SERVICE/COMPONENT -> FEIGN_CLIENT/REPOSITORY/MAPPER in the Kuzu graph and returns staged chains. |
| `list_code_index_tables` | Lance tables + Kuzu graph metadata. |
| `refresh_code_index` | Rebuild LanceDB + Kuzu graph. |
| `find_implementors` | Classes implementing an interface. |
| `find_subclasses` | Types extending a class/interface. |
| `find_injectors` | Classes that inject the given type, incl. mechanism/annotation/field. |
| `list_by_role` | Symbols with a given role (CONTROLLER, SERVICE, ...). |
| `list_by_annotation` | Symbols whose annotation list contains the given simple name. |
| `graph_neighbors` | Generic BFS over `EXTENDS|IMPLEMENTS|INJECTS`, directional. |
| `impact_analysis` | Reverse closure: what breaks if this changes. |
| `graph_meta` | Counts, ontology version, build timestamp, parse errors. |

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
| `FEIGN_CLIENT` | +0.06 |
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
**2. Meta-annotation walk (automatic)** — `@interface` definitions in your
source can carry meta-annotations; Layer A resolves chains to built-in
stereotype and capability trigger names (e.g. `@Service`, `@KafkaListener`)
via `graph_enrich.collect_annotation_meta_chain` (single index for both
Kuzu and Lance — see below). **3. Last resort — source stubs** — copy the following
into your project (any package) and add `@CodebaseRole(CodebaseRoleKind.SERVICE)` /
`@CodebaseCapability(CodebaseCapabilityKind.MESSAGE_LISTENER)` on a class. Matched by **simple
name only** (no Maven dependency on this bundle):

```java
package com.example.rag; // any package

import java.lang.annotation.*;

public enum CodebaseRoleKind {
    CONTROLLER, SERVICE, REPOSITORY, COMPONENT, CONFIG, ENTITY, FEIGN_CLIENT, MAPPER, DTO
}

public enum CodebaseCapabilityKind {
    MESSAGE_LISTENER, MESSAGE_PRODUCER, SCHEDULED_TASK, EXCEPTION_HANDLER
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

Legacy string-literal forms (`@CodebaseRole("SERVICE")`,
`@CodebaseCapability("MESSAGE_LISTENER")`) are a breaking change and are no
longer applied by the resolver.

Resolution order in code: built-in inference, then config annotation maps,
then meta-annotation walk, then `@CodebaseRole` / `@CodebaseCapability`, then
`role_overrides.fqn` (highest priority for explicit per-type config). Rebuild
Lance + Kuzu (`refresh_code_index` or `build_ast_graph.py`) after changing
overrides.

**Kuzu vs Lance (Layer A consistency):** both the Kuzu graph writer and Lance
chunk enrichment call **one** function, `graph_enrich.collect_annotation_meta_chain`,
which scans the project with sorted `*.java` paths, the same exclude rules as
`build_ast_graph` / `iter_java_source_files`, parse-error warnings on stderr, and
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

### Debugging empty `context_before` / `context_after`

If `context_neighbors=1` returns empty context strings, set
`LANCEDB_MCP_DEBUG_CONTEXT=1` in the MCP server env before launching. The
server then logs (to stderr) why expansion bailed: missing schema columns,
empty bucket scan, chunk not found in bucket, or underlying scan error.
Typical causes are (a) a stale server that hasn't reloaded after a reindex,
or (b) a legacy index without `range_start` / `range_end` — the code falls
back to exact-text matching in that case, so re-running the flow fixes it.

## 6. Deferred (call-graph layer)

Phase 1 intentionally excludes call-graph edges. These are planned follow-ups:

- `CALLS` — method-to-method edges; requires local + cross-type call resolution.
- `HTTP_CALLS` — Feign (`@FeignClient`), `RestTemplate`, `WebClient`.
- `ASYNC_CALLS` — Kafka (`@KafkaListener`), Spring messaging patterns.
- Cross-service topology tools (`get_service_topology`, `trace_request_flow`) depending on the above.
- Agentic routing layer (query classifier → vector / graph / both) from the DKB paper §4.1; meaningful only once CALLS lands.
- Incremental Kuzu updates (per-changed-file) to avoid full rebuild.
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
