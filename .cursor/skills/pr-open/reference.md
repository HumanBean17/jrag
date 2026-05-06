# PR Open Reference

Use this reference to keep PR descriptions consistent and review-ready.

## Quality bar

1. Scope names the exact plan/prompt section implemented.
2. Changes are grouped by behavior, not just file names.
3. Validation includes command + pass/fail + result summary.
4. Out-of-scope is explicit to prevent review ambiguity.
5. Definition of Done is checklist-form and verifiable.

## Definition of Done checklist guidance

A strong DoD checklist should verify:
- deliverables complete
- test/lint evidence recorded
- sentinel checks verified
- file scope respected
- PR metadata correct (base/title/branch)

Avoid vague items like "looks good" or "reviewed code".

## Common failure modes

- Missing "Out of Scope Confirmed" section
- Listing tests without result counts
- No manual evidence for plan-required runtime checks
- DoD present but not tied to measurable outcomes
- PR body copied from template but not filled with concrete details
