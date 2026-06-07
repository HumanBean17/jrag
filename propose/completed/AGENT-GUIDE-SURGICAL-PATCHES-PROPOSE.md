# AGENT-GUIDE-SURGICAL-PATCHES — three small additions to docs/AGENT-GUIDE.md

**Status**: completed — shipped (PR-AGP-1 → PR-AGP-2; patches A–C in `docs/AGENT-GUIDE.md`).
**Author**: Dmitriy Teriaev + Perplexity Computer
**Date**: 2026-05-11

## TL;DR

- Today's `docs/AGENT-GUIDE.md` is a strong operating manual for the four MCP tools but it does not inoculate the agent against three recurring failure modes: treating empty results as ground truth, treating MCP as exhaustive across reflection / unindexed code / build files, and over-trusting a stale graph after `increment`.
- Add **three surgical patches** to AGENT-GUIDE.md, total ≤ 60 lines: a *"What this MCP is NOT"* subsection, a *"Staleness & fallback"* paragraph, and a *"Confidence calibration on cross-service edges"* paragraph.
- All three patches land **inside** the `<!-- BEGIN java-codebase-rag MCP guide -->` … `<!-- END java-codebase-rag MCP guide -->` markers so the drop-in `CLAUDE.md` / `AGENTS.md` block stays self-contained.
- **Scope is hard-bounded.** No restructure, no new sections at the top level, no ontology bump, no cardinal-number changes ("four MCP navigation tools" stays exact). Only insertions inside existing slots.
- This propose is the companion to [`EXPLORATION-SKILL-PROPOSE.md`](./EXPLORATION-SKILL-PROPOSE.md) (the standalone `java-codebase-explore` skill). The split is intentional: AGENT-GUIDE.md stays as the operating manual; the new skill is the strategy guide. These patches are the **minimum** the manual needs even if the skill never ships.
- **Migration shape**: **2 PRs** — propose merge → AGENT-GUIDE.md patch. No code changes. No schema changes. No ontology bump.

---

## §1 — Frame: what is AGENT-GUIDE.md, really?

`docs/AGENT-GUIDE.md` is **a drop-in operating manual that weak and mid-tier coding agents copy into their `CLAUDE.md` / `AGENTS.md` to stay on the rails when calling the MCP.** Its job is to keep agents from calling the wrong tool, omitting required arguments, passing stringified JSON, or fishing with `search` when the graph answers exactly.

It is **not** a tutorial, not a tour, not a strategy guide. The forthcoming `java-codebase-explore` skill is the strategy guide; this propose deliberately does not turn AGENT-GUIDE.md into that.

The frame for this propose: **the smallest set of additions that close the three highest-leverage failure modes weak agents still hit, given the existing scope of the manual.** Anything that requires restructure or new top-level sections belongs in the exploration skill, not here.

This frame rules out:

- Adding mission catalogues, exploration sequences, or system-level guidance.
- Reordering or renaming existing sections.
- Bumping cardinal numbers ("four MCP navigation tools", "nine edge types", "ontology version 11" all stay exact).
- Expanding the recovery playbook beyond a column-add or row-add.
- Inlining anything from the future `java-codebase-explore` skill.

## §2 — Design principles

1. **Minimum-viable patch.** Each of the three patches is ≤ 25 lines. Total addition budget: 60 lines. If a patch grows past that, it belongs in the exploration skill.
2. **Inside the markers, every time.** All three patches land inside `<!-- BEGIN java-codebase-rag MCP guide -->` … `<!-- END java-codebase-rag MCP guide -->` so downstream `CLAUDE.md` blocks pick them up on the next pull.
3. **No structural change.** Insertion only; no section reorder, no rename. Maintenance friction stays at "pull a fresh block".
4. **Cardinal numbers untouched.** Existing counts ("four tools", "nine edges") are load-bearing for the rest of the doc and the ontology-bump checklist. Patches must not introduce a new counted thing.
5. **Tone match.** Existing AGENT-GUIDE.md is terse, table-heavy, second-person imperative. Patches must match — no soft language, no "you might want to consider".
6. **No duplication with the future exploration skill.** Where the exploration skill will have a richer treatment (e.g. anti-capabilities), this propose ships the **shortest defensible version** that the operating manual cannot do without. The skill expands; the manual states.

## §3 — The proposed surface

Three patches. Each lands at an explicit **heading / anchor** in `docs/AGENT-GUIDE.md` (do not rely on stale line numbers; re-locate by search).

### Patch A — *"What this MCP is NOT"* subsection

**Insertion point**: between the existing **"Do NOT use this MCP when…"** paragraph and the **"Workflow (GPS model)"** heading.

**Content** (≤ 12 lines):

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

**Why the placement**: it sits exactly where the existing "do NOT use" framing already lives, extending the same thought rather than introducing a new structural section.

### Patch B — *"Staleness & fallback"* (recovery rows + staleness note)

**Canonical block order (single source of truth):**

1. Append **two new rows** at the **end** of the *Recovery playbook* markdown table (after the last pre-patch row).
2. The table is followed by a blank line, then the existing sentence **unchanged** (this propose does not edit it):

   `After two failed attempts on the same intent, stop and report tool name, args, and response.`

3. Append the staleness paragraph (the block that begins with `**Staleness rule:**`) on the lines **immediately after** that sentence (not between the last table row and that sentence).

There is **no** “staleness paragraph directly under the table” placement: the `After two failed attempts…` line stays between the table and the staleness paragraph.

**Content** (≤ 8 lines added to existing table + 4-line note):

New rows (step 1):

```markdown
| Result disagrees with the open file | Index is stale (typical after `increment`-only catch-up) | Trust the file. Confirm staleness with `java-codebase-rag meta` (last `reprocess` time). Report as staleness, not contradiction. |
| Empty `search` result on a string you can read in the open file | Project not indexed, wrong `table` (try `all`), or chunking missed it | Try `find(kind=symbol, filter={"fqn_prefix": …})`. Fall back to `rg` in the project tree if still empty. |
```

Staleness paragraph (step 3, after the `After two failed attempts…` line):

```markdown
**Staleness rule:** after `java-codebase-rag increment`, Lance is fresh
but Kuzu may be stale. A graph older than the source tree is normal
mid-development. When in doubt, run `meta` and compare against your
working tree.
```

**Why the placement**: the recovery playbook is where agents land after a confusing result; the staleness note reads as a follow-on to the existing “stop thrashing” sentence, not as a stray table footer.

### Patch C — *"Confidence calibration on cross-service edges"* paragraph

**Insertion point**: end of the **"Tool reference — four tools"** section, inside the **`neighbors`** subsection, immediately after the **Batching** bullet.

**Content** (≤ 8 lines):

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

**Why the placement**: this is a `neighbors`-specific concern (confidence lives on edge attributes returned by `neighbors`), so it belongs in the `neighbors` subsection, not as a standalone section.

### 3.4 What is explicitly unchanged

- Section headings: unchanged.
- "Four MCP navigation tools": unchanged.
- "Nine edge types" table: unchanged.
- Ontology version 11 sentence: unchanged.
- Slash aliases section: unchanged.
- The footer "Maintenance notes" block: unchanged (this propose does not add a new maintenance invariant; the existing one already covers MCP-behaviour bumps).
- The marker pair `<!-- BEGIN java-codebase-rag MCP guide -->` / `<!-- END java-codebase-rag MCP guide -->`: position unchanged. All three patches land **inside** the markers.

## §4 — Use-case re-walk

Walking 16 realistic situations through the patched AGENT-GUIDE.md to confirm the patches are weight-bearing and the rest of the doc still works.

| # | Situation | Pre-patch behaviour | Post-patch behaviour |
|---|---|---|---|
| UC1 | Agent runs `search("LegacyClient")`, gets 0 hits, concludes "doesn't exist" | Wrong; doc has no rule against it | Patch A "treating an empty `search` result as proof of absence" rule triggers fallback to `rg` |
| UC2 | Agent calls `neighbors(in, [CALLS])` on a reflection-heavy class; sees 1 caller; reports "only one caller" | Wrong; doc doesn't warn about reflection | Patch A "static method calls only; lower bound" rule triggers caveat in the agent's report |
| UC3 | Agent sees an `HTTP_CALLS` edge with `confidence=0.4`; reports it as a known cross-service call | Wrong | Patch C triggers explicit reporting of confidence value |
| UC4 | User asks "how do I run the tests?" — agent calls `search("test")` | Wrong tool selection | Patch A "read README / build files directly" triggers fallback |
| UC5 | After `java-codebase-rag increment`, agent calls `neighbors`; result disagrees with the open file; agent rewrites the file based on MCP | Wrong; doc doesn't say which to trust | Patch B "the file wins; report as staleness" triggers correct call |
| UC6 | Agent calls `find` on a service that was never `init`'d; gets empty; reports "service doesn't exist" | Wrong | Patch A "Unindexed services / repos — verify with `meta`" triggers verification |
| UC7 | Agent encounters a stringified-JSON `edge_types` error | Already handled by existing "Argument shapes — JSON, not stringified JSON" | Unchanged — patches don't touch this |
| UC8 | Agent omits `direction` on `neighbors` | Already handled by existing recovery playbook row | Unchanged |
| UC9 | Agent calls `search` for "who calls Foo#bar" instead of `find` + `neighbors` | Already handled by existing "Graph beats vector for exact structural questions" rule of thumb | Unchanged |
| UC10 | Agent reports an `ASYNC_CALLS` edge with `confidence=0.95` as certain | Correct under both pre- and post-patch | Unchanged; high confidence is reported as is |
| UC11 | Agent asks "when was this method added?" via MCP | Wrong tool choice | Patch A "use `git log` / `git blame`" triggers correct fallback |
| UC12 | Agent loops `neighbors(in, [CALLS])` for 10 hops without a stopping criterion | Already handled by existing "Stop when you can answer; do not prefetch unrelated subgraphs" | Unchanged (this is exploration-strategy content; belongs in the future skill) |
| UC13 | Agent picks `search` for a question already answered in the open file | Already handled by "Do NOT use this MCP when the answer is already in the open file" | Unchanged |
| UC14 | Agent fishing-trip `search` with a long natural language sentence | Partially handled by existing "Tip: For behaviour questions, narrow noise" | Unchanged (the exploration skill expands this) |
| UC15 | Agent treats `find` empty result on `target_service:"unknown"` as "no clients call this service" | Wrong | Patch A "Unindexed services" + Patch B "fall back to `rg`" both trigger |
| UC16 | Agent reads `edge.attrs.match` and asks "what does this mean?" | Doc references "VALID_HTTP_CALL_MATCHES" but doesn't explain how to use match in reporting | Patch C adds: confidence, strategy, **and match** on `attrs` are first-class to report |

**Result of the re-walk:**

- **Eight cases** (UC1, UC2, UC3, UC4, UC5, UC6, UC11, UC15) are **new wins** — wrong behaviour before, correct after at least one of the patches.
- One case (UC16) is a partial win — the reporting habit improves, the deep meaning of `match` still lives in `java_ontology.py`.
- The rest are unchanged. None are made worse by the patches. None require a 4th patch.
- Two cases (UC12, UC14) are deliberately out of scope and remain pointers to the future exploration skill.

No surface revisions triggered.

## §5 — What this deliberately does NOT do

| Question / feature | Why we skip it |
| ------------------ | -------------- |
| Add a "Missions" / "Exploration strategy" section | Belongs in `java-codebase-explore` skill, not the operating manual. |
| Restructure or rename sections | Hard scope boundary — drop-in `CLAUDE.md` block must keep its shape. |
| Add new edge types or change taxonomy | Out of scope; ontology bumps are a separate process. |
| Add a new top-level section for "Anti-capabilities" | Patch A is a subsection slotted into existing flow; promoting it is a restructure. |
| Add per-mission canonical sequences | Belongs in the exploration skill. |
| Add a "How to read the open file vs MCP" tutorial | Patch B states the rule; tutorial belongs in the skill. |
| Update README in lockstep | Out of scope for this propose; the existing maintenance note already covers README parity for ontology bumps and these patches don't bump the ontology. README does mention staleness today; if the language drifts during implementation, a follow-up PR can sync it, but it's not required for this round. |
| Translate to Russian | AGENT-GUIDE.md is English-only by existing convention. |

## §6 — Migration plan — 2 PRs

### PR-AGP-1 — propose merge

**Title**: `propose: surgical patches to docs/AGENT-GUIDE.md`
**Purpose**: this document. Lock the three patches.
**Tests**: none (doc-only).

### PR-AGP-2 — apply the patches

**Title**: `docs(agent-guide): out-of-frame limits, staleness, neighbor edge confidence`
**Purpose**: apply Patches A, B, C inside the marker block.
**Tests**: none. Acceptance check (by hand in the PR description): `rg` (or equivalent) shows the new `### What this MCP is NOT` subsection, the two new recovery-table rows, the `**Staleness rule:**` paragraph **after** the unchanged `After two failed attempts…` line, and the new `**Confidence:**` bullet under `neighbors`; line-count diff confirms the doc grew by ≤ 60 lines; marker comments unchanged; load-bearing cardinals unchanged (`nine edge types`, ontology glossary still anchored on version **11**, and the intro still states **four** MCP navigation tools in the existing wording). All checks run by hand in the PR description.

Total: 2 PRs.

## §7 — Decisions taken (no longer open)

1. **Three patches, no more.** Out-of-frame limits (Patch A), staleness & fallback (Patch B), neighbor-edge confidence reporting (Patch C). Anything else belongs in the exploration skill.
2. **All patches inside the BEGIN/END `java-codebase-rag MCP guide` marker comments.** Drop-in block stays self-contained.
3. **Insertion only, no restructure.** Headings, ordering, and section count remain unchanged.
4. **Total addition budget ≤ 60 lines.** Hard cap. If a patch grows during implementation, defer to the exploration skill.
5. **Cardinal numbers in the doc remain frozen.** "Four MCP navigation tools", "nine edge types", "ontology version 11" all stay exact strings.
6. **No README change in this propose.** README parity is handled by the existing maintenance invariant if behaviour shifts during implementation.
7. **Patch C lives inside the `neighbors` subsection, not as a standalone section.** Confidence is a `neighbors` concern; promoting it to standalone is a restructure.
8. **Patch B adds two recovery rows + one note paragraph.** Not a new section.
9. **English only.** No translation in scope.
10. **These patches ship even if `java-codebase-explore` skill never ships.** They are the operating-manual minimum.

## §8 — Risks and how we mitigate

| Risk | Mitigation |
| ---- | ---------- |
| Patches drift into strategy guidance during implementation | Hard line budget (60 lines); reviewer rejects on overflow. |
| Patch C duplicates content with the eventual `java-codebase-explore` skill cheat sheet | Cheat sheet inlines the 9 edge types only; confidence reporting habit lives only in AGENT-GUIDE.md. Drift risk is low. |
| Downstream `CLAUDE.md` consumers don't re-pull the block | Existing maintenance note already says "Update by re-pulling from this repo when the ontology bumps" — call out the patches in the release commit message so consumers know. |
| A future patch makes the doc longer than is healthy | Hard line cap and a propose-required policy for further additions (mirrors CLI-SCENARIOS §6 discipline). |
| The marker pair drifts during the patch | PR acceptance check explicitly greps for the unchanged marker strings and validates the bounded range. |
| Patch B's `meta` reference becomes stale if the CLI is renamed | Cross-reference uses the canonical CLI name `java-codebase-rag meta`; CLI renames already trigger doc-wide updates by the existing maintenance note. |

## Appendix A — Verbatim insertions (implementation order **A → B → C**)

For the implementation PR, apply patches in **Patch A → Patch B → Patch C**
order (same as §3). This appendix lists **verbatim** insertion blocks in that
order. It is **not** a unified-diff file order: Patch B’s staleness paragraph
is **after** the existing `After two failed attempts…` sentence (see §3 Patch B
canonical block order).

### A.1 — Patch A (after “Do NOT use this MCP when…”, before `**Workflow (GPS model):**`)

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

### A.2 — Patch B part 1 (two new rows at end of Recovery playbook table)

```markdown
| Result disagrees with the open file | Index is stale (typical after `increment`-only catch-up) | Trust the file. Confirm staleness with `java-codebase-rag meta` (last `reprocess` time). Report as staleness, not contradiction. |
| Empty `search` result on a string you can read in the open file | Project not indexed, wrong `table` (try `all`), or chunking missed it | Try `find(kind=symbol, filter={"fqn_prefix": …})`. Fall back to `rg` in the project tree if still empty. |
```

(Context: the pre-existing line `After two failed attempts on the same intent, stop and report tool name, args, and response.` stays **unchanged** immediately after the table.)

### A.3 — Patch B part 2 (after `After two failed attempts…`)

```markdown
**Staleness rule:** after `java-codebase-rag increment`, Lance is fresh
but Kuzu may be stale. A graph older than the source tree is normal
mid-development. When in doubt, run `meta` and compare against your
working tree.
```

### A.4 — Patch C (after `**Batching:**` under `#### neighbors`)

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

Net additions: ~48 lines. Under the 60-line budget.
