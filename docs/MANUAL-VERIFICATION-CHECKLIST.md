# Manual Verification Checklist — `java-enterprise-codebase-rag`

Use this **after** you've read `README.md` + `CODEBASE_REQUIREMENTS.md`,
applied any brownfield annotations, and built the index against your
real project. The checklist drives an MCP-aware agent (Qwen Code,
Claude Code, Cursor, …) through 7 phases of progressively deeper
verification.

Each item has:

- ☐ a checkbox
- a **Verification prompt** — paste verbatim into your agent
- **Expected (calibration)** — what the same prompt produces on
  `tests/bank-chat-system` (the in-repo fixture, ontology v9). If your
  numbers diverge wildly from the calibration column, that's a signal,
  not a verdict — your project just is bigger or smaller; what matters
  is the **shape** (proportions, error rates, presence of expected
  edges).
- **If failing → fix** — concrete next step

Calibration was captured against `tests/bank-chat-system` on
`master @ d62b48c` (post PR-H1, ontology version 9): 84 files, 92
types, 474 members, 0 parse errors, 17 routes, 793 calls, 2 HTTP_CALLS,
5 ASYNC_CALLS, microservices = `chat-core` + `chat-assign`.

---

## Pre-flight — build the graph and prepare the agent

Run **once** before working through the phases:

```bash
# 1. Build the graph against your project (verbose, deterministic)
rm -rf /tmp/verify_kuzu
python build_ast_graph.py \
  --source-root /path/to/your/project \
  --kuzu-path /tmp/verify_kuzu --verbose 2>&1 | tee /tmp/verify_build.log

# 2. Read the summary lines (last ~10 lines of the log)
tail -12 /tmp/verify_build.log

# 3. Point the MCP server at the new graph + run it from the agent of choice
export LANCEDB_MCP_PROJECT_ROOT=/path/to/your/project
export LANCEDB_MCP_KUZU_PATH=/tmp/verify_kuzu
# … then start your MCP client (Qwen Code / Claude Code) so it sees this MCP
```

> **Quick read of the build log.** The `[pass3]` line tells you
> call-resolution health. The `[pass4]` line is route extraction
> (`routes_resolved_pct` is the headline number — a fully Spring MVC
> service hits 95-100; Kafka-heavy services drop to 70-90 because some
> topics are SpEL `${…}`). `[pass6]` shows cross-service match results.

**Calibration on `tests/bank-chat-system`:**

```
[pass1] parsed 84 files in 0.24s: 92 types, 474 members, 0 parse errors, 0 skipped
[pass2] emitted 10 EXTENDS, 14 IMPLEMENTS, 71 INJECTS, 8 phantoms in 0.00s
[pass3] Call resolution: 800 sites, 77 chained phantoms (9.6%), 294 unresolved callee (36.8%), 138 phantom receiver (17.2%), …
[pass4] Route extraction: emitted=11, exposes=11, skipped_unresolved=0, routes_resolved_pct=81.8, by_framework={'spring_mvc': 9, 'kafka': 2}
[pass5] HTTP_CALLS: 2 edges, ASYNC_CALLS: 5 edges
[pass6] http_match={'phantom': 2}, async_match={'intra_service': 1, 'phantom': 4}, cross_service_calls_total=0
```

> Note: `cross_service_calls_total=0` on the fixture is **expected** —
> the fixture is intra-service-heavy. On a real multi-service project
> this should be > 0 (otherwise see Phase 5).

---

## Phase 1 — Index health (4 items)

### 1.1 ☐ Ontology version is 9

**Verification prompt:**

> Call `graph_meta()`. Report `ontology_version`, `built_at`,
> `source_root`, and `parse_errors`. Does `ontology_version` equal `9`?

**Expected (calibration):** `ontology_version: 9`,
`source_root: /home/user/workspace/user-rag/tests/bank-chat-system`,
`parse_errors: 0`.

**If failing → fix:** older ontology means you're running a stale wheel
or an old graph file. Re-pull the repo, `git rev-parse HEAD`, then
rebuild from scratch with `rm -rf /tmp/verify_kuzu && python
build_ast_graph.py …`.

### 1.2 ☐ Parse error rate is acceptable

**Verification prompt:**

> Call `graph_meta()`. Look at `counts.files` and `parse_errors`. Compute
> `parse_errors / files * 100`. If above 1%, name the most likely
> culprit by inspecting the build log (`/tmp/verify_build.log`) for
> `[parse-error]` lines.

**Expected (calibration):** `0 / 84 = 0%`.

**If failing → fix:** > 5% means tree-sitter is choking — usually
non-UTF-8 files or generated sources you forgot to ignore. Add to
`.gitignore` or to the project's `lancedb_mcp_ignore`. Re-run
`diagnose_ignore({"path":"src/main/generated"})` to confirm the rule
took effect.

### 1.3 ☐ Symbol counts match the project's rough scale

**Verification prompt:**

> Call `graph_meta()`. Report `counts.types`, `counts.members`,
> `counts.injects`. For a back-of-envelope sanity check, run
> `wc -l src/**/*.java` outside the agent and compare: types should be
> ~1 per non-trivial file.

**Expected (calibration):** 92 types from 84 files (= 1.10 types/file —
nested classes account for the slight overshoot), 474 members, 71
injects.

**If failing → fix:** types ≪ files usually means tree-sitter parser
errors swallowed type declarations. Cross-check Phase 1.2.

### 1.4 ☐ LanceDB tables exist and are readable

**Verification prompt:**

> Call `list_code_index_tables()`. Report `lancedb_uri`,
> `embedding_model`, the list of tables, and `refresh_enabled`. Then
> run `codebase_search({"query":"main","table":"java","limit":1})`.
> Did it return at least 1 hit?

**Expected (calibration):** tables include `java`, `sql`, `yaml`; the
search returns ≥1 chunk.

**If failing → fix:** missing tables → run
`refresh_code_index({"confirm":true})` (slow, requires
`LANCEDB_MCP_ALLOW_REFRESH=1`). Empty results from `codebase_search` →
the embedding model didn't load; check `SBERT_MODEL` env and disk
space.

### Red flags for Phase 1

- `parse_errors / files > 5%` → ignore rules wrong
- `routes = 0` and you have controllers → see Phase 3
- `injects = 0` and you have any DI → built-in inference broken,
  rebuild

---

## Phase 2 — Roles & capabilities (5 items)

### 2.1 ☐ Controllers are recognised

**Verification prompt:**

> Call `list_by_role({"role":"CONTROLLER","limit":200})`. Then call
> `codebase_search({"query":"controller","table":"java","limit":50,
> "exclude_roles":["CONTROLLER"]})`. From the second result list,
> identify any class whose simple name ends in `Controller` /
> `Resource` / `Endpoint`. Report each as a candidate brownfield
> override.

**Expected (calibration):** 5 CONTROLLERs (`ChatIngressController`,
`JoinOperatorController`, `DevAssignmentController`,
`ChatManagementController`, `OperatorManagementController`). Zero
`*Controller` classes appear in the second list.

**If failing → fix:** for each candidate not classified, add either
`@CodebaseRole(CodebaseRoleKind.CONTROLLER)` (README §3a) or a
`role_overrides.fqn` entry in `.lancedb-mcp.yml`. Rebuild.

### 2.2 ☐ Services and repositories are recognised

**Verification prompt:**

> Call `list_by_role({"role":"SERVICE","limit":200})` and
> `list_by_role({"role":"REPOSITORY","limit":200})`. Spot-check 3
> service results: read each via `codebase_search` to confirm they
> contain business logic (not DTOs). Then call
> `list_by_role({"role":"OTHER","limit":100})` and report any class
> whose simple name ends in `Service`, `Repository`, `Dao`, or `Repo`.

**Expected (calibration):** 7 SERVICEs (incl. `ChatManagementService`,
`DistributionChunkService`, `OperatorSessionService`); REPOSITORYs
exist in real Spring projects but the fixture has 0 due to in-memory
stubs. No `*Service` / `*Repository` classes in OTHER.

**If failing → fix:** brownfield override per 2.1.

### 2.3 ☐ Feign clients carry CLIENT + HTTP_CLIENT

**Verification prompt:**

> Call `list_by_role({"role":"CLIENT","capability":"HTTP_CLIENT","limit":50})`.
> Then call `list_by_annotation({"annotation":"FeignClient","limit":50})`.
> Every `@FeignClient`-annotated type should appear in the first list.
> Report any divergence.

**Expected (calibration):** the fixture has Feign-style call sites but
0 `@FeignClient` classes (it uses RestTemplate); on real projects,
counts should match exactly.

**If failing → fix:** as of ontology 9 (PR-H1), `@FeignClient` →
`role=CLIENT` + `capability=HTTP_CLIENT`. If you see drift, run
`graph_meta` and confirm `ontology_version=9`. If yes and still
broken, re-index — may be a stale graph.

### 2.4 ☐ Message listeners and producers are detected

**Verification prompt:**

> Call `list_by_capability({"capability":"MESSAGE_LISTENER","limit":50})`
> and `list_by_capability({"capability":"MESSAGE_PRODUCER","limit":50})`.
> Then `list_by_annotation({"annotation":"KafkaListener","limit":50})`
> and confirm all results from the annotation query also appear in the
> capability query. Repeat for `RabbitListener`, `JmsListener`, and
> `EventListener` if your project uses them.

**Expected (calibration):** 2 listeners (`DistributionTriggerListener`,
`ChatKafkaListener`) and 2 producers (`DistributionTriggerPublisher`,
`FollowUpKafkaPublisher`).

**If failing → fix:** custom listener annotations → meta-annotation
walk should pick them up automatically (Layer A). If not, add to
`role_overrides.annotations` in `.lancedb-mcp.yml` (README §"Brownfield
overrides").

### 2.5 ☐ OTHER role is small relative to type count

**Verification prompt:**

> Call `list_by_role({"role":"OTHER","limit":500})` and report the
> count. Compute `OTHER / total_types` from `graph_meta().counts.types`.
> What fraction of OTHER are obviously utility classes (exceptions,
> records, internal helpers) vs candidates the inference should have
> handled?

**Expected (calibration):** 43 OTHER out of 92 types (47%) — fixture
has many record DTOs and helper classes. On a real project this should
be < 30% if you're well-annotated; 30-50% suggests you need a few
brownfield overrides.

**If failing → fix:** > 60% OTHER almost always means a non-Spring
stack the inference doesn't know — add `role_overrides.annotations` for
your custom stereotypes.

### Red flags for Phase 2

- `*Controller` classes in `OTHER` → JAX-RS or custom web framework
  not annotated
- Feign clients without `HTTP_CLIENT` capability → ontology drift,
  rebuild
- `MESSAGE_LISTENER` count = 0 in a Kafka-heavy project → meta-walk
  failed to find your annotation

---

## Phase 3 — Routes (4 items)

### 3.1 ☐ Route count and framework distribution

**Verification prompt:**

> Call `graph_meta()` and report `routes_total`, `routes_by_framework`,
> `routes_resolved_pct`, `routes_from_brownfield_pct`. Then
> `list_routes({"limit":500})` to see them. Does the framework mix
> match what you'd expect (e.g. mostly `spring_mvc` for an HTTP
> service)?

**Expected (calibration):** `routes_total=17`,
`routes_by_framework={spring_mvc: 9, kafka: 2}` (the remaining 6 are
extracted but unframework'd Kafka topic constants),
`routes_resolved_pct=81.8`, `routes_from_brownfield_pct=0.0`.

**If failing → fix:** `routes_resolved_pct < 60` on a Spring project
means many `@RequestMapping` paths are SpEL/`${…}` (acceptable) or
your handler types weren't classified as CONTROLLER (Phase 2.1).

### 3.2 ☐ Every controller exposes ≥1 route

**Verification prompt:**

> Call `list_by_role({"role":"CONTROLLER","limit":200})`. For each
> result FQN, call `find_callees({"fqn_or_signature":"<fqn>","depth":1,
> "limit":5})` to confirm it has methods. Then call
> `list_routes({"limit":500})` and verify each controller appears
> at least once in the routes' handler set (run
> `find_route_handlers` on a sample of route ids).

**Expected (calibration):** all 5 controllers in the fixture expose
at least one HTTP route (9 routes total / 5 controllers).

**If failing → fix:** if a controller has no route, the framework
isn't recognised on its methods. Add `@CodebaseRoute` per README §3b.

### 3.3 ☐ HTTP routes have non-empty path AND method

**Verification prompt:**

> Call `list_routes({"framework":"spring_mvc","limit":200})`. Report
> any route where `path` is empty or `method` is empty. (Empty `path`
> with `framework=spring_mvc` usually means `@RequestMapping` with no
> path — programmatic routing — which is rare and worth investigating.)

**Expected (calibration):** all 9 spring_mvc routes have non-empty
`path` and `method`.

**If failing → fix:** unresolvable SpEL paths are normal in some
`@RequestMapping` forms — accept them. But if a route has
`framework=spring_mvc` and no path, it's likely a route you should
override with `@CodebaseRoute`.

### 3.4 ☐ Kafka topics are correct (topics, brokers, kinds)

**Verification prompt:**

> Call `list_routes({"framework":"kafka","limit":200})`. For each
> result, confirm: `kind=kafka_topic` and `topic` is non-empty. Cross-
> reference against your project's `application.yml` /
> `application.properties` Kafka topic names.

**Expected (calibration):** 2 kafka routes
(`ChatTopics.INCOMING`, `${assign.kafka.distribution-topic}`). The
6 unframework'd Kafka rows in 3.1 are SpEL constants the extractor
couldn't resolve — they show up but with empty framework.

**If failing → fix:** for unresolved topics that you DO know the
literal name of, use brownfield route override:
`@CodebaseRoute(framework=kafka, kind=kafka_topic, topic="my.topic")`
on the listener method (README §3b).

### Red flags for Phase 3

- `routes_total = 0` → no controllers were classified or framework not
  recognised
- HTTP routes with empty `method` → annotation extractor didn't see
  `@GetMapping` / `@PostMapping`
- `routes_from_brownfield_pct` jumped after a refactor → you broke a
  built-in extraction; check that ontology version is still 9

---

## Phase 4 — Call graph (3 items)

### 4.1 ☐ Pick a known method, verify `find_callers` matches IDE

**Verification prompt:**

> Pick one method in your project that you know has 3-5 callers (for
> example a service method called by 1-2 controllers and 1-2 other
> services). State its FQN+signature.
> Call `find_callers({"fqn_or_signature":"<fqn>#<method>(<args>)","depth":1,"min_confidence":0.9,"limit":50})`.
> Open your IDE, run "Find Usages" on the same method, and compare:
> for each IDE caller, does it appear in the MCP result? List
> mismatches.

**Expected (calibration):** any service method like
`com.bank.chat.assign.service.DistributionService#assignNext()` should
have 1-3 callers. Whether your IDE matches MCP exactly depends on:
reflection (won't show in MCP), generated code (depends on indexing
config), and JDK external code (filtered by `exclude_external`).

**If failing → fix:** if MCP misses callers your IDE finds, lower
`min_confidence` to `0.0` and retry. If still missing, the call site
was resolved as `phantom` — check your generic / reflection-heavy code
isn't dominating.

### 4.2 ☐ End-to-end chain reproduces via `find_callees`

**Verification prompt:**

> Pick one HTTP entry point. Call `list_routes({"framework":"spring_mvc","limit":1})`,
> grab the route id, then `find_route_handlers({"route_id":"<id>"})`
> to get the handler FQN. Then `find_callees` on the handler with
> `depth=2`. Does the chain reach a service method (depth 1) and then
> a repository / external call (depth 2)?

**Expected (calibration):** `JoinOperatorController#joinOperator` →
`ChatOrchestrationService#…` → repository / Kafka publisher.

**If failing → fix:** if depth-2 returns nothing, your service classes
might be classified as OTHER (back to Phase 2.2). Or `min_confidence`
is filtering legit edges — try `min_confidence=0.0`.

### 4.3 ☐ Phantom rate is acceptable

**Verification prompt:**

> Look at the `[pass3]` line in `/tmp/verify_build.log`. Report
> `chained_phantoms %`, `unresolved_callee %`, `phantom_receiver %`.

**Expected (calibration):** chained phantoms 9.6%, unresolved callee
36.8%, phantom receiver 17.2%. The fixture has many cross-service
references that legitimately resolve to phantoms (other-service types
that aren't in the same indexing root).

**If failing → fix:** > 50% unresolved on a single-service indexing
likely means you didn't include the project's library jars or generated
sources path. > 30% chained phantoms can mean overly fluent APIs the
resolver can't follow — usually accept as a known limitation.

### Red flags for Phase 4

- `find_callers` returns 0 with `min_confidence=0.0` → wrong needle
  shape (use FQN+sig, not simple name)
- depth-2 closure returns nothing on a real chain → roles wrong (Phase
  2)

---

## Phase 5 — Cross-service edges (3 items)

### 5.1 ☐ HTTP_CALLS edges exist and resolve correctly

**Verification prompt:**

> Call `graph_meta()` and report `http_calls_total`,
> `http_calls_by_strategy`, `http_calls_match_breakdown`. Then pick
> a known cross-service HTTP call site (e.g. a Feign interface method
> on service A whose target is service B). Call
> `find_route_callers({"microservice":"<B>","path_template":"<path>"})`
> and confirm A appears as a caller with `match=cross_service`.

**Expected (calibration):** `http_calls_total=2`,
`http_calls_match_breakdown={phantom: 2}` (no cross-service in the
fixture). On a real multi-service project, expect `cross_service > 0`.

**If failing → fix:** if you expected `cross_service` but got
`phantom`, the target service isn't in the same indexing root, OR the
`@FeignClient` URL doesn't resolve to a known service. Tag with
`@CodebaseClient(clientKind="feign_method", targetService="<name>",
path="…")` (README §3c).

### 5.2 ☐ ASYNC_CALLS edges connect producer → topic → listener

**Verification prompt:**

> Call `graph_meta()` and report `async_calls_total`,
> `async_calls_by_strategy`, `async_calls_match_breakdown`. Pick a
> known Kafka topic. Call
> `find_route_callers({"microservice":"<consumer-service>","path_template":""})`
> with the route id of the consumer route. Confirm the producer
> appears as a caller.

**Expected (calibration):** `async_calls_total=5`,
`async_calls_match_breakdown={intra_service: 1, phantom: 4}`. On real
projects with multi-service Kafka, expect `cross_service` matches.

**If failing → fix:** mostly `phantom` on real cross-service async
calls means the consumer side doesn't have a `Route` node for the
topic. Either the listener isn't classified (Phase 2.4) or the topic
literal couldn't be resolved (Phase 3.4).

### 5.3 ☐ `cross_service_resolution` flag flips behaviour as expected

**Verification prompt:**

> Pick one cross-service call site that resolved to `cross_service` in
> the default `auto` mode. Edit `.lancedb-mcp.yml` to add
> `cross_service_resolution: brownfield_only`. Rebuild
> (`refresh_code_index({"confirm":true})`) and re-run the same
> `find_route_callers` query. The previously-cross_service edge
> should now be `unresolved` (unless your call site is brownfield-
> tagged). Confirm.

**Expected (calibration):** N/A — fixture has 0 cross-service edges.
Use this on your real project as a smoke test of the flag.

**If failing → fix:** flag flag has no effect → you didn't actually
rebuild after editing the YAML. `graph_meta().built_at` should be a
fresh timestamp.

### Red flags for Phase 5

- `cross_service_calls_total = 0` on a multi-service project →
  resolver couldn't bind any caller to its target. Check that all
  services are under one indexing root, and check microservice
  detection (top-level dirs under `LANCEDB_MCP_PROJECT_ROOT`).

---

## Phase 6 — Semantic search (2 items)

### 6.1 ☐ Concept query returns relevant chunks

**Verification prompt:**

> Pick a behavioural concept that exists in your code (e.g.
> "operator assignment", "session lifecycle", "retry on Kafka send").
> Call `codebase_search({"query":"<concept>","limit":8,
> "exclude_roles":["DTO","ENTITY","CONFIG","OTHER"],
> "context_neighbors":1})`. The top 3 hits should be in files you'd
> naturally point at for that concept.

**Expected (calibration):** `query="how chat assigns on operator"` →
top hits include `DistributionService`, `OperatorSessionService`,
`JoinOperatorController` (the assignment chain).

**If failing → fix:** top hits are DTOs / configs → you forgot
`exclude_roles`. Top hits are unrelated → embeddings are off (check
`SBERT_MODEL` and that `refresh_code_index` actually ran on the
current code).

### 6.2 ☐ Identifier query benefits from `auto_hybrid`

**Verification prompt:**

> Pick a class your project defines (e.g. `DistributionChunkService`).
> Run two queries: with `auto_hybrid=false` (default) and with
> `auto_hybrid=true`. Report the top 3 hits from each.

**Expected (calibration):** without auto_hybrid, top results are still
relevant but ranked lower; with auto_hybrid=true, the FTS+vector RRF
pushes the exact-name file to position 1.

**If failing → fix:** auto_hybrid has no effect → `table=all` (it
requires a single table). Stick to `table=java`.

### Red flags for Phase 6

- Chunk count from `codebase_search` is 0 on a known-good query →
  LanceDB tables empty or wrong embedding model
- `graph_expand=true` returns more results than `=false` but they're
  noise → expand depth too aggressive, set to 1

---

## Phase 7 — Brownfield overrides actually applied (3 items)

> Run this phase **only after** you've explicitly added at least one
> brownfield annotation (or YAML override) to a real type in your
> project. Otherwise skip — there's nothing to verify.

### 7.1 ☐ `@CodebaseRole` on a class flips the role

**Verification prompt:**

> Pick one class where you added `@CodebaseRole(CodebaseRoleKind.X)`.
> State the FQN and the X you set. Call
> `list_by_role({"role":"X","limit":500})` and confirm the FQN appears
> in the results. Then call `find_implementors({"name":"<simple-name>"})`
> (or `codebase_search` if it's a concrete class) to confirm the
> annotation was picked up.

**Expected (calibration):** N/A — fixture has no brownfield class
annotations applied. After you add one and rebuild, this verification
should pass.

**If failing → fix:** the class doesn't appear → either the
annotation wasn't matched by simple name (typo? wrong package?), or
the build wasn't rebuilt. `graph_meta` will show
`routes_from_brownfield_pct > 0` once any brownfield is active.

### 7.2 ☐ `@CodebaseRoute` on a method registers a route

**Verification prompt:**

> Pick one method where you added
> `@CodebaseRoute(framework=…, kind=…, path="…", method="…")`. State
> the path/method. Call
> `get_route_by_path({"microservice":"<your-service>","path_template":"<path>","method":"<method>"})`.
> The route should resolve. Then `find_route_handlers({"route_id":"<id>"})`
> — your method's enclosing type should appear.

**Expected (calibration):** N/A — fixture has 0 brownfield routes.
After you add one, `graph_meta().routes_from_brownfield_pct > 0`.

**If failing → fix:** route doesn't resolve → check that the
`@CodebaseRoute` annotation has the **correct enum values** (see
README §3b — `framework` and `kind` enums are case-sensitive
lowercase). Verify `path_template` matches the normalised servlet form
(e.g. `/users/{id}` → `/users/{}`).

### 7.3 ☐ `@CodebaseClient` on a method creates an outbound HTTP_CALLS edge

**Verification prompt:**

> Pick one method where you added
> `@CodebaseClient(clientKind="rest_template", targetService="<svc>", path="…", method="…")`.
> Call `find_callees({"fqn_or_signature":"<your-method-fqn>","depth":1,"limit":20})`.
> An outbound edge to a Route node (the target service's endpoint)
> should appear. Then call `graph_meta()` and report
> `http_clients_from_brownfield_pct` (should be > 0).

**Expected (calibration):** N/A — fixture has 0 brownfield clients.

**If failing → fix:** edge doesn't appear → most common cause is the
target service / path doesn't have a `Route` node yet (the consumer
side has to be indexed too). Verify by
`get_route_by_path({"microservice":"<svc>","path_template":"<path>"})` —
if it returns nothing, index the target service alongside.

### Red flags for Phase 7

- `routes_from_brownfield_pct = 0` after adding `@CodebaseRoute` →
  build wasn't rebuilt, or annotation didn't parse (typo in enum
  value)
- Brownfield override "tightens" but doesn't override → this is
  **intended behaviour** (partial overrides are non-destructive — see
  README §"Caller-side brownfield overrides")

---

## After completing all phases

If everything is green:

- Save your `.lancedb-mcp.yml` and any `@Codebase*` annotations to
  source control. They're now part of your project's brownfield
  contract.
- Pin the ontology version (9) somewhere in your README so future devs
  know what shape of graph this MCP produces.
- Run `graph_meta` weekly (or after big refactors) and diff the
  `counts` block — surprise drops are the leading indicator of broken
  indexing.

If something is red and the "→ fix" doesn't help:

- Capture `graph_meta()` output, `/tmp/verify_build.log` last 30
  lines, and the failing prompt. File an issue against the repo with
  those three artefacts; they're enough to diagnose 90% of cases.

---

## Appendix — calibration source

All calibration numbers in this checklist come from
`tests/bank-chat-system` indexed with `master @ d62b48c` (post PR-H1
merge, ontology version 9). Reproduce with:

```bash
cd /path/to/java-enterprise-codebase-rag
rm -rf /tmp/calib_kuzu
python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --kuzu-path /tmp/calib_kuzu --verbose
```
