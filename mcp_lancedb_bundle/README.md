# LanceDB code search MCP (export bundle)

Self-contained **stdio MCP server** for semantic search over a LanceDB index (Java / SQL / YAML) produced by CocoIndex `java_index_flow_lancedb.py`.

The bundle also includes an **optional Java AST property graph** stored in embedded **[Kuzu](https://kuzudb.com/)** (Tree-sitter–based, deterministic). Vector search covers natural-language and local semantic similarity; the graph answers structural questions (inheritance, `implements`, Spring-style `INJECTS` heuristics) and can be **fused** with vector hits via **reciprocal rank fusion (RRF)**. Implementation lives in `java_ast_graph/`. CocoIndex 1.0 here does not ship a Kuzu target; the graph is built with the `kuzu` Python package (no Docker).

**No `cocoindex` Python package is required to run search or MCP** — only `sentence-transformers`, `lancedb`, and `mcp`. CocoIndex is optional and only needed if you use the `refresh_code_index` tool. Graph extras (`kuzu`, `tree-sitter`, `tree-sitter-java`) are listed in `requirements.txt`.

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
| `KUZU_DB_PATH` | Path for the embedded Kuzu database. Default: `./kuzu_java_graph` (created on build). |
| `GRAPH_SOURCE_ROOTS` | Comma-separated repository roots to scan (e.g. `chatx-core` and `chat-assign`). If unset, a single root is taken from `LANCEDB_MCP_PROJECT_ROOT`, or the current working directory. |
| `GRAPH_BUILD_ON_REFRESH` | When `1` / `true` / `yes`, a successful `refresh_code_index` (MCP) also runs `python -m java_ast_graph.build` after CocoIndex. Requires `LANCEDB_MCP_ALLOW_REFRESH=1` and `confirm=true` on the vector refresh. |

## 3. Java AST graph (Kuzu)

### What gets indexed

- **Node tables**: `File`, `Package`, `Type` (class / interface / enum / record / annotation), `Method`.
- **Relationship types**: `F_DECLARED_IN` (Type → File), `T_IN_PACKAGE` (Type → Package), `T_EXTENDS`, `T_IMPLEMENTS`, `T_INJECTS` (heuristic DI from `@Autowired` fields and constructor parameters when types resolve), `M_DECLARED` (Type → Method).
- **Excludes**: same path intent as the Lance index—hidden dirs, `node_modules`, `target`, `build`, `.git` (see `iter_java_files` in `java_ast_graph/paths.py` and `java_index_v1_common.COMMON_EXCLUDED_PATH_PATTERNS`).

Each build is currently a **full rebuild** of the Kuzu file (suitable for prototype and mid-sized trees).

### Build the graph (CLI)

Run from a directory where `java_ast_graph` is importable (e.g. `mcp_lancedb_bundle` with `PYTHONPATH=.`); set roots if you have more than one module:

```bash
export GRAPH_SOURCE_ROOTS="/path/to/chatx-core,/path/to/chat-assign"
export KUZU_DB_PATH=./kuzu_java_graph
python -m java_ast_graph.build
```

Options:

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

Then set env vars in `.mcp.json` or your shell profile as needed (`LANCEDB_URI`, `KUZU_DB_PATH` if using the graph, etc.).

Official docs: [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings) (see MCP / `.mcp.json`).

## 5. Claude Desktop

Edit `claude_desktop_config.json` (e.g. macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) and add an entry under `mcpServers` with the same `command`, `args`, and `env` as in `mcp.json.example`.

## 6. Manual test

Vector search:

```bash
LANCEDB_URI=/path/to/lancedb_data .venv/bin/python search_lancedb.py "rate limit" --table java --limit 2
```

## 7. Syncing from the main repo

If you develop in `chat-test`, copy these files into `mcp_lancedb_bundle/` when you change behavior:

- `chunk_heuristics.py`
- `search_lancedb.py` (switch imports to `index_common` as in this bundle)
- `server.py` (from `mcp_lancedb_server.py`, with bundle imports)
- `java_ast_graph/` (and related tests/fixtures) when graph behavior changes

`index_common.py` stays bundle-specific (no CocoIndex import).
