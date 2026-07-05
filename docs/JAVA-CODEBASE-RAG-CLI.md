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

## Setup commands

### `install`

Interactive setup wizard that walks users through Java source detection, embedding model selection, agent host configuration, artifact deployment, and YAML config generation. Use `--non-interactive` for CI/automation.

```bash
# Interactive mode
java-codebase-rag install

# Non-interactive mode (requires at least one --agent)
java-codebase-rag install --non-interactive --agent claude-code
java-codebase-rag install --non-interactive --agent claude-code --agent qwen-code

# With custom embedding model
java-codebase-rag install --model /path/to/model

# User-scope installation (available globally)
java-codebase-rag install --scope user
```

**Flags:**
- `--non-interactive` — Run without prompts (requires `--agent`).
- `--agent {claude-code,qwen-code,gigacode}` — Agent host to configure (can be passed multiple times).
- `--scope {project,user}` — Installation scope (default: `project`). Project scope writes to `.<host>/` in the project repo; user scope writes to `~/.<host>/` (globally available).
- `--model MODEL` — Embedding model path or `auto` (default: `auto`, downloads `sentence-transformers/all-MiniLM-L6-v2` on first run).
- `--quiet` / `-q` — Suppress the indexing progress stream on stderr (wizard prompts unchanged).
- `--verbose` / `-v` — Raw-relay subprocess output during the indexing sub-step (no progress bar).

**Exit codes:**
- `0` — Success (all stages completed).
- `1` — Partial success (some stages failed). Re-run `install` to retry failed stages.
- `2` — Fatal error (no Java files found, required flag missing).

**Stages:**
1. Java source detection — Maven/Gradle module roots.
2. Embedding model selection — auto-download or local path.
3. Agent host selection — Claude Code, Qwen Code, GigaCode (multi-select).
4. Install scope — project or user.
5. MCP entrypoint resolution + artifact deployment — config, skill, agent files.
6. Index + finish — YAML generation, `.gitignore` update, `init`. Stage 6's indexing sub-step renders the unified `Vectors → Optimize → Graph` progress on **stderr** (see [Indexing progress](#indexing-progress-stderr)); the wizard's conversational stdout is unchanged.

**Re-running `install`:** If `.java-codebase-rag.yml` exists, the installer shows current values and offers "Update" (pre-filled) or "Start fresh". Existing MCP entries are updated in-place (merged, not duplicated). Skill/agent files trigger overwrite confirmation.

### `update`

Post-upgrade refresh: overwrites skill and agent files with the latest shipped versions and updates the MCP command path. If an index exists, also runs an incremental Lance + graph catch-up (same as `increment`). Requires a prior `install` run.

```bash
# Refresh after pip upgrade
pip install --upgrade java-codebase-rag
java-codebase-rag update

# Preview changes without writing
java-codebase-rag update --dry-run

# Force overwrite all artifacts
java-codebase-rag update --force
```

**Flags:**
- `--force` — Overwrite all artifacts even if content matches.
- `--dry-run` — Print changes without writing files.
- `--quiet` / `-q` — Suppress the indexing progress stream on stderr (wizard stdout unchanged).
- `--verbose` / `-v` — Raw-relay subprocess output during the indexing sub-step (no progress bar).

**Behavior:**
- Detects previously configured agent hosts (scans both project-level and user-level config files).
- Refreshes skill and agent files (versioned assets from the package).
- Updates MCP entrypoint path if `java-codebase-rag-mcp` has moved.
- Runs an incremental index update (Lance + graph) if an index exists — same as `java-codebase-rag increment`. The indexing sub-step renders the unified `Vectors → Optimize → Graph` progress on **stderr** (see [Indexing progress](#indexing-progress-stderr)); it no longer runs silently.

**Exit codes:**
- `0` — Success.
- `1` — Partial failure (some artifacts failed to write).
- `2` — No configured hosts found.

## Output mode

- **TTY:** human-readable `pprint` of the payload on stdout (except **successful selective `reprocess`** with `--vectors-only` / `--graph-only`, which prints `Rebuilt:` / `Skipped:` lines instead of dumping the full dict).
- **Piped / non-TTY:** **single JSON object** per invocation on stdout (no trailing noise). Use this in scripts and CI.
- **Lifecycle stderr:** `init`, `increment`, `reprocess`, `install`, `update`, and `erase` stream subprocess progress (and relayed child stdout) to **stderr**; pass **`--quiet`** to suppress that stream. **stdout** stays the JSON/pprint payload (`init`/`increment`/`reprocess`) or the wizard conversational text (`install`/`update`) only.

Example:

```bash
java-codebase-rag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq .ontology_version
```

### Indexing progress (stderr)

All five lifecycle commands that build the index (`init`, `increment`, `reprocess`, `install`, `update`) render the **same unified progress** on **stderr** during indexing: a header line, a three-phase list `Vectors → Optimize → Graph`, and a footer line. The phase list is the single source of truth for "what's happening right now":

- **Vectors** — the `cocoindex update` Lance catch-up / full reprocess.
- **Optimize** — the serialized Lance table compaction that runs after a successful vectors phase.
- **Graph** — the `build_ast_graph.py` LadybugDB/LadybugDB build (full or incremental).

**Determinate vs indeterminate per command:**

| Phase | Determinate? |
| ----- | ------------ |
| `Vectors` (full `init` / `reprocess`) | Approximately determinate — a pre-walk estimates the file count; the bar **clamps to 100% on completion** (the pre-walk overstates by ignored/empty files). |
| `Vectors` (incremental `increment` / `update`) | Indeterminate — CocoIndex's `memo=True` cache only calls the per-file function for changed files, so no denominator is known up front. A pulsing bar plus a "files touched: N" counter. |
| `Optimize` | Always indeterminate (no item count exposed by Lance compaction). |
| `Graph` (full `init` / `reprocess`) | Determinate — pass 1 does a count-first filtered walk for an exact total; passes 2–6 are six known steps. |
| `Graph` (incremental `increment` / `update`) | Determinate when it runs; falls back to a full rebuild on schema change. |

**Flags, TTY, and failure:**

| Mode | Behaviour |
| ---- | --------- |
| TTY (default) | `rich` `Live` region — the multi-line phase display (spinner + bar + `%` + ETA). |
| Non-TTY / CI | `rich` auto-disables; concise throttled stderr lines (~every 5 s per phase + a terminal line) so CI logs still show progress. |
| `--quiet` / `-q` | Suppresses the entire progress stream (no header, phases, or footer). The stdout payload is unchanged. |
| `--verbose` / `-v` | Bypasses parsing; relays raw subprocess output verbatim (Lance warnings, brownfield events, the raw `JCIRAG_PROGRESS` protocol lines). No `Live` region. |
| Phase failure | The failing phase renders a red `✗`; the footer carries `(exit=N)`. The `rich` `Live` region is torn down cleanly so the error stays visible. |
| Missing `cocoindex` / builder binary | The pre-spawn stub emits a `status=failed` line; no phase is left hung at `running`. |

> **Behaviour change (this release).** `install` and `update` now emit their indexing progress on **stderr** (previously `install` printed indexing chatter to stdout, and `update` ran the whole indexing step with `quiet=True` — completely silent). The wizard conversational stdout for both commands is otherwise unchanged. `update`'s previously-ignored `--quiet` / `--verbose` flags, and `install`'s previously-ignored `--verbose` flag, are now wired through (`install` already honored `--quiet`).

## Environment variables (summary)

| Variable | Role |
| -------- | ---- |
| `JAVA_CODEBASE_RAG_INDEX_DIR` | Root directory for Lance tables, the LadybugDB file `code_graph.lbug`, and default cocoindex state. Default: `./.java-codebase-rag/` under the resolved Java tree root. Overridden by `--index-dir` or YAML `index_dir:`. |
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

LadybugDB always resolves to `<index-dir>/code_graph.lbug`.

Relative paths for `diagnose-ignore <path>` are resolved against the MCP/CLI project root helper (`--source-root` when given, else cwd semantics described in `--help`).

## Exit codes (practical)

| Code | Typical meaning |
| ---- | ---------------- |
| `0` | Success (payload may still report logical failures inside JSON for some commands — always parse stdout in scripts). |
| `1` | Subcommand-specific failure (e.g. `analyze-pr` cannot read diff, graph missing, invalid path for `diagnose-ignore`). For **`reprocess`**, a **requested phase subprocess** ran and exited non-zero (see `phases_run` in stdout JSON). |
| `2` | No subcommand / help printed; **`init`** refused because the index dir is non-empty; **`erase`** refused in non-TTY without `--yes`; **`meta`** when graph payload reports `success: false`; unhandled internal error in `main`. For **`reprocess`**, invalid flag combination (handled like other argparse errors), or a **setup failure before any phase subprocess was spawned** (`phases_run: []` in the JSON payload — e.g. cocoindex binary missing next to this Python, flow file missing). |

## Lifecycle subcommands

### `init`

Creates a **new** index (cocoindex catch-up from empty + full `build_ast_graph.py`). **Refuses** if `code_graph.lbug` or `code_index_*` Lance tables already exist under the resolved index dir (exit **2**).

```bash
java-codebase-rag init --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

### `increment`

Runs cocoindex **catch-up** and **incremental LadybugDB graph update**. Only changed files and their single-hop dependents are re-parsed and re-written to the graph. Passes 5–6 (client/producer extraction and cross-service matching) run globally. Falls back to full `reprocess` if:
- No previous graph exists (first run)
- Graph schema is outdated (missing `source_file` on edges)
- Previous incremental run crashed (crash marker detected)
- Dependent expansion exceeds 50 files

```bash
java-codebase-rag increment --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

**Flags:**
- `--vectors-only` — runs only cocoindex catch-up; skips graph update and emits stale-graph warning. Use this when you want the old Lance-only behavior.

**Migration note:** After upgrading, run `reprocess` once to ensure edge tables have `source_file` columns (ontology version 17+).

### `reprocess`

**Default (no extra flags):** full **Lance** reprocess (cocoindex `--full-reprocess`) then full **LadybugDB** rebuild via `build_ast_graph.py`, in that order. This remains the recommended **coherence** operation when both stores might be out of date.

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

Deletes cocoindex state, the LadybugDB graph (`code_graph.lbug`), the graph builder's content-hash store (`.graph_hashes.json`), and Lance tables under the index dir. Requires **`--yes`** or interactive confirmation on a TTY. Non-TTY without `--yes` exits **2**.

```bash
java-codebase-rag erase --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --yes
```

### Hidden `refresh` alias

`java-codebase-rag refresh` runs **`reprocess`**. Prefer **`reprocess`** in scripts.

## Introspection subcommands

### `meta`

Graph metadata, ontology version, counts, `edge_counts`, plus resolved embedding fields and provenance (`embedding_model_source`, `embedding_device_source`, `index_dir`, `ladybug_path`, …).

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

Maps a **unified diff** to changed symbols, blast radius, routes touched, and risk band. Requires a **built LadybugDB graph** at `<index-dir>/code_graph.lbug`.

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

Prefer **`java-codebase-rag reprocess --graph-only`** when you only need LadybugDB rebuilt from the current Lance snapshot. To run the graph builder **without** going through the CLI (advanced / scripting):

```bash
.venv/bin/python build_ast_graph.py --source-root /path/to/java/repo --ladybug-path /path/to/.java-codebase-rag/code_graph.lbug --verbose
```

## See also

- [README.md](../README.md) — env vars, MCP tool table, ignore layout.
- [CODEBASE_REQUIREMENTS.md](./CODEBASE_REQUIREMENTS.md) — repo layout, brownfield, when to rebuild.
- [MANUAL-VERIFICATION-CHECKLIST.md](./MANUAL-VERIFICATION-CHECKLIST.md) — phased checks that mix CLI + MCP.

## `jrag` command — search CLI

The **`jrag`** command provides semantic search over the LanceDB index. This is the CLI surface for search (see MCP `search` tool in AGENT-GUIDE.md for the programmatic interface).

### `jrag search`

Semantic search via natural language queries. Returns one row per symbol/type by default; use `--chunks` to restore chunk-level output.

```bash
# Basic search (deduped by default)
jrag search "authentication service"

# Show all chunks (no dedup)
jrag search "authentication service" --chunks

# Hybrid search (vector + keyword)
jrag search "login" --hybrid

# With score breakdown
jrag search "controller" --explain

# With pagination
jrag search "service" --limit 20 --offset 20
```

**Key flags:**
- `--table {java,sql,yaml,all}` — Which content table to search (default: `java`).
- `--hybrid` — Enable vector + keyword hybrid search (single table only).
- `--explain` — Include score breakdown (distance, role weight, symbol bonus).
- `--chunks` — Show every chunk (default collapses to one row per symbol/type).
- `--limit N` — Max hits to return (default 10).
- `--offset N` — Skip N hits (pagination).
- `--min-score N` — Drop hits below this score floor (default 0.0).
- `--path-contains SUBSTR` — Narrow to chunks whose filename contains this substring.
- `--role ROLE` — Filter by role (e.g., `CONTROLLER`, `SERVICE`).
- `--framework FRAMEWORK` — Filter by framework (e.g., `spring_mvc`, `webflux`).

**Breaking change (PR-SEARCH-2):** By default, `jrag search` now returns one row per `primary_type_fqn` (symbol/type) to prevent a single type from flooding the page. The `--chunks` flag restores the previous chunk-level output. When deduped, each hit shows a `chunks=N` field indicating how many chunks were collapsed into that hit.
