# DESIGN — `java-codebase-rag`

Internal design doc (**WHAT + WHY**). For HOW see [ARCHITECTURE.md](./ARCHITECTURE.md). Operator/agent behavior lives in the existing `docs/` set; this file is for contributors working **on** the codebase.

## TL;DR

Deterministic tree-sitter graph + vector index for Java; agents get 5 MCP tools that walk a typed ontology **one hop at a time**; graph and vectors are complementary, empties are classified honestly, the file always beats the index, and `ONTOLOGY_VERSION` is the rebuild / staleness contract.


## What this is

A **GraphRAG layer for Java/Spring enterprise codebases**: a deterministic AST knowledge graph built *beside* a vector index, surfaced to AI agents through an MCP server and to operators through a CLI. The graph answers structural questions (who calls / implements / injects X; what breaks if X changes) that pure vector retrieval structurally cannot — via bidirectional traversal, not similarity.

One repo, two stores, two audiences:

- **Build time** (operator CLI): parse sources → vector chunks (LanceDB — embedded vector store) + typed graph (LadybugDB — embedded Cypher graph DB).
- **Query time** (agent): five MCP tools resolve / inspect / walk the graph, with vector search for fuzzy discovery.

## Core principles

1. **Deterministic extraction, not LLM extraction.** tree-sitter parses every file; a two-phase build (parse all nodes, then resolve edges against the complete registry) eliminates forward-reference gaps. Reproducible, runs in seconds, no ~30% silent file-skip rate. (DKB = Deterministic Knowledge Base; benchmark + rationale in `docs/paper`.)
2. **Structure complements vectors — it does not replace them.** Two stores from the same sources. Semantic questions → vector; structural questions → graph; fused via RRF (Reciprocal Rank Fusion) only where each adds signal.
3. **Walk at read time; don't precompute answers.** `neighbors` is exactly one hop. Multi-hop traces, impact analysis, "explain feature X" are the **agent's** reasoning over repeated one-hop calls. There is deliberately no magic impact/trace tool.
4. **Static analysis is a lower bound.** `CALLS` excludes reflection, Spring AOP proxies, dynamic dispatch. `resolved=false` means *external* (JDK/Spring), not *missing*. Never present this as proof of a runtime call path.
5. **Empty results must be honest, not silent.** Every empty hit is classified: `correct_empty` (genuine leaf), `not_in_project`, `external_dependency`, or `refine_query` — with did-you-mean, vocabulary context, and (for hard absence) an auditable proof.
6. **The ontology version is the contract.** `ONTOLOGY_VERSION` (currently **18**) gates incremental rebuilds, drives a read-time staleness guard, and tells the agent the index shape. A semantic extraction change bumps it; old indexes fall back to full rebuild.
7. **The file always wins.** When the index disagrees with the open source file, the index is presumed stale or partial. Mismatch is a signal to rebuild, not a fact to report.
8. **Generated sources are first-class by default.** MapStruct / OpenAPI / protobuf / … are auto-detected **by content** (`@Generated`, header banners), indexed like hand-written code, and filterable (`exclude_generated`) — never silently down-ranked.

## What it indexes

| Source | Store | Notes |
| --- | --- | --- |
| Java production sources | Lance chunks + graph Symbols | tree-sitter; tests/build/CI excluded |
| SQL (Flyway `db/migration`) | Lance chunks only | text + embedding |
| YAML (`application*.yml`) | Lance chunks only | text + embedding |

**Graph model** — 4 agent-visible node kinds: `Symbol` (types + methods), `Route` (inbound HTTP/messaging), `Client` (outbound HTTP), `Producer` (outbound async). Edges group into type wiring (`EXTENDS`/`IMPLEMENTS`/`INJECTS`), containment (`DECLARES*`), method calls (`CALLS`), overrides (`OVERRIDES`), service boundary (`EXPOSES`), and cross-service (`HTTP_CALLS`/`ASYNC_CALLS`). Full taxonomy + navigation: [`docs/EDGE-NAVIGATION.md`](./EDGE-NAVIGATION.md), [`docs/AGENT-GUIDE.md`](./AGENT-GUIDE.md).

## Surfaces (what it exposes)

| Surface | Audience | Provides |
| --- | --- | --- |
| MCP server (`server.py`) | agents | `search` / `find` / `describe` / `neighbors` / `resolve` |
| `jrag` CLI | agents / humans | same five tools, terminal rendering |
| `jrag watch` daemon | agents / humans | index freshness + warm-query accelerator over a Unix socket (one per project); pure accelerator, cold path stays byte-identical when no daemon runs |
| `java-codebase-rag` CLI | operators | index lifecycle, `meta` / `tables` / `diagnose-ignore`, `analyze-pr` |

## Non-goals (by design)

- Not a test/build/CI indexer — read those files directly.
- Not a reflection / dynamic-dispatch oracle — `CALLS` is static only.
- Not git history — use `git log` / `blame`.
- Not re-indexable from MCP — only the operator CLI rebuilds.
- **`jrag watch` is Unix-only (macOS/Linux)** — the project lock uses stdlib `fcntl`; on Windows `jrag watch` exits 2 and the cold read path is unaffected.
- **`jrag watch` is not a boot/persistent service** — it is a foreground-or-detached process the operator starts per coding session; nothing auto-starts on boot or survives logout.
- **`jrag watch` is one daemon per index dir, not multi-project** — a pidfile + `flock` enforces a single watcher (and blocks a concurrent manual `increment`) per project; run one process per project.
- **`jrag watch` is not network/remote and not for the MCP surface** — it serves warm reads over a local Unix socket to the `jrag` CLI only; the MCP server has its own warm-cache posture and is untouched.

Non-goal detail: [`docs/AGENT-GUIDE.md`](./AGENT-GUIDE.md) (§ "What this MCP is not"). Roadmap and future direction live in [`docs/PRODUCT-VISION.md`](./PRODUCT-VISION.md), not here.
