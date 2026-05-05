# Cursor task prompts — Tier 1B completion (PR-D1 → PR-D3)

Status: **completed — all PRs merged**. Kept as a reference template for
future per-PR Cursor delegation work.

One prompt per PR. Each is **self-contained**: copy the prompt verbatim
into Cursor, attach the files listed in its `@-files` block, and let
Sonnet execute. Each prompt fits comfortably in a single Sonnet session.

**Workflow per PR:**

1. Create a feature branch off `master` (or off the previous PR's branch if it hasn't merged yet).
2. Open Cursor in agent mode with **Sonnet 4.6** (or whichever Sonnet you have credits for).
3. Attach the files from the prompt's `@-files` block.
4. Paste the prompt.
5. Let it run; review the diff; iterate via Cursor chat if needed.
6. Run `pytest`. If green, commit and open PR.

**Universal rules for every prompt:**

- Sonnet must keep `pytest` green at every commit.
- The shared three-strategy resolver must be referenced by *one* name
  (`_string_value_atoms` after PR-D1). Do **not** re-implement the
  literal/SpEL/constant-ref ladder anywhere else.
- Brownfield extends `BrownfieldOverrides` — does not parallel it.
  Cite `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` line numbers
  in the PR description (mandatory).
- No `git push` from the agent; you handle pushing.
- If Sonnet hits ambiguity, it should stop and ask, not guess.

---

## PR-D1 — B2b core: HTTP_CALLS + ASYNC_CALLS extractor

**Branch:** `feat/b2b-http-async-edges` off `master`.
**Base:** `master` at the latest commit (post-Tier-1).
**Plan section:** `plans/PLAN-TIER1B-COMPLETION.md` § PR-D1 (read this first).
**Estimated diff size:** ~4 files, ~600 LOC.

**Attach (`@-files`):**

- `@plans/PLAN-TIER1B-COMPLETION.md` (the whole plan, but only the **PR-D1** section is in scope)
- `@propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md` (§3 join-key contract, §4 schema, §5 extraction)
- `@plans/PLAN-TIER1-COMPLETION.md` (style reference — your most recent predecessor)
- `@ast_java.py`
- `@build_ast_graph.py`
- `@java_ontology.py`
- `@graph_enrich.py` (read-only — for `BrownfieldOverrides` shape; do NOT modify in this PR)
- `@tests/test_route_extraction.py` (pattern reference for new test file)
- `@tests/bank-chat-system/chat-assign/src/main/java/com/bank/chat/assign/integration/ChatCoreJoinClient.java`
- `@tests/bank-chat-system/chat-core/chat-engine/src/main/java/com/bank/chat/engine/kafka/FollowUpKafkaPublisher.java`

**Prompt:**

````
You are implementing PR-D1 from `plans/PLAN-TIER1B-COMPLETION.md`.

Read the **PR-D1 — B2b core** section of the plan in full before writing
any code. The plan is the source of truth — if this prompt and the plan
disagree, the plan wins. Every test case number I mention (1, 2, …, 19)
refers to the numbered test list in PR-D1 §4.

## Scope

Implement PR-D1 exactly as specified in `plans/PLAN-TIER1B-COMPLETION.md`
§ PR-D1. **Nothing else.**

Concretely:

- **Rename** `_route_value_atoms` → `_string_value_atoms` in
  `ast_java.py`. Update all four existing call sites
  (`grep -n "_route_value_atoms" .` must return zero after the rename).
  Also rewrite the docstring to drop the route-specific framing.
- Add `OutgoingCallDecl` dataclass + `MethodDecl.outgoing_calls`
  field in `ast_java.py`. Export `OutgoingCallDecl` in `__all__`.
- Bump `ONTOLOGY_VERSION` 6 → 7 in `ast_java.py`. Update the comment
  to mention "Phase 5: HTTP_CALLS + ASYNC_CALLS (B2b)".
- Implement `_collect_outgoing_calls` in `ast_java.py` for these
  patterns (plan §3.1 has the full table):
  - **Feign-method** — any method on a `@FeignClient` interface →
    `client_kind='feign_method'`, no URI resolution.
  - **`RestTemplate.{exchange,getForObject,getForEntity,postForEntity,postForObject,put,delete}`**
    — first arg via `_string_value_atoms` (handles literal / SpEL /
    constant-ref). Method derived from method name (`getForObject` → `GET`)
    or `HttpMethod.X` second arg of `exchange`.
  - **String concat URI** — when the first arg is a binary `+` chain
    whose right-most operand is a literal `/path`, capture that
    literal as `path_template_call` (`_normalize_path` it),
    `confidence_base=0.7`, `resolved=False`. Stash full expression
    text in `raw_uri`.
  - **`KafkaTemplate.send(topic, …)`** — first arg via
    `_string_value_atoms`. `client_kind='kafka_send'`.
  - **`WebClient` chains** — emit `unresolved` (deferred to v2).
    `client_kind='web_client'`, `strategy='unresolved'`,
    `confidence_base=0.3`.
  - **`StreamBridge.send(...)`** — emit `unresolved` (deferred to v2).
    `client_kind='stream_bridge_send'`, `strategy='unresolved'`,
    `confidence_base=0.3`.
- Add `VALID_CLIENT_KINDS`, `VALID_HTTP_CALL_STRATEGIES`,
  `VALID_ASYNC_CALL_STRATEGIES`, `VALID_HTTP_CALL_MATCHES` frozensets
  to `java_ontology.py`. Add to `__all__`.
- Add `_SCHEMA_HTTP_CALLS` and `_SCHEMA_ASYNC_CALLS` to
  `build_ast_graph.py`. Wire into create + drop lists. Edge direction
  is `(Symbol)-[:HTTP_CALLS]->(Route)` and
  `(Symbol)-[:ASYNC_CALLS]->(Route)`. Do **not** reverse.
- Add `HttpCallRow`, `AsyncCallRow`, `CallEdgeStats` dataclasses and
  the corresponding `GraphTables` fields.
- Implement `pass5_imperative_edges` exactly per plan §3.4. Phantom
  `Route` rows for unresolved / pre-match cases follow the same
  schema as B2a's resolved routes; dedupe by `id`. **Every edge
  written by PR-D1 has `match='unresolved'`** — PR-D3 will rewrite
  this column. Confidence = `confidence_base × 0.3 × micro_factor`
  (PR-D1's fixed `match_factor=0.3`).
- Wire `pass5_imperative_edges` into `main` immediately after
  `pass4_routes`.
- Implement HTTP_CALLS + ASYNC_CALLS writers; phantom `Route` insert
  must dedupe (use the existing B2a writer's dedup pattern).
- Extend `graph_meta` with `http_calls_total`, `async_calls_total`,
  `http_calls_by_strategy`, `async_calls_by_strategy`,
  `http_calls_resolved_pct`, `async_calls_resolved_pct`.
  `*_by_strategy` are STRING JSON blobs — NOT `MAP(STRING, INT64)`.
  Encode with `json.dumps`; decode in `kuzu_queries.meta()` with
  `json.loads` (same pattern as `routes_by_framework` in PR-A1).
- Build the new fixture `tests/fixtures/http_caller_smoke/` (see
  plan §4.4) and add `tests/test_outgoing_call_extraction.py`,
  `tests/test_call_edges_e2e.py`, `tests/test_string_value_atoms.py`
  with cases 1–19.

## Out of scope (do NOT touch)

- `graph_enrich.py` — that's PR-D2 (brownfield).
- Any new MCP tools — that's PR-D3.
- `pass6_match_edges` or any cross-service matcher logic — PR-D3.
- Match-outcome computation beyond writing `'unresolved'` — PR-D3.
- `WebClient` fluent-chain backward walking — deferred to v2.
- `UriComponentsBuilder` resolution — deferred to v2.
- `StreamBridge` binding-to-topic resolution — deferred to v2.
- Reversing the `HTTP_CALLS` / `ASYNC_CALLS` edge direction.
- Any change to `Route` columns — the schema is frozen, see proposal §3.1.
- Any change to `pass4_routes` other than reading its output via
  `tables.routes_rows`.
- Re-implementing the three-strategy ladder anywhere — single
  source of truth is `_string_value_atoms` post-rename.

If you find yourself wanting to touch any of the above, **stop and ask** —
don't ship it.

## Deliverables

1. Renamed helper `_string_value_atoms` in `ast_java.py` (was
   `_route_value_atoms`); 4 in-file call sites updated.
2. New dataclass `OutgoingCallDecl` in `ast_java.py` with the
   field set listed in plan §1.2.
3. New field `MethodDecl.outgoing_calls: list[OutgoingCallDecl]`.
4. New helper `_collect_outgoing_calls(method_node, type_node, src, *, ctx, project_root)`
   in `ast_java.py`, called from `_parse_method` after `_collect_routes`.
5. `ONTOLOGY_VERSION` bumped 6 → 7.
6. Four new frozensets in `java_ontology.py` exported in `__all__`.
7. `_SCHEMA_HTTP_CALLS` and `_SCHEMA_ASYNC_CALLS` in
   `build_ast_graph.py`; both wired to create + drop lists.
8. `HttpCallRow`, `AsyncCallRow`, `CallEdgeStats` dataclasses and
   `GraphTables` fields.
9. `pass5_imperative_edges` function; wired into `main` after
   `pass4_routes`.
10. HTTP_CALLS + ASYNC_CALLS writers + phantom-`Route` dedup insert.
11. `graph_meta` extended with the 6 new columns listed above (STRING
    columns, JSON-blob encoding for the two `*_by_strategy` ones).
12. New test file `tests/test_outgoing_call_extraction.py` with
    cases 1–13.
13. New test file `tests/test_call_edges_e2e.py` with cases 14–19.
14. New test file `tests/test_string_value_atoms.py` with the
    rename regression-guard test.
15. New fixture `tests/fixtures/http_caller_smoke/` (~10 Java files
    per plan §4.4).
16. README.md route section updated to add the `HTTP_CALLS` /
    `ASYNC_CALLS` row to the schema table.

## Tests

All tests must pass: `python -m pytest tests -q` should report
**229 passed, 4 skipped** (210 master baseline + 19 new tests).

Sentinel grep checks (must all return zero):

- `grep -n "_route_value_atoms" .` (rename complete)
- `grep -rn "MAP(STRING" build_ast_graph.py | grep -v "STRING JSON"`
  (no MAP columns introduced for new graph_meta fields)

## Manual evidence (paste in PR description)

Run:

```bash
cd /home/user/workspace/user-rag && rm -rf /tmp/check_d1 && \
  python build_ast_graph.py --source-root tests/bank-chat-system \
  --kuzu-path /tmp/check_d1 --verbose 2>&1 | grep -E "^\[pass[45]\]"
```

Expected: a `[pass5]` line reporting at least 2 HTTP_CALLS edges and at
least 5 ASYNC_CALLS edges (the bank-chat-system fixture has two
`postForEntity` sites and five `kafkaTemplate.send` sites; see plan
§Test fixture inventory).

Plus, query via Python:

```python
import sys; sys.path.insert(0, '.')
from kuzu_queries import KuzuGraph
g = KuzuGraph('/tmp/check_d1')
m = g.meta()
print(f"http_calls_total={m['http_calls_total']}, async_calls_total={m['async_calls_total']}")
print(f"http_calls_by_strategy={m['http_calls_by_strategy']}")
print(f"ontology_version={m['ontology_version']}")
```

Expected: `http_calls_total >= 2`, `async_calls_total >= 5`,
`ontology_version=7`, `http_calls_by_strategy` is a Python `dict` (not
a string).

## Definition of Done

- [ ] All deliverables 1–16 above shipped.
- [ ] All tests pass locally (229 passed, 4 skipped).
- [ ] Sentinel greps return zero.
- [ ] No file outside `ast_java.py`, `build_ast_graph.py`,
      `java_ontology.py`, `kuzu_queries.py`, `README.md`, and the new
      `tests/` paths is modified
      (`git diff --stat master..HEAD` and check).
- [ ] PR description includes the scope statement, the manual evidence
      output, and the test count.
- [ ] PR opened against `master` with title
      `feat: B2b HTTP_CALLS + ASYNC_CALLS extractor (PR-D1)`.
- [ ] Branch is named `feat/b2b-http-async-edges`.
````

---

## PR-D2 — B2b brownfield: caller-side overrides

**Branch:** `feat/b2b-brownfield-clients` off PR-D1's branch (or
`master` if PR-D1 has merged).
**Base:** PR-D1 merged.
**Plan section:** `plans/PLAN-TIER1B-COMPLETION.md` § PR-D2 (read this first).
**Estimated diff size:** ~4 files, ~400 LOC + 12 fixtures.

**Attach (`@-files`):**

- `@plans/PLAN-TIER1B-COMPLETION.md` (the whole plan; **PR-D2** section is in scope)
- `@plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` (mandatory reading)
- `@graph_enrich.py`
- `@ast_java.py`
- `@build_ast_graph.py`
- `@tests/test_brownfield_routes.py` (pattern reference — replicate this shape)
- `@tests/fixtures/brownfield_route_stubs/` (pattern reference for the new fixture)

**Prompt:**

````
You are implementing PR-D2 from `plans/PLAN-TIER1B-COMPLETION.md`.

Read the **PR-D2 — B2b brownfield** section of the plan in full before
writing any code, plus the linked
`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` (it's
mandatory — risk #5 in the plan and the proposal both insist on it).
Every test case number I mention (20, 21, …, 31) refers to the
numbered test list in PR-D2 §4.

## Scope

Implement PR-D2 exactly as specified in `plans/PLAN-TIER1B-COMPLETION.md`
§ PR-D2. **Nothing else.** This is the caller-side mirror of PR-A3's
brownfield surface — extend `BrownfieldOverrides`, do **not** create
parallel structures.

Concretely:

- Add `HttpClientHint` and `AsyncProducerHint` dataclasses (frozen)
  to `graph_enrich.py`.
- Extend `BrownfieldOverrides` with four new dicts:
  - `annotation_to_http_client_hint: dict[str, HttpClientHint]`
  - `fqn_to_http_client_hint: dict[str, HttpClientHint]`
  - `annotation_to_async_producer_hint: dict[str, AsyncProducerHint]`
  - `fqn_to_async_producer_hint: dict[str, AsyncProducerHint]`
- Extend `load_brownfield_overrides` to parse these four new YAML
  keys (`http_client_overrides.{annotations, fqn}` and
  `async_producer_overrides.{annotations, fqn}`). Validate
  `client_kind` against `VALID_CLIENT_KINDS`. Emit the same
  `[lancedb-mcp]` warning format as `route_overrides.annotations:
  unknown framework` for unknown values.
- Implement `resolve_http_client_for_method(*, method_decl,
  enclosing_type, overrides, meta_chain, builtin_calls)` and
  `resolve_async_producer_for_method(...)` in `graph_enrich.py`.
  Both mirror `resolve_routes_for_method` for layer composition (same
  5 layers, same last-writer-wins **between brownfield layers**):
    1. Built-in detection (the `OutgoingCallDecl`s already in
       `builtin_calls`).
    2. Layer B annotations.
    3. Layer A meta-annotation chain (re-use
       `collect_annotation_meta_chain`).
    4. Layer C `@CodebaseClient` / `@CodebaseProducer` source stubs.
    5. Layer B FQN (outermost).

  **CRITICAL DIVERGENCE FROM B2a's route resolver** (plan §PR-D2 §3.5):
  if Layers 2–5 produce ≥1 brownfield `OutgoingCallDecl` for the same
  method, **drop the built-in `OutgoingCallDecl`s from `builtin_calls`
  for that method**. Rationale: a `restTemplate.X` or `kafkaTemplate.send`
  site is a single outgoing network call — emitting both auto-extracted
  and brownfield-asserted edges from it would double-count one packet.
  B2a's route resolver does NOT do this (a method can legitimately
  expose multiple paths via path arrays). Algorithm:

  ```python
  brownfield_calls = _collect_brownfield_outgoing_calls(...)  # Layers 2–5
  if brownfield_calls:
      return brownfield_calls   # caller-side: replace, not append
  return builtin_calls           # no brownfield → keep auto-detected
  ```

  The per-method boundary matters: in a class with method `A`
  (built-in only) and method `B` (built-in + `@CodebaseClient`),
  only `B`'s built-in edges are dropped. `A`'s are kept untouched.
  Tests 27, 31a, 31b lock this behaviour. **Call this divergence
  out explicitly in the PR description so reviewers don't flag it
  as a bug.**
- Add `CODEBASE_CLIENT_ANNOTATIONS` and
  `CODEBASE_PRODUCER_ANNOTATIONS` frozensets in `ast_java.py` next
  to the existing `CODEBASE_ROUTE_ANNOTATIONS`.
- Extend `_collect_outgoing_calls` (PR-D1's helper) in `ast_java.py`
  to recognise `@CodebaseClient` / `@CodebaseProducer` annotations
  on methods, including `@Repeatable` containers
  `@CodebaseClients` / `@CodebaseProducers`. Each emits an
  `OutgoingCallDecl` with `resolution_strategy='codebase_client'`
  / `'codebase_producer'`, `confidence_base=1.0`, `resolved=True`.
  Annotation arguments parse via `_string_value_atoms` (post-D1).
- Wire `resolve_http_client_for_method` and
  `resolve_async_producer_for_method` into `pass5_imperative_edges`
  (after collecting `member.decl.outgoing_calls`, before computing
  match outcomes). Load `overrides` and `meta_chain` once at the
  top of `pass5` — same pattern as `pass4_routes` does for routes.
- Add `http_clients_from_brownfield_pct` and
  `async_producers_from_brownfield_pct` to `graph_meta`. Define
  as: % of final outgoing calls whose
  `resolution_strategy ∈ {layer_b_ann, layer_a_meta, layer_c_source,
  layer_b_fqn, codebase_client, codebase_producer}`.
- Build the new fixture `tests/fixtures/brownfield_client_stubs/`
  (see plan §4) — 14 cases (12 mirroring `brownfield_route_stubs`
  + 2 for the caller-side replacement-rule divergence).
- Create `tests/test_brownfield_clients.py` with cases 20–31,
  31a, 31b.

## Out of scope (do NOT touch)

- `pass6_match_edges` or any cross-service matcher logic — PR-D3.
- Any new MCP tools — PR-D3.
- The `Route` schema or any extension of `routes_rows` columns — frozen.
- Edge schemas (`HTTP_CALLS`, `ASYNC_CALLS`) — frozen post-D1.
- Bumping `ONTOLOGY_VERSION` — stays at 7 (D1 already bumped).
- Re-implementing the three-strategy ladder — re-use
  `_string_value_atoms` from D1.
- Touching `pass4_routes` or `resolve_routes_for_method` —
  that's B2a, frozen.
- Creating a parallel "BrownfieldClientOverrides" or similar
  structure — extend the single `BrownfieldOverrides` class.

If you find yourself wanting to touch any of the above, **stop and ask** —
don't ship it.

## Deliverables

1. New frozen dataclasses `HttpClientHint` and `AsyncProducerHint`
   in `graph_enrich.py`.
2. Four new dict fields on `BrownfieldOverrides`.
3. `load_brownfield_overrides` parses
   `http_client_overrides.{annotations, fqn}` and
   `async_producer_overrides.{annotations, fqn}`.
4. New functions `resolve_http_client_for_method` and
   `resolve_async_producer_for_method` in `graph_enrich.py`,
   structurally identical to `resolve_routes_for_method`.
5. `CODEBASE_CLIENT_ANNOTATIONS` and `CODEBASE_PRODUCER_ANNOTATIONS`
   frozensets in `ast_java.py`.
6. `_collect_outgoing_calls` extended to parse `@CodebaseClient` /
   `@CodebaseProducer` (including `@Repeatable` containers).
7. `pass5_imperative_edges` calls `resolve_http_client_for_method`
   and `resolve_async_producer_for_method` per outgoing-call list.
8. `graph_meta` adds `http_clients_from_brownfield_pct` and
   `async_producers_from_brownfield_pct`.
9. New fixture `tests/fixtures/brownfield_client_stubs/` with
   12 cases.
10. New test file `tests/test_brownfield_clients.py` with cases
    20–31.
11. README.md updated with brownfield client / producer override docs.

## Tests

All tests must pass: `python -m pytest tests -q` should report
**243 passed, 4 skipped** (229 baseline post-D1 + 14 new tests).

Sentinel grep checks (must all return zero):

- `grep -rn "class BrownfieldClientOverrides\|class BrownfieldProducerOverrides" .`
  (no parallel structures introduced)
- `grep -rn "annotation_to_http_client_hint\|fqn_to_http_client_hint" graph_enrich.py | wc -l`
  must be ≥ 4 (the four new fields exist).

## Manual evidence (paste in PR description)

Cite the line numbers from
`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` that
each layer in `resolve_http_client_for_method` mirrors. **This is
mandatory** — risk #5.

Then run, with a sample `.lancedb-mcp.yml` adding one
`http_client_override`:

```yaml
# .lancedb-mcp.yml in the bank-chat-system root
http_client_overrides:
  annotations:
    "org.springframework.stereotype.Component":
      client_kind: rest_template
      target_service: chat-core
```

```bash
cd /home/user/workspace/user-rag && rm -rf /tmp/check_d2 && \
  python build_ast_graph.py --source-root tests/bank-chat-system \
  --kuzu-path /tmp/check_d2 --verbose 2>&1 | tail -20
```

**The PR description must also explicitly call out the caller-side
replacement-rule divergence** (§PR-D2 §3.5) — reviewers will
otherwise flag the missing `extend(builtin_calls)` as a bug.

Then check the percentage:

```python
import sys; sys.path.insert(0, '.')
from kuzu_queries import KuzuGraph
g = KuzuGraph('/tmp/check_d2')
m = g.meta()
print(f"http_clients_from_brownfield_pct={m['http_clients_from_brownfield_pct']}")
print(f"async_producers_from_brownfield_pct={m['async_producers_from_brownfield_pct']}")
```

Expected: `http_clients_from_brownfield_pct > 0`. (Then **revert** the
`.lancedb-mcp.yml` change before opening the PR — fixture stays
clean.)

## Definition of Done

- [ ] All deliverables 1–11 above shipped.
- [ ] All tests pass locally (243 passed, 4 skipped).
- [ ] Sentinel greps return expected counts.
- [ ] PR description cites
      `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` line numbers
      (mandatory).
- [ ] No file outside `graph_enrich.py`, `ast_java.py`,
      `build_ast_graph.py`, `kuzu_queries.py`, `README.md`, and the new
      `tests/` paths is modified.
- [ ] PR opened against `master` with title
      `feat: B2b brownfield client/producer overrides (PR-D2)`.
- [ ] Branch is named `feat/b2b-brownfield-clients`.
````

---

## PR-D3 — B6 cross-service matcher + MCP surface

**Branch:** `feat/b6-cross-service-matcher` off PR-D2's branch (or
`master` if both predecessors have merged).
**Base:** PR-D1 merged (PR-D2 strongly preferred but technically optional).
**Plan section:** `plans/PLAN-TIER1B-COMPLETION.md` § PR-D3 (read this first).
**Estimated diff size:** ~5 files, ~500 LOC + 1 fixture.

**Attach (`@-files`):**

- `@plans/PLAN-TIER1B-COMPLETION.md` (the whole plan; **PR-D3** section is in scope)
- `@propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md` (§3.3 match-outcome enum, §7 MCP surface)
- `@build_ast_graph.py`
- `@kuzu_queries.py`
- `@server.py`
- `@pr_analysis.py`
- `@reports/call-graph-review.md` (D5 structural-first ordering invariant)
- `@tests/test_mcp_tools.py` (pattern reference)
- `@tests/bank-chat-system/chat-core/chat-engine/src/main/java/com/bank/chat/engine/assign/ConfigurableChatAssignment.java`

**Prompt:**

````
You are implementing PR-D3 from `plans/PLAN-TIER1B-COMPLETION.md`.

Read the **PR-D3 — B6 cross-service matcher + MCP surface** section of
the plan in full before writing any code. The plan is the source of
truth — if this prompt and the plan disagree, the plan wins. Every
test case number I mention (32, 33, …, 48) refers to the numbered test
list in PR-D3 §5.

## Scope

Implement PR-D3 exactly as specified in `plans/PLAN-TIER1B-COMPLETION.md`
§ PR-D3. **Nothing else.** PR-D3 does NOT change the `pass5` extractor —
it adds a separate `pass6_match_edges` that reads PR-D1's phantom-route
edges and rewrites their `match` + `route_id` + `confidence` columns
in-place.

Concretely:

- Implement `_match_call_edge(call, routes, caller_microservice) ->
  (outcome, candidate_routes)` in `build_ast_graph.py` per plan §1
  algorithm. The 5-outcome enum:
    - 0 candidates → `phantom`
    - 1 candidate, different microservice → `cross_service`
    - 1 candidate, same microservice → `intra_service`
    - >1 candidates → `ambiguous`
    - `call.resolved == False` AND `path_template_call == ''` AND
      `topic_call == ''` → `unresolved` (short-circuits before any
      filtering).
  HTTP filter: `kind == 'http_endpoint'`, `method` matches (or
  either is `''`), `re.fullmatch(route.path_regex, call.path_template_call)`.
  Async filter: `topic == call.topic_call` AND
  `broker == call.broker_call` (strict equality; `''` matches `''`
  literally, NOT a wildcard).
  Feign filter: `feign_name == call.feign_target_name`.
- Implement `pass6_match_edges(tables, *, verbose)` per plan §1.
  For each `HttpCallRow` / `AsyncCallRow` with `match='unresolved'`
  (every row from PR-D1):
    1. Look up the originating `OutgoingCallDecl`.
    2. Call `_match_call_edge`.
    3. Update the edge's `match` column.
    4. If outcome ∈ {cross_service, intra_service} and exactly one
       candidate matched, **rewrite the edge's `route_id`** to the
       real `Route` id (not the phantom one).
    5. Recompute `confidence = call.confidence_base × match_factor ×
       micro_factor` where match_factor:
       cross_service=1.0, intra_service=0.6, ambiguous=0.5,
       phantom=0.4, unresolved=0.3.
    6. Update `tables.call_edge_stats` per-`match` counters.
  Then a cleanup step: remove any phantom `Route` row with zero
  incoming `HTTP_CALLS` / `ASYNC_CALLS` edges.
- Wire `pass6_match_edges` into `main` immediately after
  `pass5_imperative_edges`.
- Extend `graph_meta` with:
    - `http_calls_match_breakdown STRING` (JSON: {outcome: count})
    - `async_calls_match_breakdown STRING` (JSON: {outcome: count})
    - `cross_service_calls_total INT64`
  STRING JSON pattern (NOT MAP).
- Implement `find_route_callers(route_id=None, *, microservice='',
  path_template='', method='') -> list[CallerInfo]` in
  `kuzu_queries.py`. Exact-match only on `(microservice,
  path_template, method)` — NO regex. Returns
  `caller_symbol_id`, `caller_microservice`, `confidence`, `match`.
- Implement `trace_request_flow(entry_route_id, max_hops=5) ->
  FlowChain` in `kuzu_queries.py`. Cypher walks both:
    - Inbound: `(entry:Route)<-[:HTTP_CALLS|:ASYNC_CALLS]-(caller:Symbol)<-[:CALLS*0..N]-(origin:Symbol)`
    - Outbound: `(handler:Symbol)-[:EXPOSES]->(entry)-[CALLS*]->...`
  Preserve structural-first ordering (same-microservice CALLS before
  cross-service HTTP_CALLS at each step). See
  `reports/call-graph-review.md` for the D5 invariant.
- Register both new MCP tools in `server.py`:
  - `find_route_callers`
  - `trace_request_flow`
- Extend `impact_analysis` in `server.py`: add
  `cross_service_callers` field to the result. Walk reverse closure
  including `HTTP_CALLS` / `ASYNC_CALLS` outbound from any
  reached `Route`.
- Extend `trace_flow` in `server.py`: budgeted walk now follows
  `HTTP_CALLS` / `ASYNC_CALLS`. Same-microservice `CALLS` ordered
  first per step.
- Extend `analyze_pr` in `server.py` + `pr_analysis.py`: each
  changed method that's the source of an `EXPOSES` edge gets a
  `cross_service_callers_count` field in its risk record. Risk
  score gains `+1.0` per cross-service caller, capped at `+5.0`.
  Document the weight in the docstring.
- Build the new fixture `tests/fixtures/cross_service_smoke/` (see
  plan §5.3) — two services + a third "ambiguous" controller.
- Create `tests/test_call_edge_matching.py` with cases 32–40.
- Extend `tests/test_mcp_tools.py` with cases 41–48.
- Flip `propose/PRODUCT-VISION.md` `HTTP_CALLS` / `ASYNC_CALLS` rows
  from *planned* to *shipped*.

## Out of scope (do NOT touch)

- `pass5_imperative_edges` or `_collect_outgoing_calls` —
  frozen post-D1.
- `BrownfieldOverrides` or any caller-side override resolver —
  frozen post-D2.
- `pass4_routes` or any B2a code — frozen post-Tier-1.
- Bumping `ONTOLOGY_VERSION` — stays at 7.
- Adding regex matching to `find_route_callers` — exact-match in v1.
- Re-implementing the three-strategy ladder — re-use
  `_string_value_atoms`.
- Adding new edge tables — use existing `HTTP_CALLS` /
  `ASYNC_CALLS`.
- Performance refactors of `pass3_calls` or other passes.
- `WebClient` / `UriComponentsBuilder` / `StreamBridge` resolution —
  deferred to v2.
- Deleting `Route` rows that still have incoming edges — only
  orphaned phantoms are cleaned up.

If you find yourself wanting to touch any of the above, **stop and ask** —
don't ship it.

## Deliverables

1. New helper `_match_call_edge` in `build_ast_graph.py` per plan §1
   algorithm.
2. New `pass6_match_edges` function; wired into `main` after
   `pass5_imperative_edges`.
3. Phantom-`Route` cleanup at end of `pass6` (orphan removal).
4. `graph_meta` extended with `http_calls_match_breakdown`,
   `async_calls_match_breakdown`, `cross_service_calls_total`.
5. `find_route_callers` in `kuzu_queries.py` — exact-match only.
6. `trace_request_flow` in `kuzu_queries.py` — preserves
   structural-first ordering.
7. Both new MCP tools registered in `server.py`.
8. `impact_analysis` extended with `cross_service_callers` field.
9. `trace_flow` extended to walk `HTTP_CALLS` / `ASYNC_CALLS`.
10. `analyze_pr` extended with `cross_service_callers_count`; risk
    weight added to `pr_analysis.py` and documented in docstring.
11. New fixture `tests/fixtures/cross_service_smoke/`.
12. New test file `tests/test_call_edge_matching.py` with cases 32–40.
13. Cases 41–48 added to `tests/test_mcp_tools.py`.
14. `propose/PRODUCT-VISION.md` flipped (planned → shipped).
15. `README.md` MCP tools section updated.

## Tests

All tests must pass: `python -m pytest tests -q` should report
**258 passed, 4 skipped** (241 baseline post-D2 + 17 new tests). If
PR-D2 has not merged, baseline is 229 → 246 expected.

Sentinel grep checks (must all return zero):

- `grep -rn "match='unresolved'" build_ast_graph.py | grep "pass5\|HttpCallRow"`
  — `pass5` still writes `'unresolved'` (D3 doesn't break D1's invariant
  there); but it must be zero in `pass6`'s writer block.
- `grep -rn "regex" kuzu_queries.py | grep find_route_callers`
  — exact-match only, no regex parameter.

## Manual evidence (paste in PR description)

Run:

```bash
cd /home/user/workspace/user-rag && rm -rf /tmp/check_d3 && \
  python build_ast_graph.py --source-root tests/bank-chat-system \
  --kuzu-path /tmp/check_d3 --verbose 2>&1 | grep -E "^\[pass[56]\]"
```

Expected: a `[pass6]` line reporting per-`match`-outcome counts. The
bank-chat-system fixture should produce at least 1 `cross_service` HTTP
edge (`chat-assign → chat-core /chat/joinOperator`), at least 1
`intra_service` (the same call inside `chat-core`), several
`unresolved` (Kafka `ChatTopics.X` constants stay unresolved unless a
matching `@KafkaListener` is in scope — which it is for some).

Then call the new MCP tool:

```python
import sys; sys.path.insert(0, '.')
from kuzu_queries import KuzuGraph
g = KuzuGraph('/tmp/check_d3')
m = g.meta()
print(f"cross_service_calls_total={m['cross_service_calls_total']}")
print(f"http_calls_match_breakdown={m['http_calls_match_breakdown']}")

# Find callers of a known route
callers = g.find_route_callers(microservice='chat-core',
                               path_template='/chat/joinOperator',
                               method='POST')
for c in callers:
    print(f"  {c['caller_symbol_id']} ({c['caller_microservice']}) match={c['match']} conf={c['confidence']:.2f}")
```

Expected: at least one caller from `chat-assign` with `match='cross_service'`.

## Definition of Done

- [ ] All deliverables 1–15 above shipped.
- [ ] All tests pass locally (258 passed, 4 skipped — or 246 if PR-D2 not merged).
- [ ] Sentinel greps return expected results.
- [ ] No file outside `build_ast_graph.py`, `kuzu_queries.py`,
      `server.py`, `pr_analysis.py`, `README.md`,
      `propose/PRODUCT-VISION.md`, and the new `tests/` paths is
      modified.
- [ ] PR description includes the scope statement, the manual evidence
      output (pass6 log + meta() snippet + find_route_callers output),
      and the test count.
- [ ] PR opened against `master` with title
      `feat: B6 cross-service matcher + MCP surface (PR-D3)`.
- [ ] Branch is named `feat/b6-cross-service-matcher`.
````
