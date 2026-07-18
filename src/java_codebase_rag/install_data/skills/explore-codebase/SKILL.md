---
name: explore-codebase
description: "MUST BE USED PROACTIVELY. Universal read-only codebase exploration. Combines jrag graph navigation (call chains, routes, service boundaries, impact analysis, FQN resolution) with broad file-system search (grep, glob, file reading). Use for any exploration: locating code, tracing dependencies, finding patterns, 'where is X', 'who calls Y', 'find all controllers', 'trace the flow from A to B'. Do NOT use when the answer is already in open context or for a single known file — read that file directly."
---

# /explore-codebase — Universal codebase exploration

Read-only exploration combining **jrag graph navigation** with **broad file-system search**.

Use any time you must search, locate, navigate, or explore. **Do NOT use when** the answer is already in context or for a single known file — read it directly.

## Core Principles

1. **Read-only.** Never edit, write, or modify any file.
2. **Smallest sufficient tool.** Pick the lightest tool that answers the question.
3. **Stop when answered.** Don't prefetch unrelated subgraphs or directories.

## Tool Inventory

- **Graph (jrag MCP):** `search`, `find`, `describe`, `neighbors`, `resolve`. Node kinds: `Symbol` (types/methods), `Route` (HTTP/messaging entry points), `Client` (outbound HTTP), `Producer` (outbound async). Indexed content: Java + SQL + YAML (`table`: `java`, `sql`, `yaml`, `all`).
- **File-system:** `Grep` (content/regex), `Glob` (name/path patterns), `Read` (`offset`/`limit` for large files).
- **Other:** `Bash` (read-only: `git log`, `git blame`, `ls`, `find`), `WebSearch`/`WebFetch`.

---

## Decision Framework

| User asks… | First step | Follow-up |
| ---------- | ---------- | --------- |
| Identifier-shaped string | `resolve` (+ optional `hint_kind`) | `describe` → `neighbors` |
| Fuzzy / NL "where is X" | `search` | `describe` → `neighbors` |
| All controllers in service S | `find(kind="symbol", filter={"microservice":"S","role":"CONTROLLER"})` | `neighbors` `CALLS`/`EXPOSES` |
| Interfaces in service S | `find(..., filter={"microservice":"S","symbol_kind":"interface"})` | `neighbors`/`describe` |
| HTTP / messaging entry points | `find(kind="route", filter={…})` | `describe` |
| Outbound HTTP clients | `find(kind="client", filter={…})` | `neighbors(..., "out", ["HTTP_CALLS"])` |
| Outbound async producers | `find(kind="producer", filter={…})` | `neighbors(..., "out", ["ASYNC_CALLS"])` |
| Who calls method M? | id via `resolve`/`find`/`search` | `neighbors(ids, "in", ["CALLS"])` |
| What does M call? | same | `neighbors(ids, "out", ["CALLS"])` |
| Who hits this route? | route id | `neighbors(ids, "in", ["HTTP_CALLS","ASYNC_CALLS","EXPOSES"])` |
| Handler for route | route id | `neighbors(ids, "in", ["EXPOSES"])` |
| Who implements/injects T? | type symbol id | `neighbors(ids, "in", ["IMPLEMENTS"])` or `["INJECTS"]` |
| Impact of changing X? | bounded `neighbors` `in` loop (`CALLS`, `INJECTS`, …) | `Grep` fallback |
| Find files / text | `Glob` / `Grep` | `Read` |
| Who changed X and when? | Bash: `git log`/`git blame` | — |
| "How is this configured?" | `Glob` + `Grep`; `search(query=…, table="yaml")` | `Read` sections |

**Escalation:** ① Most targeted tool first → ② fall back gracefully (graph empty → `Grep`/`Glob`) → ③ cross-validate (graph vs file disagree → **trust the file**).

**Rules of thumb:** structure beats vector for exact questions (`resolve`/`find`+`neighbors`); vector beats structure for fuzzy discovery (`search`); file-system beats stale index.

---

## Graph Navigation Reference (jrag MCP)

**Ontology: 17.** If results look structurally wrong or empty across tools, the index may be missing/stale — ask the operator to rebuild. Responses may carry `hints_structured` (suggested next calls) and `advisories` — advisory only; ignore when `success` is false.

### Forced reasoning preamble (every MCP call)

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

**Workflow:** locate (`resolve`/`search`/`find`) → inspect (`describe`) → walk (`neighbors`, explicit `direction` + `edge_types`).

### Edge taxonomy

Use these strings **verbatim** in `neighbors(..., edge_types=[...])`.

**Stored (one hop):**

| Edge type | Semantics |
| --------- | --------- |
| `EXTENDS`, `IMPLEMENTS`, `INJECTS` | Type wiring. `in`=dependents, `out`=dependencies |
| `DECLARES`, `DECLARES_CLIENT`, `DECLARES_PRODUCER` | Containment. `in`=owner, `out`=owned member/client/producer |
| `OVERRIDES` | Subtype method → supertype declaration |
| `CALLS` | Method→method. `in`=callers, `out`=callees. Source-ordered (`call_site_line`) |
| `EXPOSES` | Method Symbol → Route (handler exposes route) |
| `HTTP_CALLS`, `ASYNC_CALLS` | Cross-service: Client/Producer → Route |

**Composed (`direction="out"` only):** type-Symbol origin — `DECLARES.DECLARES_CLIENT` (members' HTTP clients), `DECLARES.DECLARES_PRODUCER` (async producers), `DECLARES.EXPOSES` (exposed routes). Non-static-method-Symbol origin — `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, `OVERRIDDEN_BY.DECLARES_PRODUCER`, `OVERRIDDEN_BY.EXPOSES`.

> Don't mix `DECLARES.*` and `OVERRIDDEN_BY.*` in one list. Large composed counts in `edge_summary` → raise `limit` or issue separate calls.

**Argument shapes — JSON, not stringified:** `edge_types=["CALLS"]` not `"CALLS"`; `filter={"role":"CONTROLLER"}` not nested string; `ids=["sym:…","sym:…"]` not comma-joined. Omit unneeded keys. Empty `""` is often a real filter that matches nothing.

**Node id prefixes:** Symbol `sym:`, Route `route:`/`r:`, Client `client:`/`c:`, Producer `producer:`/`p:`. Use exact ids from prior calls.

**Symbol FQNs:** `<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,<SimpleType2>,…)`. Generics erased, no spaces after commas. No-arg `()`. Constructor `#<init>(…)`.

### `neighbors` — required every time

- **`direction`** `"in"`/`"out"` (no default); **`edge_types`** non-empty list.
- **Batching:** multiple `ids` expand first; `limit`/`offset` slice the **merged** list — raise `limit` when batching.
- **`CALLS`:** `attrs.resolved=false` = external (JDK/Spring), not missing. `include_unresolved=True` (`out` only) interleaves unresolved sites; exclusive with `edge_filter`. `dedup_calls=True` collapses identical (origin, callee) pairs.
- **`edge_filter`** (only with `edge_types=['CALLS']`): `min_confidence`; `include_strategies`/`exclude_strategies`; `callee_declaring_role`/`callee_declaring_roles`/`exclude_callee_declaring_roles` (callee stereotype filter — not `filter.role`, which filters the neighbor node).
- **Cross-service edges:** read `attrs.confidence`/`attrs.match` — low confidence or `unresolved`/`phantom`/`ambiguous` = resolver signal, not ground truth.

### NodeFilter (`find`, `search.filter`, `neighbors.filter`)

For `find`, `filter` is required — `{}` = no predicates. **Strict frame:** unknown keys or inapplicable populated fields → `success=false`; invalid enums rejected at the schema layer (valid set listed).

| Applicable to | Keys |
| ------------- | ---- |
| All kinds | `microservice`, `module` |
| **symbol** only | `role`, `exclude_roles`, `annotation`, `capability`, `fqn_contains`, `symbol_kind`, `symbol_kinds` |
| **route** only | `http_method`, `path_contains`, `framework` |
| **client** only | `source_layer`, `client_kind`, `target_service`, `target_path_contains`, `http_method` |
| **producer** only | `source_layer`, `producer_kind`, `topic_contains` |

Substring fields (`fqn_contains`, `path_contains`, `target_path_contains`, `topic_contains`) match literally via `CONTAINS` — no `*`/`?`; use `search(query=…)` for ranked text.

### `resolve` — identifier lookup

**Input:** FQN/suffix, `sym:`/`route:`/`client:`/`producer:` id, `METHOD /path`, route path, client target_service, producer topic. **`hint_kind`:** optional `symbol`|`route`|`client`|`producer`.

| `status` | Action |
| -------- | ------ |
| `one` | `describe(id=node.id)` |
| `many` | pick from `candidates`, then `describe` |
| `none` | fall back to `search(query=…)` or `Grep` |

Prefer `resolve` → `describe(id=…)` over `describe(fqn=…)` when FQN may collide.

### Tool signatures

- **`search`** — `query`, `table` (`java`|`sql`|`yaml`|`all`), `hybrid` (bool), `limit` (5), `offset`, `path_contains`, optional `filter` (symbol only).
- **`find`** — `kind` (`symbol`|`route`|`client`|`producer`), **`filter`** (required), `limit` (25), `offset`.
- **`describe`** — `id` (any) or `fqn` (symbol; `id` wins). Returns node + `edge_summary`.
- **`resolve`** — `identifier`, optional `hint_kind`.

### Ontology glossary

**Roles:** `CONTROLLER` | `SERVICE` | `REPOSITORY` | `COMPONENT` | `CONFIG` | `ENTITY` | `CLIENT` | `MAPPER` | `DTO` | `OTHER`. Exclude `DTO`/`OTHER`/`MAPPER` via `exclude_roles` when tracing business logic; on `CALLS` out, `edge_filter={"exclude_callee_declaring_roles":["OTHER"]}` drops framework calls.
**Capabilities:** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.
**Symbol kinds:** `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`.
**Route frameworks:** `spring_mvc`/`webflux` (HTTP), `kafka`/`rabbitmq`/`jms`/`stream` (messaging), `feign` (client mirrors). (Route *kinds*: `http_endpoint`, `http_consumer`, `kafka_topic`, `rabbit_queue`, `jms_destination`, `stream_binding`.) **Client kinds:** `feign_method`, `rest_template`, `web_client`. **Producer kinds:** `kafka_send`, `stream_bridge_send`. **Source layers (client/producer):** `builtin`, `layer_a_meta`, `layer_b_ann`, `layer_b_fqn`, `layer_c_source`. **Match types:** `cross_service`, `intra_service`, `ambiguous`, `phantom`, `unresolved`.

---

## Recovery Playbook

**After two failed attempts on the same intent, stop and report tool, args, and response snippet.**

| Symptom | Fix |
| ------- | --- |
| `neighbors` validation error | Add both `direction` and `edge_types` |
| Empty `neighbors` | Read `describe.edge_summary`; check edge type + direction |
| Cannot find symbol | `resolve`/`search`; `find` with `fqn_contains`; fallback `Grep` |
| `find` too broad | Add `microservice`, `fqn_contains`, `path_contains`, `topic_contains` |
| Empty `search` | Try `table="all"`; `find` with `fqn_contains`; `Grep` |
| Empty across tools | Index missing/stale → `Grep`/`Glob`/`Read`; ask operator to rebuild |
| Graph vs file disagree | **Trust the file**; report stale index |
| Mixed composed families on one id | Split — type keys need type id; override keys need method id |
| `Glob`/`Grep` too broad | Narrow pattern; add directory prefix / `path_filter` |

---

## Workflow Patterns

- **"Explain feature X":** `search` → pick 1–3 hits → `describe` → `neighbors` with targeted edges → stop when answered.
- **"Where is X used?":** `resolve`/`search` → `neighbors("in", ["CALLS","INJECTS","IMPLEMENTS"])` → `Grep` fallback → report sites with file:line.
- **"Find all Y":** structural → `find(kind=…, filter={…})`; textual → `Grep`; broad → `Glob`+`Grep`. Summarize, don't dump.
- **"Trace flow A→B":** resolve both → walk `CALLS`/`EXPOSES`/`HTTP_CALLS` from A → `Grep` gaps → report with file:line.
- **"How is this configured?":** `Glob` `**/application*.yml` → `Grep` the key → `Read` sections → `search(query=…, table="yaml")` supplement.
