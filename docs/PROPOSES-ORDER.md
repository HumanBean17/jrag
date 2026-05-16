# In-flight proposes: lock order and merge sequence

**Status**: living document
**Last updated**: 2026-05-16

This document records the dependency order for proposes and code PRs that are currently in flight against `master`. It supplements (does not replace) each propose's `§6 — Migration plan` section.

## Why this exists

When two or more proposes touch overlapping subsystems, the order they lock and the order their code PRs merge matters. Encoding that order in one place — instead of scattering it across propose decisions — prevents drift between "what propose A claims about propose B" and "what propose B actually says."

## Current in-flight set (as of 2026-05-16)

No proposes are in flight.

**SCHEMA-V2** and **HINTS-V3** are **completed** — artefacts under `propose/completed/` and `plans/completed/` (SCHEMA PR-A/B/C; HINTS-V3 PR-D [#160](https://github.com/HumanBean17/java-codebase-rag/pull/160)).

## Lock and merge order (archived)

### Phase 1 — propose artefacts

```
SCHEMA-V2-PROPOSE.md   [merged #151 — completed; propose/completed/]
        ↓
HINTS-V3-PROPOSE.md    [merged #154 — completed; propose/completed/; code #160]
```

**Decision 30 (SCHEMA-V2)**: `HINTS-V3-PROPOSE.md` must exist as a **merged draft propose** before SCHEMA-V2 **PR-A** implementation starts.

**HINTS-V3 lock**: `Status: locked` before SCHEMA-V2 **PR-D** code merges.

### Phase 2 — plan + cursor-prompt artefacts

```
plans/completed/PLAN-SCHEMA-V2.md
plans/completed/CURSOR-PROMPTS-SCHEMA-V2.md
plans/completed/PLAN-HINTS-V3.md
plans/completed/CURSOR-PROMPTS-HINTS-V3.md
```

### Phase 3 — code PRs (merge order) — **completed**

```
PR-A   feat(schema): EDGE_SCHEMA + docs/EDGE-NAVIGATION.md + ontology v14
        ↓
PR-B   feat(schema): HTTP_CALLS Client → Route (+ downstream API)
        ↓
PR-C   feat(schema): Producer node + ASYNC_CALLS flip (+ GraphMeta / MCP parity)
        ↓
PR-D   feat(hints): kind/direction-aware empty-result hints (EDGE_SCHEMA-driven) [#160]
```

## Re-index moments

`ONTOLOGY_VERSION` 13 → 14 landed in PR-A. **One** re-index across the SCHEMA-V2 sequence.

## What this document does NOT cover

- Per-PR deliverables — `plans/PLAN-*.md` (see `plans/completed/` for landed work)
- Cursor handoffs — `plans/CURSOR-PROMPTS-*.md`
- Out-of-scope proposes (TIER2-INCREMENTAL-REBUILD, RANKING-MICROSERVICE, etc.)

## Maintenance

Update this file when a propose enters draft, locks, or its code PRs land. After the next effort starts, add it to "Current in-flight set" and extend the archived sequence as needed.
