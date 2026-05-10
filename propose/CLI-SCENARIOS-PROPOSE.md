# CLI-SCENARIOS — restructure `java-codebase-rag` CLI, config, and naming

**Status**: draft
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-10

## TL;DR

- Today's `java-codebase-rag refresh` does one thing — full Lance reprocess + full Kuzu rebuild — but its name implies "freshen up", and operators have no obvious entry point for "I'm a new user", "wipe everything", or "incremental update on a developer machine". Plus, three layers of legacy naming (`lancedb-mcp` config file, `lancedb_data` directory, `LANCEDB_MCP_*` env vars, `user_rag` Python package) leak through everywhere.
- Replace `refresh` with **four lifecycle subcommands** matching real operator scenarios: `init`, `increment`, `reprocess`, `erase`.
- `increment` is a **partial-fidelity** subcommand: Lance side runs `cocoindex update` in catch-up mode (real incremental); Kuzu side prints a clear warning that AST graph incremental rebuild is not yet implemented and links to a tracking GitHub issue. Caller chooses to continue with stale graph or abort.
- Top-level `--help` and per-subcommand `--help` are rewritten with descriptions, examples, and a one-paragraph framing of what the tool is.
- **Consolidate environment variables from 9 to 5.** Drop `LANCEDB_MCP_ALLOW_REFRESH`, `LANCEDB_MCP_GRAPH_ENABLED`, `LANCEDB_MCP_MICROSERVICE_ROOTS`, `LANCEDB_MCP_PROJECT_ROOT`, `KUZU_DB_PATH`, `COCOINDEX_DB`. Merge `LANCEDB_URI` + `KUZU_DB_PATH` into a single `JAVA_CODEBASE_RAG_INDEX_DIR`. Rename `LANCEDB_MCP_DEBUG_CONTEXT` and the test-only `LANCEDB_MCP_RUN_HEAVY` for prefix consistency. Keep `SBERT_MODEL` / `SBERT_DEVICE` but also make them YAML-configurable.
- **Rename project-scope config**: `.lancedb-mcp.yml` → `.java-codebase-rag.yml`. Promote `microservice_roots`, `embedding.model`, `embedding.device` (and any other knob that's per-project) into it. Precedence: CLI flag > env var > YAML > built-in default.
- **Rename `lancedb_data` default index directory** to `.java-codebase-rag/` (a hidden dotted directory under the project root, matching the dotted-config-file convention). All hardcoded `./lancedb_data` defaults updated.
- **Rename Python package** `user_rag/` → `java_codebase_rag/`. Update `pyproject.toml [project.scripts]`, all imports, the test file `tests/test_user_rag_cli.py`, and the `python -m user_rag.cli` example in the operator playbook. Also update `pyproject.toml [project].name` from `java-enterprise-codebase-rag` to `java-codebase-rag` (matching the new GitHub repo name).
- One-release deprecation: `refresh` stays as a hidden alias for `reprocess` with a stderr warning, then drops. **No deprecation window for env vars / config / package rename** — breaking changes allowed (no users yet).
- Migration shape: **3 PRs** — propose merge → CLI + config consolidation (this design) → docs update across the full tree. Engine work for true Kuzu incremental is out of scope and tracked under `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`.

---

## §1 — Frame: what is this CLI, really?

The `java-codebase-rag` CLI is the **operator's lifecycle interface to the index**, not a swiss army knife. Its job is to answer one question across four stages of an index's life: *"what state is the index in, and how do I move it to the state I need?"* Every other concern — searching, navigating, analyzing — belongs to the MCP surface or to one-off introspection commands (`meta`, `tables`, `diagnose-ignore`, `analyze-pr`).

This frame rules things out:

- It rules out exposing internal pipeline stages as subcommands (no `build-graph-only`, no `cocoindex-update-only`). Those exist as advanced-user escape hatches via direct script invocation, not first-class CLI verbs.
- It rules out hiding the partial nature of incremental rebuilds. If an operator types `increment`, they get a real Lance increment plus a loud warning about Kuzu, not a silent half-rebuild.
- It rules out collapsing scenarios for "elegance". `init` and `reprocess` look mechanically identical today (both = full rebuild), but they encode different operator intent and different safety policies. A future first-time-init optimization (skipping checks the system has already done) lands on `init` without re-litigating the surface.

## §2 — Design principles

1. **Subcommand names are operator scenarios, not pipeline stages.** `init` / `increment` / `reprocess` / `erase` describe what the operator wants to happen; they do not describe how cocoindex or the graph builder are wired internally.
2. **Partial fidelity is loud.** When a subcommand cannot deliver its full promise (today: `increment` can't increment Kuzu), the CLI says so on stderr with a tracking issue link. Silent stale state is a worse failure than honest partial state.
3. **One scenario, one safe default.** `init` refuses to clobber an existing index. `erase` requires `--yes` (or an interactive confirm). `reprocess` is the explicit "I know what I want, full rebuild" verb.
4. **Help text is the contract.** `--help` at every level (root, subcommand) describes purpose, when to use it, and a copy-paste example. A new operator must be able to choose the right subcommand from `--help` alone.
5. **`refresh` deprecation gets a one-release window; nothing else does.** `refresh` keeps working with a stderr warning that points at `reprocess` for the next release, then is removed. Every other rename (env vars, config file, index directory, Python package) is a clean break — breaking changes allowed (no users yet).
6. **Cardinal-numbered subcommand surface.** Lifecycle = 4 subcommands (`init`, `increment`, `reprocess`, `erase`). Introspection = 3 subcommands (`meta`, `tables`, `diagnose-ignore`). Analysis = 1 subcommand (`analyze-pr`). Total = 8. Adding a 5th lifecycle verb requires a propose, not a drive-by PR.
7. **One source of truth per config knob.** A given setting (e.g. embedding model, microservice roots) lives in one place. Today's split (env-only / YAML-only / both-with-precedence) is harmonized into a single ordered precedence chain: **CLI flag > env var > YAML > built-in default**.
8. **No legacy naming in operator-facing surface.** Operators read env vars, the config file, the default index path, and the Python `python -m` invocation. Every one of those is renamed to `java-codebase-rag` / `.java-codebase-rag` / `java_codebase_rag`. Storage technology names (`lancedb`, `kuzu`, `sbert`) stay only inside internal modules / package boundaries.

## §3 — The proposed surface

### 3.1 Subcommand groups

The CLI organizes 8 subcommands into 3 groups, surfaced in `--help` output as labelled sections:

```
java-codebase-rag — graph-native code intelligence for Java microservices.

Lifecycle (manage the index):
  init           Create a fresh index from a Java repository.
  increment      Pick up changes since the last index update.
  reprocess      Rebuild the entire index from scratch.
  erase          Delete the index from disk.

Introspection (inspect the index):
  meta           Print ontology version, edge counts, and table summary.
  tables         List Lance tables and row counts.
  diagnose-ignore PATH
                 Show which ignore-pattern layer decided the fate of a path.

Analysis (work with code changes):
  analyze-pr     Compute blast-radius + risk score for a unified diff.

Run `java-codebase-rag <command> --help` for command-specific options.
```

### 3.2 Lifecycle subcommands

#### `init`

**Purpose**: first-time setup — operator just cloned a Java repo and wants an index.

**Behaviour**:
1. Resolve `--source-root` (or `--project-root` flag, or cwd).
2. Resolve `--index-dir` (or `JAVA_CODEBASE_RAG_INDEX_DIR`, or YAML `index_dir:`, or default `./.java-codebase-rag/`). Kuzu path is derived as `<index_dir>/code_graph.kuzu`; Lance tables live directly under `<index_dir>/`.
3. **Refuse if `<index_dir>` already contains an existing index** (`code_graph.kuzu` dir present or `code_index_*` Lance tables present). Print which paths are non-empty, suggest `reprocess` for rebuild or `erase` then `init` for clean slate. Exit code 2.
4. If clean: run `cocoindex update <flow>` (catch-up mode, populates from empty) + `build_ast_graph.py` (full build).

**Exit codes**: 0 success, 1 partial (cocoindex ok but graph build failed, or vice versa), 2 pre-flight refused (existing index, missing source root, etc.).

#### `increment`

**Purpose**: developer machine workflow — "I edited a few Java files, update the Lance side without a 30-minute full rebuild".

**Behaviour**:
1. Resolve paths the same way as `init`.
2. Run `cocoindex update <flow>` **without** `--full-reprocess` (cocoindex's native catch-up mode — re-processes only changed files).
3. Print **prominent stderr warning**: *"AST graph (Kuzu) incremental rebuild is not yet implemented. The graph reflects the index state from the last `init` or `reprocess`. Track progress at <issue-url>. Run `reprocess` for a fresh graph."*
4. Do **not** invoke `build_ast_graph.py`.
5. Exit code 0 if Lance update succeeded, even though graph is stale (the warning is the contract).

**Why ship partial:** The Lance side is what powers vector `search`. The Kuzu graph powers `find` / `neighbors` / `describe` / role enrichment. For an operator iterating on prose / chunk-level edits, Lance increment alone is genuinely useful. For an operator iterating on Java structure (new methods, new edges), the warning sends them to `reprocess`.

#### `reprocess`

**Purpose**: full rebuild from scratch — the current `refresh` behaviour.

**Behaviour**: identical to today's `refresh`:
1. `cocoindex update <flow> --full-reprocess -f`
2. Drop + rebuild Kuzu via `build_ast_graph.py`

Same env-var, same exit codes (0/1/2 with current semantics).

#### `erase`

**Purpose**: wipe the index from disk.

**Behaviour**:
1. Resolve `--index-dir` the same way as `init`.
2. Print what will be deleted (paths + sizes for Lance tables and Kuzu DB under `<index_dir>`).
3. Refuse unless `--yes` is passed *or* stdin is a TTY and operator confirms interactively.
4. `cocoindex drop <flow> -f` (clears cocoindex's internal state DB) + `rm -rf` Kuzu directory + remove Lance tables under `<index_dir>`.

### 3.3 Help redesign

Today: `java-codebase-rag --help` produces argparse default output — flat list of subcommand names with no descriptions.

Proposed: every `add_parser()` call gets `help=` (one-liner shown in subcommand list) and `description=` (paragraph shown on `<cmd> --help`). Examples are inlined into each subcommand's `description` via a custom argparse formatter so they render verbatim.

Concrete shape (to be filled in during implementation, not the propose):

- Root parser: prog name, one-paragraph description ("graph-native code intelligence layer…"), link to `docs/JAVA-CODEBASE-RAG-CLI.md` and `docs/paper/paper.pdf`, grouped subcommand list.
- Each subcommand: one-line summary (for the parent listing), full description with "When to use", "What it does", "Example", "Exit codes".

### 3.4 `refresh` deprecation

`refresh` stays as a hidden alias of `reprocess` for one release:
- Not listed in `--help` output (use `aliases=` argument or a separate hidden parser).
- On invocation, prints to stderr: `WARN: 'refresh' is deprecated; use 'reprocess'. This alias will be removed in the next release.`
- Behaviour identical to `reprocess`.
- Removed entirely in the release after.

### 3.5 Environment variable consolidation

Today's tool reads **9 environment variables** (excluding `COCOINDEX_DB`'s internal cousins). Many are redundant, leak storage tech, or duplicate things that already belong in YAML. Final shape: **5 env vars**.

**Before → after table:**

| Today | Fate | Replacement / rationale |
|---|---|---|
| `LANCEDB_URI` | renamed + merged | → `JAVA_CODEBASE_RAG_INDEX_DIR`. Single var for both Lance and Kuzu (Kuzu always lives at `<index_dir>/code_graph.kuzu`). |
| `KUZU_DB_PATH` | dropped | Folded into `JAVA_CODEBASE_RAG_INDEX_DIR`. Kuzu-on-different-storage is a hypothetical case; we add the override back if a real user needs it. |
| `LANCEDB_MCP_PROJECT_ROOT` | dropped | Use `--source-root` flag or cwd. One less env var, same effective control. |
| `LANCEDB_MCP_ALLOW_REFRESH` | dropped | The gate doesn't pay for itself — operators invoking destructive subcommands already do so intentionally. New per-subcommand safety (`init` refuses on existing index; `erase` requires `--yes` or TTY) is stricter and more legible. |
| `LANCEDB_MCP_GRAPH_ENABLED` | dropped | Today's auto-detect (graph on iff Kuzu DB exists) is correct in every observed case. The override is a leaking implementation detail. |
| `LANCEDB_MCP_MICROSERVICE_ROOTS` | dropped | Moved to YAML-only (`microservice_roots:` key). Per-project setting belongs in per-project config. |
| `LANCEDB_MCP_DEBUG_CONTEXT` | renamed | → `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`. Prefix alignment. |
| `LANCEDB_MCP_RUN_HEAVY` (test-only) | renamed | → `JAVA_CODEBASE_RAG_RUN_HEAVY`. Internal use; renaming is mechanical. |
| `COCOINDEX_DB` | dropped | Default path moves under `<index_dir>/cocoindex.db`. Edge-case override returns later if requested. |
| `SBERT_MODEL` | unchanged | Also configurable via YAML (`embedding.model:`). Precedence: CLI > env > YAML > default. |
| `SBERT_DEVICE` | unchanged | Also configurable via YAML (`embedding.device:`). Same precedence. |

**Resulting surface (5 env vars):**

| Variable | Purpose | Where else it can be set |
|---|---|---|
| `JAVA_CODEBASE_RAG_INDEX_DIR` | Where Lance + Kuzu live on disk. Default: `./.java-codebase-rag` under the project root. | `--index-dir` flag. |
| `SBERT_MODEL` | Embedding model id or local path. | `embedding.model:` in YAML; `--embedding-model` flag. |
| `SBERT_DEVICE` | Embedding device (`cpu` / `cuda` / `mps`). | `embedding.device:` in YAML; `--embedding-device` flag. |
| `JAVA_CODEBASE_RAG_DEBUG_CONTEXT` | Verbose stderr logging for context-expansion. Diagnostic only. | n/a (debug knob; env-only is fine). |
| `JAVA_CODEBASE_RAG_RUN_HEAVY` | Test-only gate for end-to-end cocoindex+Lance tests. | n/a (CI/test infra only). |

### 3.6 YAML config consolidation

The project-scope config file is renamed and its schema extended.

**File rename:** `.lancedb-mcp.yml` / `.lancedb-mcp.yaml` → `.java-codebase-rag.yml` / `.java-codebase-rag.yaml`. Loader looks for the new names only. Old files are **not** read — breaking change, called out in PR-CLI-2's release notes.

**New schema (additive):**

```yaml
# .java-codebase-rag.yml
embedding:
  model: sentence-transformers/all-MiniLM-L6-v2   # new — was: SBERT_MODEL env
  device: cpu                                      # new — was: SBERT_DEVICE env

microservice_roots: []                             # was: env-or-YAML → now YAML-only
cross_service_resolution: auto                     # unchanged
role_overrides: {...}                              # unchanged
route_overrides: {...}                             # unchanged
http_client_overrides: {...}                       # unchanged
async_producer_overrides: {...}                    # unchanged
```

**Precedence chain (locked):**

1. CLI flag (e.g. `--embedding-model`) — highest.
2. Environment variable (e.g. `SBERT_MODEL`).
3. YAML config (e.g. `embedding.model:`).
4. Built-in default — lowest.

This chain applies to every knob that exists in multiple places. Config knobs that exist in only one place (e.g. `role_overrides`, YAML-only; `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`, env-only) are unaffected.

### 3.7 Naming and package consolidation

Three more renames that the env-var work makes the natural moment for:

| Surface | Before | After |
|---|---|---|
| Default index directory | `./lancedb_data` | `./.java-codebase-rag` (under project root) |
| YAML config file | `.lancedb-mcp.yml` | `.java-codebase-rag.yml` |
| Python package directory | `user_rag/` | `java_codebase_rag/` |
| `pyproject.toml [project].name` | `java-enterprise-codebase-rag` | `java-codebase-rag` |
| Operator-facing module invocation | `python -m user_rag.cli` | `python -m java_codebase_rag.cli` |
| GitHub repo (already done by user) | `java-enterprise-codebase-rag` | `java-codebase-rag` |

**Test file rename:** `tests/test_user_rag_cli.py` → `tests/test_java_codebase_rag_cli.py`. Internal helper `_install_user_rag_entrypoint` renamed alongside.

**Import statement sweep:** every `from user_rag.cli import …` and `import user_rag` becomes `from java_codebase_rag.cli import …` / `import java_codebase_rag`. The implementation PR does this as a single `git mv` plus a mechanical grep-replace; not a per-file judgement call.

**Internal module names stay as-is.** `kuzu_queries.py`, `search_lancedb.py`, `mcp_v2.py`, `index_common.py`, etc. retain their storage-technology-flavoured names. They're internal; renaming them is churn without operator-facing payoff. The operator only sees the Python package boundary (`java_codebase_rag.cli`) and the CLI surface — both renamed.

### 3.8 GitHub issue for Kuzu incremental tracking

Open a GitHub issue titled **"AST graph (Kuzu) incremental rebuild"** referencing `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`. Issue URL is hard-coded into the `increment` warning message. The issue is the user-facing handle the warning points at; the propose is the technical scope.

---

## §4 — Use-case re-walk

| # | Use case | Subcommand(s) | Notes |
|---|---|---|---|
| UC1 | Operator just cloned the repo, wants to start indexing | `init` | Refuses if old index present → guides to `reprocess` or `erase`+`init`. |
| UC2 | Operator already has an index, wants a full rebuild after big refactor | `reprocess` | Same as today's `refresh`. |
| UC3 | Developer changed 3 Java files, wants Lance up-to-date | `increment` | Lance updates; warning about Kuzu shown but operator is searching prose, not navigating graph. |
| UC4 | Developer changed 3 Java files, wants graph navigation accurate | `increment` then `reprocess` | Warning tells them they need `reprocess`; clear escalation path. |
| UC5 | Operator wants to free disk space, project is shelved | `erase --yes` | Wipes both Lance and Kuzu. |
| UC6 | CI script previously called `refresh` | `refresh` (with deprecation warning) → migrate to `reprocess` | One-release window. |
| UC7 | Operator runs `java-codebase-rag` (no args) | help screen | Today: prints argparse usage. Proposed: prints framing + grouped subcommand list. |
| UC8 | Operator runs `java-codebase-rag --help` | same as UC7 | Same output. |
| UC9 | Operator runs `java-codebase-rag init --help` | command-specific help | Description + when to use + example + exit codes. |
| UC10 | Operator runs `init` on a path that already has Lance but no Kuzu | refused | Output enumerates which paths are non-empty so operator knows what to clean up. |
| UC11 | Operator runs `init` after `erase` | works | `erase` cleared both stores → `init` finds empty paths and proceeds. |
| UC12 | Operator runs `increment` immediately after `init` | succeeds with no-op | Cocoindex catch-up finds no changes; Kuzu warning still printed (consistent contract). |
| UC13 | Operator runs `init`/`reprocess`/`erase` without intention safeguards (e.g. piping `yes` into `erase`) | `erase` still requires `--yes` flag explicitly (piping `yes` does not satisfy it); other lifecycle subcommands proceed | `--yes` is a flag, not a stdin gesture. No global env-var gate replaces this; per-subcommand safety is what protects against accidental destruction. |
| UC14 | Operator runs `erase` without `--yes` in a TTY | interactive confirm | Y/N prompt with paths + sizes. |
| UC15 | Operator runs `erase` without `--yes` in a non-TTY (CI) | refused with exit 2 | Forces explicit `--yes` for unattended scripts. |
| UC16 | Operator runs `meta` after `increment` | succeeds | Read-only path; not affected by the lifecycle redesign. |
| UC17 | Operator wants to know "is my Kuzu graph up-to-date with my Lance index?" | not supported in this propose | Out of scope; tracked under same Tier-2 issue (graph staleness detection). |
| UC18 | Cocoindex flow file moved to a non-default path | `--source-root` | Same flag semantics as today. |
| UC19 | Operator runs `reprocess` on an empty index | works (= same as `init`, but doesn't refuse) | Intentional: `reprocess` is "I know what I want, just do it". |
| UC20 | Operator types `java-codebase-rag erase init` (typo, two verbs) | argparse error | Standard argparse behaviour; second positional is unknown. |
| UC21 | Operator already had a `lancedb_data/` directory from before the rename | `init` refuses (since the new index dir would point at `./.java-codebase-rag/` which is empty, but operator still has the old data they don't want to lose) | Mitigation: PR-CLI-2 release notes spell out `mv lancedb_data .java-codebase-rag`. No automatic migration. |
| UC22 | Operator has an old `.lancedb-mcp.yml` config from a previous run | Config is ignored; CLI prints a one-line stderr hint at startup: `"found legacy .lancedb-mcp.yml; rename to .java-codebase-rag.yml to re-enable"` | **Locked policy:** loader does **not** read the old filename. The startup hint is courtesy (no functional impact). Operator does `mv .lancedb-mcp.yml .java-codebase-rag.yml`. |
| UC23 | Operator sets both `SBERT_MODEL` env var and `embedding.model:` in YAML | env wins (precedence chain) | Documented in `--help` for the relevant CLI flags. |
| UC24 | Operator sets `--embedding-model` flag, `SBERT_MODEL` env, and `embedding.model:` YAML all at once | Flag wins | Same precedence chain; flag overrides everything. |
| UC25 | Operator runs `init` without setting `JAVA_CODEBASE_RAG_INDEX_DIR` | Uses `./.java-codebase-rag` under project root | Hidden directory; appears in `ls -a`. |
| UC26 | Operator's old script references `LANCEDB_URI` | Variable is no longer read — silent | **Locked policy:** no fall-through to legacy var names. Release notes call it out as a breaking change. Considered alternative (read legacy and warn) rejected per principle 5. |
| UC27 | Operator's old script references `LANCEDB_MCP_ALLOW_REFRESH=1` | Variable is no longer read — silent; subcommand runs whether or not it's set | Was a gate; now the gate is gone, so no functional impact. |
| UC28 | Test infrastructure runs `pytest -m lance_e2e` | Reads new `JAVA_CODEBASE_RAG_RUN_HEAVY` env var | `tests/conftest.py` and `tests/test_lancedb_e2e.py` updated. CI scripts must be updated. |
| UC29 | Library consumer imports `from user_rag.cli import main` | ImportError | Mechanical rename; consumer updates to `from java_codebase_rag.cli import main`. |
| UC30 | Operator runs `python -m user_rag.cli --help` from old habit | ImportError | Migration path documented in `JAVA-CODEBASE-RAG-CLI.md`. |

**Awkward cases surfaced:**
- **UC4** (graph staleness escalation) is handled by the warning text but is the most user-visible weak spot. Mitigation: the warning is not a one-line stderr afterthought — it's a multi-line block with the exact `reprocess` command to run.
- **UC17** (graph staleness query) would benefit from a future `meta --graph-freshness` flag that compares Kuzu's node-count snapshot against Lance's chunk count + flow run-id. Out of scope here; noted in Tier-2 issue.
- **UC21 / UC22 / UC26** (silent migration on rename) is the main risk of the no-deprecation-window policy. Mitigation: the README, `JAVA-CODEBASE-RAG-CLI.md`, and the PR-CLI-2 description **all** include a Migration section with the exact `mv` commands an operator needs to run. Plus the `init` refusal at UC21 is the natural alerting moment — a confused operator runs `init`, sees "path empty", asks why their old data isn't visible, and the README's Migration section is the first hit on grepping `lancedb_data`.

---

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Kuzu incremental rebuild | Engine work; tracked under `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md` and the new tracking issue. Out of scope of a CLI restructure. |
| Auto-detect "should this be `init` or `reprocess`?" | Violates principle 3 (one scenario, one safe default). Operator intent matters; refusing on `init` and being explicit on `reprocess` is the safety policy. |
| Pipeline-stage subcommands (`build-graph`, `update-lance`) | Violates principle 1. Direct script invocation (`python build_ast_graph.py`) is the escape hatch for advanced users; documented in `docs/JAVA-CODEBASE-RAG-CLI.md` but not a first-class CLI verb. |
| Graph-freshness query (`meta --graph-freshness`) | Useful but distinct surface; should ride with the Tier-2 incremental work where graph version tracking gets implemented. |
| `init --force` to override the "existing index" refusal | `reprocess` already does that; adding `--force` to `init` blurs the scenario boundary. |
| Restructuring introspection (`meta`, `tables`, `diagnose-ignore`) or `analyze-pr` | Out of scope — those subcommands work fine today and are not what the operator confusion is about. Lifecycle group only. |
| Adding net-new env vars or YAML keys | This propose only consolidates/renames. New knobs require their own propose. |
| Exposing `cocoindex --live` (continuous watch mode) | Interesting future feature but a different scenario class than the four lifecycle stages. Add later via its own propose if wanted. |
| Reading legacy env vars (`LANCEDB_URI`, `LANCEDB_MCP_*`, `KUZU_DB_PATH`) for one release with a deprecation warning | Considered and rejected. "No users yet" + the explicit migration table in PR-CLI-2's release notes is enough. Carrying legacy code paths adds complexity and lulls future readers into thinking those names are still supported. |
| Reading legacy config file `.lancedb-mcp.yml` for one release with a deprecation warning | Same reasoning as above. |
| Auto-migrating `lancedb_data/` → `.java-codebase-rag/` on first run | Considered and rejected. Magic file moves break operator expectations; explicit `mv` in the migration guide is clearer and reversible. |
| Renaming internal modules (`kuzu_queries.py`, `search_lancedb.py`, `mcp_v2.py`, `index_common.py`) | Internal-only; operator never imports them. Churn without payoff. Re-evaluate if a future refactor restructures the package layout anyway. |

---

## §6 — Migration plan: 3 PRs

| PR | Title | Purpose | Tests |
|---|---|---|---|
| PR-CLI-1 | propose: CLI, config, and naming consolidation | Merge this propose. | n/a (doc-only). |
| PR-CLI-2 | feat: consolidate CLI surface, env vars, config, and package naming | Implement all of §3 in one PR (lifecycle subcommands, env-var consolidation, YAML expansion, file/package rename, tracking-issue creation). | Unit tests for each subcommand's pre-flight checks (existing-index refusal on `init`, --yes gate on `erase`); precedence-chain unit tests (CLI > env > YAML > default for `embedding.model` and `embedding.device`); YAML rename test (old filename ignored, new filename read); index-dir resolution test (defaults to `<project_root>/.java-codebase-rag` when env unset); Kuzu-path derivation test (`<index_dir>/code_graph.kuzu` always). Integration test: `init` → `increment` → `meta` → `erase` round-trip on `tests/bank-chat-system`. Snapshot test on `--help` output structure (subcommand groups present). Package-rename test: `from java_codebase_rag.cli import main` works. |
| PR-CLI-3 | docs: propagate new CLI / config / naming across all docs | Catch every doc that references any legacy surface (old subcommand, old env var, old config filename, old index path, old Python package). Sweep listed below. | n/a (docs). Manual verification of every example invocation; grep-audit (see acceptance command). |

**PR-CLI-3 doc sweep.** Every file expected to need an edit; reviewer should run the acceptance grep at the end to confirm none missed.

- `README.md` — §2 (env vars) shrunk to 5-row table; §5 (CLI reference) rewritten with new subcommand table; §6 (graph layer) updated for new YAML filename; §7 (brownfield overrides) examples renamed; §8 (ignore patterns) updated for `.java-codebase-rag/ignore` (was `.lancedb-mcp/ignore`); new "Migration from legacy names" section with explicit `mv` commands.
- `docs/JAVA-CODEBASE-RAG-CLI.md` — operator playbook restructured per-scenario (`init` / `increment` / `reprocess` / `erase`), exit codes table updated, deprecation note for `refresh`, `python -m java_codebase_rag.cli` invocation example, env-var section updated.
- `docs/AGENT-GUIDE.md` — any `refresh`, `LANCEDB_MCP_*`, or `.lancedb-mcp.yml` reference updated.
- `docs/MANUAL-VERIFICATION-CHECKLIST.md` — setup phase replaces `refresh` with `init` (first-time path); env-var setup updated; YAML config filename updated.
- `docs/paper/paper.tex` — architecture paper updated for new CLI verbs / env vars / file paths; rebuild `paper.pdf` (Russian translation `paper_ru.tex` is a standalone artifact outside the repo and is not in scope).
- `AGENTS.md` — CLI doc reference + any `refresh` mention.
- `CODEBASE_REQUIREMENTS.md` — every `.lancedb-mcp.yml` / `LANCEDB_MCP_*` / `lancedb_data` reference updated.
- `mcp.json.example` — `LANCEDB_URI` and `KUZU_DB_PATH` collapsed into `JAVA_CODEBASE_RAG_INDEX_DIR`; comments updated.
- `propose/REFRESH-CODE-INDEX-AUTO-MODE-PROPOSE.md` — one-line note that `refresh` is being renamed to `reprocess`.
- `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md` — one-line note that the new tracking issue (created in PR-CLI-2) is the user-facing handle.
- `propose/PRODUCT-VISION.md` — update `lancedb_data` mention (§ about Kuzu's on-disk footprint) and any `refresh` reference.
- `.gitignore` — add `.java-codebase-rag/`, keep `lancedb_data/` for grace-period cleanup, or remove if PR-CLI-2 doesn't keep any compatibility shim.

**Acceptance grep** (run by reviewer):

```bash
grep -rEi "refresh|lancedb-mcp|lancedb_data|LANCEDB_URI|LANCEDB_MCP|KUZU_DB_PATH|COCOINDEX_DB|user_rag" \
  -- README.md AGENTS.md CODEBASE_REQUIREMENTS.md docs/ propose/ mcp.json.example .gitignore
```

Expected output after PR-CLI-3:

- The `refresh` deprecation-note mentions in `README.md` and `docs/JAVA-CODEBASE-RAG-CLI.md` (intentional).
- One-line notes in the two related propose files (intentional).
- The migration section in `README.md` and `docs/JAVA-CODEBASE-RAG-CLI.md` that documents the rename (intentional; quotes the old names by design).
- **Nothing else.** Anything else is a missed file.

The startup-slowness fix (deferred imports in `cli.py`) is a **separate, prior PR** outside this migration; it does not change the surface and should land before PR-CLI-2 so contributors testing the new subcommands aren't taxed by the multi-second startup.

---

## §7 — Decisions taken (no longer open)

**Lifecycle surface**

1. **Four lifecycle subcommands**: `init`, `increment`, `reprocess`, `erase`. No fifth lifecycle verb without a new propose.
2. **`refresh` is deprecated, not renamed**: stays as a hidden alias of `reprocess` for one release with a stderr deprecation warning, then removed. This is the **only** name in the whole consolidation with a deprecation window — everything else (env vars, YAML filename, index dir, Python package) is a hard rename per the breaking-changes-allowed policy.
3. **`increment` ships as Lance-only with a loud warning**, not blocked until Kuzu incremental lands. The warning text includes the exact `reprocess` command and a tracking GitHub issue URL.
4. **`init` refuses on existing index**, `reprocess` does not. Different safety policies for different operator intents.
5. **`erase` requires `--yes` or interactive TTY confirm**. Refuses in non-TTY without `--yes` (CI safety).

**Environment variables (9 → 5)**

6. **`LANCEDB_MCP_ALLOW_REFRESH` is dropped entirely**. The original rationale ("opt-in gate for destructive `refresh`") is replaced by per-subcommand safety: `init` refuses on existing index, `erase` requires `--yes`/TTY confirm, `reprocess` is intentional. No global opt-in.
7. **`LANCEDB_MCP_GRAPH_ENABLED` is dropped entirely**. Graph is always built. There is no "graph-disabled" mode worth maintaining as a config switch.
8. **`LANCEDB_MCP_MICROSERVICE_ROOTS` is dropped entirely**; multi-root is YAML-only via `microservice_roots:`. Env-var-by-comma-split is a worse interface than YAML.
9. **`LANCEDB_MCP_PROJECT_ROOT` is dropped entirely**. Project root is derived from CWD / `--project-root` flag.
10. **`COCOINDEX_DATABASE_URL` is dropped entirely** from the documented surface. Cocoindex's internal Postgres is an implementation detail; if it needs to be overridden, it can be set in the shell environment but is not part of the public config.
11. **`LANCEDB_URI` and `KUZU_DB_PATH` are merged into one**: `JAVA_CODEBASE_RAG_INDEX_DIR`. Lance tables live under `<index_dir>/`; Kuzu DB lives at `<index_dir>/code_graph.kuzu`. One env var, one directory, one mental model.
12. **`LANCEDB_MCP_DEBUG_CONTEXT` → `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`**, **`LANCEDB_MCP_RUN_HEAVY` → `JAVA_CODEBASE_RAG_RUN_HEAVY`**. Pure prefix rename, same semantics. No `LANCEDB_MCP_*` env vars survive.
13. **`SBERT_MODEL` and `SBERT_DEVICE` keep their names** (they're upstream-style names, not tool-prefixed) and gain YAML equivalents under `embedding:`.

**YAML config**

14. **`.lancedb-mcp.yml` → `.java-codebase-rag.yml`** (hard rename, no fallback to the old filename). Operators see a one-line stderr hint if the old filename is present: "`.lancedb-mcp.yml` is no longer read; rename to `.java-codebase-rag.yml`."
15. **New `embedding:` section** in YAML mirrors `SBERT_MODEL` / `SBERT_DEVICE`. Precedence: **CLI flag > env var > YAML > built-in default**. Same chain for every overridable knob.
16. **No new YAML keys beyond `embedding:`** in this round. `microservice_roots:` and `ignore:` already exist and are unchanged.

**Naming, filesystem, Python package**

17. **Default index directory: `./.java-codebase-rag/`** (was `./lancedb_data/`). Dot-prefix matches the YAML filename style and de-clutters `ls`.
18. **Python package `user_rag/` → `java_codebase_rag/`**. `[project.scripts]` updated to `java-codebase-rag = "java_codebase_rag.cli:main"`.
19. **`pyproject.toml` `[project].name` → `java-codebase-rag`** (was `java-enterprise-codebase-rag`). Matches GitHub repo + CLI binary + Python package. One name across all four surfaces.
20. **Internal module names are unchanged**: `kuzu_queries.py`, `search_lancedb.py`, `mcp_v2.py`, `index_common.py`, etc. Only the top-level package directory is renamed. Internal names are implementation detail; renaming them is gratuitous churn.
21. **All renames land in PR-CLI-2** (single implementation PR), not split. Doing them piecemeal would either ship a temporarily-inconsistent codebase or require many overlapping PRs. One atomic commit is cleaner given breaking changes are allowed.

**`--help` and operator UX**

22. **`--help` redesign is part of this propose, not a follow-up.** Operator confusion is half-CLI-structure, half-help-output; fixing one without the other ships an incomplete improvement.
23. **Subcommand groups in help output**: lifecycle / introspection / analysis. Three labels are enough; finer subdivision is overdesign.

**Migration mechanics**

24. **No deprecation window for env/config/package renames.** Breaking changes are explicitly allowed (no external users yet). The only carve-out is `refresh` → `reprocess`, because `refresh` is the one verb operators have typed thousands of times in this codebase's history and warrants the courtesy.
25. **GitHub tracking issue** for Kuzu incremental rebuild is created as part of PR-CLI-2 (not separately). Issue URL is committed into the warning message via a constant in `cli.py`.
26. **Slowness fix is a prior PR**, outside this propose. This propose does not block on it but recommends ordering.
27. **Docs split into a follow-up PR** (PR-CLI-3) so the implementation PR (PR-CLI-2) stays reviewable. README + CLI doc updates can be reviewed independently of the code change.
28. **No `--dry-run` flag in this round.** Useful but expands scope; defer to a follow-up if operators ask.
29. **PR-CLI-3 sweeps every doc that references any legacy surface.** Not just README + CLI doc — the full list (see §6) plus a grep-audit acceptance check. The deprecation window only works if every doc points at the new verb / new env var / new YAML name / new package name.

---

## §8 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Operators run `increment` and don't read the warning, then complain that graph navigation is stale. | Warning is multi-line, written to stderr, and includes the exact `reprocess` command verbatim. |
| CI pipelines in the wild still call `refresh`. | One-release deprecation window; `refresh` keeps working unchanged with a deprecation warning on stderr (not stdout, so it doesn't break JSON-piping consumers). |
| `init`'s "existing index" check has a false positive (e.g. operator points `--lancedb-path` at a directory that contains unrelated files). | Refusal output enumerates *which* known sub-paths are non-empty (`code_graph.kuzu`, `code_index_*` Lance tables); operator can `erase` or pick a different path. |
| `erase` accidentally wipes the wrong directory if `JAVA_CODEBASE_RAG_INDEX_DIR` is misconfigured. | Pre-deletion summary lists exact paths and sizes; non-TTY requires `--yes`; interactive TTY requires typed confirmation. |
| Snapshot test on `--help` output is brittle (any wording change breaks it). | Snapshot only tests *structure* (group labels present, subcommands grouped correctly), not exact wording. |
| Tracking GitHub issue's URL changes (issue closed, repo renamed) and the warning points at a 404. | Issue URL is a single constant in `cli.py`; updating it is a one-line PR. Acceptable risk for the user-experience benefit. |
| Cocoindex's catch-up mode has bugs we haven't seen because we always pass `--full-reprocess`. | Integration test in PR-CLI-2 exercises `init` → modify-file → `increment` → assert chunk content updated. If catch-up has issues, surface them now instead of after the rename. |
| Operators run `init` after `erase` and `init` still refuses (e.g. cocoindex internal state DB not fully cleaned up by `drop`). | Mitigation in PR-CLI-2: `erase` test verifies `init` succeeds immediately afterward; if `drop` leaves residue, the implementation also clears it. Recorded as an explicit test, not a hope. |
| **Silent rename surprises operators**: someone pulls latest, runs the old `LANCEDB_URI=...` command, and gets a confusing "index not found" because the new code only reads `JAVA_CODEBASE_RAG_INDEX_DIR`. | At CLI startup, if any of the deprecated env vars (`LANCEDB_URI`, `KUZU_DB_PATH`, `LANCEDB_MCP_*`) are set in the environment, emit a one-line stderr warning naming the new variable. Pure courtesy; the var is not honored. |
| **`.lancedb-mcp.yml` left in the repo / home dir after upgrade.** Operator wonders why YAML settings stopped taking effect. | At CLI startup, if `.lancedb-mcp.yml` exists in the search path and `.java-codebase-rag.yml` does not, emit a one-line stderr hint: "found legacy `.lancedb-mcp.yml`; rename to `.java-codebase-rag.yml` to re-enable." One-shot, no per-invocation spam beyond startup. |
| **`lancedb_data/` directory left orphaned** after operators move to `.java-codebase-rag/`. Disk usage grows silently. | PR-CLI-3 adds a one-line `find / -type d -name lancedb_data` suggestion to README's migration section. Not auto-cleaned; the tool does not delete directories the operator didn't tell it to. |
| **Library consumers break**: anyone importing `from user_rag.X import Y` in external code gets an `ImportError` after the package rename. | Explicitly accepted per breaking-changes-allowed policy. README migration section documents the import-path change. No `user_rag` shim package is published. |
| **Precedence chain inversion bug**: an operator sets `SBERT_MODEL` env var expecting it to win, but a YAML value takes effect instead. | Unit test asserts the exact resolution order (CLI > env > YAML > default) for every config-driven value; `meta` subcommand prints which source supplied each setting (`embedding.model: nomic-ai/... (from: env)`) so operators can debug without reading code. |
| **PR-CLI-2 is too large for a single review** (lifecycle + env + YAML + filesystem + Python package, all atomic). | Reviewable structure: separate commits within PR-CLI-2 per concern (one commit for env-var consolidation, one for YAML, one for index-dir default, one for package rename, one for lifecycle subcommands). Reviewer can step commit-by-commit even though the PR merges as one unit. |
| **Help-output groups (lifecycle / introspection / analysis) feel arbitrary** to a first-time operator who expects flat alphabetical listing. | Each subcommand entry in `--help` has a one-line description; groups are visual scaffolding, not a navigation requirement. `--help <subcommand>` works regardless of group. |

---

## Appendix A — `increment` warning text (verbatim)

```
WARNING: AST graph (Kuzu) incremental rebuild is not yet implemented.
The graph reflects the index state from the last `init` or `reprocess`,
which means `find`, `neighbors`, and `describe` may return stale results
for files changed since then.

Lance vector index has been updated incrementally and is current.

For an up-to-date graph, run:
    java-codebase-rag reprocess

Track progress on Kuzu incremental rebuild:
    https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/<N>
```

`<N>` is filled in by PR-CLI-2 when the tracking issue is opened. The constant lives at module scope in `cli.py`.

---

## Appendix B — What changed (traceability)

First draft. To be filled in if revisions follow review feedback.
