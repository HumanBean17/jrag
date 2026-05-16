# Manual Verification Checklist — `java-codebase-rag`

Use this **after** you've read `README.md` + `CODEBASE_REQUIREMENTS.md`,
[`docs/AGENT-GUIDE.md`](./AGENT-GUIDE.md), applied any brownfield annotations,
and built the index against your real project. The checklist mixes **shell**
checks (`java-codebase-rag` CLI for graph health and Lance tables) with **MCP**
checks (`search` / `find` / `describe` / `neighbors` / `resolve` — the MCP
navigation tools).

Each item has:

- ☐ a checkbox
- a **Verification prompt** — paste verbatim into your agent (or run the
  shell snippet yourself)
- **Expected (calibration)** — what the same prompt produces on
  `tests/bank-chat-system` (ontology **13**). If your numbers diverge wildly,
  that's a signal, not a verdict — what matters is the **shape** (proportions,
  error rates, presence of expected edges).
- **If failing → fix** — concrete next step

Calibration was captured against `tests/bank-chat-system` on
`chore/docs-sync @ 1fa1b28` (ontology version **13**): 84 files, 92 types, 474
members, 0 parse errors, 17 routes, 11 `EXPOSES`, 793 `CALLS`, 24 `OVERRIDES`,
2 `HTTP_CALLS`, 5 `ASYNC_CALLS`, 2 `Client` rows, microservices = `chat-core` + `chat-assign`.

**Convention:** Graph ops use MCP. Index health / rebuild / PR analysis use
**`java-codebase-rag`** (see README **CLI reference**). Example:

```bash
export JAVA_CODEBASE_RAG_INDEX_DIR=/tmp/verify_index
export JAVA_CODEBASE_RAG_SOURCE_ROOT=/path/to/your/project
java-codebase-rag meta --source-root "$JAVA_CODEBASE_RAG_SOURCE_ROOT" --index-dir "$JAVA_CODEBASE_RAG_INDEX_DIR"
java-codebase-rag tables --source-root "$JAVA_CODEBASE_RAG_SOURCE_ROOT" --index-dir "$JAVA_CODEBASE_RAG_INDEX_DIR"
```

---

## Pre-flight — build the graph and prepare the agent

Run **once** before working through the phases:

```bash
# 1. Build the graph against your project (verbose, deterministic)
export JAVA_CODEBASE_RAG_SOURCE_ROOT=/path/to/your/project
export JAVA_CODEBASE_RAG_INDEX_DIR=/tmp/verify_index
rm -rf "$JAVA_CODEBASE_RAG_INDEX_DIR"
mkdir -p "$JAVA_CODEBASE_RAG_INDEX_DIR"
.venv/bin/python build_ast_graph.py \
  --source-root "$JAVA_CODEBASE_RAG_SOURCE_ROOT" \
  --kuzu-path "$JAVA_CODEBASE_RAG_INDEX_DIR/code_graph.kuzu" --verbose 2>&1 | tee /tmp/verify_build.log

# 2. Read the summary lines (last ~10 lines of the log)
tail -12 /tmp/verify_build.log

# 3. Point the runtime at the index dir + Java tree (MCP: same vars in .mcp.json)
export JAVA_CODEBASE_RAG_SOURCE_ROOT=/path/to/your/project
export JAVA_CODEBASE_RAG_INDEX_DIR=/tmp/verify_index
# … then start your MCP client so it sees this server + env
```

> **Quick read of the build log.** The `[pass3]` line tells you
> call-resolution health. The `[pass4]` line is route extraction
> (`routes_resolved_pct` is the headline number — a fully Spring MVC
> service hits 95-100; Kafka-heavy services drop to 70-90 because some
> topics are SpEL `${…}`). `[pass6]` shows cross-service match results.

**Calibration on `tests/bank-chat-system` (representative build log lines):**

```
[pass1] parsed 84 files …: 92 types, 474 members, 0 parse errors …
[pass2] emitted 10 EXTENDS, 14 IMPLEMENTS, 71 INJECTS, …
[pass3] Call resolution: … (percentages vary slightly by bundle version)
[pass4] Route extraction: emitted=11, exposes=11, … routes_resolved_pct≈81.8, by_framework={'spring_mvc': 9, 'kafka': 2}
[pass5] HTTP_CALLS: 2 edges, ASYNC_CALLS: 5 edges
[pass6] http_match={'phantom': 2}, async_match={'intra_service': 1, 'phantom': 4}, cross_service_calls_total=0
```

> Note: `cross_service_calls_total=0` on the fixture is **expected** —
> the fixture is intra-service-heavy. On a real multi-service project
> this should be > 0 (otherwise see Phase 5).

---

## Phase 1 — Index health (4 items)

### 1.1 ☐ Ontology version is 13

**Verification prompt:**

> In a shell with `JAVA_CODEBASE_RAG_INDEX_DIR` and `JAVA_CODEBASE_RAG_SOURCE_ROOT`
> set for your graph, run `java-codebase-rag meta` (JSON output if piped). Report
> `ontology_version`, `built_at`, `source_root`, and `parse_errors`. Does
> `ontology_version` equal `13`?

**Expected (calibration):** `ontology_version: 13`,
`parse_errors: 0`.

**If failing → fix:** older ontology means a stale graph file. Re-pull the
repo, `git rev-parse HEAD`, then rebuild from scratch with
`rm -rf "$JAVA_CODEBASE_RAG_INDEX_DIR" && .venv/bin/python build_ast_graph.py …`
(see Phase pre-flight for the full flag pattern).

### 1.2 ☐ Parse error rate is acceptable

**Verification prompt:**

> From `java-codebase-rag meta` JSON, read `counts.files` (or equivalent) and
> `parse_errors`. Compute `parse_errors / files * 100`. If above 1%, inspect
> `/tmp/verify_build.log` for `[parse-error]` lines.

**Expected (calibration):** `0 / 84 = 0%`.

**If failing → fix:** > 5% usually means non-UTF-8 files or generated sources
you forgot to ignore. Add ignore rules, then run
`java-codebase-rag diagnose-ignore src/main/generated` (adjust path) to confirm.

### 1.3 ☐ Symbol counts match the project's rough scale

**Verification prompt:**

> From `java-codebase-rag meta`, report `counts.types`, `counts.members`,
> `counts.injects`. Compare to a rough `wc` of Java lines outside the agent.

**Expected (calibration):** 92 types from 84 files (~1.1 types/file), 474
members, 71 injects.

**If failing → fix:** types ≪ files → cross-check Phase 1.2.

### 1.4 ☐ LanceDB tables exist and MCP search works

**Verification prompt:**

> Run `java-codebase-rag tables` and confirm tables include `java` (and others you
> expect). Then call MCP `search` with
> `{"query":"main","table":"java","limit":1}`. At least one hit?

**Expected (calibration):** tables include `java`, `sql`, `yaml`; search
returns ≥1 chunk when the Lance index exists for the fixture.

**If failing → fix:** missing tables → `java-codebase-rag reprocess` (slow).
Empty `search` → check `JAVA_CODEBASE_RAG_INDEX_DIR`,
`SBERT_MODEL`, and that the index was built for this tree.

### Red flags for Phase 1

- `parse_errors / files > 5%` → ignore rules wrong
- `routes = 0` and you have controllers → see Phase 3
- `injects = 0` and you have any DI → inference broken, rebuild

---

## Phase 2 — Roles & capabilities (5 items)

MCP: use `find` with `kind="symbol"` and a `filter` object (`NodeFilter`).

### 2.1 ☐ Controllers are recognised

**Verification prompt:**

> Call `find` with
> `{"kind":"symbol","filter":{"role":"CONTROLLER"},"limit":200}`. Then call
> `search` with
> `{"query":"controller","table":"java","limit":50,"filter":{"exclude_roles":["CONTROLLER"]}}`.
> From the second result list, flag any class whose simple name ends in
> `Controller` / `Resource` / `Endpoint`.

**Expected (calibration):** 5 CONTROLLERs (`ChatIngressController`,
`JoinOperatorController`, `DevAssignmentController`,
`ChatManagementController`, `OperatorManagementController`). Zero
`*Controller` classes should appear in the second list.

**If failing → fix:** brownfield `@CodebaseRole(CONTROLLER)` or
`role_overrides.fqn` in `.java-codebase-rag.yml`. Rebuild.

### 2.2 ☐ Services and repositories are recognised

**Verification prompt:**

> Call `find` with `{"kind":"symbol","filter":{"role":"SERVICE"},"limit":200}`
> and `{"kind":"symbol","filter":{"role":"REPOSITORY"},"limit":200}`.
> Spot-check services. Call
> `find` with `{"kind":"symbol","filter":{"role":"OTHER"},"limit":100}` and
> report names ending in `Service`, `Repository`, `Dao`, or `Repo`.

**Expected (calibration):** 7 SERVICEs (incl. `ChatManagementService`,
`DistributionChunkService`, `OperatorSessionService`); REPOSITORY may be 0 in
the fixture. No `*Service` / `*Repository` in OTHER for obvious Spring types.

**If failing → fix:** brownfield overrides per 2.1.

### 2.3 ☐ Feign clients carry CLIENT + HTTP_CLIENT

**Verification prompt:**

> Call `find` with
> `{"kind":"symbol","filter":{"role":"CLIENT","capability":"HTTP_CLIENT"},"limit":50}`.
> Then `find` with
> `{"kind":"symbol","filter":{"annotation":"FeignClient"},"limit":50}`.
> On projects with `@FeignClient`, every such type should appear in the first
> list.

**Expected (calibration):** fixture uses RestTemplate-style clients, not
`@FeignClient` types; on real projects counts should align.

**If failing → fix:** confirm `java-codebase-rag meta` → `ontology_version` ≥ 10 for
`Client` nodes; full rebuild if stale.

### 2.4 ☐ Message listeners and producers are detected

**Verification prompt:**

> Call `find` with
> `{"kind":"symbol","filter":{"capability":"MESSAGE_LISTENER"},"limit":50}`
> and the same for `MESSAGE_PRODUCER`. Cross-check with
> `{"kind":"symbol","filter":{"annotation":"KafkaListener"},"limit":50}`.

**Expected (calibration):** 2 listeners (`DistributionTriggerListener`,
`ChatKafkaListener`) and 2 producers (`DistributionTriggerPublisher`,
`FollowUpKafkaPublisher`).

**If failing → fix:** custom listener annotations → meta-annotation walk
or `role_overrides.annotations` in `.java-codebase-rag.yml`.

### 2.5 ☐ OTHER role is small relative to type count

**Verification prompt:**

> Call `find` with `{"kind":"symbol","filter":{"role":"OTHER"},"limit":500}`.
> Compare count to `counts.types` from `java-codebase-rag meta`. What fraction look
> like DTOs/helpers vs missed stereotypes?

**Expected (calibration):** ~43 OTHER / 92 types in the fixture (many record
DTOs). On a well-annotated service codebase, expect lower OTHER share.

**If failing → fix:** > 60% OTHER often means an unsupported web/DI stack —
add brownfield overrides.

### Red flags for Phase 2

- `*Controller` in OTHER → JAX-RS / custom web stack
- Feign without `HTTP_CLIENT` → ontology / rebuild issue
- `MESSAGE_LISTENER` = 0 in a Kafka-heavy project → annotation walk gap

---

## Phase 3 — Routes and outbound clients (5 items)

### 3.1 ☐ Route count and framework distribution

**Verification prompt:**

> Run `java-codebase-rag meta` and report `routes_total`, `routes_by_framework`,
> `routes_resolved_pct`, `routes_from_brownfield_pct`. Then MCP `find` with
> `{"kind":"route","filter":{},"limit":500}` (narrow with `microservice` on
> large repos).

**Expected (calibration):** `routes_total=17`,
`routes_by_framework` includes `spring_mvc: 9`, `kafka: 2` (remaining rows are
less-classified Kafka/topic shapes),
`routes_resolved_pct≈81.82`, `routes_from_brownfield_pct=0.0`.

**If failing → fix:** low `routes_resolved_pct` on Spring → SpEL paths or
missing CONTROLLER classification (Phase 2.1).

### 3.2 ☐ Every controller exposes ≥1 route

**Verification prompt:**

> `find` controllers (`kind=symbol`, `role=CONTROLLER`). For one controller
> symbol `id`, call `neighbors` with
> `{"ids":"<id>","direction":"out","edge_types":["DECLARES"],"limit":50}`.
> Pick a few `route` ids from `find(kind=route)` and call `neighbors` with
> `{"ids":"<route_id>","direction":"in","edge_types":["EXPOSES"],"limit":50}` — handler symbols should appear.

**Expected (calibration):** all 5 fixture controllers participate in HTTP
routing (9 `spring_mvc` routes total across them).

**If failing → fix:** add `@CodebaseHttpRoute` / `@CodebaseAsyncRoute` per
README §3b.

### 3.3 ☐ HTTP routes have non-empty path AND method

**Verification prompt:**

> `find` with
> `{"kind":"route","filter":{"framework":"spring_mvc"},"limit":200}`. Flag
> any row where `path` or HTTP method is empty in `describe(id=…)`.

**Expected (calibration):** all 9 `spring_mvc` routes have non-empty path and
method in graph data.

**If failing → fix:** brownfield `@CodebaseHttpRoute` for ambiguous mappings.

### 3.4 ☐ Kafka topics are correct

**Verification prompt:**

> `find` with `{"kind":"route","filter":{"framework":"kafka"},"limit":200}`.
> Confirm `topic` is populated where the extractor resolved it; compare to
> config files.

**Expected (calibration):** 2 kafka-classified routes; additional Kafka-like
rows may appear with looser framework labels — treat as a shape check, not an
exact count.

**If failing → fix:** brownfield `@CodebaseAsyncRoute(topic="…")`.

### 3.5 ☐ Outbound HTTP clients surface via `find(kind="client")`

**Verification prompt:**

> `java-codebase-rag meta` → `ontology_version` and `counts.clients`. Then MCP `find`
> with `{"kind":"client","filter":{},"limit":200}`. Rows should include
> `client_kind`, `target_service`, paths, `source_layer`.

**Expected (calibration):** `ontology_version=13`, `counts.clients=2`, both
`rest_template` in the fixture.

**If failing → fix:** ontology < 10 → full rebuild. Zero clients when you
expect Feign → README §3c brownfield.

### Red flags for Phase 3

- `routes_total = 0` → Phase 2 classification / framework gap
- `routes_from_brownfield_pct` surprises after refactor → compare
  `ontology_version` to bundle expectations

---

## Phase 4 — Call graph (3 items)

### 4.1 ☐ Known method: inbound `CALLS` matches IDE usages

**Verification prompt:**

> Pick a method with known callers. Resolve its **symbol id** via `search`
> or `find` + `describe`. Call `neighbors` with
> `{"ids":"<sym_id>","direction":"in","edge_types":["CALLS"],"limit":50}`.
> Compare to IDE “Find Usages”. Optionally filter mentally by
> `attrs.confidence` (high confidence first).

**Expected (calibration):** e.g. `DistributionService#assignNext` has a small set of inbound `CALLS`; JDK-only phantom callers may differ from IDE.

**If failing → fix:** if edges missing, check phantom/low-confidence explanations in `CODEBASE_REQUIREMENTS.md` call graph section.

### 4.2 ☐ Entry route → handler → service chain

**Verification prompt:**

> `find` one `spring_mvc` route; `describe` the route id. `neighbors` with
> `{"ids":"<route_id>","direction":"in","edge_types":["EXPOSES"],"limit":50}` to get handler symbol id(s).
> Then `neighbors` on the handler with `{"ids":"<handler_sym_id>","direction":"out","edge_types":["CALLS"],"limit":50}`,
> repeat one more hop if needed. Does the chain reach service / repo code?

**Expected (calibration):** `JoinOperatorController#joinOperator` → service → repository / messaging.

**If failing → fix:** handler classified as OTHER → Phase 2.2.

### 4.3 ☐ Phantom rate is acceptable

**Verification prompt:**

> Read `[pass3]` from `/tmp/verify_build.log` and report phantom /
> unresolved percentages.

**Expected (calibration):** fixture shows substantial unresolved/phantom shares by design (cross-service-ish references). Treat as baseline, not failure.

**If failing → fix:** > 50% unresolved on a **single** service repo → indexing root / generated sources issues.

### Red flags for Phase 4

- Zero inbound `CALLS` for a hot method → wrong symbol id or confidence filtering too aggressive mentally
- Chain stops at OTHER services → Phase 2

---

## Phase 5 — Cross-service edges (3 items)

### 5.1 ☐ HTTP_CALLS metadata

**Verification prompt:**

> `java-codebase-rag meta` → report `counts.http_calls`, `http_calls_match_breakdown`,
> `edge_counts.HTTP_CALLS`. On a real project, pick a known Feign call and
> locate the consumer symbol id, then `neighbors` with
> `{"ids":"<sym_id>","direction":"out","edge_types":["HTTP_CALLS"],"limit":50}` and inspect `attrs.match`.

**Expected (calibration):** `http_calls_match_breakdown={"phantom":2}`, `edge_counts.HTTP_CALLS=2`, `cross_service_calls_total=0` on the fixture.

**If failing → fix:** expected `cross_service` but see `phantom` → target service not indexed or URL resolution; use `@CodebaseHttpClient` per README brownfield section.

### 5.2 ☐ ASYNC_CALLS producer → consumer

**Verification prompt:**

> `java-codebase-rag meta` → `async_calls_match_breakdown`, `edge_counts.ASYNC_CALLS`.
> On a real project, walk `neighbors` with explicit `ids`, `direction`, `limit`, and e.g.
> `edge_types":["ASYNC_CALLS"]` (add `HTTP_CALLS` when relevant).

**Expected (calibration):** `async_calls_match_breakdown={"intra_service":1,"phantom":4}`, 5 async edges total on the fixture.

**If failing → fix:** consumer route missing → Phase 2.4 / 3.4.

### 5.3 ☐ `cross_service_resolution` toggles behaviour

**Verification prompt:**

> On a project with real `cross_service` matches: set
> `cross_service_resolution: brownfield_only` in `.java-codebase-rag.yml`, run
> `java-codebase-rag reprocess`, re-check the same `neighbors` / meta breakdown. Edges
> should tighten to brownfield-tagged sites.

**Expected (calibration):** N/A on fixture (no cross-service matches).

**If failing → fix:** confirm `built_at` changed after rebuild (`java-codebase-rag meta`).

### Red flags for Phase 5

- `cross_service_calls_total = 0` on a true multi-service monorepo → resolver / microservice boundary configuration

---

## Phase 6 — Semantic search (2 items)

### 6.1 ☐ Concept query returns relevant chunks

**Verification prompt:**

> MCP `search` with behavioural query, e.g.
> `{"query":"operator assignment flow","table":"java","limit":8,"filter":{"exclude_roles":["DTO","ENTITY","CONFIG","OTHER"]}}`.
> Top hits should land in orchestration / controller code.

**Expected (calibration):** query about chat operator assignment surfaces `DistributionService`, `OperatorSessionService`, `JoinOperatorController`.

**If failing → fix:** add / tune `filter`; confirm Lance index matches tree.

### 6.2 ☐ Identifier query benefits from hybrid mode

**Verification prompt:**

> Run `search` twice for a distinctive class name: once `hybrid=false`, once
> `hybrid=true` (keep `table="java"`).

**Expected (calibration):** hybrid often pulls the defining file higher for identifier-like queries.

**If failing → fix:** hybrid with `table="all"` is unsupported for fusion — use `java` only.

### Red flags for Phase 6

- Zero hits on known-good query → empty Lance table or wrong `JAVA_CODEBASE_RAG_INDEX_DIR`

---

## Phase 7 — Brownfield overrides (3 items)

> Run only after you added brownfield annotations or YAML overrides.

### 7.1 ☐ `@CodebaseRole` flips the role

**Verification prompt:**

> After tagging a class, rebuild. `find` with
> `{"kind":"symbol","filter":{"role":"<X>"},"limit":500}` must include its
> FQN. Confirm via `describe` / `search`.

**If failing → fix:** typo in annotation simple name or no rebuild.

### 7.2 ☐ `@CodebaseHttpRoute` / `@CodebaseAsyncRoute` registers

**Verification prompt:**

> Rebuild, then `find` with
> `{"kind":"route","filter":{"path_prefix":"<your-prefix>","http_method":"<METHOD>"},"limit":20}`.
> `describe` the route; `neighbors` `in` / `EXPOSES` should reach your
> handler.

**If failing → fix:** path/method/topic args must match README §3b normalised forms.

### 7.3 ☐ `@CodebaseHttpClient` creates caller-side edges

**Verification prompt:**

> Rebuild. From the annotated method's symbol id, call `neighbors` e.g.
> `{"ids":"<sym_id>","direction":"out","edge_types":["HTTP_CALLS","ASYNC_CALLS"],"limit":50}` (trim edge types to what you expect). Target routes should exist on the callee service index.

**If failing → fix:** callee service must be indexed with matching `Route` rows.

### Red flags for Phase 7

- Brownfield routes/clients absent after edits → no rebuild or parse failure

---

## After completing all phases

If everything is green:

- Commit `.java-codebase-rag.yml` and `@Codebase*` stubs.
- Record **ontology 13** (or current `java-codebase-rag meta` value) in your team docs.
- Periodically diff `java-codebase-rag meta` `counts` after large refactors.

If something is red:

- Capture `java-codebase-rag meta` JSON, `/tmp/verify_build.log` tail, and the failing
  prompt.

---

## Appendix — calibration source

Reproduce fixture numbers with:

```bash
cd /path/to/java-codebase-rag
rm -rf /tmp/calib_index
.venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --kuzu-path /tmp/calib_index/code_graph.kuzu \
  --verbose
java-codebase-rag meta --source-root tests/bank-chat-system --index-dir /tmp/calib_index
```

`build_ast_graph.py` still takes `--kuzu-path` (the Kuzu file). Point it at `<index-dir>/code_graph.kuzu` so it matches the layout `java-codebase-rag meta --index-dir` expects under that directory.

Current snapshot: `tests/bank-chat-system`, `chore/docs-sync @ 1fa1b28`, ontology **13**.
