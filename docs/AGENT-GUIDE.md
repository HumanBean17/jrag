# Agent Guide — `java-codebase-rag` MCP

Copy the block between `<!-- BEGIN` and `<!-- END` into your project's `AGENTS.md`, `CLAUDE.md`, or equivalent. It is self-contained: five MCP tools, shared `NodeFilter`, edge taxonomy, tool-selection rules, and recovery moves.

---

<!-- BEGIN java-codebase-rag MCP guide -->

## java-codebase-rag MCP — operating manual

**Tools:** `search`, `find`, `describe`, `neighbors`, `resolve`.

**Node kinds:** `Symbol` (types and methods), `Route` (HTTP and messaging entry points), `Client` (outbound HTTP call sites), `Producer` (outbound async call sites).

**Indexed content:** Java production sources plus SQL and YAML (use `search` `table`: `java`, `sql`, `yaml`, or `all`).

**Ontology: 19** — if results look structurally wrong or empty across tools, the index may be missing, stale, or built with a different `ontology_version`; you cannot re-index via MCP — ask the operator to rebuild.

**Responses:** On success, `search`, `find`, `describe`, `neighbors`, and `resolve` may include two top-level fields: `hints_structured` (≤5 suggested next-tool calls) and `advisories` (≤5 pure informational strings). Each `hints_structured` entry has `tool`, `args`, `actionable`, `label`, and `reason`. `actionable=true` means you can call the tool directly with `args`; `actionable=false` means partial/advisory — fill missing values or use as guidance. `reason` explains why the hint was emitted. `advisories` carry context education (fuzzy strategy warnings, role collision explanations, etc.) with no tool call suggestion. For `search`/`find`, echoed `limit`/`offset`. Hints are advisory; ignore them when `success` is false.

**Use this MCP when** you need whole-codebase structure: callers/callees, route handlers, HTTP/async seams, clients/producers, or fuzzy entry points for a concept.

**Do not use this MCP when** the answer is already in the open file, or for third-party library trivia from training data alone. Prefer the smallest call that answers the question.

### What this MCP is not

- **Test files, build files, CI/deploy** — read those files directly in the repo.
- **Reflection and dynamic dispatch** — `CALLS` is static analysis only; the resolved set is a **lower bound**.
- **Proof of absence** — an empty result may mean the project was not indexed, the wrong `table`, or a filter that matches nothing. When `absence` is populated on empty results, `absence.proof` provides an auditable signal (nearest distance, symbols scanned, thresholds) for `not_in_project` verdicts.
- **Git history** — use `git log` / `git blame` for "who changed" / "when".

When MCP disagrees with the open file, **the file wins**; treat the mismatch as a likely stale or incomplete index.

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
| `role`, `exclude_roles`, `annotation`, `capability`, `fqn_contains`, `symbol_kind`, `symbol_kinds`, `generated_only`, `exclude_generated` | **symbol** |
| `http_method`, `path_contains`, `framework` | **route** |
| `source_layer`, `client_kind`, `target_service`, `target_path_contains`, `http_method` | **client** |
| `source_layer`, `producer_kind`, `topic_contains` | **producer** |

`http_method` filters HTTP verbs on **routes** (declared method) and on **clients** (outbound call method). Not applicable to **symbol** rows.

**Strict frame:** one populated field → one stored attribute for that kind. Unknown keys or inapplicable populated fields → `success=false` with a teaching `message`. Invalid enum values (e.g. wrong case) are rejected earlier at the schema layer with the valid set listed. The substring fields (`fqn_contains`, `path_contains`, `target_path_contains`, `topic_contains`) match literally via `CONTAINS` — no `*`/`?` metacharacters; use `search(query=…)` for ranked text instead. `search.query` is opaque text, not a DSL.

### Generated source detection

**Generated sources** (MapStruct mappers, OpenAPI clients, protobuf stubs, etc.) are **auto-detected by content** (not by path). Every MCP search/find/describe/neighbors result row carries two fields:

- `generated` (bool) — `true` if the source file is generated.
- `generated_by` (string | null) — the generator family slug (`openapi`, `jsonschema2pojo`, `protobuf`, `mapstruct`, `wsimport`, `querydsl`, `jooq`, `immutables`, `autovalue`, `lombok`), or `null` for unrecognized generators.

**Detection criteria:** A Java source file is classified as generated when it carries a `@Generated` annotation (javax/jakarta.annotation.processing.Generated and equivalents: lombok.Generated, org.immutables.value.Generated, com.squareup.javapoet.Generated) OR a recognized generator header banner (OpenAPI, jsonschema2pojo, protobuf, MapStruct, wsimport).

**Note:** The detector matches `@Generated` by simple annotation name. If your project defines its own unrelated `@Generated` annotation (e.g., `@com.example.Generated`), it will be flagged as generated code. To exclude a specific FQN from detection, add it to `generated_detection.exclude_fqns` in your `.java-codebase-rag.yml` configuration.

**Equal-treatment default:** Generated sources are indexed and returned **exactly** like hand-written code by default — they are **not** ranked down and **not** excluded from graph traversal. The existing role-aware ranking already down-ranks non-actionable roles (DTOs/mappers), which covers most generated code.

**Filtering:** Use `filter={"exclude_generated": true}` on the MCP `NodeFilter` to exclude generated sources when you only want hand-written code. Use `filter={"generated_only": true}` to show only generated sources. On the CLI, use `--exclude-generated` or `--generated-only` flags.

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
| Impact / "what breaks if I change X"? | no magic tool | loop `neighbors` `in` with `CALLS`, `INJECTS`, … until bounded |

**Rules of thumb:**

1. **Structure beats vector** for exact questions — use `resolve` / `find` + `neighbors`, not `search`, for "who calls …".
2. **Vector beats structure** for fuzzy discovery — `search` first, then pivot to `describe` / `neighbors`.
3. **Filter by role** to keep traces focused — exclude `DTO`, `OTHER`, `MAPPER` for business logic; target `SERVICE` for orchestration, `REPOSITORY` for data access.

### Tool reference

#### `search`

Ranked chunk retrieval. Args: `query`, `table` (`java`|`sql`|`yaml`|`all`, default `java`), `hybrid` (bool), `limit` (default 5), `offset`, `path_contains`, optional `filter` (symbol-applicable `NodeFilter` only), optional `chunks` (bool, default `false`). Returns one row per `primary_type_fqn` (symbol/type) by default; set `chunks=true` to restore chunk-level output. When deduped, each hit includes a `chunks` field (≥1) indicating how many chunks were collapsed into that hit.

> **Intel Mac (graph-only) installs:** `search` runs the **lexical backend** — BM25 keyword ranking over the symbol graph's LadybugDB full-text index instead of embeddings, behind this same contract. Same `query`/`table`/`filter`/`limit`/`chunks` behavior; results are keyword-ranked (not semantic), `hybrid` is ignored, `sql`/`yaml` tables aren't indexed (only Java symbols), and an `advisories` entry + `lexical_mode=true` flag note the mode. Structural discovery (`find`/`describe`/`neighbors`/`resolve`) is unaffected.

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
| `CLIENT` | Outbound call wrapper (HTTP and messaging) |
| `MAPPER` | Data mapper / converter |
| `DTO` | Data transfer object — data carrier, no logic |
| `OTHER` | Infrastructure / utility / framework / JDK / unclassified |

**Filtering with roles:** `DTO`, `OTHER`, and `MAPPER` are data carriers and infrastructure — exclude them with `exclude_roles` or `edge_filter.exclude_callee_declaring_roles` when tracing business logic. On `CALLS` `out` edges, use `edge_filter={"exclude_callee_declaring_roles": ["OTHER"]}` to drop JDK/Spring/framework calls. Use `filter.role` to target a specific layer (e.g. `role=SERVICE` for business logic, `role=REPOSITORY` for data access).

**Capabilities (`filter.capability`):** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.

**Symbol kinds (`symbol_kind` / `symbol_kinds`):** `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`.

**Route `framework` (closed set on stored routes):** `spring_mvc`, `webflux`. (The `kafka` / `rabbitmq` / `jms` / `stream` values are route *kinds*, not frameworks; `feign` is a client kind.)

**Client kinds:** `feign_method`, `rest_template`, `web_client`.

**Producer kinds:** `kafka_send`, `stream_bridge_send`.

**Source layers (client/producer):** `builtin`, `layer_a_meta`, `layer_b_ann`, `layer_b_fqn`, `layer_c_source`.

**HTTP call `attrs.match` / async `attrs.match`:** `cross_service`, `intra_service`, `ambiguous`, `phantom`, `unresolved`.

### Recovery playbook

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `neighbors` validation error | Missing `direction` or `edge_types` | Add both explicitly |
| Empty `neighbors` | Wrong edge type or direction | Read `describe.edge_summary`; `EXPOSES` is Symbol→Route; `OVERRIDES` is method↔method only; `HTTP_CALLS` starts from **Client** ids. If `absence` is populated, read `absence.verdict` first: `not_in_project` → stop (target isn't in this project); `external_dependency` → it's a referenced-but-undefined dep; `refine_query` → use provided `closest_symbols`/`vocabulary_context`/`filter_relaxation`; `correct_empty` → the zero is correct. |
| Cannot find symbol | Wrong id or empty index | `resolve` / `search`; try `find` with `fqn_contains`. If `absence` is populated, read `absence.verdict` first (see `Empty neighbors` above). |
| `find` returns too much | Broad filter | Add `microservice`, `fqn_contains`, `path_contains`, `topic_contains`, … |
| Route not found | Path mismatch | `find(kind="route", filter={"path_contains":…})` |
| Empty `search` | Wrong `table`, no index, or chunk miss | Try `table="all"`; `find` with `fqn_contains`; read source files directly. If `absence` is populated, read `absence.verdict` first (see `Empty neighbors` above). |
| Empty results across several tools | Index missing, stale, or wrong project | You cannot rebuild the index via MCP — ask the operator; meanwhile use open files / `rg` |
| Result vs open file disagree | Stale or partial index | Trust the file; say index may be stale |
| Mixed composed families on one id | `DECLARES.*` + `OVERRIDDEN_BY.*` together | Split calls — type keys need a type id; override keys need a method id |
| Override dot-key on type / DECLARES on method | Wrong Symbol origin for axis | Read `describe.edge_summary`; use the axis that matches the node kind |

After two failed attempts on the same intent, stop and report tool name, args, and response snippet.

### Common navigation patterns

These patterns combine the five tools above. Use the decision tree to pick the right starting tool.

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
| Impact of changing X | `resolve` → `describe` → bounded `neighbors(in, ["CALLS","INJECTS","IMPLEMENTS","EXTENDS"])` depth ≤2 |

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
2. Update the five-tool cheat sheet in `README.md` and the "Driving the MCP from an agent" bullet there.
3. If enrichment semantics changed, add a "Re-index required" callout in [`docs/CONFIGURATION.md`](./CONFIGURATION.md) §3.
