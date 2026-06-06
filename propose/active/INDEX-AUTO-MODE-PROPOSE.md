# Proposal: Smart `increment` / `reprocess` Mode Selection

Status: **active — ready for planning**.
Companion proposal: [`propose/active/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`](TIER2-INCREMENTAL-REBUILD-PROPOSE.md)
(Kuzu incremental rebuild implementation).

## Goal

Extend `java-codebase-rag increment` so it can choose incremental vs full
rebuild automatically for both Lance and Kuzu, while staying safe for
rename/delete/move and indexing-semantics changes.

## Problem

Current behavior:

- `java-codebase-rag increment` updates Lance vectors incrementally
  (via CocoIndex) but prints a warning that the Kuzu graph is stale.
- `java-codebase-rag reprocess` always does a full rebuild of both
  Lance and Kuzu.

The `increment` command is correct for Lance but incomplete for Kuzu.
The Kuzu incremental path is defined in
[`TIER2-INCREMENTAL-REBUILD-PROPOSE.md`](TIER2-INCREMENTAL-REBUILD-PROPOSE.md).
This proposal defines the decision engine that both commands use to
determine when incremental is safe.

## Proposed API Changes

### CLI

No new commands. Existing commands gain automatic mode selection:

- `java-codebase-rag increment` — incremental Lance + Kuzu when safe,
  full Kuzu fallback when not. Lance is always incremental via CocoIndex.
- `java-codebase-rag reprocess` — full rebuild of both Lance + Kuzu
  (unchanged).
- `java-codebase-rag reprocess --graph-only` — full Kuzu rebuild only.
- `java-codebase-rag reprocess --vectors-only` — full Lance rebuild only.

Index building is **CLI-only**. The MCP server (`server.py`) is a
read-only query interface and does not expose index-building tools.

## Decision Engine (`mode=auto`)

Returns two independent mode choices — Lance and Kuzu may use different
modes:

```python
@dataclass
class RefreshDecision:
    lance_mode: Literal["incremental", "full"]
    kuzu_mode: Literal["incremental", "full"]
    reasons: list[str]
    detected_changes: ChangeSet
```

### Choose `full` for Kuzu when any of the following is true

- At least one file is deleted.
- At least one file is renamed or moved.
- `.java-codebase-rag.yml` or `.lancedb-mcp.yml` changes.
- Indexing pipeline/config files change (for example:
  `java_index_flow_lancedb.py`, `build_ast_graph.py`,
  `graph_enrich.py`, enrichment/chunking components).
- `@interface` definitions changed (meta-annotation fanout risk).
- `.deps.json` is missing, corrupt, or has wrong `ontology_version`.
- Change detection fails or is ambiguous.
- More than 50% of files are dirty (incremental would be slower).

### Choose `full` for Lance when any of the following is true

- Same config/indexing-pipeline triggers as Kuzu.
- `.java-codebase-rag.yml` changes.
- CocoIndex flow definition changes.

### Choose `incremental` when all are true

- Only in-place file content modifications/additions.
- No rename/delete/move events.
- No config/index/meta-annotation risk triggers.
- `.deps.json` exists and is current (Kuzu incremental prerequisite).

## Change Detection Strategy

1. Prefer git diff status:
   - `git diff --name-status <base>...HEAD`
   - optionally include working tree status (`git diff --name-status`
     and `git diff --name-status --cached`)
2. If git signal is unavailable, use `changed_paths` when supplied.
3. If still uncertain, fall back to `full`.

Represent results as:

- `added[]`, `modified[]`, `deleted[]`, `renamed[]`
- plus boolean risk flags for config/index/meta-annotation changes

## Execution Plan

### Full mode

- Lance: `cocoindex update ... --full-reprocess -f`
- Kuzu: `build_ast_graph.py --source-root ...` (full rebuild via
  `_drop_all`)

### Incremental mode

- Lance: `cocoindex update ... -f` (without `--full-reprocess`)
- Kuzu: `build_ast_graph.py --source-root ... --changed-paths ...`
  (incremental rebuild via TIER2 proposal)

The decision engine returns two independent mode choices — Lance and
Kuzu may incrementally update independently. For example, if
`.deps.json` is missing but no config changed, Lance could be
incremental while Kuzu falls back to full.

## CLI Output Enhancements

Emit the mode decision to stderr with `[graph]` / `[vectors]`
prefixes consistent with existing progress output (see
`CLI-PROGRESS-OUTPUT-PROPOSE.md`).

Decision transparency on stderr:

- Effective mode (incremental or full) for Lance and Kuzu independently.
- Decision reasons (why full was chosen, if applicable).
- Detected changes summary (added, modified, deleted, renamed).

## Safety Policy

- Default `auto`.
- On uncertainty, choose `full`.
- `mode=full` always respected.
- `mode=incremental` allowed, but return warnings when risk triggers
  exist.
- Never silently downgrade explicit `full` to incremental.
- Kuzu incremental failure at runtime → roll back, fall back to full
  Kuzu rebuild, log reason.

## Test Plan

1. `auto` + modified-only → incremental (both Lance + Kuzu).
2. `auto` + deleted file → full Kuzu, incremental Lance.
3. `auto` + renamed file → full Kuzu, incremental Lance.
4. `auto` + `.java-codebase-rag.yml` change → full (both).
5. `auto` + detection failure → full with reason.
6. explicit `mode=full` → full regardless of diffs.
7. explicit `mode=incremental` + risky changes → incremental + warning.
8. backward compatibility: `confirm=true` only call still succeeds.
9. `.deps.json` missing → full Kuzu, incremental Lance.
10. `.deps.json` stale `ontology_version` → full Kuzu, incremental Lance.

## Implementation Notes

- Keep mode-selection logic isolated in helper functions for testability:
  - `_detect_repo_changes(...)`
  - `_choose_refresh_mode(...)`
- Make logs/user-facing messages explicit about why full mode was
  selected.
- Preserve current subprocess environment and project-root behavior.
- The decision engine lives in a shared module (`refresh_decision.py`)
  usable by `cli.py` and `pipeline.py`.

## Rollout

1. Implement decision engine with isolated helpers + tests.
2. Integrate into `cli.py`'s `_cmd_increment` — remove
   `_emit_increment_kuzu_warning()`, dispatch to Kuzu incremental or
   full based on decision.
3. Update `README.md` and `docs/JAVA-CODEBASE-RAG-CLI.md`.
