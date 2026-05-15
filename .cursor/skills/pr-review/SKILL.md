---
name: pr-review
description: >-
  Reviews pull requests against plan scope, requires pasted pytest subset
  evidence plus green full-suite CI, and rejects checkbox-only test claims.
  Use when reviewing a PR, approving a merge, or checking an agent handoff.
disable-model-invocation: true
---

# PR review

Use this checklist when reviewing a PR that was driven by a written plan or a **`plan-prompts`** / `CURSOR-PROMPTS-*` task handoff.

## 1. Scope and diff hygiene

- [ ] Diff matches stated scope; no drive-by refactors or scope leaks from the plan’s **Out of scope** list.
- [ ] If the task prompt listed sentinel `git grep` patterns, they are **absent** from `git diff master..HEAD` (when that contract applies).

## 2. Test evidence — iteration subset (mandatory)

The PR body or thread must include **pasteable proof** that the author ran the files declared under **`## Tests to run (iteration loop)`** in the task prompt.

**Acceptable**

- The **exact** command line (e.g. `.venv/bin/python -m pytest tests/test_foo.py tests/test_bar.py -v` or equivalent).
- The **exit code** or explicit pass summary tied to that command (e.g. `exit 0`, or pytest’s final `N passed` line immediately after the command).

**Not acceptable (reject the review)**

- Only a checkbox such as `- [x] subset ran` or “tests passed” **without** the command and outcome above.
- A vague “ran pytest” with no file list and no exit code.
- Substituting a different file list than the prompt declared, without explanation.

If the task prompt declared **docs-only** (empty iteration list per UC15), subset evidence is: state that no test files were required for iteration, and still require a **green `test` CI** run below (pytest may be skipped when only documentation paths changed).

**Subset green does not replace the merge gate:** If the required `test` CI check is red (or missing), the PR is not merge-ready even when the declared subset passed locally.

## 3. Test evidence — full suite / CI (mandatory when repo CI exists)

When this repository has a required GitHub Actions workflow (`.github/workflows/test.yml`):

- [ ] The PR description or review comment includes a **link** to a **green** `test` Actions run on **this PR** at the **same commit** being reviewed (or the tip the reviewer approves). For code changes, the run must include `pytest tests` with `JAVA_CODEBASE_RAG_RUN_HEAVY` unset or `0`. For docs-only PRs, a green run with pytest skipped is sufficient.

If CI is not yet enabled for the repo, note that in the review; once the workflow exists, **withhold approval** until both §2 and §3 are satisfied.

## 4. Plan and docs

- [ ] PR body references the plan/propose when the work was plan-driven.
- [ ] `tests/README.md` or other operator docs changes remain consistent with repo conventions.

## 5. Manual / product evidence

Reproduce or spot-check any plan-required manual command **after** §2–§3 are satisfied (or in parallel if independent).

---

## Self-check (dry-run)

- **Fail:** Review comment says “subset verified [x]” with no pytest command → **does not meet §2**.
- **Pass:** Pasted command, pytest summary / `exit 0`, and link to green full-suite run on the PR commit.
