---
name: plan-prompts
description: Generate per-PR Cursor execution prompts in `plans/CURSOR-PROMPTS-*.md` from an existing `plans/PLAN-*.md`. Each prompt must include `## Tests to run (iteration loop)` (pytest file subset + rationales) between Deliverables and Tests per TEST-SUITE-FAST-LOOP. Use when the user asks to split implementation into Cursor-ready PR prompts with strict in-scope/out-of-scope guardrails.
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
3. Any fixed constraints (branch naming, files or areas not to touch).

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
- `Branch`, `Base`, `Plan section`
- `@-files` list for context attachment
- copy-paste `Prompt` block with strict scope contract

## Per-PR prompt requirements

Each PR prompt must include all of:
- **Scope** with concrete deliverables mapped to plan section
- **Out of scope (do NOT touch)** list mirroring plan boundaries
- **Deliverables** numbered and testable
- **`## Tests to run (iteration loop)`** — pytest **file** subset for fast local iteration (see below); must appear **after Deliverables and before the full Tests section**
- **Tests** command and expected signals (pass/fail, skips, fixtures); avoid hard totals that go stale across branches
- **Sentinel checks** (`rg` patterns) where scope enforcement is critical
- **Manual evidence** commands when plan requires runtime proof
- **Definition of Done** checklist with PR title + branch convention

### Tests to run (iteration loop) — required subsection

Per [`propose/completed/TEST-SUITE-FAST-LOOP-PROPOSE.md`](../../../propose/completed/TEST-SUITE-FAST-LOOP-PROPOSE.md) and [`plans/completed/PLAN-TEST-SUITE-FAST-LOOP.md`](../../../plans/completed/PLAN-TEST-SUITE-FAST-LOOP.md) PR-2:

- Add a markdown section with the **exact heading** `## Tests to run (iteration loop)` inside the fenced **Prompt** block, **immediately after** `## Deliverables` and **before** `## Tests`.
- Content: bullet list of `tests/test_*.py` paths, each with a **one-line rationale** tied to the PR’s code paths.
- **Merge gate:** state that CI enforces a green `test` check on every PR; code changes run the full default suite (`pytest tests`, `JAVA_CODEBASE_RAG_RUN_HEAVY` unset or `0`), docs-only PRs skip pytest but still need a green `test` job; the iteration list is for speed only.
- **Docs-only (UC15):** if the PR is documentation-only with no test signal, use an explicit empty pattern, e.g. a single bullet `*(none — docs-only change; CI test job passes but pytest is skipped.)*` — do not invent a fake file list.

This heading must stay verbatim so reviewers (and the repo **`pr-review`** skill in `.cursor/skills/pr-review/`) can grep for it.

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

## Tests to run (iteration loop)

Run only these files during local iteration; CI `test` on PR + `master` is the merge gate (full pytest when code changes).

- `tests/test_<file>.py` — <one-line rationale>
- `tests/test_<other>.py` — <one-line rationale>

Docs-only PRs (UC15): use a single bullet such as *(none — docs-only change; CI test job passes but pytest is skipped.)* instead of inventing paths.

## Tests
Run: `<command>`
Expected: <pass/fail, skips, key fixtures — not a brittle total count>

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
- [ ] Each generated prompt includes **`## Tests to run (iteration loop)`** between Deliverables and Tests (or the UC15 docs-only line)
- [ ] Tests and sentinel checks are present where needed
- [ ] No scope drift from plan decisions

## Additional resources

- See [reference.md](reference.md) for quality rules.
- See [examples.md](examples.md) for compact copy-ready snippets.
