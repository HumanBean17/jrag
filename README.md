# java-codebase-rag

A graph-native code intelligence layer for Java microservice estates, exposed to LLM agents via the **Model Context Protocol (MCP)**.

The system extracts a deterministic property graph from Java source (tree-sitter), stores it in **Kuzu** (graph) alongside a **LanceDB** vector index (chunks), and exposes a deliberately small MCP surface — **five tools**: `search`, `find`, `describe`, `neighbors`, `resolve` — that collapse onto three primitive agent operations: **locate**, **inspect**, **walk**.

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

- **Hybrid RAG + GraphRAG, not either-or.** Semantic recall (LanceDB chunk vectors) and structural navigation (Kuzu property graph) are composed in one surface. `search` finds candidate nodes by meaning; `neighbors` walks the exact edge you care about (`CALLS`, `IMPLEMENTS`, `INJECTS`, `DECLARES_ROUTE`, …). The agent picks the right primitive per step instead of being forced into pure-vector or pure-symbol search.

- **A Java-tuned role model.** Symbols are labelled with stereotypes inferred from Spring and JAX-RS conventions — `CONTROLLER`, `SERVICE`, `REPOSITORY`, `CLIENT`, `PRODUCER`, `MAPPER`, `DTO`. Agents can ask "list controllers" or "who injects this repository" directly, instead of grep-ing for `@RestController` and hoping for the best. Roles drive both filtering (`find` with a `NodeFilter`) and ranking.

- **Ranking specialized for Java codebases.** The composite ranker is aware of role, microservice, and FQN structure — not a generic BM25. A search for `"chat ingress"` surfaces controllers before utility classes; a search scoped to one microservice doesn't drown in matches from the other 19. Defaults are tuned on the bank-chat fixture and exposed in `docs/CONFIGURATION.md` for per-repo overrides.

- **Cross-service resolution + system-level navigation.** `HTTP_CALLS` and `ASYNC_CALLS` edges connect Clients and Producers in one microservice to Routes and Handlers in another, resolved at index time from URL/topic strings + Spring `@FeignClient` / `RestTemplate` conventions. `/who-hits-route`, `/trace-request-flow`, and `/impact-of` use these to answer questions a single-service tool fundamentally can't — "who calls this REST endpoint from outside this service", "trace this Kafka message end-to-end", "if I change this DTO, which services break".

- **Brownfield annotations as a first-class override.** Real Java estates have hand-rolled HTTP clients, dynamic topic names, reflection-heavy routing. `@CodebaseHttpRoute`, `@CodebaseAsyncRoute`, `@CodebaseHttpClient`, and `@CodebaseProducer` let you pin the truth in source. They have **exclusive priority** — when a symbol is annotated, framework-convention inference is skipped entirely. You get a correct graph on legacy code without rewriting it.

The rest of this README is the install, walkthrough, and tool cheat sheet for putting that to work.

---

## Install

```bash
pip install java-codebase-rag
```

Python **3.11+** required. After install, `java-codebase-rag --help` should print the CLI groups.
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

After `pip install --upgrade java-codebase-rag`, run `java-codebase-rag update` to refresh shipped artifacts.

### Manual registration

If you prefer manual configuration, see [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) for the full CLI reference.

> **Stability disclaimer.** This package does **not** promise backward compatibility. MCP tool contracts, env vars, Lance/Kuzu schemas, config files, and Python APIs may change without a deprecation period. Track `main` and rebuild indexes when ontology or embedding settings change.

---

## 5-minute walkthrough — index this repo's bank-chat fixture

This repo ships a small multi-module Spring fixture under [`tests/bank-chat-system/`](./tests/bank-chat-system/) (`chat-core` + `chat-assign`) that the test suite uses for calibration. You can index it and confirm the install works end-to-end in under five minutes — no agent host required.

```bash
# 1. Clone the repo to get the fixture (the published package doesn't include tests/)
git clone https://github.com/HumanBean17/java-codebase-rag
cd java-codebase-rag

# 2. Build the index (Lance vectors + Kuzu graph). First run downloads the
#    embedding model (~90 MB) and takes ~30-60s on the fixture.
java-codebase-rag init --source-root tests/bank-chat-system --index-dir /tmp/bank-chat-index

# 3. Inspect what landed (resolved config, edge counts, ontology version)
java-codebase-rag meta --source-root tests/bank-chat-system --index-dir /tmp/bank-chat-index
```

Smoke-test the index with two checks (`search_lancedb` ships with the package):

```bash
# Vector search — proves the LanceDB side works
JAVA_CODEBASE_RAG_INDEX_DIR=/tmp/bank-chat-index \
  python -m search_lancedb "chat ingress controller" --table java --limit 3

# Vector + graph expansion — proves Kuzu is wired in
JAVA_CODEBASE_RAG_INDEX_DIR=/tmp/bank-chat-index \
  python -m search_lancedb "chat ingress controller" --table java --limit 3 \
    --graph-expand --expand-depth 2
```

If vector hits come back and graph expansion adds neighbor symbols, the install works end-to-end. Wire it into your agent next — the five MCP tools (`search`, `find`, `describe`, `neighbors`, `resolve`) are reachable over stdio.

---

## Wire into an MCP host

> **Quick setup:** Run `java-codebase-rag install` from your Java project root. The interactive wizard handles MCP registration, skill deployment, and configuration for Claude Code, Qwen Code, and GigaCode in one step.

### Claude Code (manual)

With the package installed, the console script `java-codebase-rag-mcp` is on your `PATH`. Register it project-scoped:

```bash
claude mcp add --transport stdio java-codebase-rag -- java-codebase-rag-mcp
```

**Zero-env-var configuration:** The tool automatically walks up the directory tree to find `.java-codebase-rag.yml`, so you don't need to set `JAVA_CODEBASE_RAG_SOURCE_ROOT` when working from within a project. Just place the config file at your project root and the tool will find it. See [`mcp.json.example`](./mcp.json.example) for the minimal configuration.

If you need to override defaults, you can set env vars (`JAVA_CODEBASE_RAG_INDEX_DIR`, `JAVA_CODEBASE_RAG_SOURCE_ROOT`, `SBERT_MODEL`, …) in `.mcp.json` or your shell profile. For a full configuration template, see [`mcp.json.example`](./mcp.json.example). Official docs: [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings).

### Claude Desktop

Edit `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) and add under `mcpServers`:

```json
{
  "mcpServers": {
    "java-codebase-rag": {
      "command": "java-codebase-rag-mcp",
      "env": {
        "JAVA_CODEBASE_RAG_INDEX_DIR": "/ABSOLUTE/PATH/TO/.java-codebase-rag",
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": "/ABSOLUTE/PATH/TO/your-java-project"
      }
    }
  }
}
```

See [`mcp.json.example`](./mcp.json.example) for the same shape in `.mcp.json` (Claude Code project-scoped) form.

### Driving the MCP from an agent

Pick **one** of two options (not both — they cover the same navigation intents):

1. **[`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md)** (recommended for most) — standalone MCP operating manual. Copy-paste the `BEGIN`/`END` block into your project's `QWEN.md`, `CLAUDE.md`, or `AGENTS.md`. Contains: five-tool reference, `NodeFilter` / edge taxonomy, ontology glossary, recovery playbook, and navigation patterns. Self-contained — no external file dependencies.

2. **[`/explore-codebase`](./skills/explore-codebase/SKILL.md)** (for hosts with skill discovery) — single self-contained skill with the complete operating manual. If your MCP host supports skill discovery (Claude Code, Qwen Code, Cursor), load `/explore-codebase` to get the full tool reference, edge taxonomy, decision tree, and recovery playbook in one shot.

Also: **[`docs/MANUAL-VERIFICATION-CHECKLIST.md`](./docs/MANUAL-VERIFICATION-CHECKLIST.md)** — 7-phase agent-driven verification you run after indexing your real project.

---

## The five tools, at a glance

| Tool | Purpose | Required args |
|---|---|---|
| `search` | Locate nodes by NL / code text. | `query` |
| `find` | Locate nodes by structured filter. | `kind`, `filter` |
| `describe` | Full record + edge counts for one node. | `id` |
| `resolve` | Identifier-shaped lookup (FQN-collision-safe). Returns `one` / `many` / `none`. | `identifier` |
| `neighbors` | Graph walk, one hop. | `ids`, `direction`, `edge_types` |

Full schemas, `NodeFilter` / `EdgeFilter` semantics, and the hints contract live in [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md). Edge types and traversal directions are listed in [`docs/EDGE-NAVIGATION.md`](./docs/EDGE-NAVIGATION.md).

### Three-layer architecture

Layer 1 (storage) → Layer 2 (5 MCP tools) → Layer 3 (skill). The [`/explore-codebase`](./skills/explore-codebase/SKILL.md) skill provides the full operating manual for Layer 2. See the [architecture diagram in `skills/README.md`](./skills/README.md#three-layer-architecture).

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
| Setup | `update` | Refresh shipped artifacts (skill, agent, MCP entry) after pip upgrade. |
| Lifecycle | `init` | First-time index. Refuses if artifacts already exist. |
| Lifecycle | `increment` | CocoIndex catch-up + incremental Kuzu update. `--vectors-only` for Lance only. |
| Lifecycle | `reprocess` | Full Lance + Kuzu rebuild. `--vectors-only` / `--graph-only` for a single phase. |
| Lifecycle | `erase` | Delete index artifacts. Requires `--yes` or TTY confirm. |
| Introspection | `meta`, `tables`, `diagnose-ignore`, `unresolved-calls` | Health, table listing, ignore-layer diagnostics, receiver-failure call sites. |
| Analysis | `analyze-pr` | Blast-radius / risk from a unified diff. |

---

## Further reading

| Document | What's in it |
|---|---|
| [`docs/paper/paper.pdf`](./docs/paper/paper.pdf) | Architecture report — design rationale, GPS metaphor, three-layer architecture, design principles, future work. |
| [`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md) | Agent-facing guide. Copy-paste into `QWEN.md` / `CLAUDE.md` / `AGENTS.md`. |
| [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) | Environment variables, project YAML, graph ontology, brownfield overrides, ignore patterns. |
| [`docs/JAVA-CODEBASE-RAG-CLI.md`](./docs/JAVA-CODEBASE-RAG-CLI.md) | CLI operator playbook: workflows, exit codes, env alignment. |
| [`docs/EDGE-NAVIGATION.md`](./docs/EDGE-NAVIGATION.md) | MCP-traversable edges, directions, dot-key composition. |
| [`skills/`](./skills/) | Single `/explore-codebase` skill — complete MCP operating manual for hosts with skill discovery (alternative to copy-pasting AGENT-GUIDE). See [`skills/README.md`](./skills/README.md). |
| [`docs/MANUAL-VERIFICATION-CHECKLIST.md`](./docs/MANUAL-VERIFICATION-CHECKLIST.md) | 7-phase agent-driven verification after indexing your project. |
| [`docs/CODEBASE_REQUIREMENTS.md`](./docs/CODEBASE_REQUIREMENTS.md) | Assumptions about your Java repo + per-file edit map for non-conforming codebases. |
| [`automation/cursor_propose_only/README.md`](./automation/cursor_propose_only/README.md) | Optional proposal orchestration workflow (single-command autopilot, planning bundles, automated execution/review loops). |
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

The default embedding model is `sentence-transformers/all-MiniLM-L6-v2` (downloaded on first `init`). Override via the `EMBEDDING_MODEL` env var — see [`docs/CONFIGURATION.md` §1](./docs/CONFIGURATION.md#1-environment-variables).

---

## Roadmap (graph layer)

- `get_service_topology` — microservice-level summary aggregating `HTTP_CALLS` / `ASYNC_CALLS`.
- Agentic routing layer (query classifier → vector / graph / both).
- Optional `codegraph_nodes` LanceDB table embedding symbol summaries so the graph itself is vector-searchable.
