---
name: plan
description: Write high-quality implementation plans for this repository using the merged plan format. Use when creating, updating, or reviewing files under `plans/`, splitting work into multiple PRs, or generating per-PR execution contracts.
disable-model-invocation: true
---

# Plan Skill

Author implementation plans that match this repo's merged style (`PLAN-*`) and stay execution-ready for agent handoff.

## When to use

Use this skill when:
- the user asks for a new plan file in `plans/`
- an existing plan needs restructuring or deeper execution detail
- a proposal is approved and now needs a multi-PR delivery split
- the user asks to create per-PR execution prompts/contracts

Do not use this skill for one-file fixes, tiny docs edits, or direct implementation requests with no planning phase.

## Input contract

Before drafting a plan, confirm:
1. The proposal or problem statement this plan implements.
2. Whether this is a single PR or multi-PR rollout.
3. Any fixed constraints (out-of-scope, required tests, branch strategy).

If the user already provided these, proceed without extra questions.

## Required repo context

Read before writing:
1. `README.md` (public surface, env vars, ontology/reindex impact)
2. `CODEBASE_REQUIREMENTS.md` (brownfield and source assumptions)
3. Relevant active/completed docs under `plans/` and `propose/`
4. Target implementation files only as needed

## Quality bar from merged plan PRs

Strong plans in this repo consistently include:
- upfront **Status** and dependency context (`Depends on`, if applicable)
- a clear **Goal** section with concrete expected outcomes
- explicit **Principles (do not relitigate in review)** to freeze key decisions
- a **PR breakdown overview table** (scope, ontology bump, files, tests, dependency order)
- per-PR sections with:
  - file-by-file changes
  - named tests (verbatim test function names where possible)
  - definition of done
  - implementation step checklist
- explicit **Cross-PR risks and mitigations**
- explicit **Out of scope**
- whole-plan done definition and optional landing tracking

## Default plan structure

Use this structure unless the user requests a different format:

```markdown
# Plan: <topic>

Status: **active (planning)**. This plan implements
[`propose/<TOPIC>-PROPOSE.md`](../propose/<TOPIC>-PROPOSE.md).

Depends on: <dependency or "none">.

## Goal
- <outcome 1>
- <outcome 2>

## Principles (do not relitigate in review)
- <principle 1>
- <principle 2>

## PR breakdown - overview
| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-X1 | ... | ... | ... | ... | ... |

Landing order: **X1 -> X2 -> X3**.

## Resolved design decisions
| Topic | Decision |
| --- | --- |
| ... | ... |

---

# PR-X1 - <title>
## File-by-file changes
### 1. `path/to/file.py`
- <changes>

## Tests for PR-X1
1. `test_name_1`
2. `test_name_2`

## Definition of done (PR-X1)
- <checklist>

## Implementation step list
| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | ... | ... | ... |

---

# Cross-PR risks and mitigations
| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | ... | ... | ... |

# Out of scope
- <non-goals>

# Whole-plan done definition
1. <condition>
2. <condition>

# Tracking
- `PR-X1`: _pending_
- `PR-X2`: _pending_
```

## Rules while authoring

- Prefer concrete, testable statements over high-level intentions.
- Keep PR scopes independent when possible; state landing order explicitly.
- Name tests exactly when feasible; avoid vague "add tests".
- Call out ontology bump and re-index impact for schema/enrichment changes.
- Keep "Out of scope" strict; use it to prevent scope creep during implementation.
- Do not add compatibility shims unless explicitly requested.

## Per-PR execution prompt option

If the user wants Cursor-ready per-PR prompts, add a companion file:
- `plans/CURSOR-PROMPTS-<TOPIC>.md`

Each PR prompt should include:
- branch/base
- in-scope deliverables
- out-of-scope guardrails
- explicit test count/commands
- definition of done and PR title convention

Use `plans/completed/CURSOR-PROMPTS-TIER1B.md` as the structural reference.

## Naming and placement

- Plan files live in `plans/` as `PLAN-<TOPIC>.md` (uppercase topic).
- Move completed plans to `plans/completed/` after full rollout lands.

## Final checklist

- [ ] Plan has status, goal, principles, PR breakdown, risks, out-of-scope, done definition
- [ ] Every PR section has file-level scope and tests
- [ ] Ontology/reindex implications are explicit when relevant
- [ ] Implementation order is explicit and dependency-safe
- [ ] Plan is execution-ready without re-deriving design

## Additional resources

- See [reference.md](reference.md) for style rules distilled from merged PRs.
- See [examples.md](examples.md) for reusable section snippets.
