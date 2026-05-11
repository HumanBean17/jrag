# Proposal: Smart `refresh_code_index` Mode Selection

> **Note (2026):** the operator CLI’s full rebuild verb is now `java-codebase-rag reprocess` (see `propose/CLI-SCENARIOS-PROPOSE.md`); this MCP-side proposal still refers to `refresh_code_index` by name.

## Goal

Extend `refresh_code_index` so it can choose incremental vs full rebuild automatically, while staying safe for rename/delete/move and indexing-semantics changes.

## Problem

Current behavior always runs:

- `cocoindex update ... --full-reprocess -f`
- followed by full Kuzu rebuild

This is correct but expensive for small edit-only iterations. At the same time, a naive incremental flow can leave stale entries after file deletes/renames or when metadata semantics change.

## Proposed API Changes

Add optional inputs to `refresh_code_index`:

- `confirm: bool = false` (existing)
- `mode: "auto" | "incremental" | "full" = "auto"`
- `changed_paths: list[str] | null = null`
- `git_ref_base: str = "HEAD"`
- `reason: str | null = null`

Backward compatibility:

- Calls passing only `confirm=true` should still work.
- Default mode should become `auto` (safe-by-default decisioning).

## Decision Engine (`mode=auto`)

### Choose `full` when any of the following is true

- At least one file is deleted.
- At least one file is renamed or moved.
- `.lancedb-mcp.yml` changes.
- Indexing pipeline/config files change (for example: `java_index_flow_lancedb.py`, enrichment/chunking components).
- `@interface` definitions changed (meta-annotation fanout risk).
- Change detection fails or is ambiguous.

### Choose `incremental` when all are true

- Only in-place file content modifications/additions.
- No rename/delete/move events.
- No index/config/meta-annotation risk triggers.

## Change Detection Strategy

1. Prefer git diff status:
   - `git diff --name-status <base>...HEAD`
   - optionally include working tree status (`git diff --name-status` and `git diff --name-status --cached`)
2. If git signal is unavailable, use `changed_paths` when supplied.
3. If still uncertain, fall back to `full`.

Represent results as:

- `added[]`, `modified[]`, `deleted[]`, `renamed[]`
- plus boolean risk flags for config/index/meta-annotation changes

## Execution Plan

### Full mode

- Keep current behavior:
  - `cocoindex update ... --full-reprocess -f`
  - run `build_ast_graph.py --source-root ...`

### Incremental mode

- Run `cocoindex update ... -f` (without `--full-reprocess`)
- Graph handling in two phases:
  - Phase A: still run full graph rebuild for correctness.
  - Phase B (future): incremental graph updates scoped to changed files.

## Response Payload Enhancements

Add decision transparency fields:

- `effective_mode: "incremental" | "full"`
- `decision_reasons: list[str]`
- `detected_changes: { added, modified, deleted, renamed }`
- optional `warnings: list[str]` (for forced incremental under risky conditions)

Keep existing stdout/stderr/exit_code fields intact.

## Safety Policy

- Default `auto`.
- On uncertainty, choose `full`.
- `mode=full` always respected.
- `mode=incremental` allowed, but return warnings when risk triggers exist.
- Never silently downgrade explicit `full` to incremental.

## Test Plan

Add/extend tests in `tests/test_mcp_tools.py`:

1. `auto` + modified-only -> incremental chosen.
2. `auto` + deleted file -> full chosen.
3. `auto` + renamed file -> full chosen.
4. `auto` + `.lancedb-mcp.yml` change -> full chosen.
5. `auto` + detection failure -> full chosen with reason.
6. explicit `mode=full` -> full regardless of diffs.
7. explicit `mode=incremental` + risky changes -> incremental + warning.
8. backward compatibility: `confirm=true` only call still succeeds.

## Implementation Notes

- Keep mode-selection logic isolated in helper functions for testability:
  - `_detect_repo_changes(...)`
  - `_choose_refresh_mode(...)`
- Make logs/user-facing messages explicit about why full mode was selected.
- Preserve current subprocess environment and project-root behavior.

## Rollout

1. Implement API and auto decisioning with Phase A graph behavior.
2. Add test coverage and docs update in `README.md`.
3. Later: implement true incremental Kuzu graph updates (Phase B).
