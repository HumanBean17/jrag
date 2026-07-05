# java-codebase-rag

A graph-native code intelligence layer for Java microservice estates — usable as an **MCP server** or a **CLI** (`jrag`), two surfaces over the same graph.

The system extracts a deterministic property graph from Java source (tree-sitter), stores it in **LadybugDB** (graph) alongside a **LanceDB** vector index (chunks), and exposes two agent surfaces, picked at install time (`java-codebase-rag install --surface mcp|cli`): the **MCP** surface ships five tools — `search`, `find`, `describe`, `neighbors`, `resolve` — over stdio; the **CLI** surface ships `jrag`, one command per engineering intent. Both collapse onto three primitive operations: **locate**, **inspect**, **walk**.

> **What this MCP is:** a **GPS for code navigation**, not a reasoning engine.
> Agents use a simple loop:
>
> 1. **Locate** entry nodes (`search` / `find`, or identifier-shaped **`resolve`**)
> 2. **Inspect** what a node is (`describe`)
> 3. **Walk** one hop at a time (`neighbors`) until enough evidence is gathered
>
> The MCP exposes structure and adjacency; the agent owns multi-hop reasoning and stop conditions.

For the design rationale, the GPS metaphor, and the full ontology, see [`docs/paper/paper.pdf`](./docs/paper/paper.pdf) (architecture report).

---

## Why this exists

Generic code-search tools (grep, ctags, vector-only RAG) hit a ceiling on real Java microservice estates: they find files but lose the structure that makes a Spring/JAX-RS system navigable. This project is built around five choices that target that gap.

- **Hybrid RAG + GraphRAG, not either-or.** Semantic recall (LanceDB chunk vectors) and structural navigation (LadybugDB property graph) are composed in one surface. `search` finds candidate nodes by meaning; `neighbors` walks the exact edge you care about (`CALLS`, `IMPLEMENTS`, `INJECTS`, `EXPOSES`, …). The agent picks the right primitive per step instead of being forced into pure-vector or pure-symbol search.

- **A Java-tuned role model.** Symbols are labelled with stereotypes inferred from Spring and JAX-RS conventions — `CONTROLLER`, `SERVICE`, `REPOSITORY`, `COMPONENT`, `CONFIG`, `ENTITY`, `CLIENT`, `MAPPER`, `DTO`. Agents can ask "list controllers" or "who injects this repository" directly, instead of grep-ing for `@RestController` and hoping for the best. Roles drive both filtering (`find` with a `NodeFilter`) and ranking.

- **Ranking specialized for Java codebases.** The composite ranker is aware of role, microservice, and FQN structure — not a generic BM25. A search for `"chat ingress"` surfaces controllers before utility classes; a search scoped to one microservice doesn't drown in matches from the other 19. Defaults are tuned on the bank-chat fixture and exposed in `docs/CONFIGURATION.md` for per-repo overrides.

- **Cross-service resolution + system-level navigation.** `HTTP_CALLS` and `ASYNC_CALLS` edges connect Clients and Producers in one microservice to Routes and Handlers in another, resolved at index time from URL/topic strings + Spring `@FeignClient` / `RestTemplate` conventions. `/who-hits-route`, `/trace-request-flow`, and `/impact-of` use these to answer questions a single-service tool fundamentally can't — "who calls this REST endpoint from outside this service", "trace this Kafka message end-to-end", "if I change this DTO, which services break".

- **Brownfield annotations as a first-class override.** Real Java estates have hand-rolled HTTP clients, dynamic topic names, reflection-heavy routing. `@CodebaseHttpRoute`, `@CodebaseAsyncRoute`, `@CodebaseHttpClient`, and `@CodebaseProducer` let you pin the truth in source. They have **exclusive priority** — when a symbol is annotated, framework-convention inference is skipped entirely. You get a correct graph on legacy code without rewriting it.

The rest of this README is the install, the tool/command orientation, and the reference for putting that to work.

---

## Install

```bash
pip install java-codebase-rag
```

Python **3.11+** required, on **Linux, macOS, and Windows** — every native dependency (LanceDB, LadybugDB/kuzu, CocoIndex) ships a wheel for each platform. After install, `java-codebase-rag --help` should print the CLI groups.
The package includes the CocoIndex lifecycle dependency used by `init`, `increment`, `reprocess`, and `erase`.

### Interactive setup (recommended)

Run `java-codebase-rag install` from your Java project root to launch an interactive setup wizard that:

1. Detects Java source directories (Maven/Gradle modules)
2. Configures the embedding model (auto-downloads ~90MB or uses a local path)
3. Selects agent hosts (Claude Code, Qwen Code, GigaCode)
4. Deploys MCP registration, skill, and agent artifacts
5. Generates `.java-codebase-rag.yml` configuration
6. Runs `init` to build the index

```bash
# Interactive mode
java-codebase-rag install

# Non-interactive mode (for CI/automation)
java-codebase-rag install --non-interactive --agent claude-code
```

After `pip install --upgrade java-codebase-rag`, run `java-codebase-rag update` to refresh shipped artifacts and catch up the index (Lance + graph).

All indexing lifecycle commands (`init`, `increment`, `reprocess`, `install`, `update`) show a unified `Vectors → Optimize → Graph` progress bar on stderr during the index build (powered by `rich`); pass `--quiet` to suppress it.

### Manual registration

If you prefer manual configuration, see [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) for the full CLI reference.

> **Stability disclaimer.** This package does **not** promise backward compatibility. MCP tool contracts, env vars, Lance/LadybugDB schemas, config files, and Python APIs may change without a deprecation period. Track `main` and rebuild indexes when ontology or embedding settings change.

---

## Tools & commands at a glance

Pick a surface once at install time — `java-codebase-rag install --surface mcp|cli` (default `mcp`). Both surfaces walk the same LanceDB vectors + LadybugDB graph.

**MCP surface — five tools over stdio**

| Tool | Purpose | Required args |
|---|---|---|
| `search` | Locate nodes by NL / code text. | `query` |
| `find` | Locate nodes by structured filter. | `kind`, `filter` |
| `describe` | Full record + edge counts for one node. | `id` |
| `resolve` | Identifier-shaped lookup (FQN-collision-safe). Returns `one` / `many` / `none`. | `identifier` |
| `neighbors` | Graph walk, one hop. | `ids`, `direction`, `edge_types` |

Full schemas, `NodeFilter` / `EdgeFilter` semantics, and the hints contract live in [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md). Edge types and traversal directions are listed in [`docs/EDGE-NAVIGATION.md`](./docs/EDGE-NAVIGATION.md).

**CLI surface — `jrag`, one command per engineering intent**

```bash
# Orientation
jrag status                    # index health (ontology version, freshness, counts)
jrag microservices             # microservices with resolved type counts
jrag map                       # counts per kind per service/module
jrag map --module              # group by module instead
jrag conventions               # dominant roles + framework tallies
jrag overview chat-core        # bundle for a microservice
jrag overview /chat/assign     # route flow (inbound callers + outbound CALLS)
jrag overview banking.chat     # topic producers + consumers
jrag overview chat-core --as microservice  # override auto-detection

# Locate
jrag find ChatService          # exact name/FQN lookup (symbols)
jrag find --role CONTROLLER    # filter mode (NodeFilter flags)
jrag inspect ChatService       # full node details + edge_summary
jrag outline src/main/.../Foo.java  # all symbols declared in a file
jrag imports src/main/.../Foo.java   # imports resolved to graph nodes

# Listings
jrag http-routes               # HTTP routes
jrag http-clients              # HTTP clients (Feign / RestTemplate / WebClient)
jrag producers                 # async message producers (Kafka / StreamBridge)
jrag topics                    # message topics grouped by producer
jrag jobs                      # scheduled tasks (@Scheduled)
jrag listeners                 # message listeners (@KafkaListener etc.)
jrag entities                  # JPA entities

# Traversals (all resolve-first)
jrag callers ChatService#assign(Request)   # who calls me?
jrag callees ChatService#assign(Request)   # what do I call?
jrag hierarchy AbstractBase               # type tree (parents + children)
jrag implementations PaymentProcessor     # classes implementing an interface
jrag subclasses AbstractRepository        # classes extending a type
jrag overrides Impl#run()                 # methods this overrides (dispatch UP)
jrag overridden-by Iface#run()            # methods overriding this (dispatch DOWN)
jrag dependents PaymentGateway            # who injects this type?
jrag dependencies ChatService             # types this injects
jrag impact PaymentGateway                # fleet-wide blast radius
jrag decompose ChatIngressController#assign   # role-waterfall flow
jrag flow /chat/assign                    # request flow through a route
jrag connection chat-core                 # cross-service connections

# Semantic search
jrag search "assign a chat agent"         # semantic over Lance (java table)
jrag search "kafka" --table all           # java + sql + yaml tables
jrag search "audit" --hybrid              # vector + keyword hybrid
jrag search "audit" --offset 5            # paginated
```

Every `<query>` command takes human-readable identifiers (FQN / simple name / route path / topic) — never raw node IDs. Output contract, flags, and the resolve-first rule are in [`jrag` — agent CLI](#jrag--agent-cli) below.

### Three-layer architecture

Layer 1 (storage) → Layer 2 (5 MCP tools **or** the `jrag` CLI) → Layer 3 (skill). The MCP-surface skill **[`/explore-codebase`](./skills/explore-codebase/SKILL.md)** documents the 5-tool MCP; the CLI-surface skill **[`/explore-codebase-cli`](./skills/explore-codebase-cli/SKILL.md)** documents the `jrag` CLI (PR-JRAG-5). See the [architecture diagram in `skills/README.md`](./skills/README.md#three-layer-architecture).

---

## Configuration

The operator-facing surface is small: pick an index dir, pick an embedding model, optionally drop a `.java-codebase-rag.yml` at your project root for microservice layout and brownfield overrides.

| If you want to… | See |
|---|---|
| Set env vars and override precedence | [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) §1 |
| Configure microservice roots and embeddings via YAML | [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) §2 |
| Understand the graph (nodes, edges, capabilities, ranking) | [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) §3 |
| Steer a brownfield Java tree (custom stereotypes, non-Spring stacks) | [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) §4 |
| Control which files the indexer walks | [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) §5 |
| Check whether your repo fits this tool's assumptions | [`docs/CODEBASE_REQUIREMENTS.md`](./docs/CODEBASE_REQUIREMENTS.md) |

---

## CLI cheat sheet

Run `java-codebase-rag --help` to list grouped subcommands. Operator playbook with workflows, exit codes, and env alignment lives in [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md).

| Group | Subcommand | What it does |
|---|---|---|
| Setup | `install` | Interactive setup wizard: config, MCP registration, skill/agent deployment, indexing. |
| Setup | `update` | Refresh shipped artifacts (skill, agent, MCP entry) + incremental Lance/graph catch-up after pip upgrade. |
| Lifecycle | `init` | First-time index. Refuses if artifacts already exist. |
| Lifecycle | `increment` | CocoIndex catch-up + incremental LadybugDB update. `--vectors-only` for Lance only. |
| Lifecycle | `reprocess` | Full Lance + LadybugDB rebuild. `--vectors-only` / `--graph-only` for a single phase. |
| Lifecycle | `erase` | Delete index artifacts. Requires `--yes` or TTY confirm. |
| Introspection | `meta`, `tables`, `diagnose-ignore`, `unresolved-calls` | Health, table listing, ignore-layer diagnostics, receiver-failure call sites. |
| Analysis | `analyze-pr` | Blast-radius / risk from a unified diff. |

---

## jrag — agent CLI

`jrag` is a separate console script (alongside `java-codebase-rag`) built for AI
coding agents. It gives the agent **one command per engineering intent** and
takes human-readable identifiers (FQN / simple name / route path / topic) —
never raw node IDs. Every `<query>` command resolves the identifier via
`resolve_v2` as the first step; on `many` it returns candidates and stops, on
`none` it returns `not_found`. Auto-pick is forbidden.

The default output is compact text (a deliberate divergence from the operator
CLI's TTY heuristic — `jrag` is agent-facing/non-TTY). `--format json` emits the
shared envelope verbatim. Every command emits the same envelope shape:

```json
{
  "status": "ok",
  "nodes": {"com.example.Foo": {"kind": "symbol", "fqn": "com.example.Foo"}},
  "edges": [{"edge_type": "CALLS", "confidence": 0.9, "target": "com.example.Bar#baz()"}],
  "root": "com.example.Foo",
  "agent_next_actions": ["jrag callees com.example.Foo#bar()"],
  "truncated": false
}
```

No raw graph node id ever appears on either surface: `nodes` is keyed by each
node's natural identifier (FQN for symbols, `METHOD path` for routes,
`member_fqn->target` for clients, `topic:<name>` for topics), `root` is the
root's natural identifier, and each edge carries `target` (the referenced node's
identifier) instead of a graph id. The agent reuses these identifiers directly
as the next command's `<query>` — there is nothing else to pass.

`agent_next_actions` carries up to 5 contextual next-step hints (e.g. after
`inspect`, the agent sees `jrag callers <fqn>`, `jrag callees <fqn>`, etc. for
the edges the root actually has). Omitted from JSON when empty.

The full command catalog lives in [Tools & commands at a glance](#tools--commands-at-a-glance).

### Flags

| Flag | Scope | Effect |
|------|-------|--------|
| `--format text\|json` | all | output format (default: text) |
| `--service <name>` | listings/traversals | filter by microservice |
| `--module <name>` | listings/traversals | filter by module |
| `--limit <n>` | listings/traversals | cap results (default 20; `limit+1` fetch detects truncation) |
| `--offset <n>` | `find`, `search` only | paginate (other commands reject it) |
| `--kind symbol\|route\|client\|producer` | `<query>` commands | resolve hint |
| `--java-kind`, `--role`, `--fqn-contains` | `<query>` commands | client-side post-filters |
| `--index-dir <path>` | all | override index directory |

`--offset` is intentionally NOT a global flag: only `find` and `search` route
through backends that accept it. Every other command rejects it.

A missing or stale index produces an actionable `status: error` envelope (exit
2) rather than a traceback:

```
error: No index at /path/to/code_graph.lbug. Run: java-codebase-rag init --source-root <root>
```

See [`plans/active/PLAN-JRAG-CLI.md`](./plans/active/PLAN-JRAG-CLI.md) for the
full design and per-PR breakdown.

---

## Further reading

| Document | What's in it |
|---|---|
| [`docs/paper/paper.pdf`](./docs/paper/paper.pdf) | Architecture report — design rationale, GPS metaphor, three-layer architecture, design principles, future work. |
| [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md) | Agent-facing guide. Copy-paste into `QWEN.md` / `CLAUDE.md` / `AGENTS.md`. |
| [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) | Environment variables, project YAML, graph ontology, brownfield overrides, ignore patterns. |
| [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) | CLI operator playbook: workflows, exit codes, env alignment. |
| [`docs/EDGE-NAVIGATION.md`](./docs/EDGE-NAVIGATION.md) | MCP-traversable edges, directions, dot-key composition. |
| [`skills/`](./skills/) | `/explore-codebase` (MCP surface) + `/explore-codebase-cli` (CLI surface) skills — operating manuals for hosts with skill discovery (alternative to copy-pasting AGENT-GUIDE). See [`skills/README.md`](./skills/README.md). |
| [`docs/MANUAL-VERIFICATION-CHECKLIST.md`](./docs/MANUAL-VERIFICATION-CHECKLIST.md) | 7-phase agent-driven verification after indexing your project. |
| [`docs/CODEBASE_REQUIREMENTS.md`](./docs/CODEBASE_REQUIREMENTS.md) | Assumptions about your Java repo + per-file edit map for non-conforming codebases. |
| [`docs/PRODUCT-VISION.md`](./docs/PRODUCT-VISION.md) | Long-term product direction. |

---

## Install from source (contributors)

```bash
git clone https://github.com/HumanBean17/java-codebase-rag
cd java-codebase-rag
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The `cocoindex` package powers lifecycle commands that run the indexer (`init`, `increment`, `reprocess`, `erase`). Search and MCP navigation do not invoke it directly.

The default embedding model is `sentence-transformers/all-MiniLM-L6-v2` (downloaded on first `init`). Override via the `SBERT_MODEL` env var — see [`docs/CONFIGURATION.md` §1](./docs/CONFIGURATION.md#1-environment-variables).

---

## Roadmap (graph layer)

- `get_service_topology` — microservice-level summary aggregating `HTTP_CALLS` / `ASYNC_CALLS`.
- Agentic routing layer (query classifier → vector / graph / both).
- Optional `codegraph_nodes` LanceDB table embedding symbol summaries so the graph itself is vector-searchable.
