# Agent Guide — `java-enterprise-codebase-rag` MCP

> **How to use this file.** Copy the block between the `<!-- BEGIN/END
> user-rag MCP guide -->` markers below into your project's `QWEN.md`,
> `CLAUDE.md`, `AGENTS.md`, or equivalent. The block is self-contained:
> all 23 MCP tools, the ontology glossary (v10), a forced reasoning
> preamble, a decision tree, a recovery playbook, and slash-style prompt
> aliases. Update by re-pulling from this repo when the ontology bumps.
>
> Why this exists: weak / mid models pick the wrong tool, pass simple
> names where FQNs are required, or ask vector search for things the
> graph already knows exactly. This guide is engineered to keep them on
> the rails.
>
> Calibrated against ontology version **10** (see `ast_java.ONTOLOGY_VERSION` /
> `java_ontology.py` valid sets).

---

<!-- BEGIN user-rag MCP guide -->

## user-rag MCP — agent operating manual

This MCP indexes Java enterprise projects into two stores:

- **LanceDB** — vector + hybrid search over Java/SQL/YAML chunks, scoped
  by role / capability / module / microservice.
- **Kuzu graph** — exact symbol graph with edges `EXTENDS`, `IMPLEMENTS`,
  `INJECTS`, `DECLARES`, `CALLS`, `EXPOSES`, `HTTP_CALLS`, `ASYNC_CALLS`,
  plus `Route` nodes for inbound endpoints (HTTP, Kafka, Feign, …).

**Use this MCP when** the user asks anything that needs whole-codebase
context: "who calls X", "what handles route Y", "trace the flow when Z
happens", "what breaks if I change this", "where is concept C
implemented", "review this PR diff for blast radius".

**Do NOT use this MCP when** the answer is fully visible in the file the
user is currently editing, or when the question is about a third-party
library you can answer from training data. Prefer the cheapest tool that
answers the question.

### Forced reasoning preamble (every tool call)

Before every MCP tool call, output **one short line** with this shape:

```
Q-class: <semantic | exact-symbol | route | call-graph | impact | pr | diagnostic>
Pick: <tool_name>  Why: <≤8 words>
```

Then, **before issuing the call**, sanity-check arguments against
*Argument shapes* below: arrays must be JSON arrays (not stringified),
method needles must be `pkg.Type#method(SimpleArg1,SimpleArg2)`, and
path templates must be the normalised servlet form. Most weak-model
failures here are not wrong-tool-choice but wrong-argument-shape.

Then make the tool call. If the first call returns nothing useful, do
**not** loop the same tool with random tweaks — go to **Recovery
playbook** at the bottom of this guide.

### Argument shapes — what the parser actually wants

Two classes of mistakes burn the most calls. Read this once, then refer
back when a call returns nothing or fails validation.

#### A. JSON, not stringified JSON

FastMCP / Pydantic enforce real JSON types. **Pass arrays as JSON arrays
and objects as JSON objects — never as a string containing JSON.** This
is the single most common mistake on weak models because they over-quote
defensively.

| Param                | ✅ Right                                       | ❌ Wrong (will fail or coerce poorly)                |
| -------------------- | ----------------------------------------------- | ----------------------------------------------------- |
| `exclude_roles`      | `["DTO","ENTITY","CONFIG","OTHER"]`              | `"[\"DTO\",\"ENTITY\",\"CONFIG\",\"OTHER\"]"`           |
| `edge_types`         | `["EXTENDS","IMPLEMENTS"]`                       | `"EXTENDS,IMPLEMENTS"` or `"[EXTENDS,IMPLEMENTS]"`     |
| `confirm`            | `true`                                          | `"true"`                                              |
| `limit`              | `20`                                            | `"20"`                                                |
| `min_confidence`     | `0.9`                                           | `"0.9"`                                               |
| any optional you don't want | omit the key entirely                    | `null` is OK; empty string `""` is NOT (treated as a real filter that matches nothing) |
| string enums (`role`, `framework`, `capability`, `kind`) | `"CONTROLLER"`            | `["CONTROLLER"]` (single value, not a list)            |

**One-line rule:** if the schema says `list[str]`, send `["a","b"]`. If
it says `str`, send `"a"`. Don't wrap arrays in extra quotes "to be
safe."

#### B. Method needles — FQN + signature, with simple type names

`find_callers` / `find_callees` accept three needle shapes. The signed
FQN form is the only one that's unambiguous on overloaded methods.

**The FQN format is exactly:**

```
<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,<SimpleType2>,…)
```

Key rules:

- **Simple type names only**, no package prefixes inside the parens:
  `String`, not `java.lang.String`. `List`, not `java.util.List`.
- **Generics are erased**: `List<String>` → `List`. `Map<String,Long>` → `Map`.
- **Arrays / varargs**: not formally tested in fixture; if your
  search misses, try the simple base type without `[]` first.
- **No spaces** between commas and types: `(String,String,String)`.
- **No-arg method**: trailing `()`.
- **Constructor**: methodName is `<init>`. Example:
  `com.foo.Bar#<init>(String,int)`.
- **Nested type**: dot-separated under the outer type, before the `#`:
  `com.foo.Outer.Inner#method()`.

**Examples (verbatim from `tests/bank-chat-system`):**

```
✅ com.bank.chat.assign.ChatAssignApplication#main(String)
✅ com.bank.chat.assign.config.AssignProperties.ChatCore#setBaseUrl(String)
✅ com.bank.chat.assign.integration.ChatCoreJoinClient#joinOperator(String,String,String)
✅ com.bank.chat.assign.service.OperatorSessionService#openSession(String,List)
✅ com.bank.chat.assign.ChatAssignApplication#<init>()
```

**The three needle shapes, ranked by precision:**

1. **Method FQN with signature** — unambiguous, exact match. Use
   whenever you have it.
2. **Type FQN** (e.g. `com.foo.Bar`) — fans out to ALL declared
   methods of that type via `DECLARES`. Useful for "who calls anything
   on this class."
3. **Simple method name** (e.g. `joinOperator`) — matches every method
   of that name across the codebase. May return many rows; only use
   when you don't know the type.

**Overloaded methods — the failure you actually hit.** If a class has
both `bar()` and `bar(String)` and you pass `Foo#bar()` expecting
both, you'll only get the no-arg one. To resolve:

- Don't know the signature? **Drop the parens** entirely and use just
  the simple name (`bar`) — you'll get rows for every overload, then
  pick the one(s) you want and re-query with full FQN+sig.
- Or: pass the **type FQN** (`com.foo.Foo`) which fans out via
  `DECLARES` and includes every method of every overload.
- Or: call `codebase_search({"query":"Foo bar","auto_hybrid":true,"limit":5})`
  to recover the exact stored FQN, then retry with that string.

**How to find the FQN you need:**

- From `codebase_search` results: each `CodeChunkHit` carries `fqn`
  for the enclosing symbol — copy it verbatim.
- From `list_by_role` / `list_by_annotation` / `find_implementors`:
  each `SymbolDto` has an `fqn` field for the type. Then run
  `find_callees({"fqn_or_signature":"<typeFqn>","depth":1})` to list
  its methods with their signed FQNs.
- Phantom rows (`?HashMap<>#<init>(0)`, `?RestTemplate#<init>(0)`) are
  internal placeholders for unindexed external types. **Never pass
  them as a needle** — they won't match anything.

#### C. Path templates — the normalised servlet form

`get_route_by_path` and `find_route_callers` expect `path_template` in
the form the graph stores, NOT the raw `@RequestMapping` value:

| Source code annotation               | What to pass            |
| ------------------------------------ | ----------------------- |
| `@GetMapping("/users/{id}")`         | `"/users/{id}"`         |
| `@PostMapping("/users/{id}/avatar")` | `"/users/{id}/avatar"`  |
| `@RequestMapping("/api")` + method `@GetMapping("/me")` | the **concatenated** template `"/api/me"` |
| SpEL only: `@GetMapping("${app.endpoint}")` | empty string — use `list_routes` with `path_prefix` instead |

If unsure, run `list_routes({"path_prefix":"/users"})` first and copy
the `path` field from a result.

### Decision tree — pick the first tool

| User asks…                                                       | First tool                                          | Typical follow-up                              |
| ---------------------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------- |
| "How does X work" / "where is concept Y" (natural language)      | `codebase_search`                                   | `find_callers` on the top hit's FQN            |
| "What happens when <event> in <feature>" (end-to-end behaviour)  | `trace_flow`                                        | `find_callees` on stage-1 symbols              |
| "Who calls method/class M"                                       | `find_callers` (FQN preferred)                      | Widen with `depth`, narrow with `microservice` |
| "What does method M call"                                        | `find_callees`                                      | `graph_neighbors` for type wiring              |
| "Show me the handler for HTTP path /foo/bar"                     | `get_route_by_path` then `find_route_handlers`      | `trace_request_flow`                           |
| "List all HTTP endpoints / Kafka topics"                         | `list_routes` (filter by `framework`)               | `find_route_handlers` per id                   |
| "List outbound HTTP clients / Feign methods for this service"   | `list_clients` (filter by `client_kind`, `target_service`, `path_prefix`) | `find_route_callers` for resolved call edges   |
| "Who calls route /foo/bar"                                       | `find_route_callers`                                | `trace_request_flow`                           |
| "All controllers / services / repositories in service X"         | `list_by_role`                                      | `list_by_role` + `capability=` filter          |
| "Everything annotated `@Transactional`"                          | `list_by_annotation`                                | `find_callers` per result                      |
| "Everything that produces / listens to messages"                 | `list_by_capability` (`MESSAGE_PRODUCER` / `_LISTENER`) | `find_callees`                              |
| "Who implements this interface"                                  | `find_implementors`                                 | `find_callers` on each impl                    |
| "Who extends this class"                                         | `find_subclasses`                                   | `impact_analysis`                              |
| "Where is X injected"                                            | `find_injectors`                                    | `find_callers`                                 |
| "What breaks if I change this type"                              | `impact_analysis`                                   | `analyze_pr` if there's a diff                 |
| "Review this PR / diff"                                          | `analyze_pr` (paste the unified diff)               | `find_route_callers` on touched routes         |
| "Why is path X ignored / not indexed"                            | `diagnose_ignore`                                   | —                                              |
| "Is the index healthy / what version / how big"                  | `graph_meta`                                        | `list_code_index_tables`                       |
| "Rebuild the index" (slow, requires confirm)                     | `refresh_code_index`                                | `graph_meta` to verify                         |

**Two rules of thumb:**

1. **Graph beats vector for exact questions.** "Who calls `Foo#bar()`"
   is a graph question — never use `codebase_search` for that.
2. **Vector beats graph for fuzzy questions.** "How does authentication
   work" should start with `codebase_search` (or `trace_flow`); the
   graph alone won't surface the right entry point.

### Tool reference — all 23 tools

Grouped by purpose. Required arguments are **bold**; common mistakes are
flagged with ⚠.

#### Search (LanceDB)

##### `codebase_search` — vector / hybrid search over Java / SQL / YAML chunks

- **Args:** **`query`** (string, natural language or identifier).
  Useful optionals: `table` (`java`|`sql`|`yaml`|`all`, default `java`),
  `limit` (1-50, default 5), `role`, `exclude_roles`, `capability`,
  `module`, `microservice`, `package_prefix`, `auto_hybrid` (set true
  for identifier-ish queries like `DistributionChunkService`),
  `graph_expand` (BFS through Kuzu after top-k), `context_neighbors`
  (attach 1-2 adjacent chunks for context).
- ⚠ For behavioural questions, set
  `exclude_roles=["DTO","ENTITY","CONFIG","OTHER"]` — DTOs and entities
  are noisy and rarely the answer.
- ⚠ `hybrid=true` and `auto_hybrid=true` require a single `table` (not
  `all`).
- **Example:** `{"query":"how chat assigns on operator","exclude_roles":["DTO","ENTITY","CONFIG","OTHER"],"limit":8}`

##### `list_code_index_tables` — index health summary

- **Args:** none.
- Returns LanceDB URI, embedding model, project root, refresh-allowed
  flag, graph metadata (use `graph_meta` for just the graph side).

#### Symbols (Kuzu graph — type wiring)

##### `find_implementors` — classes implementing an interface

- **Args:** **`name`** (interface simple name or FQN). Optionals:
  `module`, `microservice`, `capability`, `limit`.
- ⚠ Pass simple name (`PaymentService`) **or** FQN
  (`com.acme.PaymentService`) — both work via the simple-name index.

##### `find_subclasses` — classes / interfaces extending a given type

- **Args:** **`name`**. Same optionals as `find_implementors`.

##### `find_injectors` — types that inject (field/ctor/setter/Lombok) a given type

- **Args:** **`name`** (the type **being** injected). Optional
  `capability` filters the **consumer** (injecting class), not the
  injected type.
- Returns edges with `mechanism`, `annotation`, `field_or_param`.

##### `graph_neighbors` — generic bidirectional neighbour expansion

- **Args:** **`name`**, `depth` (1-3, default 1), `direction`
  (`out`|`in`|`both`, default `both`), `edge_types` (subset of
  `EXTENDS`, `IMPLEMENTS`, `INJECTS`).
- Use this when none of the specialised tools fit (e.g. "find
  everything one hop from `Foo` over implements + extends").

##### `impact_analysis` — reverse closure over INJECTS+IMPLEMENTS+EXTENDS

- **Args:** **`name`**, `depth` (1-4, default 2), `limit` (default 300).
- Answers "who breaks if I change this type". Also returns
  `cross_service_callers` for any route the impacted symbol exposes.

#### Routes (inbound entry points)

##### `list_routes` — list inbound `Route` nodes (HTTP, async)

- **Args:** none required. Optionals: `microservice`, `framework`
  (`spring_mvc`|`webflux`|`kafka`|`rabbitmq`|`jms`|`stream`),
  `path_prefix`, `method`, `limit`.
- ⚠ Routes with empty `framework` are ones the extractor couldn't
  classify — usually annotation-only Kafka topic constants. If you
  expected an HTTP route here, check brownfield overrides.

##### `list_clients` — list outbound `Client` nodes (Feign, imperative HTTP)

- **Args:** none required. Optionals: `microservice`, `client_kind`
  (`feign_method`|`rest_template`|`web_client`), `target_service`,
  `path_prefix`, `method`, `limit` (1–500).
- Returns rows with `path`, `path_template`, `member_fqn`, `source_layer`
  (`builtin` vs brownfield layers), and other fields from the graph. Pair
  with `find_route_callers` when you need **resolved** `HTTP_CALLS` edges,
  not just declarations.
- ⚠ Requires a graph built with `ontology_version` **10+** — check
  `graph_meta` first.

##### `find_route_handlers` — symbols that EXPOSES a Route id

- **Args:** **`route_id`** (e.g. `r:0a2bdd…`).
- ⚠ Feign declarations are outbound clients and are not represented as inbound
  routes; use `find_route_callers` / caller tooling instead.

##### `get_route_by_path` — resolve one Route by (microservice, path, method)

- **Args:** **`microservice`**, **`path_template`**, optional `method`.
- ⚠ `path_template` must be the normalised servlet form: `{` `}` placeholders
  are kept as `{}` (e.g. `/api/users/{}`). For SpEL-only routes
  (`${kafka.topic}`) `path_template` is empty — use `list_routes` with
  `path_prefix` instead.

##### `find_route_callers` — who calls a Route (HTTP_CALLS / ASYNC_CALLS)

- **Args:** either **`route_id`**, OR **`microservice`** +
  **`path_template`** + optional `method`.
- Use this for cross-service dependency questions.

##### `trace_request_flow` — inbound + outbound around one entry route

- **Args:** **`entry_route_id`**, optional `max_hops`.
- Returns: callers (HTTP/ASYNC) → handler → outbound CALLS chain. Best
  starting point for "what happens when this endpoint is hit".

#### Calls (CALLS edges between methods)

##### `find_callers` — inbound CALLS closure for a method or type

- **Args:** **`fqn_or_signature`**. Three needle shapes (see *Argument shapes §B* for the full format spec):
  - method FQN with sig (most precise): `com.foo.Bar#baz(String,int)` — simple type names only, no spaces, generics erased
  - type FQN: `com.foo.Bar` (fans out to all methods via DECLARES)
  - simple method name: `baz` (matches all overloads everywhere; useful as a recovery step)
- Optionals: `depth` (1-5, default 1), `limit`, `min_confidence` (e.g.
  `0.9` to drop low-confidence chained-receiver edges), `exclude_external`
  (default true — drops JDK / Spring / Lombok callers), `module`,
  `microservice`.
- ⚠ For "who really calls this", set `min_confidence=0.9` and
  `depth=1` first; widen if too narrow.

##### `find_callees` — outbound CALLS closure

- **Args / optionals:** same shape as `find_callers`.

#### Roles & capabilities (multi-tag axes)

##### `list_by_role` — graph symbols with a given role

- **Args:** **`role`** (one of
  `CONTROLLER|SERVICE|REPOSITORY|COMPONENT|CONFIG|ENTITY|CLIENT|MAPPER|OTHER`).
  Optionals: `module`, `microservice`, `capability` (AND-filter), `limit`.
- ⚠ Use `OTHER` to find things the inference missed — these are
  brownfield candidates.

##### `list_by_annotation` — symbols whose annotation list contains a simple name

- **Args:** **`annotation`** (simple name, e.g. `Transactional`,
  `Async`). Optionals: `module`, `microservice`, `capability`, `limit`.
- ⚠ Pass the **simple** name without `@`.

##### `list_by_capability` — symbols carrying a capability

- **Args:** **`capability`** (one of
  `MESSAGE_LISTENER|MESSAGE_PRODUCER|HTTP_CLIENT|SCHEDULED_TASK|EXCEPTION_HANDLER`).
  Optionals: `module`, `microservice`, `limit`.

#### Behavioural / cross-cutting

##### `trace_flow` — end-to-end behavioural trace from a natural-language query

- **Args:** **`query`**. Optionals: `microservice`, `module`,
  `seed_limit` (default ~5), `stage_limit` (default ~8), `depth`
  (hops-per-stage), `follow_calls` (default true).
- Picks seeds via vector search restricted to behavioural roles
  (CONTROLLER / COMPONENT / SERVICE / CLIENT + MESSAGE_LISTENER /
  SCHEDULED_TASK), then walks the graph in 3 role-ordered stages
  (entrypoints → services → integrations). Each result row carries
  `via: [{edge_type, from_fqn, hop}]` so you know **why** it's there.
- Use this for "what happens when X" questions instead of chaining 4
  separate tools.

##### `analyze_pr` — map a unified diff to indexed symbols + risk score

- **Args:** **`diff_unified`** (string, full `git diff` output).
- Returns: `changed_symbols`, `blast_radius_total`,
  `cross_service_callers`, `routes_touched`, `risk_score` (0-1),
  `risk_band`, `notes`. Binary hunks and renames are surfaced in
  `notes` and skipped for symbol mapping.

#### Index management & diagnostics

##### `graph_meta` — Kuzu metadata: counts, ontology version, build timestamp

- **Args:** none. First tool to run on a fresh index — confirms
  `ontology_version=10` and surfaces build counts.

##### `diagnose_ignore` — explain why a path is ignored

- **Args:** **`path`** (relative to project root or absolute inside
  project). Returns the layer that decided
  (`builtin_default`|`project_root`|`nested`|`gitignore`).

##### `refresh_code_index` — rebuild LanceDB chunks + Kuzu graph (slow)

- **Args:** **`confirm`** (must be `true`). Requires
  `LANCEDB_MCP_ALLOW_REFRESH=1`.
- ⚠ Always call `graph_meta` after to verify the rebuild succeeded.

### Ontology glossary (version 10)

Source of truth: `java_ontology.py`. Pass these strings verbatim
(case-sensitive).

#### Roles (`role` column on type-level Symbol nodes)

`CONTROLLER`, `SERVICE`, `REPOSITORY`, `COMPONENT`, `CONFIG`, `ENTITY`,
`CLIENT`, `MAPPER`, `DTO`, `OTHER`.

- `CLIENT` covers Feign clients (`@FeignClient`) and brownfield
  `@CodebaseRole(CLIENT)`. As of ontology 10, plain `RestTemplate`
  wrappers stay in their natural stereotype role (typically `SERVICE`)
  unless you explicitly tag them.
- `OTHER` = the inference didn't recognise the type. Treat as a
  brownfield candidate.

#### Capabilities (multi-tag, may be empty)

`MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`,
`EXCEPTION_HANDLER`.

- Capabilities are independent of role — a `@Service` can carry
  `MESSAGE_PRODUCER` + `MESSAGE_LISTENER` simultaneously.
- `HTTP_CLIENT` fires for `@FeignClient` types and brownfield
  `@CodebaseCapability(HTTP_CLIENT)`. RestTemplate-only wrappers do not
  auto-promote.
- Capabilities are derived at the **type level**: method-level evidence
  is aggregated up to the enclosing type.

#### Route framework (on `Route` nodes)

`spring_mvc`, `webflux`, `kafka`, `rabbitmq`, `jms`, `stream`.

#### Route kind

`http_endpoint`, `kafka_topic`, `rabbit_queue`,
`jms_destination`, `stream_binding`.

#### Client node kind (`Client` rows / `list_clients`)

`feign_method`, `rest_template`, `web_client` (`VALID_CLIENT_KINDS` in
`java_ontology.py`).

#### Client kind (on `HTTP_CALLS` / `ASYNC_CALLS` edges)

`feign_method`, `rest_template`, `web_client`, `kafka_send`,
`stream_bridge_send`.

#### Call match (resolution outcome on cross-service edges)

`cross_service`, `intra_service`, `ambiguous`, `phantom`, `unresolved`.

- `phantom` = the called type is referenced by name but has no Symbol
  row (external library or unindexed code). Common and not always a
  bug.
- `cross_service` = caller and callee are in different microservices
  and the resolver had enough information to bind them. Goal is to
  maximise this for legitimate inter-service calls.

### Recovery playbook — when results look wrong

| Symptom                                                                  | Likely cause                                                                                             | Fix                                                                                                   |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `find_callers`/`find_callees` returns 0 rows                             | Wrong needle shape: pass FQN with sig (`com.foo.Bar#baz(String,int)`), not just `baz`                    | Run `codebase_search` with the simple name to recover the FQN, then retry                             |
| `find_callers`/`find_callees` returns LESS than expected on an overloaded method | Needle was `Foo#bar()` but the overload you wanted is `Foo#bar(String)` — the resolver only matched the no-arg one | Drop the parens (`bar`) to list all overloads, then re-query with the full FQN+sig of the right one. Or pass the type FQN to fan out via DECLARES. See *Argument shapes §B*. |
| Tool returns a validation / type error mentioning a list field           | Stringified JSON: `"[\"DTO\"]"` instead of `["DTO"]`                                                       | Pass real JSON arrays. See *Argument shapes §A* table.                                                |
| `path_template` filter returns nothing                                   | Passed the raw annotation value, but the graph stores the concatenated servlet form                     | Run `list_routes({"path_prefix":"/your/prefix"})` and copy the exact `path` field, then retry         |
| Tool says "graph unavailable"                                            | Index not built or `LANCEDB_MCP_PROJECT_ROOT` not set                                                    | Run `graph_meta` to confirm; `refresh_code_index({"confirm":true})` if needed                         |
| Expected route is missing from `list_routes`                             | Framework not recognised by built-in extractor                                                           | Add `@CodebaseHttpRoute(path=…, method=…)` or `@CodebaseAsyncRoute(topic=…)` per README §3b, then `refresh_code_index` |
| `list_clients` returns no rows / errors                                  | Stale graph (ontology below 10) or no outbound clients in index                                        | Run `graph_meta`; rebuild with `refresh_code_index` if needed; tag call sites with `@CodebaseClient` per README §3c   |
| `list_by_role` shows a `*Controller` class as `OTHER`                    | Non-Spring web stack (JAX-RS, custom)                                                                    | Add `@CodebaseRole(CodebaseRoleKind.CONTROLLER)` per README §3a, or `role_overrides.fqn` in YAML      |
| `cross_service_calls_total = 0` but you know there are inter-service calls | Resolution mode is `brownfield_only` and call sites have no brownfield tag, OR target services unindexed | Switch to `cross_service_resolution: auto` in YAML, or tag with `@CodebaseClient`                     |
| `codebase_search` returns DTOs / config classes instead of behaviour     | Default ranking; no role filter                                                                          | Add `exclude_roles=["DTO","ENTITY","CONFIG","OTHER"]`                                                 |
| Identifier search returns junk                                           | Pure vector lookup is fuzzy on identifiers                                                               | Set `auto_hybrid=true` (FTS + vector RRF)                                                             |
| Same query returns different results across runs                         | None — graph build is deterministic                                                                      | If you actually see this, file a bug with `graph_meta` `built_at` from both runs                     |

If two consecutive recovery attempts on the same intent fail, **stop
and report** the failure to the user with the tool name, the args you
tried, and what you got back. Do not loop further.

### Slash-style aliases (prompt templates, not real commands)

Paste these into your prompt to nudge a weak model. They are just
shorthand for the right tool + args.

- `/who-calls <fqn-with-sig>` → `find_callers({"fqn_or_signature":"<fqn>","depth":1,"min_confidence":0.9})`. **Pass the full signed FQN** (e.g. `com.foo.Bar#baz(String,int)`) — see *Argument shapes §B* for format. If you only have the simple name, query that first and re-issue with the exact FQN.
- `/calls-from <fqn-with-sig>` → `find_callees({"fqn_or_signature":"<fqn>","depth":1})`. Same FQN-with-signature rule — simple name will match all overloads but not let you target one.
- `/route <method> <path> [microservice]` → `list_routes({"path_prefix":"<path>","method":"<method>","microservice":"<ms>"})`
- `/clients [microservice]` → `list_clients({"microservice":"<ms>","limit":100})` — add `client_kind` / `path_prefix` when narrowing Feign vs imperative HTTP
- `/handler <route_id>` → `find_route_handlers({"route_id":"<route_id>"})`
- `/who-hits <microservice> <path>` → `find_route_callers({"microservice":"<ms>","path_template":"<path>"})`
- `/why-no-route <fqn>` → 1) `list_by_role({"role":"OTHER"})` to confirm the type wasn't classified, 2) `list_by_annotation` for any custom annotation, 3) suggest brownfield `@CodebaseHttpRoute` / `@CodebaseAsyncRoute`
- `/role-of <name>` → `find_implementors({"name":"<name>"})` if it's an interface; `list_by_role({"role":"…"})` to scan
- `/impact <fqn>` → `impact_analysis({"name":"<fqn>","depth":2})`
- `/cross-service <fqn>` → 1) `impact_analysis`, 2) inspect `cross_service_callers`, 3) `find_route_callers` per route
- `/flow <natural language>` → `trace_flow({"query":"<nl>","seed_limit":5,"stage_limit":8})`
- `/diff-risk <unified diff>` → `analyze_pr({"diff_unified":"<diff>"})`
- `/health` → `graph_meta()` then `list_code_index_tables()`

### One-liner: the canonical workflow for "explain feature X"

1. `trace_flow({"query":"<X>","seed_limit":5})` — get the role-ordered chain.
2. For each stage symbol whose hop is interesting: `find_callees` (depth 1) to fan out, `find_callers` (depth 1) to fan in.
3. If a `Route` shows up in stage 0: `trace_request_flow({"entry_route_id":"<id>"})` for the full inbound + outbound picture.
4. If anything looks wrong, run **Recovery playbook** before re-querying.

<!-- END user-rag MCP guide -->

---

## Maintenance notes (for the repo, not the agent)

- Bump the **ontology version** sentence at the top of the BEGIN block
  whenever `ONTOLOGY_VERSION` changes in `ast_java.py`.
- When a new MCP tool is added in `server.py`, add it to (a) the
  decision tree, (b) the tool reference, (c) a slash alias if the use
  case is common.
- The forced-reasoning preamble adds ~30 tokens per tool call. That's
  intentional cost for substantially better tool selection on weak
  models. Remove it if you're driving with Opus / GPT-5 / Sonnet 4.6
  and don't need the scaffolding.
- For the per-tool `Skills/` split (one file per tool / per workflow),
  see the follow-up plan once usage patterns shake out from real
  enterprise project use.
