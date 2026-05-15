# Plan Style Reference (Repo-grounded)

This reference distills patterns from **merged multi-PR plans** in this repo. **Do not cache example filenames from here** — open **`plans/completed/`** (and root **`plans/`** for in-flight work) for current `PLAN-*.md` / `CURSOR-PROMPTS-*.md` examples; match their section depth and tables, not a particular PR number.

## Non-negotiables

1. **Plan-only clarity**
   - State when a PR is docs/planning only.
2. **Execution-ready detail**
   - A contributor should implement without re-deriving architecture.
3. **Scope control**
   - Out-of-scope section must be explicit and enforceable.
4. **Test contract**
   - Provide named tests and expected validation commands.
5. **Dependency clarity**
   - State prerequisites and PR landing order.

## Recommended section order

1. Status + proposal link + dependencies
2. Goal
3. Principles (frozen decisions)
4. PR overview table
5. Resolved design decisions
6. Per-PR sections (files/tests/DoD/steps)
7. Cross-PR risks + mitigations
8. Out of scope
9. Whole-plan done definition
10. Tracking

## Writing patterns that work well

- Use "do not relitigate in review" for locked design constraints.
- Use per-PR test names to avoid ambiguity during implementation.
- Use "Definition of done" per PR plus overall done definition.
- Use small "implementation step list" tables for deterministic execution.
- Include explicit non-goals for adjacent tempting work.

## Anti-patterns

- "Add tests" without naming concrete test cases.
- Mixing multiple PR scopes into one section.
- Omitting dependency order for multi-PR plans.
- Vague risk sections with no mitigation actions.
- Missing ontology/reindex notes when schema changes are planned.
