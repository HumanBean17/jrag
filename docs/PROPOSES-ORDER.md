# In-flight proposes: lock order and merge sequence

**Status**: living document
**Last updated**: 2026-05-16

This document records the dependency order for proposes and code PRs that are currently in flight against `master`. It supplements (does not replace) each propose's `§6 — Migration plan` section.

## Why this exists

When two or more proposes touch overlapping subsystems, the order they lock and the order their code PRs merge matters. Encoding that order in one place — instead of scattering it across propose decisions — prevents drift between "what propose A claims about propose B" and "what propose B actually says."

## Current in-flight set (as of 2026-05-16)

1. **SCHEMA-V2** — `propose/SCHEMA-V2-PROPOSE.md` (`Status: locked — implementing`; propose [#151](https://github.com/HumanBean17/java-codebase-rag/pull/151), plan [#155](https://github.com/HumanBean17/java-codebase-rag/pull/155))
   - 4 code PRs: PR-A (`EDGE_SCHEMA` + ontology v14 bump), PR-B (`HTTP_CALLS` flip + downstream API), PR-C (`Producer` node + `ASYNC_CALLS` flip + GraphMeta + MCP parity), PR-D (hints v3).
2. **HINTS-V3** — `propose/HINTS-V3-PROPOSE.md` (`Status: locked — implementing via SCHEMA-V2 PR-D`; propose [#154](https://github.com/HumanBean17/java-codebase-rag/pull/154), plan [#155](https://github.com/HumanBean17/java-codebase-rag/pull/155))
   - Implementation = SCHEMA-V2 PR-D (same PR).

No other proposes are in flight.

## Lock and merge order

### Phase 1 — propose artefacts

```
SCHEMA-V2-PROPOSE.md   [merged #151 — locked for code sequence]
        ↓
HINTS-V3-PROPOSE.md    [merged #154 — implementing in PR-D]
```

**Decision 30 (SCHEMA-V2)**: `HINTS-V3-PROPOSE.md` must exist as a **merged draft propose** before SCHEMA-V2 **PR-A** implementation starts. That unblocks the four-code-PR sequence; it does **not** require HINTS-V3 to be `Status: locked` before PR-A.

**HINTS-V3 lock**: `Status: locked` before SCHEMA-V2 **PR-D** code merges (satisfied while Phase 3 is in flight; see propose headers).

### Phase 2 — plan + cursor-prompt artefacts

```
plans/PLAN-SCHEMA-V2.md          [landed #155]
plans/CURSOR-PROMPTS-SCHEMA-V2.md
plans/PLAN-HINTS-V3.md
plans/CURSOR-PROMPTS-HINTS-V3.md
```

SCHEMA-V2 Decision 29: `PLAN-SCHEMA-V2.md` + `CURSOR-PROMPTS-SCHEMA-V2.md` are merge gates for **PR-A** (satisfied when [#155](https://github.com/HumanBean17/java-codebase-rag/pull/155) is on `master`).

By analogy: `PLAN-HINTS-V3.md` + `CURSOR-PROMPTS-HINTS-V3.md` are merge gates for **PR-D** (same PR).

Plans and prompts may be drafted in parallel with each other; each pair must land before its code PR. **Code PRs (Phase 3) are not started** until Phase 2 is on `master`.

### Phase 3 — code PRs (merge order) — **implementing**

```
PR-A   feat(schema): EDGE_SCHEMA + docs/EDGE-NAVIGATION.md + ontology v14
        ↓  (requires HINTS-V3 propose merged as draft per Decision 30)
PR-B   feat(schema): HTTP_CALLS Client → Route (+ downstream API)
        ↓
PR-C   feat(schema): Producer node + ASYNC_CALLS flip (+ GraphMeta / MCP parity)
        ↓  (requires HINTS-V3 propose Status: locked)
PR-D   feat(hints): kind/direction-aware empty-result hints (EDGE_SCHEMA-driven)
```

PR-A needs `EDGE_SCHEMA` infrastructure. PR-B and PR-C are sequential for review surface. PR-D consumes post-flip `src`/`dst` and must not merge until HINTS-V3 is **locked**.

No PR in this set is parallelizable.

## Re-index moments

`ONTOLOGY_VERSION` 13 → 14 lands in PR-A. **One** re-index across the sequence. README + `docs/AGENT-GUIDE.md` updated in PR-A.

## What this document does NOT cover

- Per-PR deliverables — `plans/PLAN-*.md`
- Cursor handoffs — `plans/CURSOR-PROMPTS-*.md`
- Out-of-scope proposes (TIER2-INCREMENTAL-REBUILD, RANKING-MICROSERVICE, etc.)
- Intra-PR review threads

## Maintenance

Update this file when a propose enters draft, locks, or its code PRs land. After PR-D merges, collapse to "no proposes in flight" until the next effort starts.
