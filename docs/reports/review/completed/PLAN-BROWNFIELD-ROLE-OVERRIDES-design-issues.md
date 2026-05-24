# Design issues: PLAN-BROWNFIELD-ROLE-OVERRIDES (plan / specification)

**Plan file:** `plans/todo/PLAN-BROWNFIELD-ROLE-OVERRIDES.md`  
**Review date:** 2026-04-26  
**Scope:** Problems, ambiguities, or gaps in the *written plan* (not the codebase).

---

## 1. Dual pipeline for meta-annotation data (spec gap)

The plan describes building Layer A (meta-annotation reachability) from a two-pass process anchored in `build_ast_graph.py` and `GraphTables`. The chunk-enrichment / Lance path must also apply the same resolution rules, but the plan does **not** require a single shared primitive for “which `@interface` definitions exist in the project.”

A careful reader can infer that graph build and index enrichment should agree, but two independent implementations (graph tables vs. a separate tree walk) are **not** ruled out. If file coverage, exclude patterns, or parse-failure handling differ, Lance and Kuzu can **disagree** on `meta_chain` for the same type. The plan would be stronger with an explicit constraint: e.g. “meta maps MUST be derived from the same file set and exclusion rules as `build_ast_graph` pass1,” or “Lance and Kuzu MUST share one builder function.”

---

## 2. Depth cap for meta-annotation resolution is under-specified

The plan gives a sketch of `_resolve_meta_chain` with `len(seen) > 4` and cycle handling. As written, the `seen` set is used both for **cycle** detection and as a stand-in for **path depth**. On a *linear* chain of meta-annotations, set size tracks depth. On **branching** shapes, set cardinality and “steps from root” diverge, so the sketch does not define a single clear semantics (strict path depth vs. global visit count).

The follow-up test (“six wrappers → `OTHER`”) depends on a precise cap. The plan should name the exact metric (e.g. maximum path length from the start simple name) and the integer bound, so implementers and tests are aligned.

---

## 3. Pre-flight test 9 mixes “unit” and “integration” scope

The pre-flight item asks for a “unit-style” regression but specifies: build a **fresh** Lance index with FQN overrides, **query the table directly**, and then run **`codebase_search(..., capability=...)`** end to end. That is a **multi-layer** test (indexer + storage + search API) and is expensive to run and to keep stable in CI.

A tiered requirement would match intent better: (1) schema / `JavaLanceChunk` field, (2) `process_java_file` row, (3) optional full search. As written, teams may either skip the heavy part or over-invest in flaky integration for what is mainly a **write-path** contract.

---

## 4. “Precedence” vs. “execution order” is correct but error-prone to skim

The plan is internally consistent: execution order is the *reverse* of listed priority, and guards use the **current** `role` after each step. Still, a reader who only scans the “Precedence summary (final)” table may implement **C before FQN** in the wrong direction or mis-order **B vs. A** without reading the “Execution order in code (REQUIRED)” block.

This is a **documentation hazard** in the spec, not a logic error. A short, single bullet at the top (“Apply steps in *only* the order: …; do not reorder”) or a Mermaid sequence diagram would reduce mis-implementation.

---

## 5. Layer A duplicate `@interface` simple names

The plan correctly specifies first-seen-wins and a stderr warning. The **implication** (colliding simple names in different packages map to one `meta_chain` entry) is only obvious if you already know Java’s annotation resolution limits in this indexer. A one-line “Limitation:” callout in the plan would set expectations for monorepos with same-named annotations.

---

## 6. Rollout vs. single document

The plan says three independent PRs (Phase 1 → 2 → 3) while also presenting all phases in one file. That is fine for a complete picture, but the **merge strategy** (squashed single PR vs. three) is a process choice the plan does not need to fix—only note that “shippable phases” and “one landing” can conflict in review scope unless branches are cut accordingly.

---

## Summary

| ID | Topic                         | Severity (spec) |
|----|------------------------------|-----------------|
| 1  | Single source of truth for meta map inputs | High (consistency) |
| 2  | Depth / cycle semantics       | Medium          |
| 3  | Pre-flight test cost / tiers   | Low–medium      |
| 4  | Precedence skimming hazard    | Low             |
| 5  | Duplicate simple-name limits  | Low             |
| 6  | Multi-PR vs one doc            | Process only    |
