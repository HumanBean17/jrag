<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Agent task prompts — java-codebase-explore skill (PR-EXPLORE-1 → PR-EXPLORE-2)

Status: **completed** (reference). Implements [`plans/completed/PLAN-EXPLORATION-SKILL.md`](./PLAN-EXPLORATION-SKILL.md)
and [`propose/completed/EXPLORATION-SKILL-PROPOSE.md`](../../propose/completed/EXPLORATION-SKILL-PROPOSE.md).

One prompt per PR. Each is **self-contained**: copy the prompt verbatim into Cursor,
attach the files listed in its `@-files` block, and execute. If the prompt disagrees
with the plan, **the plan wins**.

**Workflow per PR**

1. Create the branch named in the prompt off the stated base.
2. Attach all `@-files` for that PR.
3. Paste the **Prompt** block (fenced content only).
4. Review the diff against **Out of scope** and **Sentinel checks** before pushing.
5. For PR-EXPLORE-2, record **Manual evidence** in the PR description.

**Universal rules**

- No `git push` from the agent unless your workflow explicitly allows it; prefer
  you pushing after review.
- **No** edits to `docs/AGENT-GUIDE.md`, `server.py`, indexer/graph Python, or
  ontology constants in these PRs — if any of that is required, stop and ask.
- Doc-only: **pytest is not a gate** for these two PRs; still run
  `.venv/bin/ruff check .` only if you touch Python (should not happen).

---

## PR-EXPLORE-1 — Propose merge (lock design)

**Branch:** `plan/exploration-skill-propose` off `master` (or `chore/exploration-skill-propose`).
**Base:** `master`.
**Plan section:** `plans/completed/PLAN-EXPLORATION-SKILL.md` § PR-EXPLORE-1.
**Estimated diff size:** 1 file, small LOC (status line tweak only if needed).

**Attach (`@-files`):**

- `@plans/completed/PLAN-EXPLORATION-SKILL.md` (read **PR-EXPLORE-1** section first)
- `@propose/completed/EXPLORATION-SKILL-PROPOSE.md`

**Prompt:**

````
You are implementing PR-EXPLORE-1 from `plans/completed/PLAN-EXPLORATION-SKILL.md`.

Read the **PR-EXPLORE-1 — Propose merge (lock design)** section in full. The plan
and `propose/completed/EXPLORATION-SKILL-PROPOSE.md` are the source of truth.

## Scope

- Land `propose/completed/EXPLORATION-SKILL-PROPOSE.md` on the default branch as the locked
  design for the java-codebase-explore skill.
- Optional: update the propose **Status** line from draft to the repo’s agreed
  convention (e.g. ready for implementation) if that matches maintainer practice.
- **Nothing else** in this PR — no skill files, scripts, README, or AGENT-GUIDE.

## Out of scope (do NOT touch)

- `docs/skills/**`, `scripts/**`, `docs/skills/*.zip`, `README.md`
- `docs/AGENT-GUIDE.md`, `AGENTS.md`, `.cursor/rules/**`, `server.py`, any
  `*.py` under the repo root for product code
- Moving the propose to `propose/completed/` (that happens after the **whole**
  plan lands per repo convention — see plan **Whole-plan done definition**)

## Deliverables

1. Single PR whose diff is **only** `propose/completed/EXPLORATION-SKILL-PROPOSE.md` (plus
   trivial typo fixes in that same file if you find load-bearing errors — avoid
   scope creep).
2. PR title aligned with propose §6, e.g. `propose: java-codebase-explore skill`
   (or equivalent imperative style).

## Tests

Run: **none required** (documentation-only PR).

Optional sanity: `.venv/bin/ruff check .` — expected: unchanged outcome vs
master if no Python changed.

## Sentinel checks

Run from repo root after your edits (before commit):

```bash
git status --short
```

Expected: only `propose/completed/EXPLORATION-SKILL-PROPOSE.md` modified (or empty if you
only need to open a PR from an already-correct file).

```bash
git diff --name-only
```

Expected: **only** `propose/completed/EXPLORATION-SKILL-PROPOSE.md`.

Forbidden paths on this branch (must be **absent** from `git diff --name-only`):

- `docs/AGENT-GUIDE.md`
- `README.md`
- `server.py`
- `docs/skills/`

## Manual evidence

- PR link + screenshot or bullet that §3–§7 of the propose remain intact as the
  contract for PR-EXPLORE-2.

## Definition of Done

- [ ] Merged (or ready for merge) with propose-only diff
- [ ] PR title: `propose: java-codebase-explore skill` (or equivalent)
- [ ] Branch: `plan/exploration-skill-propose` or `chore/exploration-skill-propose`
````

---

## PR-EXPLORE-2 — Ship skill + build automation + README

**Branch:** `feat/java-codebase-explore-skill` off `master` (preferred: after
PR-EXPLORE-1 is merged, rebase so design is locked on `master`).
**Base:** `master` (post PR-EXPLORE-1 merge recommended).
**Plan section:** `plans/completed/PLAN-EXPLORATION-SKILL.md` § PR-EXPLORE-2.
**Estimated diff size:** ~4 files (new `docs/skills/` tree, new `scripts/`, binary zip,
`README.md`), skill body target ≤ ~800 lines per plan.

**Attach (`@-files`):**

- `@plans/completed/PLAN-EXPLORATION-SKILL.md` (read **PR-EXPLORE-2** section first)
- `@propose/completed/EXPLORATION-SKILL-PROPOSE.md` (§3.2 outline, §3.3 missions, §3.5 metadata,
  Appendix A verbatim, Appendix B verbatim, §4 UC2 / UC6 rows)
- `@README.md` (§3 *Driving the MCP from an agent*, §9 *Further reading* — edit only
  the bullets/table rows the plan names)
- `@docs/JAVA-CODEBASE-RAG-CLI.md` (read-only — align pre-flight / `meta` / `increment`
  wording with CLI truth)

**Prompt:**

````
You are implementing PR-EXPLORE-2 from `plans/completed/PLAN-EXPLORATION-SKILL.md`.

Read the **PR-EXPLORE-2 — Ship skill + build automation + README** section and
`propose/completed/EXPLORATION-SKILL-PROPOSE.md` §3 / Appendices A–B. The plan and propose
are the source of truth.

## Scope

1. **`docs/skills/java-codebase-explore.md`** (new)
   - Follow **fixed section order** in plan / propose: Activation → Pre-flight →
     Map the seams → Mission catalogue (exactly **six** missions) → When MCP is
     the wrong layer → What this MCP is NOT → Confidence and staleness →
     Anti-patterns → Cheat sheet appendix.
   - Each mission uses the uniform template from propose §3.3 (When it applies /
     Goal / Opening move / Sequence / Stopping rule / Fallbacks). Mission set
     and names are exactly those in `plans/completed/PLAN-EXPLORATION-SKILL.md` PR-EXPLORE-2
     (Understand a feature; Plan a change; Onboard onto an unfamiliar service;
     Trace a cross-service flow; Prepare to write a propose doc; Debug a specific
     symptom).
   - Paste propose **Appendix A** verbatim into the anti-capabilities section
     (markdown fence in propose is for review — ship as normal headings/list in
     the skill, wording unchanged).
   - Paste propose **Appendix B** verbatim into the cheat sheet appendix.
   - Include metadata aligned with propose §3.5 (`name`, `title`, `description`,
     `when_to_load`, `when_not_to_load`) in the skill doc and mirror into the
     packaged `SKILL.md` for Perplexity.
   - Use `attrs` / `edge.attrs` vocabulary for confidence (`attrs.confidence`,
     `attrs.strategy`, `attrs.match`) per plan **Resolved design decisions**.
   - Link to `docs/AGENT-GUIDE.md` for argument shapes, recovery, slash aliases
     (relative repo path or GitHub raw URL pattern consistent with other docs —
     pick one and use it in the cheat sheet line that already points at AGENT-GUIDE
     in Appendix B).

2. **`scripts/build-explore-skill.sh`** (new, executable)
   - Rebuilds `docs/skills/java-codebase-explore.zip` deterministically from the
     canonical markdown + Perplexity-format `SKILL.md` manifest.
   - Header comment: prerequisites (e.g. `zip`), how to run, when to run (after
     skill edits; ontology bump / release hygiene).
   - Fail fast if a required tool is missing.

3. **`docs/skills/java-codebase-explore.zip`**
   - Generated by the script; **commit the artifact** produced on a clean run.

4. **`README.md`**
   - §3 *Driving the MCP from an agent*: add **one** new bullet **immediately after**
     the existing AGENT-GUIDE bullet, linking to `docs/skills/java-codebase-explore.md`
     and stating strategy vs operating manual in **one sentence**.
   - §9 *Further reading* table: add **one** row for the same markdown path (zip
     mention optional if it helps operators using Perplexity).

## Out of scope (do NOT touch)

- `docs/AGENT-GUIDE.md` (surgical patches are a different plan)
- `AGENTS.md`, `.cursor/rules/**` (unless a follow-up explicitly requests a
  one-line pointer — not in this PR)
- `server.py`, `search_lancedb.py`, `build_ast_graph.py`, `java_ontology.py`, or
  any production Python beyond the bash script
- Ontology bump / `ontology_version` / README ontology callout changes beyond
  keeping prose consistent with current **v11** as already stated in README
- Claude Code / Cursor skill bundle formats
- A seventh mission or an eighth anti-capability **entry** in Appendix A’s
  shipped list (cap **seven** items per propose §8 dumping-ground risk — do not
  extend the verbatim Appendix A block without a new propose)

If you believe AGENT-GUIDE **must** change to avoid a factual contradiction, stop
and ask — default is **no** AGENT-GUIDE diff.

## Deliverables

1. `docs/skills/java-codebase-explore.md` meeting section order, six missions,
   verbatim Appendices A/B content, metadata block, ≤ ~800 lines total per plan.
2. `scripts/build-explore-skill.sh` + committed `docs/skills/java-codebase-explore.zip`.
3. `README.md` updated in **both** §3 and §9 as specified.
4. PR description contains **Manual evidence** for UC2 and UC6 (see below).

## Tests

Run: **no pytest gate** for this PR.

If you accidentally touch any `*.py` file, run:

```bash
.venv/bin/ruff check .
```

Expected: clean for touched files. Prefer **zero** Python file diffs.

## Sentinel checks

From repo root with your branch checked out (before opening PR):

```bash
git diff master..HEAD --name-only
```

Expected set is **subset of**:

- `docs/skills/java-codebase-explore.md`
- `docs/skills/java-codebase-explore.zip`
- `scripts/build-explore-skill.sh`
- `README.md`

**Forbidden** paths must not appear in the diff file list. Run:

```bash
git diff master..HEAD --name-only | rg '^(docs/AGENT-GUIDE\.md|server\.py|AGENTS\.md|\.cursor/)'
```

**Expected:** empty stdout and **`rg` exit code 1** (no matches). **Failure:** any
printed filename or **`rg` exit code 0** — revert those paths.

Spot-check README still mentions ontology v11 only in existing places you did not
intend to edit:

```bash
rg -n "ontology|v11" README.md
```

Review the diff hunk-by-hunk — you should not be bumping ontology version as part
of this skill ship.

Confirm six mission headings:

```bash
rg -n "^### Mission:" docs/skills/java-codebase-explore.md | wc -l
```

Expected: **6** (or six missions with the exact heading pattern you used — if
you use a different heading token, document it in the PR and keep count at six).

## Manual evidence

Paste into the PR description (verbatim prompts + what the agent did, no need for
CI logs):

1. **UC2** (from propose §4): *"I'm new to billing-service; orient me."* — Show
   that an agent following **only** the skill body (plus normal MCP tool
   descriptions) can execute the documented onboarding sequence without asking
   you for JSON/tool-shape help.
2. **UC6** (from propose §4): *"Why is order-service slow on /checkout?"* — Show
   the same bar, with **fallback** rules (e.g. `git log`, file reads) exercised
   per the skill.

Also note **zip reproducibility**: command you ran to regenerate the zip, and
whether `git diff` is clean aside from expected zip metadata noise (call out
noise explicitly if present).

## Definition of Done

- [ ] All deliverables present; script runs; zip committed
- [ ] Sentinels green; no forbidden paths in diff
- [ ] PR title: `feat(docs): java-codebase-explore agent skill` (or repo-equivalent
  conventional commit / PR title per `propose/completed/EXPLORATION-SKILL-PROPOSE.md` §6)
- [ ] Branch: `feat/java-codebase-explore-skill`
- [ ] Manual UC2 + UC6 evidence recorded in PR body
````

---

## Final checklist (prompt author)

- [x] One section per PR in plan landing order
- [x] Each prompt: scope, out of scope, numbered deliverables, tests/sentinels,
      manual evidence, DoD
- [x] No scope drift from `plans/completed/PLAN-EXPLORATION-SKILL.md`
