# Plan: CLI scenarios — lifecycle verbs, config consolidation, naming

Status: **active**. This plan implements
[`propose/CLI-SCENARIOS-PROPOSE.md`](../propose/CLI-SCENARIOS-PROPOSE.md).
**PR-CLI-1** (propose approval + plan tracking) is [#72](https://github.com/HumanBean17/java-codebase-rag/pull/72); merge that PR to `master` to close the gate on mainline.

Depends on: **none** (engine work for Kuzu incremental rebuild stays under
[`propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`](../propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md)
and the tracking issue created in PR-CLI-2). **Recommended ordering:** any
standalone `cli.py` import-latency / deferred-import hardening PR should land
before PR-CLI-2 so contributors exercising new subcommands do not pay multi-second
`--help` cost (see propose §6 closing note).

## Goal

- Replace the single lifecycle verb `refresh` with **four scenario-first
  subcommands:** `init`, `increment`, `reprocess`, `erase`, plus a **one-release
  hidden alias** `refresh` → `reprocess` with stderr deprecation only.
- Consolidate operator configuration: **9 → 5 environment variables** (headline:
  nine non-`SBERT_*` names folded — see propose §3.5 for the **11-name**
  inventory), YAML file rename **`.lancedb-mcp.yml` → `.java-codebase-rag.yml`**,
  optional YAML **`index_dir:`**, default index directory
  **`./lancedb_data` → `./.java-codebase-rag/`**, unified precedence
  **CLI flag > env var > YAML > built-in default** for every knob that appears in
  more than one place.
- Rename the Python package **`user_rag/` → `java_codebase_rag/`** and
  **`pyproject.toml` `[project].name` → `java-codebase-rag`** so CLI binary,
  distribution name, and import path align.
- Ship **`increment` as Lance-only** with a **fixed multi-line stderr warning**
  (includes verbatim `reprocess` command and a **single module-level constant**
  GitHub issue URL for Kuzu incremental work).
- Rewrite **root and subcommand `--help`** with grouped sections (lifecycle /
  introspection / analysis), descriptions, examples, and exit-code notes.
- **Update operator-facing documentation** in **PR-CLI-3** so it matches the new
  CLI, env vars, YAML filename, index paths, ignore layout, package import path, and
  migration story — **`README.md`**, **`docs/*`**, **`AGENTS.md`**,
  **`CODEBASE_REQUIREMENTS.md`**, **`.cursor/rules/*.mdc`**, selected **`propose/*.md`**,
  **`mcp.json.example`** (comment polish only; keys land in PR-CLI-2), **`paper.pdf`**
  rebuild, plus **acceptance grep** / agent-rules audit per
  [`propose/CLI-SCENARIOS-PROPOSE.md`](../propose/CLI-SCENARIOS-PROPOSE.md) §6. **PR-CLI-2**
  does **not** replace this: it ships code + `mcp.json.example` keys; a full doc
  sweep is explicitly **out of scope** for PR-CLI-2 (see PR-CLI-2 Definition of done).
- **No ontology bump** and **no Kuzu / Lance schema changes** — this is operator
  surface, paths, and config only. Existing graphs remain valid; operators migrate
  paths and env vars using the PR-CLI-3 docs.

## Principles (do not relitigate in review)

- **Subcommand names are operator scenarios, not pipeline stages.** No
  `build-graph-only` / `cocoindex-update-only` as first-class CLI verbs.
- **Partial fidelity is loud.** `increment` never runs `build_ast_graph.py`; stderr
  warning is the contract (stale graph is acceptable only when the operator has
  been warned).
- **One scenario, one safe default.** `init` refuses an existing index; `reprocess`
  does not; `erase` requires `--yes` or interactive TTY confirm; non-TTY `erase`
  without `--yes` exits **2**.
- **`refresh` is the only deprecation window.** Hidden alias + stderr warning for
  one release, then delete. **No** reading legacy env vars or legacy config
  filenames for compatibility — optional **stderr hints only** when legacy
  artifacts are *detected* (see propose §3.5 / §7 / §8).
- **Cardinality of the CLI surface:** 8 subcommands total (4 lifecycle + 3
  introspection + 1 analysis). A fifth lifecycle verb requires a new propose.
- **Internal module filenames** (`search_lancedb.py`, `kuzu_queries.py`, etc.) stay
  unchanged; only operator-facing package boundary and paths rename.
- **Breaking changes are explicit.** No `user_rag` import shim; no honoring
  `LANCEDB_URI` / `LANCEDB_MCP_*` / `KUZU_DB_PATH` / `COCOINDEX_DB` as documented
  public configuration — migration docs + optional detect-only stderr hints carry
  the burden.

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-CLI-1** | Land / freeze propose (doc-only merge of `CLI-SCENARIOS-PROPOSE.md` if not already on `master`) | none | `propose/CLI-SCENARIOS-PROPOSE.md` (status bump); `plans/PLAN-CLI-SCENARIOS.md` (tracking) | n/a | none |
| **PR-CLI-2** | Full implementation: lifecycle handlers, env + YAML + index layout, package rename, `server.py` / indexer / path helpers, **`mcp_v2.py`**, **`path_filtering.py`** (`.lancedb-mcp/ignore` → `.java-codebase-rag/ignore`), help redesign, tracking issue constant, user-visible stderr hints; **`mcp.json.example`** env keys = source of truth | none | `pyproject.toml`, package dir rename, `server.py`, `mcp_v2.py`, `java_codebase_rag/cli.py`, `java_index_flow_lancedb.py`, `graph_enrich.py`, `path_filtering.py`, `search_lancedb.py`, `kuzu_queries.py`, `build_ast_graph.py`, tests, `mcp.json.example`, `.gitignore`, any other `user_rag` / env / path references in Python | unit + integration + help-structure test (see below) | PR-CLI-1 merged |
| **PR-CLI-3** | Doc and example sweep + **`.cursor/rules/`** + migration sections + acceptance grep; **`mcp.json.example`** comment/example polish only (keys already correct from PR-CLI-2) | none | `README.md`, `docs/*`, `AGENTS.md`, `.cursor/rules/*.mdc`, `CODEBASE_REQUIREMENTS.md`, `mcp.json.example` (prose only if needed), selected `propose/*.md`, `.gitignore` notes | manual grep audit; `ruff` / `pytest` unchanged by docs | PR-CLI-2 merged |

Landing order: **PR-CLI-1 → PR-CLI-2 → PR-CLI-3**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `init` | CocoIndex catch-up from empty + full `build_ast_graph.py`; **refuse** if `code_graph.kuzu` or `code_index_*` Lance tables already present under resolved index dir; exit **2**. |
| `increment` | CocoIndex update **without** full reprocess; **no** graph build; **full** stderr warning block **every** run (including no-op catch-up); template in propose Appendix A (canonical issue URL when filed). |
| `reprocess` | Same pipeline as today’s `refresh` (full Lance reprocess + graph rebuild). |
| `erase` | Summary of paths + sizes; `cocoindex drop` + remove Kuzu dir + Lance tables under index dir; `--yes` or TTY interactive confirm. |
| Index layout | `JAVA_CODEBASE_RAG_INDEX_DIR` (or `--index-dir` / YAML `index_dir:` / default); Kuzu at `<index_dir>/code_graph.kuzu`; Lance tables under `<index_dir>/`; cocoindex state DB default under `<index_dir>/` (`COCOINDEX_DB` dropped from public surface — propose §3.5 / §7). |
| Java tree root | **`--source-root`** or cwd only — **no `--project-root` flag** (propose §3.1 / §7). |
| Layered ignore files | **PR-CLI-2** renames `.lancedb-mcp/ignore` → `.java-codebase-rag/ignore` in `path_filtering.py` (project + nested); same semantics as today (propose §6 README bullet). |
| `microservice_roots` | **YAML-only** (env var `LANCEDB_MCP_MICROSERVICE_ROOTS` removed). |
| Graph disable switch | Removed; graph follows “DB exists” auto-detect (no `LANCEDB_MCP_GRAPH_ENABLED`). |
| Refresh gate | `LANCEDB_MCP_ALLOW_REFRESH` removed; safety is per-subcommand behaviour. |
| `meta` / precedence debugging | `meta` exposes **embedding** resolution + **provenance** (`cli` / `env` / `yaml` / `default`); existing path fields stay accurate under the unified resolver (propose §3.7). |
| Legacy env vars set in environment | **Do not read**; optional one-line stderr notice that names the replacement var (propose §8). |
| Legacy `.lancedb-mcp.yml` present | **Do not read**; optional one-line stderr hint to rename (propose §8 / UC22). |

---

# PR-CLI-1 — propose merge (documentation gate)

## File-by-file changes

### 1. `propose/CLI-SCENARIOS-PROPOSE.md`

- Set status from **draft** to **approved** (or equivalent) once reviewers sign off.
- Ensure Appendix A issue URL placeholder is clearly marked as filled in PR-CLI-2
  (or update to org/repo that matches the canonical GitHub remote).

## Tests for PR-CLI-1

- n/a (documentation only).

## Definition of done (PR-CLI-1)

- Propose is the agreed source of truth for CLI-2 / CLI-3 implementation.
- No code changes in this PR unless the team prefers to land propose alongside
  PR-CLI-2 (then fold PR-CLI-1 into process as a checklist item only).

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Review & merge propose | `propose/CLI-SCENARIOS-PROPOSE.md`; plan tracking in this file | [#72](https://github.com/HumanBean17/java-codebase-rag/pull/72) merged to `master` |

---

# PR-CLI-2 — implementation (single atomic PR)

## File-by-file changes

### 1. `pyproject.toml`

- `[project].name` → `java-codebase-rag`.
- `[project.scripts]` entrypoint → `java_codebase_rag.cli:main`.
- Packages / discovery updated for new package name.

### 2. `user_rag/` → `java_codebase_rag/` (package rename)

- `git mv` the package directory; update **all** imports and references across the
  repo (tests, scripts, docs examples in code comments if any).
- Replace `tests/test_user_rag_cli.py` with `tests/test_java_codebase_rag_cli.py`
  (and rename helpers like `_install_user_rag_entrypoint` consistently).

### 3. `java_codebase_rag/cli.py` (was `user_rag/cli.py`)

- **Parser:** grouped subcommand help (lifecycle / introspection / analysis);
  `help=` + `description=` + examples per subcommand; custom formatter if needed.
- **Flags:** keep **`--source-root`** as the only Java-tree-root flag (cwd if
  absent); add **`--index-dir`**, **`--embedding-model`**, **`--embedding-device`**
  as needed; map to env + internal resolution. **Do not add `--project-root`.**
- **Handlers:** implement `init`, `increment`, `reprocess`, `erase`; wire
  `reprocess` to current refresh pipeline; implement hidden `refresh` alias with
  deprecation stderr (stderr only).
- **Pre-flight:** `init` existing-index detection; `erase` gating; path resolution
  for index dir and derived Kuzu path.
- **Constant:** module-level `KUZU_INCREMENTAL_TRACKING_ISSUE_URL` (or similar)
  set to the real issue URL once the issue is opened.
- **Startup hints:** legacy env / legacy YAML file detection → one-line stderr
  hints (no functional read of legacy values).

### 4. `server.py`

- Remove or bypass `LANCEDB_MCP_ALLOW_REFRESH` gating; align
  `run_refresh_pipeline` (or successors) with new env names and index layout.
- Update tool / error messages that mention `refresh` or old env vars to
  `reprocess` / `JAVA_CODEBASE_RAG_*` / migration wording.
- Ensure nothing in MCP handlers writes **stdout** diagnostics (stdio rule
  unchanged).

### 5. `java_index_flow_lancedb.py`, `search_lancedb.py`, `kuzu_queries.py`,
   `graph_enrich.py`, `path_filtering.py`, `build_ast_graph.py`, `mcp_v2.py`,
   `index_common.py` (as needed)

- Replace reads of `LANCEDB_URI`, `KUZU_DB_PATH`, `LANCEDB_MCP_PROJECT_ROOT`,
  `LANCEDB_MCP_MICROSERVICE_ROOTS`, `LANCEDB_MCP_GRAPH_ENABLED`, cocoindex DB
  path env, etc., with the consolidated model (single index dir + YAML + derived
  paths). **`mcp_v2.py`** currently defaults via `LANCEDB_URI` — must move with the
  same resolver as `search_lancedb.py` / server.
- **`path_filtering.py`:** rename ignore directory segment **`.lancedb-mcp/ignore`**
  → **`.java-codebase-rag/ignore`** (project-level and nested); update
  `_scan_negation_any_lancedb` naming / docstrings and any tests that anchor on the
  old path.
- Config loader: **only** `.java-codebase-rag.yml` / `.java-codebase-rag.yaml`;
  implement `embedding.model` / `embedding.device`; keep existing brownfield keys
  unchanged.
- Update default relative index path to `.java-codebase-rag` where applicable.

### 6. Tests

- `tests/conftest.py`, `tests/test_lancedb_e2e.py`: `JAVA_CODEBASE_RAG_RUN_HEAVY`
  rename; path / env updates.
- Any test using `LANCEDB_MCP_*`, `LANCEDB_URI`, `lancedb_data`, or `user_rag`.

### 7. `mcp.json.example`, `.gitignore`

- **PR-CLI-2 is source of truth** for env keys / structure so MCP launch matches
  `server.py`. PR-CLI-3 only polishes comments if needed (propose §6).
- Example env: `JAVA_CODEBASE_RAG_INDEX_DIR` (and other new vars only).
- Ignore `.java-codebase-rag/`; keep or document `lancedb_data/` per migration note.

### 8. GitHub

- Open issue **“AST graph (Kuzu) incremental rebuild”** referencing
  `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`; paste URL into cli constant.

## Tests for PR-CLI-2

Name these in implementation (adjust only if pytest collection or existing naming
conventions require it):

1. `test_cli_init_refuses_when_index_paths_non_empty`
2. `test_cli_erase_refuses_non_tty_without_yes`
3. `test_cli_erase_succeeds_with_yes_flag`
4. `test_embedding_model_precedence_cli_over_env_over_yaml_over_default`
5. `test_embedding_device_precedence_cli_over_env_over_yaml_over_default`
6. `test_yaml_config_ignores_legacy_filename_reads_new_filename`
7. `test_index_dir_defaults_to_dot_java_codebase_rag_under_project_root`
8. `test_index_dir_precedence_cli_over_env_over_yaml_over_default`
9. `test_kuzu_path_derived_as_index_dir_code_graph_kuzu`
10. `test_cli_lifecycle_round_trip_init_increment_meta_erase` (fixture:
   `tests/bank-chat-system` or temp copy; assert `increment` warning on stderr)
11. `test_help_output_includes_three_group_labels` — assert **structure** (group
    labels + subcommand grouping), **not** exact line breaks or the illustrative
    `diagnose-ignore PATH` layout in propose §3.1 (anti-brittleness — propose §8).
12. `test_java_codebase_rag_cli_module_importable`
13. `test_refresh_hidden_alias_deprecates_on_stderr`
14. `test_increment_emits_kuzu_stale_warning_block` (assert warning on **no-op** run too)
15. `test_meta_reports_embedding_setting_source` (or equivalent helper coverage)
16. `test_legacy_env_var_set_emits_stderr_hint` (optional if covered by integration)
17. `test_init_after_erase_succeeds` (cocoindex residue: must pass or fix erase)

**Integration nuance (propose §8):** add a test that touches a Java file after
`init` and runs `increment`, then asserts Lance-visible change (chunk / row
signal) so cocoindex catch-up is exercised under the new CLI.

**Commands (must pass before merge):**

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Heavy: `LANCEDB_MCP_RUN_HEAVY` name changes to `JAVA_CODEBASE_RAG_RUN_HEAVY` in
the same PR — run heavy locally when touching indexer paths:

```bash
JAVA_CODEBASE_RAG_RUN_HEAVY=1 .venv/bin/python -m pytest tests -v
```

## Definition of done (PR-CLI-2)

- All 8 subcommands behave per propose §3 and use-case table (UC1–UC30) where
  applicable.
- Legacy env vars are not honored; optional stderr hints implemented per locked
  policy.
- `refresh` works as hidden alias with deprecation text; not listed in standard
  `--help` output.
- Package install exposes `java-codebase-rag` console script and
  `python -m java_codebase_rag.cli`.
- README **not** required to be fully updated in this PR (deferred to PR-CLI-3),
  but **code** and **tests** must be consistent with the new surface.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Open tracking issue; add URL constant | `java_codebase_rag/cli.py` | Issue live; constant set |
| 2 | Implement config resolution (index dir, embedding, YAML load, hints) | cli + loaders + `server.py` helpers | Unit tests for precedence + YAML rename pass |
| 3 | Implement lifecycle commands | `java_codebase_rag/cli.py`, `server.py`, indexer modules | Round-trip + erase + init refusal pass |
| 4 | Package rename + pyproject | whole tree | `test_java_codebase_rag_cli_module_importable` passes |
| 5 | Help / groups / hidden alias | `java_codebase_rag/cli.py` | `test_help_output_includes_three_group_labels`, alias test pass |
| 6 | Sweep Python for old env / package / path strings; update tests that anchor on `.lancedb-mcp/ignore` | grep-driven + `tests/test_path_filtering.py` (if applicable) | `ruff` + `pytest` clean |

---

# PR-CLI-3 — documentation and migration sweep

## File-by-file changes

Follow the **explicit file list** in propose §6 (`README.md`,
`docs/JAVA-CODEBASE-RAG-CLI.md`, `docs/AGENT-GUIDE.md`,
`docs/MANUAL-VERIFICATION-CHECKLIST.md`, `docs/paper/paper.tex` + rebuild
`paper.pdf`, `AGENTS.md`, **`.cursor/rules/*.mdc`** (agent rules audit),
`CODEBASE_REQUIREMENTS.md`, `mcp.json.example` (comments only — keys from PR-CLI-2),
`propose/INDEX-AUTO-MODE-PROPOSE.md`,
`propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`, `propose/PRODUCT-VISION.md`,
`.gitignore`).

Add **Migration from legacy names** sections with explicit `mv` commands
(`lancedb_data` → `.java-codebase-rag`, `.lancedb-mcp.yml` →
`.java-codebase-rag.yml`, env var mapping table).

## Tests for PR-CLI-3

- n/a automated (documentation). Run **acceptance grep** from propose §6 and
  reconcile every match as intentional (migration text, deprecation notes,
  one-line cross-propose references).

## Definition of done (PR-CLI-3)

- Acceptance grep passes interpretive review: only expected hits remain (**docs +
  `.cursor/rules/` scope per propose §6** — internal `*.py` filenames like
  `search_lancedb.py` are **out of scope** for that grep and may retain `lancedb`
  by design). **Stale** legacy env / `refresh` / `user_rag` in rules **must** be
  eliminated except explicit migration text.
- Every example command in touched docs runs against the new CLI names and env
  vars (spot-check).
- `.cursor/rules` / `AGENTS.md` references updated so agents use
  `JAVA_CODEBASE_RAG_RUN_HEAVY` in instructions.

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | README + CLI operator guide | `README.md`, `docs/JAVA-CODEBASE-RAG-CLI.md` | New subcommand table + 5 env vars + migration |
| 2 | Agent + checklist + requirements | `docs/*`, `CODEBASE_REQUIREMENTS.md` | No stale operator paths |
| 3 | Paper + proposes + example MCP JSON | `docs/paper/`, `propose/*`, `mcp.json.example` | PDF rebuilt; examples updated |
| 4 | Acceptance grep | repo root | Reviewer sign-off |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Operators miss stderr warnings for `increment` | Medium | Multi-line template + integration test; docs emphasize graph staleness |
| 2 | Large PR-CLI-2 review load | Medium | Land as one PR with **sequential commits** per concern (propose §8) |
| 3 | Cocoindex catch-up bugs | Medium | File-touch + `increment` integration test in PR-CLI-2 |
| 4 | `erase` leaves cocoindex residue → `init` refuses | High | Explicit `init`-after-erase test; extend `erase` cleanup if needed |
| 5 | Brittle full-string `--help` snapshots | Low | Assert **structure** (group labels + subcommand presence), not prose |
| 6 | Tracking issue URL drift | Low | Single constant in `cli.py`; one-line fix PR if URL changes |
| 7 | Silent breakage for old scripts | Medium | stderr hints for legacy env; migration section in PR-CLI-3; no silent reads |

# Out of scope

- Kuzu incremental rebuild (Tier 2 engine work).
- `meta --graph-freshness` or any automatic “is graph stale?” query.
- Pipeline-stage subcommands; `cocoindex --live` watch mode; `init --force`.
- New YAML keys beyond `embedding:` + renames already in the propose (no new
  knobs).
- Renaming internal Python modules (`search_lancedb.py`, etc.).
- Reading legacy env vars or legacy config files for compatibility.
- Automatic migration of `lancedb_data/` or config files on disk.

# Whole-plan done definition

1. Propose merged and treated as locked for this rollout.
2. PR-CLI-2 merged: new lifecycle CLI, 5 env vars, new YAML + index defaults,
   package rename, tests green (including heavy where indexer touched).
3. PR-CLI-3 merged: docs, **`AGENTS.md`**, and **`.cursor/rules/`** fully migrated;
   acceptance grep clean per propose §6 (including rules directory).
4. Tracking issue exists and the URL in `cli.py` matches it.
5. Per-PR agent prompts live in
   [`plans/CURSOR-PROMPTS-CLI-SCENARIOS.md`](./CURSOR-PROMPTS-CLI-SCENARIOS.md)
   (template: `plans/completed/CURSOR-PROMPTS-TIER1B.md`).

# Tracking

- `PR-CLI-1`: **done (awaiting `master` merge)** — [#72](https://github.com/HumanBean17/java-codebase-rag/pull/72) (propose **approved** + this plan’s tracking update)
- `PR-CLI-2`: **implemented (awaiting merge to `master`)** — lifecycle CLI + config consolidation in tree
- `PR-CLI-3`: **in review** — `chore/cli-scenarios-docs`: README / docs / `AGENTS.md` / `.cursor/rules/` / migration / `paper.pdf` rebuild; mark **merged** when the PR lands on `master` (acceptance grep per `propose/CLI-SCENARIOS-PROPOSE.md` §6)
- `Kuzu incremental tracking issue`: **opened** — [#73](https://github.com/HumanBean17/java-codebase-rag/issues/73) (Tier 2 engine scope; URL matches `KUZU_INCREMENTAL_TRACKING_ISSUE_URL` in `java_codebase_rag/cli.py`)
