# Plan: Tier 1 completion (B2a + B4 + B5)

Status: **ready to implement**. Self-contained: an agent picking this up
should be able to land it without re-deriving the design. Pairs with
[`propose/TIER1-COMPLETION-PROPOSE.md`](../propose/TIER1-COMPLETION-PROPOSE.md)
(scope, rationale, schema). The follow-on proposal
[`propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md`](../propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md)
(B2b + B6) depends on this plan landing first — **do not pre-implement
its hooks here**.

## Goal

Close out Tier 1 within the static-analysis remit:

- **B2a** — `Route` node + `EXPOSES` rel for Spring MVC / WebFlux /
  Feign / Kafka / RabbitMQ / JMS / Spring Cloud Stream **declarations
  only**. Brownfield-overridable via the same surface that exists for
  roles/capabilities.
- **B4** — `analyze_pr(diff)` MCP tool: maps a unified diff to changed
  symbols, computes blast-radius via `impact_analysis`, returns a risk
  score.
- **B5** — Layered ignore patterns: `pathspec` over project-root
  `.lancedb-mcp/ignore` + nested `.lancedb-mcp/ignore` files +
  `.gitignore` integration.

Three sub-features ship in **three independent PRs** (see §Rollout).

## Principles (do not relitigate in review)

- **Mostly additive.** No table dropped, no MCP tool removed. New nodes
  / edges / tools only.
- **Brownfield surface extends `BrownfieldOverrides` — does not parallel
  it.** The route resolver mirrors `resolve_role_and_capabilities`
  shape-for-shape. See
  [`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`](completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md)
  — **mandatory reading** before touching §PR-A3.
- **Confidence-scored edges.** Same three-strategy ladder as
  `pass3_calls`: literal=1.0, SpEL=0.85, constant_ref=0.7.
- **LanceDB untouched.** No reindex; only the Kuzu graph rebuilds.
- **Ontology bump 4 → 5** (only on B2a's PR, not on B4 or B5).
- **Microservice-aware identity.** `Route.id` includes `microservice`
  so the same path in two services is two routes — required by
  the deferred B2b/B6 join.
- **Kuzu MAP columns are STRING JSON blobs.** Kuzu's Python binder
  (0.11.x) rejects native `dict` for `MAP(STRING, INT64)` parameters
  (`STRUCT()` vs `MAP` mismatch). PR-A1 shipped `routes_by_framework`
  as a `STRING` column carrying a JSON document, with the schema
  comment `# JSON map {framework: count}; STRING avoids Kuzu Python
  MAP↔STRUCT binder mismatch.` and a decoder in `kuzu_queries.meta()`.
  **Every later PR that wants a MAP-shaped graph_meta field must
  follow this pattern** — STRING column + JSON encode on write +
  decode in `meta()`. Mentioned again in PR-A2 §2 and PR-A3 §3 so
  the implementer doesn't re-discover it.

## PR breakdown — overview

| PR        | Scope                                                            | Ontology bump | Files touched (approx) | Test buckets                      | Independent of      |
| --------- | ---------------------------------------------------------------- | ------------- | ---------------------- | --------------------------------- | ------------------- |
| **PR-A1** | B2a schema + extractor (literal-only, no brownfield, no MCP)     | 4 → 5         | 3                      | unit + integration                | —                   |
| **PR-A2** | B2a SpEL/constant-ref resolution + MCP tools                     | none          | 3                      | unit + MCP                        | PR-A1               |
| **PR-A3** | B2a brownfield (route_overrides + @CodebaseRoute + 5-layer)      | none          | 4                      | 12 brownfield fixtures            | PR-A2               |
| **PR-B**  | B4 `analyze_pr` MCP tool                                         | none          | 3                      | unit + MCP                        | PR-A1 (only edges)  |
| **PR-C**  | B5 layered ignores                                               | none          | 4                      | unit + integration                | none                |

PRs land in order **A1 → A2 → A3 → B → C**. PR-B and PR-C are also
independently mergeable after A1 if priorities shift. Each PR keeps the
test suite green at every commit.

---

# PR-A1 — B2a schema + literal extractor

**Goal:** Land the `Route` node, `EXPOSES` rel, and a `pass4_routes`
extractor that handles **literal-string** annotation arguments only.
SpEL and constant-ref handling is deferred to PR-A2; brownfield is
deferred to PR-A3. After this PR, `Route` nodes appear in the graph for
the bank-chat-system corpus.

## File-by-file changes

### 1. `ast_java.py` — Route declaration model

Additions (~40 lines, no removals):

1. New dataclass `RouteDecl`:
   ```python
   @dataclass
   class RouteDecl:
       method_fqn: str           # owning method's Symbol id
       method_sig: str           # method signature for stable Symbol lookup
       kind: str                 # 'http_endpoint' | 'http_consumer' | 'kafka_topic' | 'rabbit_queue' | 'jms_destination' | 'stream_binding'
       framework: str            # 'spring_mvc' | 'webflux' | 'feign' | 'kafka' | 'rabbitmq' | 'jms' | 'stream'
       http_method: str          # 'GET' | 'POST' | … | '' for async
       path: str                 # raw path as it appeared in source (literal only in PR-A1)
       topic: str                # async only
       broker: str               # async only — '' for default broker
       feign_name: str           # @FeignClient(name=…) — '' for non-Feign
       feign_url: str            # @FeignClient(url=…) — '' when name-based
       resolution_strategy: str  # PR-A1: always 'annotation' (literal). PR-A2 adds 'spel' / 'constant_ref'.
       confidence: float         # PR-A1: always 1.0. PR-A2 adds 0.85 / 0.7.
       resolved: bool            # PR-A1: always True for emitted routes. PR-A2 adds False for unresolved.
       filename: str
       start_line: int
       end_line: int
   ```
   Exported in `__all__`.

2. New `MethodDecl.routes: list[RouteDecl] = field(default_factory=list)`.

3. **Bump `ONTOLOGY_VERSION` from 4 to 5.** Update the comment in
   `ast_java.py` to mention "Phase 4: Route + EXPOSES (B2a)".

4. New helper `_collect_routes(method_node, type_node, src, *, …)`
   called from `_parse_method`. Reads:
   - **Type-level base path / class config:**
     - `@RequestMapping("/api/v1")` on `@Controller` / `@RestController`.
     - `@FeignClient(name=…, url=…, path=…)` on the interface.
     - `@KafkaListener(topics=…)` at class level (rare).
     - `@RabbitListener(queues=…)` at class level.
   - **Method-level mapping:**
     - `@RequestMapping`, `@GetMapping`, `@PostMapping`,
       `@PutMapping`, `@DeleteMapping`, `@PatchMapping`.
     - WebFlux equivalents (same annotations; framework differs only by
       enclosing class — see #5 below).
     - `@KafkaListener`, `@RabbitListener`, `@JmsListener`,
       `@StreamListener`, Spring Cloud Stream `@Bean Function/Consumer/Supplier`.
   - **Path composition:** `class_base + method_path`, normalized via
     `posixpath.normpath`. `value` / `path` arrays produce one
     `RouteDecl` per element.
   - **PR-A1 scope:** literal-string arguments only. If an argument is a
     SpEL expression (`${…}`) or a constant reference (e.g.
     `Endpoints.USERS`), **skip the route** and increment a counter
     `routes_skipped_unresolved`. PR-A2 will pick these up.

5. Framework detection rule for WebFlux: same annotations as Spring MVC
   but the controller method's return type is `Mono<…>` / `Flux<…>` or
   the class is annotated with `@RestController` and uses reactive
   types in any signature. Use `framework='webflux'` in that case;
   otherwise `'spring_mvc'`. Document this rule next to `_collect_routes`.

6. Feign nuance: `@FeignClient` interfaces have no body, but each
   abstract method is an exposer. `_collect_routes` emits one
   `RouteDecl` per method with `kind='http_endpoint'`,
   `framework='feign'`, plus `feign_name` / `feign_url` populated from
   the interface annotation. The "exposer" semantically is the Feign
   declaration; the imperative caller side is B2b's job, not this PR's.

### 2. `java_ontology.py` — route taxonomy

Additions (~15 lines):

1. New frozensets:
   ```python
   VALID_ROUTE_FRAMEWORKS: frozenset[str] = frozenset((
       "spring_mvc", "webflux", "feign", "kafka", "rabbitmq", "jms", "stream",
   ))
   VALID_ROUTE_KINDS: frozenset[str] = frozenset((
       "http_endpoint", "http_consumer", "kafka_topic",
       "rabbit_queue", "jms_destination", "stream_binding",
   ))
   ```
2. Add both to `__all__`.

### 3. `build_ast_graph.py` — schema, extractor pass, writers

#### 3.1 Schema additions

Add after the existing `_SCHEMA_*` constants (around line 1127):

```python
_SCHEMA_ROUTE = (
    "CREATE NODE TABLE Route("
    "id STRING, kind STRING, framework STRING, "
    "method STRING, path STRING, path_template STRING, path_regex STRING, "
    "topic STRING, broker STRING, "
    "feign_name STRING, feign_url STRING, "
    "microservice STRING, module STRING, "
    "filename STRING, start_line INT64, end_line INT64, "
    "resolved BOOLEAN, "
    "PRIMARY KEY(id))"
)
_SCHEMA_EXPOSES = (
    "CREATE REL TABLE EXPOSES(FROM Symbol TO Route, "
    "confidence DOUBLE, strategy STRING)"
)
```

Add both to the create-tables list and the drop-on-rebuild list.
Edge direction `(Symbol)-[:EXPOSES]->(Route)` is **locked** — do not
reverse it; it is required for the deferred B2b/B6 traversal
`(caller)-[:HTTP_CALLS]->(Route)<-[:EXPOSES]-(handler)`.

#### 3.2 New helpers

Add module-level functions in `build_ast_graph.py`:

1. **`_normalize_path(raw_path: str) -> tuple[str, str]`** — returns
   `(path_template, path_regex)`.
   - `/api/users/{id}` → `("/api/users/{}", "^/api/users/[^/]+/?$")`.
   - `/api/users/{id:\d+}` → strip the regex constraint to `{}` for the
     template; preserve the constraint in the regex
     (`^/api/users/\d+/?$`).
   - Trailing slash variants collapsed (template normalized without
     trailing slash; regex allows both via `/?$`).
   - Multi-`{}`: handle left-to-right.
   - Unit-tested in PR-A1's tests; **shared by B2a/B2b** so its output
     is the source of truth.

2. **`_route_id(framework: str, kind: str, http_method: str, path_template: str, topic: str, broker: str, microservice: str) -> str`** —
   stable hash:
   ```python
   import hashlib
   key = f"{framework}|{kind}|{http_method}|{path_template}|{topic}|{broker}|{microservice}"
   return f"r:{hashlib.sha1(key.encode()).hexdigest()[:16]}"
   ```
   Including `microservice` makes "`/api/users` in svc A" and "`/api/users`
   in svc B" two distinct routes.

3. **`_emit_route(tables: GraphTables, decl: RouteDecl, *, microservice: str, module: str)`** —
   appends `RouteRow` and `ExposesRow` to `tables`. Dedupes by `Route.id`.

#### 3.3 Dataclasses

Add to `GraphTables`:
```python
routes_rows: list[RouteRow]      = field(default_factory=list)
exposes_rows: list[ExposesRow]   = field(default_factory=list)
route_stats: RouteExtractionStats = field(default_factory=RouteExtractionStats)
```

`RouteRow` mirrors the Route node columns. `ExposesRow` carries
`(symbol_id, route_id, confidence, strategy)`. `RouteExtractionStats`:
counters per `framework`, per `kind`, plus `routes_skipped_unresolved`
(PR-A1 increments this for SpEL/const-ref; PR-A2 turns them into
unresolved Routes).

#### 3.4 New `pass4_routes` function

Runs after `pass3_calls`. Signature mirrors the existing pass:

```python
def pass4_routes(tables: GraphTables, asts: dict[str, JavaFileAst], *, verbose: bool) -> None:
    ...
```

Loop:
1. For each AST and each `MethodDecl` with `method.routes`:
   - Determine `microservice` from the file's owning module (re-use the
     existing `_microservice_for_file` / equivalent — search for how
     `pass3_calls` derives it; do **not** reinvent).
   - For each `RouteDecl`:
     - `path_template, path_regex = _normalize_path(decl.path)` (or
       `("", "")` if `kind != 'http_endpoint'`).
     - `route_id = _route_id(...)`.
     - Append a `RouteRow` (dedup by `id`).
     - Append an `ExposesRow(method_symbol_id, route_id, decl.confidence, decl.resolution_strategy)`.
   - Update `tables.route_stats`.

The pass does **not** read or mutate `tables.calls_rows`.

#### 3.5 Writers

Add a writer block after the existing CALLS writer:
- Insert `Route` rows; idempotent on `id`.
- Insert `EXPOSES` rows; dedup by `(from, to)` since one method emits
  one logical edge per route (Feign array-method case is already one
  per `RouteDecl`).

#### 3.6 graph_meta extension

Add to the `graph_meta` MERGE call (around line 1343):
```python
"routes_total INT64, "
"exposes_total INT64, "
"routes_by_framework STRING, "   # JSON blob: {framework: count}; see note below
"routes_resolved_pct DOUBLE, "
```
Populate from `tables.route_stats`. **`routes_by_framework` is a
`STRING` column, not `MAP(STRING, INT64)`** — Kuzu's Python binder
(0.11.x) rejects `dict` for MAP parameters with a `STRUCT()` vs
`MAP` error. Encode with `json.dumps(...)` on write; decode with
`json.loads(...)` in `kuzu_queries.meta()` so consumers still see a
`dict`. Add an inline schema comment recording the reason. PR-A2
and PR-A3 follow the same pattern for any new MAP-shaped field.

#### 3.7 CLI wire-up

In `main`, add `pass4_routes(tables, asts, verbose=args.verbose)`
right after `pass3_calls(...)` (line 1421).

### 4. Tests for PR-A1

#### 4.1 New test file: `tests/test_route_extraction.py`

Inline-source unit tests for `_collect_routes` and `_normalize_path`.
Required cases:

1. `@GetMapping("/users")` on a `@RestController` → one Route,
   `framework=spring_mvc`, `http_method=GET`, `path=/users`.
2. `@RequestMapping(value="/api", method=RequestMethod.POST)` →
   `http_method=POST`.
3. Class-level `@RequestMapping("/api/v1")` + method-level
   `@GetMapping("/users")` → `path=/api/v1/users`.
4. `@RequestMapping(path={"/a", "/b"})` → two Routes, same method.
5. `Mono<User> getUser()` return type with `@GetMapping` →
   `framework=webflux`.
6. `@FeignClient(name="user-svc", url="", path="/users")` interface
   with one `@GetMapping("/{id}")` method → one Route,
   `framework=feign`, `feign_name=user-svc`, `path=/users/{id}`.
7. `@KafkaListener(topics="orders")` → Route with
   `kind=kafka_topic`, `framework=kafka`, `topic=orders`,
   `http_method=''`.
8. `@KafkaListener(topics="${app.topic}")` → **no Route emitted in
   PR-A1**; `routes_skipped_unresolved` counter incremented.
9. `@GetMapping(Endpoints.USERS)` (constant ref) → no Route emitted;
   counter incremented.
10. Path normalization: `_normalize_path("/api/users/{id}/orders/{oid}")`
    → `("/api/users/{}/orders/{}", "^/api/users/[^/]+/orders/[^/]+/?$")`.
11. Path normalization with regex constraint:
    `/api/users/{id:\\d+}` → template `{}`, regex `\\d+`.

#### 4.2 New fixture: `tests/fixtures/route_extraction_smoke/`

Maven-shaped mini-project, ~6 files:
- `UserController.java` — Spring MVC, class + method mappings.
- `OrderController.java` — `@RequestMapping` array form.
- `UserClient.java` — `@FeignClient` interface, 3 methods.
- `OrderListener.java` — `@KafkaListener` class form.
- `WebFluxController.java` — `Mono`/`Flux` return types.
- `pom.xml` — multi-module shape with two services to exercise
  microservice scoping.

#### 4.3 Extensions to existing tests

`tests/test_ast_graph_build.py`:
- New: `test_routes_and_exposes_populated` on bank-chat-system →
  `count(Route) > 0` and `count(EXPOSES) > 0`.
- Update: `test_schema_has_all_expected_tables` adds `"Route"`,
  `"EXPOSES"`.
- Update: `test_graph_meta_present_and_versioned` expects
  `ontology_version == 5`, plus `routes_total >= 0`,
  `routes_by_framework` non-empty.
- New: `test_route_id_includes_microservice` — fixture has same
  `/api/users` path in two services; assert two distinct `Route` ids.
- New: `test_exposes_edge_direction` — `(Symbol)-[:EXPOSES]->(Route)`
  succeeds; `(Route)-[:EXPOSES]->(Symbol)` returns 0 rows.

### 5. PR-A1 Definition of done

- [ ] `pytest` green; new + regression.
- [ ] `build_ast_graph.py --source-root tests/bank-chat-system` produces
      a graph with `ontology_version=5` and non-empty `Route` /
      `EXPOSES` tables.
- [ ] `graph_meta.routes_by_framework` shows at least `spring_mvc` for
      bank-chat-system.
- [ ] No SpEL/const-ref routes in the graph yet (those land in PR-A2).
- [ ] PR description quotes the `RouteExtractionStats` from a manual
      run on bank-chat-system.

## PR-A1 implementation step list

| #  | Step                                                             | File(s)                  | Done when                                          |
| -- | ---------------------------------------------------------------- | ------------------------ | -------------------------------------------------- |
| 1  | Add `VALID_ROUTE_FRAMEWORKS` / `VALID_ROUTE_KINDS`               | `java_ontology.py`       | imported successfully                              |
| 2  | Add `RouteDecl` dataclass + bump `ONTOLOGY_VERSION` 4→5           | `ast_java.py`            | `ONTOLOGY_VERSION == 5`                            |
| 3  | Implement `_collect_routes` (literal-only)                        | `ast_java.py`            | unit cases 1–7 pass                                |
| 4  | Implement skip-and-count for SpEL / constant_ref                 | `ast_java.py`            | unit cases 8, 9 pass                               |
| 5  | Implement `_normalize_path` + `_route_id` helpers                 | `build_ast_graph.py`     | unit cases 10, 11 pass                             |
| 6  | Add `RouteRow` / `ExposesRow` / `RouteExtractionStats`            | `build_ast_graph.py`     | imports clean                                      |
| 7  | Implement `pass4_routes` and wire after `pass3_calls`             | `build_ast_graph.py`     | rebuild populates `routes_rows`                    |
| 8  | Add `_SCHEMA_ROUTE` / `_SCHEMA_EXPOSES`; create + drop wired      | `build_ast_graph.py`     | rebuild succeeds                                   |
| 9  | Writers + `graph_meta` extension                                  | `build_ast_graph.py`     | `graph_meta.routes_total > 0` on smoke corpus      |
| 10 | New fixture project                                               | `tests/fixtures/route_extraction_smoke/` | files exist                          |
| 11 | New + extended tests                                              | `tests/`                 | `pytest` green                                     |

---

# PR-A2 — B2a SpEL/constant-ref + MCP tools

**Goal:** Turn the `routes_skipped_unresolved` counter into actual
unresolved `Route` nodes with proper confidence scoring; add the
read-only MCP tools that consume the route graph.

## File-by-file changes

### 1. `ast_java.py` — three-strategy resolution

Replace the "skip" branch in `_collect_routes` with full three-strategy
ladder:

| Strategy        | When                                              | `path` field                                           | `path_template` / `path_regex` | `confidence` | `resolved` |
| --------------- | ------------------------------------------------- | ------------------------------------------------------ | ------------------------------ | ------------ | ---------- |
| `annotation`    | literal string                                    | as-written                                             | normalized                     | `1.0`        | `True`     |
| `spel`          | `${…}` placeholder anywhere in the path           | as-written (with `${…}` retained)                      | `""` / `""`                    | `0.85`       | `False`    |
| `constant_ref`  | bare identifier or qualified ident (no `${}`)     | as-written (`Endpoints.USERS`)                         | `""` / `""`                    | `0.7`        | `False`    |

Detection: a SpEL/const-ref node in the tree-sitter AST is anything
where the annotation argument is **not** a `string_literal`. SpEL
specifically is a `string_literal` whose decoded text starts with `${`
or contains `${` (Spring runtime evaluates SpEL inside string literals
too, e.g. `@GetMapping("${app.api.base}/users")`).

For SpEL inside a literal: `decode_string_literal` then check `re.search(r"\\$\\{", text)`.

### 2. `kuzu_queries.py` — read-only helpers

Add (~80 lines):

```python
def list_routes(graph, *, microservice: str | None = None,
                framework: str | None = None,
                path_prefix: str | None = None,
                method: str | None = None,
                limit: int = 100) -> list[dict]: ...

def find_route_handlers(graph, *, route_id: str) -> list[dict]:
    """
    All `Symbol`s that EXPOSES this route. (Plural because Feign
    `feign_inherit` and class-level @RequestMapping arrays can produce
    multiple exposers.)
    """

def get_route_by_path(graph, *, microservice: str, path_template: str,
                      method: str = '') -> dict | None: ...
```

Cypher patterns (Kuzu dialect):
```cypher
MATCH (s:Symbol)-[:EXPOSES]->(r:Route)
WHERE r.framework = $fw AND r.path STARTS WITH $prefix
RETURN r, s
ORDER BY r.framework, r.path
LIMIT $limit
```

**Do not** add `find_route_callers` — that's B2b's tool. PR-A2 only
ships the read-only handler-lookup side.

### 3. `server.py` — MCP tools

Three new MCP tools:

| Tool                  | Inputs                                                   | Output                                  |
| --------------------- | -------------------------------------------------------- | --------------------------------------- |
| `list_routes`         | `microservice?`, `framework?`, `path_prefix?`, `method?`, `limit?` | List of route dicts                     |
| `find_route_handlers` | `route_id`                                               | List of `{symbol, confidence, strategy}` |
| `get_route_by_path`   | `microservice`, `path_template`, `method?`               | Single route dict or `null`             |

Update `_INSTRUCTIONS` to mention the new tools and the deferred
`find_route_callers` (note: "available after B2b ships").

### 4. Tests for PR-A2

#### 4.1 New tests in `tests/test_route_extraction.py`

12. `@GetMapping("${app.api.base}/users")` → Route with
    `strategy=spel`, `confidence=0.85`, `resolved=False`,
    `path_template==""`, `path_regex==""`.
13. `@GetMapping(Endpoints.USERS)` → `strategy=constant_ref`,
    `confidence=0.7`, `resolved=False`.
14. Mixed: `@RequestMapping("${prefix}" + Endpoints.USERS)` (string
    concat) → out of scope; document that it falls through to
    `constant_ref` since it isn't a string literal.

#### 4.2 New tests in `tests/test_kuzu_queries.py`

15. `test_list_routes_filter_by_framework` — fixture with mixed
    `spring_mvc` / `feign` returns only requested.
16. `test_find_route_handlers_feign_array` — Feign interface with 3
    methods returns 3 handlers when the type-level Route is queried.
17. `test_get_route_by_path_microservice_isolated` — fixture has
    `/api/users` in two services; lookup with svc A returns A's route,
    not B's.

#### 4.3 New tests in `tests/test_mcp_tools.py`

18. Smoke for each of the three new MCP tools (same pattern as
    existing `test_find_injectors_tool`).

### 5. PR-A2 Definition of done

- [ ] `pytest` green; new + regression.
- [ ] After rebuild on bank-chat-system, `graph_meta.routes_resolved_pct`
      reported and quoted in the PR description.
- [ ] All three new MCP tools callable through the server.
- [ ] `_INSTRUCTIONS` updated with the new tools.

## PR-A2 implementation step list

| # | Step                                                       | File(s)               | Done when                                |
| - | ---------------------------------------------------------- | --------------------- | ---------------------------------------- |
| 1 | Replace skip-branch with three-strategy resolution          | `ast_java.py`         | unit 12, 13 pass                         |
| 2 | Implement `list_routes` / `find_route_handlers` / `get_route_by_path` | `kuzu_queries.py` | unit 15, 16, 17 pass                |
| 3 | Wire MCP tools                                             | `server.py`           | unit 18 passes                           |
| 4 | Update `_INSTRUCTIONS`                                     | `server.py`           | grep finds new tool names                |
| 5 | Update `README.md` route section                           | `README.md`           | manual review                            |

---

# PR-A3 — B2a brownfield (route_overrides + @CodebaseRoute)

**Goal:** Make the route detector work on legacy codebases that use
custom (non-Spring) annotations. Mirrors the existing role/capability
brownfield system **exactly**. **Mandatory reading before this PR:**
[`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`](completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md).

## File-by-file changes

### 1. `graph_enrich.py` — extend BrownfieldOverrides + new resolver

Extend `BrownfieldOverrides` (around line 162):

```python
@dataclass(frozen=True)
class BrownfieldOverrides:
    annotation_to_role: dict[str, str]
    annotation_to_capabilities: dict[str, tuple[str, ...]]
    fqn_role: dict[str, str]
    fqn_capabilities: dict[str, tuple[str, ...]]
    # NEW for B2a:
    annotation_to_route_hint: dict[str, "RouteHint"]   # by annotation FQN
    fqn_to_route_hint: dict[str, "RouteHint"]          # by class FQN
```

Where `RouteHint` is a small frozen dataclass:
```python
@dataclass(frozen=True)
class RouteHint:
    framework: str
    kind: str
    path: str = ""
    method: str = ""
    topic: str = ""
    broker: str = ""
```

Extend `_load_brownfield_overrides` to read the new YAML keys
`route_overrides.annotations` and `route_overrides.fqn` from
`.lancedb-mcp.yml`. **Do not duplicate the file-loading code** — add
parsing branches inside the existing function.

YAML shape (mirrors `role_overrides`):
```yaml
route_overrides:
  annotations:
    "com.acme.AcmeRoute":
      framework: spring_mvc
      kind: http_endpoint
      method: GET
  fqn:
    "com.legacy.UserApi":
      framework: spring_mvc
      kind: http_endpoint
      path: "/legacy/users"
```

New resolver, shape-identical to `resolve_role_and_capabilities` (line 466):

```python
def resolve_routes_for_method(
    *,
    method_decl: MethodDecl,
    enclosing_type: TypeDecl,
    overrides: BrownfieldOverrides,
    meta_chain: dict[str, frozenset[str]] | None,
    builtin_routes: list[RouteDecl],
) -> list[RouteDecl]:
    """
    Apply 5-layer composition (last writer wins) to produce the final
    route list for a method:

    1. `builtin_routes` — what `_collect_routes` produced (Spring,
       Feign, Kafka built-ins).
    2. Layer B annotations: any annotation on the method or type whose
       FQN is in `overrides.annotation_to_route_hint`.
    3. Layer A meta-chain: any annotation that transitively meta-points
       to a built-in framework annotation (re-use
       `collect_annotation_meta_chain`).
    4. Layer C in-source: `@CodebaseRoute` on the method (or
       `@CodebaseRoutes` repeatable container).
    5. Layer B fqn: if `enclosing_type.fqn` is in
       `overrides.fqn_to_route_hint`, apply the hint to every method
       whose route list is still empty.
    """
```

The composition rule is **last writer wins per key** (`framework` /
`kind` / `path` / etc.), not "replace whole list". This mirrors
`resolve_role_and_capabilities`.

### 2. `ast_java.py` — `@CodebaseRoute` source stub support

Add detection in `_collect_routes`. After collecting built-in routes,
also collect:

- `@CodebaseRoute(framework=…, kind=…, path=…, method=…, topic=…)`
- `@CodebaseRoutes({…})` — `@Repeatable` container.

These are emitted as `RouteDecl` with `resolution_strategy='codebase_route'`
and the same confidence as their underlying form (`1.0` for literal,
`0.85` for SpEL, `0.7` for constant_ref). **They are visible to the
five-layer resolver in `graph_enrich`** — `_collect_routes` does not
itself merge layers; that's `resolve_routes_for_method`.

### 3. `build_ast_graph.py` — wire resolver into pass4_routes

Replace direct use of `method.routes` in `pass4_routes` with:

```python
final_routes = resolve_routes_for_method(
    method_decl=method,
    enclosing_type=type_decl,
    overrides=overrides,             # already loaded in pass2 / pass3
    meta_chain=meta_chain,            # already loaded in pass2
    builtin_routes=method.routes,
)
for route in final_routes:
    _emit_route(tables, route, microservice=ms, module=mod)
```

Update `RouteExtractionStats` with:
- `routes_from_brownfield_pct: float`
- `routes_by_layer: dict[str, int]` (counts of `'builtin' | 'layer_b_ann' | 'layer_a_meta' | 'layer_c_source' | 'layer_b_fqn'`)

Surface both via `graph_meta`. **`routes_by_layer` is a MAP-shaped
field — use the same `STRING` JSON-blob pattern PR-A1 used for
`routes_by_framework`** (Kuzu Python binder rejects `dict` for
`MAP(STRING, INT64)`). Encode with `json.dumps` on write, decode in
`kuzu_queries.meta()`. Re-use the legacy-row read path PR-A1 added
(`_META_LEGACY`) when extending the meta query so v5 graphs without
`routes_by_layer` still load.

### 4. Source stubs in `tests/fixtures/`

Add `@CodebaseRoute` and `@CodebaseRoutes` to the existing brownfield
fixture annotation directory (same place where `@CodebaseRole` /
`@CodebaseCapability` live).

### 5. Tests for PR-A3 (12 mandatory brownfield fixtures)

In `tests/test_brownfield_routes.py`:

19. **Layer B annotation override:** custom `@AcmeRoute` mapped via
    YAML → produces a `Route` with the configured framework/kind.
20. **Layer B fqn override:** legacy class `com.legacy.UserApi` listed
    in `route_overrides.fqn` → all its methods get routes.
21. **Layer A meta-chain:** `@AcmeRestController` is meta-annotated
    with `@RestController` → its `@GetMapping` methods produce routes
    even though the class is not directly `@RestController`.
22. **Layer C source stub:** method with `@CodebaseRoute(framework=spring_mvc, kind=http_endpoint, path="/x")`
    → Route emitted.
23. **Layer C wins over auto-detect:** method has both `@GetMapping("/a")`
    *and* `@CodebaseRoute(path="/b")` → final Route's path is `/b`,
    `resolution_strategy='codebase_route'`.
24. **`@CodebaseRoutes` repeatable:** method with two `@CodebaseRoute`
    entries via `@CodebaseRoutes({…})` → two Routes emitted.
25. **Layer B fqn wins over Layer C:** as designed in
    `resolve_role_and_capabilities` — fqn override is the *outermost*
    layer (last writer wins).
26. **Empty override file:** missing `.lancedb-mcp.yml` → no error,
    no overrides applied.
27. **Malformed override:** YAML with unknown framework value →
    rejected at load time with a clear error message that mentions the
    bad key.
28. **Brownfield doesn't affect built-ins:** vanilla `@GetMapping`
    fixture still yields the same Routes whether or not
    `route_overrides` is present.
29. **Determinism:** running twice over the same fixture produces
    byte-identical Route ids.
30. **`graph_meta.routes_from_brownfield_pct`** matches the
    fixture-counted percentage of brownfield-sourced routes.

### 6. PR-A3 Definition of done

- [ ] All 12 brownfield fixtures pass.
- [ ] PR description cites line numbers from
      `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` (Fix 1 — meta
      chain, Fix 2 — iterative closure, Fix 6 — sorted iteration) to
      prove the implementation followed the existing pattern.
- [ ] No new file-loading code in `_load_brownfield_overrides` —
      only new parsing branches.
- [ ] `graph_meta.routes_from_brownfield_pct` reported on
      bank-chat-system in PR description.
- [ ] `README.md` brownfield section extended to document
      `route_overrides` and `@CodebaseRoute`.

## PR-A3 implementation step list

| # | Step                                                          | File(s)               | Done when                                |
| - | ------------------------------------------------------------- | --------------------- | ---------------------------------------- |
| 1 | Read `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` end-to-end | (no code)            | implementer notes Fix 1, 2, 6 in PR desc |
| 2 | Add `RouteHint` + extend `BrownfieldOverrides`                | `graph_enrich.py`     | dataclass imports clean                  |
| 3 | Extend `_load_brownfield_overrides` for `route_overrides:`     | `graph_enrich.py`     | YAML parses without error                |
| 4 | Implement `resolve_routes_for_method` (5-layer)               | `graph_enrich.py`     | fixtures 19–27 pass                      |
| 5 | Add `@CodebaseRoute` / `@CodebaseRoutes` detection            | `ast_java.py`         | fixtures 22, 23, 24 pass                 |
| 6 | Wire resolver into `pass4_routes`                             | `build_ast_graph.py`  | brownfield routes appear in graph        |
| 7 | Extend stats + `graph_meta`                                   | `build_ast_graph.py`  | `routes_from_brownfield_pct` populated   |
| 8 | Add fixture annotations                                       | `tests/fixtures/`     | files exist                              |
| 9 | All 12 fixture tests                                          | `tests/test_brownfield_routes.py` | pytest green                 |
| 10 | Update `README.md`                                           | `README.md`           | manual review                            |

---

# PR-B — B4 `analyze_pr` MCP tool

**Goal:** Add `analyze_pr(diff_unified: str)` MCP tool that maps a unified
diff to changed `Symbol` ids, runs `impact_analysis` on them, returns a
risk score and human-readable summary. No graph schema changes.

## File-by-file changes

### 1. New module: `pr_analysis.py`

```python
@dataclass
class ChangedSymbol:
    symbol_id: str
    fqn: str
    kind: str          # 'method' | 'type' | 'field'
    change_type: str   # 'added' | 'removed' | 'modified'
    file: str
    hunk_lines: list[int]   # affected line numbers in the new file

@dataclass
class PrRiskReport:
    changed_symbols: list[ChangedSymbol]
    blast_radius_total: int        # sum of `impact_analysis` callers
    blast_radius_by_symbol: dict[str, int]
    cross_service_callers: int     # sum of callers in a different microservice (uses CALLS only — HTTP_CALLS arrives with B2b)
    routes_touched: list[str]       # Route.id values for any EXPOSES the changed symbols carry
    risk_score: float               # 0.0–1.0; see formula below
    risk_band: str                  # 'low' | 'medium' | 'high'
    notes: list[str]                # human-readable bullets

def parse_unified_diff(diff_text: str) -> list["DiffHunk"]: ...
def map_hunks_to_symbols(graph, hunks: list["DiffHunk"]) -> list[ChangedSymbol]: ...
def compute_risk(graph, changed: list[ChangedSymbol]) -> PrRiskReport: ...
```

Use the [`unidiff`](https://pypi.org/project/unidiff/) PyPI library
for parsing. Add it to `pyproject.toml` / `requirements.txt`.

#### 1.1 Hunk → symbol mapping

For each `(file, line_range)` hunk:
1. Find all `Symbol` rows in the graph where
   `filename = hunk.file AND start_line <= hunk_max AND end_line >= hunk_min`.
2. Symbols whose entire body is inside the hunk → `change_type='modified'`.
3. Symbols where only a few lines overlap (e.g. signature change) →
   still `'modified'`; flag in `notes`.
4. Added symbols (file is `+++ /dev/null` reverse, or symbols not in
   graph but in the new content) → `'added'`. **PR-B does not parse
   added Java content** — only graph-resident symbols are mapped. New
   symbols are reported as a count in `notes` ("3 new methods not yet
   indexed; risk underestimated").
5. Removed symbols → `'removed'`. Look up by old line numbers from the
   `---` side of the diff.

#### 1.2 Risk score formula

```
risk_score = clip(
    0.4 * normalize(blast_radius_total, 100)
  + 0.3 * normalize(cross_service_callers, 20)
  + 0.2 * (1.0 if any change is in a public interface method else 0.0)
  + 0.1 * normalize(len(routes_touched), 5),
    0, 1)

risk_band:
    < 0.3  → 'low'
    < 0.7  → 'medium'
    else   → 'high'
```

`normalize(x, ceiling) = min(x, ceiling) / ceiling`. Constants are
v1 baselines; document in code that they are intentionally simple and
expected to be tuned after real-world use.

### 2. `kuzu_queries.py` — supporting query

Add `find_symbols_in_file_range(graph, *, filename, start_line, end_line)`
that returns symbols overlapping the given range. Used by
`map_hunks_to_symbols`.

### 3. `server.py` — MCP tool wiring

```python
@mcp.tool()
def analyze_pr(diff_unified: str) -> dict:
    """
    Map a unified diff to changed symbols and report blast radius.
    Inputs: unified-diff text (e.g. `git diff master`).
    Output: PrRiskReport as a JSON-serializable dict.
    """
```

Add to `_INSTRUCTIONS`.

### 4. Tests for PR-B

#### 4.1 New file: `tests/test_pr_analysis.py`

31. `parse_unified_diff` on a single-file diff → list with one
    `DiffHunk`.
32. `parse_unified_diff` on multi-file diff → list with N hunks.
33. `map_hunks_to_symbols` on bank-chat-system: hand-crafted diff over
    `ChatManagementService.enqueue` → returns that symbol with
    `change_type='modified'`.
34. `compute_risk` on a leaf private method → `risk_band='low'`,
    `blast_radius_total <= 2`.
35. `compute_risk` on a controller method (many callers, on a route)
    → `risk_band='high'`, `routes_touched` non-empty.
36. Removed symbol: diff with a `-public void foo()` block → reported
    with `change_type='removed'`.
37. Added symbol: diff adds a new method → reported in `notes` with
    "not yet indexed".

#### 4.2 New tests in `tests/test_mcp_tools.py`

38. Smoke: `analyze_pr` over a tiny diff returns a dict with
    `risk_score`, `risk_band`, `changed_symbols`.

### 5. PR-B Definition of done

- [ ] `pytest` green.
- [ ] `analyze_pr` callable via MCP server.
- [ ] PR description includes a sample run on a real diff against
      bank-chat-system, with the resulting JSON report quoted.
- [ ] `unidiff` added to dependencies.
- [ ] `README.md` documents the tool with one example.

## PR-B implementation step list

| # | Step                                              | File(s)              | Done when                            |
| - | ------------------------------------------------- | -------------------- | ------------------------------------ |
| 1 | Add `unidiff` to dependencies                     | `pyproject.toml`     | install succeeds                     |
| 2 | Implement `parse_unified_diff`                    | `pr_analysis.py`     | tests 31, 32 pass                    |
| 3 | Implement `find_symbols_in_file_range`            | `kuzu_queries.py`    | unit query works                     |
| 4 | Implement `map_hunks_to_symbols`                  | `pr_analysis.py`     | tests 33, 36, 37 pass                |
| 5 | Implement `compute_risk`                          | `pr_analysis.py`     | tests 34, 35 pass                    |
| 6 | Wire `analyze_pr` MCP tool                        | `server.py`          | test 38 passes                       |
| 7 | Update `_INSTRUCTIONS` + `README.md`              | `server.py`, `README.md` | manual review                    |

---

# PR-C — B5 layered ignore patterns

**Goal:** Replace the single `COMMON_EXCLUDED_PATH_PATTERNS` list with a
layered ignore system: project-root `.lancedb-mcp/ignore` →
nested `.lancedb-mcp/ignore` files (innermost wins) → `.gitignore`
integration. Uses [`pathspec`](https://pypi.org/project/pathspec/).

**Behavioural compatibility:** the existing
`COMMON_EXCLUDED_PATH_PATTERNS` set becomes the **default top layer**
when no project-root ignore file exists. So projects without any
`.lancedb-mcp/ignore` see no behaviour change.

## File-by-file changes

### 1. New module: `path_filtering.py`

```python
@dataclass
class IgnoreLayer:
    root: Path                      # the directory this layer applies to
    spec: pathspec.PathSpec
    source: str                     # 'builtin_default' | 'project_root' | 'nested' | 'gitignore'

class LayeredIgnore:
    def __init__(self, project_root: Path, *, use_gitignore: bool = True): ...
    def is_ignored(self, path: Path) -> tuple[bool, IgnoreLayer | None]: ...
    def diagnose(self, path: Path) -> str: ...   # multi-line explanation; see §3
```

Resolution order (innermost wins; later overrides earlier):
1. `builtin_default` — current `COMMON_EXCLUDED_PATH_PATTERNS`.
2. `project_root` — `<project>/.lancedb-mcp/ignore`.
3. `nested` — every `<dir>/.lancedb-mcp/ignore` discovered while
   walking; for a given file, the *closest* nested ignore wins.
4. `gitignore` — if `use_gitignore` and a sibling `.gitignore` exists,
   merge it as an additional layer. **Negation patterns (`!foo`) are
   honoured.**

`is_ignored(path)` returns the **last layer that produced a match**, or
`(False, None)` if no layer matched.

### 2. `graph_enrich.py` + `java_index_flow_lancedb.py` — replace direct use

Find every call site that consumes `COMMON_EXCLUDED_PATH_PATTERNS`:

```bash
grep -n COMMON_EXCLUDED_PATH_PATTERNS *.py
```

Replace with `LayeredIgnore(project_root)` calls. Keep the legacy
constant in `path_filtering.py` for the `builtin_default` layer.

`iter_java_source_files(root, excludes)` becomes
`iter_java_source_files(root, *, ignore: LayeredIgnore)`. Old signature
deprecated; provide a compatibility shim that builds a `LayeredIgnore`
from the legacy `excludes` list for one release, with a
`DeprecationWarning`.

### 3. Diagnostics — `diagnose_ignore` MCP tool

```python
@mcp.tool()
def diagnose_ignore(path: str) -> dict:
    """
    Explain whether `path` is ignored and which layer made the decision.
    Returns:
      {
        "ignored": bool,
        "layer": "builtin_default" | "project_root" | ... | None,
        "matching_pattern": "**/*.class" | None,
        "explanation": "Excluded by .lancedb-mcp/ignore at /repo/svc-a (line 4): **/build/**"
      }
    """
```

Useful for users debugging "why is this file missing from the graph".

### 4. Tests for PR-C

#### 4.1 New file: `tests/test_path_filtering.py`

39. Builtin default: `Foo.class` is ignored when no other layer matches.
40. Project-root override: `.lancedb-mcp/ignore` containing
    `!**/Foo.class` un-ignores it.
41. Nested ignore: nested `.lancedb-mcp/ignore` further down adds
    `**/Generated*.java`; only files under that nested root are
    ignored, files in siblings are not.
42. Innermost wins: nested ignore re-includes (`!**/Generated*.java`)
    something the project-root ignored.
43. `.gitignore` integration: `.gitignore` at repo root with `**/build/`
    ignores `build/` even without `.lancedb-mcp/ignore`.
44. `.gitignore` with `use_gitignore=False` → no effect.
45. `diagnose` output for a file matched by nested layer: explanation
    cites the nested ignore file path and the pattern line number.
46. `is_ignored` on a path *outside* the project root → `False`,
    `None`.

#### 4.2 Extension to existing tests

47. `tests/test_lancedb_e2e.py` — add a fixture project with a custom
    `.lancedb-mcp/ignore` and assert the indexed file count differs
    accordingly.

#### 4.3 New MCP tool test

48. `tests/test_mcp_tools.py` — smoke for `diagnose_ignore`.

### 5. PR-C Definition of done

- [ ] `pytest` green; legacy `COMMON_EXCLUDED_PATH_PATTERNS` projects
      see zero behaviour change.
- [ ] `diagnose_ignore` MCP tool works.
- [ ] PR description includes before/after file count on a project
      that uses a `.lancedb-mcp/ignore` to exclude generated code.
- [ ] `pathspec` added to dependencies.
- [ ] `README.md` has a new "Ignore patterns" section.

## PR-C implementation step list

| # | Step                                                  | File(s)                  | Done when                              |
| - | ----------------------------------------------------- | ------------------------ | -------------------------------------- |
| 1 | Add `pathspec` dependency                             | `pyproject.toml`         | install succeeds                       |
| 2 | Implement `LayeredIgnore`                             | `path_filtering.py`      | tests 39–46 pass                       |
| 3 | Implement `diagnose_ignore` helper                    | `path_filtering.py`      | test 45 passes                         |
| 4 | Replace `COMMON_EXCLUDED_PATH_PATTERNS` call sites    | `graph_enrich.py`, `java_index_flow_lancedb.py` | grep returns only the canonical definition |
| 5 | Compatibility shim for old `iter_java_source_files`   | `path_filtering.py`      | deprecation warning emitted, tests still pass |
| 6 | Wire `diagnose_ignore` MCP tool                       | `server.py`              | test 48 passes                         |
| 7 | Extend `tests/test_lancedb_e2e.py`                    | `tests/test_lancedb_e2e.py` | test 47 passes                      |
| 8 | Update `README.md`                                    | `README.md`              | manual review                          |

---

# Cross-PR risks (re-stated from the proposal)

| #  | Risk                                                                                       | Severity | Mitigation                                                                                  |
| -- | ------------------------------------------------------------------------------------------ | -------- | ------------------------------------------------------------------------------------------- |
| 1  | `_normalize_path` regression breaks future B6 matcher                                      | High     | PR-A1 must include round-trip tests on `path_template ↔ path_regex` so B2b inherits a stable contract. |
| 2  | Brownfield divergence from role/capability resolver                                        | High     | PR-A3 implementer must cite line numbers from `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` in PR description. |
| 3  | `unidiff` library quirks with binary diffs / renames                                       | Medium   | PR-B explicitly skips binary diffs and reports renames as `notes`, not `changed_symbols`.   |
| 4  | `pathspec` deviation from gitignore semantics on edge cases                                | Medium   | PR-C uses `pathspec.GitIgnoreSpec` (not `WildMatchPattern`) for the `gitignore` layer specifically. |
| 5  | `routes_from_brownfield_pct` ambiguous when both built-in and brownfield contribute         | Low      | Define as: % of final routes whose `resolution_strategy ∈ {layer_b_ann, layer_a_meta, layer_c_source, layer_b_fqn}`. Document in code. |

# Out of scope (tracked elsewhere)

- **B2b + B6** — imperative HTTP/async edges + cross-service matcher.
  See `propose/TIER1B-HTTP-ASYNC-EDGES-PROPOSE.md`. Do not pre-implement
  any caller-side detection here.
- **Microservice-scoped CALLS resolution** — the correctness gap in
  `_lookup_method_candidates`. Orthogonal small PR; tracked in
  `propose/TIER1-COMPLETION-PROPOSE.md` §10.
- **B7 Louvain communities**, **B8 dead code**, **B3 runtime traces** —
  separate proposals.

# Done-definition (whole plan)

1. PRs A1, A2, A3, B, C all merged in order.
2. Ontology version `5` on bank-chat-system after rebuild.
3. `pytest` green at every commit.
4. `graph_meta` includes `routes_total`, `exposes_total`,
   `routes_by_framework`, `routes_resolved_pct`,
   `routes_from_brownfield_pct`.
5. New MCP tools live: `list_routes`, `find_route_handlers`,
   `get_route_by_path`, `analyze_pr`, `diagnose_ignore`.
6. `README.md` updated for routes, brownfield routes, `analyze_pr`,
   layered ignores.
7. Each PR's description quotes the relevant stats from a manual run
   on bank-chat-system as evidence.
