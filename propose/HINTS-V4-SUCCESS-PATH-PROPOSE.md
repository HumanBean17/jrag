# HINTS-V4 — success-path road signs for `neighbors`, `find`, and `search`

## Status
Proposal — not yet implemented. Tracks [issue #163](https://github.com/HumanBean17/java-codebase-rag/issues/163).

## Problem Statement

Real-codebase agent traces show `hints: []` on most **successful** tool calls. The landed catalog (v1 + #161 producer amendments + v2 `resolve`/fuzzy + v3 empty-neighbors structural) is heavily weighted toward recovery:

| Tool | Success-path coverage today |
|---|---|
| `describe` | Strong for type/method rollups, route/client/producer declaring-method rows |
| `neighbors` | Non-empty: fuzzy-strategy meta only; empty: v3 structural rows |
| `find` | Page-full and empty→`resolve` only |
| `search` | Weak-score spread only |
| `resolve` | `none` / `many` only (`one` correctly silent) |

That violates the original frame in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` §1: hints attach to **all outputs**, not only errors. The highest-friction workflow in production is the happy path where the agent must chain tools (class → members → clients → `HTTP_CALLS` → route handler) without ever calling `describe` on the type first — so it never sees the v1 type-rollup hints.

Concrete failure mode (observed on brownfield corpora):

1. Agent calls `neighbors([class_id], 'out', ['DECLARES'])` and gets method `NodeRef`s — **no hint**.
2. Agent guesses `neighbors([class_id], 'out', ['DECLARES_CLIENT'])` on the type id — empty + v3 type-level requery (late).
3. Correct path (`DECLARES` → member ids → `DECLARES_CLIENT` / `EXPOSES`) is only documented inline when the agent happened to `describe` the class earlier.

Issue #163’s candidate table is directionally right but needs tightening against road-sign discipline (char cap, tone, payload limits, cap interaction). Producer symmetry (#161) is already on the `describe` side; v4 must mirror it on `neighbors` success chains.

## Proposed Solution

Add a **v4 success-path catalog** as an amendment to the locked hint templates (`HINTS-ROAD-SIGNS` §7.11). Implement in `mcp_hints.py` only — no graph, ontology, or MCP shape changes.

### Design frame (extends v2, does not contradict it)

v2 (`propose/completed/HINTS-V2-PROPOSE.md` §1) ruled out **per-row** neighbors hints and confidence thresholds. v4 keeps both rules. Success-path hints are **output-level**, keyed on:

- echoed `requested_edge_types` + `requested_direction` (+ `subject_record` when already plumbed), and
- the **homogeneous endpoint kind** of `results[].other.kind` (and `other.symbol_kind` when `kind == "symbol"`),

with **no** new graph reads and **no** per-edge-row emissions.

### Trigger contract (neighbors)

Fire a success-path hint iff **all** of:

1. `success is True` and `len(results) > 0` and `offset == 0` (same guard as v3 empty structural hints — paginated tail pages are ambiguous).
2. `len(requested_edge_types) == 1` (single-edge calls only; multi-edge requests are agent-composed and out of scope).
3. Every row’s `other.kind` (and method vs type `symbol_kind` when relevant) matches the row’s predicate — **mixed endpoint kinds → no hint** (lossy by design).
4. Rendered string `len <= 120` after substitution; otherwise drop that row.

Priority: **`PRIORITY_LEAF_FOLLOWUP` (2)** — same tier as `describe` method leaf hints and route/client declaring-method rows. Fuzzy-strategy (`PRIORITY_META` 1) and v3 empty structural (`PRIORITY_META` 1) lose cap contests to success-path hints.

### Locked v4 template rows

Placeholders: `{id}` = echoed `origin_id` when a single origin is needed; `member_ids` / `client_ids` / `producer_ids` / `handler_ids` = conventional batch placeholders (same convention as v1 describe two-hop strings). No `{id}` substitution for batch placeholders — agents already treat them as “ids from the rows above.”

#### `neighbors` (highest priority — implement first)

| ID | Trigger | Template (verbatim target) |
|---|---|---|
| N1 | `requested_edge_types == ['DECLARES']`, `direction=='out'`, `subject_record` is type Symbol, all `other` are method/constructor Symbols | `members: neighbors(member_ids,'out',['DECLARES_CLIENT']) or neighbors(member_ids,'out',['EXPOSES'])` |
| N2 | `['DECLARES_CLIENT']`, `direction=='out'`, all `other.kind=='client'` | `HTTP targets: neighbors(client_ids,'out',['HTTP_CALLS'])` |
| N3 | `['DECLARES_PRODUCER']`, `direction=='out'`, all `other.kind=='producer'` | `async targets: neighbors(producer_ids,'out',['ASYNC_CALLS'])` |
| N4 | `['EXPOSES']`, `direction=='in'`, all `other` are method/constructor Symbols | `callers: neighbors(handler_ids,'in',['CALLS'])` |
| N5 | `['HTTP_CALLS']`, `direction=='in'`, all `other.kind=='client'` | `declaring method: neighbors(client_ids,'in',['DECLARES_CLIENT'])` |
| N6 | `['ASYNC_CALLS']`, `direction=='in'`, all `other.kind=='producer'` | `declaring method: neighbors(producer_ids,'in',['DECLARES_PRODUCER'])` |

**Rejected from issue #163 (or deferred):**

| Candidate | Reason |
|---|---|
| Combined hint using “to see clients/routes…” | Violates v1 tone (“to see”); N1 uses imperative labels instead |
| `neighbors(route_id,'in',['HTTP_CALLS'])` when results are clients | Covered by N5; symmetric async row is N6 |
| Deriving strings from `EDGE_SCHEMA.typical_traversals` at success time | v3 owns mismatch recovery; success follow-ups are workflow-specific two-hop shapes, not `alien_subject` strings |
| `IMPLEMENTS` / `EXTENDS` / `CALLS` success hints | Still deferred per v1 §5; add only with trace evidence in a later amendment |
| Hints when `len(requested_edge_types) > 1` | Cap + ambiguity; agent chose a bundle deliberately |

**N1 note:** Unlike `describe` type rollups, neighbors success after `DECLARES` cannot branch on per-member `edge_summary` (not in the payload). N1 intentionally emits **one** combined clients/routes sign — same information density as the describe rollup pair, one cap slot.

#### `find` (second tranche)

| ID | Trigger | Template |
|---|---|---|
| F1 | `kind=='route'`, `len(results)>0`, not page-full | `handler: neighbors([route_id],'in',['EXPOSES'])` |
| F2 | `kind=='client'`, `len(results)>0`, not page-full | `HTTP targets: neighbors([client_id],'out',['HTTP_CALLS'])` |
| F3 | `kind=='producer'`, `len(results)>0`, not page-full | `async targets: neighbors([producer_id],'out',['ASYNC_CALLS'])` |

Use the **first** result’s `id` as `route_id` / `client_id` / `producer_id` when multiple rows are returned (output-level sign, not per-row). If `len(results) >= limit` and `has_more_results`, page-full meta (priority 1) still fires; F-rows use priority 2 and win the cap.

`describe(id=…)` cross-tool wording from #163 is **dropped** — `neighbors` alone reaches the handler; keeps one tool per hint.

#### `search` (optional third tranche — lowest priority)

| ID | Trigger | Template |
|---|---|---|
| S1 | `len(results)==1`, top hit `symbol_id` present | `inspect: describe(id='{symbol_id}')` |

**Do not** emit S1 on every multi-hit search (noise). If traces show agents stall on multi-hit pages, a follow-up amendment can add a dominant-score gate — not in v4 initial lock.

### Cap interaction

| Output | Typical success hint count | Competes with |
|---|---|---|
| `describe` | 0–3 (unchanged) | — |
| `neighbors` | 0–1 success + 0–1 fuzzy | v3 empty (mutually exclusive with non-empty) |
| `find` | 0–1 success | page-full meta (rare co-fire) |
| `search` | 0–1 | weak-score meta (orthogonal triggers) |

No new priority level required. `PRIORITY_LEAF_FOLLOWUP` is sufficient.

### Module changes

- `mcp_hints.py`: add `TPL_*` constants + `neighbors_success_hints()` / `find_success_hints()` helpers; extend `generate_hints` branches.
- Module header comment: point to this propose as v4 amendment.
- `MCP_HINTS_FIELD_DESCRIPTION`: no change (behavior stays advisory).

## Scope

- v4 template catalog and generator logic in `mcp_hints.py`
- Unit tests in `tests/test_mcp_hints.py` (synthetic payloads +, where cheap, `neighbors_v2` round-trips on `kuzu_graph`)
- Appendix amendment note in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (traceability paragraph only — optional docs PR)

## Schema / Ontology / Re-index impact

- Ontology bump: **not required**
- Re-index required: **no** (hints are pure functions of MCP payloads)
- Config/tool surface changes: **none** (field `hints` already exists)

## Tests / Validation

Named scenarios (implementer contract):

| Scenario | Assert |
|---|---|
| `test_hints_neighbors_declares_methods_emits_member_followup` | N1 fires on type + `DECLARES` + method targets |
| `test_hints_neighbors_declares_client_homogeneous_emits_http_calls` | N2 |
| `test_hints_neighbors_declares_producer_homogeneous_emits_async_calls` | N3 |
| `test_hints_neighbors_exposes_in_methods_emits_calls` | N4 |
| `test_hints_neighbors_http_calls_in_clients_emits_declares_client` | N5 |
| `test_hints_neighbors_mixed_endpoint_kinds_silent` | client + route in one page → `[]` success-path |
| `test_hints_neighbors_success_beats_fuzzy_in_cap` | constructed payload with both signals → leaf hint retained |
| `test_hints_find_route_success_emits_handler` | F1 |
| `test_hints_find_client_success_emits_http_calls` | F2 |
| `test_hints_search_single_hit_emits_describe` | S1 |
| `test_hints_all_v4_templates_under_120_chars` | rendered with realistic ids |
| Char-cap + dedupe + cap-5 | reuse existing `finalize_hint_list` tests pattern |

## Open Questions ([TBD])

1. **Single-edge-only gate** — Recommended: **yes** for v4 (row 2 in trigger contract). Multi-edge `neighbors` calls are advanced composition; guessing follow-ups is likely wrong.
2. **Emit N1 when some members lack clients/routes** — Recommended: **yes, always** when N1’s homogeneous method predicate holds. Lossy sign beats silence; agent may ignore.
3. **Search S1 in initial PR** — Recommended: **defer to PR-B** (or skip if scope pressure). Neighbors tranche delivers most of #163’s value.
4. **`find` F1 when `len(results) > 1`** — Recommended: **emit once** using first id; matches output-level rule. Mention in PR description so reviewers do not expect per-row hints.
5. **Amend `HINTS-ROAD-SIGNS` appendix in-repo** — Recommended: **yes**, short “v4 amendment (#163)” paragraph like the #161 producer block.

## Out of scope

- Per-row hints on `Edge` / `SearchHit` / `NodeRef`
- `IMPLEMENTS` / `EXTENDS` / generic `CALLS` success chains (v1 §5 deferral stands until trace-backed)
- `describe` catalog changes (covered by #161 / existing v1)
- `resolve(status='one')` hints
- Structured `next_actions` / pre-fetched walks
- Graph or `EDGE_SCHEMA` changes (no `success_follow_up` field on `EdgeSpec` in v4)
- Hint versioning field (`hints_version`)
- Conditioning on `attrs.match` / confidence for `HTTP_CALLS` / `ASYNC_CALLS` rows (strategy already covered by v2 fuzzy hint on non-empty neighbor pages)

## Sequencing / Follow-ups

Suggested **2 PRs** (can collapse to 1 if reviewer prefers):

| PR | Delivers | Depends on |
|---|---|---|
| **PR-A** | N1–N6 + neighbors tests + char-cap sweep for new templates | — |
| **PR-B** | F1–F3, optional S1 + find/search tests + appendix traceability note | PR-A optional |

After land: move this file to `propose/completed/`, add `plans/PLAN-HINTS-V4.md` + `CURSOR-PROMPTS-HINTS-V4.md` if the team wants the per-PR sentinel contract (not required for a 1–2 PR effort).

**Related issues:** #161 (describe producer symmetry — landed); #163 (this propose). No ontology coordination with SCHEMA-V2.

## Grill notes (issue #163 review)

What the issue gets right:

- Problem is real and measurable (empty `hints` on happy paths).
- Correctly cites v1 deferral of `HTTP_CALLS` / `ASYNC_CALLS` chains now that ontology 14 + Client/Producer endpoints exist.
- Identifies `neighbors` as the highest-impact gap vs already-strong `describe`.
- Purity constraint (payload-only) is preserved.

What needed correction in the issue draft:

| Issue draft element | v4 decision |
|---|---|
| Wording “to see clients/routes…” | Replaced with imperative labels (`members:`, `HTTP targets:`) per HINTS-ROAD-SIGNS §8 |
| `find` → `describe(id=…)` OR `neighbors` | `neighbors` only (one tool per hint) |
| `search` hint on any top hit with `symbol_id` | Gated to `len(results)==1` to avoid spam |
| Unconditional success hints without homogeneous kind check | Required — mixed `other.kind` → silent |
| New priority tier | Unnecessary — reuse `PRIORITY_LEAF_FOLLOWUP` |
| Imply re-index / schema work | None needed |

**Tension with HINTS-V2 §1 (“not what to do next”)**: v4 resolves by classifying these as **continuation signs** grounded in `(edge requested, endpoint kind returned)` — the same observable contract as “you got client nodes” fuzzy strategy, but pointing forward along the resolver chain instead of backward at attrs.
