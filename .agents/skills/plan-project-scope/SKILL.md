---
name: plan-project-scope
description: Write high-quality implementation plans for this repository using the merged plan format. Use when creating, updating, or reviewing files under `plans/`, splitting work into multiple PRs, or generating per-PR execution contracts.
disable-model-invocation: true
---

# Plan Skill

Author implementation plans that match this repo's merged style (`PLAN-*`) and stay execution-ready for agent handoff.

## When to use

Use this skill when:
- the user asks for a new plan file in `plans/active/`
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
3. Relevant active/completed docs under `plans/active/` and `propose/active/`
4. Target implementation files only as needed

## Quality bar from merged plan PRs

Strong plans in this repo consistently include:
- upfront **Status** and dependency context (`Depends on`, if applicable)
- a clear **Goal** section with concrete expected outcomes
- explicit **Principles (do not relitigate in review)** to freeze key decisions
- a **PR breakdown overview table** (scope, ontology bump, areas of concern, tests, dependency order)
- **Areas of concern column:** short **risk/review lens** (what to double-check or where coupling is likely). **Not** a module allowlist, **not** exhaustive, and **not** a substitute for the per-PR **File-by-file changes** section (that section remains the touch-scope contract)
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
[`propose/active/<TOPIC>-PROPOSE.md`](../../propose/active/<TOPIC>-PROPOSE.md).

Depends on: <dependency or "none">.

## Goal
- <outcome 1>
- <outcome 2>

## Principles (do not relitigate in review)
- <principle 1>
- <principle 2>

## PR breakdown - overview
| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
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
- Name tests exactly when feasible; avoid vague “add tests”.
- Call out ontology bump and re-index impact for schema/enrichment changes.
- Keep “Out of scope” strict; use it to prevent scope creep during implementation.
- Treat **Areas of concern** as **heads-up text for reviewers** (coupling, regression risk, semantic hotspots). Do **not** phrase it as “only these modules” — honest implementation may touch adjacent files; when that happens, update the **File-by-file changes** list and **Out of scope** so the contract stays clear.
- Do not add compatibility shims unless explicitly requested.

## No placeholders

Every section must contain the actual detail an agent or reviewer needs. These are **plan failures** — never write them:
- “TBD”, “TODO”, “implement later”, “fill in details”
- “Add appropriate error handling” / “add validation” / “handle edge cases” (without specifics)
- “Write tests for the above” (without naming test cases)
- “Similar to PR-X1” (repeat the detail — the implementer may read PRs out of order)
- File-by-file changes that describe *what* to do without enough context for *how*
- References to functions, types, or schemas not defined in any PR section

When a decision is genuinely deferred, label it explicitly: `**DEFERRED:** <what> — will be resolved in <which PR> because <why>.`

## Self-review

After drafting the complete plan, run these checks before finalizing:

1. **Spec coverage:** Skim each requirement from the proposal. Can you point to a PR section that implements it? Add tasks for any gaps.
2. **Placeholder scan:** Search the plan for the anti-patterns listed in “No placeholders” above. Fix them.
3. **Consistency check:** Do function names, type signatures, schema versions, and file paths used in later PRs match what earlier PRs define? A function called `clearLayers()` in PR-X1 but `clearFullLayers()` in PR-X3 is a plan bug.
4. **Dependency order:** Does the landing order actually satisfy all cross-PR dependencies stated in the overview table?

## Per-PR execution prompt option

If the user wants agent-ready per-PR prompts, add a companion file:
- `plans/AGENT-PROMPTS-<TOPIC>.md`

Each PR prompt should include:
- branch/base
- in-scope deliverables
- out-of-scope guardrails
- pytest commands and evidence expectations (avoid hard totals that go stale)
- definition of done and PR title convention

Use **any** completed **`plans/completed/AGENT-PROMPTS-*.md`** as the structural reference (pick one that matches the effort’s shape).

## Naming and placement

- Active plan files live in `plans/active/` as `PLAN-<TOPIC>.md` (uppercase topic).
- Move completed plans to `plans/completed/` after full rollout lands.

## Execution handoff

After saving the plan, offer the user an execution choice:

1. **Subagent-driven** — dispatch a fresh subagent per PR (via `superpowers:subagent-driven-development`), review between PRs, fast iteration. Best for multi-PR plans.
2. **Inline execution** — execute PRs in this session (via `superpowers:executing-plans`), batch execution with checkpoints. Best for single-PR plans or when context continuity matters.
3. **Manual handoff** — save the plan and AGENT-PROMPTS file for later execution. The implementer picks up from the written artifacts.

If the user picks option 1 or 2, invoke the corresponding superpowers skill before starting execution.

## Final checklist

- [ ] Plan has status, goal, principles, PR breakdown, risks, out-of-scope, done definition
- [ ] Every PR section has file-level scope and named tests
- [ ] No placeholders — every section has concrete detail, no TBD/TODO/vague directives
- [ ] Ontology/reindex implications are explicit when relevant
- [ ] Implementation order is explicit and dependency-safe
- [ ] Cross-PR names/types/paths are consistent (self-review passed)
- [ ] Plan is execution-ready without re-deriving design

## Additional resources

- See [reference.md](reference.md) for style rules distilled from merged PRs.
- See [examples.md](examples.md) for reusable section snippets.
