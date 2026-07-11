# ARCHITECTURE — `java-codebase-rag`

Internal implementation doc (**HOW**). For WHAT/WHY see [DESIGN.md](./DESIGN.md); operator behavior in `docs/`.

## Overview

```
              Java repo (.java · db/migration/*.sql · application*.yml)
                                 │
   ════════════════════ build time (operator CLI) ════════════════════
   Vectors (CocoIndex flow) → Optimize (Lance tables) → Graph (tree-sitter)
                                 │
              ┌──────────────────┴──────────────────┐
              ▼                                     ▼
      LanceDB — 3 vector tables             LadybugDB — code_graph.lbug
      (semantic / hybrid retrieval)         (Cypher structural traversal)
              │                                     │
              └──────────────────┬──────────────────┘
                                 ▼   query time (agent / human)
   MCP server · jrag CLI · operator CLI (lifecycle / analyze-pr)
                search · find · describe · neighbors · resolve
```

*CocoIndex drives the vector flow into LanceDB; LadybugDB is the embedded Cypher graph DB.*

## Repository layout

Core library = **top-level `.py` modules** (`py-modules`); the installable **`java_codebase_rag/` package** holds CLI entrypoints, orchestration, and config.

| Concern | Modules |
| --- | --- |
| Write path | `java_codebase_rag/cli.py`, `java_codebase_rag/pipeline.py`, `java_codebase_rag/lance_optimize.py`, `java_index_flow_lancedb.py`, `build_ast_graph.py` |
| Parse + ontology | `ast_java.py` (`ONTOLOGY_VERSION=19`), `java_ontology.py` (`EDGE_SCHEMA` + label sets), `graph_enrich.py`, `chunk_heuristics.py` |
| Read path | `server.py`, `mcp_v2.py`, `ladybug_queries.py`, `search_lancedb.py`, `search_lexical.py`, `search_scoring.py`, `resolve_service.py`, `java_codebase_rag/read_payloads.py` |
| Hints + absence | `mcp_hints.py`, `graph_types.py`, `absence_types.py`, `absence_vocab.py`, `absence_diagnosis.py` |
| Config + paths | `java_codebase_rag/config.py`, `path_filtering.py`, `index_common.py`, `brownfield_events.py` |
| Watch daemon | `java_codebase_rag/watch/` (`lock`, `paths`, `protocol`, `warm`, `server`, `client`, `watcher`, `daemon`) |
| Surfaces | `java_codebase_rag/{cli,jrag,installer}.py` |
| Shipped artifacts | `skills/`, `agents/` (deployed verbatim to agent host via `install`/`update`) |

**Entrypoints** (`pyproject.toml [project.scripts]`): `java-codebase-rag` → `java_codebase_rag.cli:_console_script_main`; `java-codebase-rag-mcp` → `server:main`; `jrag` → `java_codebase_rag.jrag:_console_script_main`.

## Write path (indexing)

```
java-codebase-rag init|increment|reprocess      java_codebase_rag/cli.py
      │  resolve config  (CLI flag > env > YAML > default)
      ▼
java_codebase_rag/pipeline.py
  ├─▶ cocoindex update  (java_index_flow_lancedb.py)        [Vectors]
  │       embed chunks → 3 Lance tables
  ├─▶ lance_optimize.py   serialized compact + BTree/FTS    [Optimize]
  └─▶ build_ast_graph.py   tree-sitter, 6 passes             [Graph]
          PASS1 nodes · PASS2 wiring (EXTENDS/IMPLEMENTS/INJECTS/DECLARES)
          PASS3 calls · PASS4 routes + EXPOSES
          PASS5 clients/producers · PASS6 cross-service match
          ▼
      LadybugDB code_graph.lbug  +  .graph_hashes.json
```

- **`init`** — refuses a non-empty index dir (exit 2); full vectors + full graph.
- **`increment`** — CocoIndex `memo=True` catch-up (changed files only) + **incremental graph**. Falls back to **full** rebuild on any of: no graph · `ontology_version < 19` · crash marker (`.graph_increment_in_progress`) · dependent expansion > 50 files.
- **`reprocess`** — default = full vectors + full graph; `--vectors-only` / `--graph-only` selective (mutually exclusive). Exit semantics in `cli._reprocess_exit_code`.

**Phantom nodes:** unresolved callees / supertypes (external libs, `java.lang`) become `Symbol` rows with `resolved=false` and empty filename — so every edge lands on *a* node. Skipped by dependent expansion and scoped deletion.

## Read path (query)

```
MCP tool call (server.py)  ──asyncio.to_thread──▶  mcp_v2.*
  ├─ search ─▶ search_lancedb.run_search    (vector / hybrid; optional graph-expand + RRF rank fusion)
  │            └─ lancedb import absent (Intel Mac) → search_lexical (BM25 over Symbol FTS index; heuristic scan fallback)
  ├─ find / describe / neighbors ─▶ ladybug_queries.LadybugGraph   (Cypher)
  └─ resolve ─▶ resolve_service.resolve_v2   (cascade → status one | many | none)
       on empty ─▶ absence_diagnosis.diagnose   → verdict + (optional) proof
       always   ─▶ mcp_hints.generate_hints     → hints_structured + advisories
```

| Tool | Backing | Notes |
| --- | --- | --- |
| `search` | Lance vector/hybrid, or BM25 lexical fallback | dedup by FQN; role weights via `search_scoring` |
| `find` | Ladybug Cypher | required `NodeFilter`; strict per-kind frame |
| `describe` | Ladybug Cypher | node record + `edge_summary` (composed/override rollups) |
| `neighbors` | Ladybug Cypher | one hop; `direction` + `edge_types` required; dot-key composed edges |
| `resolve` | Ladybug Cypher | per-kind generators exact→fuzzy; cap 10 candidates |

**Lexical fallback** is selected by import availability (`mcp_v2` guards `from search_lancedb import …`): same row contract, flagged via `lexical_mode` + advisory. It is **BM25-first**: `build_ast_graph` indexes `Symbol.search_text` (camelCase-split token soup) under a LadybugDB FTS index (`sym_fts`, Okapi BM25), and `search_lexical` fetches top-K candidates via `QUERY_FTS_INDEX` then re-ranks them with the name/type/fqn/role heuristic in `search_lexical` (helpers from `search_scoring`). The FTS index auto-maintains on `increment`; the heuristic scan is the fallback when the index/extension is absent (older graph, offline first run). **`jrag` CLI** calls the same `mcp_v2.*` functions — identical backends, only rendering differs.

### Watch path (`jrag watch`) — warm reads + freshness

When a `jrag watch` daemon is running, the **read path gains a warm hop**: the `jrag` read handlers ask the daemon over a Unix socket for the already-built payload instead of cold-loading the model and graph. The daemon reuses the MCP server's warm-cache posture — a process-singleton `_st_model` (SBERT) and a `LadybugGraph` — served to the CLI, so each query skips the per-call torch/model load. Output is byte-identical to the cold path (the same payload cores in `read_payloads.py` run either way). With no daemon running, the client transparently takes the cold path — the daemon is a pure accelerator, never a dependency.

| Concern | Module | Notes |
| --- | --- | --- |
| Project lock | `watch/lock.py` | `ProjectLock` (pidfile + stdlib `flock`). New — the codebase had no locking. One daemon per index dir; also blocks a concurrent manual `increment`. Unix-only (`WatchUnsupportedPlatform` on Windows). |
| Runtime paths | `watch/paths.py` | socket, pidfile, state file under the index dir. |
| IPC protocol | `watch/protocol.py` | `Request`/`Response`/`ErrorShape`; `ERR_*` kinds. |
| Warm resources | `watch/warm.py` | `WarmResources` holds the model + graph; `LadybugGraph.reset_for_path` swaps the live graph handle after a reindex. |
| Socket server | `watch/server.py` | `WatchServer` dispatches read payloads (serialized, not rendered). |
| IPC client | `watch/client.py` | `is_daemon_alive` / `get_payload`; any error → cold fallback. |
| Watcher | `watch/watcher.py` | `SourceWatcher` (watchdog native + polling fallback), lossless debounce, per-type routing. |
| Daemon | `watch/daemon.py` | `WatchDaemon` lifecycle: lock → warm → server → watcher → serve loop → teardown (`os._exit(0)`). |

**Reindexing is subprocessed**, never in-process: cocoindex for vectors, `build_ast_graph.py --incremental` for the graph. **Concurrency:** searches never wait and never see partial state — Lance commits are atomic per version (fresh per-query reads are consistent), and the graph (LadybugDB — no transactions, single writer) is kept readable via a **copy-on-write file snapshot** of `code_graph.lbug` taken around each graph reindex: reads continue from the snapshot while the subprocess writes the original, then `reset_for_path` repoints the live handle.

## Stores

**LanceDB** (index dir, e.g. `.java-codebase-rag/`) — 3 tables (`LANCE_TABLE_NAMES`): `javacodeindex_java_code` (Java chunks w/ role · module · microservice · generated), `sqlschemaindex_sql_schema`, `yamlconfigindex_yaml_config`. cocoindex state in `cocoindex.db/`.

**LadybugDB** (`code_graph.lbug`) — 6 node tables: `Symbol`, `Route`, `Client`, `Producer`, `UnresolvedCallSite`, `GraphMeta`; rel tables = the **11** `EDGE_SCHEMA` edges + `UNRESOLVED_AT`. `GraphMeta` carries `ontology_version`, counts, per-pass stats.

## Config & project-root

Precedence **CLI flag > env > YAML (`.java-codebase-rag.yml`) > default**; each value tagged with source for `meta` provenance. `discover_project_root` walks up from cwd for the YAML or the `.java-codebase-rag/` dir (never a bare `$HOME` index). Resolved paths: index dir → `code_graph.lbug` + `cocoindex.db`. `.java-codebase-rag.hosts` is the **installer** marker (hosts + surface), not an indexing config. *Brownfield* = in-source/YAML role & capability overrides (`brownfield_events.py` emits build-time diagnostics; config in [`docs/CONFIGURATION.md`](./CONFIGURATION.md)).

## Extension points (where to change things)

- **New edge type** → `EDGE_SCHEMA` (`java_ontology.py`) + a builder emit (`build_ast_graph.py`) + Cypher in `ladybug_queries.py` + AGENT-GUIDE taxonomy.
- **New role/capability** → inference tables in `ast_java.py` + valid sets in `java_ontology.py`.
- **New node kind** → Ladybug schema (`_create_schema`) + extraction pass + `NodeFilter` / resolve generators in `mcp_v2.py` / `resolve_service.py`.
- **Semantic extraction change** → bump `ONTOLOGY_VERSION` (`ast_java.py:87`); read guard + incremental fallback follow automatically; note reindex in [`docs/CONFIGURATION.md`](./CONFIGURATION.md).
- **Watch surface** → `java_codebase_rag/watch/` (warm reads + debounced reindex). New read command? add a payload core in `read_payloads.py`, then wire `server.py` dispatch + the `jrag` handler (cold path stays the default). The cold path must stay byte-identical — the daemon is an accelerator, never a dependency.

Dev workflow (editable install, test-reset ritual, full-suite discipline) — see [`CLAUDE.md`](../CLAUDE.md).

## Key constants

| Constant | Value / location |
| --- | --- |
| `ONTOLOGY_VERSION` | `19` — `ast_java.py:87` |
| `LANCE_TABLE_NAMES` | 3 tables — `java_codebase_rag/lance_optimize.py:35` |
| Graph passes | 6 (labels `build_ast_graph.py:83`) |
| Incremental cap | `expansion_cap=50` — `build_ast_graph.py:3800` |
| Config precedence | `config.py:3` |
| Tool registration | `server.py:594` (first of 5 `@mcp.tool`) |

## TL;DR

Two stores built in lockstep — LanceDB vectors via CocoIndex, LadybugDB graph via a 6-pass tree-sitter build — queried by 5 MCP tools that split cleanly: `search` → vector/lexical, `find`/`describe`/`neighbors`/`resolve` → Cypher. Hints and absence wrap every response; `ONTOLOGY_VERSION=19` is the rebuild/staleness contract. Contributors extend via `EDGE_SCHEMA` + builder passes, and bump the version on any semantic change.
