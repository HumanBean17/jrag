# Plan: Tier 1B completion (B2b + B6)

Status: **ready to implement**. Self-contained: an agent picking this up
should be able to land it without re-deriving the design. Pairs with
[`propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md`](../propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md)
(scope, rationale, schema). Tier 1
([`PLAN-TIER1-COMPLETION.md`](PLAN-TIER1-COMPLETION.md)) **must be
merged** — this plan reads `Route` rows produced by `pass4_routes` and
the `BrownfieldOverrides` surface shipped by PR-A3.

## Goal

Close out Tier 1B (the caller-side half of the route graph) within the
static-analysis remit:

- **B2b** — `HTTP_CALLS` and `ASYNC_CALLS` rel tables. New
  `pass5_imperative_edges` extracts per-method outgoing HTTP and Kafka
  call sites (Feign methods, `RestTemplate`, `KafkaTemplate.send`),
  resolves URI / topic via the same three-strategy ladder as B2a, and
  emits an edge to a `Route` (resolved, phantom, or unresolved).
- **B2b brownfield** — `http_client_overrides`, `async_producer_overrides`,
  `@CodebaseClient`, `@CodebaseProducer` — caller-side mirror of PR-A3.
  Extends `BrownfieldOverrides`; does **not** parallel it.
- **B6** — Cross-service matcher. For every caller-side tuple emitted
  by `pass5`, find the `Route` it targets across services using the
  join-key contract (proposal §3). Emit one of five match outcomes
  (`cross_service`, `intra_service`, `ambiguous`, `phantom`,
  `unresolved`) on each edge. Two new MCP tools
  (`find_route_callers`, `trace_request_flow`); three existing tools
  extended (`impact_analysis`, `trace_flow`, `analyze_pr`).

The three sub-features ship in **three independent PRs** (see §Rollout).

## Principles (do not relitigate in review)

- **Mostly additive.** No table dropped, no MCP tool removed. New
  edge tables / overrides / tools only.
- **Brownfield surface extends `BrownfieldOverrides` — does not parallel
  it.** The caller-side resolver mirrors `resolve_routes_for_method`
  shape-for-shape. Re-read
  [`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`](completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md)
  before touching §PR-D2.
- **One three-strategy resolver, two callers.** PR-A2's
  `_route_value_atoms` (`ast_java.py:1041`) is renamed to
  `_string_value_atoms` in PR-D1 and re-used from `pass5`. **Do not
  duplicate the literal/SpEL/constant-ref ladder.** This is risk #2
  in the proposal and the #1 review trap.
- **`Route` schema is frozen.** §3.1 of the proposal lists the
  read-only contract; PR-D1 adds no columns to `Route`. If you find
  yourself needing one, stop and re-discuss.
- **Edge direction `(Symbol)-[:HTTP_CALLS]->(Route)` /
  `(Symbol)-[:ASYNC_CALLS]->(Route)` is locked.** This pairs with
  B2a's `(Symbol)-[:EXPOSES]->(Route)` so the cross-service
  traversal `(caller)-[:HTTP_CALLS]->(Route)<-[:EXPOSES]-(handler)`
  works without reversal.
- **Confidence-scored edges.** `confidence_base` from the resolver
  ladder × `match_factor` from B6 × `micro_factor`. Formula in
  proposal §5.3. Codified once in PR-D1; PR-D3 only feeds it the
  match outcome.
- **Microservice-aware identity.** Caller `caller_microservice`
  derived from the same `_microservice_for_file` helper B2a uses.
  Self-edges flagged as `intra_service`, never silently dropped.
- **Ontology bump 6 → 7** (PR-D1 only). PR-D2 and PR-D3 do **not**
  bump.
- **Kuzu MAP columns are STRING JSON blobs.** Re-stated from
  PLAN-TIER1: any new map-shaped `graph_meta` field added in PR-D1 /
  PR-D2 / PR-D3 follows the STRING-column + JSON encode/decode
  pattern. Do not try `MAP(STRING, INT64)`.

## PR breakdown — overview

| PR        | Scope                                                                              | Ontology bump | Files touched (approx) | Test buckets                         | Independent of      |
| --------- | ---------------------------------------------------------------------------------- | ------------- | ---------------------- | ------------------------------------ | ------------------- |
| **PR-D1** | B2b core: `HTTP_CALLS` + `ASYNC_CALLS` schema, `pass5_imperative_edges`, shared resolver rename, no brownfield, no MCP tools | 6 → 7         | 4                      | per-pattern detection + resolution + per-outcome | Tier 1 (A1–A3, B, C) |
| **PR-D2** | B2b brownfield: `@CodebaseClient` / `@CodebaseProducer`, `http_client_overrides` + `async_producer_overrides`, 5-layer resolver | none          | 4                      | 12 brownfield fixtures               | PR-D1               |
| **PR-D3** | B6 cross-service matcher: match-outcome enum on edges, two new MCP tools, three existing tools extended | none          | 5                      | match-outcome + traversal + MCP      | PR-D1 (matcher needs HTTP_CALLS) |

PRs land in order **D1 → D2 → D3**. PR-D2 and PR-D3 are independently
mergeable after D1 if priorities shift, with the caveat that D3's
match outcomes default to `phantom` for caller-side fields that only
PR-D2's brownfield surface can resolve. Each PR keeps the test suite
green at every commit.

## Resolved [TBD]s from the proposal

| Proposal location          | Decision                                                                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| §5.1 `web_client`          | **Deferred to v2.** PR-D1 detects the `WebClient` class but emits `unresolved` for any chained URI. Documented in PR-D1 §3.2.          |
| §5.1 `stream_bridge_send`  | **Deferred to v2.** PR-D1 emits `unresolved` (proposal already pre-decided this).                                                      |
| §5.1 `rest_template`       | **In scope.** PR-D1 supports `exchange`, `getForObject`, `getForEntity`, `postForEntity`, `postForObject`, `put`, `delete`. URI is the first argument; HTTP method is derived from method name (`getForObject` → `GET`, etc.) or the `HttpMethod.X` second argument of `exchange`. |
| §5.1 URI string concat     | **In scope, partial.** When the first arg is a binary `+` chain whose right-most operand is a literal `/path`, capture that literal as `path_template_call`. Anything else → `unresolved`. Test: `ChatCoreJoinClient` `base + "/chat/joinOperator"` produces a resolved tail. |
| §5.1 `UriComponentsBuilder`| **Deferred to v2.** PR-D1 emits `unresolved`.                                                                                          |
| §5.2 shared resolver       | **Rename in PR-D1, no separate extraction PR.** `_route_value_atoms` → `_string_value_atoms`. Existing four call sites in `ast_java.py` updated; new pass5 call sites use the same helper. |
| §5.3 confidence weights    | **Adopt baseline as written; validate post-merge.** Validation is a follow-on action item, not a release blocker.                      |
| §5.3 `micro_factor`        | **In scope.** Computed in PR-D1; default `1.0` (caller microservice always known via `_microservice_for_file`); fall back to `0.85` only if the helper returns empty string. |
| §6 brownfield surface      | **Full scope in PR-D2.** Mirrors PR-A3's 5-layer resolver exactly.                                                                     |
| §7.1 `find_route_callers`  | **Exact match in v1, regex follow-on.** Inputs: `route_id` *or* (`microservice`, `path_template`, `method`). No regex parameter on the path; that's a v2 follow-up.                                                              |
| §7.2 `analyze_pr` extension| **In scope.** PR-D3 surfaces "N callers across M services" only when the touched method is the source of an `EXPOSES` edge.            |
| §9 risk #4 multi-broker    | **`broker` already in `Route`'s join key.** PR-D1 reads `KafkaTemplate` bean name as `broker_call`; default `''` (single-broker codebases unaffected). |

If the implementer needs to revisit any of the deferred items, **open a
v2 proposal**, not a follow-on commit on these PRs.

## Test fixture inventory (all three PRs)

| Fixture (in `tests/fixtures/`)            | Used by | Purpose                                                                                  |
| ----------------------------------------- | ------- | ---------------------------------------------------------------------------------------- |
| `bank-chat-system` (existing)             | D1, D3  | Existing call sites: `ChatCoreJoinClient.postForEntity`, `FollowUpKafkaPublisher.send` (×4 with `ChatTopics` constants), `DistributionTriggerPublisher.send`. Provides the cross-service `chat-assign → chat-core` HTTP edge end-to-end. Already has `EXPOSES` rows from Tier 1. |
| `http_caller_smoke` (new in D1)           | D1      | Minimal multi-pattern fixture: `@FeignClient` interface + caller, `RestTemplate.exchange/getForObject/postForEntity`, `KafkaTemplate.send` literal + constant. ~10 files. |
| `web_client_unresolved_smoke` (new in D1) | D1      | One `WebClient.get().uri("/x").retrieve()` chain. Asserts `match='unresolved'`, `strategy='web_client'`, `confidence=0.2 × 0.3 × 1.0 ≈ 0.06`. Locks the v1 deferral. |
| `brownfield_client_stubs` (new in D2)     | D2      | Mirrors PR-A3's `brownfield_route_stubs` fixture: 12 cases for caller-side overrides (annotation, FQN, meta-chain, `@CodebaseClient` source stub, repeatable, last-writer-wins). |
| `cross_service_smoke` (new in D3)         | D3      | Two services that expose the same path; assert callers match only their counterpart, not their own service. Plus one ambiguous case (one path exposed in 3 services). |

The bank-chat-system fixture is the **headline** for manual evidence in
each PR description — it's the only fixture rich enough to demonstrate
all three match outcomes (`cross_service` from
`ChatCoreJoinClient` → `chat-core/joinOperator`, `intra_service` from
`ConfigurableChatAssignment.postForEntity` → its own service's
controller, `phantom` from any external URL).

---

# PR-D1 — B2b core: HTTP_CALLS + ASYNC_CALLS extractor

**Goal:** Land the `HTTP_CALLS` and `ASYNC_CALLS` rel tables and a new
`pass5_imperative_edges` that detects Feign-method, `RestTemplate`,
and `KafkaTemplate.send` call sites, resolves URI / topic via the
shared three-strategy ladder, and emits one edge per call site to a
`Route`. Match-outcome computation is deferred to PR-D3 — D1 emits
edges with `match='unresolved'` for any caller-side resolved field
(D3 will recompute and update `match` in-place from the join-key
contract).

After this PR, querying `MATCH (s:Symbol)-[r:HTTP_CALLS]->(rt:Route)`
on bank-chat-system returns at least 2 rows (the two `postForEntity`
sites) and `MATCH (s:Symbol)-[r:ASYNC_CALLS]->(rt:Route)` returns at
least 5 rows (4 `FollowUpKafkaPublisher.send` + 1
`DistributionTriggerPublisher.send`).

## File-by-file changes

### 1. `ast_java.py` — caller-side dataclass + resolver rename

Additions / renames (~80 lines, 1 rename):

1. **Rename** `_route_value_atoms` → `_string_value_atoms` (`ast_java.py:1041`).
   Update its docstring to drop the "route-ish" framing — it's now the
   universal three-strategy resolver. Update all four existing call
   sites in `ast_java.py` (greppable: `_route_value_atoms`).
   `_literal_strings_from_route_arg` keeps its name (caller is
   B2a-specific) but its body now calls `_string_value_atoms`.

2. New dataclass `OutgoingCallDecl` (export in `__all__`):
   ```python
   @dataclass
   class OutgoingCallDecl:
       method_fqn: str             # owning method's Symbol id
       method_sig: str             # method signature for stable Symbol lookup
       client_kind: str            # 'feign_method' | 'rest_template' | 'web_client' | 'kafka_send' | 'stream_bridge_send'
       channel: str                # 'http' | 'async'
       feign_target_name: str      # @FeignClient(name=…) on the caller interface, '' otherwise
       feign_target_url: str       # @FeignClient(url=…) — '' when name-based or non-Feign
       path_template_call: str     # URI argument, curly-collapsed via _normalize_path; '' if unresolved
       method_call: str            # 'GET' | 'POST' | … or '' when unknown
       topic_call: str             # async only
       broker_call: str            # async only — '' for default broker
       raw_uri: str                # the unresolved URI source text — for debugging in HTTP_CALLS.raw_uri
       raw_topic: str              # async equivalent
       resolution_strategy: str    # 'feign_inherit' | 'feign_method' | 'rest_template' | 'web_client' | 'kafka_template' | 'stream_bridge' | 'unresolved'
       confidence_base: float      # 1.0 / 0.85 / 0.7 / 0.3 (unresolved)
       resolved: bool
       filename: str
       start_line: int
       end_line: int
   ```

3. New `MethodDecl.outgoing_calls: list[OutgoingCallDecl] = field(default_factory=list)`.

4. **Bump `ONTOLOGY_VERSION` from 6 to 7.** Update the comment to
   mention "Phase 5: HTTP_CALLS + ASYNC_CALLS (B2b)".

5. New helper `_collect_outgoing_calls(method_node, type_node, src, *, ctx, project_root)`
   called from `_parse_method` after `_collect_routes`. Detection
   patterns are listed in §3.1 below; the helper emits one
   `OutgoingCallDecl` per call site. **PR-D1 emits no `match` field
   yet** — PR-D3 fills it.

6. The `feign_method` case is special: the *caller* method is itself
   the Feign-interface method. The corresponding `Route` already
   exists from B2a's `feign_inherit` strategy. In `pass5`, the
   `feign_method` join is by `(caller method's Symbol.id == Route's
   exposing Symbol.id)` — no URI resolution needed. **PR-D1 implements
   this as the cleanest case (no string resolution).** The helper
   emits an `OutgoingCallDecl` with `client_kind='feign_method'`,
   `feign_target_name = <interface's @FeignClient(name=…)>`,
   `confidence_base=1.0`, `resolution_strategy='feign_method'`.

### 2. `java_ontology.py` — caller taxonomy

Additions (~10 lines):

```python
VALID_CLIENT_KINDS: frozenset[str] = frozenset((
    "feign_method", "rest_template", "web_client",
    "kafka_send", "stream_bridge_send",
))

VALID_HTTP_CALL_STRATEGIES: frozenset[str] = frozenset((
    "feign_method", "rest_template", "web_client", "unresolved",
))

VALID_ASYNC_CALL_STRATEGIES: frozenset[str] = frozenset((
    "kafka_template", "stream_bridge", "rabbit_template", "jms_template", "unresolved",
))

VALID_HTTP_CALL_MATCHES: frozenset[str] = frozenset((
    "cross_service", "intra_service", "ambiguous", "phantom", "unresolved",
))
```

Add all to `__all__`. PR-D3 reads `VALID_HTTP_CALL_MATCHES`.

### 3. `build_ast_graph.py` — schema, pass5, writers

#### 3.1 Detection patterns (`_collect_outgoing_calls` body)

The helper walks the method body's AST and emits an
`OutgoingCallDecl` per recognised call site. Recognise these patterns
(every other call passes through silently):

| Pattern                                                                | `client_kind`        | URI / topic source                                         | Method derivation                                                |
| ---------------------------------------------------------------------- | -------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------- |
| Method on an interface annotated `@FeignClient` (any method)           | `feign_method`       | n/a (matched by Symbol.id of the method itself)            | n/a                                                              |
| `restTemplate.{exchange,getForObject,getForEntity,postForEntity,postForObject,put,delete}(uri, …)` (any receiver of type `RestTemplate`) | `rest_template`      | First positional arg → `_string_value_atoms`               | Method name → `GET`/`POST`/`PUT`/`DELETE`; `exchange` reads the `HttpMethod.X` second arg. |
| `webClient.{get,post,put,delete,patch}().uri("…").retrieve()…` chain   | `web_client`         | **PR-D1: emit `unresolved`** (deferred to v2)              | Method name on first link of chain                               |
| `kafkaTemplate.send(topic, …)` (any receiver of type `KafkaTemplate`)  | `kafka_send`         | First positional arg → `_string_value_atoms`               | n/a (async)                                                      |
| `streamBridge.send(binding, …)`                                        | `stream_bridge_send` | **PR-D1: emit `unresolved`** (deferred to v2)              | n/a                                                              |

Receiver-type inference uses the same heuristics as the existing
`pass3_calls` callee-resolver (search for how `pass3_calls` deduces
receiver types — re-use, do not reinvent). When the receiver type
cannot be determined, **skip the call site silently** (false-negative
preferred over false-positive).

URI string concatenation handling (PR-D1 in scope):

- For `RestTemplate.X(<expr>, …)`, if `<expr>` is a binary `+` chain
  and at least one literal-string operand starts with `/`, capture
  that literal as `path_template_call` (after normalising via
  `_normalize_path`). `confidence_base = 0.7`,
  `resolution_strategy = 'rest_template'`, `resolved = False`. Stash
  the full `_txt(<expr>, src)` as `raw_uri`.
- For pure literal first arg, `confidence_base = 1.0`,
  `strategy = 'rest_template'`, `resolved = True`.
- For SpEL (`${...}`) literal first arg, `confidence_base = 0.85`,
  `resolved = False`.
- For constant ref (`Endpoints.USERS`), `confidence_base = 0.7`,
  `resolved = False`.
- For everything else (lambda, ternary, method call), emit
  `confidence_base = 0.3`, `resolution_strategy = 'unresolved'`,
  `resolved = False`, `path_template_call = ''`, `raw_uri = <full expression text>`.

The Kafka topic resolution mirrors the URI ladder exactly — same
helper, same four outcomes.

#### 3.2 Schema additions

Add after the existing `_SCHEMA_EXPOSES` constant:

```python
_SCHEMA_HTTP_CALLS = (
    "CREATE REL TABLE HTTP_CALLS(FROM Symbol TO Route, "
    "confidence DOUBLE, strategy STRING, "
    "method_call STRING, raw_uri STRING, match STRING)"
)
_SCHEMA_ASYNC_CALLS = (
    "CREATE REL TABLE ASYNC_CALLS(FROM Symbol TO Route, "
    "confidence DOUBLE, strategy STRING, "
    "direction STRING, raw_topic STRING, match STRING)"
)
```

Add both to the create-tables list and the drop-on-rebuild list.
Edge direction `(Symbol)-[:HTTP_CALLS]->(Route)` is **locked**.

PR-D1 writes `match='unresolved'` for every emitted edge. PR-D3
overwrites this column in its writer.

#### 3.3 Dataclasses

Add to `GraphTables`:

```python
http_call_rows:  list[HttpCallRow]      = field(default_factory=list)
async_call_rows: list[AsyncCallRow]     = field(default_factory=list)
call_edge_stats: CallEdgeStats          = field(default_factory=CallEdgeStats)
```

`HttpCallRow`: `(symbol_id, route_id, confidence, strategy,
method_call, raw_uri, match)`. `AsyncCallRow`: same shape with
`direction='producer'` (always — consumer side is `EXPOSES`),
`raw_topic` instead of `raw_uri`. `CallEdgeStats`: counters per
`client_kind`, per `strategy`, per `match`, plus
`http_calls_skipped_unresolved` / `async_calls_skipped_unresolved`.

#### 3.4 New `pass5_imperative_edges` function

Runs after `pass4_routes` (proposal §5.4). Signature mirrors `pass4_routes`:

```python
def pass5_imperative_edges(
    tables: GraphTables,
    asts: dict[str, JavaFileAst],
    *,
    source_root: Path,
    verbose: bool,
) -> None: ...
```

Loop:

1. Build a `routes_by_id: dict[str, RouteRow]` index over
   `tables.routes_rows` (for the `feign_method` case to look up the
   pre-existing route's id from the caller's Symbol id).
2. For each `MethodDecl` with `method.outgoing_calls`:
   - Determine `caller_microservice` via `_microservice_for_file`.
   - For each `OutgoingCallDecl`:
     - **`feign_method` shortcut:** look up the `Route` whose exposing
       `Symbol.id` equals the caller method's `Symbol.id` *in the
       caller's microservice*. If found, emit
       `HttpCallRow(symbol_id=caller, route_id=that route, confidence=1.0,
       strategy='feign_method', method_call=that route.method, raw_uri='',
       match='unresolved')`. PR-D3 will recompute `match`. If not
       found, fall through to the unresolved branch.
     - **Resolved (literal / SpEL / const-ref / concat-tail) HTTP:**
       compute `route_id` by hashing the same key B2a uses
       (`_route_id(framework='', kind='http_endpoint', http_method=method_call,
       path_template=path_template_call, topic='', broker='',
       microservice='')` — note `framework=''` and
       `microservice=''` for the *caller-side* synthetic id; B2a
       always uses concrete values, so caller-side ids never collide
       with exposer-side ids). The matched real `Route` (with its
       resolved id) is found in PR-D3.

       In PR-D1, emit a **phantom `Route`** with this synthetic id
       and `path_template`, `path_regex`, `microservice='', resolved=False`.
       Append a `HttpCallRow` pointing to it.
       `confidence = confidence_base × 0.3 × micro_factor` (PR-D1's
       fixed `match_factor=0.3` — PR-D3 recomputes).
     - **Unresolved HTTP:** emit a phantom `Route` with synthetic id
       `r:phantom:{sha1(filename+start_line+raw_uri)[:12]}` to keep
       it unique per call site. Confidence as above with
       `confidence_base=0.3`.
     - **Async (Kafka):** mirror HTTP. The synthetic route id key:
       `_route_id('', 'kafka_topic', '', '', topic_call, broker_call, '')`.
       Emit `AsyncCallRow(direction='producer', raw_topic=…, match='unresolved')`.
   - Update `tables.call_edge_stats`.
3. Phantom `Route` rows go into `tables.routes_rows` alongside B2a's
   resolved ones — same writer, same dedupe by `id`.

The pass does **not** read or mutate `tables.calls_rows` or
`tables.exposes_rows`.

#### 3.5 Writers

Add writer blocks after the existing `EXPOSES` writer:

- Insert any new (phantom) `Route` rows from `tables.routes_rows`
  that weren't already inserted by `pass4_routes`. The B2a writer
  already dedupes on `id` — PR-D1's writer can simply re-call the
  same insert with `OR IGNORE` semantics (or compute the diff and
  insert only new ids).
- Insert `HTTP_CALLS` rows; dedup by `(from, to)`.
- Insert `ASYNC_CALLS` rows; dedup by `(from, to)`.

#### 3.6 graph_meta extension

Add to the `graph_meta` MERGE call:

```python
"http_calls_total INT64, "
"async_calls_total INT64, "
"http_calls_by_strategy STRING, "    # JSON blob: {strategy: count}
"async_calls_by_strategy STRING, "
"http_calls_resolved_pct DOUBLE, "
"async_calls_resolved_pct DOUBLE, "
```

`*_by_strategy` follows the same Kuzu MAP-as-STRING pattern (see PLAN-TIER1
PR-A1 §3.6). `*_resolved_pct` = % of edges where `strategy != 'unresolved'`.

PR-D3 will add `*_match_breakdown` columns (cross_service / intra_service /
ambiguous / phantom counts). **Do not pre-add them in PR-D1.**

#### 3.7 CLI wire-up

In `main`, add `pass5_imperative_edges(tables, asts, source_root=root, verbose=args.verbose)`
right after `pass4_routes(...)`.

### 4. Tests for PR-D1

#### 4.1 New test file: `tests/test_outgoing_call_extraction.py`

Inline-source unit tests for `_collect_outgoing_calls` and the shared
`_string_value_atoms` rename. Required cases:

| #  | Test name                                                  | Asserts                                                                                  |
| -- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 1  | `test_string_value_atoms_renamed_call_sites_still_work`    | All four ex-`_route_value_atoms` call sites in `ast_java.py` still produce identical output on a sampled fixture (regression guard for the rename). |
| 2  | `test_feign_method_caller_emits_outgoing_call`             | Calling a `@FeignClient` interface method emits one `OutgoingCallDecl(client_kind='feign_method', feign_target_name='user-svc')`. |
| 3  | `test_rest_template_get_for_object_literal`                | `restTemplate.getForObject("/api/users", String.class)` → `client_kind='rest_template'`, `path_template_call='/api/users'`, `method_call='GET'`, `confidence_base=1.0`. |
| 4  | `test_rest_template_exchange_with_http_method_const`       | `restTemplate.exchange("/x", HttpMethod.PUT, …)` → `method_call='PUT'`. |
| 5  | `test_rest_template_post_for_entity_string_concat_tail`    | `restTemplate.postForEntity(base + "/chat/joinOperator", …)` → `path_template_call='/chat/joinOperator'`, `confidence_base=0.7`, `resolved=False`, `raw_uri` contains the full expression text. |
| 6  | `test_rest_template_spel_uri`                              | `restTemplate.getForObject("${api.path}", …)` → `confidence_base=0.85`, `strategy='rest_template'`, `resolved=False`. |
| 7  | `test_rest_template_constant_ref_uri`                      | `restTemplate.getForObject(Endpoints.USERS, …)` → `confidence_base=0.7`, `resolved=False`. |
| 8  | `test_rest_template_unresolved_uri_method_call`            | `restTemplate.getForObject(buildUri(), …)` → `strategy='rest_template'`, `confidence_base=0.3`, `resolved=False`, `path_template_call=''`. |
| 9  | `test_kafka_template_send_literal`                         | `kafkaTemplate.send("orders", payload)` → `client_kind='kafka_send'`, `topic_call='orders'`, `confidence_base=1.0`. |
| 10 | `test_kafka_template_send_constant_ref`                    | `kafkaTemplate.send(ChatTopics.INCOMING, …)` → `topic_call='ChatTopics.INCOMING'`, `confidence_base=0.7`, `resolved=False`. |
| 11 | `test_web_client_chain_emits_unresolved_v1`                | `webClient.get().uri("/x").retrieve()` → `client_kind='web_client'`, `strategy='unresolved'`, `confidence_base=0.3`. (Locks v1 deferral.) |
| 12 | `test_stream_bridge_emits_unresolved_v1`                   | `streamBridge.send("binding-out-0", payload)` → `strategy='unresolved'`. |
| 13 | `test_unknown_receiver_type_silently_skipped`              | `someObj.send("x")` where `someObj` has no inferable type → no `OutgoingCallDecl` emitted. |

#### 4.2 New integration test: `tests/test_call_edges_e2e.py`

| #  | Test name                                       | Asserts                                                                                  |
| -- | ----------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 14 | `test_http_calls_table_built_on_bank_chat`      | After `build_ast_graph.py --source-root tests/bank-chat-system`, `MATCH (s:Symbol)-[r:HTTP_CALLS]->(rt:Route) RETURN count(*)` ≥ 2. Both `postForEntity` sites present. |
| 15 | `test_async_calls_table_built_on_bank_chat`     | Same, ≥ 5 async edges. All four `FollowUpKafkaPublisher.send` plus `DistributionTriggerPublisher.send`. |
| 16 | `test_pr_d1_emits_unresolved_match_for_all`     | Every emitted edge has `match='unresolved'` in PR-D1 (PR-D3 will overwrite). |
| 17 | `test_phantom_routes_dedup_across_call_sites`   | Two methods calling `restTemplate.getForObject("/api/users", …)` produce two `HTTP_CALLS` edges to the **same** phantom `Route` (id collision is intentional pre-D3). |
| 18 | `test_graph_meta_call_edge_counters`            | `graph_meta.http_calls_total > 0`, `async_calls_total > 0`, `*_by_strategy` is a JSON dict, `*_resolved_pct` ∈ [0,1]. |
| 19 | `test_ontology_version_bumped_to_7`             | `ONTOLOGY_VERSION == 7` and `meta()` reports `7`. |

#### 4.3 New unit test: `tests/test_string_value_atoms.py`

A focused regression-guard test for the rename (see test 1 above) —
exists as a separate file so future B2c / B2d / Bx callers know to
re-use this helper.

#### 4.4 Manual evidence fixture: `tests/fixtures/http_caller_smoke/`

~10 Java files: one Feign interface + caller, one
`RestTemplate.exchange` call, one literal `KafkaTemplate.send`, one
constant-ref `KafkaTemplate.send`, one `WebClient` chain (asserted as
unresolved). pom.xml minimal. Used by tests 11, 13.

### 5. PR-D1 Definition of done

- [ ] `_route_value_atoms` renamed to `_string_value_atoms`; no
      duplicate three-strategy ladder anywhere in the codebase
      (`grep -n "annotation.*1\.0\|spel.*0\.85\|constant_ref.*0\.7" .` returns the
      one definition only).
- [ ] `HTTP_CALLS` and `ASYNC_CALLS` tables created; ontology bumped 6 → 7.
- [ ] `pass5_imperative_edges` runs after `pass4_routes`; verbose
      output reports per-`client_kind` and per-`strategy` counts.
- [ ] `graph_meta` includes `http_calls_total`, `async_calls_total`,
      `http_calls_by_strategy`, `async_calls_by_strategy`,
      `http_calls_resolved_pct`, `async_calls_resolved_pct`.
- [ ] `pytest tests -q` green; regression: existing tests at the same
      count as master + 19 new tests.
- [ ] Manual evidence in PR description: bank-chat-system rebuild
      shows `http_calls_total >= 2`, `async_calls_total >= 5`,
      and `pass5` log line `[pass5] HTTP_CALLS: N edges, ASYNC_CALLS: M edges`.

## PR-D1 implementation step list

| # | Step                                                           | File(s)                  | Done when                              |
| - | -------------------------------------------------------------- | ------------------------ | -------------------------------------- |
| 1 | Rename `_route_value_atoms` → `_string_value_atoms`; update all four call sites | `ast_java.py`        | `grep -n "_route_value_atoms" .` returns nothing |
| 2 | Add `OutgoingCallDecl`, `MethodDecl.outgoing_calls`            | `ast_java.py`            | dataclass exported                     |
| 3 | Implement `_collect_outgoing_calls` for Feign / RestTemplate / Kafka | `ast_java.py`      | tests 2–10, 13 pass                    |
| 4 | Implement WebClient + StreamBridge unresolved branches         | `ast_java.py`            | tests 11, 12 pass                      |
| 5 | Add `VALID_CLIENT_KINDS`, `VALID_HTTP_CALL_*`, `VALID_ASYNC_CALL_*` | `java_ontology.py`  | imports succeed                        |
| 6 | Add `_SCHEMA_HTTP_CALLS`, `_SCHEMA_ASYNC_CALLS` to create + drop lists | `build_ast_graph.py` | DDL runs                              |
| 7 | Add `HttpCallRow`, `AsyncCallRow`, `CallEdgeStats`, `GraphTables` fields | `build_ast_graph.py` | dataclass passes mypy / runtime       |
| 8 | Implement `pass5_imperative_edges`; wire into `main` after `pass4_routes` | `build_ast_graph.py` | tests 14–17 pass on bank-chat       |
| 9 | Implement HTTP_CALLS + ASYNC_CALLS writers; phantom-route insert dedup | `build_ast_graph.py` | tests 14, 15, 17 pass               |
| 10 | Extend `graph_meta` schema + populate from `call_edge_stats`  | `build_ast_graph.py`     | test 18 passes                         |
| 11 | Bump `ONTOLOGY_VERSION` 6 → 7                                  | `ast_java.py`            | test 19 passes                         |
| 12 | Update `README.md` route section: add `HTTP_CALLS` / `ASYNC_CALLS` row to the schema table | `README.md` | manual review                          |

---

# PR-D2 — B2b brownfield: caller-side overrides + `@CodebaseClient` / `@CodebaseProducer`

**Goal:** Add the caller-side mirror of PR-A3's brownfield surface.
After this PR, users with legacy `@LegacyHttpClient` /
`@LegacyEvent`-style annotations can map them to `client_kind` +
`target_service` + `topic` via `.lancedb-mcp.yml` and / or
`@CodebaseClient` / `@CodebaseProducer` source stubs. Brownfield
overrides feed the same `OutgoingCallDecl` pipeline shipped in PR-D1.

## File-by-file changes

### 1. `graph_enrich.py` — extend `BrownfieldOverrides` + new resolver

Additions (~120 lines, no removals):

1. New dataclasses (mirror `RouteHint`):
   ```python
   @dataclass(frozen=True)
   class HttpClientHint:
       client_kind: str           # 'feign_method' | 'rest_template' | 'web_client'
       target_service: str        # forces feign_target_name, '' if absent
       path: str                  # optional literal path; '' to keep auto-detected
       method: str                # optional HTTP method; '' to keep auto-detected

   @dataclass(frozen=True)
   class AsyncProducerHint:
       client_kind: str           # 'kafka_send' | 'stream_bridge_send' | …
       topic: str                 # required when present
       broker: str                # default ''
   ```

2. Extend `BrownfieldOverrides`:
   ```python
   @dataclass
   class BrownfieldOverrides:
       # … existing fields (annotation_to_role, etc.) …
       annotation_to_http_client_hint: dict[str, HttpClientHint]
       fqn_to_http_client_hint: dict[str, HttpClientHint]
       annotation_to_async_producer_hint: dict[str, AsyncProducerHint]
       fqn_to_async_producer_hint: dict[str, AsyncProducerHint]
   ```

3. Extend `load_brownfield_overrides`:
   - New YAML keys `http_client_overrides.{annotations, fqn}` and
     `async_producer_overrides.{annotations, fqn}`. Schema mirrors
     `route_overrides` exactly. Validate `client_kind` against
     `VALID_CLIENT_KINDS`.
   - Same `[lancedb-mcp]` warning format on unknown keys (search for
     `route_overrides.annotations: unknown framework` for the
     existing pattern).

4. New module-level functions:
   ```python
   def resolve_http_client_for_method(
       *,
       method_decl: MethodDecl,
       enclosing_type: TypeDecl,
       overrides: BrownfieldOverrides,
       meta_chain: dict[str, frozenset[str]],
       builtin_calls: list[OutgoingCallDecl],
   ) -> list[OutgoingCallDecl]: ...

   def resolve_async_producer_for_method(...) -> list[OutgoingCallDecl]: ...
   ```

   Composition order (mirrors `resolve_routes_for_method` line for
   line — cite `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`
   line numbers in PR description):

   1. Built-in detection (PR-D1's `OutgoingCallDecl`s already in
      `builtin_calls`).
   2. Layer B: `annotation_to_http_client_hint` /
      `annotation_to_async_producer_hint`.
   3. Layer A: meta-annotation chain walk (re-use
      `collect_annotation_meta_chain`).
   4. Layer C: `@CodebaseClient` / `@CodebaseProducer` in source.
   5. Layer B: `fqn_to_http_client_hint` /
      `fqn_to_async_producer_hint` (outermost — last writer wins).

   Last writer wins, exactly like B2a.

### 2. `ast_java.py` — `@CodebaseClient` / `@CodebaseProducer` parsing

Additions (~30 lines):

1. Add `CODEBASE_CLIENT_ANNOTATIONS` / `CODEBASE_PRODUCER_ANNOTATIONS`
   frozensets next to the existing `CODEBASE_ROUTE_ANNOTATIONS`.

2. In `_collect_outgoing_calls`, after the built-in detection,
   look for `@CodebaseClient` / `@CodebaseProducer` annotations on
   the method (including `@Repeatable` containers `@CodebaseClients`
   / `@CodebaseProducers`). For each, append an `OutgoingCallDecl`
   with `resolution_strategy='codebase_client'` /
   `'codebase_producer'`, `confidence_base=1.0`, `resolved=True`.
   The runtime values (`clientKind`, `targetService`, `topic`,
   `broker`, `path`, `method`) come from the annotation arguments
   parsed via `_string_value_atoms`.

### 3. `build_ast_graph.py` — wire brownfield resolver into `pass5`

Modify `pass5_imperative_edges` (~10 lines):

- After collecting `member.decl.outgoing_calls`, call
  `resolve_http_client_for_method(...)` and
  `resolve_async_producer_for_method(...)` to get the *final* list
  of outgoing calls (built-in + brownfield-merged).
- Pass `overrides` and `meta_chain` from `pass5`'s top (load once,
  same pattern as `pass4_routes`).
- Update `graph_meta` to add:
  - `http_clients_from_brownfield_pct DOUBLE`
  - `async_producers_from_brownfield_pct DOUBLE`

  Computed as: % of final outgoing calls whose
  `resolution_strategy ∈ {layer_b_ann, layer_a_meta, layer_c_source,
  layer_b_fqn, codebase_client, codebase_producer}`.

### 4. New stub source: `tests/fixtures/brownfield_client_stubs/`

12 fixtures mirroring `brownfield_route_stubs` — cases for caller-side
overrides:

| #  | Case                                                                        |
| -- | --------------------------------------------------------------------------- |
| 20 | Layer B annotation: legacy annotation mapped to `feign_method` + `target_service` |
| 21 | Layer B FQN: FQN-based mapping wins over auto-detection                     |
| 22 | Layer A meta-annotation chain: `@LegacyHttpClient` → `@CodebaseClient` chain |
| 23 | Layer C source: bare `@CodebaseClient` produces an `OutgoingCallDecl`       |
| 24 | Layer C source for async: `@CodebaseProducer(topic="x")` on a method        |
| 25 | Repeatable `@CodebaseClient` (×2): produces two `OutgoingCallDecl`s         |
| 26 | Last writer wins: layer-B-fqn overrides layer-C-source                      |
| 27 | Method-level wins over built-in: `@CodebaseClient` on a method that already has `restTemplate.exchange` produces only the override (or both? see PR-A3 for the precedent — replicate exactly) |
| 28 | `client_kind=feign_method` + `target_service='user-svc'`: ensures `feign_target_name` is forced even on a `RestTemplate.exchange` |
| 29 | YAML `http_client_overrides.annotations` with unknown `client_kind` → warning + skip |
| 30 | Brownfield % counter: `http_clients_from_brownfield_pct` ≥ expected fraction on the fixture |
| 31 | Async-side equivalent of #20: legacy annotation → `kafka_send` + `topic`    |

### 5. PR-D2 Definition of done

- [ ] `BrownfieldOverrides` extended (no parallel structure introduced).
- [ ] `HttpClientHint` and `AsyncProducerHint` dataclasses live.
- [ ] `resolve_http_client_for_method` and
      `resolve_async_producer_for_method` mirror
      `resolve_routes_for_method` shape-for-shape.
- [ ] `@CodebaseClient` / `@CodebaseProducer` in-source stubs parsed
      from `@Repeatable` containers as well as singular forms.
- [ ] `graph_meta` exposes `http_clients_from_brownfield_pct` and
      `async_producers_from_brownfield_pct`.
- [ ] `pytest` green; 12 new brownfield fixtures pass.
- [ ] PR description cites `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`
      line numbers for the resolver's 5-layer table (mandatory — risk #5).
- [ ] Manual evidence: rebuild bank-chat-system with a sample
      `.lancedb-mcp.yml` adding one `http_client_override` —
      `http_clients_from_brownfield_pct > 0`.

## PR-D2 implementation step list

| # | Step                                                                | File(s)                            | Done when                       |
| - | ------------------------------------------------------------------- | ---------------------------------- | ------------------------------- |
| 1 | Add `HttpClientHint` + `AsyncProducerHint` dataclasses              | `graph_enrich.py`                  | dataclasses imported            |
| 2 | Extend `BrownfieldOverrides` with 4 new dicts                       | `graph_enrich.py`                  | mypy passes                     |
| 3 | Extend `load_brownfield_overrides` to parse new YAML keys + warn    | `graph_enrich.py`                  | test 29 passes                  |
| 4 | Implement `resolve_http_client_for_method` (5-layer)                | `graph_enrich.py`                  | tests 20–22, 26 pass            |
| 5 | Implement `resolve_async_producer_for_method` (5-layer)             | `graph_enrich.py`                  | test 31 passes                  |
| 6 | Add `CODEBASE_CLIENT_ANNOTATIONS` + `CODEBASE_PRODUCER_ANNOTATIONS` | `ast_java.py`                      | constants exported              |
| 7 | Parse `@CodebaseClient` / `@CodebaseProducer` in `_collect_outgoing_calls` | `ast_java.py`              | tests 23–25 pass                |
| 8 | Wire `resolve_*_for_method` into `pass5_imperative_edges`           | `build_ast_graph.py`               | tests 27, 28 pass               |
| 9 | Add `*_from_brownfield_pct` to `graph_meta`                         | `build_ast_graph.py`               | test 30 passes                  |
| 10 | Create `tests/fixtures/brownfield_client_stubs/`                    | `tests/fixtures/...`              | fixture files in place          |
| 11 | Create `tests/test_brownfield_clients.py` (12 cases)                | `tests/test_brownfield_clients.py` | all 12 pass                     |
| 12 | Update `README.md` with brownfield client / producer override docs  | `README.md`                        | manual review                   |

---

# PR-D3 — B6 cross-service matcher + MCP surface

**Goal:** Wire up the cross-service matcher described in proposal §3.3
(match-outcome enum). After this PR, every `HTTP_CALLS` and
`ASYNC_CALLS` edge has its `match` column populated with one of
`cross_service` / `intra_service` / `ambiguous` / `phantom` /
`unresolved`. Two new MCP tools (`find_route_callers`,
`trace_request_flow`) expose the joined caller→handler chain. Three
existing tools (`impact_analysis`, `trace_flow`, `analyze_pr`)
extend their walks to follow the new edges.

PR-D3 does **not** change the `pass5` extractor. It runs as a *new*
`pass6_match_edges` that reads the caller-side phantom routes
emitted by PR-D1 and rewrites the edge `match` + `route_id` columns
in-place when a real `Route` is found.

## File-by-file changes

### 1. `build_ast_graph.py` — `pass6_match_edges`

Additions (~150 lines):

1. New module-level helper:
   ```python
   def _match_call_edge(
       call: OutgoingCallDecl,
       routes: list[RouteRow],
       caller_microservice: str,
   ) -> tuple[str, list[RouteRow]]:
       """Return (outcome, candidate_routes). outcome ∈ VALID_HTTP_CALL_MATCHES."""
   ```

   Algorithm (proposal §3 contract):

   - **`feign_method`:** filter `routes` where `feign_name == call.feign_target_name`
     and the exposing `Symbol.id` matches the caller's expected
     interface method (already the same Symbol — the route was
     emitted by `feign_inherit`). Should always be exactly one;
     `cross_service` if its `microservice != caller_microservice`,
     `intra_service` otherwise.
   - **`rest_template` / `web_client` HTTP:** filter `routes` where
     `kind == 'http_endpoint'`, `method == call.method_call` (or
     either is `''`), and `re.fullmatch(route.path_regex,
     call.path_template_call)` matches. Apply
     `caller_microservice != route.microservice` discriminator for
     `cross_service` vs `intra_service`.
   - **`kafka_send` / async:** filter `routes` where `topic == call.topic_call`
     and `broker == call.broker_call` (with `''` matching `''`
     literally; not a wildcard).
   - **Outcome aggregation:**
     - 0 candidates → `phantom`
     - 1 candidate, different microservice → `cross_service`
     - 1 candidate, same microservice → `intra_service`
     - >1 candidate → `ambiguous`
     - `call.resolved == False` and `call.path_template_call == ''`
       (and `call.topic_call == ''`) → `unresolved` short-circuits
       before any of the above.

2. New `pass6_match_edges`:

   ```python
   def pass6_match_edges(
       tables: GraphTables,
       *,
       verbose: bool,
   ) -> None: ...
   ```

   For each `HttpCallRow` / `AsyncCallRow` with `match='unresolved'`
   (which is *every* row from PR-D1):
   - Look up the originating `OutgoingCallDecl` via the row's
     `(symbol_id, raw_uri / raw_topic, …)` key (PR-D1 must persist
     enough to round-trip — see PR-D1 §3.3 fields).
   - Call `_match_call_edge`.
   - Update the edge row's `match` column.
   - If `outcome ∈ {cross_service, intra_service}` and exactly one
     candidate matched, **rewrite the edge's `route_id`** to point to
     the real `Route` (not the phantom one). The phantom `Route` is
     left in `tables.routes_rows` as orphaned — a separate cleanup
     pass at the end of `pass6` removes any phantom `Route` row
     with zero incoming `HTTP_CALLS` / `ASYNC_CALLS` edges.
   - Recompute `confidence`:
     `confidence = call.confidence_base × match_factor × micro_factor`
     where `match_factor` is from §5.3:
     `cross_service=1.0`, `intra_service=0.6`, `ambiguous=0.5`,
     `phantom=0.4`, `unresolved=0.3`.
   - Update `tables.call_edge_stats` per-`match` counters.

3. Wire `pass6_match_edges` into `main` after `pass5_imperative_edges`.

4. Extend `graph_meta`:
   - `http_calls_match_breakdown STRING` (JSON: `{outcome: count}`)
   - `async_calls_match_breakdown STRING` (JSON: `{outcome: count}`)
   - `cross_service_calls_total INT64` (sum of `cross_service` outcomes
     across both edge types — handy single number for PR-A1-style
     headlines).

### 2. `kuzu_queries.py` — new query helpers

Additions (~80 lines):

1. `find_route_callers(route_id: str | None = None, *, microservice: str = '', path_template: str = '', method: str = '') -> list[CallerInfo]`
   - If `route_id` provided, look it up directly.
   - Else, find the `Route` matching `(microservice, path_template, method)`
     (exact match, no regex — see resolved [TBD] above).
   - Return all `(s)-[:HTTP_CALLS|:ASYNC_CALLS]->(rt:Route)` where
     `rt.id` matches. Each result includes `caller_symbol_id`,
     `caller_microservice`, `confidence`, `match`.

2. `trace_request_flow(entry_route_id: str, max_hops: int = 5) -> FlowChain`
   - Cypher walk:
     `MATCH path = (entry:Route {id: $rid}) <-[:HTTP_CALLS|:ASYNC_CALLS]- (caller:Symbol)
       <-[:CALLS*0..$max_hops]- (origin:Symbol)`
     Plus the *outbound* leg from `entry`'s handler:
     `(handler:Symbol)-[:EXPOSES]->(entry) -[CALLS*]-> …`
   - Returns an ordered chain across services; preserves the
     structural-first ordering from the call-graph D5 fix (see
     `reports/call-graph-review.md`).

### 3. `server.py` — register MCP tools + extend existing

Additions (~60 lines):

1. Register two new MCP tools:
   ```python
   @mcp.tool()
   def find_route_callers(...) -> dict: ...

   @mcp.tool()
   def trace_request_flow(...) -> dict: ...
   ```

2. Extend `impact_analysis`:
   - In the reverse-closure step, for any `Route` reached via
     `EXPOSES`, also follow `HTTP_CALLS` / `ASYNC_CALLS` *outbound
     from* the `Route` to find callers across services.
   - Result struct gains `cross_service_callers` field (list of
     `(caller_symbol_id, caller_microservice, match, confidence)`).

3. Extend `trace_flow`:
   - Add `HTTP_CALLS` / `ASYNC_CALLS` to its budgeted walk.
   - Preserve the structural-first ordering: same-microservice
     `CALLS` edges come before cross-service `HTTP_CALLS` /
     `ASYNC_CALLS` at each step.

4. Extend `analyze_pr`:
   - For each changed method that's the source of an `EXPOSES` edge,
     surface a `cross_service_callers_count` field in the per-symbol
     risk record.
   - Do **not** add `HTTP_CALLS` to the symbol-impact walk — that's
     `impact_analysis`'s job; `analyze_pr` reads its result.

### 4. `pr_analysis.py` — risk score weighting (small)

Modify the risk-score calculation to add weight when a changed
method has `cross_service_callers_count > 0`. Suggested weight:
`+1.0` per cross-service caller, capped at `+5.0`. Document the
weight in the function docstring.

### 5. Tests for PR-D3

#### 5.1 New test file: `tests/test_call_edge_matching.py`

| #  | Test name                                              | Asserts                                                                                  |
| -- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| 32 | `test_match_cross_service_resttemplate`                | `chat-assign`'s `ChatCoreJoinClient.postForEntity("/chat/joinOperator", …)` matches `chat-core`'s `POST /chat/joinOperator` route → `match='cross_service'`, edge `route_id` rewritten to the real route. |
| 33 | `test_match_intra_service_resttemplate`                | `chat-core`'s `ConfigurableChatAssignment.postForEntity` to its own service's controller → `match='intra_service'`. |
| 34 | `test_match_ambiguous_two_services_same_path`          | Two services expose `POST /api/users`; one caller hits it → `match='ambiguous'`, both candidates logged. |
| 35 | `test_match_phantom_external_url`                      | Caller hits `https://external.com/api/x` (no matching `Route` anywhere) → `match='phantom'`. |
| 36 | `test_match_unresolved_short_circuits`                 | Caller with `webClient.get().uri(buildUri()).retrieve()` → `match='unresolved'` regardless of any matching `Route` (short-circuits). |
| 37 | `test_feign_method_cross_service_match`                | Caller calls a `@FeignClient(name="user-svc")` method → matches the `Route` whose `feign_name='user-svc'` in another service. |
| 38 | `test_kafka_topic_broker_disambiguation`               | Two `KafkaListener`s on the same topic but different brokers; producer with `broker_call=''` matches only the default-broker one. |
| 39 | `test_confidence_recomputed_per_outcome`               | `cross_service` edge: `confidence = confidence_base × 1.0 × 1.0`. `intra_service`: `× 0.6`. `phantom`: `× 0.4`. `unresolved`: `× 0.3`. |
| 40 | `test_phantom_routes_cleaned_up_when_real_match_found` | After `pass6`, the phantom `Route` for a now-resolved cross-service edge has zero incoming edges; cleanup removes it. |

#### 5.2 New MCP tool tests in `tests/test_mcp_tools.py`

| #  | Test name                                       | Asserts                                                                |
| -- | ----------------------------------------------- | ---------------------------------------------------------------------- |
| 41 | `test_find_route_callers_by_route_id`           | Returns ≥ 1 caller for a known route_id with at least one HTTP_CALLS edge. |
| 42 | `test_find_route_callers_by_path_method`        | Same lookup via `(microservice, path_template, method)`.               |
| 43 | `test_find_route_callers_no_match_returns_empty`| Unknown route_id → `[]`, not an error.                                 |
| 44 | `test_trace_request_flow_two_hop`               | Entry route → handler → CALLS to local helper → second `HTTP_CALLS`-out → returns ≥ 2-hop chain. |
| 45 | `test_trace_request_flow_max_hops_respected`    | `max_hops=1` truncates correctly.                                      |
| 46 | `test_impact_analysis_includes_cross_service_callers` | `impact_analysis` on a method exposing `POST /chat/joinOperator` returns `cross_service_callers` ≥ 1. |
| 47 | `test_analyze_pr_surfaces_cross_service_count`  | Diff that touches a `@RestController` method increments `cross_service_callers_count` in the per-symbol risk record. |
| 48 | `test_trace_flow_follows_http_calls`            | `trace_flow` from a caller method yields a result that includes the matched `Route` and the handler `Symbol`. |

#### 5.3 New fixture: `tests/fixtures/cross_service_smoke/`

Two services (`svc-a/`, `svc-b/`) each with one controller. `svc-a`
calls `svc-b`'s endpoint via `RestTemplate`; `svc-b` calls `svc-a`'s
via `@FeignClient`. Plus a third "ambiguous" controller with the same
path in both services. Used by tests 32, 34, 37.

### 6. PR-D3 Definition of done

- [ ] `pass6_match_edges` runs after `pass5_imperative_edges`; every
      edge has a `match ∈ VALID_HTTP_CALL_MATCHES`.
- [ ] Phantom `Route` rows for resolved edges are cleaned up.
- [ ] `find_route_callers` and `trace_request_flow` MCP tools live.
- [ ] `impact_analysis`, `trace_flow`, `analyze_pr` extended (no
      removal of existing fields — additive only).
- [ ] `graph_meta.http_calls_match_breakdown` /
      `async_calls_match_breakdown` are JSON dicts;
      `cross_service_calls_total > 0` on bank-chat-system.
- [ ] `pytest` green; 17 new tests added.
- [ ] Manual evidence: rebuild bank-chat-system; PR description
      includes the `match` breakdown and a sample
      `find_route_callers` call returning the `chat-assign →
      chat-core` cross-service edge.

## PR-D3 implementation step list

| # | Step                                                                    | File(s)                  | Done when                              |
| - | ----------------------------------------------------------------------- | ------------------------ | -------------------------------------- |
| 1 | Implement `_match_call_edge` (5-outcome enum)                           | `build_ast_graph.py`     | tests 32–37 pass on cross_service_smoke |
| 2 | Implement `pass6_match_edges`; rewrite `match` + `route_id` + `confidence` | `build_ast_graph.py` | tests 39 passes                        |
| 3 | Phantom `Route` cleanup at end of `pass6`                               | `build_ast_graph.py`     | test 40 passes                         |
| 4 | Wire `pass6` into `main` after `pass5`                                  | `build_ast_graph.py`     | bank-chat-system rebuild emits all 5 outcomes |
| 5 | Add `http_calls_match_breakdown` + `async_calls_match_breakdown` + `cross_service_calls_total` to `graph_meta` | `build_ast_graph.py` | meta() returns dicts            |
| 6 | Add `find_route_callers` + `trace_request_flow` to `kuzu_queries.py`   | `kuzu_queries.py`        | tests 41–43, 44–45 pass                |
| 7 | Register MCP tools in `server.py`                                       | `server.py`              | tools listed in `mcp.tools()`          |
| 8 | Extend `impact_analysis`: `cross_service_callers` field                 | `server.py`              | test 46 passes                         |
| 9 | Extend `trace_flow`: walk includes `HTTP_CALLS` / `ASYNC_CALLS`         | `server.py`              | test 48 passes                         |
| 10 | Extend `analyze_pr`: `cross_service_callers_count` per changed symbol  | `server.py`, `pr_analysis.py` | test 47 passes                    |
| 11 | Risk-score weight bump in `pr_analysis.py`                             | `pr_analysis.py`         | docstring + unit test                  |
| 12 | Create `tests/fixtures/cross_service_smoke/`                           | `tests/fixtures/...`     | fixture files in place                 |
| 13 | Update `README.md` MCP tools section + `propose/PRODUCT-VISION.md` (`HTTP_CALLS` planned → shipped) | `README.md`, `propose/PRODUCT-VISION.md` | manual review            |

---

# Cross-PR risks (re-stated from the proposal)

| #  | Risk                                                                                       | Severity | Mitigation                                                                                  |
| -- | ------------------------------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------------------- |
| 1  | B2a's `path_regex` regression breaks B6                                                    | High     | PR-D3's tests 32, 34 round-trip through `_normalize_path` via the real `Route.path_regex`. If this regresses, tests fail. |
| 2  | `feign_name` resolution rules diverge between B2a (interface decl) and B2b (caller side)   | High     | One resolver, used by both passes (PR-D1 step 1). PR-D1 description must cite the shared helper line number after rename. |
| 3  | SpEL routes can't be matched cross-service                                                 | Medium   | Accepted — `unresolved` outcome with `confidence=confidence_base × 0.3 × micro_factor`. Ingest property files in a follow-on PR. |
| 4  | Multi-broker Kafka — same topic on different brokers wrongly merged                        | Medium   | `broker` already in `Route` join key. PR-D1 emits `broker_call`; PR-D3 matches strict-equality (no wildcard). |
| 5  | Brownfield divergence from B2a's role/route resolver                                       | High     | Same mitigation as PR-A3: PR-D2 description cites `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` line numbers. |
| 6  | Spring Cloud Gateway routes never appear, leaving phantom edges to gateway-routed services | Medium   | Out of scope (proposal §2). Documented in `README` as known gap; users add brownfield overrides. |
| 7  | `RestTemplate` URIs built via `UriComponentsBuilder` chains                                | Medium   | Deferred to v2. PR-D1 emits `unresolved`; test 8 locks the deferral. |
| 8  | Performance — `pass5` + `pass6` add two full AST walks                                     | Low      | Re-use the visitor from `pass3_calls`. Measure on bank-chat-system before merge. Acceptable budget: < 25% of `pass3_calls` runtime. |

# Out of scope (tracked elsewhere)

- **WebClient fluent chains** — proposal §11. Defer to a v2 proposal
  with proper backward-walking of the chain.
- **`UriComponentsBuilder` URI construction** — proposal §11. Defer
  to v2.
- **`StreamBridge` binding → topic resolution** — proposal §11.
  Requires Spring Cloud Stream config ingestion.
- **OpenAPI / AsyncAPI doc parsing** as a fallback resolver —
  proposal §2 explicit non-goal.
- **`find_route_callers` regex variant** — exact-match in v1, regex
  follow-up after the v1 telemetry shows we need it.
- **B7 Louvain communities**, **B8 dead code**, **B3 runtime traces**
  — separate proposals.
- **Confidence-weight validation on the real 5-service codebase** —
  follow-on action item, not a release blocker.

# Done-definition (whole plan)

1. PRs D1, D2, D3 all merged in order.
2. Ontology version `7` on bank-chat-system after rebuild.
3. `pytest` green at every commit.
4. `graph_meta` includes `http_calls_total`, `async_calls_total`,
   `http_calls_by_strategy`, `async_calls_by_strategy`,
   `http_calls_resolved_pct`, `async_calls_resolved_pct`,
   `http_clients_from_brownfield_pct`,
   `async_producers_from_brownfield_pct`,
   `http_calls_match_breakdown`, `async_calls_match_breakdown`,
   `cross_service_calls_total`.
5. New MCP tools live: `find_route_callers`, `trace_request_flow`.
6. Existing MCP tools extended (`impact_analysis`, `trace_flow`,
   `analyze_pr`).
7. `README.md` updated for caller-side edges, brownfield clients,
   match outcomes; `propose/PRODUCT-VISION.md` flips
   `HTTP_CALLS` / `ASYNC_CALLS` from *planned* to *shipped*.
8. Each PR's description quotes the relevant stats from a manual run
   on bank-chat-system as evidence.
