> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# INCREMENTAL-GRAPH-PROPOSE

## Status
Proposal — not yet implemented.

## Problem Statement
The graph index (Kuzu) always requires a full 6-pass rebuild, even when only a few files have changed. For large codebases this is slow and resource-intensive. The `increment` CLI command already updates vectors incrementally via CocoIndex but explicitly skips the graph, warning users that `find`, `neighbors`, and `describe` may return stale results. Users must choose between fast-but-stale queries or a slow full `reprocess` — a critical gap for iterative development workflows.

Concrete failure modes:
- User runs `increment`, gets vector updates, but graph queries return outdated node/edge data
- User runs `reprocess` for correctness but waits for the entire codebase to be re-parsed
- Cross-service edges (HTTP_CALLS, ASYNC_CALLS) become stale when only one service changes

## Proposed Solution
Add incremental graph rebuild mode with safe fallback:

**Change detection.** Track file content hashes in `.graph_hashes.json`. CocoIndex tracks file changes internally via LMDB but exposes no API to query which files changed. A separate hash store is the only practical option for the graph pipeline.

**Selective deletion.** Symbol nodes already carry `filename STRING` — use that for file-scoped lookups. Add `source_file STRING` only to edge tables, where file attribution is currently missing. Deletion collects node IDs by `filename` first (primary-key lookup), then batch-deletes edges by `source_file` using collected IDs to avoid full table scans. Phantom nodes (where `filename=""`) are never deleted by file-scoped logic — they represent external types outside the codebase.

**Two-phase rebuild.** The 6-pass pipeline splits naturally:
- **Scoped phase (passes 1–4):** Tree-sitter parsing and structural edge emission run only on changed files + single-hop dependents. Pass 3 loads existing types from Kuzu into `tables.types` before resolving calls in changed files, so cross-file type resolution works without reprocessing all files.
- **Global phase (passes 5–6):** Client/producer extraction (pass 5) and cross-service matching (pass 6) iterate all members and all routes respectively — both are cheap, in-memory operations. These always run globally regardless of which files changed.

**Dependent expansion.** After deleting changed files' nodes/edges, query the graph for files whose nodes had edges pointing into deleted nodes. Reprocess those dependents too. Cap expansion at a configurable threshold (default: 50 files) — if exceeded, fall back to full `reprocess` with a warning. This prevents pathological cases like changing a widely-used utility class from expanding to 500+ dependents.

**Cross-file edge semantics.** Edges like EXTENDS, IMPLEMENTS, OVERRIDES, and HTTP_CALLS span two files. `source_file` on an edge records only the origin side (e.g., for CALLS, the calling method's file). When file Y changes, edges from file X pointing into Y are not found by `source_file` alone — the single-hop dependent expansion covers this by reprocessing file X as well.

**Crash safety.** The current full rebuild is crash-safe because the graph is built in-memory first, then written atomically. Incremental mode cannot guarantee atomicity across delete+rebuild. Mitigation: if the process crashes mid-increment, the next `increment` detects a stale/incomplete state (via a marker file) and falls back to full `reprocess`. UnresolvedCallSite nodes and UNRESOLVED_AT edges are treated like any other node/edge — deleted when their source file is reprocessed, re-created by pass 3.

**CLI integration.** `increment` now updates both vectors and graph. Add `--vectors-only` flag to skip graph updates. Full `reprocess` remains available as a safety net and serves as the one-time migration path.

## Scope
- schema: add `source_file STRING` to edge tables only (Symbol nodes already have `filename`)
- incremental orchestrator: file hash tracking, scoped pass 1–4, global pass 5–6, dependent expansion
- CLI: `increment` command updated; `--vectors-only` flag added
- breaking schema change: existing Kuzu databases require one-time full `init` or `reprocess`

## Schema / Ontology / Re-index impact
- Ontology bump: required (additive `source_file` property on edge tables)
- Re-index required: yes, one-time — existing databases lack `source_file` on edges
- Config/tool surface changes: `--vectors-only` flag on `increment`; new `.graph_hashes.json` data file

## Tests / Validation
- unit tests for file hash tracker: detect added/changed/removed files, hash computation
- unit tests for scoped rebuild: only changed-file nodes/edges deleted and recreated
- integration: single-file change → `increment` → only that file's nodes updated, dependents refreshed, unrelated untouched
- integration: new file → `increment` → all new nodes/edges appear
- integration: deleted file → `increment` → orphaned nodes/edges cleaned up
- integration: phantom nodes survive file-scoped deletion
- integration: high-fan-in change triggers cap → falls back to full reprocess
- crash recovery: interrupted increment → next run detects and falls back to full reprocess
- benchmark: `increment` vs `reprocess` time on single-file change

## Open Questions ([TBD])
1. Hash storage format — Resolved: JSON file (`.graph_hashes.json`) in index directory.
2. Dependent reprocessing depth — Resolved: single hop only, with expansion cap (default 50 files).
3. Edge `source_file` semantics — Resolved: origin-side file only; dependent expansion covers target-side changes.
4. Fallback on corrupted state — Resolved: fall back to full `reprocess` with warning.
5. Should pass 5–6 be skipped when no route/client/producer-related files changed? — Recommended: no — always run globally; they're cheap and ensure consistency.

## Out of scope
- automatic/manual trigger improvements (user must still run `increment` explicitly)
- watch mode or file-system watcher for automatic increment
- multi-hop dependent propagation beyond one hop
- full `reprocess` path optimization
- streaming/progress reporting for long increments
- MCP server tool or query API changes
- sharing CocoIndex's change tracking (internal LMDB, no public API)

## Sequencing / Follow-ups
Single implementation PR.

Follow-up PRs (not in scope):
- performance benchmarking and optimization for large codebases
- watch mode for automatic increment triggers

## PR body (proposal-only) template
## What
Adds `propose/active/INCREMENTAL-GRAPH-PROPOSE.md` describing incremental graph rebuild strategy.

## Why now
Full graph rebuild dominates edit-query iteration latency; vectors already support increment but graph does not.

## Highlights
- Delete-and-rebuild strategy scoped to changed files
- `source_file` on edge tables only (Symbol nodes already have `filename`)
- Two-phase rebuild: scoped pass 1–4, global pass 5–6
- Single-hop dependent expansion with fan-out cap
- Crash recovery via marker file and full-reprocess fallback

## Tests
Proposal-only; baseline unchanged.

## Out of scope
- Implementation and follow-up optimization/watch-mode PRs.
