---
name: pr-open
description: Open a pull request to `master` with a comprehensive, review-ready description using this repo's plan-driven format. Use when the user asks to create/open a PR, especially from `plans/PLAN-*.md` or `plans/CURSOR-PROMPTS-*.md`, and include a Definition of Done checklist in the PR body.
disable-model-invocation: true
---

# PR Open Skill

Create PRs to `master` with complete context, validation evidence, and a
Definition of Done checklist that matches this repository's plan-prompt style.

## When to use

Use this skill when:
- the user asks to open/create a PR
- the branch already contains implementation and needs a full PR description
- the work follows a plan/prompt contract and needs checklist-backed evidence

Do not use this skill for propose-only docs PRs unless the user explicitly asks
for the full implementation-style PR template.

## Required inputs

Before opening a PR, collect:
1. PR scope source (`plans/PLAN-*.md` section, prompt file, or user text)
2. Branch and base (`master` unless user says otherwise)
3. Test command(s) and results
4. Manual evidence command(s) and outcomes (if required by plan)
5. Out-of-scope items that must be explicitly confirmed

If any are missing, infer from repository context or ask the user.

## Preparation workflow

1. Inspect git state:
   - `git status --short --branch`
   - `git diff --stat master...HEAD`
   - `git log --oneline master..HEAD`
2. Ensure branch is pushed:
   - if no upstream, run `git push -u origin HEAD`
3. Gather evidence:
   - run lint/tests requested by plan or user
   - run manual evidence commands listed by plan/prompt (if any)
4. Build PR body using the template below.
5. Open PR with:
   - `gh pr create --base master --title "<title>" --body "<heredoc body>"`

## Utility script (one command)

Use the included script to generate the full PR body from structured input and
open the PR in one step:

1. Copy and fill `.cursor/skills/pr-open/pr-input.example.json`.
2. Preview body:
   - `python .cursor/skills/pr-open/create_pr.py --input /path/to/pr-input.json --print-only`
3. Open PR:
   - `python .cursor/skills/pr-open/create_pr.py --input /path/to/pr-input.json --create`

Script behavior:
- enforces required sections/fields
- outputs sections in canonical order
- includes Definition of Done checklist
- defaults base branch to `master`

## PR title convention

- Prefer: `feat: <scope> (<plan-pr-id>)` for feature PRs.
- Prefer: `fix: <scope>` for bug-fix PRs.
- Keep title aligned with plan/prompt naming when provided.

## PR body template (comprehensive)

Use this exact section order:

```markdown
## Scope
<What this PR implements and which plan/prompt section it maps to.>

## What Changed
- <Concrete code changes, grouped by module/behavior>
- <...>

## Semantics / Non-Goals
- <Behavior intentionally unchanged>
- <Explicit non-goals>

## Validation
### Lint
- `<command>` ✅/❌

### Tests
- `<command>` ✅/❌
- Result: `<counts or summary>`

### Additional checks
- `<command>` ✅/❌

## Sentinel checks
- `<rg command>` -> <result>

## Manual evidence
- `<command>`
- Observed: <key output summary>

## Out of Scope Confirmed
Did not implement:
- <item>
- <item>

## Definition of Done
- [ ] All listed deliverables for this PR are shipped.
- [ ] Required lint/tests pass locally with recorded command output.
- [ ] Sentinel checks produce expected results.
- [ ] Only in-scope files are modified.
- [ ] PR description includes scope, validation, and manual evidence.
- [ ] PR targets `master` with agreed title and branch naming.
```

## Plan-prompt mapping rule

When working from `plans/CURSOR-PROMPTS-*.md`, map sections directly:
- Prompt `Scope` -> PR `Scope`
- Prompt `Deliverables` -> PR `What Changed`
- Prompt `Tests` -> PR `Validation`
- Prompt `Manual evidence` -> PR `Manual evidence`
- Prompt `Out of scope` -> PR `Out of Scope Confirmed`
- Prompt `Definition of Done` -> PR `Definition of Done`

## Example anchor

Mirror the structure used in PR #42:
- clear `Scope` mapped to plan PR id
- explicit `Semantics / Non-Goals`
- command-level `Validation`
- `Sentinel checks` and `Manual evidence`
- explicit `Out of Scope Confirmed`

## Final checklist

- [ ] Base branch is `master`
- [ ] Branch is pushed and tracks remote
- [ ] PR body includes all template sections
- [ ] Definition of Done checklist is present
- [ ] PR URL is returned to the user

## Additional resources

- See [reference.md](reference.md) for section quality rules.
- See [examples.md](examples.md) for copy-ready PR body examples.
- Use [pr-input.example.json](pr-input.example.json) as the fillable input shape.
