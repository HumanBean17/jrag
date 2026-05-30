# Agent Guide — `java-codebase-rag` MCP

Copy the block between `<!-- BEGIN` and `<!-- END` into your project's `AGENTS.md`, `CLAUDE.md`, or equivalent. It is self-contained: six MCP tools, shared `NodeFilter`, edge taxonomy, tool-selection rules, and recovery moves.

---

<!-- BEGIN java-codebase-rag MCP guide -->

## java-codebase-rag MCP — operating manual

**Tools:** `search`, `find`, `describe`, `neighbors`, `resolve`, `trace`.

**Node kinds:** `Symbol` (types and methods), `Route` (HTTP and messaging entry points), `Client` (outbound HTTP call sites), `Producer` (outbound async call sites).

**Indexed content:** Java production sources plus SQL and YAML (use `search` `table`: `java`, `sql`, `yaml`, or `all`).

**Ontology: 15** — if results look structurally wrong or empty across tools, the index may be missing, stale, or built with a different `ontology_version`; you cannot re-index via MCP — ask the operator to rebuild.

**Responses:** On success, `search`, `find`, `describe`, `neighbors`, `resolve`, and `trace` may include two top-level fields: `hints_structured` (≤5 suggested next-tool calls) and `advisories` (≤5 pure informational strings). Each `hints_structured` entry has `tool`, `args`, `actionable`, `label`, and `reason`. `actionable=true` means you can call the tool directly with `args`; `actionable=false` means partial/advisory — fill missing values or use as guidance. `reason` explains why the hint was emitted. `advisories` carry context education (fuzzy strategy warnings, role collision explanations, etc.) with no tool call suggestion. For `search`/`find`, echoed `limit`/`offset`. Hints are advisory; ignore them when `success` is false.

**Use this MCP when** you need whole-codebase structure: callers/callees, route handlers, HTTP/async seams, clients/producers, or fuzzy entry points for a concept.

**Do not use this MCP when** the answer is already in the open file, or for third-party library trivia from training data alone. Prefer the smallest call that answers the question.

### What this MCP is not

- **Test files, build files, CI/deploy** — read those files directly in the repo.
- **Reflection and dynamic dispatch** — `CALLS` is static analysis only; the resolved set is a **lower bound**.
- **Proof of absence** — an empty result may mean the project was not indexed, the wrong `table`, or a filter that matches nothing.
- **Git history** — use `git log` / `git blame` for "who changed" / "when".

When MCP disagrees with the open file, **the file wins**; treat the mismatch as a likely stale or incomplete index.

### Workflow (locate → inspect → walk)

1. **Locate** — `resolve` for identifier-shaped strings; `search` for natural language or code fragments; `find` for structured `NodeFilter` discovery.
2. **Inspect** — `describe(id)` for the full record and `edge_summary` (per-label `in`/`out` counts).
3. **Walk** — `neighbors` in a loop with explicit **`direction`** and **`edge_types`**, or `trace` for multi-hop BFS with server-side pruning in one call.

### Forced reasoning preamble (every tool call)

Before each MCP call, output one short line:

```
Q-class: <semantic | structured | inspect | walk | trace>
Pick: <search|find|describe|neighbors|trace|resolve>  Why: <≤8 words>
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

**Composed edges — type Symbol origin (`direction="out"` only):**

| Edge type | Meaning |
| --------- | ------- |
| `DECLARES.DECLARES_CLIENT` | Members' HTTP clients in one hop |
| `DECLARES.DECLARES_PRODUCER` | Members' async producers in one hop |
| `DECLARES.EXPOSES` | Members' exposed routes in one hop |

**Composed edges — non-static method Symbol origin (`direction="out"` only):**

| Edge type | Meaning |
| --------- | ------- |
| `OVERRIDDEN_BY` | Concrete overrider methods |
| `OVERRIDDEN_BY.DECLARES_CLIENT` | Clients declared on overriders |
| `OVERRIDDEN_BY.DECLARES_PRODUCER` | Producers on overriders |
| `OVERRIDDEN_BY.EXPOSES` | Routes exposed by overriders |

`neighbors(decl_id, "out", ["OVERRIDDEN_BY"])` returns the same overrider methods as `neighbors(decl_id, "in", ["OVERRIDES"])` — prefer the dot-key when `edge_summary` advertises it.

Do not mix `DECLARES.*` and `OVERRIDDEN_BY.*` in one `edge_types` list on a single origin id — the handler rejects the whole request (only one axis applies per node).

**Pagination:** default `neighbors` `limit=25` slices the merged flat + composed edge list. When `edge_summary` shows a large `out` count for a composed key, raise `limit` (and use `offset`) or issue separate calls per key.

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
| `microservice`, `module` | All kinds |
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

**`microservice`** — service where the node lives. **`target_service`** (clients only) — remote service being called. **`role`** (symbols only) — architectural stereotype (`CONTROLLER`, `SERVICE`, …).

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
| Impact / "what breaks if I change X"? | `trace` or `neighbors` loop | `trace(id, "in", ["CALLS","OVERRIDES"], max_depth=3)` or loop `neighbors` `in` with `CALLS`, `INJECTS` |

**Rules of thumb:**

1. **Structure beats vector** for exact questions — use `resolve` / `find` + `neighbors`, not `search`, for "who calls …".
2. **Vector beats structure** for fuzzy discovery — `search` first, then pivot to `describe` / `neighbors`.
3. **Filter by role** to keep traces focused — exclude `DTO`, `OTHER`, `MAPPER` for business logic; target `SERVICE` for orchestration, `REPOSITORY` for data access.

### Tool reference

#### `trace`

Multi-hop BFS traversal with server-side pruning. Returns structured paths, a node dict, and traversal stats. Use when the question implies a path or chain (3+ hops), needs to cross a service boundary, or a `neighbors` loop has exceeded 2 hops without converging. Args: `ids` (string or array), **`direction`**, **`edge_types`** (stored labels only — no composed dot-keys), `max_depth` (1–5, default 3), `max_paths` (default 20), `max_nodes_discovered` (100–2000, default 500), `filter` (hard gate `NodeFilter`), `edge_filter` (CALLS edge attribute filtering), `prune_roles` (soft gate — edges recorded, frontier stops), `fan_out_cap` (per-node edge limit, default 5), `collapse_trivial` (collapse wrapper chains, default true), `include_unresolved` (interleave unresolved call sites).

Returns `TraceOutput` with `nodes` (dict of `NodeRef`), `edges` (list of `TraceEdge` with `hop`, `parent_edge_id`, `collapsed`, `cross_service_boundary`), `paths` (ranked root-to-leaf), and `stats` (budget, pruning counts). Cross-service edges (`HTTP_CALLS`, `ASYNC_CALLS`) are boundary signals — BFS stops at the service boundary and includes the downstream node for the agent to continue with a separate `trace` call.

**`trace` vs `neighbors`:** Use `neighbors` for single-hop adjacency (full unfiltered result). Use `trace` for multi-hop path questions, impact analysis, or when `neighbors` returns high fan-out (>8 CALLS edges).

#### `search`

Ranked chunk retrieval. Args: `query`, `table` (`java`|`sql`|`yaml`|`all`, default `java`), `hybrid` (bool), `limit` (default 5), `offset`, `path_contains`, optional `filter` (symbol-applicable `NodeFilter` only).

#### `find`

Exact listing for one kind. Args: `kind` (`symbol`|`route`|`client`|`producer`), **`filter`** (required object), `limit` (default 25), `offset`. Returns `NodeRef` rows (`id`, `kind`, `fqn`, `microservice`, `module`, `role` on symbols, `symbol_kind` on symbols).

#### `describe`

Full node + `edge_summary`. Args: `id` (any kind) or `fqn` (symbol only; `id` wins).

- **Stored keys** — counts for edges that exist in the graph.
- **Type symbols** (`class`, `interface`, `enum`, `record`, `annotation`) may add composed keys `DECLARES.DECLARES_CLIENT`, `DECLARES.DECLARES_PRODUCER`, `DECLARES.EXPOSES` — navigable via `neighbors` with those dot-keys (`out` only).
- **Method symbols** may add virtual keys `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_*`, `OVERRIDDEN_BY.EXPOSES` (navigable via `neighbors` on non-static method origins, `out` only), plus an **`OVERRIDES`** row with incident counts. Static methods and constructors do not get override-axis keys.

Composed counts are **edge rows**, not distinct methods; `count > 0` means "there is something to walk".

#### `resolve`

Identifier lookup; three statuses above. Args: `identifier`, optional `hint_kind`.

#### `neighbors`

One hop. Args: `ids` (string or array), **`direction`**, **`edge_types`**, `limit` (default 25), `offset`, optional `filter` on the other node, optional **`edge_filter`** (`edge_types` must be exactly `['CALLS']` — no composed dot-keys or second stored label; fail-loud otherwise).

**Multiple origin ids:** `offset`/`limit` apply to the **concatenated** edge list (`ids[0]` edges first, then `ids[1]`, …). A large first origin can leave no rows for later ids within the same page. Prefer one id per call or raise `limit`.

Returns **edges** with `attrs` (`confidence`, `strategy`, `match`, … on cross-service edges) and **`other`** node.

**Cross-service edges** (`HTTP_CALLS`, `ASYNC_CALLS`): read `attrs.confidence` and `attrs.match` — low confidence or `unresolved`/`phantom`/`ambiguous` means treat as a resolver signal, not ground truth.

**`CALLS` edges:** source-ordered (`call_site_line`, `call_site_byte`). `attrs.resolved=false` means the callee is external (JDK/Spring) — not a missing symbol. **`include_unresolved=True`** (CALLS + `direction=out` only) interleaves unresolved call sites with resolved `CALLS` (`row_kind` discriminator); **mutually exclusive with `edge_filter`**. **`dedup_calls=True`** collapses identical `(origin, callee)` pairs to one row with `call_site_lines`. Optional **`edge_filter`** projects before pagination: `min_confidence`; `include_strategies` / `exclude_strategies` (mutually exclusive); `callee_declaring_role`, `callee_declaring_roles`, `exclude_callee_declaring_roles` (`["OTHER"]` also drops known-external rows). **Note:** `filter.role` filters the neighbor node, not the callee's declaring type — use `edge_filter.callee_declaring_role` for callee stereotype filtering.

### Ontology glossary

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
| Mixed composed families on one id | `DECLARES.*` + `OVERRIDDEN_BY.*` together | Split calls — type keys need a type id; override keys need a method id |
| Override dot-key on type / DECLARES on method | Wrong Symbol origin for axis | Read `describe.edge_summary`; use the axis that matches the node kind |

After two failed attempts on the same intent, stop and report tool name, args, and response snippet.

### Common navigation patterns

These patterns combine the six tools above. Use the decision tree to pick the right starting tool.

| Intent | Tool chain |
| ------ | ---------- |
| Natural-language "find X" | `search(query=…, limit=8)` → `describe(top_hit.symbol_id)` |
| List controllers in service S | `find(kind="symbol", filter={microservice:"S", role:"CONTROLLER"})` |
| List routes in service S | `find(kind="route", filter={microservice:"S"})` |
| List clients in service S | `find(kind="client", filter={microservice:"S"}, limit=100)` |
| List producers in service S | `find(kind="producer", filter={microservice:"S"}, limit=100)` |
| Who calls method M | `resolve` → `neighbors(ids, "in", ["CALLS"])` |
| What does M call | `resolve` → `neighbors(ids, "out", ["CALLS"])` |
| Handler for route R | `neighbors(route_id, "in", ["EXPOSES"])` |
| All inbound to route R | `neighbors(route_id, "in", ["HTTP_CALLS","ASYNC_CALLS","EXPOSES"])` |
| Implementors of interface T | `neighbors(type_id, "in", ["IMPLEMENTS"])` |
| Where is T injected | `neighbors(type_id, "in", ["INJECTS"])` |
| Impact of changing X | `resolve` → `trace(id, "in", ["CALLS","OVERRIDES"], max_depth=3)` or `neighbors` loop depth ≤2 |
| "What happens when route R is called?" | `find(kind="route")` → `trace(route_id, "out", ["EXPOSES","CALLS"], max_depth=4)` |
| "Trace from X to database" | `trace(id, "out", ["CALLS"], max_depth=4, prune_roles=["DTO","EXCEPTION"])` |
| "What calls this across services?" | `trace(id, "out", ["CALLS","HTTP_CALLS","ASYNC_CALLS"], max_depth=5)` |

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
2. Update the six-tool cheat sheet in `README.md` and the "Driving the MCP from an agent" bullet there.
3. If enrichment semantics changed, add a "Re-index required" callout in [`docs/CONFIGURATION.md`](./CONFIGURATION.md) §3.
