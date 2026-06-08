# Migrate from KuzuDB to LadybugDB

## Status
Proposal — not yet implemented.

## Problem Statement

KuzuDB ([kuzudb/kuzu](https://github.com/kuzudb/kuzu)) was archived in October 2025 after Apple acquired Kuzu Inc. The repository is frozen at v0.11.3 with no future releases, bug fixes, or support. The extension server is shut down. This project depends on `kuzu>=0.11.3,<0.12` for its graph storage layer — any Kuzu bug encountered from this point forward is permanent and unfixable.

LadybugDB ([LadybugDB/ladybug](https://github.com/LadybugDB/ladybug)) is the direct successor: same core team's fork, MIT licensed, actively developed (v0.17.1 as of June 2026), with identical Cypher query language and a Python API that is a mechanical rename of Kuzu's (`import ladybug` / `ladybug.Database` / `ladybug.Connection`). The v0.12.0 release explicitly stated "functionally equivalent to v0.11.3 except for name change."

## Proposed Solution

Replace KuzuDB with LadybugDB across the entire codebase in a single PR. The migration is purely mechanical — verified by installing LadybugDB v0.17.1 and testing every query pattern used by this project:

- `ladybug.Database(path)` / `ladybug.Database(path, read_only=True)` — same constructor
- `ladybug.Connection(db)` — identical
- `conn.execute(query)`, `conn.execute(query, params)` — identical
- `r.get_column_names()`, `r.has_next()`, `r.get_next()` — identical
- `label()`, `MATCH`, `CREATE`, `DETACH DELETE`, rel table DDL — all identical
- No `conn.prepare()` usage exists in the codebase (the only deprecated LadybugDB pattern)

**MAP-as-STRING workaround preserved**: LadybugDB's Python binder has the same limitation as Kuzu's — `dict` values are rejected for `MAP` columns. The existing pattern of storing map-shaped `graph_meta` data as `STRING` JSON blobs and decoding in `ladybug_queries.meta()` is preserved unchanged.

### Concrete changes

1. **`pyproject.toml`**: Replace `kuzu>=0.11.3,<0.12` with `ladybug>=0.17.1,<0.18`, rename keyword `"kuzu"` → `"ladybug"`, rename `py-modules` entry `"kuzu_queries"` → `"ladybug_queries"`
2. **`kuzu_queries.py` → `ladybug_queries.py`**: Rename file, `KuzuGraph` → `LadybugGraph`, `resolve_kuzu_path` → `resolve_ladybug_path`, `import kuzu` → `import ladybug`
3. **`build_ast_graph.py`**: `import kuzu` → `import ladybug`, all `kuzu.Database`/`kuzu.Connection` → `ladybug.Database`/`ladybug.Connection`, `code_graph.kuzu` → `code_graph.lbug`, `write_kuzu()` → `write_ladybug()`
4. **`java_ontology.py`**: Rename `EdgeAttr` field `kuzu_type` → `graph_type`
5. **`scripts/generate_edge_navigation.py`**: Update `attr.kuzu_type` → `attr.graph_type`
6. **`java_codebase_rag/config.py`**: Rename `kuzu_path` dataclass field → `ladybug_path`, update `code_graph.kuzu` → `code_graph.lbug` path construction, remove `KUZU_DB_PATH` from legacy env hints
7. **`java_codebase_rag/cli.py`**: Rename `kuzu_path` references, `_emit_increment_kuzu_warning()` → `_emit_increment_ladybug_warning()`, update all `from kuzu_queries import KuzuGraph` → `from ladybug_queries import LadybugGraph`, rename payload key `"kuzu_path"` → `"ladybug_path"`
8. **`java_codebase_rag/pipeline.py`**: Rename `kuzu_path` parameter → `ladybug_path`, update `--kuzu-path` subprocess arg → `--ladybug-path`
9. **`java_codebase_rag/installer.py`**: Update `kuzu_path=cfg.kuzu_path` → `ladybug_path=cfg.ladybug_path`
10. **CLI args**: `--kuzu-path` → `--ladybug-path` across `build_ast_graph.py`, `search_lancedb.py`, `server.py`
11. **All consumers**: Update `from kuzu_queries import` → `from ladybug_queries import` in `search_lancedb.py`, `server.py`, `pr_analysis.py`, `mcp_v2.py`
12. **Tests**: Same mechanical rename in `test_kuzu_queries.py` → `test_ladybug_queries.py`, `conftest.py` fixture names (`kuzu_graph` → `ladybug_graph`, `kuzu_db_path` → `ladybug_db_path`), and ~25 additional test files
13. **Docs**: Update `README.md`, `AGENTS.md`, `docs/CONFIGURATION.md`, `docs/JAVA-CODEBASE-RAG-CLI.md`, `docs/PRODUCT-VISION.md` (16 refs), `docs/CODEBASE_REQUIREMENTS.md` (10 refs), `docs/MANUAL-VERIFICATION-CHECKLIST.md` (3 refs), `tests/README.md`
14. **Database format**: Default path changes from `code_graph.kuzu` to `code_graph.lbug`. When encountering an old `.kuzu` file, the tool will force a rebuild (storage formats diverged across 4 minor versions since the fork). Extension `.lbug` is short, unique, and avoids collision with existing file types.

### Files affected

| File | Change type |
|---|---|
| `pyproject.toml` | Dependency swap + keyword + py-modules entry |
| `kuzu_queries.py` → `ladybug_queries.py` | Rename + import swap + class rename |
| `build_ast_graph.py` | Import swap + class refs + path defaults + `write_kuzu` rename |
| `java_ontology.py` | Field rename `kuzu_type` → `graph_type` |
| `scripts/generate_edge_navigation.py` | Update `attr.kuzu_type` → `attr.graph_type` |
| `java_codebase_rag/config.py` | Dataclass field rename + path string + legacy env hint |
| `java_codebase_rag/cli.py` | Field/function/import renames + payload key |
| `java_codebase_rag/pipeline.py` | Parameter rename + subprocess arg |
| `java_codebase_rag/installer.py` | Parameter access rename |
| `search_lancedb.py` | Import swap + CLI arg rename |
| `server.py` | Import swap + CLI arg rename |
| `pr_analysis.py` | Import swap |
| `mcp_v2.py` | Import swap |
| `tests/test_kuzu_queries.py` → `tests/test_ladybug_queries.py` | Rename + import swap |
| `tests/conftest.py` | Fixture renames (`kuzu_graph`, `kuzu_db_path` → `ladybug_*`) |
| `tests/_builders.py`, `tests/pinned_ids.py` | Import swap |
| ~25 additional test files | Import swap + CLI arg rename + fixture name updates |
| `AGENTS.md` | Kuzu Cypher pitfalls section + validation commands + file map |
| `README.md` | All Kuzu references |
| `docs/PRODUCT-VISION.md` | 16 kuzu references |
| `docs/CONFIGURATION.md` | Graph layer refs |
| `docs/CODEBASE_REQUIREMENTS.md` | 10 kuzu references |
| `docs/JAVA-CODEBASE-RAG-CLI.md` | CLI arg refs |
| `docs/MANUAL-VERIFICATION-CHECKLIST.md` | `--kuzu-path` refs |
| `tests/README.md` | Fixture refs |

## Scope

### In scope

- Swap `kuzu` dependency for `ladybug` in `pyproject.toml`
- Rename `kuzu_queries.py` → `ladybug_queries.py`, class `KuzuGraph` → `LadybugGraph`
- Rename all `import kuzu` → `import ladybug`, `kuzu.Database` → `ladybug.Database`, `kuzu.Connection` → `ladybug.Connection`
- Rename `java_ontology.py` field `kuzu_type` → `graph_type`
- Rename `java_codebase_rag/` package fields and functions: `kuzu_path` → `ladybug_path`, `write_kuzu` → `write_ladybug`, `_emit_increment_kuzu_warning` → `_emit_increment_ladybug_warning`
- Rename CLI args `--kuzu-path` → `--ladybug-path`
- Rename default database file from `code_graph.kuzu` → `code_graph.lbug`
- Update all `from kuzu_queries import` → `from ladybug_queries import`
- Rename test file `test_kuzu_queries.py` → `test_ladybug_queries.py`
- Rename conftest.py fixtures (`kuzu_graph`, `kuzu_db_path` → `ladybug_*`)
- Remove `KUZU_DB_PATH` from legacy env hints
- Update all doc references to Kuzu → LadybugDB
- Force rebuild when old `.kuzu` databases are detected

### Out of scope

- LadybugDB-specific features (DuckDB foreign tables, Parquet storage, `CREATE GRAPH`, `PROPERTIES(NODES(p), 'prop')` helper, multiple labels per node)
- Schema or ontology changes
- Query optimization using LadybugDB's new optimizers (CountRelTableOptimizer, ForeignJoinPushDownOptimizer)
- Removing the MAP-as-STRING workaround (LadybugDB has the same `dict` limitation for `MAP` columns)
- `langchain-ladybug` integration
- CocoIndex Ladybug target integration
- Performance benchmarking (LadybugDB vs Kuzu) — assumed equivalent or better

## Schema / Ontology / Re-index impact

- **Ontology bump**: Not required. No enrichment semantics change — the graph schema (node/rel tables, properties, edge types) remains identical. Only the storage engine changes.
- **Re-index required**: **Yes.** The storage format has diverged across 4+ LadybugDB minor versions since the Kuzu v0.11.3 fork. Existing `code_graph.kuzu` databases cannot be opened by LadybugDB. Users must run `reprocess` (or the builder will auto-detect and force a rebuild).
- **Config/tool surface changes**:
  - `--kuzu-path` CLI arg → `--ladybug-path`
  - `JAVA_CODEBASE_RAG_INDEX_DIR/code_graph.kuzu` → `code_graph.lbug`
  - `pyproject.toml` dependency change
  - `kuzu_queries` module → `ladybug_queries`
  - `KUZU_DB_PATH` legacy env hint removed

## Tests / Validation

1. **Existing test suite passes** — All tests in `tests/` that currently exercise Kuzu must pass with LadybugDB after the rename. No test logic changes needed (only import/reference renames).
2. **Graph builder smoke test** — `build_ast_graph.py --source-root tests/bank-chat-system` produces a valid `.lbug` database with identical edge counts to the previous `.kuzu` build.
3. **MCP server smoke test** — `search`, `find`, `describe`, `neighbors`, `resolve` all return correct results against the rebuilt `.lbug` database.
4. **Stale database detection** — Verify the tool detects old `.kuzu` files and forces a rebuild rather than crashing.

## Open Questions ([TBD])

1. Should `KUZU_PATH` / `--kuzu-path` be kept as a deprecated alias during a transition period, or cleanly removed? — Recommended: **clean removal** (repo allows breaking changes).
2. Should the version pin be `ladybug>=0.17.1,<0.18` (strict) or a wider range? — Recommended: **strict minor pin** matching current Kuzu policy.
3. Should `kuzu_type` in `java_ontology.py` be renamed to `graph_type` (engine-agnostic) or `ladybug_type` (matching new engine)? — Recommended: **`graph_type`** (avoids another rename if the engine changes again).

## Sequencing / Follow-ups

Single PR. No follow-ups required unless LadybugDB-specific features are desired later.
