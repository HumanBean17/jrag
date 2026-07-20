<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Plan: Migrate from KuzuDB to LadybugDB

Status: **active (planning)**. This plan implements
[`propose/active/LADYBUG-DB-MIGRATE-PROPOSE.md`](../../propose/active/LADYBUG-DB-MIGRATE-PROPOSE.md)
as a single PR. This file is plan-only and does not implement code.

Depends on: none.

## Goal

- Replace the `kuzu` Python dependency with `ladybug` (the maintained fork by the same core team).
- Rename all code symbols (`KuzuGraph` → `LadybugGraph`, `kuzu_queries` → `ladybug_queries`, `kuzu_path` → `ladybug_path`, `kuzu_type` → `graph_type`) so no orphan "kuzu" identifiers remain in production code or tests.
- Change the default database file extension from `code_graph.kuzu` to `code_graph.lbug`.
- Ensure the full test suite passes after the rename.
- Update all documentation to reflect the new engine name.

## Principles (do not relitigate in review)

- **Single PR, purely mechanical rename.** No logic changes, no new features, no query optimization. Every change is `s/kuzu/ladybug/` at the API surface or `s/kuzu_type/graph_type/` in `java_ontology.py`.
- **No compatibility shim.** Clean break — no `--kuzu-path` alias, no `KuzuGraph` alias, no `KUZU_DB_PATH` env hint. The repo allows breaking changes.
- **Strict dependency pin.** `ladybug>=0.17.1,<0.18` mirrors the existing `kuzu>=0.11.3,<0.12` policy.
- **Force rebuild on old `.kuzu` files.** Storage formats diverged across 4+ LadybugDB minor versions since the fork. The builder must detect old `code_graph.kuzu` and force a full rebuild rather than crash.
- **`kuzu_type` → `graph_type` (engine-agnostic).** Avoids another rename if the engine changes again. Applied in `java_ontology.py` and `scripts/generate_edge_navigation.py`.
- **MAP-as-STRING workaround preserved.** LadybugDB has the same `dict` limitation for `MAP` columns. The existing JSON-blob pattern in `kuzu_queries.py` (now `ladybug_queries.py`) stays unchanged.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Full mechanical rename: dependency, imports, class/function names, CLI args, config fields, file renames, docs | none | `.venv` must resolve `ladybug` package; storage format incompatibility detection; test fixture rename; doc consistency | full suite + graph builder smoke | n/a (single PR) |

Landing order: **PR-1**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Keep `--kuzu-path` as deprecated alias? | No — clean removal (repo allows breaking changes). |
| Dependency pin range | `ladybug>=0.17.1,<0.18` — strict minor pin matching current Kuzu policy. |
| Rename `kuzu_type` to `ladybug_type` or `graph_type`? | `graph_type` — engine-agnostic, avoids future rename. |
| Database file extension | `.lbug` — short, unique, no collision with existing types. |
| `KUZU_DB_PATH` legacy env hint | Remove from `_LEGACY_ENV_HINTS` in `config.py`. |
| Ontology version bump | Not required — no enrichment semantics change. Only storage engine changes. |
| Re-index required | Yes — storage format diverged. Builder auto-detects old `.kuzu` and forces rebuild. |

---

# PR-1 — Replace KuzuDB with LadybugDB

## File-by-file changes

### 1. `pyproject.toml`
- Replace `kuzu>=0.11.3,<0.12` with `ladybug>=0.17.1,<0.18` in `dependencies`.
- Change `"kuzu"` to `"ladybug"` in `keywords`.
- Change `"kuzu_queries"` to `"ladybug_queries"` in `py-modules`.

### 2. `kuzu_queries.py` → `ladybug_queries.py` (rename file)
- `import kuzu` → `import ladybug`
- `kuzu.Database(db_path, read_only=True)` → `ladybug.Database(db_path, read_only=True)`
- `kuzu.Connection(self._db)` → `ladybug.Connection(self._db)`
- Class `KuzuGraph` → `LadybugGraph`
- Function `resolve_kuzu_path` → `resolve_ladybug_path`
- All docstring references to "Kuzu" → "Ladybug" (or engine-agnostic where appropriate)
- `__all__` list: `"KuzuGraph"` → `"LadybugGraph"`, `"resolve_kuzu_path"` → `"resolve_ladybug_path"`
- Default path construction: `code_graph.kuzu` → `code_graph.lbug`
- Comment in docstring about "Kuzu MAP-as-STRING" → "LadybugDB MAP-as-STRING" or keep engine-agnostic

### 3. `build_ast_graph.py`
- `import kuzu` → `import ladybug`
- `kuzu.Database` → `ladybug.Database`
- `kuzu.Connection` → `ladybug.Connection`
- Function `write_kuzu()` → `write_ladybug()`
- CLI arg `--kuzu-path` → `--ladybug-path`
- Variable names: `kuzu_path` → `ladybug_path`
- Default path: `code_graph.kuzu` → `code_graph.lbug`
- All `write_kuzu(...)` call sites → `write_ladybug(...)`
- Docstring/comment references to Kuzu → LadybugDB

### 4. `java_ontology.py`
- Field `kuzu_type: str` in `EdgeAttr` → `graph_type: str`

### 5. `scripts/generate_edge_navigation.py`
- All `attr.kuzu_type` → `attr.graph_type`

### 6. `java_codebase_rag/config.py`
- Dataclass field `kuzu_path: Path` → `ladybug_path: Path` in `ResolvedOperatorConfig`
- Default path construction: `index_dir / "code_graph.kuzu"` → `index_dir / "code_graph.lbug"`
- Remove `("KUZU_DB_PATH", ...)` from `_LEGACY_ENV_HINTS`
- Function `apply_to_os_environ` / `subprocess_env` — no explicit `kuzu` env var set (just path on dataclass), but update any docstrings referencing `kuzu_path`.
- `index_dir_has_existing_artifacts`: `code_graph.kuzu` → `code_graph.lbug`
- `resolve_operator_config`: `ku = index_dir / "code_graph.kuzu"` → `ku = index_dir / "code_graph.lbug"`, `kuzu_path=ku` → `ladybug_path=ku`

### 7. `java_codebase_rag/cli.py`
- `from kuzu_queries import KuzuGraph` → `from ladybug_queries import LadybugGraph`
- `KuzuGraph` → `LadybugGraph` at all call sites
- `cfg.kuzu_path` → `cfg.ladybug_path`
- Function `_emit_increment_kuzu_warning()` → `_emit_increment_ladybug_warning()`
- Constant `KUZU_INCREMENTAL_TRACKING_ISSUE_URL` → `LADYBUG_INCREMENTAL_TRACKING_ISSUE_URL`
- Payload key `"kuzu_path"` → `"ladybug_path"` (if passed to subprocess calls)

### 8. `java_codebase_rag/pipeline.py`
- Parameter `kuzu_path` → `ladybug_path` in all function signatures
- `--kuzu-path` subprocess argument → `--ladybug-path`
- `cfg.kuzu_path` → `cfg.ladybug_path`

### 9. `java_codebase_rag/installer.py`
- `cfg.kuzu_path` → `cfg.ladybug_path`

### 10. `search_lancedb.py`
- `from kuzu_queries import KuzuGraph` → `from ladybug_queries import LadybugGraph`
- `KuzuGraph` → `LadybugGraph`
- `resolve_kuzu_path` → `resolve_ladybug_path`
- CLI arg `--kuzu-path` → `--ladybug-path`
- Variable names: `kuzu_path` → `ladybug_path`

### 11. `server.py`
- `from kuzu_queries import KuzuGraph, resolve_kuzu_path` → `from ladybug_queries import LadybugGraph, resolve_ladybug_path`
- `KuzuGraph` → `LadybugGraph`
- `resolve_kuzu_path` → `resolve_ladybug_path`
- CLI arg `--kuzu-path` → `--ladybug-path`
- Variable names: `kuzu_path` → `ladybug_path`

### 12. `pr_analysis.py`
- `from kuzu_queries import ...` → `from ladybug_queries import ...`

### 13. `mcp_v2.py`
- `from kuzu_queries import KuzuGraph, ...` → `from ladybug_queries import LadybugGraph, ...`
- `KuzuGraph` → `LadybugGraph`

### 14. `ast_java.py`, `graph_enrich.py`, `java_index_flow_lancedb.py` (comment-only changes)
These three source files have comment/docstring references to "Kuzu" (not code-level API calls):
- `ast_java.py:328` — "not a Kuzu column" → "not a graph column"
- `graph_enrich.py:337,353,1705` — "Kuzu writer", "Kuzu and Lance", "Kuzu Symbol nodes" → "LadybugDB writer", "LadybugDB and Lance", "LadybugDB Symbol nodes"
- `java_index_flow_lancedb.py:7` — "Kuzu" in module docstring → "LadybugDB"

### 15. `tests/test_kuzu_queries.py` → `tests/test_ladybug_queries.py` (rename file)
- `import kuzu` → `import ladybug`
- `from kuzu_queries import KuzuGraph` → `from ladybug_queries import LadybugGraph`
- `KuzuGraph` → `LadybugGraph` throughout
- Fixture `kuzu_graph` → `ladybug_graph` references

### 16. `tests/conftest.py`
- All fixtures named `kuzu_*` → `ladybug_*`:
  - `kuzu_db_path` → `ladybug_db_path`
  - `kuzu_graph` → `ladybug_graph`
  - `kuzu_db_path_call_graph_smoke` → `ladybug_db_path_call_graph_smoke`
  - `kuzu_db_path_route_extraction_smoke` → `ladybug_db_path_route_extraction_smoke`
  - `kuzu_graph_route_extraction_smoke` → `ladybug_graph_route_extraction_smoke`
  - `kuzu_db_path_cross_service_smoke` → `ladybug_db_path_cross_service_smoke`
  - `kuzu_db_path_fqn_collision_smoke` → `ladybug_db_path_fqn_collision_smoke`
  - `kuzu_graph_fqn_collision_smoke` → `ladybug_graph_fqn_collision_smoke`
  - `kuzu_db_path_http_caller_smoke` → `ladybug_db_path_http_caller_smoke`
- `import kuzu` → `import ladybug`
- `kuzu.Database` / `kuzu.Connection` → `ladybug.Database` / `ladybug.Connection`
- Path construction: `code_graph.kuzu` → `code_graph.lbug`
- `build_ast_graph.write_kuzu` → `build_ast_graph.write_ladybug`

### 17. `tests/_builders.py`
- `from build_ast_graph import write_kuzu` → `from build_ast_graph import write_ladybug`
- Function `build_kuzu_to` → `build_ladybug_to`
- Function `build_kuzu_into` → `build_ladybug_into`
- Function `build_kuzu_imperative_into` → `build_ladybug_imperative_into`
- Function `build_kuzu_full_into` → `build_ladybug_full_into`
- Docstrings referencing `write_kuzu` → `write_ladybug`

### 18. `tests/pinned_ids.py`
- `from kuzu_queries import ...` → `from ladybug_queries import ...`

### 19. All remaining test files (exhaustive list)
Each test file needs the same mechanical rename pattern:
- `import kuzu` → `import ladybug`
- `kuzu.Database` / `kuzu.Connection` → `ladybug.Database` / `ladybug.Connection`
- `from kuzu_queries import KuzuGraph, ...` → `from ladybug_queries import LadybugGraph, ...`
- `from tests._builders import build_kuzu_*` → `build_ladybug_*`
- `KuzuGraph` → `LadybugGraph`
- `--kuzu-path` → `--ladybug-path` (in CLI test arguments)
- `kuzu_graph` fixture → `ladybug_graph` fixture
- `kuzu_db_path` fixture → `ladybug_db_path` fixture
- `kuzu_path` / `kuzu_db_path` variable names → `ladybug_path` / `ladybug_db_path`
- Path strings: `code_graph.kuzu` / `*.kuzu` → `code_graph.lbug` / `*.lbug`

Complete list of files (verified by `grep -ri "kuzu" --include="*.py" -l tests/`):

| File | Key renames |
| --- | --- |
| `tests/test_config.py` | `code_graph.kuzu` path strings (4 refs) → `code_graph.lbug` |
| `tests/test_cli_quiet_parity.py` | `--kuzu-path` CLI args, `code_graph.kuzu` path strings (~8 refs) |
| `tests/test_incremental_graph.py` | Heavy: `import kuzu`, `kuzu.Database`, `kuzu.Connection`, `write_kuzu`, `code_graph.kuzu`, `kuzu_path` (~80+ refs) |
| `tests/test_mcp_v2_compose.py` | Heavy: `build_kuzu_to`, `build_kuzu_full_into`, `KuzuGraph`, `kuzu_graph` fixture, `g.kuzu` paths (~60+ refs) |
| `tests/test_call_edges_e2e.py` | Heavy: `import kuzu`, `kuzu.Database`, `kuzu.Connection`, `KuzuGraph`, `kuzu_db_path` fixtures, `build_kuzu_full_into`, `g.kuzu` paths (~40+ refs) |
| `tests/test_ast_graph_build.py` | `write_kuzu`, `kuzu_path`, `code_graph.kuzu` |
| `tests/test_call_graph_smoke_roundtrip.py` | `kuzu_graph` fixture, `KuzuGraph` |
| `tests/test_call_graph_receiver_resolution.py` | `import kuzu`, `KuzuGraph`, `kuzu.Database`, `kuzu.Connection` |
| `tests/test_call_invariant.py` | `import kuzu`, `KuzuGraph`, `kuzu.Connection`, `kuzu.Database`, `kuzu_db_path` fixtures |
| `tests/test_mcp_v2.py` | `KuzuGraph`, `kuzu_graph` fixture |
| `tests/test_search_lancedb.py` | `--kuzu-path` CLI args, `KuzuGraph` |
| `tests/test_pr_analysis.py` | `KuzuGraph`, `kuzu_graph` fixture |
| `tests/test_java_codebase_rag_cli.py` | `--kuzu-path` CLI args, `code_graph.kuzu` paths |
| `tests/test_mcp_hints.py` | `kuzu_graph` fixture, `KuzuGraph` |
| `tests/test_bank_chat_brownfield_integration.py` | `import kuzu`, `KuzuGraph`, `kuzu_db_path` fixture |
| `tests/test_brownfield_clients.py` | `build_kuzu_to`, `KuzuGraph`, `g.kuzu` paths |
| `tests/test_brownfield_routes.py` | `kuzu_graph` fixture, `KuzuGraph` |
| `tests/test_client_node_extraction.py` | `import kuzu`, `build_kuzu_full_into`, `kuzu.Database`, `kuzu.Connection`, `g.kuzu` |
| `tests/test_client_hint_recovery.py` | `write_kuzu`, `KuzuGraph`, `client_hints.kuzu` paths |
| `tests/test_feign_not_exposer.py` | `write_kuzu`, `KuzuGraph`, `feign_meta.kuzu` / `legacy_meta.kuzu` paths, `kuzu.Database` |
| `tests/test_assign_endpoint_client_extraction.py` | `import kuzu`, `build_kuzu_imperative_into`, `kuzu.Database`, `kuzu.Connection`, `g.kuzu` |
| `tests/test_cross_service_resolution_flag.py` | `build_kuzu_to`, `KuzuGraph`, `g.kuzu`, `kuzu.Database` |
| `tests/test_client_role_rename.py` | `build_kuzu_to`, `KuzuGraph`, `g.kuzu` |
| `tests/test_lancedb_e2e.py` | `--kuzu-path` CLI args, `code_graph.kuzu` paths |

### 20. `AGENTS.md`
- "Kuzu Cypher pitfalls" section → "LadybugDB Cypher pitfalls"
- `kuzu_queries.py` references → `ladybug_queries.py`
- `KuzuGraph` → `LadybugGraph`
- `code_graph.kuzu` → `code_graph.lbug`
- `--kuzu-path` → `--ladybug-path`
- Validation command: `--kuzu-path` → `--ladybug-path`, `code_graph.kuzu` → `code_graph.lbug`

### 21. `README.md`
- All "Kuzu" / "KuzuDB" references → "LadybugDB" / "Ladybug"
- `kuzu_queries` → `ladybug_queries`
- `code_graph.kuzu` → `code_graph.lbug`
- Stability disclaimer: "Lance/Kuzu schemas" → "Lance/Ladybug schemas"

### 22. `docs/PRODUCT-VISION.md`
- ~16 Kuzu references → LadybugDB

### 23. `docs/CONFIGURATION.md`
- Graph layer references: `code_graph.kuzu` → `code_graph.lbug`
- `kuzu_queries` → `ladybug_queries`

### 24. `docs/CODEBASE_REQUIREMENTS.md`
- ~10 Kuzu references → LadybugDB
- `code_graph.kuzu` → `code_graph.lbug`
- B.9 section: `kuzu_queries.py` → `ladybug_queries.py`

### 25. `docs/JAVA-CODEBASE-RAG-CLI.md`
- `--kuzu-path` → `--ladybug-path`

### 26. `docs/MANUAL-VERIFICATION-CHECKLIST.md`
- `--kuzu-path` → `--ladybug-path`
- `code_graph.kuzu` → `code_graph.lbug`

### 27. `docs/reports/` (3 files)
- `docs/reports/what-to-borrow-from-cmm.md` (4 refs) → LadybugDB
- `docs/reports/call-graph-review.md` (2 refs) → LadybugDB
- `docs/reports/review/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-issues.md` (1 ref) → LadybugDB

### 28. `tests/README.md`
- Fixture name references: `kuzu_graph` → `ladybug_graph`, `kuzu_db_path` → `ladybug_db_path`

## Stale database detection

When the tool encounters an old `code_graph.kuzu` file:
- `resolve_ladybug_path()` in `ladybug_queries.py` constructs a path ending in `code_graph.lbug`.
- If `code_graph.lbug` does not exist but `code_graph.kuzu` does exist, the builder (`build_ast_graph.py`) should detect this and print a clear message that a rebuild is required (storage format incompatible).
- `config.py` `index_dir_has_existing_artifacts()` checks for `code_graph.lbug` — old `.kuzu` files are simply ignored, which means the system treats it as "no graph exists" and forces a rebuild naturally.

No explicit migration code is needed — the rename of the default path effectively forces a rebuild because the old file is no longer found at the new path.

## Tests for PR-1

1. All existing tests in `tests/` pass after the rename (no test logic changes — only import/reference renames).
2. Graph builder smoke: `build_ast_graph.py --source-root tests/bank-chat-system` produces a valid `.lbug` database.
3. MCP tool smoke: `search`, `find`, `describe`, `neighbors`, `resolve` return correct results against the rebuilt `.lbug` database.
4. Stale `.kuzu` detection: old database is not opened (system treats it as absent, forces rebuild).

## Definition of done (PR-1)

- `grep -ri "kuzu" --include="*.py"` returns zero hits in production code and tests.
- `grep -ri "kuzu" --include="*.md" AGENTS.md README.md docs/ tests/README.md` returns zero.
- `docs/reports/` kuzu refs updated (historical archival docs — not in the strict grep scope but included for cleanliness).
- `plans/completed/` and `propose/completed/` are **out of scope** for the grep (archival — do not touch historical plans/proposals).
- `tests/fixtures/perf_baselines.json` "Kuzu" reference in notes string is left as-is (JSON fixture data, not user-facing).
- `.venv/bin/ruff check .` passes.
- `.venv/bin/python -m pytest tests -v` passes without `JAVA_CODEBASE_RAG_RUN_HEAVY`.
- Graph builder smoke produces a valid `.lbug` database from the bank-chat fixture.
- `pyproject.toml` depends on `ladybug>=0.17.1,<0.18` (not `kuzu`).

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Swap dependency in `pyproject.toml` and install | `pyproject.toml` | `.venv/bin/pip install -e .` resolves `ladybug` |
| 2 | Rename `kuzu_queries.py` → `ladybug_queries.py`, swap imports and class names | `ladybug_queries.py` | `LadybugGraph`, `resolve_ladybug_path`, `import ladybug` |
| 3 | Rename `kuzu_type` → `graph_type` in `EdgeAttr` | `java_ontology.py`, `scripts/generate_edge_navigation.py` | `grep -r kuzu_type` returns zero |
| 4 | Update `config.py` — field rename, path, legacy hints | `java_codebase_rag/config.py` | `kuzu_path` field gone, `.lbug` default |
| 5 | Update `cli.py` — imports, field refs, function names | `java_codebase_rag/cli.py` | No `kuzu` references remain |
| 6 | Update `pipeline.py` — parameter and arg renames | `java_codebase_rag/pipeline.py` | `--ladybug-path` in subprocess args |
| 7 | Update `installer.py` — config field access | `java_codebase_rag/installer.py` | `cfg.ladybug_path` |
| 8 | Update `build_ast_graph.py` — import, class, function, CLI arg | `build_ast_graph.py` | `write_ladybug()`, `--ladybug-path` |
| 9 | Update remaining source modules | `search_lancedb.py`, `server.py`, `pr_analysis.py`, `mcp_v2.py` | No `kuzu` references remain |
| 10 | Update comment-only source files | `ast_java.py`, `graph_enrich.py`, `java_index_flow_lancedb.py` | No `kuzu` in comments |
| 11 | Rename and update `test_kuzu_queries.py` | `tests/test_ladybug_queries.py` | File renamed, all test functions pass |
| 12 | Update `conftest.py` — fixture renames | `tests/conftest.py` | All fixtures use `ladybug_` prefix |
| 13 | Update `_builders.py` — function renames | `tests/_builders.py` | `build_ladybug_*`, `write_ladybug` import |
| 14 | Update `pinned_ids.py` | `tests/pinned_ids.py` | Import resolved |
| 15 | Update all remaining 24 test files | See exhaustive table in §19 | No `kuzu` references in `tests/` |
| 16 | Update `AGENTS.md` | `AGENTS.md` | Cypher pitfalls section updated |
| 17 | Update `README.md` | `README.md` | All Kuzu → LadybugDB |
| 18 | Update docs | `docs/PRODUCT-VISION.md`, `docs/CONFIGURATION.md`, `docs/CODEBASE_REQUIREMENTS.md`, `docs/JAVA-CODEBASE-RAG-CLI.md`, `docs/MANUAL-VERIFICATION-CHECKLIST.md`, `docs/reports/`, `tests/README.md` | Grep-clean in scope |
| 19 | Run full test suite | terminal | `.venv/bin/python -m pytest tests -v` green |
| 20 | Run ruff | terminal | `.venv/bin/ruff check .` clean |
| 21 | Graph builder smoke | terminal | `.lbug` database produced |
| 22 | Final grep verification | terminal | Zero kuzu hits in scope |

---

# Cross-PR risks and mitigations

Not applicable — single PR.

# Out of scope

- LadybugDB-specific features (DuckDB foreign tables, Parquet storage, `CREATE GRAPH`, `PROPERTIES(NODES(p), 'prop')` helper, multiple labels per node).
- Schema or ontology changes.
- Query optimization using LadybugDB's new optimizers (CountRelTableOptimizer, ForeignJoinPushDownOptimizer).
- Removing the MAP-as-STRING workaround (LadybugDB has the same `dict` limitation for `MAP` columns).
- `langchain-ladybug` integration.
- CocoIndex Ladybug target integration.
- Performance benchmarking (LadybugDB vs Kuzu).
- Any refactoring beyond the mechanical rename.

# Whole-plan done definition

1. `ladybug` replaces `kuzu` as the graph storage dependency — `pyproject.toml` has `ladybug>=0.17.1,<0.18`.
2. `grep -ri "kuzu" --include="*.py"` returns zero across all `.py` files.
3. `grep -ri "kuzu" --include="*.md" AGENTS.md README.md docs/ tests/README.md` returns zero.
4. `plans/completed/` and `propose/completed/` are explicitly excluded from grep scope (archival).
5. `tests/fixtures/perf_baselines.json` "Kuzu" note left as-is (fixture data).
6. Full test suite passes: `.venv/bin/python -m pytest tests -v`.
7. Graph builder produces a valid `.lbug` database from the bank-chat fixture.
8. Ruff passes: `.venv/bin/ruff check .`.

# Tracking

- `PR-1`: _pending_
