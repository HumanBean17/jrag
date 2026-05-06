# Plan Prompts Reference

Use this when converting `PLAN-*` into `CURSOR-PROMPTS-*`.

## Core quality bar

1. Prompt is self-contained and executable by another agent.
2. Scope is locked to one PR section only.
3. Out-of-scope is explicit and enforceable.
4. Deliverables are concrete and numbered.
5. Tests + evidence are specific, not generic.

## Mapping rule (plan -> prompt)

- Plan PR section title -> Prompt PR section title
- Plan file-by-file changes -> Prompt "Scope" + "Deliverables"
- Plan tests list -> Prompt "Tests"
- Plan done definition -> Prompt "Definition of Done"
- Plan risks/out-of-scope -> Prompt "Out of scope" + sentinel checks

## Common failure modes

- Mixing two PR scopes into one prompt
- Dropping out-of-scope items from the plan
- Vague tests ("run tests") without commands
- Missing branch/base/title conventions
- Adding new architecture decisions not present in the plan
