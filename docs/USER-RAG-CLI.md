# `user-rag` CLI — operator guide

The **`user-rag`** command is the **operator surface** for this bundle: index rebuild, graph health, LanceDB table inspection, ignore diagnostics, and PR diff analysis. It is **not** the MCP tool surface (that is `search` / `find` / `describe` / `neighbors` only). For agents driving the MCP server, see [`AGENT-GUIDE.md`](./AGENT-GUIDE.md).

## Install and discovery

After installing the package (e.g. editable install from the repo root), the console script is on your `PATH`:

```bash
pip install -e .
user-rag --help
```

If `user-rag` is missing, run the module entrypoint:

```bash
python -m user_rag.cli --help
```

## Output mode

- **TTY:** human-readable `pprint` of the payload on stdout.
- **Piped / non-TTY:** **single JSON object** per invocation on stdout (no trailing noise). Use this in scripts and CI.

Example:

```bash
user-rag meta --kuzu-path /data/graph.kuzu | jq .ontology_version
```

## Environment variables (summary)

Full tables and semantics live in [README.md](../README.md) (Environment + AST graph sections). The CLI **also** honours these when you pass flags (flags set `os.environ` before running handlers):

| Variable | Role |
| -------- | ---- |
| `LANCEDB_MCP_PROJECT_ROOT` | Java tree root (module / microservice resolution, default project root for relative paths). Overridden by `--source-root`. |
| `KUZU_DB_PATH` | Kuzu database path. Overridden by `--kuzu-path`. |
| `LANCEDB_URI` | LanceDB directory / URI. Overridden by `--lancedb-path`. |
| `LANCEDB_MCP_ALLOW_REFRESH` | Must be `1` / `true` / `yes` for **`user-rag refresh`** to run the full CocoIndex + graph pipeline. Graph-only rebuilds use `build_ast_graph.py` and do **not** require this flag. |
| `SBERT_MODEL` / `SBERT_DEVICE` | Embedding model for refresh / search (must match the index). |

## Shared flags

Every subcommand accepts (all optional):

| Flag | Sets |
| ---- | ---- |
| `--source-root DIR` | `LANCEDB_MCP_PROJECT_ROOT` |
| `--kuzu-path DIR` | `KUZU_DB_PATH` (and resets the in-process `KuzuGraph` singleton) |
| `--lancedb-path DIR` | `LANCEDB_URI` |

Relative paths for `diagnose-ignore <path>` are resolved against `--source-root` when given, otherwise against the current project root helper in `server.py`.

## Exit codes (practical)

| Code | Typical meaning |
| ---- | ---------------- |
| `0` | Success (payload may still report logical failures inside JSON for some commands — always parse stdout in scripts). |
| `1` | Subcommand-specific failure (e.g. `analyze-pr` cannot read diff, graph missing, invalid path for `diagnose-ignore`). **`refresh`:** CocoIndex or graph builder exited non-zero. |
| `2` | No subcommand / help printed; **`refresh`** pre-launch failure (e.g. refresh disabled — see below); **`meta`** when graph payload reports `success: false`; unhandled internal error in `main`. |

**`user-rag refresh`:** If `LANCEDB_MCP_ALLOW_REFRESH` is not enabled, the pipeline returns JSON with `success: false` and the CLI exits **`2`** (internal / pre-launch). If CocoIndex or the graph step fails, exit code is **`2`** when `exit_code` is absent in the payload, else **`1`**.

## Subcommands

### `refresh`

Full **CocoIndex reprocess** (Lance chunks) plus **`build_ast_graph.py`** for Kuzu.

- **Requires:** `LANCEDB_MCP_ALLOW_REFRESH=1` (or `true` / `yes`).
- **Optional:** `--quiet` (less verbose graph build).
- **Slow:** treat as a batch job, not an agent tool call.

```bash
export LANCEDB_MCP_ALLOW_REFRESH=1
user-rag refresh --source-root /path/to/java/repo \
  --kuzu-path /data/code_graph.kuzu \
  --lancedb-path /data/lancedb_data --quiet
```

**Graph only (no Lance re-embed):** use `python build_ast_graph.py --source-root ... --kuzu-path ...` — no `LANCEDB_MCP_ALLOW_REFRESH` check.

### `meta`

Kuzu / graph metadata: ontology version, counts, `edge_counts`, route stats, match breakdowns, etc. Read **`ontology_version`** after upgrades or fixture builds.

```bash
user-rag meta --source-root /path/to/repo --kuzu-path /data/code_graph.kuzu
```

### `tables`

LanceDB URI, embedding model, table list, and graph summary block (same helper as the old MCP “list tables” payload). Handy for “is the index wired?” without opening files.

```bash
user-rag tables --lancedb-path /data/lancedb_data
```

### `diagnose-ignore`

Explains **why a path** is ignored or not ignored by the layered ignore rules (builtin + project + gitignore-style). First argument is **`path`** (relative to project root or absolute under it).

```bash
user-rag diagnose-ignore src/main/generated/Foo.java --source-root /path/to/repo
```

### `analyze-pr`

Maps a **unified diff** to changed symbols, blast radius, routes touched, and risk band. Requires a **built Kuzu graph** at `KUZU_DB_PATH` / `--kuzu-path`.

Provide exactly one of:

- `--diff-file PATH`
- `--diff-stdin` (read diff from stdin)

```bash
git diff > /tmp/pr.diff
user-rag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/repo --kuzu-path /data/code_graph.kuzu
```

Paths in the diff should align with **`Symbol.filename`** layout in the graph (project-relative Java paths). Use this from **PR-triage scripts** or Cursor skills instead of the removed MCP `analyze_pr` tool.

## Suggested workflows

### 1. Quick health after a build

```bash
user-rag meta --kuzu-path "$KUZU_DB_PATH" | jq '{ontology_version, parse_errors, counts, edge_counts}'
user-rag tables --lancedb-path "$LANCEDB_URI" | jq '.tables | keys'
```

### 2. “Why isn’t this file in the index?”

```bash
user-rag diagnose-ignore path/inside/repo/to/File.java --source-root /path/to/repo
```

### 3. Full re-index (operator / CI)

```bash
export LANCEDB_MCP_ALLOW_REFRESH=1
user-rag refresh --source-root /path/to/repo \
  --kuzu-path /data/code_graph.kuzu \
  --lancedb-path /data/lancedb_data --quiet
user-rag meta --kuzu-path /data/code_graph.kuzu | jq .ontology_version
```

### 4. PR risk pass (local)

```bash
git diff origin/main...HEAD > /tmp/pr.diff
user-rag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/repo --kuzu-path /data/code_graph.kuzu | jq '{risk_score,risk_band,blast_radius_total}'
```

## See also

- [README.md](../README.md) — env vars, MCP tool table, CLI synopsis, migration from v1 MCP ops.
- [CODEBASE_REQUIREMENTS.md](../CODEBASE_REQUIREMENTS.md) — repo layout, brownfield, when to rebuild.
- [MANUAL-VERIFICATION-CHECKLIST.md](./MANUAL-VERIFICATION-CHECKLIST.md) — phased checks that mix CLI + MCP.
