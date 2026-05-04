# AGENTS.md

Entry point for Cursor CLI agents (and other agentic tools) operating
this repo. Detailed guidance lives in `.cursor/rules/*.mdc` — those
files are auto-loaded by Cursor based on globs and `alwaysApply`. This
file is a flat summary for tools that don't read `.cursor/rules/`.

## What this repo is

Self-contained **stdio MCP server** for semantic + structural search
over a Java codebase:

- **LanceDB** vector index (Java / SQL / YAML chunks, `sentence-transformers`).
- **Kuzu** AST graph (Tree-sitter Java, deterministic) with
  `EXTENDS`, `IMPLEMENTS`, `INJECTS`, `DECLARES`, `CALLS` edges.
- **MCP tools** in `server.py`: `codebase_search`, `trace_flow`,
  `find_callers` / `find_callees` / `find_implementors` / `find_subclasses`
  / `find_injectors`, `impact_analysis`, `list_by_role` /
  `list_by_annotation` / `list_by_capability`, `graph_neighbors`,
  `list_code_index_tables`, `graph_meta`, gated `refresh_code_index`.

## Hard rules (read first)

1. **No backward-compatibility obligation.** Prefer removals and
   schema updates over shims (`.cursor/rules/breaking-changes.mdc`).
2. **No overfitting to the test fixture.** `tests/bank-chat-system/`
   is a deterministic corpus, not a model of production. Assert on
   invariants, not exact counts (`.cursor/rules/tests-and-fixtures.mdc`).
3. **MCP server is stdio.** `print()` to stdout breaks the transport.
   All diagnostics go to stderr.
4. **One source of truth for roles and capabilities:** `java_ontology.py`
   + the inference tables in `ast_java.py`. No string literals
   sprinkled elsewhere.
5. **Schema changes require a full reindex.** Update the README
   "Re-index required" block and bump `ontology_version` when
   enrichment semantics change.

## Investigation order

1. `README.md` — feature surface and behaviour.
2. `CODEBASE_REQUIREMENTS.md` — Java-repo assumptions and per-file
   tuning map.
3. `propose/` and `plans/` — designed-but-deferred work and the
   "propose-then-implement" culture.
4. `tests/README.md` — testing philosophy.
5. The relevant `.cursor/rules/*.mdc` for the file you're editing.

## Workflow

- Branch from `master`. Branch names: `cursor/<topic>` (CLI work),
  `plan/<name>` (in-progress propose).
- Commit messages: present tense, imperative, lowercase first word
  (e.g. `fixed call graph review D6`).
- Always open a PR; never push to `master`.
- Run `ruff check .` and `pytest tests -v` before pushing.
- For non-trivial features, drop a short propose under `propose/` and
  reference it in the PR.

## Environment for running the server

`LANCEDB_URI` (required), `LANCEDB_MCP_PROJECT_ROOT`, `KUZU_DB_PATH`
(defaults to `${LANCEDB_URI}/code_graph.kuzu`),
`LANCEDB_MCP_GRAPH_ENABLED`, `LANCEDB_MCP_ALLOW_REFRESH`,
`LANCEDB_MCP_MICROSERVICE_ROOTS`, `SBERT_MODEL`, `SBERT_DEVICE`. See
`README.md` §2 and `mcp.json.example`.
