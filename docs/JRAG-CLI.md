# `jrag` CLI — operator guide

The **`jrag`** command is the **operator surface** for this bundle: index lifecycle (`init` / `increment` / `reprocess` / `erase`), graph and Lance health (`meta`, `tables`), ignore diagnostics, and PR diff analysis. It is **not** the MCP navigation surface (that is `search` / `find` / `describe` / `neighbors` / `resolve` on the MCP server — this CLI is lifecycle and introspection only). For agents driving the MCP server, see [`AGENT-GUIDE.md`](./AGENT-GUIDE.md).

> **Renamed from `java-codebase-rag`.** The operator command is now `jrag`; `java-codebase-rag` remains as a working alias (with a TTY-gated deprecation nudge). On-disk artifacts (`.java-codebase-rag/`, `.java-codebase-rag.yml`, `.java-codebase-rag.hosts`) and `JAVA_CODEBASE_RAG_*` env vars are unchanged. See [`MIGRATION.md`](./MIGRATION.md).

## Install and discovery

After installing the package (e.g. editable install from the repo root), the console script is on your `PATH`:

```bash
.venv/bin/pip install -e ".[dev]"
jrag --help
```

If `jrag` is missing, run the module entrypoint:

```bash
.venv/bin/python -m java_codebase_rag.cli --help
```

## Setup commands

### `install`

Interactive setup wizard that walks users through Java source detection, embedding model selection, agent host configuration, agent-surface wiring (SessionStart hook or MCP entry), and YAML config generation. Use `--non-interactive` for CI/automation.

```bash
# Interactive mode
jrag install

# Non-interactive mode (requires at least one --agent)
jrag install --non-interactive --agent claude-code
jrag install --non-interactive --agent claude-code --agent qwen-code

# With custom embedding model
jrag install --model /path/to/model

# User-scope installation (available globally)
jrag install --scope user
```

**Flags:**
- `--non-interactive` — Run without prompts (requires `--agent`).
- `--agent {claude-code,qwen-code,gigacode}` — Agent host to configure (can be passed multiple times).
- `--scope {project,user}` — Installation scope (default: `project`). Project scope writes to `.<host>/` in the project repo; user scope writes to `~/.<host>/` (globally available).
- `--model MODEL` — Embedding model path or `auto` (default: `auto`, downloads `sentence-transformers/all-MiniLM-L6-v2` on first run).
- `--surface {mcp,cli}` — Agent surface (default: `cli`, recommended). `cli` installs a **`jrag prime` SessionStart hook** into each selected host's settings file — no files are deployed. `mcp` registers the `jrag-mcp` stdio MCP server (five tools: `search`/`find`/`describe`/`neighbors`/`resolve`) — tools only, no files. Omit to choose interactively. See [`jrag prime`](#jrag-prime-sessionstart-priming) below.
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
5. Surface selection — `cli` (recommended: prime hook) or `mcp` (stdio MCP server). Re-runs pre-fill the prior surface.
6. Surface entrypoint resolution + deployment — the SessionStart prime hook (`cli`) or the MCP server entry (`mcp`). Neither surface deploys files.
7. Index + finish — YAML generation, `.gitignore` update, `init`. Stage 7's indexing sub-step renders the unified `Vectors → Optimize → Graph` progress on **stderr** (see [Indexing progress](#indexing-progress-stderr)); the wizard's conversational stdout is unchanged.

**Re-running `install`:** If `.java-codebase-rag.yml` exists, the installer shows current values and offers "Update" (pre-filled) or "Start fresh". Existing MCP entries are updated in-place (merged, not duplicated). The SessionStart hook merges idempotently — keyed on its command, never duplicated, and never touching unrelated hooks.

#### Multi-system workspace

`jrag` accepts a **parent source-root** that contains several nested Java systems, not just a single repo. The typical layout is a grouping directory whose immediate children are independent systems, each a multi-module Maven/Gradle reactor:

```
WorkingProject/
├─ SystemA/
│  └─ microservice-1/        # pom.xml / build.gradle here
│     └─ src/main/java/...
└─ SystemB/
   └─ microservice-2/        # pom.xml / build.gradle here
      └─ src/main/java/...
```

Point the installer at the parent:

```bash
jrag install --source-root WorkingProject
# or, from inside the parent:
cd WorkingProject && jrag install
```

Detection recognises the multi-system layout, prints the systems it found (`Multi-system workspace — found Java under: SystemA/, SystemB/. Indexing all as one merged index.`), and builds **one merged index** at `WorkingProject/.java-codebase-rag/`. The generated `.java-codebase-rag.yml` **omits `microservice_roots`** — every system is indexed (the name-list key cannot express a nested subset), so `jrag` walks the whole tree. Cross-service edges (`HTTP_CALLS` / `ASYNC_CALLS`) resolve within the single graph, so cross-system questions ("what calls into `SystemA` from `SystemB`?") work without extra configuration.

`jrag init --source-root WorkingProject` does the same — builds one merged index directly, skipping the wizard.

> **Parent-POM caveat (`microservice` attribution).** If the `System*/` grouping directories themselves carry an aggregator `pom.xml` (or other build marker), `microservice` attribution **rolls up to the System name** (`SystemA`), while `module` keeps inner-module granularity (`microservice-1`). When the `System*/` dirs have *no* marker, `microservice` resolves to the inner microservice name (`microservice-1`) via the outermost-marker fallback. See [`CODEBASE_REQUIREMENTS.md`](./CODEBASE_REQUIREMENTS.md) §A.1 (Two location concepts: `module` and `microservice`).

### `update`

Post-upgrade refresh: brings the deployed surface's entry up to date — the SessionStart prime hook's command on the `cli` surface, the MCP server command path on the `mcp` surface — and removes any legacy 0.12.x skill/agent files still on disk. If an index exists, also runs an incremental Lance + graph catch-up (same as `increment`). Can also switch the agent surface (`mcp` ↔ `cli`) for an existing install. Requires a prior `install` run.

```bash
# Refresh after pip upgrade
pip install --upgrade jrag-cli
jrag update

# Preview changes without writing
jrag update --dry-run

# Force overwrite all artifacts
jrag update --force

# Switch surface (migrate an existing install)
jrag update --surface cli      # mcp → cli
jrag update --surface mcp      # cli → mcp
```

**Flags:**
- `--force` — Overwrite all artifacts even if content matches. (Effectively inert for the hook: it is content-addressed by the resolved `jrag` path, so a matching entry is already current.)
- `--dry-run` — Print changes without writing files.
- `--surface {mcp,cli}` — Switch agent surface. Tears down the old surface's entry (removes just the `jrag-mcp` MCP entry on `mcp`→`cli`; removes the SessionStart prime hook on `cli`→`mcp`), deploys the new surface's, and rewrites the install marker so the switch persists. Omit to keep the current surface; on a TTY you'll be prompted (cursor on the current surface).
- `--quiet` / `-q` — Suppress the indexing progress stream on stderr (wizard stdout unchanged).
- `--verbose` / `-v` — Raw-relay subprocess output during the indexing sub-step (no progress bar).

**Behavior:**
- Detects previously configured agent hosts (reads the `.java-codebase-rag.hosts` marker — one `surface` value (`mcp`|`cli`) per host; falls back to scanning project- and user-level MCP config files).
- Refreshes the deployed surface: on `cli`, re-points the prime hook at the current `jrag` path (no-op when already current); on `mcp`, updates the MCP entrypoint path if `jrag-mcp` has moved.
- Removes the four legacy 0.12.x artifact files wherever they still exist in the scope — `skills/explore-codebase/SKILL.md`, `skills/explore-codebase-cli/SKILL.md`, `agents/explorer-rag-enhanced.md`, `agents/explorer-rag-cli.md` — so upgrades from any 0.12.x clean up regardless of which surface was installed then.
- With `--surface` (or the interactive prompt), migrates each host whose recorded surface differs: tears down the old surface, deploys the new one, rewrites the marker. Non-interactive `update` without `--surface` keeps the current surface.
- Runs an incremental index update (Lance + graph) if an index exists — same as `jrag increment`. The indexing sub-step renders the unified `Vectors → Optimize → Graph` progress on **stderr** (see [Indexing progress](#indexing-progress-stderr)); it no longer runs silently.

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
jrag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq .ontology_version
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
jrag init --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

### `increment`

Runs cocoindex **catch-up** and **incremental LadybugDB graph update**. Only changed files and their single-hop dependents are re-parsed and re-written to the graph. Passes 5–6 (client/producer extraction and cross-service matching) run globally. Falls back to full `reprocess` if:
- No previous graph exists (first run)
- Graph schema is outdated (missing `source_file` on edges)
- Previous incremental run crashed (crash marker detected)
- Dependent expansion exceeds 50 files

```bash
jrag increment --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
```

**Flags:**
- `--vectors-only` — runs only cocoindex catch-up; skips graph update and emits stale-graph warning. Use this when you want the old Lance-only behavior.

**Migration note:** After upgrading, run `reprocess` once to ensure edge tables have `source_file` columns (ontology version 17+).

### `reprocess`

**Default (no extra flags):** full **Lance** reprocess (cocoindex `--full-reprocess`) then full **LadybugDB** rebuild via `build_ast_graph.py`, in that order. This remains the recommended **coherence** operation when both stores might be out of date.

**Selective flags (mutually exclusive):**

- `--vectors-only` — runs only the cocoindex full reprocess phase; does **not** invoke the graph builder.
- `--graph-only` — runs only `build_ast_graph.py`; does **not** invoke cocoindex.

**Reprocess for new schema fields:** Adding `generated` and `generated_by` columns to Lance chunks and graph Symbol nodes is a schema change (ontology version bumped 17→18). Existing indexes must be reprocessed via `jrag reprocess` to populate these fields; until then, old chunks report `generated=false`. After upgrading, run a full reprocess once to enable generated-source detection and filtering.

Passing **both** flags is rejected by argparse **before** any subprocess runs. The error is printed on **stderr** in this form (wording may vary slightly with Python/argparse version):

```text
jrag: argument --graph-only: not allowed with argument --vectors-only
```

Use `jrag reprocess --help` for the live synopsis.

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
jrag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
jrag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --vectors-only --quiet
jrag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --graph-only --quiet
```

### `erase`

Deletes cocoindex state, the LadybugDB graph (`code_graph.lbug`), the graph builder's content-hash store (`.graph_hashes.json`), and Lance tables under the index dir. Requires **`--yes`** or interactive confirmation on a TTY. Non-TTY without `--yes` exits **2**.

```bash
jrag erase --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --yes
```

### Hidden `refresh` alias

`jrag refresh` runs **`reprocess`**. Prefer **`reprocess`** in scripts.

## Introspection subcommands

### `meta`

Graph metadata, ontology version, counts, `edge_counts`, plus resolved embedding fields and provenance (`embedding_model_source`, `embedding_device_source`, `index_dir`, `ladybug_path`, …).

```bash
jrag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

### `tables`

Lance table listing and embedding summary (same helper as the server’s table introspection).

```bash
jrag tables --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

### `diagnose-ignore`

Explains **why a path** is ignored or not ignored by the layered ignore rules (builtin + project `.java-codebase-rag/ignore` + nested ignore files + gitignore layers).

```bash
jrag diagnose-ignore src/main/generated/Foo.java --source-root /path/to/java/repo
```

### `unresolved-calls`

Lists or aggregates **receiver-failure** call sites stored as `UnresolvedCallSite` (not on `CALLS` after ontology 15 PR-3). Reasons: `phantom_unresolved_receiver`, `chained_receiver`.

```bash
jrag unresolved-calls stats --by microservice --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
jrag unresolved-calls list --method-id sym:... --limit 100 --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

`stats --by` accepts `reason`, `microservice`, or `caller_role` (declaring type role of the caller method).

## Analysis: `analyze-pr`

Maps a **unified diff** to changed symbols, blast radius, routes touched, and risk band. Requires a **built LadybugDB graph** at `<index-dir>/code_graph.lbug`.

Provide exactly one of:

- `--diff-file PATH`
- `--diff-stdin` (read diff from stdin)

```bash
git diff > /tmp/pr.diff
jrag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag
```

Paths in the diff should align with **`Symbol.filename`** layout in the graph (project-relative Java paths). Use this from **PR-triage scripts** or Cursor skills; PR mapping is **CLI-only** (the MCP exposes retrieval tools only).

## Suggested workflows

### 1. Quick health after a build

```bash
jrag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq '{ontology_version, parse_errors, counts, edge_counts}'
jrag tables --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq '.tables | keys'
```

### 2. “Why isn’t this file in the index?”

```bash
jrag diagnose-ignore path/inside/repo/to/File.java --source-root /path/to/java/repo
```

### 3. Full re-index (operator / CI)

```bash
jrag reprocess --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag --quiet
jrag meta --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq .ontology_version
```

### 4. PR risk pass (local)

```bash
git diff origin/main...HEAD > /tmp/pr.diff
jrag analyze-pr --diff-file /tmp/pr.diff --source-root /path/to/java/repo --index-dir /path/to/.java-codebase-rag | jq '{risk_score,risk_band,blast_radius_total}'
```

## Graph-only escape hatch

Prefer **`jrag reprocess --graph-only`** when you only need LadybugDB rebuilt from the current Lance snapshot. To run the graph builder **without** going through the CLI (advanced / scripting):

```bash
.venv/bin/python build_ast_graph.py --source-root /path/to/java/repo --ladybug-path /path/to/.java-codebase-rag/code_graph.lbug --verbose
```

## See also

- [README.md](../README.md) — env vars, MCP tool table, ignore layout.
- [CODEBASE_REQUIREMENTS.md](./CODEBASE_REQUIREMENTS.md) — repo layout, brownfield, when to rebuild.
- [MANUAL-VERIFICATION-CHECKLIST.md](./MANUAL-VERIFICATION-CHECKLIST.md) — phased checks that mix CLI + MCP.

## `jrag` command — agent CLI

`jrag` is also the **agent-facing** CLI — the same console script as the operator surface above, exposing **one command per engineering intent** over the same LanceDB vectors + LadybugDB graph as the MCP surface. It takes human-readable identifiers (FQN / simple name / route path / topic) — never raw node IDs. Every `<query>` command resolves the identifier as its first step; on `many` candidates it returns them and stops, on `none` it returns `not_found` (auto-pick is forbidden).

Output defaults to compact text; `--format json` emits the shared envelope verbatim. Shared flags apply to most commands: `--service`, `--module`, `--limit`, `--format`, `--detail {brief,normal,full}`, `--index-dir`. `<query>` commands also take resolve hints (`--kind`, `--role`, `--fqn-contains`). Run `jrag <command> --help` for the per-command synopsis; for the full envelope contract and flag table, see [`jrag` — agent CLI](../README.md#jrag--agent-cli) in the README.

A missing or stale index produces an actionable `status: error` envelope (exit **2**) rather than a traceback — the one exception is `prime`, which stays silent and exits 0 (see below).

### Command reference

Commands are grouped by engineering intent. Every `<query>` argument is a human-readable identifier (FQN, `Symbol#method(args)`, route path, or topic), resolved before any graph walk.

#### Orientation

Index health and coarse-grained maps — no identifier needed.

```bash
jrag status                    # index freshness, ontology version, counts
jrag microservices             # microservices with resolved type counts
jrag map                       # symbol counts per kind, grouped by microservice
jrag map --by module           # ...grouped by module instead
jrag conventions               # dominant roles + framework tallies
jrag overview chat-core        # bundle for a microservice
jrag overview /chat/assign     # route flow (inbound callers + outbound CALLS)
jrag overview banking.chat     # topic: producers + consumers
jrag overview chat-core --as microservice  # override auto-detection
jrag prime                     # session-start orientation payload (see below)
```

`overview` dispatches on its subject: a `/`-prefixed string is a route (same as `flow`), a known microservice name yields its routes + clients + producers, otherwise it is treated as a topic. `--as {microservice,route,topic}` overrides the auto-detection.

#### Locate

Find a specific node by name, or inspect one in full. `<query>` commands resolve first.

```bash
jrag find ChatService                 # exact name / FQN lookup (symbols only)
jrag find --role CONTROLLER           # filter mode (structured NodeFilter flags)
jrag find --framework spring_mvc --capability HTTP_CLIENT
jrag inspect ChatService              # full node record + edge summary
jrag outline src/main/.../Foo.java    # symbols declared in a file
jrag imports src/main/.../Foo.java    # imports resolved to graph nodes
```

`find` has two modes: a positional `<query>` for exact name/FQN lookup (symbols only), or **filter mode** (no positional) using structured flags (`--role`, `--java-kind`, `--annotation`, `--capability`, `--framework`, `--http-method`, `--client-kind`, `--producer-kind`, `--topic-contains`, …). Domain flags imply `--kind` when omitted; `--offset` paginates in filter mode only.

#### Listings

Surface every node of a given Java role. No `<query>`; pair with `--service` / `--module` to scope.

```bash
jrag http-routes        # HTTP routes
jrag http-clients       # HTTP clients (Feign / RestTemplate / WebClient)
jrag producers          # async message producers (Kafka / StreamBridge)
jrag topics             # message topics, grouped by producer
jrag jobs               # scheduled tasks (@Scheduled)
jrag listeners          # message listeners (@KafkaListener etc.)
jrag entities           # JPA entities
```

#### Traversals

One-hop and multi-hop walks. **All resolve-first** — pass a human-readable identifier.

```bash
jrag callers ChatService#assign(Request)      # who calls me?
jrag callers ChatIngressController            # a controller also lists its EXPOSES routes
jrag callees ChatService#assign(Request)      # what do I call?
jrag dependencies ChatService                 # types this Symbol injects (INJECTS out)
jrag dependents PaymentGateway                # who injects this type?
jrag hierarchy AbstractBase                   # type tree (parents + children)
jrag implementations PaymentProcessor         # classes implementing an interface
jrag subclasses AbstractRepository            # classes extending a type
jrag overrides Impl#run()                     # methods this overrides (dispatch UP)
jrag overridden-by Iface#run()                # methods overriding this (dispatch DOWN)
jrag impact PaymentGateway                    # fleet-wide blast radius (reverse closure)
jrag decompose ChatIngressController#assign   # role-waterfall flow from an entrypoint
jrag flow /chat/assign                        # request flow through a route
jrag connection chat-core                     # cross-service connections (inbound/outbound)
```

#### Semantic search

```bash
jrag search "assign a chat agent"   # semantic over Lance (java table)
jrag search "kafka" --table all     # java + sql + yaml tables
```

See [`jrag search`](#jrag-search) below for the full flag reference (hybrid, explain, dedup, pagination, role/framework filters, generated-source filtering).

#### Maintenance

```bash
jrag vocab-index            # rebuild the vocabulary sidecar (did-you-mean / absence diagnosis)
```

### `jrag prime` (SessionStart priming)

`prime` prints the orientation payload that the `cli` surface injects at session start: a navigation-framed summary of what `jrag` is, one trust rule, the live index state, and the agent command surface embedded verbatim from `jrag --help`. It is the payload behind the SessionStart hook that `jrag install --surface cli` wires; running it by hand shows exactly what a hooked session sees.

```bash
jrag prime                    # bare markdown (human inspection)
jrag prime --hook-json        # SessionStart hook envelope (what the hook emits)
```

**Payload template** — four parts; `{…}` slots are computed per repo (canonical text: `PRIME_TEMPLATE` in `java_codebase_rag/prime.py`):

```markdown
`jrag` is a CLI over a prebuilt structural index of this Java/Kotlin repo — a map, not an oracle.
It resolves names to files, walks who-calls-whom and dependency edges, and surfaces entry points
and service boundaries — structure you'd otherwise grep for. You are the explorer; jrag is the map.

**Trust rule:** if jrag and the files disagree, trust the files — the index may lag the working tree.

**Index state**

- Index {freshness} (incremented {age} ago) · watch daemon {running|not running}
- {n} services ({top names, truncated}) · {n} symbols
- {n} routes · {n} clients · {n} producers

**Commands by group** (from `jrag --help`)   … agent commands grouped health / locate /
                                                listings / traversal / orientation / search
**Command reference**                          … one line per agent command, verbatim from
                                                `jrag --help`

Run `jrag <command> --help` for flags.
```

A stale index spells out the change count (`stale — 56 files changed since last increment`); service names list the seven largest services then truncate. **Operator commands are excluded**: the embedded surface lists agent verbs only — the operator lifecycle (`init`, `install`, `update`, `reprocess`, `erase`, `meta`, `tables`, `diagnose-ignore`) never appears in the payload.

**Three states, one exit code.** `prime` is hook-safe by construction — every soft state degrades to empty output, and the exit code is **0 in all of them**:

| State | stdout | stderr | exit |
| ----- | ------ | ------ | ---- |
| Index present (fresh or stale) | full payload | — | `0` |
| No project YAML / no index discovered | nothing | nothing | `0` |
| Internal error (unreadable index meta, corrupt YAML) | nothing | one line: `jrag prime: <ErrorType>: <message>` | `0` |

Silence when unindexed is what makes a user-scope hook tolerable — prime fires in every session of every repo and must never nag repos it does not index.

**`--hook-json`** wraps the same markdown in the Claude Code SessionStart envelope (payload in `additionalContext`; qwen-code consumes the same shape):

```json
{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<markdown payload>"}}
```

**Latency.** SessionStart fires on start, resume, and after compaction, so prime reads metadata only — project-root discovery, graph meta, index mtimes, and the watch daemon state file. It never imports the vector stack (torch / sentence_transformers / lancedb); a test guards the import set.

**Where the hook lives.** `install --surface cli` merges it into each selected host's settings file: claude-code → `.claude/settings.json` (project scope) or `~/.claude/settings.json` (user scope); qwen-code → `.qwen/settings.json`; gigacode → `.gigacode/settings.json`. The merge is idempotent, keyed on the hook command, and never touches unrelated hooks.

**Manual wiring (other hosts).** For a host outside the three above that supports SessionStart-style hooks, add this to its settings JSON (`jrag` must be on the host's PATH — the installer writes the resolved absolute path):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [{"type": "command", "command": "jrag prime --hook-json"}]
      }
    ]
  }
}
```

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
- `--exclude-generated` — Exclude generated sources from results (auto-detected by `@Generated` annotations and generator header banners).
- `--generated-only` — Show only generated sources.

**Breaking change (PR-SEARCH-2):** By default, `jrag search` now returns one row per `primary_type_fqn` (symbol/type) to prevent a single type from flooding the page. The `--chunks` flag restores the previous chunk-level output. When deduped, each hit shows a `chunks=N` field indicating how many chunks were collapsed into that hit.

### `jrag watch`

A single long-running daemon that does two things at once **while it runs**: (a) **keeps the index fresh** — it watches the source tree and re-runs a debounced per-type reindex (vectors via cocoindex, graph via `build_ast_graph.py --incremental`) on file change; and (b) **serves every read command warm** — `search` / `find` / `inspect` / `callers` / `callees` / `flow` are served over a Unix socket from a pre-loaded model + graph (graph-only on Intel Mac, where there is no model), so each query skips the per-call model/graph load and is effectively instant. The "run it while you code" workflow: start it once per coding session (foreground or detached), then keep issuing the normal `jrag` read commands — they are accelerated automatically.

```bash
# Foreground — Ctrl+C (or SIGTERM) stops it
jrag watch

# Background — returns once the daemon is ready to serve
jrag watch --detach

# Is it running? (exit 0 if up, 1 if down)
jrag watch --status

# Graceful stop (SIGTERM; SIGKILL after 5 s); cleans the socket
jrag watch --stop
```

**Modes / flags:**
- *(default, foreground)* — runs in the foreground; **Ctrl+C** or **SIGTERM** stops it. On a TTY it renders a status panel showing the socket path, reindex count, and last reindex.
- `--detach` — start as a background daemon and return once it is ready to serve (logs to a file under the index dir).
- `--stop` — gracefully stop a running watcher (pidfile + signal; SIGKILL after 5 s) and clean up its socket.
- `--status` — print `up`/`down` with pid, socket path, and last reindex. Exits **0** if up, **1** if down. Does **not** acquire the project lock.
- `--debounce-ms N` — reindex debounce window in ms (overrides YAML `watch:debounce_ms`).
- `--backend {auto,watchdog,polling}` — file-watch backend (overrides YAML `watch:backend`).

**Cold-fallback guarantee.** With **no daemon running**, every read command behaves byte-identically to today — the daemon is a pure accelerator + freshness layer, never a dependency. If the daemon is down or unreachable, each `jrag` read silently takes the cold path (identical output, identical exit codes; you only pay the one-off cold-start model/graph load). See [`CONFIGURATION.md`](./CONFIGURATION.md) § 2 for the `watch:` block.

**Unix-only.** `jrag watch` relies on `fcntl`, so it runs on **macOS / Linux** only. On Windows it prints `jrag watch: watch mode requires macOS/Linux` to stderr and exits **2**; the cold read path is unaffected on every platform. One daemon per index dir — a pidfile + `flock` prevents two watchers (or a concurrent manual `increment`) on the same project.

**Graph-only (macOS Intel).** On Intel Mac the vector stack is absent (PEP 508 excludes `sentence_transformers`/`lancedb`/`cocoindex`), so the daemon runs in **lexical/graph-only mode**: it skips the embedding-model warm-up and the cocoindex vectors reindex, serves warm **lexical** `search` (BM25 over the symbol graph — same as the cold `search` path on Intel Mac) plus every structural command (`find`/`inspect`/`callers`/`callees`/`flow`), and reindexes only the graph on file change. `jrag watch --status` and the TTY panel report `mode: lexical (graph-only)`. Every `search` result carries the usual `lexical_mode=true` flag + advisory.
