# `java-codebase-rag` CLI — operator guide

The **`java-codebase-rag`** command is the **operator surface** for this bundle: index lifecycle (`init` / `increment` / `reprocess` / `erase`), graph and Lance health (`meta`, `tables`), ignore diagnostics, and PR diff analysis. It is **not** the MCP navigation surface (that is `search` / `find` / `describe` / `neighbors` / `resolve` on the MCP server — this CLI is lifecycle and introspection only). For agents driving the MCP server, see [`AGENT-GUIDE.md`](./AGENT-GUIDE.md).

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

- **TTY:** human-readable `pprint` of the payload on stdout (except **successful selective `reprocess`** with `--vectors-only` / `--graph-only`, which prints `Rebuilt:` / `Skipped:` lines instead of dumping the full dict).
- **Piped / non-TTY:** **single JSON object** per invocation on stdout (no trailing noise). Use this in scripts and CI.
- **Lifecycle stderr:** `init`, `increment`, `reprocess`, and `erase` stream subprocess progress (and relayed child stdout) to **stderr**; pass **`--quiet`** to suppress that stream. **stdout** stays the JSON/pprint payload only.

Example:

```bash
java-codebase-rag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq .ontology_version
```

## Environment variables (summary)

| Variable | Role |
| -------- | ---- |
| `JAVA_CODEBASE_RAG_INDEX_DIR` | Root directory for Lance tables, the Kuzu file `code_graph.kuzu`, and default cocoindex state. Default: `./.java-codebase-rag/` under the resolved Java tree root. Overridden by `--index-dir` or YAML `index_dir:`. |
| `SBERT_MODEL` / `SBERT_DEVICE` | Embedding model and device; must match the index. Overridden by `--embedding-model` / `--embedding-device` or YAML `embedding.model` / `embedding.device`. |
| `JAVA_CODEBASE_RAG_DEBUG_CONTEXT` | Verbose stderr logging for context expansion (diagnostic). |
| `JAVA_CODEBASE_RAG_RUN_HEAVY` | Test-only gate for slow end-to-end indexer tests (`pytest`). |

**Precedence** (when a knob exists in more than one place): **CLI flag > env var > YAML (`.java-codebase-rag.yml`) > built-in default**.

Only the variable names in the table above are read as configuration.

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
| `1` | Subcommand-specific failure (e.g. `analyze-pr` cannot read diff, graph missing, invalid path for `diagnose-ignore`). For **`reprocess`**, a **requested phase subprocess** ran and exited non-zero (see `phases_run` in stdout JSON). |
| `2` | No subcommand / help printed; **`init`** refused because the index dir is non-empty; **`erase`** refused in non-TTY without `--yes`; **`meta`** when graph payload reports `success: false`; unhandled internal error in `main`. For **`reprocess`**, invalid flag combination (handled like other argparse errors), or a **setup failure before any phase subprocess was spawned** (`phases_run: []` in the JSON payload — e.g. cocoindex binary missing next to this Python, flow file missing). |

## Lifecycle subcommands

### `init`

Creates a **new** index (cocoindex catch-up from empty + full `build_ast_graph.py`). **Refuses** if `code_graph.kuzu` or `code_index_*` Lance tables already exist under the resolved index dir (exit **2**).

```bash
java-codebase-rag init --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

### `increment`

Runs cocoindex **catch-up** without a full Lance reprocess. **Does not** rebuild Kuzu. Every run prints a **multi-line stderr warning** that graph navigation may be stale until you run `reprocess` (see [`propose/completed/CLI-SCENARIOS-PROPOSE.md`](../propose/completed/CLI-SCENARIOS-PROPOSE.md) Appendix A for the contract).

```bash
java-codebase-rag increment --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

### `reprocess`

**Default (no extra flags):** full **Lance** reprocess (cocoindex `--full-reprocess`) then full **Kuzu** rebuild via `build_ast_graph.py`, in that order. This remains the recommended **coherence** operation when both stores might be out of date.

**Selective flags (mutually exclusive):**

- `--vectors-only` — runs only the cocoindex full reprocess phase; does **not** invoke the graph builder.
- `--graph-only` — runs only `build_ast_graph.py`; does **not** invoke cocoindex.

Passing **both** flags is rejected by argparse **before** any subprocess runs. The error is printed on **stderr** in this form (wording may vary slightly with Python/argparse version):

```text
java-codebase-rag: argument --graph-only: not allowed with argument --vectors-only
```

Use `java-codebase-rag reprocess --help` for the live synopsis.

#### Drift warning (stderr)

After a **successful** selective run, the CLI prints **exactly one** line to **stderr** naming the store that was **not** rebuilt. **`--quiet` does not suppress this line** (quiet only affects subprocess verbosity). There is no extra exit code for drift; scripts should treat stderr as informational.

#### JSON payload: `phases_run`

The stdout JSON includes an additive list field `phases_run`: which phases actually **spawned** subprocesses, in order (`"vectors"`, `"graph"`). Examples:

- Default success after both phases: `["vectors", "graph"]`
- Default run where cocoindex fails before the graph step: `["vectors"]` (graph never started)
- `--vectors-only` success: `["vectors"]`
- `--graph-only` success: `["graph"]`
- Setup failure before any phase (missing cocoindex binary, missing bundled flow file, or pipeline preflight `126`/`127` stubs): `[]`

Because `exit_code` and `graph_exit_code` can be `null` in multiple situations, **prefer branching on `phases_run` first**, then on the relevant per-phase exit field. **Asymmetry:** `--vectors-only` reports the cocoindex process in `exit_code` (and leaves `graph_exit_code` null); `--graph-only` leaves top-level `exit_code` null and reports the graph builder in `graph_exit_code`, so scripts that only read `exit_code` miss graph-only outcomes unless they branch on `phases_run` / `graph_exit_code`.

```bash
java-codebase-rag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
java-codebase-rag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --vectors-only --quiet
java-codebase-rag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --graph-only --quiet
```

### `erase`

Deletes cocoindex state, the Kuzu directory, and Lance tables under the index dir. Requires **`--yes`** or interactive confirmation on a TTY. Non-TTY without `--yes` exits **2**.

```bash
java-codebase-rag erase --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --yes
```

### Hidden `refresh` alias

`java-codebase-rag refresh` runs **`reprocess`**. Prefer **`reprocess`** in scripts.

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

### `unresolved-calls`

Lists or aggregates **receiver-failure** call sites stored as `UnresolvedCallSite` (not on `CALLS` after ontology 15 PR-3). Reasons: `phantom_unresolved_receiver`, `chained_receiver`.

```bash
java-codebase-rag unresolved-calls stats --by microservice --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
java-codebase-rag unresolved-calls list --method-id sym:... --limit 100 --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

`stats --by` accepts `reason`, `microservice`, or `caller_role` (declaring type role of the caller method).

## Analysis: `analyze-pr`

Maps a **unified diff** to changed symbols, blast radius, routes touched, and risk band. Requires a **built Kuzu graph** at `<index-dir>/code_graph.kuzu`.

Provide exactly one of:

- `--diff-file PATH`
- `--diff-stdin` (read diff from stdin)

```bash
git diff > /tmp/pr.diff
java-codebase-rag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

Paths in the diff should align with **`Symbol.filename`** layout in the graph (project-relative Java paths). Use this from **PR-triage scripts** or Cursor skills; PR mapping is **CLI-only** (the MCP exposes retrieval tools only).

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

Prefer **`java-codebase-rag reprocess --graph-only`** when you only need Kuzu rebuilt from the current Lance snapshot. To run the graph builder **without** going through the CLI (advanced / scripting):

```bash
.venv/bin/python build_ast_graph.py --source-root /path/to/java/repo --kuzu-path /path/to/.java-codebase-rag/code_graph.kuzu --verbose
```

## See also

- [README.md](../README.md) — env vars, MCP tool table, ignore layout.
- [CODEBASE_REQUIREMENTS.md](./CODEBASE_REQUIREMENTS.md) — repo layout, brownfield, when to rebuild.
- [MANUAL-VERIFICATION-CHECKLIST.md](./MANUAL-VERIFICATION-CHECKLIST.md) — phased checks that mix CLI + MCP.
