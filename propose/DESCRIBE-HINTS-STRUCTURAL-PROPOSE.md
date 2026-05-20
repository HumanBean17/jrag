# DESCRIBE-HINTS-STRUCTURAL — type wiring and method-body road signs

## Status

**Draft** — in-flight under `propose/`.

**Tracks tier 3 (deferred):** [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191).

**Amends:** [`propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`](./completed/HINTS-ROAD-SIGNS-PROPOSE.md) §5 deferred rows (`IMPLEMENTS` / `EXTENDS` on describe; partial — this propose lands **IMPLEMENTS** only on describe, not `EXTENDS`).

**Depends on (landed):** describe rollups (`DECLARES.*`, `OVERRIDDEN_BY.*`), stored `OVERRIDES`, v4 `TPL_FIND_SUCCESS_HTTP_TARGETS` / `TPL_FIND_SUCCESS_ASYNC_TARGETS` strings.

## TL;DR

- Extend the **`describe` success-path** hint catalog in `mcp_hints.py` with **10 hint rows** (tiers 1–2; **7** new `TPL_DESCRIBE_*` constants, reuse find v4 strings for **I/J**).
- Triggers read **`edge_summary` in/out counts** and **`record.data`** only — no graph re-query, no `ontology_version` bump, **no re-index**.
- **Suppression:** tier-1 structural hints fire only when **no type rollup** hints (`DECLARES.DECLARES_CLIENT` / `DECLARES.EXPOSES` / `DECLARES.DECLARES_PRODUCER`) would emit; tier-2 rows have per-row gates below.
- **Tier 3** (callers, members-only, `EXTENDS`) stays in [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191).

## Problem statement

Agents investigating **interfaces, ports, and service classes** often `describe` the contract node, see **empty `hints`**, and stay on abstract members or DTO surface — while `edge_summary` already shows the right **stored** edges (`IMPLEMENTS.in`, `INJECTS.out`, etc.).

The v1 catalog optimized **integration rollups** (clients/routes/producers via members) and **declaration-side override axis**. It deliberately deferred **`IMPLEMENTS` / `EXTENDS`** describe rows. Production-shaped failure modes on `tests/bank-chat-system`:

| Node | `edge_summary` signal | v1 `hints` |
|------|----------------------|------------|
| `ChatAssignmentPort` (interface) | `IMPLEMENTS.in=1`, `INJECTS.in=2` | `[]` |
| `EventProcessor` (interface) | `IMPLEMENTS.in=11` | `[]` |
| `DistributionService` (SERVICE) | `INJECTS.out>0` | `[]` |
| `RegexComplianceScanner#scan` (impl) | `OVERRIDES.out=1`, unresolved sites | `[]` |
| `Client` with `HTTP_CALLS.out=1` | declaring + HTTP edge | declaring only |

**Principle:** if `edge_summary` advertises a **one-hop stored `EdgeType`**, describe should emit a **single** `neighbors(...)` road sign when that hop is the obvious next step — same frame as rollups, without dot-keys in emissions.

## Proposed solution

Pure amendment to `generate_hints("describe", …)` in `mcp_hints.py`:

1. Add `_in_count(edge_summary, key)` (symmetric to `_out_count`).
2. Add `_type_rollup_would_emit(edge_summary) -> bool` (any of the three `DECLARES.*` composed keys with `out > 0`).
3. Register templates below; priority **`PRIORITY_LEAF_FOLLOWUP` (2)** for all new rows (below rollups **4** and override axis **3**, above meta **1**).
4. Reuse v4 strings for client/producer second hops (`TPL_FIND_SUCCESS_HTTP_TARGETS`, `TPL_FIND_SUCCESS_ASYNC_TARGETS`).
5. **Refactor `generate_hints("describe")` control flow** — today `client` / `producer` / type symbols **early-return** after the first hint block (`mcp_hints.py` ~689–719). Implementation must **append** tier-1 rows (when not suppressed) and **I/J** on endpoints **before** `finalize_hint_list`, not only add branches at the bottom of the file.

### Suppression (tier 1)

All tier-1 rows require:

- `record.kind == "symbol"`
- `decl_kind in {class, interface, enum, record, annotation}`
- **`not _type_rollup_would_emit(edge_summary)`**

### Tier 1 — type wiring (P0)

| ID | Trigger | Template constant | Emission (≤120 chars with realistic `sym:…` id) |
|----|---------|-------------------|--------------------------------------------------|
| **A** | `decl_kind == "interface"` and `IMPLEMENTS.in > 0` | `TPL_DESCRIBE_TYPE_IMPLEMENTORS` | `implementors: neighbors(['{id}'],'in',['IMPLEMENTS'])` |
| **B** | `decl_kind == "class"` and `IMPLEMENTS.out > 0` | `TPL_DESCRIBE_TYPE_IMPLEMENTS` | `implements: neighbors(['{id}'],'out',['IMPLEMENTS'])` |
| **C** | `decl_kind == "class"` and `role == "SERVICE"` and `INJECTS.out > 0` | `TPL_DESCRIBE_TYPE_DEPENDENCIES` | `dependencies: neighbors(['{id}'],'out',['INJECTS'])` |
| **D** | `decl_kind in {interface, class}` and `INJECTS.in > 0` | `TPL_DESCRIBE_TYPE_INJECTORS` | `injectors: neighbors(['{id}'],'in',['INJECTS'])` |

**Notes:**

- **A** is the interface → implementors fix discussed in review.
- **B** covers `@Component` / service **impl** classes (e.g. `RegexComplianceScanner` with `IMPLEMENTS.out=1`).
- **C** is gated to **`SERVICE`** to avoid hint spam on every `INJECTS.out` class; widen only via propose amendment.
- **D** fires on ports and wired abstractions (`ChatAssignmentPort` injectors).

### Tier 2 — methods and endpoints (P1)

| ID | Trigger | Template | Emission |
|----|---------|----------|----------|
| **E** | method/constructor; `1 <= CALLS.out <= 9`; no tier-1 rollup on parent (N/A here); **gate:** `role != "OTHER"` OR `CALLS.out >= 3` | `TPL_DESCRIBE_METHOD_OUTBOUND_CALLS` | `outbound calls: neighbors(['{id}'],'out',['CALLS'])` |
| **G** | method; `OVERRIDES.out > 0`; **`not _override_axis_would_emit(edge_summary)`** — true when any key `k` with `k == "OVERRIDDEN_BY"` or `k.startswith("OVERRIDDEN_BY.")` has `out > 0` (same keys as override rollups today) | `TPL_DESCRIBE_METHOD_SUPER_DECL` | `super declaration: neighbors(['{id}'],'out',['OVERRIDES'])` |
| **H** | method; `int(record.data.unresolved_call_sites_total or 0) > 0` | `TPL_DESCRIBE_METHOD_UNRESOLVED` | `unresolved: neighbors(['{id}'],'out',['CALLS'],include_unresolved=True)` |
| **I** | `kind == "client"` and `HTTP_CALLS.out > 0` | `TPL_FIND_SUCCESS_HTTP_TARGETS` (existing) | same as find v4 |
| **J** | `kind == "producer"` and `ASYNC_CALLS.out > 0` | `TPL_FIND_SUCCESS_ASYNC_TARGETS` (existing) | same as find v4 |

**Notes:**

- **E** closes the gap between “leaf method” (UC11) and `CALLS.out >= 10` meta; does not duplicate the meta row.
- **G** covers **override implementation** methods (`RegexComplianceScanner#scan`) where rollups exist only on the **declaration** (`ChatAssignmentPort#requestAssignment` already gets `OVERRIDDEN_BY*` hints).
- **H** points at the mutually exclusive `include_unresolved` path (see `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` wording in `mcp_hints.py`).
- **I/J** bring **describe** parity with find/neighbors v4 second hops; **additive** alongside existing declaring-method hints (cap may drop lowest-priority meta).

### Tier 3 — deferred ([#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191))

| ID | Sketch | Why deferred |
|----|--------|--------------|
| **F** | `CALLS.in` callers hint | ~200 getter/setter false positives on bank fixture without role/count gates |
| **K** | `DECLARES.out` members hint | Low ROI; agents usually try `DECLARES` |
| **L** | `EXTENDS.out` supertype hint | Secondary to **A/B/D** |

## What this does NOT do

- No `neighbors` / `EdgeType` / schema changes
- No dot-key emissions in new templates (stored literals only)
- No `EXTENDS` describe row (tier 3 / [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191))
- No change to `MCP_HINTS_FIELD_DESCRIPTION` beyond one sentence noting structural `IMPLEMENTS` / `INJECTS` describe hints (optional doc-only in same PR)

## Migration

| Item | Action |
|------|--------|
| `ontology_version` | unchanged |
| Re-index | not required |
| PR count | **Two PRs total:** (1) this propose ([#192](https://github.com/HumanBean17/java-enterprise-codebase-rag/pull/192)); (2) implementation (`mcp_hints.py` + tests + optional `docs/AGENT-GUIDE.md`) |

## Test plan (`tests/test_mcp_hints.py`)

Use `tests/bank-chat-system` session `kuzu_graph` fixture unless noted.

| Test name | Asserts |
|-----------|---------|
| `test_hints_describe_interface_implementors_emits` | **A** on `ChatAssignmentPort` (or any interface with `IMPLEMENTS.in > 0`, no rollups) |
| `test_hints_describe_class_implements_emits` | **B** on `RegexComplianceScanner` (class, `IMPLEMENTS.out > 0`, no rollups) |
| `test_hints_describe_service_dependencies_emits` | **C** on a `SERVICE` with `INJECTS.out > 0`, no rollups |
| `test_hints_describe_type_injectors_emits` | **D** on `ChatAssignmentPort` |
| `test_hints_describe_type_skips_tier1_when_rollups` | On `_controller_class_id_with_exposes` (or any type with a firing rollup): **no** tier-1 substring (`implementors:`, `implements:`, `dependencies:`, `injectors:`) in `hints` — **not** “**A** absent” (row **A** is interface-only; controllers never emit **A**) |
| `test_hints_describe_method_outbound_calls_mid_fanout_emits` | **E** on `RegexComplianceScanner#scan` (`CALLS.out=4`, non-OTHER) or any method with `3 <= CALLS.out <= 9` |
| `test_hints_describe_method_outbound_calls_low_fanout_non_other_emits` | (optional) **E** with `CALLS.out` in `1..2` and `role != "OTHER"` |
| `test_hints_describe_method_super_declaration_emits` | **G** on `RegexComplianceScanner#scan` |
| `test_hints_describe_method_unresolved_emits` | **H** on method with `unresolved_call_sites_total > 0` |
| `test_hints_describe_client_http_targets_emits` | **I** on client with `HTTP_CALLS.out > 0` |
| `test_hints_describe_producer_async_targets_emits` | **J** on producer with `ASYNC_CALLS.out > 0` |
| `test_hints_describe_structural_templates_char_cap` | all new templates render ≤120 chars with realistic ids |

Update `test_all_hint_templates_char_cap` tuple list with new `(template, kwargs)` pairs.

**Regression (implementation PR):** relax `test_hints_describe_client_always_declaring_method` and `test_hints_describe_producer_always_declaring_method` from `out.hints == [want]` to `want in out.hints` (bank fixture: first `Client` / `Producer` already have `HTTP_CALLS.out` / `ASYNC_CALLS.out` while **I/J** are additive).

## Docs (same PR, optional but recommended)

- `docs/AGENT-GUIDE.md` — under **describe** / workflow table: interface investigate → `IMPLEMENTS.in` → tier-1 **A** hint; link tier 3 [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191).
- `README.md` — one line under MCP hints if there is an existing describe bullet (no full catalog paste).

## Risks

| Risk | Mitigation |
|------|------------|
| Hint cap drops useful rows | Tier-1 suppressed when rollups fire; priorities unchanged |
| **C** too narrow (non-SERVICE services) | Amend `role` gate in a follow-up; do not widen in implementation without test |
| **E** noise on OTHER methods | Role / `CALLS.out >= 3` gate in table |
| **I/J** duplicate declaring + HTTP hints | Accept up to 2 leaf hints on endpoints; cap 5 |

## Acceptance

- [ ] All tier 1–2 templates implemented in `mcp_hints.py`
- [ ] Named tests above pass; `.venv/bin/ruff check .` clean
- [ ] `.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k describe` green
- [ ] [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191) references this propose for tier 3

## After landing

- **This propose PR ([#192](https://github.com/HumanBean17/java-enterprise-codebase-rag/pull/192)):** merge only updates `propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`; file **stays** in `propose/` (per `agent-workflow.mdc`).
- **Implementation PR:** move this file to `propose/completed/` when `mcp_hints.py` + tests land.
- [#191](https://github.com/HumanBean17/java-enterprise-codebase-rag/issues/191): close only when tier 3 is implemented or explicitly wont-fixed.
