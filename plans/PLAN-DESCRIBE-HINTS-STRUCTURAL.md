# Plan: DESCRIBE-HINTS-STRUCTURAL (type wiring + method-body road signs)

Status: **active (planning)**. This plan implements
[`propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](../propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md)
(issue [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191) tier 3 tracks deferred rows; propose PR [#192](https://github.com/HumanBean17/java-enterprise-codebase-rag/pull/192)).

Depends on: **landed** — describe type rollups (`DECLARES.*` via members), override-axis hints (`OVERRIDDEN_BY.*`), stored `OVERRIDES`, v4 find strings `TPL_FIND_SUCCESS_HTTP_TARGETS` / `TPL_FIND_SUCCESS_ASYNC_TARGETS` ([`plans/completed/PLAN-HINTS-V4.md`](./completed/PLAN-HINTS-V4.md)). **Propose lock:** merge [#192](https://github.com/HumanBean17/java-enterprise-codebase-rag/pull/192) (propose-only) before implementation PR merge.

## Goal

- **Tier 1–2 describe hints:** When `describe` succeeds on a Symbol type, method, `Client`, or `Producer`, emit stored-edge road signs from `edge_summary` / `record.data` so agents chain `neighbors(...)` without re-deriving traversal from empty `hints`.
- **Control-flow fix:** Remove describe-branch **early returns** that block stacking (type rollups + tier-1 structural; client/producer declaring + **I/J**).
- **No graph/schema churn:** Query-time `mcp_hints.py` only; **no** `ontology_version` bump; **no** re-index.
- **Tier 3 stays deferred:** `CALLS.in` callers (**F**), `DECLARES.out` members (**K**), `EXTENDS` (**L**) remain in [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191) until a follow-up propose/plan.

## Principles (do not relitigate in review)

- **Output-level only** — hints are pure functions of `DescribeOutput.model_dump()` shapes; **no** graph I/O inside `generate_hints`.
- **Stored `EdgeType` literals in emissions** — new templates use flat keys (`IMPLEMENTS`, `INJECTS`, `CALLS`, `OVERRIDES`) only; no new dot-key describe rows.
- **Suppression over duplication** — tier-1 type wiring (**A–D**) is silent when `_type_rollup_would_emit(edge_summary)` (any of `DECLARES.DECLARES_CLIENT` / `DECLARES.EXPOSES` / `DECLARES.DECLARES_PRODUCER` has `out > 0`). **G** is silent when `_override_axis_would_emit` (any `OVERRIDDEN_BY` or `OVERRIDDEN_BY.*` key with `out > 0`).
- **Priority unchanged** — all new rows use `PRIORITY_LEAF_FOLLOWUP` (2): below rollups (4) and override axis (3), above meta (1). Cap-5 dedupe unchanged.
- **SERVICE gate for C** — `TPL_DESCRIBE_TYPE_DEPENDENCIES` fires only when `role == "SERVICE"`; widening requires a propose amendment + test.
- **E noise gate** — `1 <= CALLS.out <= 9` and (`role != "OTHER"` OR `CALLS.out >= 3`); does not duplicate `CALLS.out >= 10` meta row.
- **I/J additive on endpoints** — `Client` / `Producer` may emit declaring-method hint **and** HTTP/async second hop; relax strict `hints == [want]` regression tests to `want in out.hints`.
- **Partial UC11 amendment** — methods with only trivial `DECLARES` still get `hints == []`; methods with `CALLS` / `OVERRIDES` / unresolved signal may emit **E** / **G** / **H** when gated (intentional; not scope creep).
- **IMPLEMENTS only, not EXTENDS** — lands **A/B**; **L** stays tier 3.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-DHS1 | `mcp_hints.py` describe catalog A–J; `tests/test_mcp_hints.py`; optional `docs/AGENT-GUIDE.md` + README one-liner | **No** | Early-return refactor (client/producer/type); `_in_count` vs composed keys; cap contests when I/J stack on declaring hints; **G** vs override-axis on same method | `test_hints_describe_*` (propose table) + char-cap parametrize | #192 merged |

**Landing order:** **#192 (propose) → PR-DHS1 (implementation)**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Implementation surface | `mcp_hints.py` + `tests/test_mcp_hints.py`; optional docs only in same PR |
| Template count | **7** new `TPL_DESCRIBE_*` constants; **I/J** alias `TPL_FIND_SUCCESS_HTTP_TARGETS` / `TPL_FIND_SUCCESS_ASYNC_TARGETS` (no forked strings) |
| Count helpers | Add `_in_count` (symmetric to `_out_count`); `_type_rollup_would_emit`; `_override_axis_would_emit`; `_symbol_role` (top-level `record["role"]`) |
| Type subject gate | Tier-1 requires `record.kind == "symbol"` and `decl_kind in _TYPE_SYMBOL_KINDS` |
| Route nodes | Unchanged — single declaring hint, early return OK |
| `MCP_HINTS_FIELD_DESCRIPTION` | Optional one sentence on structural `IMPLEMENTS` / `INJECTS` describe hints (same PR if touched) |
| Propose lifecycle | File stays in `propose/` until PR-DHS1 merges; then move to `propose/completed/` |
| Tier 3 | [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191) stays open; reference this propose for **F/K/L** |

---

# PR-DHS1 — describe structural + method-body hints (A–J)

## File-by-file changes

### 1. `mcp_hints.py`

**Module docstring:** add pointer to `propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md` (amendment after v4).

**New templates (verbatim emissions from propose):**

| ID | Constant | String |
| --- | --- | --- |
| A | `TPL_DESCRIBE_TYPE_IMPLEMENTORS` | `implementors: neighbors(['{id}'],'in',['IMPLEMENTS'])` |
| B | `TPL_DESCRIBE_TYPE_IMPLEMENTS` | `implements: neighbors(['{id}'],'out',['IMPLEMENTS'])` |
| C | `TPL_DESCRIBE_TYPE_DEPENDENCIES` | `dependencies: neighbors(['{id}'],'out',['INJECTS'])` |
| D | `TPL_DESCRIBE_TYPE_INJECTORS` | `injectors: neighbors(['{id}'],'in',['INJECTS'])` |
| E | `TPL_DESCRIBE_METHOD_OUTBOUND_CALLS` | `outbound calls: neighbors(['{id}'],'out',['CALLS'])` |
| G | `TPL_DESCRIBE_METHOD_SUPER_DECL` | `super declaration: neighbors(['{id}'],'out',['OVERRIDES'])` |
| H | `TPL_DESCRIBE_METHOD_UNRESOLVED` | `unresolved: neighbors(['{id}'],'out',['CALLS'],include_unresolved=True)` |

**I/J:** emit `TPL_FIND_SUCCESS_HTTP_TARGETS.format(id=node_id)` / `TPL_FIND_SUCCESS_ASYNC_TARGETS.format(id=node_id)` when `HTTP_CALLS.out > 0` / `ASYNC_CALLS.out > 0` on `client` / `producer` records.

**Helpers:**

```python
def _in_count(edge_summary, key) -> int:  # mirror _out_count; read cell["in"]
def _type_rollup_would_emit(edge_summary) -> bool:
    # any DECLARES.DECLARES_CLIENT | DECLARES.EXPOSES | DECLARES.DECLARES_PRODUCER with out > 0
def _override_axis_would_emit(edge_summary) -> bool:
    # any key == "OVERRIDDEN_BY" or startswith "OVERRIDDEN_BY." with out > 0
def _symbol_role(record) -> str | None:  # str(record.get("role") or "").strip() or None
```

**`generate_hints("describe")` refactor** (replace ~689–744 early-return pattern):

1. Build `pairs: list[tuple[int, str]]` for all applicable rows; **one** `return finalize_hint_list(pairs)` at end of describe branch.
2. **`kind == "route"`** — append declaring template; return (unchanged behavior).
3. **`kind == "client"`** — append `TPL_DESCRIBE_CLIENT_DECLARING`; if `_out_count(edge_summary, "HTTP_CALLS") > 0`, append **I** at `PRIORITY_LEAF_FOLLOWUP`; finalize.
4. **`kind == "producer"`** — append `TPL_DESCRIBE_PRODUCER_DECLARING`; if `_out_count(edge_summary, "ASYNC_CALLS") > 0`, append **J**; finalize.
5. **`kind != "symbol"`** — finalize empty or prior pairs.
6. **Type symbols** (`decl_kind in _TYPE_SYMBOL_KINDS`):
   - Existing rollup block (priority 4) unchanged.
   - If `not _type_rollup_would_emit(edge_summary)`:
     - **A:** `decl_kind == "interface"` and `_in_count(..., "IMPLEMENTS") > 0`
     - **B:** `decl_kind == "class"` and `_out_count(..., "IMPLEMENTS") > 0`
     - **C:** `decl_kind == "class"` and `_symbol_role(rec) == "SERVICE"` and `_out_count(..., "INJECTS") > 0`
     - **D:** `decl_kind in {"interface", "class"}` and `_in_count(..., "INJECTS") > 0`
   - All tier-1 at `PRIORITY_LEAF_FOLLOWUP`; drop rendered string if `len > 120`.
7. **Method/constructor symbols** (`decl_kind in _METHOD_SYMBOL_KINDS`):
   - Existing override-axis + leaf integration hints unchanged (priorities 3/2).
   - **E:** `1 <= _out_count(..., "CALLS") <= 9` and (`_symbol_role(rec) != "OTHER"` OR `_out_count(..., "CALLS") >= 3`)
   - **G:** `_out_count(..., "OVERRIDES") > 0` and `not _override_axis_would_emit(edge_summary)`
   - **H:** `int((rec.get("data") or {}).get("unresolved_call_sites_total") or 0) > 0`
   - **CALLS >= 10** meta row unchanged (after **E** gate so no duplicate).
8. Char-cap: drop any rendered tier-1/2 row with `len > 120` before append (match find/neighbors pattern).

**Optional:** extend `MCP_HINTS_FIELD_DESCRIPTION` with one sentence that describe may recommend `IMPLEMENTS` / `INJECTS` structural hops on type Symbols when rollups do not fire.

### 2. `tests/test_mcp_hints.py`

**Fixture helpers** (session `kuzu_graph` unless noted):

| Helper | Purpose |
| --- | --- |
| `_symbol_id_by_fqn(kuzu_graph, fqn)` | Resolve stable bank ids |
| `_interface_with_implements_in(kuzu_graph)` | **A** — e.g. `com.bank.chat.engine.assign.ChatAssignmentPort` |
| `_class_with_implements_out(kuzu_graph)` | **B** — e.g. `com.bank.chat.engine.compliance.RegexComplianceScanner` |
| `_service_class_with_injects_out(kuzu_graph)` | **C** — `role = 'SERVICE'` and `INJECTS.out > 0`, no type rollups |
| `_method_scan_regex_compliance(kuzu_graph)` | **E/G** — `RegexComplianceScanner#scan` |
| `_method_with_unresolved_total_positive(kuzu_graph)` | **H** — Cypher: method Symbol where describe payload would have `unresolved_call_sites_total > 0` |
| `_client_with_http_calls_out(kuzu_graph)` | **I** — first client with `HTTP_CALLS.out > 0` if ordering matters |
| `_producer_with_async_calls_out(kuzu_graph)` | **J** |

Use existing `_controller_class_id_with_exposes` for rollup suppression test.

**Regression updates:**

- `test_hints_describe_client_always_declaring_method` — `assert want in out.hints` (not `== [want]`).
- `test_hints_describe_producer_always_declaring_method` — same.

**Char cap:** extend `test_hints_template_rendered_length_leq_120` parametrize tuples with all seven new templates + realistic `sym:…` ids (propose name `test_hints_describe_structural_templates_char_cap` may alias this extension or add a focused parametrize — prefer **extending** existing global parametrize to avoid duplicate coverage).

### 3. `docs/AGENT-GUIDE.md` (optional, recommended)

- Under **describe** workflow: interface investigate → check `edge_summary.IMPLEMENTS.in` → tier-1 **A** hint; link tier 3 [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191).

### 4. `README.md` (optional)

- One line under MCP `hints` / describe bullet: structural `IMPLEMENTS` / `INJECTS` describe hints when rollups absent — no full catalog paste.

## Tests for PR-DHS1

Implement **verbatim** names from the propose (bank fixture unless noted):

1. `test_hints_describe_interface_implementors_emits` — **A** on `ChatAssignmentPort` (or any interface with `IMPLEMENTS.in > 0`, no rollups)
2. `test_hints_describe_class_implements_emits` — **B** on `RegexComplianceScanner`
3. `test_hints_describe_service_dependencies_emits` — **C** on a `SERVICE` with `INJECTS.out > 0`, no rollups
4. `test_hints_describe_type_injectors_emits` — **D** on `ChatAssignmentPort`
5. `test_hints_describe_type_skips_tier1_when_rollups` — on `_controller_class_id_with_exposes`: assert **no** tier-1 substrings (`implementors:`, `implements:`, `dependencies:`, `injectors:`) in `hints` (not “**A** absent” — controllers are classes, not interfaces)
6. `test_hints_describe_method_outbound_calls_mid_fanout_emits` — **E** on `RegexComplianceScanner#scan` or any method with `3 <= CALLS.out <= 9`
7. `test_hints_describe_method_outbound_calls_low_fanout_non_other_emits` — **(optional)** **E** with `CALLS.out` in `1..2` and `role != "OTHER"`
8. `test_hints_describe_method_super_declaration_emits` — **G** on `RegexComplianceScanner#scan`
9. `test_hints_describe_method_unresolved_emits` — **H** on method with `unresolved_call_sites_total > 0`
10. `test_hints_describe_client_http_targets_emits` — **I**
11. `test_hints_describe_producer_async_targets_emits` — **J**
12. Extend `test_hints_template_rendered_length_leq_120` with new template tuples (covers propose `test_hints_describe_structural_templates_char_cap` intent)

**Validation commands:**

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k describe
.venv/bin/python -m pytest tests -v
```

## Definition of done (PR-DHS1)

- [ ] All tier **A–J** triggers implemented; describe branch uses append-then-finalize (no spurious early return on type/client/producer).
- [ ] All named tests above pass; client/producer regression tests relaxed per propose.
- [ ] `ruff check .` clean; full `pytest tests -v` green without `JAVA_CODEBASE_RAG_RUN_HEAVY`.
- [ ] Optional docs/README lines landed or explicitly skipped in PR description.
- [ ] `propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md` moved to `propose/completed/` in **same** PR as code.
- [ ] [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191) comment references completed propose for tier 3 boundary (issue stays open).

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `_in_count`, rollup/override-axis gates, `_symbol_role` | `mcp_hints.py` | Unit-callable helpers match propose semantics |
| 2 | Add seven `TPL_DESCRIBE_*` constants + docstring pointer | `mcp_hints.py` | Templates render ≤120 with realistic ids |
| 3 | Refactor `generate_hints("describe")` to append A–J; remove blocking early returns | `mcp_hints.py` | Manual `describe_v2` on port + client shows stacked hints |
| 4 | Add fixture helpers + 10–11 new tests; relax client/producer regressions | `tests/test_mcp_hints.py` | `-k describe` green |
| 5 | Extend char-cap parametrize | `tests/test_mcp_hints.py` | New templates in `test_hints_template_rendered_length_leq_120` |
| 6 | Optional AGENT-GUIDE + README line | docs | Reviewer can follow interface workflow |
| 7 | Move propose to `completed/`; ruff + full pytest | repo | PR ready |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Cap drops declaring hint when I/J co-fire | Medium | Both priority 2; accept up to 2 endpoint hints; document in PR if cap trims meta |
| 2 | **C** misses non-SERVICE classes with `INJECTS.out` | Low | Locked SERVICE gate; follow-up propose to widen |
| 3 | **E** noise on OTHER methods | Medium | `role != "OTHER"` OR `CALLS.out >= 3`; optional test for 1..2 non-OTHER |
| 4 | **G** duplicates override-axis on declaration methods | Low | `_override_axis_would_emit` suppression; test on impl method (`#scan`) |
| 5 | Refactor regresses type rollups | High | Keep rollup block first; `test_hints_describe_type_skips_tier1_when_rollups` + existing rollup tests |
| 6 | **H** fixture sparse | Medium | Cypher helper skips if no unresolved methods in bank graph |

# Out of scope

- `ontology_version` bump, `EDGE_SCHEMA` / `build_ast_graph.py` / re-index
- `mcp_v2.py`, `server.py`, `kuzu_queries.py` (unless describe payload missing a field — unexpected)
- Tier 3 rows **F** (`CALLS.in` callers), **K** (`DECLARES.out` members), **L** (`EXTENDS`) — [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191)
- `neighbors` / `find` / `resolve` catalog changes
- Dot-key emissions in new templates
- Widening **C** beyond `SERVICE` without propose amendment
- Per-row hints on `DescribeOutput.record` sub-objects

# Whole-plan done definition

1. PR-DHS1 merged; propose in `propose/completed/`.
2. Bank-fixture describe tests for **A–J** pass; tier-1 suppressed when type rollups fire.
3. Agents describing `ChatAssignmentPort`-shaped nodes see implementors/injectors hints when rollups absent.
4. [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191) still tracks tier 3 only.

# Tracking

- Propose [#192](https://github.com/HumanBean17/java-enterprise-codebase-rag/pull/192): _pending_
- `PR-DHS1`: _pending_
