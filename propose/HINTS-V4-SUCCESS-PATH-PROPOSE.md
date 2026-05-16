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

v2 (`propose/completed/HINTS-V2-PROPOSE.md` §1) ruled out **per-row** neighbors hints and confidence thresholds. v4 keeps both rules. Success-path hints are **output-level**, keyed on echoed request fields and homogeneous endpoint kinds in the serialized payload, with **no** new graph reads and **no** per-edge-row emissions.

### Payload fields (`generate_hints` reads dicts from `.model_dump()`)

**`neighbors`** — `mcp_v2.neighbors_v2` builds:

| Key | Shape | Use |
|---|---|---|
| `success` | `bool` | must be `True` |
| `results` | `list[dict]` | each row is `Edge.model_dump()` |
| `results[i]["other"]` | `dict` | `NodeRef`: `kind`, `id`, optional `symbol_kind` |
| `results[i]["other"]["kind"]` | `"symbol" \| "route" \| "client" \| "producer"` | homogeneous-endpoint gate |
| `results[i]["other"]["symbol_kind"]` | `str \| None` | method vs type when `kind == "symbol"` |
| `requested_edge_types` | `list[str]` | echoed `edge_types` |
| `requested_direction` | `"in" \| "out"` | echoed direction |
| `offset` | `int` | echoed page offset; success hints require `0` |
| `origin_id` | `str` | first origin id |
| `subject_record` | `dict` | `NodeRecord.model_dump()` for origin; **required for N1** |

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
5. **N1 only:** `payload["subject_record"]` is present, `subject_record["kind"] == "symbol"`, and `subject_record["data"]["kind"]` (declaration kind) is one of `class`, `interface`, `enum`, `record`, `annotation`.

Priority: **`PRIORITY_LEAF_FOLLOWUP` (2)**. Fuzzy-strategy (`PRIORITY_META` 1) and v3 empty structural (`PRIORITY_META` 1) lose cap contests to success-path hints.

### Locked v4 template rows

**Substitution conventions** (match v1 `mcp_hints.py`):

- `{id}` — substituted with a **concrete** node id string (same as describe route/client rows).
- `member_ids` / `client_ids` / `producer_ids` / `handler_ids` — **batch placeholders**, never substituted; agent copies ids from `results[].other.id`.

#### `neighbors` (highest priority — implement first)

| ID | Trigger | Template (verbatim target) |
|---|---|---|
| N1 | `requested_edge_types == ['DECLARES']`, `direction=='out'`, subject_record is type Symbol (trigger §5), all `other` are method/constructor Symbols | `members: neighbors(member_ids,'out',['DECLARES_CLIENT']) or neighbors(member_ids,'out',['EXPOSES'])` |
| N2 | `['DECLARES_CLIENT']`, `direction=='out'`, all `other.kind=='client'` | `HTTP targets: neighbors(client_ids,'out',['HTTP_CALLS'])` |
| N3 | `['DECLARES_PRODUCER']`, `direction=='out'`, all `other.kind=='producer'` | `async targets: neighbors(producer_ids,'out',['ASYNC_CALLS'])` |
| N4 | `['EXPOSES']`, `direction=='in'`, all `other` are method/constructor Symbols | `callers: neighbors(handler_ids,'in',['CALLS'])` |
| N5 | `['HTTP_CALLS']`, `direction=='in'`, all `other.kind=='client'` | `declaring method: neighbors(client_ids,'in',['DECLARES_CLIENT'])` |
| N6 | `['ASYNC_CALLS']`, `direction=='in'`, all `other.kind=='producer'` | `declaring method: neighbors(producer_ids,'in',['DECLARES_PRODUCER'])` |

**N1 — dual branch vs v1 §2.1:** v1 already emits multi-hop strings with `then` (type rollup templates). N1 uses `or` to combine two branches that `describe` would emit as **separate** hints when both rollups fire — a cap tradeoff because neighbors success payloads lack per-member `edge_summary`. One advisory string, two atomic `EdgeType` literals, ≤120 chars.

**N5/N6 vs `describe`:** `describe(client|producer)` already emits declaring-method hints. N5/N6 still ship because agents on `neighbors(route, in, HTTP_CALLS|ASYNC_CALLS)` often never `describe` intermediate endpoint nodes — duplicate road signs across tools are intentional (same rationale as N1 vs describe type rollups).

**Rejected from issue #163 (or deferred):**

| Candidate | Reason |
|---|---|
| Combined hint using “to see clients/routes…” | Violates v1 tone (“to see”); N1 uses imperative labels instead |
| `neighbors(route_id,'in',['HTTP_CALLS'])` when results are clients | Covered by N5; symmetric async row is N6 |
| Deriving strings from `EDGE_SCHEMA.typical_traversals` at success time | v3 owns mismatch recovery; success follow-ups are workflow-specific two-hop shapes, not `alien_subject` strings |
| `IMPLEMENTS` / `EXTENDS` / `CALLS` success hints (generic) | Still deferred per v1 §5; N4 is the only `CALLS` success row — see Open questions |
| Hints when `len(requested_edge_types) > 1` | Locked out by trigger contract §2 |

**N1 note:** Unlike `describe` type rollups, neighbors success after `DECLARES` cannot branch on per-member `edge_summary` (not in the payload). N1 intentionally emits **one** combined clients/routes sign — same information density as the describe rollup pair, one cap slot.

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
| `neighbors` | 0–1 success + 0–1 fuzzy | v3 empty (mutually exclusive with non-empty) |
| `find` | 0–1 success | page-full meta (rare co-fire) |
| `search` | 0–1 (S1 only if PR-B ships) | weak-score meta (orthogonal triggers) |

| Co-fire (`neighbors`) | Priority | Cap outcome |
|---|---|---|
| Success-path (N*) | 2 (`PRIORITY_LEAF_FOLLOWUP`) | Retained |
| Fuzzy-strategy (v2) | 1 (`PRIORITY_META`) | Retained if room; never displaces success hint |
| Both on same page | 2 + 1 | Up to 2 hints; success hint wins any tie-break by higher priority |

No new priority level required.

### Module changes

- `mcp_hints.py`: add `TPL_*` constants + `neighbors_success_hints()` / `find_success_hints()` helpers; extend `generate_hints` branches.
- Module header comment: point to this propose as v4 amendment.
- `MCP_HINTS_FIELD_DESCRIPTION`: no change (behavior stays advisory; offset gating is test-backed, not field-description scope).

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
| `test_hints_neighbors_offset_suppresses_success_hints` | `offset > 0`, non-empty `results` → no N* hints (mirror v3 offset test) |
| `test_hints_neighbors_success_beats_fuzzy_in_cap` | constructed payload with both signals → leaf hint retained |
| `test_hints_find_route_success_emits_handler` | F1 with concrete `{id}` substitution |
| `test_hints_find_client_success_emits_http_calls` | F2 |
| `test_hints_all_v4_templates_under_120_chars` | rendered with realistic ids |
| Char-cap + dedupe + cap-5 | reuse existing `finalize_hint_list` tests pattern |

S1 test (`test_hints_search_single_hit_emits_describe`) — add only if PR-B ships S1.

## Open Questions ([TBD])

1. **Emit N1 when some members lack clients/routes** — Recommended: **yes, always** when N1’s homogeneous method predicate holds. Lossy sign beats silence; agent may ignore.
2. **N4 dead-end risk** — After `EXPOSES` → handlers, inbound `CALLS` is often empty (entry points). Recommended: **ship N4** — advisory hint, low cost of empty follow-up; defer removal only if traces show agents loop on it.
3. **Search S1 in initial PR** — Recommended: **defer to PR-B** (or skip). Neighbors tranche delivers most of #163’s value.
4. **`find` F1 when `len(results) > 1`** — Recommended: **emit once** using `results[0]["id"]`; mention in PR description so reviewers do not expect per-row hints.
5. **Amend `HINTS-ROAD-SIGNS` appendix in-repo** — Recommended: **yes**, short “v4 amendment (#163)” paragraph like the #161 producer block.

## Out of scope

- Per-row hints on `Edge` / `SearchHit` / `NodeRef`
- `IMPLEMENTS` / `EXTENDS` success chains (v1 §5 deferral stands until trace-backed)
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
| **PR-B** | F1–F3 + find tests + appendix traceability note; optional S1 | PR-A optional |

After land: move this file to `propose/completed/`, add `plans/PLAN-HINTS-V4.md` + `CURSOR-PROMPTS-HINTS-V4.md` if the team wants the per-PR sentinel contract (not required for a 1–2 PR effort).

**Related issues:** #161 (describe producer symmetry — landed); #163 (this propose). No ontology coordination with SCHEMA-V2.
