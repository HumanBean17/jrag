# Plan: AGENT-GUIDE surgical patches (failure-mode inoculation)

Status: **completed** (landed). This plan implemented
[`propose/completed/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`](../../propose/completed/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md).

Depends on: **none** (documentation only; no code or schema).

Companion prompts:
[`plans/completed/AGENT-PROMPTS-AGENT-GUIDE-SURGICAL-PATCHES.md`](./AGENT-PROMPTS-AGENT-GUIDE-SURGICAL-PATCHES.md)
(Cursor-ready per-PR contracts; structural reference
`plans/completed/AGENT-PROMPTS-TIER1B.md`).

## Goal

- Close three recurring agent failure modes via **minimal insertions** in
  `docs/AGENT-GUIDE.md`: empty results mistaken for proof; MCP treated as
  exhaustive (reflection, unindexed code, build files); over-trust of a
  stale graph after `increment`.
- Keep the drop-in block between `<!-- BEGIN java-codebase-rag MCP guide -->`
  and `<!-- END java-codebase-rag MCP guide -->` **self-contained** and
  **shape-stable** (no new top-level sections, no reordered headings, frozen
  cardinal phrases per propose).
- Land in **two PRs**: propose lock on main, then guide edits. After rollout,
  move this plan and the propose into `completed/` per repo convention.

## Principles (do not relitigate in review)

- **Insertion only** inside the marker block; no section rename/reorder; no
  new counted surface in prose (“four MCP navigation tools”, “nine edge types”,
  ontology **11** strings stay verbatim).
- **Line budget:** total net addition **≤ 60 lines** across Patches A–C; if
  text grows past budget, defer to the future `java-codebase-explore` skill,
  not the operating manual.
- **Tone:** terse, table-heavy, second-person imperative — match existing
  `docs/AGENT-GUIDE.md`.
- **No code, no ontology bump, no README requirement** for this effort (propose
  explicitly defers README lockstep; maintenance notes already cover MCP/ontology
  parity when behaviour changes).

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-AGP-1 | Land / lock the propose on the default branch (status text as agreed); **no** `AGENT-GUIDE.md` edits | No | `propose/completed/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` only (plus optional Status line tweak) | None (doc-only) | — |
| PR-AGP-2 | Apply Patches A, B, C verbatim intent from propose §3 / Appendix A | No | `docs/AGENT-GUIDE.md` only | None; manual acceptance checklist | PR-AGP-1 merged preferred so “locked patches” precede apply PR |

Landing order: **PR-AGP-1 → PR-AGP-2**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Patch count | Three only (What MCP is NOT; staleness + recovery rows; neighbors confidence). |
| Marker scope | All three patches **inside** `<!-- BEGIN java-codebase-rag MCP guide -->` … `<!-- END java-codebase-rag MCP guide -->`. |
| Patch A placement | New `### What this MCP is NOT` **after** the “Do NOT use this MCP when…” paragraph, **before** `**Workflow (GPS model):**` (in current tree: between lines ~36 and ~38). |
| Patch B placement | Two new **table rows** at the **end** of the *Recovery playbook* table; then the existing `After two failed attempts…` line **unchanged**; then the **Staleness rule** paragraph (never between the last table row and that sentence). |
| Patch C placement | New bullet under **`#### `neighbors``**, immediately **after** the **Batching:** line (in current tree: after line ~177). |
| Exploration skill | No mission catalogues, no exploration sequences, no anti-capabilities essay — those belong in `java-codebase-explore`; this plan ships the manual minimum only. |

---

# PR-AGP-1 — Propose merge (lock patches)

## File-by-file changes

### 1. `propose/completed/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`

- Ensure the file is on the default branch with content matching the locked
  three-patch design (§3 + Appendix A).
- Optionally move **Status** from `draft` to agreed wording if the team
  requires it before apply PR; do not change patch text without a new propose
  revision.

## Tests for PR-AGP-1

- None (documentation-only).

## Definition of done (PR-AGP-1)

- Propose is merged; reviewers can cite §3 / Appendix A as the authoritative
  insertion spec for PR-AGP-2.
- No edits under `docs/AGENT-GUIDE.md` in this PR.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Open PR from `chore/` or `plan/` branch; scope = propose only | `propose/completed/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` | CI green; no AGENT-GUIDE diff |
| 2 | PR description references this plan + propose | PR body | Linked |
| 3 | Merge to default branch | — | PR-AGP-1 closed |

---

# PR-AGP-2 — Apply patches to `docs/AGENT-GUIDE.md`

## File-by-file changes

### 1. `docs/AGENT-GUIDE.md`

**Patch A — `### What this MCP is NOT`**

- Insert the subsection from propose §3 / Appendix A between the paragraph
  ending “Prefer the smallest call that answers the question.” and the line
  `**Workflow (GPS model):**`.

**Patch B — Recovery playbook**

- Append two markdown table rows to the *Recovery playbook* table (after the
  “Need ontology / rebuild / PR analysis” row), matching propose wording
  (disagreement vs open file; empty `search` with visible string in editor).
- Leave `After two failed attempts on the same intent, stop and report tool name, args, and response.` **unchanged**.
- Immediately **after** that line, append the **Staleness rule** paragraph
  (Lance vs Kuzu after `increment`, `meta` comparison).

**Patch C — `neighbors` confidence**

- After the **Batching:** bullet under `#### neighbors`, add the **Confidence:**
  bullet block from propose (cross-service `HTTP_CALLS` / `ASYNC_CALLS` attrs;
  intra-service edge list; pointer to *What this MCP is NOT* for reflection).

**Global invariants (manual verify)**

- Marker comments unchanged and still wrap the same block.
- Load-bearing cardinals unchanged (spot-check stable substrings): `Tool reference — four tools`, `nine edge types`, `Ontology glossary (version 11)` (see manual acceptance list).
- Net line growth ≤ **60** lines.

## Tests for PR-AGP-2

- None automated. **Manual acceptance** (copy into PR description):

  1. `rg -n "What this MCP is NOT" docs/AGENT-GUIDE.md` — one hit inside markers.
  2. `rg -n "After two failed attempts|Staleness rule" docs/AGENT-GUIDE.md` — the line matching **Staleness rule** must appear **below** the line matching **After two failed attempts** (staleness paragraph follows that sentence, not the table).
  3. `rg -n '^- \*\*Confidence:\*\*' docs/AGENT-GUIDE.md` — present under `neighbors` (same pattern as `AGENT-PROMPTS`).
  4. `rg -n "<!-- BEGIN java-codebase-rag MCP guide -->|<!-- END java-codebase-rag MCP guide -->" docs/AGENT-GUIDE.md` — both present, unchanged strings.
  5. Load-bearing cardinals (stable substrings; adjust only if copy moves):
     - `rg -n "Tool reference — four tools" docs/AGENT-GUIDE.md`
     - `rg -n "nine edge types" docs/AGENT-GUIDE.md`
     - `rg -n "Ontology glossary (version 11)" docs/AGENT-GUIDE.md`
  6. `git diff master --stat docs/AGENT-GUIDE.md` — confirm ~≤60 lines added (approximate; reviewer judgment).

## Definition of done (PR-AGP-2)

- Patches A, B, C applied per propose §3 (Patch B **canonical block order**) and
  Appendix A in **implementation order A → B → C**; marker block remains the
  drop-in copy region; no edits to *Maintenance notes* footer, slash aliases, or
  unrelated sections outside the intentional insertions.
- PR references this plan + merged propose.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Apply Patch A | `docs/AGENT-GUIDE.md` | Subsection visible in correct gap |
| 2 | Apply Patch B (rows, then `After two failed…`, then staleness) | `docs/AGENT-GUIDE.md` | Order matches propose §3 Patch B canonical block |
| 3 | Apply Patch C | `docs/AGENT-GUIDE.md` | Confidence bullet follows Batching |
| 4 | Run manual grep checklist | — | All checks pass |
| 5 | Merge PR | — | PR-AGP-2 closed |

---

## Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Patch text drifts into strategy / exploration content | Medium | Enforce ≤60 lines; reviewer rejects overflow; cite propose Appendix A blocks verbatim (order **A → B → C**; Patch B staleness **after** `After two failed attempts…`). |
| 2 | Marker block accidentally split or headings reordered | Medium | Grep for exact BEGIN/END strings before merge; diff review only `docs/AGENT-GUIDE.md`. |
| 3 | Cardinal-count strings edited by accident | Low | Manual acceptance greps for stable substrings (`Tool reference — four tools`, `nine edge types`, `Ontology glossary (version 11)`). |
| 4 | PR-AGP-2 merges before propose lock | Low | Prefer PR-AGP-1 first; if reversed, ensure propose text on main matches applied patches. |

## Out of scope

- README or `AGENTS.md` / `.cursor` rule changes (unless a follow-up explicitly
  requests README sync).
- New MCP tools, CLI behaviour, Kuzu/Lance schema, or `ontology_version` bump.
- `java-codebase-explore` skill content, mission catalogues, exploration
  stop-condition guidance, or restructuring `AGENT-GUIDE.md`.
- Automated tests for markdown (none required by propose).
- Translation / localization.

## Whole-plan done definition

1. PR-AGP-1 and PR-AGP-2 merged; `docs/AGENT-GUIDE.md` contains Patches A–C inside
   the marker block with acceptance checks satisfied.
2. `propose/completed/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` on the default branch
   per repo convention.
3. ~~This plan moved to `plans/completed/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md`.~~ **Done.**

## Tracking

- `PR-AGP-1`: **merged**
- `PR-AGP-2`: **merged**
- [`plans/completed/AGENT-PROMPTS-AGENT-GUIDE-SURGICAL-PATCHES.md`](./AGENT-PROMPTS-AGENT-GUIDE-SURGICAL-PATCHES.md): **completed** (reference prompts)
