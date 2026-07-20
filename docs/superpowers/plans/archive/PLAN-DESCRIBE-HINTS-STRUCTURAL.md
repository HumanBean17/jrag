> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: DESCRIBE-HINTS-STRUCTURAL — type wiring and method-body road signs

Status: **active (planning)**. This plan implements
[`propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](../propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md)
(issue [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191) tier 1–2).

Depends on: describe rollups (`DECLARES.*`, `OVERRIDDEN_BY.*`), stored `OVERRIDES`, v4 `TPL_FIND_SUCCESS_HTTP_TARGETS` / `TPL_FIND_SUCCESS_ASYNC_TARGETS` templates, [`hints_structured` dual-output pattern](../propose/completed/HINTS-STRUCTURED-PROPOSE.md) — all landed on `master`.

## Goal

- Extend the `describe` success-path hint catalog with 10 new rows (A–E, G–J) that read `edge_summary` in/out counts and `record.data` — no graph re-query, no `ontology_version` bump, no re-index.
- Tier-1 structural hints (A–D) fire only when no type-rollup hints (`DECLARES.DECLARES_CLIENT` / `DECLARES.EXPOSES` / `DECLARES.DECLARES_PRODUCER`) would emit.
- Tier-2 method/endpoint hints (E, G, H) and client/producer second hops (I, J) have per-row gates described in the propose.
- Every new row appends to both `pairs` (string) and `struct_pairs` (structured) following the established dual-list pattern.

## Principles (do not relitigate in review)

- **Suppression invariant** — tier-1 structural hints (A–D) never co-emit with type rollups. When `DECLARES.DECLARES_CLIENT` / `DECLARES.EXPOSES` / `DECLARES.DECLARES_PRODUCER` have `out > 0`, no A–D hint fires for that type.
- **Priority 2 for all new rows** — `PRIORITY_LEAF_FOLLOWUP` (below rollups 4 and override axis 3, above meta 1). Same priority as existing leaf follow-ups.
- **No ontology bump, no re-index** — pure hint-catalog amendment.
- **No early-return extension for client/producer** — rows I/J are appended before the existing `return` in the `client`/`producer` branches, not after. The `kind == "client"` / `kind == "producer"` blocks still return after emitting both the existing declaring hint and the new I/J row.
- **Reuse v4 templates for I/J** — `TPL_FIND_SUCCESS_HTTP_TARGETS` / `TPL_FIND_SUCCESS_ASYNC_TARGETS` and their structured equivalents.
- **Dual-list parity** — every new string hint has a corresponding `_StructuredHint` in `struct_pairs`.
- **`_in_count` helper** — symmetric to existing `_out_count`, same defensive null/type checks.
- **`_type_rollup_would_emit` helper** — returns `True` when any of the three DECLARES rollup keys has `out > 0`.
- **No dot-key emissions in new templates** — stored literals only (`IMPLEMENTS`, `INJECTS`, `CALLS`, `OVERRIDES`).

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Helpers (`_in_count`, `_type_rollup_would_emit`), 7 new `TPL_DESCRIBE_*` constants, describe control-flow refactor (remove early-returns for type blocks), all 10 rows (A–E, G–J) with dual-list emission, structured parity, 12 string + 9 structured tests, char-cap parametrization, regression relaxations | **No** | Refactoring `is_type` block from early-return to fallthrough (must not break existing rollup tests); I/J added inside client/producer `if kind ==` blocks before `return` (regression on `test_hints_describe_client_always_declaring_method` and `test_hints_describe_producer_always_declaring_method`); `_in_count` must handle missing `"in"` key gracefully | `test_hints_describe_*` (string + structured); char-cap; regression relaxations | — |

**Landing order:** PR-1 only (single PR per propose §Migration).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Single vs multi PR | Single PR — all 10 rows share the same helpers and control-flow refactor; splitting adds churn without isolation benefit |
| Row E gate | `role != "OTHER"` OR `CALLS.out >= 3` — avoids noise on trivial getters/setters |
| Row C scope | Gated to `role == "SERVICE"` — widening requires propose amendment |
| I/J placement | Inside existing `kind == "client"` / `kind == "producer"` blocks, before `return` — additive alongside existing declaring hint |
| `_type_rollup_would_emit` naming | Verb phrase matches the "would emit" pattern from the propose; returns `bool` |
| Regression test relaxation | Change `== [want]` to `want in out.hints` for client/producer declaring tests; same for structured counterparts |

---

# PR-1 — All 10 describe structural rows, helpers, tests

## File-by-file changes

### 1. `mcp_hints.py`

**New helpers (after `_out_count`, ~line 254):**

- `_in_count(edge_summary, key) -> int` — symmetric to `_out_count`: reads `cell.get("in", 0)` instead of `"out"`.
- `_type_rollup_would_emit(edge_summary) -> bool` — returns `True` when any of `DECLARES.DECLARES_CLIENT`, `DECLARES.EXPOSES`, `DECLARES.DECLARES_PRODUCER` has `_out_count > 0`.

**New template constants (after existing `TPL_DESCRIBE_*` block, ~line 125):**

```python
TPL_DESCRIBE_TYPE_IMPLEMENTORS = "implementors: neighbors(['{id}'],'in',['IMPLEMENTS'])"
TPL_DESCRIBE_TYPE_IMPLEMENTS = "implements: neighbors(['{id}'],'out',['IMPLEMENTS'])"
TPL_DESCRIBE_TYPE_DEPENDENCIES = "dependencies: neighbors(['{id}'],'out',['INJECTS'])"
TPL_DESCRIBE_TYPE_INJECTORS = "injectors: neighbors(['{id}'],'in',['INJECTS'])"
TPL_DESCRIBE_METHOD_OUTBOUND_CALLS = "outbound calls: neighbors(['{id}'],'out',['CALLS'])"
TPL_DESCRIBE_METHOD_SUPER_DECL = "super declaration: neighbors(['{id}'],'out',['OVERRIDES'])"
TPL_DESCRIBE_METHOD_UNRESOLVED = "unresolved: neighbors(['{id}'],'out',['CALLS'],include_unresolved=True)"
```

**Control-flow refactor in `generate_hints("describe", …)` (~lines 1056–1140):**

The current `is_type` block (lines 1056–1081) returns early after adding rollup hints. This must be refactored to **fall through** so tier-1 structural hints (A–D) can be appended after rollups, gated by `not _type_rollup_would_emit(edge_summary)`.

The new flow:

```
if is_type:
    # existing rollups (DECLARES.DECLARES_CLIENT, EXPOSES, DECLARES_PRODUCER) — unchanged
    # NO early return — fall through

    if not _type_rollup_would_emit(edge_summary):
        # Tier 1 structural (A–D)
        if decl_kind == "interface" and _in_count(edge_summary, "IMPLEMENTS") > 0:
            # Row A
        if decl_kind == "class" and _out_count(edge_summary, "IMPLEMENTS") > 0:
            # Row B
        if decl_kind == "class" and role == "SERVICE" and _out_count(edge_summary, "INJECTS") > 0:
            # Row C
        if decl_kind in {"interface", "class"} and _in_count(edge_summary, "INJECTS") > 0:
            # Row D

    return (finalize_hint_list(pairs), finalize_structured_hints(struct_pairs))
```

**Client/producer blocks — I/J rows (lines 1034–1047):**

Before each `return` in the `kind == "client"` and `kind == "producer"` blocks, add:

```python
# Inside kind == "client" block (after existing declaring hint, before return):
if _out_count(edge_summary, "HTTP_CALLS") > 0:
    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_FIND_SUCCESS_HTTP_TARGETS.format(id=node_id)))
    struct_pairs.append(_StructuredHint(
        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["HTTP_CALLS"]},
        True, PRIORITY_LEAF_FOLLOWUP,
    ))

# Inside kind == "producer" block (after existing declaring hint, before return):
if _out_count(edge_summary, "ASYNC_CALLS") > 0:
    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_FIND_SUCCESS_ASYNC_TARGETS.format(id=node_id)))
    struct_pairs.append(_StructuredHint(
        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["ASYNC_CALLS"]},
        True, PRIORITY_LEAF_FOLLOWUP,
    ))
```

**Method block — E/G/H rows (lines 1083–1138, inside `if is_method:`):**

After existing leaf follow-ups (DECLARES_CLIENT, DECLARES_PRODUCER, EXPOSES) and before the `CALLS >= 10` meta row:

```python
# Row E — outbound calls (mid fanout)
calls_out = _out_count(edge_summary, "CALLS")
if 1 <= calls_out <= 9:
    role = str((rec.get("data") or {}).get("role") or rec.get("role") or "")
    if role != "OTHER" or calls_out >= 3:
        pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_OUTBOUND_CALLS.format(id=node_id)))
        struct_pairs.append(_StructuredHint(
            "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"]},
            True, PRIORITY_LEAF_FOLLOWUP,
        ))

# Row G — super declaration (OVERRIDES.out)
if _out_count(edge_summary, "OVERRIDES") > 0:
    override_axis_emits = any(
        _out_count(edge_summary, k) > 0
        for k in ["OVERRIDDEN_BY"] + [k for k in (edge_summary or {}) if k == "OVERRIDDEN_BY" or k.startswith("OVERRIDDEN_BY.")]
    )
    if not override_axis_emits:
        pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_SUPER_DECL.format(id=node_id)))
        struct_pairs.append(_StructuredHint(
            "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["OVERRIDES"]},
            True, PRIORITY_LEAF_FOLLOWUP,
        ))

# Row H — unresolved call sites
data = rec.get("data")
unresolved = 0
if isinstance(data, dict):
    unresolved = int(data.get("unresolved_call_sites_total") or 0)
if unresolved > 0:
    pairs.append((PRIORITY_LEAF_FOLLOWUP, TPL_DESCRIBE_METHOD_UNRESOLVED.format(id=node_id)))
    struct_pairs.append(_StructuredHint(
        "neighbors", {"ids": [node_id], "direction": "out", "edge_types": ["CALLS"], "include_unresolved": True},
        True, PRIORITY_LEAF_FOLLOWUP,
    ))
```

**Row G override-axis gate** — inline the check: `any _out_count(edge_summary, k) > 0 for k in ["OVERRIDDEN_BY"] + [k for k in edge_summary if k.startswith("OVERRIDDEN_BY.")]`. This matches the propose's `not _override_axis_would_emit(edge_summary)`.

### 2. `tests/test_mcp_hints.py`

**New helper functions:**

```python
def _interface_with_implements_in(kuzu_graph) -> str:
    """Interface Symbol with IMPLEMENTS.in > 0, no type rollups."""
    ...

def _class_with_implements_out(kuzu_graph) -> str:
    """Class Symbol with IMPLEMENTS.out > 0, no type rollups."""
    ...

def _service_with_injects_out(kuzu_graph) -> str:
    """SERVICE class Symbol with INJECTS.out > 0, no type rollups."""
    ...

def _type_with_injects_in(kuzu_graph) -> str:
    """Interface or class Symbol with INJECTS.in > 0, no type rollups."""
    ...

def _method_with_mid_calls_out(kuzu_graph) -> str:
    """Method Symbol with 3 <= CALLS.out <= 9."""
    ...

def _method_with_overrides_out(kuzu_graph) -> str:
    """Method Symbol with OVERRIDES.out > 0, no OVERRIDDEN_BY axis."""
    ...

def _method_with_unresolved(kuzu_graph) -> str:
    """Method Symbol with unresolved_call_sites_total > 0."""
    ...
```

Each helper uses a Cypher query on `kuzu_graph` to find a matching node, with `pytest.skip()` if the fixture lacks a candidate.

**New string hint tests (12 tests):**

1. `test_hints_describe_interface_implementors_emits` — row A
2. `test_hints_describe_class_implements_emits` — row B
3. `test_hints_describe_service_dependencies_emits` — row C
4. `test_hints_describe_type_injectors_emits` — row D
5. `test_hints_describe_type_skips_tier1_when_rollups` — suppression on type with rollups
6. `test_hints_describe_method_outbound_calls_mid_fanout_emits` — row E
7. `test_hints_describe_method_outbound_calls_low_fanout_non_other_emits` — row E variant
8. `test_hints_describe_method_super_declaration_emits` — row G
9. `test_hints_describe_method_unresolved_emits` — row H
10. `test_hints_describe_client_http_targets_emits` — row I
11. `test_hints_describe_producer_async_targets_emits` — row J
12. `test_hints_describe_structural_templates_char_cap` — all new templates render ≤120 chars

**New structured hint tests (9 tests):**

1. `test_structured_hints_describe_interface_implementors` — A structured parity
2. `test_structured_hints_describe_class_implements` — B structured parity
3. `test_structured_hints_describe_service_dependencies` — C structured parity
4. `test_structured_hints_describe_type_injectors` — D structured parity
5. `test_structured_hints_describe_method_outbound_calls` — E structured parity
6. `test_structured_hints_describe_method_super_declaration` — G structured parity
7. `test_structured_hints_describe_method_unresolved` — H with `include_unresolved: True`
8. `test_structured_hints_describe_client_http_targets` — I structured parity
9. `test_structured_hints_describe_producer_async_targets` — J structured parity

**Regression relaxations:**

- `test_hints_describe_client_always_declaring_method`: change `assert out.hints == [want]` to `assert want in out.hints`
- `test_hints_describe_producer_always_declaring_method`: same relaxation
- Structured counterparts: assert the new structured hint `in` the list, not sole entry

**Char-cap parametrization update:**

Add new `(template, kwargs)` pairs to `test_hints_all_v4_templates_under_120_chars` for all 7 new `TPL_DESCRIBE_*` constants. Use realistic `id` kwargs (e.g. `{"id": "sym:com.example.RegexComplianceScanner#scan(String)"}`).

### 3. `propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`

Move to `propose/completed/` after PR-1 lands.

## Tests for PR-1

1. `test_hints_describe_interface_implementors_emits`
2. `test_hints_describe_class_implements_emits`
3. `test_hints_describe_service_dependencies_emits`
4. `test_hints_describe_type_injectors_emits`
5. `test_hints_describe_type_skips_tier1_when_rollups`
6. `test_hints_describe_method_outbound_calls_mid_fanout_emits`
7. `test_hints_describe_method_outbound_calls_low_fanout_non_other_emits`
8. `test_hints_describe_method_super_declaration_emits`
9. `test_hints_describe_method_unresolved_emits`
10. `test_hints_describe_client_http_targets_emits`
11. `test_hints_describe_producer_async_targets_emits`
12. `test_hints_describe_structural_templates_char_cap`
13. `test_structured_hints_describe_interface_implementors`
14. `test_structured_hints_describe_class_implements`
15. `test_structured_hints_describe_service_dependencies`
16. `test_structured_hints_describe_type_injectors`
17. `test_structured_hints_describe_method_outbound_calls`
18. `test_structured_hints_describe_method_super_declaration`
19. `test_structured_hints_describe_method_unresolved`
20. `test_structured_hints_describe_client_http_targets`
21. `test_structured_hints_describe_producer_async_targets`

## Definition of done (PR-1)

- All 10 hint rows emit correct string and structured hints for matching fixtures
- Tier-1 suppression works: no A–D when type rollups fire
- Row E gate works: no emission on OTHER methods with `CALLS.out < 3`
- Row G gate works: no emission when OVERRIDDEN_BY axis fires
- Client/producer declaring tests relaxed and still pass
- All new templates render ≤120 chars
- `.venv/bin/ruff check .` clean
- `.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k describe` green
- `.venv/bin/python -m pytest tests -v` green (full suite)

## Implementation step list

| # | Step | File(s) | Done when |
| --- | --- | --- | --- |
| 1 | Add `_in_count` and `_type_rollup_would_emit` helpers | `mcp_hints.py` | Both functions pass manual inspection; `_in_count` symmetric with `_out_count` |
| 2 | Add 7 new `TPL_DESCRIBE_*` constants | `mcp_hints.py` | Constants compile; all ≤120 chars with realistic `id` |
| 3 | Refactor `is_type` block: remove early return, add tier-1 structural hints (A–D) after rollups with suppression gate | `mcp_hints.py` | Existing type-rollup tests pass; new tier-1 tests pass |
| 4 | Add I/J rows inside `kind == "client"` and `kind == "producer"` blocks | `mcp_hints.py` | Client/producer tests pass with relaxed assertions |
| 5 | Add E/G/H rows inside `is_method` block | `mcp_hints.py` | Method tests pass |
| 6 | Write string hint tests (12 tests) | `tests/test_mcp_hints.py` | All pass |
| 7 | Write structured hint tests (9 tests) | `tests/test_mcp_hints.py` | All pass |
| 8 | Relax regression tests for client/producer | `tests/test_mcp_hints.py` | Relaxed tests pass |
| 9 | Add char-cap parametrization entries | `tests/test_mcp_hints.py` | Parametrized test passes |
| 10 | Run full suite + ruff | all | Green + clean |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | `is_type` early-return removal breaks existing rollup tests | high | Step 3: run `pytest tests/test_mcp_hints.py -v -k describe` immediately after refactor, before adding new rows |
| 2 | I/J additive hints push hint count over cap (5) on rich endpoints | low | Client/producer already emit only 1 hint; I/J adds at most 1 more — well within cap |
| 3 | Row E noise on getter/setter methods in fixture | low | Gate: `role != "OTHER" OR CALLS.out >= 3`; verify with fixture inspection |
| 4 | Row G override-axis gate misses a key variant | medium | Inline gate iterates all keys starting with `OVERRIDDEN_BY` — matches existing override-axis trigger logic exactly |
| 5 | Structured hint `args` key mismatch vs propose table | low | Copy `args` dict directly from propose §Structured hint mapping table |

# Out of scope

- Tier 3 rows (F/K/L) — tracked in [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191)
- `EXTENDS` describe row
- `neighbors` / `EdgeType` / schema changes
- `ontology_version` bump or re-index
- `docs/AGENT-GUIDE.md` or `README.md` updates (optional per propose; skip in this PR)
- Any changes to `mcp_v2.py`, `server.py`, or output models

# Whole-plan done definition

1. All 10 describe structural rows implemented with string + structured parity
2. All 21 named tests pass
3. Full test suite green
4. Ruff clean
5. Proposal moved to `propose/completed/`

# Tracking

- `PR-1`: _pending_
