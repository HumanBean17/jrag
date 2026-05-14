# MCP Filter Frame — typed query language with one named carve-out

**Status**: draft
**Author**: Dmitriy Teriaev + Computer
**Date**: 2026-05-14
**Issue**: #117

## TL;DR

- **Frame**: MCP V2 is a typed query language for the code graph. Filters and traversal targets are strict — every input field maps to one and only one stored attribute, inapplicable inputs fail loud. `search.query` is the single carve-out: opaque natural-language or code text, ranked results.
- **Why this and not permissive**: every shipped strict surface (EdgeType literal, kind enum, direction enum) has zero issues; every shipped permissive surface (filter bag, silent-drop) accounts for #117 / #118 / part of #119 in one session.
- **Migration step 1 already shipped** (#122): `extra="forbid"` on NodeFilter + per-kind applicability validation across `search` / `find` / `neighbors`. Loud-fail surface is live.
- **What's left**: vocabulary audit (Appendix A), the 7 frame-edge decisions from #117, and `resolve` as a deferred named primitive (own propose, own PR).
- **Migration shape**: 3 PRs after this propose locks. PR-FRAME-1 (vocabulary audit + renames, no users so no deprecation aliases), PR-FRAME-2 (lock the 7 edge-question decisions as code + tests, including `describe(fqn=…)`), PR-FRAME-3 (revisit-trigger doc + lightweight local counters). `resolve` is a separate propose, not part of this migration.
- **No-users constraint**: breaking changes are allowed. No deprecation aliases, no transition-window scaffolding, no V2.x-vs-V3.0 ceremony — rename, ship, move on.
- **Builds on, does not replace, PR #89 V2 decisions**: four primitives, closed EdgeType, rollup dot-keys read-only, `_coerce_filter` lossless JSON-decoding.

---

## §1 Frame

The MCP V2 surface is a **typed query language for the code graph**. Filters and traversal targets are strict — every input field has one and only one mapping to a stored attribute, and inapplicable inputs fail loud. The `search` tool's *query* parameter is the single exception: it accepts opaque natural-language or code text and returns ranked results. Everything else — including `search`'s filter parameter — follows strict-frame rules.

This frame **rules out** smart filters on `find` / `describe` / `neighbors`, DSL-shaped queries inside `search.query`, fuzzy edge labels in `neighbors`, field aliases that map across kinds, and silent acceptance of fields that don't apply to the requested kind.

It **explicitly permits** fuzzy ranking on `search.query` (already shipped), natural-language input on `search.query`, lossless multi-form input (e.g., `_coerce_filter` accepting JSON-encoded strings), and per-kind aligned vocabulary where the concept is genuinely the same (`microservice`, `module`).

The carve-out earns its keep because `search` is fundamentally about *inexact retrieval* — outputs ranked by score, not boolean-matched. `find` / `describe` / `neighbors` are about *exact structural lookup* — the graph either has this node/edge or it doesn't. The frame traces the epistemic difference between operation classes: strict where truth is binary, permissive where truth is ranked.

---

## §2 Design principles

1. **One input field, one stored attribute.** No alias across kinds. No predicate that means different things in different contexts.
2. **Inapplicable input is loud failure, never silent drop.** The agent learns the contract from its own mistakes.
3. **Lossless multi-form input is fine; lossy input is rejected.** `_coerce_filter` accepting JSON-encoded strings is lossless. Accepting fields we'd silently discard is lossy.
4. **Strict where truth is binary, permissive where truth is ranked.** `search.query` is the only permissive surface; `search.filter` and all of `find` / `describe` / `neighbors` follow strict-frame rules.
5. **EdgeType is a closed set.** Rollup dot-keys are read-only signals on outputs, never accepted as `neighbors(edge_types=...)` values (PR #89 decision #11, preserved).
6. **Each smart behavior given up gets a named home.** Removal without relocation is forbidden — the relocation table (§5) is a hard requirement, not a courtesy.
7. **Revisit trigger is concrete, not vague.** Three issues with workflows that have no clean `search` / `resolve` / multi-call analog within 6 months reopens the frame.
8. **No users, no version ceremony.** Breaking changes are allowed; renames ship in place. Frame revision events are tracked in commit history, not version numbers.

---

## §3 Proposed surface

The four primitives (`search`, `find`, `describe`, `neighbors`) are unchanged in shape. The frame constrains how their **inputs** are interpreted.

### 3.1 Filter contract (NodeFilter)

`NodeFilter` is a typed bag with `extra="forbid"`. Each field is declared once and maps to one stored attribute on one kind. Per-kind applicability is enforced in `find` / `search` / `neighbors`.

**Already shipped (#122)**:

- `model_config = ConfigDict(extra="forbid")` — unknown fields rejected by Pydantic before any handler runs.
- `_NODEFILTER_APPLICABLE_FIELDS[kind]` — the per-kind applicable-field set, derived from the 17-field NodeFilter.
- `_nodefilter_applicability_error(kind, nf)` — returns a structured error message listing inapplicable populated fields and the applicable-field list.
- Enforced at: `search_v2` (line 525), `find_v2` (line 557), `neighbors_v2` (line 678 — the `other_kind` branch for filtered neighbor results).

**Remaining**: aligned-vocabulary audit (Appendix A) and the 7 frame-edge decisions (§3.4).

### 3.2 Traversal contract (EdgeType)

`EdgeType = Literal[9 values]` is closed. Dot-keys like `DECLARES.DECLARES_CLIENT`, `OVERRIDDEN_BY`, `OVERRIDES` are read-only output signals in `edge_summary`, never accepted as input. This is locked from PR #89 decision #11; this propose **does not revisit** it, only references it.

### 3.3 Search carve-out

`search(query: str, filter: NodeFilter | None, ...)` — `query` is opaque text, score-ranked output. `filter`, when present, follows strict-frame rules (already shipped; `_nodefilter_applicability_error` runs against `kind="symbol"` since search returns symbols today).

`search.filter` is **not** smart even when hosted inside `search`. The carve-out is precisely scoped to `query` and `score`.

### 3.4 The 7 frame-edge decisions (from #117 grilling)

The strict frame doesn't automatically answer the following questions. The propose locks each:

1. **Wildcards in structured predicates.** `fqn_prefix="com.x.*Service"` is **rejected**. Wildcard semantics are smart-by-nature (LIKE-shaped operators leak engine details into the contract). If wildcard match is the actual intent, use `search(query="com.x.*Service")` or `resolve` (deferred). **Decision: no wildcards in any structured predicate.**
2. **FQN-as-identifier in `describe`.** Today `describe` accepts `(id, kind)` — there is no `fqn` parameter. **Decision: PR-FRAME-2 adds `fqn` as a second accepted identifier shape for `describe`, accepted only when `kind="symbol"`.** Routes and Clients accept `id` only (FQN is not their natural identifier). Multi-microservice FQN collisions are resolved via a `microservice` co-parameter or fall through to `resolve` once it ships. This is an additive change to the tool schema, not a frame-only statement.
3. **Multi-value field semantics.** `microservice=["a", "b"]` means **OR** (disjunction within field). Cross-field is **AND** (conjunction across fields). This matches SQL `IN` and is the only sane structured-predicate default. Today the schema exercises this with `symbol_kinds` (multi-value OR) and `exclude_roles` (multi-value negation); the decision generalizes their semantics to any multi-value field added later. **Decision: within-field OR, cross-field AND.**
4. **Negation predicates.** `exclude_roles` exists today. Negation is **strict structured predicate**, not smart behavior — it's just "field NOT IN list". Generalizable as `exclude_<fieldname>` parameters where useful. **Decision: negation predicates are strict; add `exclude_<fieldname>` mirrors only where vocabulary audit (Appendix A) flags a real need.**
5. **Empty-filter semantics.** `find(kind="client", filter={})` means **"all clients of the requested kind"** (current behavior, locked). `filter=None` is equivalent. The agent can always page through if the result is large. **Decision: empty filter = no predicate, full result set; safety is enforced by pagination, not by required predicates.**
6. **Revisit-trigger tightening.** "N legitimate workflows hit fail-loud" is sharpened: **N=3**, "legitimate" = "issue filed where the workflow has no clean analog under `search` + (deferred) `resolve` + multi-call patterns under the strict frame". **Decision: revisit when 3 such issues accumulate within 6 months of frame lock.**
7. **Identifier-resolution fallback (pre-`resolve`).** Until `resolve` ships, identifier-resolution workflows fall back to `search` + `describe`-per-candidate. **Decision: document this fallback in the agent-facing tool descriptions; do not gate frame lock on `resolve` being ready.** Not a user-facing transition — the AMA agent is the only consumer.

### 3.5 `resolve` as named-but-deferred primitive

`resolve(identifier: str, hint_kind: str | None) → ResolveOutput` is named here as the strict frame's escape valve for identifier-shaped lookups (one node / N candidates with reasons / no match). It is **designed in its own propose**, not this one, for two reasons:

- Conflating frame decision with new-tool design is two decisions in one thread.
- `resolve`'s exact shape depends on what aligned-vocabulary audit (Appendix A) does to `microservice`, `target_service`, and the FQN-vs-ID question.

**Decision: open `propose: design \`resolve\` tool` as a follow-up issue immediately after this propose locks.**

---

## §4 Use-case re-walk

18 realistic agent workflows, walked through the strict-frame surface. Each row: how many tool calls, which primitives, any awkwardness.

| # | Use case | Calls | Chain |
|---|---|---|---|
| UC1 | Agent finds all clients in microservice `operator-api` | 1 | `find(kind="client", filter={microservice:"operator-api"})` |
| UC2 | Agent finds clients in `operator-api` calling target service `partner-api` | 1 | `find(kind="client", filter={microservice:"operator-api", target_service:"partner-api"})` |
| UC3 | Agent finds clients with FQN starting `com.foo.assign` (bug surface that opened #117) | 1 (correctly) | `find(kind="symbol", filter={fqn_prefix:"com.foo.assign", symbol_kind:"interface"})` — note: this is the **right shape**; client FQN was never a real concept |
| UC4 | Agent typos a Symbol-only field on `find(kind="client")` | 1 (loud fail) | Returns `success=False, message="Invalid filter for kind='client': populated field(s) not applicable: [fqn_prefix]. Applicable field(s): [microservice, module, source_layer, client_kind, target_service, target_path_prefix, client_method]"`. Already shipped (#122). |
| UC5 | Agent searches for "smartcare assign client" by free text | 1 | `search(query="smartcare assign client")` — ranked results, score-based |
| UC6 | Agent has FQN `com.foo.SmartCareAssignClient` and wants the node | 1 | `describe(fqn="com.foo.SmartCareAssignClient")` — bijective for Symbols (decision §3.4.2) |
| UC7 | Agent has identifier `"smartcare"` (short service name) and wants canonical | 1 (or 2) | Pre-`resolve`: `search(query="smartcare")` → ranked results. Post-`resolve`: `resolve("smartcare", hint_kind="microservice")` → canonical or candidates. §3.4.7 documents the pre-`resolve` fallback. |
| UC8 | Agent gets a class and wants all overriders | 2 | `describe(id=X)` → read `edge_summary["OVERRIDDEN_BY"].out` → `neighbors(in, [EXTENDS, IMPLEMENTS])` on declarer + class-level walk. Note: `OVERRIDDEN_BY` stays a read-only signal (PR #89 decision #11); decomposition lives in #118. |
| UC9 | Agent gets a class and wants all clients that route to its members | 2 | `describe(id=X)` → read `edge_summary["DECLARES.DECLARES_CLIENT"].out` → `neighbors(out, [DECLARES])` → `neighbors(out, [DECLARES_CLIENT])` per member. The 2-call rollup pattern. |
| UC10 | Agent passes wildcard `fqn_prefix="com.*.assign"` | 1 (loud fail) | Returns `success=False, message="..."` — wildcards rejected (decision §3.4.1). Agent's next step: `search(query="com assign")` or wait for `resolve`. |
| UC11 | Agent passes `microservice=["operator-api", "partner-api"]` | 1 | OR within field, returns clients in either microservice. Decision §3.4.3. |
| UC12 | Agent passes `filter={}` to `find(kind="symbol")` with no kind constraint | 1 | Returns first page of all Symbols. Pagination is the safety net (decision §3.4.5). |
| UC13 | Agent passes filter `{microservice:"x", target_service:"y", fqn_prefix:"z"}` with `kind="client"` | 1 (loud fail) | `fqn_prefix` not applicable to `client`. Already shipped (#122). Message lists applicable fields. |
| UC14 | Agent passes negated filter `{exclude_roles:["CONTROLLER"]}` on `kind="symbol"` | 1 | Returns Symbols whose `role NOT IN [CONTROLLER]`. Already shipped; decision §3.4.4 keeps it strict. |
| UC15 | Agent uses `search` with structured-looking query `"microservice:operator-api role:CONTROLLER"` | 1 (ranked) | `search.query` is opaque — the query string is treated as text, returns ranked results based on text/vector match. **No DSL parsing.** If structured predicates are needed: `find` with structured filter. (The carve-out is bounded.) |
| UC16 | Agent passes `kind="route"` with `filter={http_method:"POST", path_prefix:"/api/v1"}` | 1 | Returns routes matching both predicates (cross-field AND, decision §3.4.3). |
| UC17 | Agent passes `kind="route"` with `filter={client_method:"GET"}` | 1 (loud fail) | `client_method` is Client-only; loud fail with applicable fields listed. |
| UC18 | Agent calls `neighbors(node_id=..., edge_types=["DECLARES.DECLARES_CLIENT"])` | 1 (loud fail) | Dot-keys rejected by `EdgeType` Literal (PR #89 invariant). Hint message can be added (PR #120 family) saying "this is a read-only rollup signal; use neighbors twice instead". |

**Findings from the re-walk:**

- **No missing primitive surfaces.** Every UC has a clean answer under the strict frame, even if some need 2 calls or the pre-`resolve` `search` fallback (UC7).
- **UC7 is the workflow most visibly degraded** before `resolve` ships — but `search` covers it acceptably, and the agent learns the pattern from the tool description.
- **UC8 and UC9 (rollup decomposition)** are honest 2-call patterns. They're the reason #118 is open: someone might prefer dot-notation in `neighbors`, but that's a separate frame decision.
- **UC15 is the most interesting carve-out boundary.** An agent might try to write structured queries inside `search.query`. The frame holds: `search` is text-in, ranked-out; `find` is structured-in, exact-out. No DSL in the middle.

---

## §5 What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Smart fuzzy-match in `find` filter fields (e.g., `target_service="smartcare"` matching `"operator-api"`) | Belongs in `resolve` (deferred propose). Strict frame won't ambiguity-resolve inside structured predicates. |
| Wildcard / regex inside structured predicates (`fqn_prefix="com.*.x"`) | Decision §3.4.1. Wildcards leak engine semantics. Use `search` or `resolve`. |
| Cross-kind shortcuts (`find` returning Symbols + Routes + Clients in one call) | Multiple `find` calls, one per kind. Composability beats convenience. |
| DSL parsing inside `search.query` (`"microservice:x role:y"`) | Carve-out is bounded to text+score. Structured-in goes to `find`. |
| Field aliases across kinds (one `fqn_prefix` working on both Symbols and Clients) | Each kind has its own typed predicate set. Aligned vocabulary only where the concept is genuinely the same (Appendix A). |
| Auto-traversal of rollup dot-keys in `neighbors` | PR #89 decision #11. Stays out of `EdgeType` Literal. Hints (PR #120 family) teach the 2-hop pattern. |
| Designing `resolve` in this propose | Separate propose. Frame names the primitive; design is its own thread. |
| Telemetry hooks for revisit-trigger counting | Migration PR-FRAME-3 scope, not propose scope. Lock the trigger here; instrument later. |

---

## §6 Migration — 3 PRs

Migration step 1 (extra="forbid" + per-kind applicability) **already shipped as #122**. The remaining migration is 3 PRs.

### PR-FRAME-1 — Vocabulary renames

Apply the Appendix A field-by-field audit. Rename misaligned vocabulary in place — **no deprecation aliases, no users to migrate**. Update `_NODEFILTER_APPLICABLE_FIELDS`, `NodeFilter`, all call-site references, and tests in one commit. Verify the inapplicability error message reflects renamed fields.

### PR-FRAME-2 — Lock the 7 frame-edge decisions in code

Code + tests for decisions §3.4.1–§3.4.7. Wildcard rejection, `describe(fqn=…)` for Symbol kind (additive parameter, not an alias), OR within field / AND across fields, negation predicate mirrors where flagged by audit, empty-filter pagination test, revisit-trigger doc string, identifier-resolution fallback note in tool descriptions.

### PR-FRAME-3 — Lightweight local counters + tool-description updates

Local counter for revisit-trigger tracking — a stderr counter or a small local file recording fail-loud events per workflow shape. Not product telemetry; no observability stack. Update tool descriptions to teach the contract: applicable fields per kind, identifier shapes per kind, identifier-resolution fallback for the AMA agent.

`resolve` ships as its own propose + PR series, not part of this migration.

---

## §7 Decisions taken (no longer open)

1. **Frame**: typed query language with strict structured predicates and one `search.query` carve-out (§1).
2. **`extra="forbid"` on NodeFilter** is locked (shipped #122).
3. **Per-kind applicability validation** is locked (shipped #122) and enforced at `search`, `find`, `neighbors` entry points.
4. **EdgeType remains closed.** Dot-keys stay read-only output signals (PR #89 decision #11, restated).
5. **Wildcards inside structured predicates are rejected.** §3.4.1.
6. **`describe(fqn=...)` is a lossless alias for Symbol nodes only.** Routes and Clients accept `id` only. §3.4.2.
7. **Within-field OR, cross-field AND.** §3.4.3.
8. **Negation predicates are strict structured predicates.** Add `exclude_<fieldname>` mirrors only where Appendix A flags a real need. §3.4.4.
9. **Empty filter = no predicate; pagination is the safety net.** §3.4.5.
10. **Revisit trigger: 3 legitimate workflows fail-loud within 6 months reopens.** §3.4.6.
11. **Identifier-resolution fallback: `search` + `describe`-per-candidate is the documented pattern until `resolve` ships.** §3.4.7.
12. **`resolve` is named in the frame, designed in a separate propose.** §3.5.
13. **`search.filter` follows strict-frame rules even though hosted inside `search`.** Carve-out is precisely scoped to `query` and `score`. §3.3.
14. **No users, no version ceremony.** Renames ship in place; no V2.x / V3.0 distinction. Frame revision events (§3.4.6 revisit trigger) are noted in commit history, not version numbers.
15. **PR #89 invariants are preserved, not relitigated**: four primitives, closed EdgeType, rollup dot-keys read-only, `_coerce_filter` lossless JSON-decoding.

---

## §8 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agent workflows hit fail-loud often pre-`resolve` (between #122 ship and `resolve` ship). | Tool description teaches the `search` + `describe`-per-candidate fallback. Local counters (PR-FRAME-3) track fail-loud events; if rate is high, accelerate `resolve` propose. |
| `resolve` design takes longer than expected; identifier-resolution fallback stays in use. | `search` covers most identifier-resolution workflows under the frame. Frame doesn't gate on `resolve` (decision §3.4.7). |
| Wildcard rejection (§3.4.1) feels too strict to agents who try `fqn_prefix="com.x.*"`. | Error message can hint: "wildcards not supported in structured predicates; use search(query='...') for ranked text match". Hint candidates feed back into PR #120 family. |
| Strict frame produces verbose error messages that crowd the LLM context. | Error messages are structured (`success=False, message=...`), not chatty. Applicable-field list is the only structured payload. |
| The carve-out boundary on `search.query` blurs over time (someone tries DSL parsing). | Decision §15 (PR #89 invariants preserved) + decision §3.3 (search.filter strict) + tool description language. Frame is restateable; if blur happens, that's a revisit-trigger event. |
| Revisit-trigger N=3 is arbitrary; could be too lenient or too strict. | First revisit is a learning event. If 3 turns out to be wrong, decision §3.4.6 changes — cheap to amend, no users to migrate. |

---

## Appendix A — Aligned-vocabulary audit (the work this propose hands to PR-FRAME-1)

Vocabulary audit across the 17 NodeFilter fields — **concept alignment, not applicability bugs**. Today `_NODEFILTER_APPLICABLE_FIELDS` already keeps fields disjoint per kind; this audit asks whether two same-concept fields under different names should share a name across kinds. Each row: where the field applies, what the concept is, and whether the name is aligned with other kinds that share the concept.

| Field | Applies to | Concept | Alignment status | Action |
|---|---|---|---|---|
| `microservice` | symbol, route, client | "which service does this live in" | ✓ aligned | keep |
| `module` | symbol, route, client | "which module/package within service" | ✓ aligned | keep |
| `source_layer` | symbol, client | "architectural layer (controller/service/etc.)" | partial — Symbol uses `role`, Client uses `source_layer` | **AUDIT**: is `source_layer` on Client the same concept as `role` on Symbol? If yes, rename Client's `source_layer` → `role` with alias. If no, document the distinction. |
| `role` | symbol | architectural role (CONTROLLER, SERVICE, REPO, ...) | symbol-only by design today | see `source_layer` audit |
| `exclude_roles` | symbol | negation mirror of `role` | symbol-only | keep; mirror naming pattern for other negations from audit |
| `annotation` | symbol | Java annotation present on declaration | symbol-only | keep |
| `capability` | symbol | semantic capability tag | symbol-only | keep |
| `fqn_prefix` | symbol | FQN starts-with match | symbol-only today; **the bug surface** | **AUDIT**: do Routes / Clients have a natural FQN concept? Routes: no (URL path is the natural identifier). Clients: maybe — the declaring class/method. Decision likely: leave symbol-only; Client's "FQN" is the declarer Symbol's, reachable via `neighbors`. |
| `symbol_kind` | symbol | one of class/interface/enum/record/annotation/method/constructor | symbol-only | keep |
| `symbol_kinds` | symbol | OR of `symbol_kind` values | symbol-only; multi-value mirror | keep; matches decision §3.4.3 (within-field OR) |
| `http_method` | route | GET / POST / PUT / DELETE / ... | route-only | keep |
| `path_prefix` | route | URL path starts-with | route-only | **AUDIT**: vs Client's `target_path_prefix`. Symmetric concept (URL prefix), different sides (server-side route vs client-side target). Names are intentionally distinct because the concept *is* distinct (where the URL lives in the conversation). Decision: keep both. |
| `framework` | route | Spring/JAX-RS/etc. | route-only | keep |
| `client_kind` | client | RestTemplate / Feign / WebClient / ... | client-only | keep |
| `target_service` | client | which service this client calls | client-only | **AUDIT**: could a `target_microservice` rename make this consistent with `microservice`? Or is "service" the right abstraction (allows future non-microservice targets)? Decision: keep `target_service` (more general); document the distinction. |
| `target_path_prefix` | client | client-side URL target prefix | client-only | see `path_prefix` audit; keep both |
| `client_method` | client | HTTP method the client uses | client-only | **AUDIT**: vs `http_method` on Route. Same HTTP-method concept on different sides. Rename `client_method` → `http_method` with cross-kind aligned semantics — **this is the most plausible cross-kind alignment in the audit**. Post-rename, `_NODEFILTER_APPLICABLE_FIELDS` lists `http_method` under both `route` and `client`; PR-FRAME-2 tests cover both kinds with the same predicate name. |

**Summary**: 3 audit decisions to make in PR-FRAME-1 — `source_layer`/`role`, `target_service` vs `target_microservice`, and `client_method` vs `http_method` cross-kind alignment. With no users, renames ship in place; no alias scaffolding.

---

## References

- `mcp_v2.py:21–31` — `EdgeType` Literal (closed set, kept)
- `mcp_v2.py:59–82` — `NodeFilter` (`extra="forbid"` shipped #122)
- `mcp_v2.py:83–93` — `_NODEFILTER_APPLICABLE_FIELDS` (per-kind map, shipped #122)
- `mcp_v2.py:130–146` — `_nodefilter_inapplicable_fields` / `_nodefilter_applicability_error` (shipped #122)
- `mcp_v2.py:525, 557, 678` — enforcement at search/find/neighbors entry points (shipped #122)
- `mcp_v2.py:161–180` — `_coerce_filter` (lossless JSON-decoding, kept)
- Issue #117 — frame direction locked
- Issue #118 — rollup decomposition (depends on §3.2 + UC8/UC9 in this propose)
- Issue #122 — lossless-permissive shipped; this propose's migration step 1
- PR #89 — V2 redesign propose; this propose builds on, does not replace
- PR #120 — hints propose (paused; revisit after this and #118 lock)
