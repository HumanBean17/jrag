# In-flight proposes: lock order and merge sequence

**Status**: living document
**Last updated**: 2026-05-16

This document records the dependency order for proposes and code PRs that are currently in flight against `master`. It supplements (does not replace) each propose's `§6 — Migration plan` section.

## Why this exists

When two or more proposes touch overlapping subsystems, the order they lock and the order their code PRs merge matters. Encoding that order in one place — instead of scattering it across propose decisions — prevents drift between "what propose A claims about propose B" and "what propose B actually says."

## Current in-flight set (as of 2026-05-16)

1. **SCHEMA-V2** — `propose/SCHEMA-V2-PROPOSE.md` (locked via #151)
   - 4 code PRs: PR-A (`EDGE_SCHEMA` + ontology v14 bump), PR-B (`HTTP_CALLS` flip + downstream API), PR-C (`Producer` node + `ASYNC_CALLS` flip + GraphMeta + MCP parity), PR-D (hints v3).
2. **HINTS-V3** — `propose/HINTS-V3-PROPOSE.md` (this propose, draft)
   - 1 code PR, which is the same PR-D enumerated in SCHEMA-V2.

No other proposes are in flight.

## Lock and merge order

### Phase 1 — propose locks

```
SCHEMA-V2-PROPOSE.md   [LOCKED via #151]
        ↓ (decision 30: PR-D blocked until HINTS-V3 propose exists as draft PR)
HINTS-V3-PROPOSE.md    [draft PR]
```

SCHEMA-V2 propose is already merged. HINTS-V3 propose opens as a draft PR (this propose). Both must reach `Status: locked` before any code PR-A merges. SCHEMA-V2 Decision 30 makes the gating explicit.

### Phase 2 — plan + cursor-prompt artefacts

```
plans/PLAN-SCHEMA-V2.md
plans/CURSOR-PROMPTS-SCHEMA-V2.md
plans/PLAN-HINTS-V3.md
plans/CURSOR-PROMPTS-HINTS-V3.md
```

SCHEMA-V2 Decision 29 makes `PLAN-SCHEMA-V2.md` + `CURSOR-PROMPTS-SCHEMA-V2.md` a merge gate for PR-A. By analogy, `PLAN-HINTS-V3.md` + `CURSOR-PROMPTS-HINTS-V3.md` are a merge gate for PR-D.

Plans + prompts can be drafted in parallel; they don't have to be merged before each other. They do all have to be merged before their respective code PRs.

### Phase 3 — code PRs (merge order)

```
PR-A   feat(schema): add EDGE_SCHEMA + generate docs/EDGE-NAVIGATION.md + bump ontology to v14
        ↓
PR-B   feat(schema): HTTP_CALLS originates from Client, not Symbol  (+ downstream API + HTTP docs)
        ↓
PR-C   feat(schema): introduce Producer node and route ASYNC_CALLS through it
        ↓ (HINTS-V3 propose must be LOCKED, not just draft, by this point)
PR-D   feat(hints): kind- and direction-aware empty-result hints driven by EDGE_SCHEMA
```

PR-A is strictly first because every subsequent PR depends on `EDGE_SCHEMA` + the ontology version bump. PR-B and PR-C are sequential because PR-C's MCP-parity / GraphMeta / type-level-rollup additions are easier to review on top of a clean PR-B. PR-D is last because its templates substitute post-flip `src_kind` / `dst_kind` from the final `EDGE_SCHEMA`.

No PR in this set is parallelizable. Each depends on its predecessor's `master` shape.

## Re-index moments

`ONTOLOGY_VERSION` 13 → 14 lands in PR-A. **One** re-index is required across the whole sequence; PRs B/C/D do not bump the version again. The README + `docs/AGENT-GUIDE.md` "Re-index required" sections are updated in PR-A; PR-B/C/D may amend wording but do not re-trigger.

## What this document does NOT cover

- Per-PR file-path / function-signature detail — that's the per-PR plan (`plans/PLAN-*.md`).
- Cursor task prompts — `plans/CURSOR-PROMPTS-*.md`.
- Out-of-scope proposes (TIER2-INCREMENTAL-REBUILD, RANKING-MICROSERVICE, ENHANCED-ROLE-RECOGNITION, INDEX-AUTO-MODE) — those are not in the merge sequence right now. When one becomes in-flight, this doc is updated.
- Review cycles inside a single propose or PR — see each PR's review threads.

## Maintenance

This file is updated whenever:
- A new propose enters draft.
- A propose locks or its code PRs merge.
- The dependency graph changes.

Stale rows are deleted when their code PRs land in `master`. After PR-D merges, this whole document collapses to "no proposes in flight" until the next one starts.
