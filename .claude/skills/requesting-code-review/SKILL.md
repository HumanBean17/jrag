---
name: requesting-code-review
description: Use when completing tasks, implementing major features, or before merging to verify work meets requirements
---

# Requesting Code Review

Fan out a **team** of code reviewer subagents — one per review scope — and run them in parallel. Each reviewer gets precisely crafted context for its scope, never your session's history. Splitting the review across focused reviewers catches more than a single pass, and isolating their context preserves your own for continued work.

**Core principle:** Review early, review often.

## When to Request Review

**Mandatory:**
- After each task in subagent-driven development
- After completing major feature
- Before merge to main

**Optional but valuable:**
- When stuck (fresh perspective)
- Before refactoring (baseline check)
- After fixing complex bug

## How to Request

**1. Get git SHAs:**
```bash
BASE_SHA=$(git rev-parse HEAD~1)  # or origin/main
HEAD_SHA=$(git rev-parse HEAD)
```

**2. Decide the scopes:**

Look at the diff and decompose it into review scopes. **You choose the breakdown** — by subsystem, by concern, by file cluster, whatever fits this change. There is no fixed set of scopes; pick what this diff actually needs (one or several). The goal is a focused, non-overlapping slice per reviewer.

**3. Fan out the review team:**

Dispatch one `general-purpose` reviewer per scope, **all in one response** so they run in parallel. Each fills the per-reviewer template at [code-reviewer.md](code-reviewer.md).

**Shared placeholders:**
- `{DESCRIPTION}` - Brief summary of what you built
- `{PLAN_OR_REQUIREMENTS}` - What it should do
- `{BASE_SHA}` - Starting commit
- `{HEAD_SHA}` - Ending commit

**Per-reviewer placeholder:**
- `{SCOPE}` - The slice of the diff this reviewer owns (you decide)

**4. Merge findings:**

Collect every reviewer's output, dedupe overlapping issues, and rank by severity. The merged result is your review.

**5. Act on feedback:**
- Fix Critical issues immediately
- Fix Important issues before proceeding
- Note Minor issues for later
- Push back if reviewers are wrong (with reasoning)

## Example

```
[Just completed Task 2: Add verification function — diff spans index logic, CLI, and tests]

You: Let me request code review before proceeding.

BASE_SHA=$(git log --oneline | grep "Task 1" | head -1 | awk '{print $1}')
HEAD_SHA=$(git rev-parse HEAD)

[Decide scopes from the diff: index logic, CLI, tests]
[Dispatch one reviewer per scope, all in one response]
  Reviewer A — SCOPE: index/repair logic and data integrity
  Reviewer B — SCOPE: CLI flag handling and user-facing behavior
  Reviewer C — SCOPE: test coverage and assertion quality
  (shared) DESCRIPTION: Added verifyIndex() and repairIndex()
  (shared) BASE_SHA: a7981ec  HEAD_SHA: 3df7661

[Team returns in parallel, you merge]:
  Strengths: Clean architecture, real tests
  Issues (deduped, ranked):
    Important: Missing progress indicators
    Minor: Magic number (100) for reporting interval
  Assessment: Ready to proceed

You: [Fix progress indicators]
[Continue to Task 3]
```

## Integration with Workflows

**Subagent-Driven Development:**
- Review after EACH task
- Catch issues before they compound
- Fix before moving to next task

**Executing Plans:**
- Review after each task or at natural checkpoints
- Get feedback, apply, continue

**Ad-Hoc Development:**
- Review before merge
- Review when stuck

## Red Flags

**Never:**
- Skip review because "it's simple"
- Ignore Critical issues
- Proceed with unfixed Important issues
- Argue with valid technical feedback

**If reviewer wrong:**
- Push back with technical reasoning
- Show code/tests that prove it works
- Request clarification

See template at: [code-reviewer.md](code-reviewer.md)
