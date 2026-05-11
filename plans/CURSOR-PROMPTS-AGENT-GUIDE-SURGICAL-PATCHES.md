# Cursor task prompts — AGENT-GUIDE surgical patches (PR-AGP-1 → PR-AGP-2)

Status: **active**. Companion to
[`plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md`](./PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md)
and
[`propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`](../propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md).

One prompt per PR. Each is **self-contained**: copy the prompt into Cursor in
agent mode, attach the files from its `@-files` block, and execute.

**Workflow per PR**

1. Branch off `master` (use the branch name in the prompt).
2. Attach `@-files` from the prompt.
3. Paste the prompt body.
4. Run validation commands from the prompt before opening the PR.
5. Do not push from the agent unless your workflow explicitly allows it.

**Test count contract**

- **New tests added in these PRs:** **0** (documentation only).
- Still run the repo gate before push:
  `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -q` — must match
  the current baseline (no new failures).

**Universal rules**

- [`propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`](../propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md)
  **§3 Patch B “Canonical block order”** is the single source of truth for
  where the staleness paragraph sits (after `After two failed attempts…`, not
  directly under the table). Appendix A subsection **A.1 → A.4** matches
  **implementation order Patch A → B → C**; use it for verbatim wording.
- If anything still disagrees, **§3 wins** over Appendix prose layout.
- **No drive-by edits** outside the deliverables list for that PR.
- After **both** PRs merge, move the propose + plan to `completed/` per
  `PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md` whole-plan done (human or follow-up PR).

---

## PR-AGP-1 — Propose merge (lock three patches)

**PR title (convention):** `propose: surgical patches to docs/AGENT-GUIDE.md`

**Branch:** `plan/agent-guide-surgical-patches-propose` off `master`.

**Base:** `master` at latest.

**Plan section:** `plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md` § PR-AGP-1.

**Estimated diff size:** 1 file, small (status line optional).

**Attach (`@-files`):**

- `@plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md` (PR-AGP-1 section only in scope)
- `@propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`

**Prompt:**

````
You are implementing PR-AGP-1 from `plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md`.

Read the **PR-AGP-1 — Propose merge** section of the plan in full before
editing anything.

## Scope

Land the propose document on the default branch so PR-AGP-2 can cite §3 /
Appendix A as the locked insertion spec.

- Touch **only** `propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`.
- Optionally update **Status** from `draft` to agreed wording if your team
  requires it before the apply PR. Do **not** change §3 patch text or Appendix A
  without an explicit new propose revision.

## Out of scope (do NOT touch)

- `docs/AGENT-GUIDE.md` — zero edits in this PR.
- `README.md`, `AGENTS.md`, `.cursor/rules/`, code, tests, plans other than
  incidental typo fixes in the **same** propose file if truly necessary (prefer
  zero drive-by).

## Deliverables

1. `propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` merged-ready on your branch.
2. PR description lists: scope statement, link to this plan + propose, **test
   count 0 new tests**, note that `docs/AGENT-GUIDE.md` is intentionally untouched.

## Tests

- **New tests:** 0.
- Gate (from repo root):

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -q
```

## Sentinel grep (must be zero / empty as specified)

After your commit(s), on `git diff master..HEAD`:

```bash
git diff master..HEAD --name-only
```

**Expected:** only `propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` appears
(plus no other paths). If anything else appears, you violated scope — revert.

Confirm AGENT-GUIDE is untouched:

```bash
git diff master..HEAD -- docs/AGENT-GUIDE.md
```

**Expected:** empty diff.

## Definition of done

- PR-AGP-1 merged; reviewers can point implementers of PR-AGP-2 at propose §3 /
  Appendix A.
````

---

## PR-AGP-2 — Apply patches to `docs/AGENT-GUIDE.md`

**PR title (convention):** `docs(agent-guide): out-of-frame limits, staleness, neighbor edge confidence`

**Branch:** `chore/agent-guide-surgical-patches-apply` off `master` (prefer after
PR-AGP-1 is merged; if not, ensure propose on `master` already matches the patch
text below).

**Base:** `master` at latest.

**Plan section:** `plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md` § PR-AGP-2.

**Estimated diff size:** 1 file, ≤ 60 net lines added.

**Attach (`@-files`):**

- `@plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md` (PR-AGP-2 section)
- `@propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` (§3 Patch B canonical order +
  Appendix A.1–A.4 — verbatim wording and **A → B → C** apply order)
- `@docs/AGENT-GUIDE.md`

**Prompt:**

````
You are implementing PR-AGP-2 from `plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md`.

Read propose `propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` §3 (especially
Patch B canonical block order) and Appendix A.1–A.4 before editing. Verbatim
markdown comes from Appendix; **placement** for Patch B follows §3 (staleness
paragraph **after** the unchanged `After two failed attempts…` line).

## Scope

Apply **Patches A, B, and C** as **insertions only** inside the marker block:

- Opening marker (exact): `<!-- BEGIN java-codebase-rag MCP guide -->`
- Closing marker (exact): `<!-- END java-codebase-rag MCP guide -->`

Do **not** move, rename, or reorder existing sections. Do **not** edit the
*Maintenance notes* block below the markers, slash aliases, or the file preamble
above the markers except where strictly necessary for merge conflicts (should
be none).

### Patch A — after "Do NOT use this MCP when…"

Insert the following **immediately after** the paragraph that ends with
"Prefer the smallest call that answers the question." and **immediately before**
the line `**Workflow (GPS model):**`:

```markdown
### What this MCP is NOT

The MCP indexes Java production code, SQL, and YAML — nothing else.
Treat the following as out of frame:

- **Test files, build files, deploy / runtime story** — read `pom.xml`,
  `build.gradle`, `Dockerfile`, `.github/workflows/`, README directly.
- **Reflection, dynamic dispatch, SPI lookups** — `CALLS` resolves
  static method calls only; the resolved caller set is a **lower bound**.
- **Unindexed services / repos** — verify with `java-codebase-rag meta`
  before treating an empty `search` result as proof of absence.
- **"When did X change", "who changed X"** — use `git log` / `git blame`.

When MCP disagrees with the open file, the file wins; report the
disagreement as evidence of staleness, not as a contradiction.
```

### Patch B — Recovery playbook (table rows + note placement)

1. At the **end** of the *Recovery playbook* markdown table, **after** the row
   that begins with `| Need ontology / rebuild / PR analysis |`, append these
   **two** rows (preserve table column structure):

```markdown
| Result disagrees with the open file | Index is stale (typical after `increment`-only catch-up) | Trust the file. Confirm staleness with `java-codebase-rag meta` (last `reprocess` time). Report as staleness, not contradiction. |
| Empty `search` result on a string you can read in the open file | Project not indexed, wrong `table` (try `all`), or chunking missed it | Try `find(kind=symbol, filter={"fqn_prefix": …})`. Fall back to `rg` in the project tree if still empty. |
```

2. Keep the following line **verbatim** (do not remove or edit):

`After two failed attempts on the same intent, stop and report tool name, args, and response.`

3. **Immediately after** that line, append this paragraph:

```markdown
**Staleness rule:** after `java-codebase-rag increment`, Lance is fresh
but Kuzu may be stale (see `propose/TIER2-INCREMENTAL-REBUILD-PROPOSE.md`).
A graph older than the source tree is normal mid-development. When in
doubt, run `meta` and compare against your working tree.
```

### Patch C — `neighbors` subsection after Batching

Under `#### neighbors`, **immediately after** the bullet that begins with
`**Batching:**`, insert:

```markdown
- **Confidence:** Cross-service edges (`HTTP_CALLS`, `ASYNC_CALLS`)
  carry confidence, strategy, and match metadata on `edge.attrs`
  (`attrs.confidence`, `attrs.strategy`, `attrs.match`). Low
  confidence means the resolver had to guess at the route binding —
  treat it as a **resolver gap signal**, not a hallucination. Report
  low-confidence edges with their confidence value, not as facts.
  Intra-service edges (`CALLS`, `INJECTS`, `IMPLEMENTS`, `EXTENDS`,
  `DECLARES`, `DECLARES_CLIENT`, `EXPOSES`) faithfully represent
  the static graph; the resolved set is still a **lower bound** under
  reflection / dynamic dispatch (see *What this MCP is NOT*).
```

### Global invariants

- Total net addition across all patches: **≤ 60 lines**.
- Leave unchanged load-bearing cardinals (stable anchors to spot-check, not
  exhaustive): heading `### Tool reference — four tools`, phrase `nine edge types`,
  heading `### Ontology glossary (version 11)` — do not rephrase or renumber.
- Marker comment lines: unchanged text (only their line numbers may shift).

## Out of scope (do NOT touch)

- Any file other than `docs/AGENT-GUIDE.md`.
- README, propose, plans, code, tests.
- New top-level sections, exploration strategy, mission catalogues.
- Rewording Patch A/B/C beyond typographic fixes required by markdown rendering
  (prefer zero).

## Deliverables

1. `docs/AGENT-GUIDE.md` with Patches A, B, C applied per above.
2. PR description includes: scope, link to plan + propose, **0 new tests**,
   pasted **Manual evidence** output (see below).

## Tests

- **New tests:** 0.
- Gate:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -q
```

## Sentinel grep (scope + acceptance)

**Scope — diff must be single-file:**

```bash
git diff master..HEAD --name-only
```

**Expected:** exactly `docs/AGENT-GUIDE.md`.

**Acceptance — run and paste output into the PR description:**

```bash
rg -n "What this MCP is NOT" docs/AGENT-GUIDE.md
rg -n "After two failed attempts|Staleness rule" docs/AGENT-GUIDE.md
rg -n '^- \*\*Confidence:\*\*' docs/AGENT-GUIDE.md
rg -n "<!-- BEGIN java-codebase-rag MCP guide -->" docs/AGENT-GUIDE.md
rg -n "<!-- END java-codebase-rag MCP guide -->" docs/AGENT-GUIDE.md
rg -n "Tool reference — four tools" docs/AGENT-GUIDE.md
rg -n "nine edge types" docs/AGENT-GUIDE.md
rg -n "Ontology glossary (version 11)" docs/AGENT-GUIDE.md
```

From the `After two failed attempts|Staleness rule` output, confirm the
**`Staleness rule` line number is greater than** the `After two failed attempts`
line number (staleness paragraph must not sit immediately under the table).

Sanity: markers still bracket the new content (visual review: Patch A/C sit
between BEGIN and END).

**Line budget:**

```bash
git diff master..HEAD --stat docs/AGENT-GUIDE.md
```

Reviewer judgment: approximate **≤ 60 lines** added total.

## Manual evidence (paste in PR description)

Use the **Acceptance** command block above verbatim; include its `rg` hits
(show that each pattern matches expected lines).

## Definition of done

- All three patches present; Patch B row order: new rows inside table →
  unchanged `After two failed attempts…` → **Staleness rule** paragraph.
- Sentinel `git diff master..HEAD --name-only` shows only `docs/AGENT-GUIDE.md`.
- `ruff` + `pytest` gate green; **0** new tests.
````

---

## Post-rollout (human or chore PR)

After PR-AGP-2 merges:

1. Move `propose/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md` →
   `propose/completed/AGENT-GUIDE-SURGICAL-PATCHES-PROPOSE.md`.
2. Move `plans/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md` →
   `plans/completed/PLAN-AGENT-GUIDE-SURGICAL-PATCHES.md`.
3. Update this file's **Status** line to **completed** and fix any relative links
   if paths change.
