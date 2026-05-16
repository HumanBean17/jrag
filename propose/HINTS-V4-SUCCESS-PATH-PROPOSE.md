# HINTS-V4 — success-path road signs for `neighbors`, `find`, and `search`

## Status

Proposal — not yet implemented. Tracks [issue #163](https://github.com/HumanBean17/java-codebase-rag/issues/163).

**Depends on (landed):** [NEIGHBORS-DOT-KEY-TRAVERSAL](./completed/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md) ([#171](https://github.com/HumanBean17/java-codebase-rag/pull/171)) — `neighbors` accepts `DECLARES.DECLARES_CLIENT`, `DECLARES.DECLARES_PRODUCER`, `DECLARES.EXPOSES` on type Symbol origins; describe rollup templates already prescribe those dot-keys.

## Amendment after NEIGHBORS-DOT-KEY (#171)

This section records deltas applied to the original #163 draft after dot-key traversal landed.

| Area | Original draft | Amended |
|---|---|---|
| Type → clients/routes after `DECLARES` | Single N1 with `member_ids` + flat `DECLARES_CLIENT` / `EXPOSES` | **N1a + N1b** — two hints reusing the same strings as `TPL_DESCRIBE_TYPE_*_VIA_MEMBERS` (dot-keys from `{id}` = type `origin_id`) |
| Combined dot-key `or` in one hint | One 99-char member-batch line | **Rejected** — a single combined dot-key line exceeds 120 chars with realistic class ids (~194 chars measured) |
| Success after composed traversal | Not covered | **N2/N3** triggers extended to composed `requested_edge_types`; **N7** for `DECLARES.EXPOSES` → routes |
| HINTS-ROAD-SIGNS §2.7–2.9 | Emissions use atomic `EdgeType` only | **Second partial reversal** (after describe rollups in #171): v4 success-path emissions may recommend the three `DECLARES.*` dot-keys on type origins only; v3 **empty** structural hints still never use dot-keys (`_filter_neighbors_dotkey_hints` unchanged) |
| `EDGE_SCHEMA.type_subject` | Assumed aligned | **Out of v4 scope** — v3 empty hints may still show the legacy two-hop `DECLARES` → `member_ids` string until [#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) |

## Problem Statement

Real-codebase agent traces show `hints: []` on most **successful** tool calls. The landed catalog (v1 + #161 producer amendments + v2 `resolve`/fuzzy + v3 empty-neighbors structural) is heavily weighted toward recovery:

| Tool | Success-path coverage today |
|---|---|
| `describe` | Strong for type/method rollups, route/client/producer declaring-method rows (type rollups use `DECLARES.*` dot-keys since #171) |
| `neighbors` | Non-empty: fuzzy-strategy meta only; empty: v3 structural rows |
| `find` | Page-full and empty→`resolve` only |
| `search` | Weak-score spread only |
| `resolve` | `none` / `many` only (`one` correctly silent) |

That violates the original frame in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` §1: hints attach to **all outputs**, not only errors. The highest-friction workflow in production is the happy path where the agent must chain tools (class → clients → `HTTP_CALLS` → route handler) without ever calling `describe` on the type first — so it never sees the v1 type-rollup hints.

Concrete failure mode (observed on brownfield corpora; still valid after #171):

1. Agent calls `neighbors([class_id], 'out', ['DECLARES'])` and gets method `NodeRef`s — **no hint**.
2. Agent guesses `neighbors([class_id], 'out', ['DECLARES_CLIENT'])` on the type id — empty + v3 type-level requery (late). Flat member-only edges on a type Symbol remain invalid; dot-keys are the type-level shortcut.
3. Preferred path from the type is `neighbors(['{class_id}'],'out',['DECLARES.DECLARES_CLIENT'])` (and/or `DECLARES.EXPOSES` / `DECLARES.DECLARES_PRODUCER`) — same as describe rollups — but agents that skip `describe` never see that unless v4 fires on step 1.

Issue #163’s candidate table is directionally right but needs tightening against road-sign discipline (char cap, tone, payload limits, cap interaction). Producer symmetry (#161) is already on the `describe` side; v4 must mirror it on `neighbors` success chains, aligned with the landed describe dot-key templates.

## Proposed Solution

Add a **v4 success-path catalog** as an amendment to the locked hint templates (`HINTS-ROAD-SIGNS` §7.11). Implement in `mcp_hints.py` only — no graph, ontology, or MCP shape changes.

### Design frame (extends v2, does not contradict it)

v2 (`propose/completed/HINTS-V2-PROPOSE.md` §1) ruled out **per-row** neighbors hints and confidence thresholds. v4 keeps both rules. Success-path hints are **output-level**, keyed on echoed request fields and homogeneous endpoint kinds in the serialized payload, with **no** new graph reads and **no** per-edge-row emissions.

**Dot-key emissions (partial reversal):** v4 may recommend `DECLARES.DECLARES_CLIENT`, `DECLARES.DECLARES_PRODUCER`, and `DECLARES.EXPOSES` in success-path hint strings when the trigger is a **type** Symbol origin (N1a/N1b) — matching `TPL_DESCRIBE_TYPE_*_VIA_MEMBERS`. All other success-path rows continue to use flat stored `EdgeType` literals only. `OVERRIDDEN_BY.*` dot-keys remain describe-only and must not appear in any hint emission.

### Payload fields (`generate_hints` reads dicts from `.model_dump()`)

**`neighbors`** — `mcp_v2.neighbors_v2` builds:

| Key | Shape | Use |
|---|---|---|
| `success` | `bool` | must be `True` |
| `results` | `list[dict]` | each row is `Edge.model_dump()` |
| `results[i]["other"]` | `dict` | `NodeRef`: `kind`, `id`, optional `symbol_kind` |
| `results[i]["other"]["kind"]` | `"symbol" \| "route" \| "client" \| "producer"` | homogeneous-endpoint gate |
| `results[i]["other"]["symbol_kind"]` | `str \| None` | method vs type when `kind == "symbol"` |
| `requested_edge_types` | `list[str]` | echoed `edge_types` (includes composed dot-keys when requested) |
| `requested_direction` | `"in" \| "out"` | echoed direction |
| `offset` | `int` | echoed page offset; success hints require `0` |
| `origin_id` | `str` | first origin id; `{id}` for N1a/N1b |
| `subject_record` | `dict` | `NodeRecord.model_dump()` for origin; **required for N1a/N1b** |

**`find`** — `find_v2` builds:

| Key | Shape | Use |
|---|---|---|
| `kind` | `str` | `"route" \| "client" \| "producer" \| …` |
| `results` | `list[dict]` | each row is `NodeRef.model_dump()` |
| `results[i]["id"]` | `str` | substituted into F-templates as `{id}` |
| `limit`, `has_more_results` | | page-full gate |

**`search`** — deferred S1 only (see below).

### Trigger contract (`neighbors`)

Fire a success-path hint iff **all** of:

1. `payload["success"] is True` and `len(payload["results"]) > 0` and `payload.get("offset", 0) == 0` (paginated tail pages are ambiguous — mirror v3 empty-hint suppression).
2. `len(payload["requested_edge_types"]) == 1` (**locked** — multi-edge requests are agent-composed; no success hints).
3. Every row’s `results[i]["other"]["kind"]` (and `symbol_kind` when `kind == "symbol"`) matches the template predicate — **mixed endpoint kinds → no hint**.
4. Rendered string `len <= 120` after substitution; otherwise drop that row.
5. **N1a/N1b only:** `payload["subject_record"]` is present, `subject_record["kind"] == "symbol"`, and `subject_record["data"]["kind"]` (declaration kind) is one of `class`, `interface`, `enum`, `record`, `annotation`.

Priority: **`PRIORITY_LEAF_FOLLOWUP` (2)**. Fuzzy-strategy (`PRIORITY_META` 1) and v3 empty structural (`PRIORITY_META` 1) lose cap contests to success-path hints.

### Locked v4 template rows

**Substitution conventions** (match v1 `mcp_hints.py`):

- `{id}` — substituted with a **concrete** node id string (same as describe route/client rows). For N1a/N1b use `origin_id` (the type Symbol the agent queried).
- `member_ids` / `client_ids` / `producer_ids` / `handler_ids` / `route_ids` — **batch placeholders**, never substituted; agent copies ids from `results[].other.id`.

**Reuse describe dot-key strings:** N1a/N1b must match the landed constants verbatim (single source of truth — import or reference `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS`, `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS`; do not fork wording).

#### `neighbors` (highest priority — implement first)

| ID | Trigger | Template (verbatim target) |
|---|---|---|
| N1a | `requested_edge_types == ['DECLARES']`, `direction=='out'`, subject_record is type Symbol (trigger §5), all `other` are method/constructor Symbols | Same as `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS`: `clients via members: neighbors(['{id}'],'out',['DECLARES.DECLARES_CLIENT'])` |
| N1b | same as N1a | Same as `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS`: `routes via members: neighbors(['{id}'],'out',['DECLARES.EXPOSES'])` |
| N2 | `requested_edge_types` is `['DECLARES_CLIENT']` **or** `['DECLARES.DECLARES_CLIENT']`, `direction=='out'`, all `other.kind=='client'` | `HTTP targets: neighbors(client_ids,'out',['HTTP_CALLS'])` |
| N3 | `['DECLARES_PRODUCER']` **or** `['DECLARES.DECLARES_PRODUCER']`, `direction=='out'`, all `other.kind=='producer'` | `async targets: neighbors(producer_ids,'out',['ASYNC_CALLS'])` |
| N4 | `['EXPOSES']`, `direction=='in'`, all `other` are method/constructor Symbols | `callers: neighbors(handler_ids,'in',['CALLS'])` |
| N5 | `['HTTP_CALLS']`, `direction=='in'`, all `other.kind=='client'` | `declaring method: neighbors(client_ids,'in',['DECLARES_CLIENT'])` |
| N6 | `['ASYNC_CALLS']`, `direction=='in'`, all `other.kind=='producer'` | `declaring method: neighbors(producer_ids,'in',['DECLARES_PRODUCER'])` |
| N7 | `['DECLARES.EXPOSES']`, `direction=='out'`, all `other.kind=='route'` | `handler: neighbors(route_ids,'in',['EXPOSES'])` |

**N1a/N1b — split vs single combined hint:** v1 describe emits separate client/route rollup hints when `edge_summary` counts are positive. Neighbors success after `DECLARES` lacks `edge_summary`, so both N1a and N1b fire whenever the homogeneous-method predicate holds (lossy — see Open Q1). A single combined `or` line with dot-keys does not fit the 120-char cap; the old member-batch combined line is **withdrawn** as the primary recipe.

**N1a/N1b vs describe:** Duplicate road signs across tools are intentional — same rationale as the original N1 vs describe type rollups.

**N2/N3 composed triggers:** When the agent already used a type-level dot-key, terminal nodes are homogeneous clients/producers; the HTTP/ASYNC follow-up is the same as after a per-member `DECLARES_CLIENT` walk.

**N5/N6 vs `describe`:** `describe(client|producer)` already emits declaring-method hints. N5/N6 still ship because agents on `neighbors(route, in, HTTP_CALLS|ASYNC_CALLS)` often never `describe` intermediate endpoint nodes.

**Rejected from issue #163 (or deferred):**

| Candidate | Reason |
|---|---|
| Combined hint using “to see clients/routes…” | Violates v1 tone (“to see”); N1a/N1b use imperative labels instead |
| Single N1 with `or` joining two dot-key `neighbors()` calls | Exceeds 120 chars with realistic type ids; use N1a + N1b |
| N1 prescribing `member_ids` + flat `DECLARES_CLIENT` / `EXPOSES` | Superseded by dot-keys (#171); member-batch path remains valid in the graph but is not the taught happy path |
| `neighbors(route_id,'in',['HTTP_CALLS'])` when results are clients | Covered by N5; symmetric async row is N6 |
| Deriving strings from `EDGE_SCHEMA.typical_traversals` at success time | v3 owns mismatch recovery; success follow-ups are workflow-specific shapes, not `alien_subject` strings |
| `IMPLEMENTS` / `EXTENDS` / `CALLS` success hints (generic) | Still deferred per v1 §5; N4 is the only `CALLS` success row — see Open questions |
| Hints when `len(requested_edge_types) > 1` | Locked out by trigger contract §2 |
| Dot-keys in emissions for `OVERRIDDEN_BY.*` or non-type origins | Describe-only / type-origin-only per #171 |

#### `find` (second tranche)

| ID | Trigger | Template |
|---|---|---|
| F1 | `kind=='route'`, `len(results)>0`, not page-full | `handler: neighbors(['{id}'],'in',['EXPOSES'])` |
| F2 | `kind=='client'`, `len(results)>0`, not page-full | `HTTP targets: neighbors(['{id}'],'out',['HTTP_CALLS'])` |
| F3 | `kind=='producer'`, `len(results)>0`, not page-full | `async targets: neighbors(['{id}'],'out',['ASYNC_CALLS'])` |

`{id}` = `payload["results"][0]["id"]` (first row only when multiple matches — output-level sign, not per-row). If `len(results) >= limit` and `has_more_results`, page-full meta (priority 1) may still fire; F-rows use priority 2 and win the cap.

`describe(id=…)` cross-tool wording from #163 is **dropped** — `neighbors` alone reaches the handler; keeps one tool per hint.

#### `search` — deferred (PR-B optional)

Not in the v4 lock for PR-A approval. Candidate if traces warrant it:

| ID | Trigger | Template |
|---|---|---|
| S1 | `len(results)==1`, top hit `symbol_id` present | `inspect: describe(id='{symbol_id}')` |

Do not emit on multi-hit pages (noise). Dominant-score gate is a follow-up amendment if needed.

### Cap interaction

| Output | Typical success hint count | Competes with |
|---|---|---|
| `describe` | 0–3 (unchanged) | — |
| `neighbors` | 0–2 success (N1a+N1b may co-fire) + 0–1 fuzzy | v3 empty (mutually exclusive with non-empty) |
| `find` | 0–1 success | page-full meta (rare co-fire) |
| `search` | 0–1 (S1 only if PR-B ships) | weak-score meta (orthogonal triggers) |

| Co-fire (`neighbors`) | Priority | Cap outcome |
|---|---|---|
| Success-path (N*) | 2 (`PRIORITY_LEAF_FOLLOWUP`) | Retained |
| Fuzzy-strategy (v2) | 1 (`PRIORITY_META`) | Retained if room; never displaces success hint |
| N1a + N1b on same page | 2 + 2 | Both retained (dedupe only if rendered strings identical) |
| Both success + fuzzy on same page | 2 (+2 if N1a/N1b) + 1 | Up to cap 5; success hints win tie-break by higher priority |

No new priority level required.

### Module changes

- `mcp_hints.py`: add `TPL_NEIGHBORS_SUCCESS_*` constants (N1a/N1b may alias describe templates), `neighbors_success_hints()` / `find_success_hints()` helpers; extend `generate_hints` branches.
- Module header comment: point to this propose as v4 amendment.
- `MCP_HINTS_FIELD_DESCRIPTION`: **no change** (already documents describe dot-keys and empty-neighbors dot-key prohibition per #171).

## Scope

- v4 template catalog and generator logic in `mcp_hints.py`
- Unit tests in `tests/test_mcp_hints.py` (synthetic payloads +, where cheap, `neighbors_v2` round-trips on `kuzu_graph`)
- Appendix amendment note in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (traceability paragraph: v4 partial dot-key emission reversal — optional docs PR)

## Schema / Ontology / Re-index impact

- Ontology bump: **not required**
- Re-index required: **no** (hints are pure functions of MCP payloads)
- Config/tool surface changes: **none** (field `hints` already exists)

## Tests / Validation

Named scenarios (implementer contract):

| Scenario | Assert |
|---|---|
| `test_hints_neighbors_declares_methods_emits_dot_key_clients` | N1a fires on type + `DECLARES` + method targets; `{id}` = `origin_id` |
| `test_hints_neighbors_declares_methods_emits_dot_key_routes` | N1b fires on same payload as N1a (both hints present) |
| `test_hints_neighbors_declares_client_homogeneous_emits_http_calls` | N2 flat `DECLARES_CLIENT` |
| `test_hints_neighbors_declares_dot_key_client_homogeneous_emits_http_calls` | N2 composed `DECLARES.DECLARES_CLIENT` |
| `test_hints_neighbors_declares_producer_homogeneous_emits_async_calls` | N3 flat |
| `test_hints_neighbors_declares_dot_key_producer_homogeneous_emits_async_calls` | N3 composed |
| `test_hints_neighbors_declares_dot_key_exposes_homogeneous_emits_handler` | N7 |
| `test_hints_neighbors_exposes_in_methods_emits_calls` | N4 |
| `test_hints_neighbors_http_calls_in_clients_emits_declares_client` | N5 |
| `test_hints_neighbors_mixed_endpoint_kinds_silent` | client + route in one page → no success-path hints |
| `test_hints_neighbors_offset_suppresses_success_hints` | `offset > 0`, non-empty `results` → no N* hints (mirror v3 offset test) |
| `test_hints_neighbors_success_beats_fuzzy_in_cap` | constructed payload with both signals → leaf hint retained |
| `test_hints_find_route_success_emits_handler` | F1 with concrete `{id}` substitution |
| `test_hints_find_client_success_emits_http_calls` | F2 |
| `test_hints_all_v4_templates_under_120_chars` | rendered with realistic ids (include N1a/N1b via describe template constants + N7) |
| Char-cap + dedupe + cap-5 | reuse existing `finalize_hint_list` tests pattern |

S1 test (`test_hints_search_single_hit_emits_describe`) — add only if PR-B ships S1.

## Open Questions ([TBD])

1. **Emit N1a/N1b when some members lack clients/routes** — Recommended: **yes, always** when the homogeneous-method predicate holds. Lossy signs beat silence; agent may ignore. (Unchanged from original N1 rationale.)
2. **N4 dead-end risk** — After `EXPOSES` → handlers, inbound `CALLS` is often empty (entry points). Recommended: **ship N4** — advisory hint, low cost of empty follow-up; defer removal only if traces show agents loop on it.
3. **Search S1 in initial PR** — Recommended: **defer to PR-B** (or skip). Neighbors tranche delivers most of #163’s value.
4. **`find` F1 when `len(results) > 1`** — Recommended: **emit once** using `results[0]["id"]`; mention in PR description so reviewers do not expect per-row hints.
5. **Amend `HINTS-ROAD-SIGNS` appendix in-repo** — Recommended: **yes**, short “v4 amendment (#163)” paragraph documenting the second partial dot-key emission reversal (type-origin success path only), like the #161 producer block.

## Out of scope

- Per-row hints on `Edge` / `SearchHit` / `NodeRef`
- `IMPLEMENTS` / `EXTENDS` success chains (v1 §5 deferral stands until trace-backed)
- `describe` catalog changes (covered by #161 / #171 dot-key describe templates)
- `resolve(status='one')` hints
- Structured `next_actions` / pre-fetched walks
- Graph or `EDGE_SCHEMA` changes (no `success_follow_up` field on `EdgeSpec` in v4; [#172](https://github.com/HumanBean17/java-codebase-rag/issues/172) for `type_subject` alignment)
- Hint versioning field (`hints_version`)
- Conditioning on `attrs.match` / confidence for `HTTP_CALLS` / `ASYNC_CALLS` rows (strategy already covered by v2 fuzzy hint on non-empty neighbor pages)
- Member-batch `DECLARES` → `member_ids` → flat edges as the **primary** v4 teaching path (valid graph path, not promoted in success hints)

## Sequencing / Follow-ups

Suggested **2 PRs** (can collapse to 1 if reviewer prefers):

| PR | Delivers | Depends on |
|---|---|---|
| **PR-A** | N1a–N7 + neighbors tests + char-cap sweep for new templates | #171 landed |
| **PR-B** | F1–F3 + find tests + appendix traceability note; optional S1 | PR-A optional |

After land: move this file to `propose/completed/`, add `plans/PLAN-HINTS-V4.md` + `CURSOR-PROMPTS-HINTS-V4.md` if the team wants the per-PR sentinel contract (not required for a 1–2 PR effort).

**Related issues:** #161 (describe producer symmetry — landed); #163 (this propose); #171 (neighbors `DECLARES.*` dot-keys — landed). No ontology coordination with SCHEMA-V2.
