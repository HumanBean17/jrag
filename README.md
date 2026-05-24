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

## Install

```bash
pip install java-codebase-rag
```

Python **3.11+** required. After install, `java-codebase-rag --help` should print the CLI groups.

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

### Claude Code

With the package installed, the console script `java-codebase-rag-mcp` is on your `PATH`. Register it project-scoped:

```bash
claude mcp add --transport stdio java-codebase-rag -- java-codebase-rag-mcp
```

Then set env vars (`JAVA_CODEBASE_RAG_INDEX_DIR`, `JAVA_CODEBASE_RAG_SOURCE_ROOT`, `SBERT_MODEL`, …) in `.mcp.json` or your shell profile. For a project-scoped `.mcp.json` template, see [`mcp.json.example`](./mcp.json.example). Official docs: [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings).

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

- **[`docs/AGENT-GUIDE.md`](./docs/AGENT-GUIDE.md)** — standalone MCP operating manual (copy-paste into `QWEN.md` / `CLAUDE.md` / `AGENTS.md`): five tools, `NodeFilter`, edge taxonomy, required `neighbors` arguments, ontology glossary, recovery playbook, slash-style aliases.
- **[`skills/`](./skills/)** — user-facing navigation and workflow skills for java-codebase-rag consumers. Skills are `SKILL.md` files; agents discover them via slash-names (`/callees`, `/routes`, etc.). See [`propose/active/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](./propose/active/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md) for the full Tier 1 + Tier 2 skill set.
- **[`docs/MANUAL-VERIFICATION-CHECKLIST.md`](./docs/MANUAL-VERIFICATION-CHECKLIST.md)** — 7-phase agent-driven verification you run after indexing your real project.

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
| Lifecycle | `init` | First-time index. Refuses if artifacts already exist. |
| Lifecycle | `increment` | CocoIndex catch-up (Lance only); Kuzu stays stale until `reprocess`. |
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
| [`skills/`](./skills/) | User-facing skills for java-codebase-rag consumers. Navigation and workflow skills (Tier 1 + Tier 2) planned — see [`propose/active/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](./propose/active/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md). |
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

The `cocoindex` package is **only** needed for lifecycle commands that run the indexer (`init`, `increment`, `reprocess`, `erase`). Search and MCP navigation work without it.

The default embedding model is `sentence-transformers/all-MiniLM-L6-v2` (downloaded on first `init`). Override via the `EMBEDDING_MODEL` env var — see [`docs/CONFIGURATION.md` §1](./docs/CONFIGURATION.md#1-environment-variables).

---

## Roadmap (graph layer)

- `get_service_topology` — microservice-level summary aggregating `HTTP_CALLS` / `ASYNC_CALLS`.
- Agentic routing layer (query classifier → vector / graph / both).
- Incremental Kuzu updates (per-changed-file) — see [`propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`](./propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md) and [`propose/INDEX-AUTO-MODE-PROPOSE.md`](./propose/INDEX-AUTO-MODE-PROPOSE.md).
- Optional `codegraph_nodes` LanceDB table embedding symbol summaries so the graph itself is vector-searchable.
