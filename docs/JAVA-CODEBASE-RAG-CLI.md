# `java-codebase-rag` CLI — operator guide

The **`java-codebase-rag`** command is the **operator surface** for this bundle: index lifecycle (`init` / `increment` / `reprocess` / `erase`), graph and Lance health (`meta`, `tables`), ignore diagnostics, and PR diff analysis. It is **not** the MCP tool surface (that is `search` / `find` / `describe` / `neighbors` only). For agents driving the MCP server, see [`AGENT-GUIDE.md`](./AGENT-GUIDE.md).

## Install and discovery

After installing the package (e.g. editable install from the repo root), the console script is on your `PATH`:

```bash
.venv/bin/pip install -e .
java-codebase-rag --help
```

If `java-codebase-rag` is missing, run the module entrypoint:

```bash
.venv/bin/python -m java_codebase_rag.cli --help
```

## Output mode

- **TTY:** human-readable `pprint` of the payload on stdout.
- **Piped / non-TTY:** **single JSON object** per invocation on stdout (no trailing noise). Use this in scripts and CI.

Example:

```bash
java-codebase-rag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq .ontology_version
```

## Environment variables (summary)

| Variable | Role |
| -------- | ---- |
| `JAVA_CODEBASE_RAG_INDEX_DIR` | Root directory for Lance tables, `code_graph.kuzu/`, and default cocoindex state. Default: `./.java-codebase-rag/` under the resolved Java tree root. Overridden by `--index-dir` or YAML `index_dir:`. |
| `SBERT_MODEL` / `SBERT_DEVICE` | Embedding model and device; must match the index. Overridden by `--embedding-model` / `--embedding-device` or YAML `embedding.model` / `embedding.device`. |
| `JAVA_CODEBASE_RAG_DEBUG_CONTEXT` | Verbose stderr logging for context expansion (diagnostic). |
| `JAVA_CODEBASE_RAG_RUN_HEAVY` | Test-only gate for slow end-to-end indexer tests (`pytest`). |

**Precedence** (when a knob exists in more than one place): **CLI flag > env var > YAML (`.java-codebase-rag.yml`) > built-in default**.

Legacy names (`LANCEDB_URI`, `LANCEDB_MCP_*`, `KUZU_DB_PATH`, `COCOINDEX_DB`, …) are **never** applied; the CLI may emit a **one-line stderr hint** if it detects them. Rename config with:

```bash
mv .lancedb-mcp.yml .java-codebase-rag.yml
# or: mv .lancedb-mcp.yaml .java-codebase-rag.yaml
```

If you still have data under `lancedb_data/` and want a single index dir:

```bash
mv lancedb_data .java-codebase-rag
```

Move layered ignore files the same way:

```bash
mkdir -p .java-codebase-rag
mv .lancedb-mcp/ignore .java-codebase-rag/ignore
```

## Shared flags

Every subcommand accepts (all optional unless noted):

| Flag | Meaning |
| ---- | ------- |
| `--source-root DIR` | Java repository root (default: current working directory). |
| `--index-dir DIR` | Index directory (default: `./.java-codebase-rag` under the resolved source root, or `JAVA_CODEBASE_RAG_INDEX_DIR`). |
| `--embedding-model` / `--embedding-device` | Override embedding resolution for subprocesses that honor env. |

Kuzu always resolves to `<index-dir>/code_graph.kuzu`.

Relative paths for `diagnose-ignore <path>` are resolved against the MCP/CLI project root helper (`--source-root` when given, else cwd semantics described in `--help`).

## Exit codes (practical)

| Code | Typical meaning |
| ---- | ---------------- |
| `0` | Success (payload may still report logical failures inside JSON for some commands — always parse stdout in scripts). |
| `1` | Subcommand-specific failure (e.g. `analyze-pr` cannot read diff, graph missing, invalid path for `diagnose-ignore`). |
| `2` | No subcommand / help printed; **`init`** refused because the index dir is non-empty; **`erase`** refused in non-TTY without `--yes`; **`meta`** when graph payload reports `success: false`; unhandled internal error in `main`. |

## Lifecycle subcommands

### `init`

Creates a **new** index (cocoindex catch-up from empty + full `build_ast_graph.py`). **Refuses** if `code_graph.kuzu` or `code_index_*` Lance tables already exist under the resolved index dir (exit **2**).

```bash
java-codebase-rag init --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

### `increment`

Runs cocoindex **catch-up** without a full Lance reprocess. **Does not** rebuild Kuzu. Every run prints a **multi-line stderr warning** that graph navigation may be stale until you run `reprocess` (see [`propose/CLI-SCENARIOS-PROPOSE.md`](../propose/CLI-SCENARIOS-PROPOSE.md) Appendix A for the contract).

```bash
java-codebase-rag increment --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

### `reprocess`

Full **Lance reprocess** + **full Kuzu rebuild** (same pipeline as the historical `refresh` command).

```bash
java-codebase-rag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

### `erase`

Deletes cocoindex state, the Kuzu directory, and Lance tables under the index dir. Requires **`--yes`** or interactive confirmation on a TTY. Non-TTY without `--yes` exits **2**.

```bash
java-codebase-rag erase --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --yes
```

### Hidden `refresh` alias

Invoking `java-codebase-rag refresh` runs **`reprocess`** and prints a one-line **stderr** deprecation warning. The alias is removed after the next release; migrate scripts to `reprocess`.

## Introspection subcommands

### `meta`

Graph metadata, ontology version, counts, `edge_counts`, plus resolved embedding fields and provenance (`embedding_model_source`, `embedding_device_source`, `index_dir`, `kuzu_path`, …).

```bash
java-codebase-rag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

### `tables`

Lance table listing and embedding summary (same helper as the server’s table introspection).

```bash
java-codebase-rag tables --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

### `diagnose-ignore`

Explains **why a path** is ignored or not ignored by the layered ignore rules (builtin + project `.java-codebase-rag/ignore` + nested ignore files + gitignore layers).

```bash
java-codebase-rag diagnose-ignore src/main/generated/Foo.java --source-root /path/to/java/repo
```

## Analysis: `analyze-pr`

Maps a **unified diff** to changed symbols, blast radius, routes touched, and risk band. Requires a **built Kuzu graph** at `<index-dir>/code_graph.kuzu`.

Provide exactly one of:

- `--diff-file PATH`
- `--diff-stdin` (read diff from stdin)

```bash
git diff > /tmp/pr.diff
java-codebase-rag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

Paths in the diff should align with **`Symbol.filename`** layout in the graph (project-relative Java paths). Use this from **PR-triage scripts** or Cursor skills instead of the removed MCP `analyze_pr` tool.

## Suggested workflows

### 1. Quick health after a build

```bash
java-codebase-rag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq '{ontology_version, parse_errors, counts, edge_counts}'
java-codebase-rag tables --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq '.tables | keys'
```

### 2. “Why isn’t this file in the index?”

```bash
java-codebase-rag diagnose-ignore path/inside/repo/to/File.java --source-root /path/to/java/repo
```

### 3. Full re-index (operator / CI)

```bash
java-codebase-rag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
java-codebase-rag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq .ontology_version
```

### 4. PR risk pass (local)

```bash
git diff origin/main...HEAD > /tmp/pr.diff
java-codebase-rag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq '{risk_score,risk_band,blast_radius_total}'
```

## Graph-only escape hatch

To rebuild **only** Kuzu (no Lance re-embed), call the graph builder directly:

```bash
.venv/bin/python build_ast_graph.py --source-root /path/to/java/repo --kuzu-path /path/to/.java-codebase-rag/code_graph.kuzu --verbose
```

## See also

- [README.md](../README.md) — env vars, MCP tool table, ignore layout, migration table.
- [CODEBASE_REQUIREMENTS.md](../CODEBASE_REQUIREMENTS.md) — repo layout, brownfield, when to rebuild.
- [MANUAL-VERIFICATION-CHECKLIST.md](./MANUAL-VERIFICATION-CHECKLIST.md) — phased checks that mix CLI + MCP.
