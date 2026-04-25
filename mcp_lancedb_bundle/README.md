# LanceDB code search MCP (export bundle)

Self-contained **stdio MCP server** for semantic search over a LanceDB index (Java / SQL / YAML) produced by CocoIndex `java_index_flow_lancedb.py`, *plus* a deterministic AST-derived graph (Kuzu sidecar) for structural code queries.

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

From the repo root (if not using the bundle venv), `python -m pip install -r mcp_lancedb_bundle/requirements.txt` installs the same dependencies, including graph libraries.

## 2. Environment

| Variable | Purpose |
|----------|---------|
| `LANCEDB_URI` | **Required for real use:** absolute path to the `lancedb_data` directory (or remote LanceDB URI). |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. |
| `LANCEDB_MCP_PROJECT_ROOT` | Repo root containing `java_index_flow_lancedb.py` (for `refresh_code_index`). Defaults to this bundle directory. Also the default single source root for the graph when `GRAPH_SOURCE_ROOTS` is unset; used by the server to resolve the project and read snippets. |
| `LANCEDB_MCP_ALLOW_REFRESH` | Set to `1` to enable the heavy `refresh_code_index` tool. |
| `KUZU_DB_PATH` | Absolute path to the Kuzu graph DB. Defaults to `${LANCEDB_URI}/code_graph.kuzu`. |
| `LANCEDB_MCP_GRAPH_ENABLED` | `1`/`0` to force on/off; auto-on when the Kuzu DB exists. |
| `LANCEDB_MCP_MICROSERVICE_ROOTS` | Optional comma-separated directory names that should be treated as microservice roots (overrides structural inference). Same effect as listing them under `microservice_roots:` in `.lancedb-mcp.yml` at the project root. |

```bash
python -m java_ast_graph.build --db /path/to/my_graph
python -m java_ast_graph.build --quiet
```

Typical workflow with vectors:

1. `cocoindex update java_index_flow_lancedb.py:JavaCodeIndexLance` (or your project’s `ccc` / MCP `refresh_code_index` for vectors).
2. `python -m java_ast_graph.build` (graph).

### Tests

```bash
cd mcp_lancedb_bundle
PYTHONPATH=. python -m unittest tests.test_java_ast_graph -v
```

A small Java fixture under `tests/fixtures/ast_sample/` is used for parse and Kuzu load checks.

### MCP tools

Core tools (always): `codebase_search`, `list_code_index_tables`, `refresh_code_index`.

If `KUZU_DB_PATH` exists and `java_ast_graph` imports successfully, these are **additional**:

| Tool | Description |
|------|-------------|
| `list_code_index_tables` | Also reports `kuzu_db_path`, `kuzu_db_exists`, and `graph_build_on_refresh`. |
| `graph_implementors` | Types that `T_IMPLEMENTS` a given **interface** FQN. |
| `graph_injectors` | Upstream types with `T_INJECTS` into a **target** type FQN. |
| `graph_expand_from_type_seed` | Seed by **type name substring** (no vector step), then bidirectional expansion over `T_EXTENDS` / `T_IMPLEMENTS` / `T_INJECTS` (configurable depth/limit). |
| `graph_match` | **Read-only** Cypher: query must start with `MATCH` and must not contain dangerous substrings (e.g. `DELETE`, `DROP`, `CREATE`, `MERGE`, `SET`). |
| `codebase_vector_graph` | Vector + Kuzu graph (DKB): vector top-k, graph seeds from the **query + optional chunk text**, bidirectional structural expansion (default depth 2), optional **interface–consumer** pass (implementors + injectors), then RRF with vector chunks. Not the same as `codebase_search` with `hybrid=true` (that is vector+FTS). |

`refresh_code_index` may append graph build output fields (`graph_exit_code`, `graph_stdout`, `graph_stderr`) when `GRAPH_BUILD_ON_REFRESH` is enabled.

### Vector + graph RRF

`java_ast_graph.hybrid_rrf.fuse_vector_and_graph` implements standard RRF over two ranked lists (vector vs graph-derived rows). The MCP `codebase_vector_graph` tool ties this to `search_lancedb.run_search` and `graph_retriever` (`collect_graph_seeds`, `expand_neighbors_bidirectional`, `expand_interface_consumers`). Tune `vector_limit` / `limit`, `graph_depth`, `graph_limit`, `snippet_max_bytes`, `max_vector_text_chars`, and `include_chunk_seeds` / `interface_expansion` for cost vs. context. For **vector + full-text** (FTS) on one table, use `codebase_search` with `hybrid=true`, not this tool.

### Troubleshooting and tuning

- **`codebase_search` + `hybrid=true`:** The tool returns `hybrid_attempted` and `hybrid_used` (single-table only). If `hybrid_used` is false while `hybrid_attempted` is true, the run is **vector-only** (Lance may lack an inverted FTS on `text` or hybrid search failed). In that case `message` is prefixed with `HYBRID_FALLBACK:` and explains the error. A successful `create_fts_index` on the `text` column (triggered on demand from `search_lancedb.py` when the backend allows it) is required for true vector+FTS RRF.
- **`codebase_vector_graph` response size:** Defaults favor smaller agent payloads (`graph_limit` 28, 2000 chars/bytes for vector text and file snippets). Raise `graph_limit` or the snippet caps when you need more surrounding code.

### Roadmap and references

- **Not in scope (v1):** incremental Kuzu updates; full static `CALLS` / Feign-Kafka-style edges; CocoIndex-native Kuzu export (revisit when upstream ships a Kuzu target—extractors in `java_ast_graph` should stay portable).
- Internal research summary: `ast_graph_rag_java.md` (AST GraphRAG, DKB-style two-pass extraction, RRF, routing).
- Vector index: `java_index_flow_lancedb.py`, `search_lancedb.py`, `.cursorrules`.

## 4. Claude Code

**Project scope:** copy `mcp.json.example` to your repo as `.mcp.json`, replace absolute paths, merge with existing `mcpServers` if any.

Or use the CLI:

```bash
claude mcp add --transport stdio lancedb-code -- \
  /path/to/mcp_lancedb_bundle/.venv/bin/python \
  /path/to/mcp_lancedb_bundle/server.py
```

Then set env vars in `.mcp.json` or your shell profile as needed (`LANCEDB_URI`, `KUZU_DB_PATH`, etc.).

Official docs: [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings) (see MCP / `.mcp.json`).

## 5. Claude Desktop

Edit `claude_desktop_config.json` (e.g. macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) and add an entry under `mcpServers` with the same `command`, `args`, and `env` as in `mcp.json.example`.

## 5. AST Graph layer (Kuzu)

A sidecar deterministic graph derived from Tree-sitter Java parsing lives next to the LanceDB files (default `${LANCEDB_URI}/code_graph.kuzu`).

**Node types:** `package`, `file`, `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`. Unresolved targets become **phantom** nodes (`resolved=false`, FQN guessed from imports / `java.lang`).

**Edge types (Phase 1):** `EXTENDS`, `IMPLEMENTS`, `INJECTS`. Injection mechanisms detected:
- field `@Autowired` / `@Inject` / `@Resource`
- constructor injection (Spring single-ctor rule and explicit `@Autowired`)
- setter `@Autowired`
- Lombok `@RequiredArgsConstructor` (final fields) and `@AllArgsConstructor` (all non-static)

**Java chunk rows are enriched** with `package`, `module`, `microservice`, `primary_type_fqn`, `primary_type_kind`, `role`, `annotations_on_type`, `symbols`, `ontology_version`. `role` is inferred from stereotype annotations (`@RestController`, `@Service`, `@Repository`, `@Component`, `@Configuration`, `@Entity`, `@FeignClient`, `@Mapper`).

**Two location fields are tracked per Java symbol / chunk:**

- `module` — the *innermost* build-marker (`pom.xml`, `build.gradle`, `build.gradle.kts`, `build.sbt`) ancestor's directory name. This is the legacy `service` field, renamed.
- `microservice` — the *outermost* build-marker ancestor under `LANCEDB_MCP_PROJECT_ROOT`. For a single-module project both equal the same name; for a multi-module reactor (e.g. `chat-core/{chat-app,chat-engine,...}`) every child module collapses to `microservice='chat-core'` while keeping its own `module='chat-app'` etc.

Resolution order for `microservice`:
1. explicit override list — `LANCEDB_MCP_MICROSERVICE_ROOTS=foo,bar` env var or
   `microservice_roots: [foo, bar]` in `.lancedb-mcp.yml` at the project root;
2. outermost build marker between `project_root` and the file;
3. first path segment under `project_root`;
4. `""` if nothing matches.

> **Breaking change.** This release replaces the single `service` field with `module` + `microservice` and bumps `ONTOLOGY_VERSION` to `2`. Any existing index built before this change must be rebuilt (`refresh_code_index` or `cocoindex update --full-reprocess -f`). The old `service=...` filter on `codebase_search` and the graph tools no longer exists. Use `microservice=...` (most common) or `module=...` (for multi-module reactors). Use `list_code_index_tables` / `graph_meta` to discover canonical names — both now expose `module_counts` and `microservice_counts`.

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
- `graph_enrich.py`
- `kuzu_queries.py`
- `build_ast_graph.py`
- `search_lancedb.py` (switch imports to `index_common` as in this bundle)
- `server.py` (from `mcp_lancedb_server.py`, with bundle imports)
- `java_ast_graph/` (and related tests/fixtures) when graph behavior changes

`index_common.py` stays bundle-specific (no CocoIndex import).
