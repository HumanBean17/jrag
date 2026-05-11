# Cursor task prompts — CLI scenarios (PR-CLI-1 → PR-CLI-3)

Status: **active**. Implements
[`plans/PLAN-CLI-SCENARIOS.md`](./PLAN-CLI-SCENARIOS.md) and
[`propose/CLI-SCENARIOS-PROPOSE.md`](../propose/CLI-SCENARIOS-PROPOSE.md).

One prompt per PR. Each is **self-contained**: copy the prompt verbatim into
Cursor, attach the files listed in its `@-files` block, and execute in agent
mode. If this prompt disagrees with **`plans/PLAN-CLI-SCENARIOS.md`**, the plan
wins.

**Workflow per PR**

1. Create the branch named in the prompt off the stated base.
2. Attach `@-files` (plan + propose + key modules).
3. Paste the **Prompt** block.
4. Run **`ruff check .`** and **`pytest`** with repo `.venv` (see Tests).
5. Commit; open PR (no direct push to `master`).

**Universal rules**

- Use **`.venv/bin/python`** and **`.venv/bin/ruff`** from the repo root only.
- **No ontology bump** and **no Kuzu/Lance schema changes** in this rollout.
- **`server.py`** is stdio MCP: tool handlers must not write to **stdout**
  (diagnostics → stderr).
- **No `user_rag` import shim**; **do not honor** legacy env vars for config
  (detect-only stderr hints allowed per propose §3.5).
- **Do not special-case** `tests/bank-chat-system/` in production code.
- If a change would violate an **Out of scope** line, **stop and ask**.

---

## PR-CLI-1 — Propose approval (documentation gate)

**Branch:** `chore/cli-scenarios-propose-approval` off `master`.  
**Base:** `master`.  
**Plan section:** `plans/PLAN-CLI-SCENARIOS.md` § PR-CLI-1.  
**Estimated diff size:** 1 file, ~10 LOC.

**Attach (`@-files`):**

- `@plans/PLAN-CLI-SCENARIOS.md` (PR-CLI-1 section only)
- `@propose/CLI-SCENARIOS-PROPOSE.md`

**Prompt:**

````
You are implementing PR-CLI-1 from `plans/PLAN-CLI-SCENARIOS.md`.

## Scope

- Update `propose/CLI-SCENARIOS-PROPOSE.md` **status** from **draft** to **approved**
  (or the repo’s equivalent convention) if reviewers have signed off.
- Ensure **Appendix A** still states that the real issue URL is filled in **PR-CLI-2**
  (placeholder or canonical remote note — do not invent a final URL here).

## Out of scope (do NOT touch)

- Any `.py`, `pyproject.toml`, tests, or `README.md`.
- Implementation of lifecycle subcommands (PR-CLI-2).
- Doc sweep (PR-CLI-3).

## Deliverables

1. Propose status line updated appropriately.
2. Appendix A / traceability note consistent with “URL constant lands in PR-CLI-2”.

## Tests

None (documentation only).

## Sentinel checks

- `git diff master --name-only` should list **only** `propose/CLI-SCENARIOS-PROPOSE.md`
  (or be empty if already merged).

## Manual evidence

N/A.

## Definition of Done

- [ ] PR title: `chore: approve CLI-SCENARIOS propose`
- [ ] Branch: `chore/cli-scenarios-propose-approval`
- [ ] Plan tracking: PR-CLI-1 marked done when merged
````

---

## PR-CLI-2 — CLI lifecycle, env consolidation, package rename

**Branch:** `feat/cli-scenarios` off `master` (after PR-CLI-1 is on `master`, or
stack if your team allows).  
**Base:** `master` (contains merged PR-CLI-1).  
**Plan section:** `plans/PLAN-CLI-SCENARIOS.md` § PR-CLI-2 + **Resolved design
decisions** table.  
**Propose:** `propose/CLI-SCENARIOS-PROPOSE.md` §3, §7, Appendix A (warning text).  
**Estimated diff size:** ~25–40 files, large LOC (single atomic PR; prefer
sequential commits per concern inside the branch).

**Attach (`@-files`):**

- `@plans/PLAN-CLI-SCENARIOS.md`
- `@propose/CLI-SCENARIOS-PROPOSE.md`
- `@user_rag/cli.py` (pre-rename; becomes `java_codebase_rag/cli.py`)
- `@server.py`
- `@java_index_flow_lancedb.py`
- `@search_lancedb.py`
- `@kuzu_queries.py`
- `@graph_enrich.py`
- `@path_filtering.py`
- `@build_ast_graph.py`
- `@mcp_v2.py`
- `@pyproject.toml`
- `@mcp.json.example`
- `@tests/test_user_rag_cli.py`
- `@tests/conftest.py`
- `@tests/test_lancedb_e2e.py`
- `@tests/test_path_filtering.py`
- `@.cursor/rules/python-venv-only.mdc` (venv command paths)

**Prompt:**

````
You are implementing PR-CLI-2 from `plans/PLAN-CLI-SCENARIOS.md`.

Read the **PR-CLI-2** section and the **Resolved design decisions** table in full.
Cross-check behaviour with `propose/CLI-SCENARIOS-PROPOSE.md` §3 / §7 / Appendix A.
If this prompt and the plan disagree, the plan wins.

## Scope

Implement the **full operator surface and code migration** in one PR:

1. **Package rename:** `user_rag/` → `java_codebase_rag/` (`git mv`); update all
   imports; `pyproject.toml` `[project].name` → `java-codebase-rag`, console script
   → `java_codebase_rag.cli:main`.
2. **Lifecycle CLI:** `init`, `increment`, `reprocess`, `erase` per plan + propose;
   hidden **`refresh`** alias → `reprocess` with stderr deprecation (not listed in
   standard `--help`); keep `meta`, `tables`, `diagnose-ignore`, `analyze-pr`.
3. **Flags:** **`--source-root`** only for Java tree (cwd if absent). **`--index-dir`**,
   **`--embedding-model`**, **`--embedding-device`** as needed. **No `--project-root`.**
4. **Env consolidation:** public surface = 5 vars per propose §3.5
   (`JAVA_CODEBASE_RAG_INDEX_DIR`, `SBERT_MODEL`, `SBERT_DEVICE`,
   `JAVA_CODEBASE_RAG_DEBUG_CONTEXT`, `JAVA_CODEBASE_RAG_RUN_HEAVY`). Remove
   `LANCEDB_MCP_ALLOW_REFRESH`, `LANCEDB_MCP_GRAPH_ENABLED`,
   `LANCEDB_MCP_MICROSERVICE_ROOTS`, `LANCEDB_MCP_PROJECT_ROOT`, merge away
   `LANCEDB_URI` / `KUZU_DB_PATH`, drop public `COCOINDEX_DB` (state DB under
   `<index_dir>/`). **Never read** legacy names for config; optional detect-only
   stderr hints per locked policy.
5. **YAML:** load **only** `.java-codebase-rag.yml` / `.yaml`; add `embedding:` and
   optional `index_dir:`; precedence **CLI > env > YAML > default** for shared knobs.
6. **MCP + indexer sweep:** `server.py`, `search_lancedb.py`, `kuzu_queries.py`,
   `graph_enrich.py`, `java_index_flow_lancedb.py`, `build_ast_graph.py`,
   **`mcp_v2.py`**, tests — all use the new resolver (not CLI-only).
7. **`path_filtering.py`:** rename `.lancedb-mcp/ignore` → `.java-codebase-rag/ignore`
   (project + nested); update helpers / tests that anchor on old path.
8. **`increment`:** Lance-only; **full** Appendix A warning block on **every** run
   (including no-op); module-level constant URL for tracking issue — **open** GitHub
   issue “AST graph (Kuzu) incremental rebuild” (link TIER2 propose), paste real URL.
9. **`meta`:** embedding values + **provenance** (`cli`/`env`/`yaml`/`default`);
   keep index/graph path fields accurate under unified resolver.
10. **`mcp.json.example`:** env keys match live server (**source of truth** for this
    file in PR-CLI-2).
11. **`.gitignore`:** `.java-codebase-rag/` as appropriate.
12. **Help:** grouped sections (lifecycle / introspection / analysis); structure
    test must not assert exact `diagnose-ignore` layout (propose §3.1 mock).

## Out of scope (do NOT touch)

- Full **`README.md`** / **`docs/*`** rewrite (PR-CLI-3). Fixing broken links inside
  touched Python docstrings only if unavoidable is OK — no doc-wide sweep.
- **`.cursor/rules/*.mdc`** content updates (PR-CLI-3).
- **Kuzu incremental** engine work; **`meta --graph-freshness`**.
- Renaming internal modules (`search_lancedb.py`, `kuzu_queries.py`, …) — package
  directory rename only.
- **`tests/bank-chat-system/`** Java sources (fixture).
- **Ontology / graph schema** bumps.

If you need to touch anything above, **stop and ask**.

## Deliverables

1. New package `java_codebase_rag/` with CLI implementing §3 lifecycle + hidden
   `refresh` alias.
2. All Python imports use `java_codebase_rag` (no `user_rag`).
3. Consolidated env + YAML loader + optional stderr hints (no legacy honor).
4. `path_filtering.py` ignore path rename + test updates.
5. `mcp_v2.py` + `server.py` + index pipeline on `JAVA_CODEBASE_RAG_INDEX_DIR` model.
6. `mcp.json.example` updated keys.
7. **Tests** — implement at minimum these **named** tests (adjust names only if
   pytest conventions require):

   1. `test_cli_init_refuses_when_index_paths_non_empty`
   2. `test_cli_erase_refuses_non_tty_without_yes`
   3. `test_cli_erase_succeeds_with_yes_flag`
   4. `test_embedding_model_precedence_cli_over_env_over_yaml_over_default`
   5. `test_embedding_device_precedence_cli_over_env_over_yaml_over_default`
   6. `test_yaml_config_ignores_legacy_filename_reads_new_filename`
   7. `test_index_dir_defaults_to_dot_java_codebase_rag_under_project_root`
   8. `test_index_dir_precedence_cli_over_env_over_yaml_over_default`
   9. `test_kuzu_path_derived_as_index_dir_code_graph_kuzu`
   10. `test_cli_lifecycle_round_trip_init_increment_meta_erase` (assert `increment`
       stderr warning)
   11. `test_help_output_includes_three_group_labels` (**structure only** — not exact
       line breaks / §3.1 mock layout)
   12. `test_java_codebase_rag_cli_module_importable`
   13. `test_refresh_hidden_alias_deprecates_on_stderr`
   14. `test_increment_emits_kuzu_stale_warning_block` (include **no-op** run)
   15. `test_meta_reports_embedding_setting_source`
   16. `test_legacy_env_var_set_emits_stderr_hint` (optional if covered elsewhere)
   17. `test_init_after_erase_succeeds`

8. **Extra integration test** (plan): after `init`, touch a Java file under a temp
   tree, run `increment`, assert Lance-visible change (cocoindex catch-up).

## Tests

Run from repo root:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: **all pass**; skips only where tests document env gating.

If you changed indexer / cocoindex paths, also run locally:

```bash
JAVA_CODEBASE_RAG_RUN_HEAVY=1 .venv/bin/python -m pytest tests -v
```

Report **pass + skip counts** in the PR description.

## Sentinel checks

Run from repo root; investigate any unexpected hits:

```bash
rg '\buser_rag\b' --glob '*.py' .
rg '^from user_rag|^import user_rag' --glob '*.py' .
rg 'LANCEDB_MCP_ALLOW_REFRESH' --glob '*.py' .
rg 'LANCEDB_MCP_GRAPH_ENABLED' --glob '*.py' .
rg 'LANCEDB_MCP_MICROSERVICE_ROOTS' --glob '*.py' .
```

Note: `LANCEDB_URI` / `KUZU_DB_PATH` / `COCOINDEX_DB` should **not** be read for
**configuration** after this PR — comments or propose docs may still mention them;
production code paths must use the consolidated model.

## Manual evidence

1. `java-codebase-rag --help` shows three **group labels** (lifecycle /
   introspection / analysis).
2. `java-codebase-rag refresh …` prints deprecation to **stderr** and behaves like
   `reprocess`.
3. `java-codebase-rag increment …` prints **full** Kuzu warning block (stderr),
   including on no-op.

## Definition of Done

- [ ] Tracking GitHub issue opened; URL constant in `java_codebase_rag/cli.py`
      matches it.
- [ ] PR title: `feat: cli lifecycle, env consolidation, java_codebase_rag package`
- [ ] Branch: `feat/cli-scenarios`
- [ ] `ruff` + `pytest` green; heavy run if indexer touched
- [ ] Plan tracking: PR-CLI-2 marked done when merged
````

---

## PR-CLI-3 — Docs, rules, migration sweep

**Branch:** `chore/cli-scenarios-docs` off `master` (after PR-CLI-2 merged).  
**Base:** `master`.  
**Plan section:** `plans/PLAN-CLI-SCENARIOS.md` § PR-CLI-3; propose §6 (doc list +
acceptance grep + agent rules audit).  
**Estimated diff size:** ~15–25 files, mostly markdown.

**Attach (`@-files`):**

- `@plans/PLAN-CLI-SCENARIOS.md` (PR-CLI-3 section)
- `@propose/CLI-SCENARIOS-PROPOSE.md` (§6)
- `@README.md`
- `@AGENTS.md`
- `@CODEBASE_REQUIREMENTS.md`
- `@docs/JAVA-CODEBASE-RAG-CLI.md`
- `@mcp.json.example`
- `@.cursor/rules/project-overview.mdc`
- `@.cursor/rules/agent-workflow.mdc`
- `@.cursor/rules/breaking-changes.mdc`
- `@.cursor/rules/python-venv-only.mdc`

**Prompt:**

````
You are implementing PR-CLI-3 from `plans/PLAN-CLI-SCENARIOS.md` and
`propose/CLI-SCENARIOS-PROPOSE.md` §6.

## Scope

1. Update **all operator-facing docs** listed in propose §6: new subcommands
   (`init` / `increment` / `reprocess` / `erase`), **5 env vars**, `.java-codebase-rag.yml`,
   default index dir **`.java-codebase-rag/`**, layered ignore path
   **`.java-codebase-rag/ignore`**, package **`java_codebase_rag`**, `python -m java_codebase_rag.cli`,
   `reprocess` vs deprecated hidden `refresh`, test gate **`JAVA_CODEBASE_RAG_RUN_HEAVY`**.
2. Add **Migration from legacy names** sections (`mv` for `lancedb_data`, `.lancedb-mcp.yml`,
   `.lancedb-mcp/ignore`, env mapping) per propose.
3. **`mcp.json.example`:** **comment / example prose only** — env **keys** are already
   correct from PR-CLI-2; do not fight that PR’s structure.
4. **Agent rules audit:** update **every** `.cursor/rules/*.mdc` + **`AGENTS.md`**
   so commands and env vars match the post-rename surface. Stale `LANCEDB_MCP_*` /
   `refresh` / `lancedb_data` / `user_rag` in rules must be **zero** except explicit
   migration text.
5. **`docs/paper/paper.tex`:** update + rebuild **`docs/paper/paper.pdf`**.
6. **Touch** propose files listed in §6 (one-line notes where specified).

## Out of scope (do NOT touch)

- **Production Python** (`.py` outside `docs/` paper build scripts if any — prefer no
  `.py` changes). If you find a doc bug caused by code, file a follow-up — do not
  expand this PR into code fixes.
- **`tests/bank-chat-system/`** Java fixture.
- Changing **`ontology_version`** or graph schema docs beyond naming/path updates.

## Deliverables

1. README + operator docs + CODEBASE_REQUIREMENTS + AGENTS + agent rules aligned
   with PR-CLI-2 surface.
2. Migration sections with explicit `mv` / env table.
3. `paper.pdf` rebuilt from `paper.tex`.
4. Acceptance grep run (below) — reviewer sign-off on intentional vs stray hits.

## Tests

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

Expected: same pass/skip profile as **master** after PR-CLI-2 (docs-only PR should
not change test outcomes). If anything fails, **stop** — likely an accidental `.py`
edit.

## Sentinel checks

Run **acceptance grep** from propose §6 (paths may vary slightly — use propose
wording). Example:

```bash
grep -rEi "refresh|lancedb-mcp|lancedb_data|LANCEDB_URI|LANCEDB_MCP|KUZU_DB_PATH|COCOINDEX_DB|user_rag" \
  -- README.md AGENTS.md CODEBASE_REQUIREMENTS.md docs/ propose/ mcp.json.example .gitignore .cursor/rules/
```

**Expected intentional hits** only: deprecation notes, migration quotes, one-line
related-propose references — per propose §6. **Nothing else** (especially under
`.cursor/rules/`).

**Out of scope for this grep:** internal `*.py` filenames like `search_lancedb.py`
(propose §6 codebase note).

## Manual evidence

- Spot-check: every **copy-paste** command in touched `docs/` and `README.md` uses
  the new CLI / env / module path.
- Confirm `JAVA_CODEBASE_RAG_RUN_HEAVY` appears in agent instructions where the old
  test gate name was documented.

## Definition of Done

- [ ] PR title: `chore: docs and rules for CLI scenarios migration`
- [ ] Branch: `chore/cli-scenarios-docs`
- [ ] Acceptance grep + rules audit complete per propose §6
- [ ] Plan tracking: PR-CLI-3 marked done when merged
````

---

## Final checklist (author)

- [ ] All three prompts exist in landing order (CLI-1 → CLI-2 → CLI-3).
- [ ] Each prompt has scope, out-of-scope, deliverables, tests, sentinels, DoD.
- [ ] PR-CLI-2 lists all modules from the plan sweep including `mcp_v2.py` and
      `path_filtering.py`.
- [ ] Venv commands use **`.venv/bin/...`** per repo rules.
