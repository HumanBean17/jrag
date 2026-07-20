<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# `resolve` tool — strict-frame identifier resolution for the MCP V2 surface

**Status**: completed (landed: PR #135 + PR #140; plan: [`plans/completed/PLAN-RESOLVE-TOOL.md`](../plans/completed/PLAN-RESOLVE-TOOL.md))
**Author**: Dmitriy Teriaev + Computer
**Date**: 2026-05-15
**Builds on**: [`propose/completed/MCP-FILTER-FRAME-PROPOSE.md`](completed/MCP-FILTER-FRAME-PROPOSE.md) (locked, #117 shipped via PR-FRAME-1 → PR-FRAME-3)

## TL;DR

- **What**: a fifth MCP V2 tool, `resolve(identifier: str, hint_kind: NodeKind | None) → ResolveOutput`, that turns an identifier-shaped input (FQN, short service name, route path, client target) into one of three loud states: a single canonical `NodeRef`, a ranked list of candidates with reasons, or no match.
- **Why**: the locked strict frame (§3.5 of the filter-frame propose) named `resolve` as its escape valve for identifier-shaped lookups; until it exists, agents fall through to `search` + `describe`-per-candidate, which is inexact-by-design and pushes the "is this the canonical node?" call onto the agent every time.
- **Output shape**: discriminated envelope `{status: "one" | "many" | "none", node?: NodeRef, candidates?: [{node, score, reason}], message?: str}`. Agent code branches once on `status`; the candidate list, when present, is ordered and carries a per-candidate `reason` (`"exact_fqn"`, `"fqn_suffix"`, `"client_target"`, etc. — see §3.4 for the closed set).
- **Hint shape**: `hint_kind` only — exactly as the filter-frame propose specified. No `microservice` co-hint, no open-ended `hints: dict`. FQN collisions across microservices surface as `status="many"` with the microservice visible in each candidate's `NodeRef`; the agent disambiguates with a follow-up call. Smart-by-nature hint bags are explicitly out (frame principle 1: one input field, one stored attribute).
- **Scope**: all three kinds (Symbol, Route, Client). Symbols are the dominant case and the cleanest contract; Route and Client resolution earn their seat because both already carry identifier-shaped attributes (`path` template for routes, `target_service` + `target_path_prefix` for clients) and the alternative — one tool per kind — fragments the agent's mental model.
- **Frame impact**: removes the §3.4.7 `search` + `describe`-per-candidate fallback wording from all four existing tool descriptions. `resolve` is now the primitive for "I have a near-identifier, give me the node"; `search` is the primitive for "I have natural-language or code text, give me ranked hits". The carve-out boundary in the filter frame stays — `resolve` is strict, not permissive.
- **Migration**: 2 PRs after this propose locks. PR-RESOLVE-1 (`resolve` tool: schema, handler, candidate ranking, three-status envelope, tests; `ResolveReason` placed in `java_ontology.py`). PR-RESOLVE-2 (tool-description sweep: remove the §3.4.7 fallback wording from `server.py` tool descriptions and `_INSTRUCTIONS`, the `describe_v2` collision `hint_message`, `docs/AGENT-GUIDE.md`, and the `README.md` MCP tool table). No deprecation aliases — no users, breaking changes allowed. Adding the tool does not bump `ontology_version`.
- **Builds on, does not change**: the four existing primitives, the closed `EdgeType`, the strict NodeFilter contract, and the `search.query` carve-out are all untouched.

---

## §1 Frame

`resolve` is **the strict-frame primitive for identifier-shaped lookups**. It accepts a free-form identifier string, a single optional kind hint, and answers with one of three loud states: this is your node, here are the candidates, there is no match.

This frame **rules out**:

- Smart fuzzy-match inside `find`'s structured predicates (`target_service="smartcare"` matching `"operator-api"`) — that work goes to `resolve`, not to `find`.
- Hint bags richer than `hint_kind` — including the `microservice` co-hint we considered. The filter-frame propose's principle 1 ("one input field, one stored attribute") binds `resolve` too. Co-hints would re-introduce the smart-by-nature filter behavior the frame was written to remove.
- Returning a single best-guess node when the input is ambiguous. Ambiguity is a contract signal, not a thing to silently rank-away.
- `resolve` as a synonym for `search`. `search` is opaque text → ranked hits; `resolve` is identifier text → typed status. The agent picks based on what it has, not what it wants.

**Relation to `describe(fqn=…)` (delta vs filter-frame §3.4.2).** The shipped `describe_v2` resolves FQN collisions with first-match-plus-hint behavior; the filter-frame propose had sketched a `microservice` co-parameter on `describe` for the same purpose. This propose standardizes collision disambiguation on `resolve(…, hint_kind="symbol") → status="many"` instead. Post-`resolve`, the agent-facing guidance is **prefer `resolve` over `describe(fqn=…)` when an FQN may collide**; `describe(fqn=…)` may keep its first-match behavior, or be tightened in a follow-up. No `microservice` parameter is added to `describe`.

It **explicitly permits**:

- Multiple identifier shapes per kind (Symbol: FQN, short name, `sym:`-prefixed id; Route: path template, `route:`/`r:` id; Client: `client:`/`c:` id, target-service alias). The frame's "lossless multi-form input" rule (principle 3) is what lets one tool absorb these without smart-by-nature behavior — each form maps to a single typed lookup, not to fuzzy interpretation.
- A `status="many"` result with a small ranked candidate list when the identifier matches multiple nodes. This is **not** the `search` carve-out reappearing — ranking here is over a closed set of exact-or-near-exact identifier matches with explicit per-candidate reasons, not over a full corpus by score.
- Loud failure (`status="none"`) when nothing matches. The agent falls back to `search` for genuinely fuzzy queries.

The carve-out earns its keep because identifier resolution is structurally different from both `find` (filter-based set selection) and `search` (text-based ranked retrieval). Resolution is **canonicalization** — many-input-shapes → one-typed-output — and it deserves a primitive rather than living as an unwritten contract on top of `search`.

---

## §2 Design principles

1. **Identifier-shaped, not query-shaped.** `resolve` accepts strings the agent already believes name a single node. If the agent doesn't have an identifier-shaped input, the right tool is `search`.
2. **Three loud states, no silent best-guess.** `status` is the agent's branch point. When ambiguity exists, the agent sees it.
3. **`hint_kind` only.** Any richer hint surface re-creates the smart-bag failure mode the filter frame eliminated. Disambiguation across stored attributes happens via a follow-up call, not by stuffing predicates into `resolve`.
4. **Candidate reasons are a closed vocabulary.** Each candidate carries a `reason` from a small, named set. This is the analog of the closed `EdgeType` set — explicit categories beat free-text rationale.
5. **Lossless multi-form input is fine; lossy is rejected.** Accepting FQN-or-id-or-short-name is lossless (each form maps deterministically to a typed lookup). Accepting "anything close enough" is lossy and belongs to `search`.
6. **Frame-aligned output shape.** `ResolveOutput` follows the existing `*Output` pattern (`success`, optional `message`) but adds the `status` discriminator. Existing primitives are not retrofitted.
7. **Composability over convenience.** `resolve` returning `status="many"` plus a follow-up `describe(id=…)` is two calls. That's correct. Bundling the describe call into `resolve` would couple two concerns.

---

## §3 Proposed surface

### 3.1 Tool signature

```python
def resolve(
    identifier: str,
    hint_kind: Literal["symbol", "route", "client"] | None = None,
) -> ResolveOutput: ...
```

`identifier` is the only required input. `hint_kind`, when present, constrains the lookup to a single kind; when absent, `resolve` searches across all three and the candidate list (if any) carries each candidate's kind via its `NodeRef`.

### 3.2 Output envelope

```python
ResolveStatus = Literal["one", "many", "none"]

class ResolveCandidate(BaseModel):
    node: NodeRef
    score: float
    reason: ResolveReason  # closed Literal — see §3.4

class ResolveOutput(BaseModel):
    success: bool
    status: ResolveStatus
    node: NodeRef | None = None        # populated iff status == "one"
    candidates: list[ResolveCandidate] = Field(default_factory=list)  # populated iff status == "many"
    message: str | None = None         # human-readable diagnostic; always present when status != "one"
```

**Invariants (well-formed input, `success == True`):**

- `status == "one"` ⟺ `node is not None` and `candidates == []`.
- `status == "many"` ⟺ `node is None` and `len(candidates) >= 2`.
- `status == "none"` ⟺ `node is None` and `candidates == []` and `message` explains the miss.

**Invariants (malformed input, `success == False`):**

- `status == "none"`, `node is None`, `candidates == []`, `message` is non-empty and starts with `"Invalid identifier:"`.
- Rationale: the response schema requires `status` to be set on every call. Reusing `"none"` for malformed input keeps a single non-nullable type for `status` without inventing a `"malformed"` value the agent would have to switch on separately — the agent already branches on `success` first, and on `status` only when `success == True`.

These invariants are checked at handler exit; a violation is a code bug, not a contract surface.

### 3.3 Input forms accepted per kind

The closed list of identifier shapes `resolve` recognizes. Anything outside this list either falls through to `status="none"` or, if it's structurally invalid (empty string, only whitespace), returns `success=False` with `Invalid identifier:` — same loud-fail style as the strict frame.

| Kind | Accepted identifier shapes |
|---|---|
| Symbol | `sym:`-prefixed canonical id; fully-qualified name (`com.foo.Bar`); FQN suffix (`Bar`, `foo.Bar`); short symbol name when unambiguous within the requested `hint_kind="symbol"`. |
| Route | `route:` or `r:`-prefixed canonical id; HTTP-method-and-path (`"GET /api/v1/customers"`); path template alone (`"/api/v1/customers/{id}"`); microservice-qualified path (`"operator-api GET /api/v1/customers"`). |
| Client | `client:` or `c:`-prefixed canonical id; `target_service` value (`"smartcare"`, `"operator-api"`) — returns the Client node(s) targeting that service; `target_service + target_path_prefix` pair (`"smartcare /api/v1/cards"`). |

The same identifier text may match multiple kinds when `hint_kind` is omitted (e.g., `"customers"` could be a route fragment and a symbol short name). That's a `status="many"` result, kind visible per candidate.

### 3.4 Candidate reasons (closed Literal)

```python
ResolveReason = Literal[
    "exact_id",            # the identifier was a canonical id and a row matched
    "exact_fqn",           # Symbol: identifier exactly equals stored fqn
    "fqn_suffix",          # Symbol: identifier is a suffix of fqn (one or more dots match)
    "short_name",          # Symbol: identifier matches the unqualified name (post-last-dot)
    "route_template",      # Route: identifier matched the stored path template
    "route_method_path",   # Route: identifier was "<METHOD> <path>" and both matched
    "client_target",       # Client: identifier matched target_service
    "client_target_path",  # Client: identifier was "<target_service> <path_prefix>" and both matched
]
```

Adding a reason is a frame decision (new `ResolveReason` literal + new candidate-generation branch); not a casual change. The reason set is small on purpose — five Symbol reasons, two Route reasons, two Client reasons, plus the universal `exact_id`. If the agent needs a new reason, the most likely answer is that the workflow should go through `find` or `search` instead.

### 3.5 Ranking among candidates

Within `status="many"`, candidates are ordered by the following tiebreak chain:

1. **Reason priority** — `exact_id` > `exact_fqn` / `route_method_path` / `client_target_path` > `fqn_suffix` / `route_template` > `short_name` / `client_target`. Roughly: how unambiguous the match was.
2. **Specificity** — longer matched substring beats shorter (e.g., `com.foo.bank.SmartCare` beats `bank.SmartCare` when the input was `com.foo.bank.SmartCare`).
3. **Stable id** — for any remaining ties, sort by `node.id` ascending so output is deterministic.

`score` is exposed for telemetry / agent display but agents must not branch on raw `score` — the contract is `status` + reason priority. Score values are not stable across implementation revisions.

### 3.6 Status decision rule

```
matches = generate_candidates(identifier, hint_kind)
matches = dedup_by_node_id(matches)  # generator paths can overlap (e.g., short_name and fqn_suffix
                                     # both matching the same Symbol). Dedupe before counting.

if len(matches) == 0:
    return ResolveOutput(success=True, status="none", message=...)
if len(matches) == 1:
    return ResolveOutput(success=True, status="one", node=matches[0].node)
return ResolveOutput(success=True, status="many", candidates=matches[:K])
```

The cap `K` is a small constant (e.g., 10) — `resolve` is not a paginated tool. If a reasonable identifier returns more candidates than `K`, the agent's next move is `search` or a more-qualified identifier, not pagination through `resolve`. `K` is a tunable in the handler, not a tool parameter.

### 3.7 Removed from existing tool descriptions

The filter-frame propose's §3.4.7 codified the pre-`resolve` fallback (`search` + `describe`-per-candidate) in agent-facing tool prose. When `resolve` ships, that prose is **removed**, not merely amended. The four primitive descriptions stop mentioning the fallback; the `resolve` description names itself as the primitive for identifier-shaped lookups. Documenting both paths invites the agent to reach for the wrong one — see anti-pattern (1) in the propose-author flow.

The agent-facing surfaces that need a sweep are broader than the four tool descriptions. PR-RESOLVE-2's checklist (§6) enumerates them.

---

## §4 Use-case re-walk

17 realistic identifier-shaped workflows. Each row: which `resolve` shape it exercises, expected `status`, follow-up call (if any), notes.

| # | Use case | Call | Expected status | Follow-up | Notes |
|---|---|---|---|---|---|
| UC1 | Agent has the canonical id `sym:com.foo.SmartCareAssignClient` | `resolve("sym:com.foo.SmartCareAssignClient")` | `one` | `describe(id=…)` for details | Trivial `exact_id` path |
| UC2 | Agent has full FQN `com.foo.SmartCareAssignClient` | `resolve("com.foo.SmartCareAssignClient", hint_kind="symbol")` | `one` (unless multi-microservice collision) | `describe(fqn=…)` | `exact_fqn` reason |
| UC3 | Agent has FQN that collides across two microservices | `resolve("com.foo.AssignClient", hint_kind="symbol")` | `many` | `describe(id=…)` on chosen candidate, or filter `find` by `microservice` first | Per-candidate `NodeRef.microservice` is visible — agent disambiguates without a `microservice` co-hint |
| UC4 | Agent has FQN suffix `SmartCareAssignClient` | `resolve("SmartCareAssignClient", hint_kind="symbol")` | `one` or `many` | depends on status | `fqn_suffix` reason; ambiguity surfaces honestly |
| UC5 | Agent has short symbol name `AssignClient` | `resolve("AssignClient", hint_kind="symbol")` | `many` (typical) | `describe(id=…)` after agent picks | `short_name` reason; the dominant ambiguous-input case |
| UC6 | Agent has short service-shaped name `"smartcare"` | `resolve("smartcare")` | `many` (Clients targeting it) or `one` if only one Client | follow-up `find(kind="client", filter={target_service:"smartcare"})` for full list | `client_target` reason; this is UC7 of the filter-frame propose, now no longer a `search` fallback |
| UC7 | Agent has identifier `"smartcare"` and wants only Clients | `resolve("smartcare", hint_kind="client")` | as above, kind-constrained | as above | `hint_kind` narrows the search space; no co-hints required |
| UC8 | Agent has HTTP route `"GET /api/v1/customers"` | `resolve("GET /api/v1/customers", hint_kind="route")` | `one` (typical) | `describe(id=…)` | `route_method_path` reason |
| UC9 | Agent has bare path `/api/v1/customers/{id}` | `resolve("/api/v1/customers/{id}", hint_kind="route")` | `one` or `many` (multiple methods on same path) | `describe(id=…)` per candidate | `route_template` reason |
| UC10 | Agent has microservice-qualified path `"operator-api GET /api/v1/customers"` | `resolve("operator-api GET /api/v1/customers", hint_kind="route")` | `one` | `describe(id=…)` | Multi-token identifier — still strict, still typed |
| UC11 | Agent has client-target pair `"smartcare /api/v1/cards"` | `resolve("smartcare /api/v1/cards", hint_kind="client")` | `one` or `many` | `describe(id=…)` per candidate | `client_target_path` reason |
| UC12 | Agent passes empty string `""` | `resolve("")` | n/a — `success=False` | n/a | `message="Invalid identifier: empty string"`; strict-frame loud-fail style |
| UC13 | Agent passes whitespace-only `"   "` | `resolve("   ")` | n/a — `success=False` | n/a | Same loud-fail; trimmed input is empty |
| UC14 | Agent passes a query-shaped sentence `"the client that handles smartcare assignments"` | `resolve("the client that handles smartcare assignments")` | `none` | Agent falls back to `search` | This is the correct boundary — `resolve` does not parse natural language |
| UC15 | Agent passes a wildcard `"com.foo.*Service"` | `resolve("com.foo.*Service", hint_kind="symbol")` | `none` (no candidate generator matches a literal `*`) | Agent uses `search` instead | Mirrors filter-frame decision §3.4.1 — no wildcards in structured surfaces |
| UC16 | Agent passes an identifier matching multiple kinds with no `hint_kind` | `resolve("customers")` | `many` (cross-kind) | `describe(id=…)` on chosen candidate | Each candidate's `NodeRef.kind` makes the kind explicit — frame holds |
| UC17 | Agent passes a route path with no `hint_kind` | `resolve("/api/v1/customers/{id}")` | `one` (Route, since other kinds don't match the path shape) or `many` | `describe(id=…)` | Identifier shape is enough for the candidate generator to scope itself |

**Findings from the re-walk:**

- **No missing primitive surfaces.** Every UC has a clean answer; the previously-degraded UC7 (filter-frame propose) is now first-class.
- **Cross-kind `resolve` without `hint_kind`** (UC16, UC17) earns its keep — the agent occasionally has an identifier-shaped string without strong prior on kind. `NodeRef.kind` makes the disambiguation honest in the response, not in a hint.
- **Three "none" cases (UC14, UC15, the one-no-match miss in UC5)** point the agent at `search`. The chain `resolve → search` is now the canonical "I tried exact, falling back to fuzzy" pattern.
- **UC3 (multi-microservice collision)** is the canonical reason `microservice` is *not* a co-hint. Disambiguation by stored attribute happens via `find` or via picking from the candidate list, both of which use existing primitives.
- **UC12 / UC13** stay strict — empty / whitespace-only input is `success=False`, not `status="none"`. The distinction matters: `none` means "your identifier was well-formed and didn't match anything"; `success=False` means "your call was malformed before we could try". The filter-frame propose's loud-fail discipline applies.

---

## §5 What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| `microservice` co-hint | Re-introduces smart-bag behavior the filter frame eliminated. Cross-microservice FQN collisions surface as `status="many"` with `NodeRef.microservice` per candidate; the agent disambiguates by picking from the candidate list (or, if the workflow is more naturally set-shaped, by calling `find(kind="symbol", filter={"microservice": ...})` separately). |
| Open-ended `hints: dict` | Even further into smart-bag territory. One typed input field per parameter is the principle. |
| Wildcards / regex in `identifier` | Decision aligned with filter-frame §3.4.1. `resolve` is strict on the identifier shape; wildcards belong to `search`. |
| Returning a single best-guess `node` when input is ambiguous | Silent rank-away. The agent must see ambiguity to decide. |
| Single best-guess + a `confidence: float` field | A smarter version of the same anti-pattern. Confidence numbers without explicit branch points push the decision back onto the agent every call. |
| Bundling `describe`-payload into `ResolveOutput` | Couples two concerns. The agent's normal next step after `status="one"` is `describe`, which is a separate primitive on purpose. |
| Symbol-only `resolve_symbol`, Route-only `resolve_route`, Client-only `resolve_client` | Three nearly-identical tool surfaces. One typed tool with `hint_kind` is the smaller mental model. The candidate-generator code is per-kind internally; the agent contract is unified. |
| Free-text `reason: str` per candidate | Closed `ResolveReason` literal is the analog of the closed `EdgeType`. Open vocabularies drift; closed ones stay reviewable. |
| `resolve` accepting `search`-shaped output (e.g., a SearchHit) | Pipelining tools is the agent's responsibility. `resolve` accepts strings; if the agent has a `SearchHit`, the right next call is `describe(id=hit.symbol_id)`. |

---

## §6 Migration — 2 PRs

### PR-RESOLVE-1 — Implement `resolve`

Purpose: ship the `resolve` tool end-to-end. Adds `ResolveOutput`, `ResolveCandidate`, `ResolveStatus` to the V2 models in `mcp_v2.py`; adds `ResolveReason` to `java_ontology.py` (single source of truth for closed vocabularies, like `EdgeType`); adds the handler in `mcp_v2.py` with per-kind candidate generators; registers the tool in `server.py`; adds named test scenarios covering each UC row above. Frame-aligned loud-fail on empty / whitespace identifier.

Named test scenarios (the contract; total count is a side-effect of implementation):

- Every `ResolveReason` appears in at least one test (any `status`).
- `status="one"` has a dedicated scenario (`exact_id` is the canonical example; other reasons may reach `one` opportunistically).
- `status="many"` has dedicated scenarios for FQN collision across microservices (UC3) and short-name ambiguity (UC5), with stable ranking under the tiebreak chain (§3.5).
- `status="none"` returns a non-empty `message`.
- Empty and whitespace-only inputs return `success=False` with `status="none"` and `message` starting `"Invalid identifier:"`.
- Cross-kind `resolve` without `hint_kind` returns candidates with mixed `kind`.
- Deduplication: a Symbol matched by two generator paths (e.g., `short_name` and `fqn_suffix`) appears once in `candidates`.

Adding `resolve` to the tool list does **not** bump `ontology_version` — ontology versioning tracks the graph schema (node kinds, edge types, stored attributes), not the tool surface.

### PR-RESOLVE-2 — Tool-description sweep

Purpose: remove the pre-`resolve` fallback wording from all agent-facing surfaces; add prose pointing at `resolve` as the primitive for identifier-shaped lookups; ensure no surface still claims the fallback is current.

**Sweep checklist** (the four tool descriptions are not the only place; the propose previously said "four tool descriptions" — the actual surface is wider):

| Surface | What to change |
|---|---|
| `server.py` — `search`'s tool description | Remove the `search + describe`-per-candidate fallback wording; point at `resolve` for identifier-shaped lookups. |
| `server.py` — `describe`'s tool description | Remove fallback wording; note that `describe(fqn=…)` keeps first-match behavior for FQN collisions and point at `resolve(…, hint_kind="symbol")` as the canonical disambiguation path. |
| `server.py` — `find`'s tool description | Remove any fallback wording if present; verify no smart-by-nature claims slipped in. |
| `server.py` — `neighbors`'s tool description | Same scan as `find`. |
| `server.py` — `_INSTRUCTIONS` agent prose | Add `resolve` to the tool inventory; remove fallback chain wording. |
| `mcp_v2.py` — `describe_v2` collision `hint_message` | Currently points at `find` + `search`; update to point at `resolve(…, hint_kind="symbol")`. |
| `docs/AGENT-GUIDE.md` — "Identifier resolution (pre-`resolve`)" section | Rename / rewrite to describe the post-`resolve` flow. |
| `README.md` — MCP tool table | Add a fifth row for `resolve`. |

Verify by re-reading each surface and grepping for the strings the agent would cargo-cult (e.g., `search.*describe`, `describe.*per.candidate`). The grep is a sanity check, not the contract — the contract is "no surface still recommends the pre-`resolve` fallback".

Does not bump `ontology_version`.

---

## §7 Decisions taken (no longer open)

1. **`resolve` exists as a fifth MCP V2 tool**, not as a smart-mode on `search` or `find`.
2. **Scope: all three kinds (Symbol, Route, Client).** No per-kind splits.
3. **Output envelope: discriminated `{status, node?, candidates?, message?}`.** Agent code branches once on `status`.
4. **`hint_kind` is the only hint.** No `microservice` co-hint, no `hints: dict`.
5. **Candidate `reason` is a closed `ResolveReason` Literal.** Adding a reason is a frame decision.
6. **Status invariants are checked at handler exit.** `one` ⟺ node populated, no candidates; `many` ⟺ ≥2 candidates, no node; `none` ⟺ no node, no candidates, message present.
7. **No silent best-guess.** Ambiguity is `status="many"` with the agent picking, never a single `node` chosen by score.
8. **Closed candidate reason set, ranking via reason priority + specificity + stable id.** `score` exposed for telemetry only.
9. **Pre-`resolve` fallback wording is removed from all four existing tool descriptions when `resolve` ships.** Not amended, removed.
10. **No micro-tools per kind.** One `resolve`, one `hint_kind`.
11. **Loud-fail on malformed input (empty, whitespace-only) is `success=False`**, distinct from `status="none"` (well-formed input, no match).
12. **Candidate cap `K`** is a small constant in the handler (initial value: 10), not a tool parameter. Exceeding it is a signal to use `search`, not to paginate.
13. **No deprecation aliases**, per the no-users constraint. PR-RESOLVE-1 ships the tool; PR-RESOLVE-2 sweeps the descriptions; both are breaking changes.
14. **`resolve` does not parse natural language.** Query-shaped sentences return `status="none"` and the agent falls back to `search`.
15. **Two PRs, in order.** PR-RESOLVE-1 (tool) → PR-RESOLVE-2 (description sweep). The description sweep waits because removing the fallback wording before the replacement exists would leave the agent without a documented path for identifier-shaped lookups.
16. **Malformed input `status` is `"none"`.** On `success=False`, `status="none"`, `node=None`, `candidates=[]`, `message` non-empty and starts `"Invalid identifier:"`. Avoids inventing a `"malformed"` status value the agent would have to branch on separately — the agent's first branch is `success`, the second is `status` (only when `success == True`).
17. **`ResolveReason` lives in `java_ontology.py`.** Closed vocabularies belong with the other closed sets (`EdgeType`, etc.), not in `mcp_v2.py`. The Pydantic models that reference it stay in `mcp_v2.py`.
18. **Adding `resolve` does not bump `ontology_version`.** Ontology versioning tracks the graph schema, not the tool surface.
19. **Candidate generators deduplicate by `node.id`** before applying the status decision rule. A Symbol matched by both `short_name` and `fqn_suffix` is one candidate, not two.
20. **`describe(fqn=…)` is not extended with a `microservice` parameter.** The filter-frame §3.4.2 sketch is superseded: cross-microservice FQN collisions go through `resolve(…, hint_kind="symbol") → status="many"`. `describe(fqn=…)` may keep first-match behavior, or be tightened in a follow-up; either way, no co-parameter is added.

---

## §8 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agent reaches for `resolve` when the right tool is `search`. | Tool description leads with "identifier-shaped input"; the `status="none"` path explicitly names `search` as the fallback. UC14 / UC15 are named in the description. |
| Candidate ranking becomes a soft contract the agent depends on. | `score` is documented as telemetry-only; status + reason priority is the agent-facing contract. Ranking changes that don't move `status` boundaries are non-breaking. |
| Cross-microservice FQN collisions become a usability cliff because there's no co-hint. | UC3 is the named pattern: `status="many"`, `NodeRef.microservice` per candidate, agent picks. If 3 issues file legitimate workflows that hit this cliff within 6 months, the filter-frame revisit-trigger (decision §3.4.6) covers re-opening this question. |
| `ResolveReason` literal drifts toward openness ("just add a reason this once"). | Adding a reason is documented as a frame decision in §3.4. Code review treats new reasons like new `EdgeType` values. |
| Removing the §3.4.7 fallback wording leaves docs out of sync with old discussion. | PR-RESOLVE-2 is dedicated to the sweep; the filter-frame propose is already in `completed/` and is a historical record, not a description surface. |
| Two-PR sequence merges out of order (PR-RESOLVE-2 before PR-RESOLVE-1). | Sequence is locked in decision §7.15. PR-RESOLVE-2 is blocked on PR-RESOLVE-1 in the PR description. |

---

## Appendix A — Concrete artefact: `ResolveOutput` schema

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

ResolveStatus = Literal["one", "many", "none"]

ResolveReason = Literal[
    "exact_id",
    "exact_fqn",
    "fqn_suffix",
    "short_name",
    "route_template",
    "route_method_path",
    "client_target",
    "client_target_path",
]


class ResolveCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: NodeRef
    score: float
    reason: ResolveReason


class ResolveOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    status: ResolveStatus
    node: NodeRef | None = None
    candidates: list[ResolveCandidate] = Field(default_factory=list)
    message: str | None = None
```

`NodeRef` is unchanged from its current definition in `mcp_v2.py` (see [`mcp_v2.py`](../mcp_v2.py), `class NodeRef`). `extra="forbid"` is consistent with the strict-frame discipline applied to `NodeFilter` in #122.

---

## References

- [`propose/completed/MCP-FILTER-FRAME-PROPOSE.md`](completed/MCP-FILTER-FRAME-PROPOSE.md) — the locked frame this propose extends; §3.5 named `resolve` and §3.4.7 named the pre-`resolve` fallback this propose removes.
- [`plans/completed/PLAN-MCP-FILTER-FRAME.md`](../plans/completed/PLAN-MCP-FILTER-FRAME.md) — the per-PR plan that shipped the filter frame in PRs #131 / #132 / #133.
- `mcp_v2.py` — `NodeRef`, `SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput`, `_coerce_filter`, `_resolve_node_kind` (which is internal-only and unrelated to the new tool — the namespace collision is acknowledged; if it becomes confusing, the internal helper is the one to rename).
