# Plan: brownfield role/capability overrides — design-fix changelog

Status: **applied**. Companion document to
`PLAN-BROWNFIELD-ROLE-OVERRIDES.md`.

## Why this file exists

The brownfield plan grew through two review rounds; the second review
(`reports/review/active/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-issues.md`)
flagged design issues that were folded back into the plan in-place.
Once they're inlined, they stop standing out — but they are exactly
the parts an implementer is most likely to skim past or get wrong,
because they encode constraints that aren't obvious from a casual
read of "just resolve overrides at enrich time".

This changelog lists every design fix applied to the plan so the
implementer can read them as a bounded checklist instead of
diffing the plan against history.

If anything below contradicts the plan, the plan is authoritative.
This file is a navigation aid, not a spec.

---

## Fix index

| #  | Source                                  | Severity | Where in plan                                                  |
|----|-----------------------------------------|----------|----------------------------------------------------------------|
| 1  | Reviewer Issue 1                        | High     | Phase 2 → "Single source of truth"                             |
| 2  | Reviewer Issue 2                        | Medium   | Phase 2 → "Implementation" → `_build_meta_chain`               |
| 3  | Reviewer Issue 3                        | Low–Med  | Pre-flight fix → test 5 (tiered)                               |
| 4  | Reviewer Issue 4                        | Low      | "Resolver execution order" + resolver docstring + step comments |
| 5  | Reviewer Issue 5                        | Low      | Phase 2 → "Limitations" (item 1)                               |
| 6  | (added) Determinism of first-seen-wins  | Low      | Phase 2 → "Single source of truth" — sorted iteration          |
| 7  | (added) Incremental-indexing staleness  | Low–Med  | Phase 2 → "Limitations" (item 2)                               |

Reviewer Issue 6 (single PR vs. three) is intentionally **not**
applied; the plan keeps the three-PR rollout. See note at the bottom.

---

## Fix 1 — Single canonical `meta_chain` builder

**Problem.** The original plan implied that `build_ast_graph.py` (Kuzu
writer) and `graph_enrich.py::enrich_chunk` (LanceDB writer) each
build `meta_chain` from their own filesystem walk. Two independent
walks can disagree on which `@interface` declarations exist
(different exclusion patterns, different parse-failure handling,
different file order), producing inconsistent roles for the same
class in Kuzu vs. LanceDB. The bug is hard to attribute because the
symptoms surface across two different MCP tools.

**Fix in plan.** New subsection *Single source of truth (REQUIRED —
read before implementation)* in Phase 2.

**What the implementer must do**:

- Put one function `collect_annotation_meta_chain(project_root_str,
  *, max_depth=4) -> dict[str, frozenset[str]]` in `graph_enrich.py`
  (or a sibling shared module).
- `build_ast_graph.py` and `enrich_chunk` BOTH call this function;
  neither re-walks the filesystem for annotation declarations.
- Cache it with `@lru_cache(maxsize=4)` keyed on `project_root_str`
  to match the existing `_load_brownfield_overrides` pattern.
- Match `build_ast_graph.py`'s pass-1 exclusion rules exactly
  (`target/`, `build/`, `out/`, `.git/`, hidden dirs, plus the
  project's existing skip rules).
- On parse error, skip the file with a stderr warning. Don't let one
  bad file silently shift the chain.

**Acceptance check.** A test that registers two distinct project
roots in one process and verifies the cache produces independent
chains; reusing the same root returns the cached `frozenset`.

---

## Fix 2 — Iterative fixed-point closure replaces recursive
`_resolve_meta_chain`

**Problem.** The original recursive sketch conflated cycle detection
(`seen` set) with depth bounding (`len(seen) > 4`). On a branching
DAG, sibling iteration order could non-deterministically trip the
depth cap before reaching legitimate built-ins. The metric being
bounded (hops from start name) is not the same as the metric being
incremented (number of distinct names visited).

**Fix in plan.** Replaced the recursive sketch with
`_build_meta_chain(decls, builtins, *, max_depth=4)`: an iterative
monotone-update closure that runs at most `max_depth` rounds, with
a `changed` early-exit. Cycle safety follows from the monotone
invariant; no `seen` set required.

**What the implementer must do**:

- Drop the recursive sketch; do not implement it.
- Implement the iterative version exactly as shown in the plan
  (Phase 2 → "Implementation" → "Pass A2 — transitive closure").
- Tests must assert the closure terminates on cyclic
  meta-annotation graphs (`@A` meta-annotated with `@B` meta-annotated
  with `@A`) without raising and without infinite-looping.

**Acceptance check.** Unit test on `_build_meta_chain` with a
synthetic cycle and a 6-deep linear chain (with `max_depth=4`,
the leaf should resolve to nothing — that's the correct behaviour
of a hard cap, not a bug).

---

## Fix 3 — Tiered tests instead of one heavy test

**Problem.** The original "test 9" of the pre-flight section spun up a
full LanceDB index just to assert that
`JavaLanceChunk.capabilities` survives the write path. That mixed
"is this column declared" with "does the LanceDB list-predicate work
end-to-end". Per-PR CI shouldn't pay for the heavy half on every
commit, but the cheap half is exactly what catches the
silent-data-drop regression that motivated the pre-flight in the
first place.

**Fix in plan.** Pre-flight section, test 5 (the LanceDB write-path
test) is now three independent tiers:

- **Tier 1 — schema (every PR)**: pure Python introspection of
  `JavaLanceChunk`; assert `capabilities` is the same Arrow type as
  `annotations_on_type` / `symbols`.
- **Tier 2 — write-path unit (every PR)**: invoke
  `process_java_file` on an in-memory fixture; assert the produced
  row carries `capabilities == [...]`.
- **Tier 3 — end-to-end (optional/nightly, gated)**: full LanceDB
  index over a fixture with config-driven capabilities; query the
  table directly AND via `codebase_search(capability="...")`.

**What the implementer must do**:

- Author all three tiers but mark Tier 3 with a pytest marker so it
  can be skipped per-PR. Tier 1 and Tier 2 must run on every PR.
- Tier 2 is the smallest test that proves the write-path is wired
  correctly; do not skip it in favour of Tier 3.

---

## Fix 4 — Precedence presentation collapsed into one ordered list

**Problem.** The previous plan presented the resolver order as two
parallel lists: "priority order, highest first" (1 → 5) and
"execution order, reverse of priority" (5 → 1). Readers had to
mentally invert one to verify the other. Both lists encoded the same
information.

**Fix in plan.** Replaced the dual presentation with a single
section, `## Resolver execution order (final, single source of
truth)`. The list is ordered top-to-bottom by code execution; both
role and capability halves are shown side-by-side; "priority" is
defined as "position in this list, last to fire = highest". The
docstring of `resolve_role_and_capabilities` and the inline `# -----
Step N` comments were updated to drop the contradictory
`(priority N)` annotations and to point at the canonical section.

**What the implementer must do**:

- When implementing `resolve_role_and_capabilities`, follow the
  step-numbered code skeleton in the plan exactly. Do not invent a
  new ordering based on "logical priority" reasoning — the
  execution order is the contract.
- Comment headers in the resolver should reference the canonical
  section (`see §"Resolver execution order"`) rather than restating
  priority numbers, to avoid the same confusion drifting back in.

**Why B (Layer B annotations) runs before A (Layer A meta-walk)
matters:** if A ran first, then B's `if role == "OTHER"` guard would
already see a non-OTHER role and silently never fire — demoting
explicit user config below auto-inference. The plan's Phase 2 test
#7 is the regression guard for this exact ordering.

---

## Fix 5 — "Duplicate `@interface` simple names" limitation
documented

**Problem.** Layer A keys its meta-chain by **simple name** of the
annotation, because the AST does not always carry import-resolved
FQNs at usage sites. If two distinct annotation types in different
packages share the same simple name (e.g. `com.team1.AcmeService` and
`com.team2.AcmeService`), only one of them contributes to
`meta_chain`. This is a real design limitation, not a bug.

**Fix in plan.** Phase 2 → new `### Limitations (callout — must be in
user-facing docs)` section, item 1.

**What the implementer must do**:

- Reproduce the "Limitations" section verbatim in `README.md`'s
  brownfield section as part of Phase 2 documentation.
- Decide first-seen-wins **after sorted iteration** (see Fix 6) so
  the choice is deterministic, not filesystem-walk-dependent.
- Emit a stderr warning when the collision is detected, naming both
  FQNs, so users have something concrete to grep for.

---

## Fix 6 — Determinism of first-seen-wins via sorted iteration
(reviewer-missed)

**Problem.** "First-seen-wins" was specified as a tie-break rule in the
original plan, but the rule is meaningless if file iteration order is
filesystem-dependent. Two developers running the same indexer on the
same project on different OSes (or even the same OS at different
times) could end up with different `meta_chain` contents, and
therefore different resolved roles for the affected classes.

**Fix in plan.** Phase 2 → "Single source of truth" mandates
`sorted(root.rglob("*.java"))` for the canonical builder's file scan
and explicitly notes "first encountered AFTER sorted iteration".

**What the implementer must do**:

- Use `sorted(...)` on the filesystem walk inside
  `collect_annotation_meta_chain`. Don't rely on `os.walk` /
  `Path.rglob` natural order.
- Add a unit test that runs the builder twice on the same fixture
  and asserts `chain == chain_again` (cheap regression for
  determinism).

---

## Fix 7 — Incremental-indexing cache staleness limitation
(reviewer-missed)

**Problem.** CocoIndex re-processes only changed files. If the changed
file is an `@interface` declaration (e.g. user removes a `@Service`
meta-annotation from `@AcmeService`), every class previously
annotated with `@AcmeService` would need re-enrichment to update
its resolved role. The plan does not track this dependency.

**Fix in plan.** Phase 2 → "Limitations" item 2 documents the
constraint and the workaround: run a full
`refresh_code_index(confirm=true)` after editing any annotation
declaration.

**What the implementer must do**:

- Add a "When to do a full rebuild" note to the README's
  capabilities/brownfield section pointing at this constraint.
- Do **not** attempt to track the dependency automatically. A future
  enhancement could persist the chain hash and force re-enrichment
  of dependents on change; explicitly out of scope here.

---

## Reviewer Issue 6 (rollout) — intentionally not applied

The reviewer suggested merging the three-PR rollout into one. The
plan keeps the original three independent PRs (Phase 1 → Phase 2 →
Phase 3) because:

- Each phase is independently functional and shippable.
- Phase 1 is the brownfield MVP; Phase 2 and Phase 3 are
  enhancements that some users won't need.
- A single merged PR forces the implementer to land annotation-walk
  + custom-annotation contract before any of it has been validated
  in real codebases.

If the implementer disagrees and wants to consolidate, that decision
is allowed — but the test plan must still verify each phase's
behaviour independently (i.e. with Phase 2/3 features disabled by
config), so the per-phase regression guards survive.

---

## How this list maps onto code

The implementer should be able to land all fixes in a single pass
through the plan. The touchpoints are:

- `graph_enrich.py` — new `collect_annotation_meta_chain` (Fix 1, 2,
  6); `enrich_chunk` consumes it; `resolve_role_and_capabilities`
  step skeleton (Fix 4).
- `build_ast_graph.py` — pass 1 calls `collect_annotation_meta_chain`
  (Fix 1) instead of doing its own walk; resolver wiring unchanged.
- `java_index_flow_lancedb.py` — pre-flight write-path fix (already
  in plan; Fix 3 only changes how it's tested).
- `tests/` — three new pre-flight tiers (Fix 3); determinism test
  (Fix 6); cyclic-graph and depth-cap tests for `_build_meta_chain`
  (Fix 2).
- `README.md` — Limitations callout reproduction (Fix 5, Fix 7).
