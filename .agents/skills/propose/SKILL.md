---
name: propose
description: Write high-quality proposal docs for this repository using the established propose style. Use when the user asks to create, update, or review files under `propose/`, or requests a "proposal/propose" before implementation.
disable-model-invocation: true
---

# Propose Skill

Create proposal docs that match this repository's accepted style and workflow.

## When to use

Use this skill when:
- the user asks for a new proposal in `propose/active/`
- the user asks to refine an existing proposal
- work is non-trivial and should be proposed before implementation

Do not use this skill for small one-file bug fixes or purely mechanical edits.

## Handoff to `plan`

If the work is expected to ship as multiple implementation PRs, create/update
the proposal here first, then hand off plan authoring to
`../plan/SKILL.md` for the execution split and per-PR delivery contract.

## Required repo context

Before drafting, read:
1. `README.md` (public surface, env vars, ontology/reindex implications)
2. `CODEBASE_REQUIREMENTS.md` (brownfield assumptions and source mapping)
3. Relevant active plan/propose docs under `plans/active/` and `propose/active/`
4. The target area's implementation files (only as needed)

## Proposal quality bar (based on merged PR patterns)

Strong proposals in this repo consistently do the following:
- state **Status** up front (proposal only, not implementation)
- define a crisp **Problem Statement** with concrete failure modes
- include a concrete **Proposed Solution** with explicit scope boundaries
- call out **Schema / ontology / re-index impact** explicitly
- include **Open questions** with `[TBD]` items and recommended defaults
- include **Out of scope** to avoid accidental scope creep
- include **Sequencing** and dependencies when multi-PR work is expected
- include a lightweight **test/validation strategy** even for docs-only PRs

## Standard propose structure

Use this structure by default (adapt section names only when needed):

```markdown
# <TOPIC TITLE>

## Status
Proposal — not yet implemented.

## Problem Statement
<What is broken/missing, and why it matters now. Include concrete examples.>

## Proposed Solution
<Core design, API/schema behavior, decision points.>

## Scope
<What this proposal changes.>

## Schema / Ontology / Re-index impact
- Ontology bump: <required or not required>
- Re-index required: <yes/no and why>
- Config/tool surface changes: <list or "none">

## Tests / Validation
<How correctness will be validated once implemented.>

## Open Questions ([TBD])
1. <Question> — Recommended: <option>
2. <Question> — Recommended: <option>

## Out of scope
- <Explicit non-goals>

## Sequencing / Follow-ups
<PR split, dependencies, or "single PR".>
```

## Writing rules

- Prefer explicit, testable statements over aspirational language.
- Keep terminology consistent with `java_ontology.py` and README terms.
- If behavior changes user-facing tools, mention exact tool names and fields.
- If semantics change, state ontology bump and re-index requirement plainly.
- If this is a design-only PR, clearly say no production code changed.
- Never propose compatibility shims unless explicitly requested.

## PR body template for propose-only changes

When opening the PR, use this compact shape:

```markdown
## What
<Added/updated proposal file(s).>

## Why now
<Urgency and context.>

## Highlights
- <3-6 key points>

## Tests
Docs-only; baseline unchanged.

## Out of scope
- <Implementation deferred>
```

## File naming

- Use uppercase kebab-style topic names ending in `-PROPOSE.md`.
- Keep names specific to the decision, e.g.:
  - `FEATURE-NAME-PROPOSE.md`
  - `TOOL-NAME-PROPOSE.md`
  - `ARCHITECTURE-CHANGE-PROPOSE.md`

## Final checklist

- [ ] Proposal file lives under `propose/active/`
- [ ] Problem statement includes concrete examples
- [ ] Schema/ontology/re-index impact is explicit
- [ ] Open questions include `[TBD]` with recommendations
- [ ] Out-of-scope section is present
- [ ] Sequencing/follow-up path is clear

## Additional resources

- See practical examples in [reference.md](reference.md).
- See a repo-grounded golden sample in [examples.md](examples.md).
