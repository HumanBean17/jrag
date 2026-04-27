# Implementation issues: PLAN-BROWNFIELD-ROLE-OVERRIDES

**Plan file:** `plans/todo/PLAN-BROWNFIELD-ROLE-OVERRIDES.md`  
**Review date:** 2026-04-26  
**Scope:** Gaps, mistakes, and risks in the *implementation* as compared to the plan’s stated behaviour and test matrix. Code and tests are assumed to live under `mcp_lancedb_bundle/`.

**Tests run:** `pytest tests/test_brownfield_overrides.py` — 20 passed (at time of review).

---

## 1. Pre-flight LanceDB + search regression is incomplete vs. plan

The plan’s pre-flight test (item 9) requires, in order: build a **fresh** Lance index using the real pipeline with FQN `role`/`capabilities`, assert rows on **direct table** read, then **`codebase_search(..., capability=...)`** and assert the type is returned.

**Implemented approximations:**

- **`enrich_chunk` + YAML** — checks resolver + chunk path; does not exercise `process_java_file` / `JavaLanceChunk` materialisation end-to-end.
- **Raw LanceDB / PyArrow** — proves `list(string)` round-trip, not the CocoIndex / `JavaLanceChunk` row shape.
- **Dataclass introspection** — confirms `JavaLanceChunk` has a `capabilities` field only.

**Risk:** A regression that removes or mis-wires the CocoIndex write path could slip past the suite; `codebase_search` capability filtering is not covered by the brownfield tests.

**Severity:** Medium (guards are weaker than specified, not a known production bug in the reviewed snapshot).

---

## 2. “Malformed YAML” test does not use malformed YAML

The plan (Phase 1, test 8) calls for malformed YAML to yield empty overrides without crashing.

**Current behaviour:** a test exercises loading from a **non-existent** path, which is closer to “missing file” than “invalid YAML.” Invalid YAML in an existing file is only covered implicitly by the loader’s `except` branch, not by a named test.

**Follow-up:** add a `tmp_path` file with content that is not valid YAML, or rename the test to match “missing config / empty” semantics.

**Severity:** Low (behaviour likely correct; test is mis-specified or misnamed).

---

## 3. Phase 2 test matrix: gaps

The plan’s Phase 2 test list includes scenarios **not** present in `tests/test_brownfield_overrides.py` (at review time):

- **Cyclic** meta-annotation graph (A ↔ B): no crash, role remains `OTHER`.
- **Long chain** (e.g. six wrappers): after depth cap, role `OTHER` (or whatever the spec fixes).
- **FQN + meta + Layer B together:** FQN should still win; explicit per-class config overrides automatic meta and annotation maps.

**Covered and notable:** B-beats-A regression, two-hop to `SERVICE`, method-level meta to capability, basic `@Service` on custom `@interface`.

**Severity:** Medium for **cycle** and **depth** (guard against stack bugs and cap drift); **low** for the FQN interaction if hand-tested or covered elsewhere (not verified here).

---

## 4. Phase 3 test matrix: minor gaps

The plan asks for:

- **Additive capability** — `@CodebaseCapability` in addition to AST-inferred capabilities (e.g. alongside a Spring stereotype).
- **Two separate** `@CodebaseCapability` annotations on the same class, as well as the **container** form.

**Current coverage** focuses on `CodebaseRole` variants, invalid role warnings, and **`@CodebaseCapabilities({...})` container** with two inner values. The **stacked** `@CodebaseCapability` / `@CodebaseCapability` case is not clearly duplicated as a dedicated test; additive-on-AST is not isolated.

**Severity:** Low (behaviour is straightforward from code structure; risk is **regression** in parser or resolver order, not a known bug).

---

## 5. Possible Lance vs. Kuzu disagreement on meta maps

**Implementation detail:** the graph writer derives annotation declarations from **in-memory graph tables**; **`enrich_chunk`** builds meta from a **separate** full-disk walk (`_collect_annotation_decls_from_disk` + cache).

If the two ever differ (excludes, parse errors, or partial scans), the **same** Java type could get **different** Layer A results in Kuzu than on Lance chunks. The plan’s intent is consistency across stores; this is an **integration consistency** risk, not a single-file bug.

**Severity:** Low until observed in a real project; worth monitoring or converging the two inputs.

---

## 6. Depth cap semantics (implementation) vs. plan’s sketch

The resolver’s recursive walk uses a **path set** and stops when `len(path) > 4`. The plan’s pseudocode used a slightly different shape (`seen` and `len(seen) > 4`).

**Risk:** off-by-one vs. the plan’s “depth 4 / six links `OTHER`” without an automated test (see §3), so behaviour could drift in a refactor.

**Severity:** Low–medium, mitigated if Phase 2 depth test is added.

---

## 7. Kuzu member nodes and capabilities

`Symbol` rows for **methods** use `_node_row` defaults (`capabilities: []`, `role: "OTHER"`) and do not run the brownfield resolver per method. The plan is **type-centric**; this is not a plan violation, but any future expectation of “method symbol capabilities in the graph” would be unmet.

**Severity:** N/A for current plan; documentation only if users assume otherwise.

---

## Summary

| ID | Topic                                | Severity   |
|----|--------------------------------------|------------|
| 1  | Pre-flight E2E (index + search)      | Medium     |
| 2  | Malformed YAML test naming / body    | Low        |
| 3  | Phase 2: cycle, depth, FQN+meta tests| Medium (partial) |
| 4  | Phase 3: stacked caps + AST additive | Low        |
| 5  | Meta map source: graph vs. disk     | Low (consistency) |
| 6  | Depth cap without test               | Low–medium |
| 7  | Method `Symbol` rows / capabilities  | N/A        |

---

## What was in good shape (for balance)

- `BrownfieldOverrides` loader, validation against shared ontology, stderr warnings for unknowns.
- `resolve_role_and_capabilities` execution order and **B-before-A** semantics with **OTHER** guards; FQN and `@CodebaseRole` ordering relative to C.
- `AnnotationRef.arguments` and `CodebaseCapabilities` value extraction in `ast_java.py`.
- Wiring: `build_ast_graph` type nodes, `enrich_chunk`, `JavaLanceChunk` + `process_java_file` for `capabilities`.
- README, CODEBASE_REQUIREMENTS, and MCP `instructions` mention customisation.
- B-beats-A regression test is present (critical for the plan’s execution-order invariant).
