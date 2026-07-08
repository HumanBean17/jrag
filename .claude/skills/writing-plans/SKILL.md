---
name: writing-plans
description: Use when you have a spec or requirements for a multi-step task, before touching code
---

# Writing Plans

## Overview

Write comprehensive implementation plans assuming the engineer has zero context for our codebase and questionable taste. Document everything they need to know: which files to touch for each task, the exact interfaces and data shapes, the test design (what each test verifies and the expected result), and docs they might need to check. Give them the whole plan as bite-sized tasks. DRY. YAGNI. TDD. Frequent commits.

Assume they are a skilled developer, but know almost nothing about our toolset or problem domain. Assume they don't know good test design very well.

## Core Principles

These principles override the rest of this skill when in conflict.

1. **Plans carry design, not code.** The plan describes WHAT to build for each task — files, exact interfaces, data shapes, validation rules, and test design (what each test verifies and the expected result). It MUST NOT contain implementation logic: no method bodies, algorithms, or actual code — neither test code nor implementation code. Writing code is the job of the implementor (`superpowers:executing-plans` / `superpowers:subagent-driven-development`). Your job here is to plan. This mirrors the brainstorming skill's rule that specs carry design, not code.
2. **Plans are self-contained.** Every task carries the full design and contracts a zero-context implementer needs to write the code — exact signatures, types, data models, validation, and error cases. Do not push the reader to the spec for these. The implementer should never have to leave the task to understand what to build. (This accepts some duplication with the spec; keep the two consistent via the Self-Review.)

**Announce at start:** "I'm using the writing-plans skill to create the implementation plan."

**Context:** If working in an isolated worktree, it should have been created via the `superpowers:using-git-worktrees` skill at execution time.

**Save plans to:** `docs/superpowers/plans/active/YYYY-MM-DD-<feature-name>.md`
- `active/` holds plans for changes currently being implemented — current source of truth. On release (merge into the base branch), the plan moves to `plans/archive/` alongside its spec and becomes an ADR (past decision, not current domain state). The move is handled by `superpowers:finishing-a-development-branch`.
- (User preferences for plan location override this default)

## Scope Check

If the spec covers multiple independent subsystems, it should have been broken into sub-project specs during brainstorming. If it wasn't, suggest breaking this into separate plans — one per subsystem. Each plan should produce working, testable software on its own.

## File Structure

Before defining tasks, map out which files will be created or modified and what each one is responsible for. This is where decomposition decisions get locked in.

- Design units with clear boundaries and well-defined interfaces. Each file should have one clear responsibility.
- You reason best about code you can hold in context at once, and your edits are more reliable when files are focused. Prefer smaller, focused files over large ones that do too much.
- Files that change together should live together. Split by responsibility, not by technical layer.
- In existing codebases, follow established patterns. If the codebase uses large files, don't unilaterally restructure - but if a file you're modifying has grown unwieldy, including a split in the plan is reasonable.

This structure informs the task decomposition. Each task should produce self-contained changes that make sense independently.

## Task Right-Sizing

A task is the smallest unit that carries its own test cycle and is worth a
fresh reviewer's gate. When drawing task boundaries: fold setup,
configuration, scaffolding, and documentation steps into the task whose
deliverable needs them; split only where a reviewer could meaningfully
reject one task while approving its neighbor. Each task ends with an
independently testable deliverable.

## Bite-Sized Task Granularity

**Each step is one action (2-5 minutes):**
- "Write the failing test" - step
- "Run it to make sure it fails" - step
- "Implement the minimal code to make the test pass" - step
- "Run the tests and make sure they pass" - step
- "Commit" - step

## Plan Document Header

**Every plan MUST start with this header:**

```markdown
# [Feature Name] Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** [One sentence describing what this builds]

**Architecture:** [2-3 sentences about approach]

**Tech Stack:** [Key technologies/libraries]

## Global Constraints

[The spec's project-wide requirements — version floors, dependency limits,
naming and copy rules, platform requirements — one line each, with exact
values copied verbatim from the spec. Every task's requirements implicitly
include this section.]

---
```

## Task Structure

````markdown
### Task N: [Component Name]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

**Interfaces:**
- Consumes: [what this task uses from earlier tasks — exact signatures and types]
- Produces: [what later tasks rely on — exact function names, parameter and
  return types, data shapes (fields + types), validation rules, and error
  cases. A task's implementer sees only their own task; this block is how they
  learn the contracts neighboring tasks use. It must be complete enough to
  implement without reading the spec.]

- [ ] **Step 1: Write the failing test**

Describe what this test verifies and the exact expected result — the scenario,
the input, and the assertion outcome. Do NOT write the test code; the
implementer writes it from this.

Example: `parse_config("name=api\nretries=3")` returns a `Config` with
`name == "api"` and `retries == 3`; blank lines are ignored.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/path/test.py::test_name -v`
Expected: FAIL with "function not defined" (or equivalent)

- [ ] **Step 3: Write minimal implementation**

Describe the behavior the implementation must provide to pass the test — inputs
handled, transformations applied, edge cases covered. Do NOT write the
implementation code; the implementer writes it from this description and the
Produces contracts above.

Example: split lines on "=", cast integer fields, skip blank lines.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/path/test.py::test_name -v`
Expected: PASS

- [ ] **Step 5: Commit**

Run: `git add tests/path/test.py src/path/file.py`
Run: `git commit -m "feat: add specific feature"`
````

## No Placeholders

Every step and task must contain the actual design content an engineer needs — even though it contains no code. These are **plan failures** — never write them:
- "TBD", "TODO", "implement later", "fill in details"
- "Add appropriate error handling" / "add validation" / "handle edge cases" (spell out which cases and what should happen)
- "Write tests for the above" (name each test's scenario and expected result)
- "Similar to Task N" (repeat the contracts and design — the engineer may be reading tasks out of order)
- A test step that doesn't state what it verifies and the exact expected result
- An implementation step that doesn't state the behavior to provide
- Interfaces, types, or data shapes left vague or undefined
- References to types, functions, or methods not defined in any task

Code is NOT the fix for any of these. The fix is more precise design: exact behavior, expected results, signatures, and data shapes.

## Remember
- Exact file paths always
- Complete design in every task — exact signatures, types, data shapes, validation, and test behavior + expected results. Never code.
- Exact commands with expected output (test runs, git commits)
- DRY, YAGNI, TDD, frequent commits

## Self-Review

After writing the complete plan, look at the spec with fresh eyes and check the plan against it. This is a checklist you run yourself — not a subagent dispatch.

**1. Code scan:** Does the plan contain any implementation logic — method bodies, algorithms, test code, or copy-paste-ready code? If so, remove it and replace each occurrence with the design it implies: exact behavior, expected test results, signatures, and data shapes. (See Core Principles: plans carry design, not code.)

**2. Self-containment scan:** Could a zero-context implementer write the code for each task using only that task? If any task pushes the reader to the spec or says "see earlier," expand its Interfaces (Consumes/Produces) until the task stands alone.

**3. Spec coverage:** Skim each section/requirement in the spec. Can you point to a task that implements it? List any gaps.

**4. Placeholder scan:** Search your plan for the red-flag patterns from "No Placeholders" above. Fix them with more precise design, not code.

**5. Type consistency:** Do the types, method signatures, and property names you used in later tasks match what you defined in earlier tasks? A function called `clearLayers()` in Task 3 but `clearFullLayers()` in Task 7 is a bug.

If you find issues, fix them inline. No need to re-review — just fix and move on. If you find a spec requirement with no task, add the task.

## Execution Handoff

After saving the plan, offer execution choice:

**"Plan complete and saved to `docs/superpowers/plans/active/<filename>.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?"**

**If Subagent-Driven chosen:**
- **REQUIRED SUB-SKILL:** Use superpowers:subagent-driven-development
- Fresh subagent per task + two-stage review

**If Inline Execution chosen:**
- **REQUIRED SUB-SKILL:** Use superpowers:executing-plans
- Batch execution with checkpoints for review
