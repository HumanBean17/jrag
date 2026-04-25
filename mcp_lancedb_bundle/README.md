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

**Java chunk rows are enriched** with `package`, `service`, `primary_type_fqn`, `primary_type_kind`, `role`, `annotations_on_type`, `symbols`, `ontology_version`. `role` is inferred from stereotype annotations (`@RestController`, `@Service`, `@Repository`, `@Component`, `@Configuration`, `@Entity`, `@FeignClient`, `@Mapper`).

`service` is inferred by walking parents until a build marker (`pom.xml`, `build.gradle`, `build.gradle.kts`, `build.sbt`) is found; its containing directory name is the service.

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
| `codebase_search` | Vector / hybrid / graph-expanded search. Supports `role`, `service`, `package_prefix` filters, `graph_expand=true` + `expand_depth=1..3` for Kuzu-BFS fusion (RRF), and `context_neighbors=1..2` to attach adjacent chunks as `context_before`/`context_after`. Java hits return `score_components` (`distance`, `hybrid_rrf`, `role_weight`, `symbol_bonus`, `import_penalty`) so callers can see why a row ranked where it did. |
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

`index_common.py` stays bundle-specific (no CocoIndex import).
