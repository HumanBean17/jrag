# ARCHITECTURE — `jrag`

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

**Entrypoints** (`pyproject.toml [project.scripts]`): `jrag` and `java-codebase-rag` (legacy alias) both → `java_codebase_rag.cli_dispatch:_console_script_main` — the unified dispatcher that routes operator verbs to `cli._console_script_main` and agent verbs to `jrag._console_script_main`; `jrag-mcp` and `java-codebase-rag-mcp` (legacy alias) both → `java_codebase_rag.mcp.server:main`.

## Write path (indexing)

```
jrag init|increment|reprocess      java_codebase_rag/cli.py
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
  ├─ search ─▶ search_lancedb.run_search    (vector / hybrid; graph-expand + 3-list RRF: vector + graph + BM25)
  │            └─ lancedb import absent (Intel Mac) → search_lexical (BM25 over Symbol FTS index; heuristic scan fallback)
  ├─ find / describe / neighbors ─▶ ladybug_queries.LadybugGraph   (Cypher)
  └─ resolve ─▶ resolve_service.resolve_v2   (cascade → status one | many | none)
       on empty ─▶ absence_diagnosis.diagnose   → verdict + (optional) proof
       always   ─▶ mcp_hints.generate_hints     → hints_structured + advisories
```

| Tool | Backing | Notes |
| --- | --- | --- |
| `search` | Lance vector/hybrid with 3-list RRF (vector + graph + BM25), or BM25 lexical fallback | dedup by FQN; role weights via `search_scoring`; list-set + `k` via injectable `RankConfig` |
| `find` | Ladybug Cypher | required `NodeFilter`; strict per-kind frame |
| `describe` | Ladybug Cypher | node record + `edge_summary` (composed/override rollups) |
| `neighbors` | Ladybug Cypher | one hop; `direction` + `edge_types` required; dot-key composed edges |
| `resolve` | Ladybug Cypher | per-kind generators exact→fuzzy; cap 10 candidates |

**Lexical fallback** is selected by import availability (`mcp_v2` guards `from search_lancedb import …`): same row contract, flagged via `lexical_mode` + advisory. It is **BM25-first**: `build_ast_graph` indexes `Symbol.search_text` (camelCase-split token soup) under a LadybugDB FTS index (`sym_fts`, Okapi BM25), and `search_lexical` fetches top-K candidates via `QUERY_FTS_INDEX` then re-ranks them with the name/type/fqn/role heuristic in `search_lexical` (helpers from `search_scoring`). The FTS index auto-maintains on `increment`; the heuristic scan is the fallback when the index/extension is absent (older graph, offline first run). **`jrag` CLI** calls the same `mcp_v2.*` functions — identical backends, only rendering differs.

**BM25 is also first-class on the primary (vector) path, not only the fallback.** `search_lancedb._graph_expand_merge` fuses **three** RRF lists — vector hits + graph-expand hits + BM25 hits — where the BM25 list is sourced from the same `sym_fts` index (via `search_lexical._try_fts_candidates`), resolved to chunk rows in BM25 rank order and re-filtered by the same LanceDB predicates as the vector list. The list-set and RRF `k` are runtime-injectable via `RankConfig` (`search_scoring.py`; default = 3-list, `k=60`), so the eval can A-B 2-list vs 3-list and sweep `k`. If the FTS extension/index is unavailable, the BM25 list is empty and the fusion degrades silently to the 2-list vector+graph ranking (no exception, no advisory) — so airgapped installs see no regression. Quality is measured by the **eval harness** (`java_codebase_rag.eval`: recall@k / precision@k / MRR over a corpus, with a Tier-A auto ground-truth derived per-Symbol and an optional Tier-B operator-authored file). On shopizer (n=400 of 2322 type-level symbols) the 3-list fusion at `k=60` decisively beats the 2-list baseline on every metric: **MRR 0.3044→0.6205 (+104%)**, **recall@1 0.220→0.490 (+123%)**, recall@10 0.535→0.860 (+61%), recall@20→0.905. The gain is large because Tier-A queries are identifier-derived (BM25's home turf) — exact-identifier matches anchor the dense ranking exactly as the hybrid thesis predicts; a future Tier-B natural-language ground truth is expected to show a smaller-but-positive delta (NL queries favor semantic vectors). The BM25 hop costs **~+25-30 ms p50 (~10%)** per query. `k∈{30,60,90,120}` all beat baseline; `k=30` narrowly edges `k=60` on this identifier-heavy eval (MRR 0.631 vs 0.620, within noise on n=400), but `k=60` ships as the conservative, regime-robust choice — re-tune when Tier-B NL ground truth exists. (An initial eval run reported a much smaller delta; that was muted by a query-preprocessing bug — camelCase identifiers weren't reaching the FTS tokenizer — fixed in `search_lancedb._bm25_candidate_rows` via `search_scoring.build_fts_query`.)

### Watch path (`jrag watch`) — warm reads + freshness

When a `jrag watch` daemon is running, the **read path gains a warm hop**: the `jrag` read handlers ask the daemon over a Unix socket for the already-built payload instead of cold-loading the model and graph. The daemon reuses the MCP server's warm-cache posture — a process-singleton `_st_model` (SBERT) and a `LadybugGraph` — served to the CLI, so each query skips the per-call torch/model load. Output is byte-identical to the cold path (the same payload cores in `read_payloads.py` run either way). With no daemon running, the client transparently takes the cold path — the daemon is a pure accelerator, never a dependency. On a **graph-only install (macOS Intel)** the daemon probes `pipeline.vector_stack_installed()` at startup: the model warm-up is skipped and `search` degrades to lexical via `mcp_v2._ensure_vector_backend`; the cocoindex vectors reindex is skipped too, so the graph reindex still completes and fires `indexing_done`. State/`--status` carry `mode: lexical`.

| Concern | Module | Notes |
| --- | --- | --- |
| Project lock | `watch/lock.py` | `ProjectLock` (pidfile + stdlib `flock`). New — the codebase had no locking. One daemon per index dir; also blocks a concurrent manual `increment`. Unix-only (`WatchUnsupportedPlatform` on Windows). |
| Runtime paths | `watch/paths.py` | socket, pidfile, state file under the index dir. |
| IPC protocol | `watch/protocol.py` | `Request`/`Response`/`ErrorShape`; `ERR_*` kinds. |
| Warm resources | `watch/warm.py` | `WarmResources` holds the model + graph; `LadybugGraph.reset_for_path` swaps the live graph handle after a reindex. |
| Socket server | `watch/server.py` | `WatchServer` dispatches read payloads (serialized, not rendered). |
| IPC client | `watch/client.py` | `is_daemon_alive` / `get_payload`; any error → cold fallback. |
| Watcher | `watch/watcher.py` | `SourceWatcher` (watchdog native + polling fallback), lossless debounce, per-type routing. |
| Daemon | `watch/daemon.py` | `WatchDaemon` lifecycle: lock → (warm, only if vector stack installed) → server → watcher → serve loop → teardown (`os._exit(0)`). |

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
