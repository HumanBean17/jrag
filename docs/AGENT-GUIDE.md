# Agent Guide — `java-enterprise-codebase-rag` MCP

> **How to use this file.** Copy the block between the `<!-- BEGIN/END
> user-rag MCP guide -->` markers below into your project's `QWEN.md`,
> `CLAUDE.md`, `AGENTS.md`, or equivalent. The block is self-contained:
> all 22 MCP tools, the ontology glossary (v9), a forced reasoning
> preamble, a decision tree, a recovery playbook, and slash-style prompt
> aliases. Update by re-pulling from this repo when the ontology bumps.
>
> Why this exists: weak / mid models pick the wrong tool, pass simple
> names where FQNs are required, or ask vector search for things the
> graph already knows exactly. This guide is engineered to keep them on
> the rails.
>
> Calibrated against ontology version **9** (see `java_ontology.py`).

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

Then make the tool call. If the first call returns nothing useful, do
**not** loop the same tool with random tweaks — go to **Recovery
playbook** at the bottom of this guide.

### Decision tree — pick the first tool

| User asks…                                                       | First tool                                          | Typical follow-up                              |
| ---------------------------------------------------------------- | --------------------------------------------------- | ---------------------------------------------- |
| "How does X work" / "where is concept Y" (natural language)      | `codebase_search`                                   | `find_callers` on the top hit's FQN            |
| "What happens when <event> in <feature>" (end-to-end behaviour)  | `trace_flow`                                        | `find_callees` on stage-1 symbols              |
| "Who calls method/class M"                                       | `find_callers` (FQN preferred)                      | Widen with `depth`, narrow with `microservice` |
| "What does method M call"                                        | `find_callees`                                      | `graph_neighbors` for type wiring              |
| "Show me the handler for HTTP path /foo/bar"                     | `get_route_by_path` then `find_route_handlers`      | `trace_request_flow`                           |
| "List all HTTP endpoints / Kafka topics"                         | `list_routes` (filter by `framework`)               | `find_route_handlers` per id                   |
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

### Tool reference — all 22 tools

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

##### `list_routes` — list `Route` nodes (HTTP, Feign, Kafka, …)

- **Args:** none required. Optionals: `microservice`, `framework`
  (`spring_mvc`|`webflux`|`feign`|`kafka`|`rabbitmq`|`jms`|`stream`),
  `path_prefix`, `method`, `limit`.
- ⚠ Routes with empty `framework` are ones the extractor couldn't
  classify — usually annotation-only Kafka topic constants. If you
  expected an HTTP route here, check brownfield overrides.

##### `find_route_handlers` — symbols that EXPOSES a Route id

- **Args:** **`route_id`** (e.g. `r:0a2bdd…`).
- ⚠ Feign **consumer** routes do NOT emit `EXPOSES` and return empty —
  use `find_route_callers` instead.

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

- **Args:** **`fqn_or_signature`**. Three needle shapes:
  - method FQN with sig: `com.foo.Bar#baz(java.lang.String)`
  - type FQN: `com.foo.Bar` (fans out via DECLARES)
  - simple method name: `baz` (may return many)
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
  `ontology_version=9` and surfaces build counts.

##### `diagnose_ignore` — explain why a path is ignored

- **Args:** **`path`** (relative to project root or absolute inside
  project). Returns the layer that decided
  (`builtin_default`|`project_root`|`nested`|`gitignore`).

##### `refresh_code_index` — rebuild LanceDB chunks + Kuzu graph (slow)

- **Args:** **`confirm`** (must be `true`). Requires
  `LANCEDB_MCP_ALLOW_REFRESH=1`.
- ⚠ Always call `graph_meta` after to verify the rebuild succeeded.

### Ontology glossary (version 9)

Source of truth: `java_ontology.py`. Pass these strings verbatim
(case-sensitive).

#### Roles (`role` column on type-level Symbol nodes)

`CONTROLLER`, `SERVICE`, `REPOSITORY`, `COMPONENT`, `CONFIG`, `ENTITY`,
`CLIENT`, `MAPPER`, `DTO`, `OTHER`.

- `CLIENT` covers Feign clients (`@FeignClient`) and brownfield
  `@CodebaseRole(CLIENT)`. As of ontology 9, plain `RestTemplate`
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

`spring_mvc`, `webflux`, `feign`, `kafka`, `rabbitmq`, `jms`, `stream`.

#### Route kind

`http_endpoint`, `http_consumer`, `kafka_topic`, `rabbit_queue`,
`jms_destination`, `stream_binding`.

- `feign` framework with `http_consumer` kind = a Feign declaration
  registers an outbound contract; it does NOT expose an inbound handler
  and won't appear in `find_route_handlers`.

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
| `find_callers`/`find_callees` returns 0 rows                             | Wrong needle shape: pass FQN with sig (`com.foo.Bar#baz()`), not just `baz`                              | Run `codebase_search` with the simple name to recover the FQN, then retry                             |
| Tool says "graph unavailable"                                            | Index not built or `LANCEDB_MCP_PROJECT_ROOT` not set                                                    | Run `graph_meta` to confirm; `refresh_code_index({"confirm":true})` if needed                         |
| Expected route is missing from `list_routes`                             | Framework not recognised by built-in extractor                                                           | Add `@CodebaseRoute(framework=…, kind=…, path=…, method=…)` per README §3b, then `refresh_code_index` |
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

- `/who-calls <fqn>` → `find_callers({"fqn_or_signature":"<fqn>","depth":1,"min_confidence":0.9})`
- `/calls-from <fqn>` → `find_callees({"fqn_or_signature":"<fqn>","depth":1})`
- `/route <method> <path> [microservice]` → `list_routes({"path_prefix":"<path>","method":"<method>","microservice":"<ms>"})`
- `/handler <route_id>` → `find_route_handlers({"route_id":"<route_id>"})`
- `/who-hits <microservice> <path>` → `find_route_callers({"microservice":"<ms>","path_template":"<path>"})`
- `/why-no-route <fqn>` → 1) `list_by_role({"role":"OTHER"})` to confirm the type wasn't classified, 2) `list_by_annotation` for any custom annotation, 3) suggest brownfield `@CodebaseRoute`
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
  whenever `ONTOLOGY_VERSION` changes in `kuzu_queries.py`.
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
