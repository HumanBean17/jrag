---
name: explore-codebase
description: Complete operating manual for the java-codebase-rag MCP tools (search, find, describe, neighbors, trace, resolve). Use this skill whenever you need to explore a Java codebase — locate symbols, trace call chains, find routes, walk cross-service boundaries, or answer any "where is X", "who calls Y", "what does Z depend on" question. Self-contained: includes edge taxonomy, NodeFilter reference, decision tree, argument shapes, recovery playbook, and navigation patterns. No external files needed.
---

# /explore-codebase — Codebase navigation via the java-codebase-rag MCP

## When to use

Any time you need to understand structure in an indexed Java codebase: locating symbols, tracing call chains, finding HTTP/messaging routes, walking cross-service boundaries, or answering questions like "where is X", "who calls Y", "what depends on Z".

## Tools

`search`, `find`, `describe`, `neighbors`, `trace`, `resolve`.

## Node kinds

`Symbol` (types and methods), `Route` (HTTP and messaging entry points), `Client` (outbound HTTP call sites), `Producer` (outbound async call sites).

## Indexed content

Java production sources plus SQL and YAML. Use `search` `table`: `java`, `sql`, `yaml`, or `all`.

## What this MCP is not

Test/build/CI files, git history — read directly. `CALLS` is static analysis only (lower bound). Empty results may mean missing/wrong index. When MCP disagrees with the open file, **the file wins**.

## Workflow (locate -> inspect -> walk)

1. **Locate** — `resolve` for identifier-shaped strings; `search` for natural language or code fragments; `find` for structured `NodeFilter` discovery.
2. **Inspect** — `describe(id)` for the full record and `edge_summary` (per-label `in`/`out` counts).
3. **Walk** — `neighbors` for single-hop adjacency; `trace` for multi-hop BFS with pruning (fan-out control, role-based pruning, cross-service boundaries).

## Forced reasoning preamble (every tool call)

Before each MCP call, output one short line:

```
Q-class: <semantic | structured | inspect | walk | trace>
Pick: <search|find|describe|neighbors|trace|resolve>  Why: <≤8 words>
```

Then use real JSON shapes (see below). If the call fails or returns nothing useful, use the **Recovery playbook** — do not thrash.

## Edge taxonomy

Use these strings **verbatim** in `neighbors(..., edge_types=[...])`.

### Stored edges (one hop)

| Group | Edge types | Semantics |
| ----- | ---------- | --------- |
| Type wiring | `EXTENDS`, `IMPLEMENTS`, `INJECTS` | `in` = who depends on this type; `out` = what this type depends on |
| Containment | `DECLARES`, `DECLARES_CLIENT`, `DECLARES_PRODUCER` | `in` = owner; `out` = owned member, client, or producer |
| Method overrides | `OVERRIDES` | Subtype **method** -> supertype **declaration** (same `signature`, one `IMPLEMENTS`/`EXTENDS` hop) |
| Method calls | `CALLS` | `in` = callers; `out` = callees (method Symbol -> method Symbol only) |
| Service boundary | `EXPOSES` | method Symbol -> Route (handler exposes route) |
| Cross-service | `HTTP_CALLS`, `ASYNC_CALLS` | `HTTP_CALLS`: Client -> Route; `ASYNC_CALLS`: Producer -> Route |

### Composed edges — type Symbol origin (`direction="out"` only)

| Edge type | Meaning |
| --------- | ------- |
| `DECLARES.DECLARES_CLIENT` | Members' HTTP clients in one hop |
| `DECLARES.DECLARES_PRODUCER` | Members' async producers in one hop |
| `DECLARES.EXPOSES` | Members' exposed routes in one hop |

### Composed edges — non-static method Symbol origin (`direction="out"` only)

| Edge type | Meaning |
| --------- | ------- |
| `OVERRIDDEN_BY` | Concrete overrider methods |
| `OVERRIDDEN_BY.DECLARES_CLIENT` | Clients declared on overriders |
| `OVERRIDDEN_BY.DECLARES_PRODUCER` | Producers on overriders |
| `OVERRIDDEN_BY.EXPOSES` | Routes exposed by overriders |

`neighbors(decl_id, "out", ["OVERRIDDEN_BY"])` returns the same overrider methods as `neighbors(decl_id, "in", ["OVERRIDES"])` — prefer the dot-key when `edge_summary` advertises it.

Do not mix `DECLARES.*` and `OVERRIDDEN_BY.*` in one `edge_types` list on a single origin id — the handler rejects the whole request (only one axis applies per node).

**Pagination:** default `neighbors` `limit=25` slices the merged flat + composed edge list. When `edge_summary` shows a large `out` count for a composed key, raise `limit` (and use `offset`) or issue separate calls per key.

## Argument shapes

### JSON, not stringified JSON

| Param | Right | Wrong |
| ----- | ----- | ----- |
| `edge_types` | `["CALLS"]` | `"CALLS"` or `"[\"CALLS\"]"` |
| `exclude_roles` | `["DTO","OTHER"]` | stringified array |
| `filter` | `{"role":"CONTROLLER"}` | nested string JSON |
| `ids` (batch) | `["sym:...","sym:..."]` | comma-joined string |

Omit keys you do not need. Empty string `""` is often a **real filter** that matches nothing.

### Node ids

| Kind | Prefixes |
| ---- | -------- |
| Symbol | `sym:` |
| Route | `route:` or `r:` |
| Client | `client:` or `c:` |
| Producer | `producer:` or `p:` |

Use exact ids from `search.symbol_id`, `find`, `describe`, or `neighbors.other.id`.

### Method / type identity (Symbol FQNs)

```
<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,<SimpleType2>,...)
```

Simple types in parentheses; generics erased (`List<String>` -> `List`). No spaces after commas. No-arg: `()`. Constructor: `#<init>(...)`.

### `neighbors` — required every time

- `direction`: `"in"` or `"out"` (no default).
- `edge_types`: non-empty list from the taxonomy above.

Optional `filter` applies to each **other** endpoint; populated fields must match that neighbor's kind (strict frame).

**Batching:** multiple `ids` expand first; `limit`/`offset` slice the **merged** edge list — raise `limit` when batching.

**Mixed flat + composed `edge_types`:** flat edges are listed before composed edges, then pagination applies. A small `limit` with e.g. `["DECLARES","DECLARES.DECLARES_CLIENT"]` may return only member Symbols and no Clients — use the dot-key alone to list terminals.

## Shared `NodeFilter` (`find`, `search.filter`, `neighbors.filter`)

For **`find`**, `filter` is required — `{}` means no predicates (all nodes of that kind, subject to pagination).

| Keys | Applies to |
| ---- | ---------- |
| `microservice`, `module` | All kinds |
| `role`, `exclude_roles`, `annotation`, `capability`, `fqn_prefix`, `symbol_kind`, `symbol_kinds` | **symbol** |
| `http_method`, `path_prefix`, `framework` | **route** |
| `client_kind`, `target_service`, `target_path_prefix`, `http_method` | **client** |
| `producer_kind`, `topic_prefix` | **producer** |

`http_method` filters HTTP verbs on **routes** (declared method) and on **clients** (outbound call method). Not applicable to **symbol** rows.

**Strict frame:** one populated field -> one stored attribute for that kind. Unknown keys or inapplicable populated fields -> `success=false` with a teaching `message`. No wildcards in `fqn_prefix`, `path_prefix`, or `target_path_prefix` (`*` / `?` rejected) — use `search(query=...)` for ranked text instead. `search.query` is opaque text, not a DSL.

## Identifier resolution (`resolve`)

**Input:** FQN or suffix, `sym:`/`route:`/`client:`/`producer:` id, `METHOD /path`, route path template, client `target_service`, `target_service` + path prefix, or producer topic.

**`hint_kind`:** optional `symbol` | `route` | `client` | `producer`. When omitted, generators run across **all four** kinds (narrow with `hint_kind` when you know the kind).

| `status` | Action |
| -------- | ------ |
| `one` | `describe(id=node.id)` |
| `many` | pick from `candidates` (`reason`, `score`, `NodeRef`), then `describe` |
| `none` | fall back to `search(query=...)` for NL/fuzzy discovery |

Prefer **`resolve` -> `describe(id=...)`** over **`describe(fqn=...)`** when an FQN may collide (`describe(fqn=...)` returns the first row).

**`microservice`** — service where the node lives. **`target_service`** (clients only) — remote service being called. **`role`** (symbols only) — architectural stereotype (`CONTROLLER`, `SERVICE`, ...).

## Common navigation patterns

### Decision tree

| User asks... | First step | Typical follow-up |
| ------------ | ---------- | ----------------- |
| Identifier-shaped string | `resolve` (+ optional `hint_kind`) | `describe` -> `neighbors` |
| Fuzzy / NL "where is X" | `search` | `describe` -> `neighbors` |
| All controllers in service S | `find(kind="symbol", filter={"microservice":"S","role":"CONTROLLER"})` | `neighbors` `CALLS` / `EXPOSES` |
| Interfaces in service S | `find(..., filter={"microservice":"S","symbol_kind":"interface"})` | `neighbors` / `describe` |
| HTTP / messaging entry points | `find(kind="route", filter={...})` | `describe` |
| Outbound HTTP clients | `find(kind="client", filter={...})` | `neighbors(..., "out", ["HTTP_CALLS"])` from client id |
| Outbound async producers | `find(kind="producer", filter={...})` | `neighbors(..., "out", ["ASYNC_CALLS"])` from producer id |
| Who calls method M? | id via `resolve` / `find` / `search` | `neighbors(ids, "in", ["CALLS"])` |
| What does M call? | same | `neighbors(ids, "out", ["CALLS"])` |
| Who hits this route? | route id | `neighbors(ids, "in", ["HTTP_CALLS","ASYNC_CALLS","EXPOSES"])` |
| Handler for route | route id | `neighbors(ids, "in", ["EXPOSES"])` |
| Who implements interface T? | type symbol id | `neighbors(ids, "in", ["IMPLEMENTS"])` |
| Where is T injected | type symbol id | `neighbors(ids, "in", ["INJECTS"])` |
| Impact / "what breaks if I change X"? | no magic tool | loop `neighbors` `in` with `CALLS`, `INJECTS`, ... until bounded |
| "What happens when route R is called?" | `find(kind="route")` then `trace(route_id, "out", ["EXPOSES","CALLS"], max_depth=4)` | `describe` on key nodes |
| "Impact of changing method M" | `resolve` / `find` then `trace(id, "in", ["CALLS","OVERRIDES"], max_depth=3)` | `describe` on callers |
| "Trace from X to database" | `trace(id, "out", ["CALLS"], max_depth=4, prune_roles=["DTO","EXCEPTION"])` | `neighbors` for pruned detail |
| "What calls this across services?" | `trace(id, "out", ["CALLS","HTTP_CALLS","ASYNC_CALLS"], max_depth=5)` | `trace` on downstream route_id if needed |

**Rules of thumb:**

1. **Structure beats vector** for exact questions — use `resolve` / `find` + `neighbors`, not `search`, for "who calls ...".
2. **Vector beats structure** for fuzzy discovery — `search` first, then pivot to `describe` / `neighbors`.
3. **Filter by role** to keep traces focused — exclude `DTO`, `OTHER`, `MAPPER` for business logic; target `SERVICE` for orchestration, `REPOSITORY` for data access.

## Tool reference

### `search`

Ranked chunk retrieval. Args: `query`, `table` (`java`|`sql`|`yaml`|`all`, default `java`), `hybrid` (bool), `limit` (default 5), `offset`, `path_contains`, optional `filter` (symbol-applicable `NodeFilter` only).

### `find`

Exact listing for one kind. Args: `kind` (`symbol`|`route`|`client`|`producer`), **`filter`** (required object), `limit` (default 25), `offset`. Returns `NodeRef` rows (`id`, `kind`, `fqn`, `microservice`, `module`, `role` on symbols, `symbol_kind` on symbols).

### `describe`

Full node + `edge_summary` (per-label in/out counts). Args: `id` (any kind) or `fqn` (symbol only; `id` wins). Type symbols add composed keys `DECLARES.*`; method symbols add `OVERRIDDEN_BY.*` and `OVERRIDES` — see edge taxonomy for details.

### `resolve`

Identifier lookup; three statuses above. Args: `identifier`, optional `hint_kind`.

### `neighbors`

One hop. Args: `ids` (string or array), **`direction`**, **`edge_types`**, `limit` (default 25), `offset`, optional `filter` on the other node, optional **`edge_filter`** (`edge_types` must be exactly `['CALLS']` — no composed dot-keys or second stored label; fail-loud otherwise).

Returns **edges** with `attrs` (`confidence`, `strategy`, `match`, ... on cross-service edges) and **`other`** node.

**Cross-service edges** (`HTTP_CALLS`, `ASYNC_CALLS`): read `attrs.confidence` and `attrs.match` — low confidence or `unresolved`/`phantom`/`ambiguous` means treat as a resolver signal, not ground truth.

**`CALLS` edges:** source-ordered (`call_site_line`, `call_site_byte`). `attrs.resolved=false` means the callee is external (JDK/Spring) — not a missing symbol. **`include_unresolved=True`** (CALLS + `direction=out` only) interleaves unresolved call sites with resolved `CALLS` (`row_kind` discriminator); **mutually exclusive with `edge_filter`**. **`dedup_calls=True`** collapses identical `(origin, callee)` pairs to one row with `call_site_lines`. Optional **`edge_filter`** projects before pagination: `min_confidence`; `include_strategies` / `exclude_strategies` (mutually exclusive); `callee_declaring_role`, `callee_declaring_roles`, `exclude_callee_declaring_roles` (`["OTHER"]` also drops known-external rows). **Note:** `filter.role` filters the neighbor node, not the callee's declaring type — use `edge_filter.callee_declaring_role` for callee stereotype filtering.

### `trace`

Multi-hop BFS with pruning. Args: `ids` (string or list), **`direction`**, **`edge_types`** (stored labels only — no composed dot-keys), `max_depth` (default 3, clamped 1–5), `max_paths` (default 20), `max_nodes_discovered` (default 500, clamped 100–2000), optional `filter` (NodeFilter), optional `edge_filter` (CALLS only), optional `prune_roles` (soft gate — edges recorded, frontier stops), `fan_out_cap` (default 5, scaffolding edges exempt), `collapse_trivial` (default true), `include_unresolved` (default false).

Returns `TraceOutput`: `success`, `seed_ids`, `direction`, `edge_types`, `actual_depth`, `nodes` (dict of id→NodeRef), `edges` (list of `TraceEdge`), `paths` (list of `TracePath`), `stats` (`TraceStats`), `advisories`.

**`TraceEdge`**: `from_id`, `to_id`, `edge_type`, `hop` (BFS depth), `parent_edge_id` (nullable), `collapsed` (bool), `collapsed_intermediates` (list of node ids), `cross_service_boundary` (bool), `attrs`.

**When to use `trace` vs `neighbors`:**
- Use `neighbors` for single-hop adjacency where you want the full unfiltered result.
- Use `trace` for multi-hop path questions (3+ hops), impact analysis, cross-service boundary discovery, or when `neighbors` returns high fan-out (>8 CALLS edges).
- After `trace`, use `neighbors` or `describe` on specific nodes for detail the trace pruned or collapsed.

## Ontology glossary

**Roles** (`filter.role` / `exclude_roles`):

| Role | Meaning |
| ---- | ------- |
| `CONTROLLER` | HTTP / messaging entry point |
| `SERVICE` | Business logic orchestration |
| `REPOSITORY` | Data access (JPA, JDBC) |
| `COMPONENT` | General Spring component |
| `CONFIG` | `@Configuration` class |
| `ENTITY` | JPA / persistence entity |
| `CLIENT` | Outbound HTTP call wrapper (Feign, RestTemplate, WebClient) |
| `MAPPER` | Data mapper / converter |
| `DTO` | Data transfer object — data carrier, no logic |
| `OTHER` | Infrastructure / utility / framework / JDK / unclassified |

**Filtering with roles:** `DTO`, `OTHER`, and `MAPPER` are data carriers and infrastructure — exclude them with `exclude_roles` or `edge_filter.exclude_callee_declaring_roles` when tracing business logic. On `CALLS` `out` edges, use `edge_filter={"exclude_callee_declaring_roles": ["OTHER"]}` to drop JDK/Spring/framework calls. Use `filter.role` to target a specific layer (e.g. `role=SERVICE` for business logic, `role=REPOSITORY` for data access).

**Capabilities (`filter.capability`):** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.

**Symbol kinds (`symbol_kind` / `symbol_kinds`):** `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`.

**Route `framework` (examples):** `spring_mvc`, `webflux`, `kafka`, `rabbitmq`, `jms`, `stream`, `codebase_async_route`, ...

**Client kinds:** `feign_method`, `rest_template`, `web_client`.

**Producer kinds:** `kafka_send`, `stream_bridge_send`.

**HTTP call `attrs.match` / async `attrs.match`:** `cross_service`, `intra_service`, `ambiguous`, `phantom`, `unresolved`.

## Recovery playbook

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `neighbors` validation error | Missing `direction` or `edge_types` | Add both explicitly |
| Empty `neighbors` | Wrong edge type or direction | Read `describe.edge_summary`; `EXPOSES` is Symbol->Route; `OVERRIDES` is method<->method only; `HTTP_CALLS` starts from **Client** ids |
| Cannot find symbol | Wrong id or empty index | `resolve` / `search`; try `find` with `fqn_prefix` |
| `find` returns too much | Broad filter | Add `microservice`, `fqn_prefix`, `path_prefix`, `topic_prefix`, ... |
| Route not found | Path mismatch | `find(kind="route", filter={"path_prefix":...})` |
| Empty `search` | Wrong `table`, no index, or chunk miss | Try `table="all"`; `find` with `fqn_prefix`; read source files directly |
| Empty results across several tools | Index missing, stale, or wrong project | You cannot rebuild the index via MCP — ask the operator; meanwhile use open files / `rg` |
| Result vs open file disagree | Stale or partial index | Trust the file; say index may be stale |
| Mixed composed families on one id | `DECLARES.*` + `OVERRIDDEN_BY.*` together | Split calls — type keys need a type id; override keys need a method id |
| Override dot-key on type / DECLARES on method | Wrong Symbol origin for axis | Read `describe.edge_summary`; use the axis that matches the node kind |

After two failed attempts on the same intent, stop and report tool name, args, and response snippet.

## Canonical workflow: "explain feature X"

1. `search` with a short query; pick 1-3 hits with strong `symbol_id` / role fit.
2. `describe` on the chosen id; read `edge_summary`.
3. Walk with `neighbors` using **small** `edge_types` sets (e.g. `CALLS` out, or `EXPOSES` / cross-service edges for boundaries).
4. Stop when you can answer; do not prefetch unrelated subgraphs.

## Worked example

User: "how does operator assignment work?"
```
Q-class: semantic  Pick: search  Why: NL feature name
-> search(query="operator assignment", limit=8)
  -> sym:com.bank.chat.assign.service.OperatorAssignmentService  (interface, SERVICE)
  -> sym:com.bank.chat.assign.api.AssignController                (CONTROLLER)

Q-class: inspect   Pick: describe Why: edge_summary on interface
-> describe(id="sym:...OperatorAssignmentService")
  -> edge_summary { IMPLEMENTS.in: 2, INJECTS.in: 3, CALLS.in: 4 }

Q-class: walk      Pick: neighbors Why: find concrete implementors
-> neighbors(ids="sym:...OperatorAssignmentService", direction="in", edge_types=["IMPLEMENTS"])
  -> RoundRobin..., Weighted... (2 strategies)

Q-class: walk      Pick: neighbors Why: trace inbound from controller
-> neighbors(ids="sym:...AssignController", direction="out", edge_types=["CALLS"])
  -> AssignController -> OperatorAssignmentService#assign -> OperatorRepository#save

Synthesize: "Operator assignment has two strategies (RoundRobin, Weighted)
behind an interface. Triggered via AssignController. Persists via OperatorRepository..."
```

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files when MCP can answer.
- Do not skip MCP calls and guess.
- Do not fabricate ids — always obtain them from `search` / `find` / `resolve`.
- Do not walk all edge types at once — small `edge_types` sets per call.
- Do not use this MCP when the answer is already in the open file, or for third-party library trivia from training data alone. Prefer the smallest call that answers the question.
