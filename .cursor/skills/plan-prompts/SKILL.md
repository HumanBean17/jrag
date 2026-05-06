---
name: plan-prompts
description: Generate per-PR Cursor execution prompts in `plans/CURSOR-PROMPTS-*.md` from an existing `plans/PLAN-*.md`. Use when the user asks to split implementation into Cursor-ready PR prompts with strict in-scope/out-of-scope guardrails.
disable-model-invocation: true
---

# Plan Prompts Skill

Create execution-ready Cursor prompts for each PR defined in a plan.

## When to use

Use this skill when:
- the user asks for `plans/CURSOR-PROMPTS-*.md`
- a `PLAN-*.md` exists and needs per-PR executable prompts
- implementation should be delegated PR-by-PR with tight scope control

Do not use this skill if there is no plan yet. Write/update the plan first.

## Required inputs

Before writing prompts, confirm:
1. Source plan file path (for example `plans/PLAN-XYZ.md`).
2. PR list and landing order from the plan.
3. Any fixed constraints (test count expectations, files not to touch, branch naming).

If already present in the plan, do not ask again.

## Source references to read

Always read:
1. `plans/completed/CURSOR-PROMPTS-TIER1B.md` (primary template)
2. The target `plans/PLAN-*.md`
3. `README.md` for current public contract terms when prompts mention tooling/schema

## Output file contract

Write one file:
- `plans/CURSOR-PROMPTS-<TOPIC>.md`

Include:
- status line
- one section per PR in plan landing order
- `Branch`, `Base`, `Plan section`, estimated diff size
- `@-files` list for context attachment
- copy-paste `Prompt` block with strict scope contract

## Per-PR prompt requirements

Each PR prompt must include all of:
- **Scope** with concrete deliverables mapped to plan section
- **Out of scope (do NOT touch)** list mirroring plan boundaries
- **Deliverables** numbered and testable
- **Tests** command and expected result format (counts only if known)
- **Sentinel checks** (`rg` patterns) where scope enforcement is critical
- **Manual evidence** commands when plan requires runtime proof
- **Definition of Done** checklist with PR title + branch convention

## Style rules

- Keep prompts self-contained; an agent should not re-derive design.
- Keep wording imperative and unambiguous.
- Preserve plan decisions; do not invent architecture changes in prompt text.
- Prefer exact symbol/file names from the plan.
- Use explicit stop conditions: "If you need to touch X, stop and ask."

## Prompt scaffold

```markdown
## PR-XX — <title>

**Branch:** `feat/<topic>` off `<base>`.
**Base:** `<base branch or predecessor PR branch>`.
**Plan section:** `plans/PLAN-<TOPIC>.md` § <section>.
**Estimated diff size:** ~<n> files, ~<m> LOC.

**Attach (`@-files`):**
- `@plans/PLAN-<TOPIC>.md`
- `@<key implementation files>`

**Prompt:**

````
You are implementing PR-XX from `plans/PLAN-<TOPIC>.md`.

Read the PR-XX section first. The plan is the source of truth.

## Scope
- <exact implementation scope>

## Out of scope (do NOT touch)
- <hard constraints>

## Deliverables
1. <deliverable>
2. <deliverable>

## Tests
Run: `<command>`
Expected: <result format / count if known>

## Sentinel checks
- `<rg command>`

## Manual evidence
<commands and expected signal>

## Definition of Done
- [ ] <item>
- [ ] PR title: `<title format>`
- [ ] Branch: `<branch>`
````
```

## Final checklist

- [ ] Prompt file covers every PR from the plan in order
- [ ] Each prompt has explicit scope and out-of-scope
- [ ] Deliverables are numbered and verifiable
- [ ] Tests and sentinel checks are present where needed
- [ ] No scope drift from plan decisions

## Additional resources

- See [reference.md](reference.md) for quality rules.
- See [examples.md](examples.md) for compact copy-ready snippets.
