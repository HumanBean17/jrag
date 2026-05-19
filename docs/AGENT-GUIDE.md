# Agent Guide — `java-codebase-rag` MCP

Copy the block between `<!-- BEGIN` and `<!-- END` into your project's `AGENTS.md`, `CLAUDE.md`, or equivalent. It is self-contained: five MCP tools, shared `NodeFilter`, edge taxonomy, tool-selection rules, and recovery moves.

---

<!-- BEGIN java-codebase-rag MCP guide -->

## java-codebase-rag MCP — operating manual

**Tools:** `search`, `find`, `describe`, `neighbors`, `resolve`.

**Node kinds:** `Symbol` (types and methods), `Route` (HTTP and messaging entry points), `Client` (outbound HTTP call sites), `Producer` (outbound async call sites).

**Indexed content:** Java production sources plus SQL and YAML (use `search` `table`: `java`, `sql`, `yaml`, or `all`).

**Ontology: 15** — if results look structurally wrong or empty across tools, the index may be missing, stale, or built with a different `ontology_version`; you cannot re-index via MCP — ask the operator to rebuild.

**Responses:** On success, `search`, `find`, `describe`, and `neighbors` may include a top-level `hints` list (≤5 suggested next calls) and, for `search`/`find`, echoed `limit`/`offset`. Hints are advisory; ignore them when `success` is false.

**Use this MCP when** you need whole-codebase structure: callers/callees, route handlers, HTTP/async seams, clients/producers, or fuzzy entry points for a concept.

**Do not use this MCP when** the answer is already in the open file, or for third-party library trivia from training data alone. Prefer the smallest call that answers the question.

### What this MCP is not

- **Test files, build files, CI/deploy** — read those files directly in the repo.
- **Reflection and dynamic dispatch** — `CALLS` is static analysis only; the resolved set is a **lower bound**.
- **Proof of absence** — an empty result may mean the project was not indexed, the wrong `table`, or a filter that matches nothing.
- **Git history** — use `git log` / `git blame` for "who changed" / "when".

When MCP disagrees with the open file, **the file wins**; treat the mismatch as a likely stale or incomplete index.

### Brownfield annotations on methods

If a method has any of these (including plural containers **`@CodebaseHttpRoutes`**, **`@CodebaseAsyncRoutes`**, **`@CodebaseHttpClients`**, **`@CodebaseProducers`**), that annotation is the **only** source for the facets it declares — framework inference on the **same** method is **not merged** for that axis:

| Annotation | Declares | Framework rows bypassed (examples) |
| ---------- | -------- | ------------------------------------ |
| `@CodebaseHttpRoute` | inbound HTTP path / verb | Spring MVC/WebFlux mapping annotations |
| `@CodebaseAsyncRoute` | inbound async topic / route | `@KafkaListener`, `@RabbitListener`, … |
| `@CodebaseHttpClient` | outbound HTTP client call site | `@FeignClient` method mappings, RestTemplate-style inference |
| `@CodebaseProducer` | outbound async producer call site | `KafkaTemplate` / `StreamBridge` producer inference |

Trust the indexed brownfield row over a framework-only reading of the source.

### Workflow (locate → inspect → walk)

1. **Locate** — `resolve` for identifier-shaped strings; `search` for natural language or code fragments; `find` for structured `NodeFilter` discovery.
2. **Inspect** — `describe(id)` for the full record and `edge_summary` (per-label `in`/`out` counts).
3. **Walk** — `neighbors` in a loop with explicit **`direction`** and **`edge_types`**. Multi-hop traces are **your** reasoning, not a separate tool.

### Forced reasoning preamble (every tool call)

Before each MCP call, output one short line:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

Then use real JSON shapes (see below). If the call fails or returns nothing useful, use the **Recovery playbook** — do not thrash.

### Edge taxonomy

Use these strings **verbatim** in `neighbors(..., edge_types=[...])`.

**Stored edges (one hop):**

| Group | Edge types | Semantics |
| ----- | ---------- | --------- |
| Type wiring | `EXTENDS`, `IMPLEMENTS`, `INJECTS` | `in` = who depends on this type; `out` = what this type depends on |
| Containment | `DECLARES`, `DECLARES_CLIENT`, `DECLARES_PRODUCER` | `in` = owner; `out` = owned member, client, or producer |
| Method overrides | `OVERRIDES` | Subtype **method** → supertype **declaration** (same `signature`, one `IMPLEMENTS`/`EXTENDS` hop) |
| Method calls | `CALLS` | `in` = callers; `out` = callees (method Symbol → method Symbol only) |
| Service boundary | `EXPOSES` | method Symbol → Route (handler exposes route) |
| Cross-service | `HTTP_CALLS`, `ASYNC_CALLS` | `HTTP_CALLS`: Client → Route; `ASYNC_CALLS`: Producer → Route |

**Composed edges (type Symbol origin, `direction="out"` only):**

| Edge type | Meaning |
| --------- | ------- |
| `DECLARES.DECLARES_CLIENT` | Members' HTTP clients in one hop |
| `DECLARES.DECLARES_PRODUCER` | Members' async producers in one hop |
| `DECLARES.EXPOSES` | Members' exposed routes in one hop |

**Not valid in `edge_types`:** `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.DECLARES_PRODUCER`, `OVERRIDDEN_BY.EXPOSES` (describe-only virtual keys).

### Argument shapes

#### JSON, not stringified JSON

| Param | Right | Wrong |
| ----- | ----- | ----- |
| `edge_types` | `["CALLS"]` | `"CALLS"` or `"[\"CALLS\"]"` |
| `exclude_roles` | `["DTO","OTHER"]` | stringified array |
| `filter` | `{"role":"CONTROLLER"}` | nested string JSON |
| `ids` (batch) | `["sym:…","sym:…"]` | comma-joined string |

Omit keys you do not need. Empty string `""` is often a **real filter** that matches nothing.

#### Node ids

| Kind | Prefixes |
| ---- | -------- |
| Symbol | `sym:` |
| Route | `route:` or `r:` |
| Client | `client:` or `c:` |
| Producer | `producer:` or `p:` |

Use exact ids from `search.symbol_id`, `find`, `describe`, or `neighbors.other.id`.

#### Method / type identity (Symbol FQNs)

```
<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,<SimpleType2>,…)
```

Simple types in parentheses; generics erased (`List<String>` → `List`). No spaces after commas. No-arg: `()`. Constructor: `#<init>(…)`.

#### `neighbors` — required every time

- `direction`: `"in"` or `"out"` (no default).
- `edge_types`: non-empty list from the taxonomy above.

Optional `filter` applies to each **other** endpoint; populated fields must match that neighbor's kind (strict frame).

**Batching:** multiple `ids` expand first; `limit`/`offset` slice the **merged** edge list — raise `limit` when batching.

**Mixed flat + composed `edge_types`:** flat edges are listed before composed edges, then pagination applies. A small `limit` with e.g. `["DECLARES","DECLARES.DECLARES_CLIENT"]` may return only member Symbols and no Clients — use the dot-key alone to list terminals.

#### Shared `NodeFilter` (`find`, `search.filter`, `neighbors.filter`)

For **`find`**, `filter` is required — `{}` means no predicates (all nodes of that kind, subject to pagination).

| Keys | Applies to |
| ---- | ---------- |
| `microservice`, `module`, `source_layer` | All kinds (`source_layer` mainly **client** / **producer**) |
| `role`, `exclude_roles`, `annotation`, `capability`, `fqn_prefix`, `symbol_kind`, `symbol_kinds` | **symbol** |
| `http_method`, `path_prefix`, `framework` | **route** |
| `client_kind`, `target_service`, `target_path_prefix`, `http_method` | **client** |
| `producer_kind`, `topic_prefix` | **producer** |

`http_method` filters HTTP verbs on **routes** (declared method) and on **clients** (outbound call method). Not applicable to **symbol** rows.

**Strict frame:** one populated field → one stored attribute for that kind. Unknown keys or inapplicable populated fields → `success=false` with a teaching `message`. No wildcards in `fqn_prefix`, `path_prefix`, or `target_path_prefix` (`*` / `?` rejected) — use `search(query=…)` for ranked text instead. `search.query` is opaque text, not a DSL.

### Identifier resolution (`resolve`)

**Input:** FQN or suffix, `sym:`/`route:`/`client:`/`producer:` id, `METHOD /path`, route path template, client `target_service`, `target_service` + path prefix, or producer topic.

**`hint_kind`:** optional `symbol` | `route` | `client` | `producer`. When omitted, generators run across **all four** kinds (narrow with `hint_kind` when you know the kind).

| `status` | Action |
| -------- | ------ |
| `one` | `describe(id=node.id)` |
| `many` | pick from `candidates` (`reason`, `score`, `NodeRef`), then `describe` |
| `none` | fall back to `search(query=…)` for NL/fuzzy discovery |

Prefer **`resolve` → `describe(id=…)`** over **`describe(fqn=…)`** when an FQN may collide (`describe(fqn=…)` returns the first row).

**`microservice`** — service where the node lives. **`target_service`** (clients only) — remote service being called. **`source_layer`** (clients/producers) — which extraction layer produced the row (`builtin`, `layer_a_meta`, `layer_b_ann`, `layer_c_source`, `layer_b_fqn`, …). **`role`** (symbols only) — architectural stereotype (`CONTROLLER`, `SERVICE`, …).

### Decision tree

| User asks… | First step | Typical follow-up |
| ---------- | ---------- | ----------------- |
| Identifier-shaped string | `resolve` (+ optional `hint_kind`) | `describe` → `neighbors` |
| Fuzzy / NL "where is X" | `search` | `describe` → `neighbors` |
| All controllers in service S | `find(kind="symbol", filter={"microservice":"S","role":"CONTROLLER"})` | `neighbors` `CALLS` / `EXPOSES` |
| Interfaces in service S | `find(..., filter={"microservice":"S","symbol_kind":"interface"})` | `neighbors` / `describe` |
| HTTP / messaging entry points | `find(kind="route", filter={…})` | `describe` |
| Outbound HTTP clients | `find(kind="client", filter={…})` | `neighbors(..., "out", ["HTTP_CALLS"])` from client id |
| Outbound async producers | `find(kind="producer", filter={…})` | `neighbors(..., "out", ["ASYNC_CALLS"])` from producer id |
| Who calls method M? | id via `resolve` / `find` / `search` | `neighbors(ids, "in", ["CALLS"])` |
| What does M call? | same | `neighbors(ids, "out", ["CALLS"])` |
| Who hits this route? | route id | `neighbors(ids, "in", ["HTTP_CALLS","ASYNC_CALLS","EXPOSES"])` |
| Handler for route | route id | `neighbors(ids, "in", ["EXPOSES"])` |
| Who implements interface T? | type symbol id | `neighbors(ids, "in", ["IMPLEMENTS"])` |
| Who injects type T? | type symbol id | `neighbors(ids, "in", ["INJECTS"])` |
| Impact / "what breaks if I change X"? | no magic tool | loop `neighbors` `in` with `CALLS`, `INJECTS`, … until bounded |

**Rules of thumb:**

1. **Structure beats vector** for exact questions — use `resolve` / `find` + `neighbors`, not `search`, for "who calls …".
2. **Vector beats structure** for fuzzy discovery — `search` first, then pivot to `describe` / `neighbors`.

### Tool reference

#### `search`

Ranked chunk retrieval. Args: `query`, `table` (`java`|`sql`|`yaml`|`all`, default `java`), `hybrid` (bool), `limit` (default 5), `offset`, `path_contains`, optional `filter` (symbol-applicable `NodeFilter` only).

#### `find`

Exact listing for one kind. Args: `kind` (`symbol`|`route`|`client`|`producer`), **`filter`** (required object), `limit` (default 25), `offset`. Returns `NodeRef` rows (`id`, `kind`, `fqn`, `microservice`, `module`, `role` on symbols, `symbol_kind` on symbols).

#### `describe`

Full node + `edge_summary`. Args: `id` (any kind) or `fqn` (symbol only; `id` wins).

- **Stored keys** — counts for edges that exist in the graph.
- **Type symbols** (`class`, `interface`, `enum`, `record`, `annotation`) may add composed keys `DECLARES.DECLARES_CLIENT`, `DECLARES.DECLARES_PRODUCER`, `DECLARES.EXPOSES` — navigable via `neighbors` with those dot-keys (`out` only).
- **Method symbols** may add virtual keys `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_*`, `OVERRIDDEN_BY.EXPOSES` (describe only), plus an **`OVERRIDES`** row merging stored `[:OVERRIDES]` counts with a dispatch-up rollup (`in`/`out` per direction uses `max` of stored vs rollup). Use `neighbors(..., ["OVERRIDES"])` to list override edges. Static methods and constructors do not get override-axis keys.

Composed counts are **edge rows**, not distinct methods; `count > 0` means "there is something to walk".

#### `resolve`

Identifier lookup; three statuses above. Args: `identifier`, optional `hint_kind`.

#### `neighbors`

One hop. Args: `ids` (string or array), **`direction`**, **`edge_types`**, `limit` (default 25), `offset`, optional `filter` on the other node, optional **`edge_filter`** (`edge_types` must be exactly `['CALLS']` — no composed dot-keys or second stored label; fail-loud otherwise).

**Multiple origin ids:** each id loads the full CALLS stream (or generic hop) in list order; `offset`/`limit` apply to the **concatenated** edge list (`ids[0]` edges first, then `ids[1]`, …), not global source order across origins — a large first origin can leave no rows for later ids within the same page. High fan-out methods are slow; prefer one id per call or a smaller `limit`.

Returns **edges** with `attrs` (`confidence`, `strategy`, `match`, … on cross-service edges) and **`other`** node.

**Cross-service edges** (`HTTP_CALLS`, `ASYNC_CALLS`): read `attrs.confidence` and `attrs.match` — low confidence or `unresolved`/`phantom`/`ambiguous` means treat as a resolver signal, not ground truth.

**`CALLS` edges:** source-ordered (`call_site_line`, `call_site_byte`). `attrs.resolved=false` or low `attrs.confidence` may be JDK/external or unresolved static sites — still a lower bound, not exhaustive runtime behaviour. **`filter` + `edge_filter` together** load the ordered CALLS stream then apply callee `NodeFilter` in Python — expect higher latency on hot methods than `edge_filter` alone. Optional **`edge_filter`** projects before pagination: `min_confidence`; `include_strategies` / `exclude_strategies` (mutually exclusive); `callee_declaring_role`, `callee_declaring_roles`, `exclude_callee_declaring_roles` (`["OTHER"]` also drops known-external rows). **`filter.role` filters the neighbor method (usually `OTHER`), not the callee stereotype** — use `edge_filter.callee_declaring_role` for repository/service hops. **`exclude_external` applies to `find_callers` / `find_callees` only** (FQN-prefix); trim JDK noise on CALLS via `edge_filter`. Accessor noise: role excludes help; getter/setter heuristics in [`propose/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md`](../propose/AGENT-SKILLS-AND-COMMANDS-PROPOSE.md) `/mini-map`.

### Ontology glossary

**Roles (`filter.role` / `exclude_roles`):** `CONTROLLER`, `SERVICE`, `REPOSITORY`, `COMPONENT`, `CONFIG`, `ENTITY`, `CLIENT`, `MAPPER`, `DTO`, `OTHER`.

**Capabilities (`filter.capability`):** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.

**Symbol kinds (`symbol_kind` / `symbol_kinds`):** `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`.

**Route `framework` (examples on stored routes):** `spring_mvc`, `webflux`, `kafka`, `rabbitmq`, `jms`, `stream`, `codebase_async_route`, …

**Client kinds:** `feign_method`, `rest_template`, `web_client`.

**Producer kinds:** `kafka_send`, `stream_bridge_send`.

**HTTP call `attrs.match` / async `attrs.match`:** `cross_service`, `intra_service`, `ambiguous`, `phantom`, `unresolved`.

### Recovery playbook

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `neighbors` validation error | Missing `direction` or `edge_types` | Add both explicitly |
| Empty `neighbors` | Wrong edge type or direction | Read `describe.edge_summary`; `EXPOSES` is Symbol→Route; `OVERRIDES` is method↔method only; `HTTP_CALLS` starts from **Client** ids |
| Cannot find symbol | Wrong id or empty index | `resolve` / `search`; try `find` with `fqn_prefix` |
| `find` returns too much | Broad filter | Add `microservice`, `fqn_prefix`, `path_prefix`, `topic_prefix`, … |
| Route not found | Path mismatch | `find(kind="route", filter={"path_prefix":…})` |
| Empty `search` | Wrong `table`, no index, or chunk miss | Try `table="all"`; `find` with `fqn_prefix`; read source files directly |
| Empty results across several tools | Index missing, stale, or wrong project | You cannot rebuild the index via MCP — ask the operator; meanwhile use open files / `rg` |
| Result vs open file disagree | Stale or partial index | Trust the file; say index may be stale |
| Used virtual key in `neighbors` | `OVERRIDDEN_BY*` is describe-only | Use stored `OVERRIDES` or manual walk via `DECLARES` → type → `IMPLEMENTS`/`EXTENDS` |

After two failed attempts on the same intent, stop and report tool name, args, and response snippet.

### Slash-style aliases

- `/nl <text>` → `search({"query":"<text>","limit":8})` then `describe` on best `symbol_id`.
- `/controllers <ms>` → `find({"kind":"symbol","filter":{"microservice":"<ms>","role":"CONTROLLER"}})`.
- `/routes <ms>` → `find({"kind":"route","filter":{"microservice":"<ms>"}})`.
- `/clients <ms>` → `find({"kind":"client","filter":{"microservice":"<ms>"},"limit":100})`.
- `/producers <ms>` → `find({"kind":"producer","filter":{"microservice":"<ms>"},"limit":100})`.
- `/callers <sym_id>` → `neighbors({"ids":"<sym_id>","direction":"in","edge_types":["CALLS"]})`.
- `/callees <sym_id>` → `neighbors({"ids":"<sym_id>","direction":"out","edge_types":["CALLS"]})`.
- `/handlers <route_id>` → `neighbors({"ids":"<route_id>","direction":"in","edge_types":["EXPOSES"]})`.
- `/who-hits-route <route_id>` → `neighbors({"ids":"<route_id>","direction":"in","edge_types":["HTTP_CALLS","ASYNC_CALLS","EXPOSES"]})`.
- `/implements <type_sym_id>` → `neighbors({"ids":"<type_sym_id>","direction":"in","edge_types":["IMPLEMENTS"]})`.
- `/injects <type_sym_id>` → `neighbors({"ids":"<type_sym_id>","direction":"in","edge_types":["INJECTS"]})`.

### Canonical workflow: "explain feature X"

1. `search` with a short query; pick 1–3 hits with strong `symbol_id` / role fit.
2. `describe` on the chosen id; read `edge_summary`.
3. Walk with `neighbors` using **small** `edge_types` sets (e.g. `CALLS` out, or `EXPOSES` / cross-service edges for boundaries).
4. Stop when you can answer; do not prefetch unrelated subgraphs.

<!-- END java-codebase-rag MCP guide -->

---

## Maintenance (repo editors only — do not paste below into agent instructions)

When MCP behaviour, `NodeFilter` keys, edge labels, or node kinds change:

1. Update this file's copy block and bump the **Ontology:** line to match `ast_java.ONTOLOGY_VERSION`.
2. Update the MCP tool table and "Driving the MCP from an agent" bullet in `README.md`.
3. If enrichment semantics changed, add a "Re-index required" callout in `README.md`.
