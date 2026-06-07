---
name: explore-codebase
description: "MUST BE USED PROACTIVELY. Universal read-only codebase exploration. Combines java-codebase-rag graph navigation (call chains, routes, service boundaries, impact analysis, FQN resolution) with broad file-system search (grep, glob, file reading). Use for any exploration: locating code, tracing dependencies, finding patterns, 'where is X', 'who calls Y', 'find all controllers', 'trace the flow from A to B'. Do NOT use when the answer is already in open context or for a single known file — read that file directly."
---

# /explore-codebase — Universal codebase exploration

Read-only exploration combining **java-codebase-rag graph navigation** with **broad file-system search**.

## When to use

Any time you need to search, locate, navigate, or explore the codebase. **Do NOT use when** the answer is already in open context or for a single known file — read that file directly.

## Core Principles

1. **Read-only.** Never edit, write, or modify any file.
2. **Smallest sufficient tool.** Pick the lightest tool that answers the question.
3. **Stop when answered.** Don't prefetch unrelated subgraphs or directories.

## Tool Inventory

### Graph tools (java-codebase-rag MCP)

`search`, `find`, `describe`, `neighbors`, `resolve`.

**Node kinds:** `Symbol` (types/methods), `Route` (HTTP/messaging entry points), `Client` (outbound HTTP), `Producer` (outbound async).
**Indexed content:** Java sources + SQL + YAML (`table`: `java`, `sql`, `yaml`, or `all`).

### File-system tools

- **Grep** — content search by pattern/regex
- **Glob** — find files by name/path pattern (`**/*.java`, `**/*Controller*.java`, `**/application*.yml`)
- **Read** — read files (`offset`/`limit` for large files)

### Other: **Bash** (read-only: `git log`, `git blame`, `ls`, `find`), **WebSearch**/**WebFetch** (external lookups)

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
| Impact of changing X? | bounded `neighbors` `in` loop with `CALLS`, `INJECTS`, … | `Grep` fallback |
| Find files matching pattern | `Glob` | `Read` |
| Search for text in files | `Grep` | `Read` |
| Who changed X and when? | Bash: `git log`/`git blame` | — |
| "How is this configured?" | `Glob` + `Grep` for config keys; `search(query=…, table="yaml")` | `Read` sections |

**Escalation:** ① Most targeted tool first → ② Fall back gracefully (graph empty → `Grep`/`Glob`) → ③ Cross-validate (graph vs file disagree → **trust the file**).

**Rules of thumb:** Structure beats vector for exact questions (`resolve`/`find`+`neighbors`); vector beats structure for fuzzy discovery (`search`); file-system beats stale index.

---

## Graph Navigation Reference (java-codebase-rag MCP)

**Ontology: 17** — if results look structurally wrong or empty across tools, the index may be missing or stale; ask the operator to rebuild.
Responses may include `hints_structured` (suggested next calls) and `advisories` — advisory only; ignore when `success` is false.

### Forced reasoning preamble (every MCP call)

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

### Workflow: locate → inspect → walk

1. **Locate** — `resolve` for identifier-shaped; `search` for NL/code fragments; `find` for structured `NodeFilter`.
2. **Inspect** — `describe(id)` for full record + `edge_summary`.
3. **Walk** — `neighbors` in a loop with explicit `direction` and `edge_types`.

### Edge taxonomy

Use these strings **verbatim** in `neighbors(..., edge_types=[...])`.

**Stored edges (one hop):**

| Edge type | Semantics |
| --------- | --------- |
| `EXTENDS`, `IMPLEMENTS`, `INJECTS` | Type wiring. `in`=dependents, `out`=dependencies |
| `DECLARES`, `DECLARES_CLIENT`, `DECLARES_PRODUCER` | Containment. `in`=owner, `out`=owned member/client/producer |
| `OVERRIDES` | Subtype method → supertype declaration |
| `CALLS` | Method→method. `in`=callers, `out`=callees. Source-ordered (`call_site_line`) |
| `EXPOSES` | Method Symbol → Route (handler exposes route) |
| `HTTP_CALLS`, `ASYNC_CALLS` | Cross-service: Client/Producer → Route |

**Composed edges — type Symbol origin (`direction="out"` only):**

`DECLARES.DECLARES_CLIENT` — members' HTTP clients | `DECLARES.DECLARES_PRODUCER` — members' async producers | `DECLARES.EXPOSES` — members' exposed routes

**Composed edges — non-static method Symbol origin (`direction="out"` only):**

`OVERRIDDEN_BY` — concrete overrider methods | `OVERRIDDEN_BY.DECLARES_CLIENT` | `OVERRIDDEN_BY.DECLARES_PRODUCER` | `OVERRIDDEN_BY.EXPOSES`

> Do not mix `DECLARES.*` and `OVERRIDDEN_BY.*` in one `edge_types` list. When `edge_summary` shows large composed counts, raise `limit` or issue separate calls per key.

### Argument shapes

**JSON, not stringified JSON:** `edge_types=["CALLS"]` not `"CALLS"`; `filter={"role":"CONTROLLER"}` not nested string; `ids=["sym:…","sym:…"]` not comma-joined. Omit keys you don't need. Empty string `""` is a real filter that matches nothing.

**Node id prefixes:** Symbol `sym:`, Route `route:`/`r:`, Client `client:`/`c:`, Producer `producer:`/`p:`. Use exact ids from previous calls.

**Symbol FQNs:** `<package>.<Type>[.<NestedType>]#<methodName>(<SimpleType1>,<SimpleType2>,…)`. Generics erased, no spaces after commas. No-arg: `()`. Constructor: `#<init>(…)`.

### `neighbors` — required every time

- **`direction`**: `"in"` or `"out"` (no default). **`edge_types`**: non-empty list.
- **Batching:** multiple `ids` expand first; `limit`/`offset` slice the **merged** edge list — raise `limit` when batching.
- **`CALLS` edges:** `attrs.resolved=false` = external (JDK/Spring), not missing. **`include_unresolved=True`** (`out` only) interleaves unresolved call sites; mutually exclusive with `edge_filter`. **`dedup_calls=True`** collapses identical (origin, callee) pairs.
- **`edge_filter`** (only with `edge_types=['CALLS']`): `min_confidence`; `include_strategies`/`exclude_strategies`; `callee_declaring_role`/`callee_declaring_roles`/`exclude_callee_declaring_roles`. Note: use `edge_filter.callee_declaring_role` for callee stereotype filtering, not `filter.role` which filters the neighbor node.
- **Cross-service edges:** read `attrs.confidence` and `attrs.match` — low confidence or `unresolved`/`phantom`/`ambiguous` = resolver signal, not ground truth.

### NodeFilter (`find`, `search.filter`, `neighbors.filter`)

For `find`, `filter` is required — `{}` means no predicates. **Strict frame:** unknown keys or inapplicable populated fields → `success=false`.

| Applicable to | Keys |
| ------------- | ---- |
| All kinds | `microservice`, `module` |
| **symbol** only | `role`, `exclude_roles`, `annotation`, `capability`, `fqn_prefix`, `symbol_kind`, `symbol_kinds` |
| **route** only | `http_method`, `path_prefix`, `framework` |
| **client** only | `client_kind`, `target_service`, `target_path_prefix`, `http_method` |
| **producer** only | `producer_kind`, `topic_prefix` |

No wildcards in prefix fields — use `search(query=…)` for ranked text.

### `resolve` — identifier lookup

**Input:** FQN/suffix, `sym:`/`route:`/`client:`/`producer:` id, `METHOD /path`, route path, client target_service, producer topic.
**`hint_kind`:** optional `symbol`|`route`|`client`|`producer` (narrows generators).

| `status` | Action |
| -------- | ------ |
| `one` | `describe(id=node.id)` |
| `many` | pick from `candidates`, then `describe` |
| `none` | fall back to `search(query=…)` or `Grep` |

Prefer `resolve` → `describe(id=…)` over `describe(fqn=…)` when FQN may collide.

### Tool signatures summary

- **`search`** — `query`, `table` (`java`|`sql`|`yaml`|`all`), `hybrid` (bool), `limit` (default 5), `offset`, `path_contains`, optional `filter` (symbol-applicable only).
- **`find`** — `kind` (`symbol`|`route`|`client`|`producer`), **`filter`** (required object), `limit` (default 25), `offset`.
- **`describe`** — `id` (any kind) or `fqn` (symbol only; `id` wins). Returns node + `edge_summary` (stored + composed keys).
- **`resolve`** — `identifier`, optional `hint_kind`.

### Ontology glossary

**Roles:** `CONTROLLER` | `SERVICE` | `REPOSITORY` | `COMPONENT` | `CONFIG` | `ENTITY` | `CLIENT` | `MAPPER` | `DTO` | `OTHER`.
Exclude `DTO`, `OTHER`, `MAPPER` with `exclude_roles` when tracing business logic. On `CALLS` out: `edge_filter={"exclude_callee_declaring_roles":["OTHER"]}` drops framework calls.

**Capabilities:** `MESSAGE_LISTENER`, `MESSAGE_PRODUCER`, `HTTP_CLIENT`, `SCHEDULED_TASK`, `EXCEPTION_HANDLER`.

**Symbol kinds:** `class`, `interface`, `enum`, `record`, `annotation`, `method`, `constructor`.

**Route frameworks:** `spring_mvc`, `webflux`, `kafka`, `rabbitmq`, `jms`, `stream`, `codebase_async_route`, …
**Client kinds:** `feign_method`, `rest_template`, `web_client`. **Producer kinds:** `kafka_send`, `stream_bridge_send`.
**Match types:** `cross_service`, `intra_service`, `ambiguous`, `phantom`, `unresolved`.

---

## Recovery Playbook

**After two failed attempts on the same intent, stop and report tool name, args, and response snippet.**

| Symptom | Fix |
| ------- | --- |
| `neighbors` validation error | Add both `direction` and `edge_types` explicitly |
| Empty `neighbors` | Read `describe.edge_summary`; check edge type and direction |
| Cannot find symbol | `resolve`/`search`; `find` with `fqn_prefix`; fallback `Grep` |
| `find` returns too much | Add `microservice`, `fqn_prefix`, `path_prefix`, `topic_prefix` |
| Empty `search` | Try `table="all"`; `find` with `fqn_prefix`; `Grep` directly |
| Empty results across tools | Index missing/stale → `Grep`/`Glob`/`Read`; ask operator to rebuild |
| Graph vs file disagree | **Trust the file**; report stale index |
| Mixed composed families on one id | Split calls — type keys need type id; override keys need method id |
| `Glob`/`Grep` too many results | Narrow pattern; add directory prefix or `path_filter` |
| `Grep` no results | Broaden pattern; check working directory; try alternate terms |

---

## Workflow Patterns

**"Explain feature X":** `search` → pick 1–3 hits → `describe` → `neighbors` with targeted edges → stop when answered.

**"Where is X used?":** `resolve`/`search` → `neighbors("in", ["CALLS","INJECTS","IMPLEMENTS"])` → `Grep` fallback → report all sites with file:line.

**"Find all Y":** Structural → `find(kind=…, filter={…})`. Textual → `Grep`. Broad → `Glob` + `Grep`. Summarize, don't dump.

**"Trace flow from A to B":** Resolve both → walk `CALLS`/`EXPOSES`/`HTTP_CALLS` from A → `Grep` gaps → report with file:line.

**"How is this configured?":** `Glob` for `**/application*.yml` → `Grep` for key → `Read` sections → `search(query=…, table="yaml")` supplement.
