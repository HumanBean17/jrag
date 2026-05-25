---
name: navigate-codebase
description: Tactical navigation skill for Java microservice codebases indexed by java-codebase-rag. Uses RAG-first, graph-for-precision strategy to prevent drowning during multi-hop tracing. Prefer this over /explore-codebase for targeted questions like "how does X flow through the system", "trace the call chain for Y", or "what happens when Z is called". Teaches hypothesis-driven walking with aggressive filtering and depth discipline.
---

# /navigate-codebase — RAG-first, graph-for-precision navigation

## When to use this skill

Use `/navigate-codebase` for **targeted tracing questions**: "how does X flow through the system", "trace the call chain for Y", "what happens when Z is called", "which services does operation W touch".

Use `/explore-codebase` when you need the full operating manual: complete tool reference, edge taxonomy, argument shapes, recovery playbook, or broad exploratory analysis.

## Core strategy: RAG-first, graph-for-precision

**Always start with `search`.** Use vector search to find the relevant entry points, then use graph tools (`describe`, `neighbors`) only to verify specific relationships or fill targeted gaps. Never use graph walking as your discovery mechanism.

The graph exists to confirm structure you already have a hypothesis about — not to explore blindly.

## Tools

`search`, `find`, `describe`, `neighbors`, `resolve`.

- **`search(query, table?, hybrid?, limit?, filter?)`** — ranked chunk retrieval. `table`: `java`|`sql`|`yaml`|`all` (default `java`). `limit` default 5.
- **`find(kind, filter, limit?)`** — structured listing. `kind`: `symbol`|`route`|`client`|`producer`. `filter` is required.
- **`describe(id)`** — full node record + `edge_summary` (per-label in/out counts). 1 arg.
- **`neighbors(ids, direction, edge_types, filter?, edge_filter?, limit?)`** — one hop. `direction` and `edge_types` required. `filter` applies to the other node. `edge_filter` (CALLS only) applies to edges before pagination.
- **`resolve(identifier, hint_kind?)`** — identifier lookup. Returns `one`, `many`, or `none`.

### NodeFilter keys (for `find`, `search.filter`, `neighbors.filter`)

| Keys | Applies to |
| ---- | ---------- |
| `microservice`, `module`, `source_layer` | All kinds |
| `role`, `exclude_roles`, `annotation`, `capability`, `fqn_prefix`, `symbol_kind`, `symbol_kinds` | **symbol** |
| `http_method`, `path_prefix`, `framework` | **route** |
| `client_kind`, `target_service`, `target_path_prefix`, `http_method` | **client** |
| `producer_kind`, `topic_prefix` | **producer** |

`edge_types` and `exclude_roles` must be real JSON arrays: `["CALLS"]`, not `"CALLS"`.

### Node id prefixes

`sym:` (Symbol), `route:` / `r:` (Route), `client:` / `c:` (Client), `producer:` / `p:` (Producer).

## Forced reasoning preamble (every tool call)

Before each MCP call, output one short line:

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

Then use real JSON shapes.

## Recovery (quick reference)

| Symptom | Fix |
| ------- | --- |
| `neighbors` validation error | Add `direction` and `edge_types` explicitly |
| Empty `neighbors` | Read `describe.edge_summary`; `EXPOSES` is Symbol→Route; `HTTP_CALLS` starts from **Client** ids |
| Cannot find symbol | Try `resolve`, then `search`, then `find` with `fqn_prefix` |
| `find` returns too much | Add `microservice`, `fqn_prefix`, or `role` to filter |
| Empty `search` | Try `table="all"`; fall back to `rg` or file reads |
| Result vs open file disagree | Trust the file; index may be stale |

## The 4 navigation rules

### Rule 1: Search before you walk

Every trace starts with `search`. Form a query from the user's question and retrieve relevant nodes. Only then pivot to `describe` and `neighbors` on specific nodes you identified.

```
GOOD: search("payment authorization flow") -> pick hits -> describe(top hit) -> neighbors(targeted hop)
BAD:  resolve("PaymentService") -> neighbors(out, CALLS) -> neighbors(out, CALLS) -> ...
```

If `search` returns nothing useful, try `resolve` or `find` — but the point is to enter the graph at a relevant node, not to start walking from an arbitrary one.

### Rule 2: Always filter, never walk bare

Every `neighbors` call must include either `edge_types` (always required), a `filter`, or `exclude_roles`. Walking without filters is the primary cause of drowning.

**Roles to exclude for business logic traces:** `DTO`, `OTHER`, `MAPPER`. These produce noise — they are data carriers and infrastructure, not logic.

**Example:**
```
neighbors(ids, direction="out", edge_types=["CALLS"], filter={"exclude_roles": ["DTO","OTHER","MAPPER"]})
```

On `CALLS` `out` edges, also consider passing `edge_filter={"exclude_callee_declaring_roles": ["OTHER"]}` (separate parameter from `filter`) to drop JDK/Spring calls.

### Rule 3: Depth discipline

**2-3 hops max on CALLS before reassessing.** After 2 hops, pause and ask: "Do I have enough to answer the question?" If yes, stop. If not, form a new hypothesis about where to look and use `search` to re-enter rather than continuing the walk.

Most important information lives within 2 hops of the entry point. If you haven't found it by then, the path is likely wrong.

**Exception: cross-service boundaries.** When the question asks about cross-service flow, follow `HTTP_CALLS` or `ASYNC_CALLS` across service boundaries regardless of depth. These edges represent the actual distributed call chain and are what the user is asking about.

### Rule 4: Hypothesis-driven hops

Before each `neighbors` call, state what you expect to find. After the result, compare against your hypothesis:

- **Match** -> proceed to the next targeted hop.
- **Partial match** -> refine your hypothesis, adjust filters, take one more hop.
- **No match** -> stop walking. Re-enter via `search` with what you learned, or report what you found.

```
Hypothesis: "AssignController calls OperatorAssignmentService"
-> neighbors(AssignController, out, CALLS) -> finds 5 methods including assign()
Match -> describe(assign method) -> neighbors(assign, out, CALLS)
Hypothesis: "assign() persists via a repository"
-> finds repository.save() -> Done. Answer the question.
```

## Anti-patterns to avoid

### Open-ended neighbors loops

Never chain `neighbors(out, CALLS)` calls without a stopping condition. Each hop multiplies the surface area. After 2 hops, stop and reassess.

### Walking all edge types at once

`neighbors(id, out, ["CALLS","IMPLEMENTS","INJECTS","EXTENDS","DECLARES"])` is a drowning pattern. Use one or two edge types per call, each with a clear purpose.

### Following into utility/infrastructure services

After 2 hops of `CALLS`, you often land in repository methods, mappers, or framework utilities. These are endpoints of the trace, not continuations. Recognize when you've reached infrastructure and stop.

### Ignoring edge_summary counts

`describe` returns `edge_summary` with per-label counts. If a node has 50 CALLS out-edges, it is a hub. Do not follow all of them — use `filter.role` or `exclude_roles` to narrow to the architectural layer you care about, or pick specific callees by name.

### Not using exclude_roles

`exclude_roles` is the single most important filter for keeping traces focused. The typical business logic trace should exclude `["DTO", "OTHER", "MAPPER"]`. For controller-to-service traces, also consider excluding `["REPOSITORY"]` if you only care about orchestration.

## Quick reference

### Edge types (most common for tracing)

| Edge type | Use when | Direction for tracing |
| --------- | -------- | --------------------- |
| `CALLS` | Tracing method call chains | `out` = callees, `in` = callers |
| `EXPOSES` | Finding handler for a route | `in` on Route -> handler Symbol |
| `HTTP_CALLS` | Cross-service HTTP calls | `out` on Client -> Route |
| `ASYNC_CALLS` | Cross-service async calls | `out` on Producer -> Route |
| `IMPLEMENTS` | Finding concrete implementations | `in` on interface -> implementors |
| `INJECTS` | Finding where a type is injected | `in` on type -> injection sites |
| `OVERRIDDEN_BY` | Finding concrete method implementations | `out` on non-static method -> overriders |

### Roles (for filtering)

`CONTROLLER`, `SERVICE`, `REPOSITORY`, `COMPONENT`, `CONFIG`, `ENTITY`, `CLIENT`, `MAPPER`, `DTO`, `OTHER`.

## Worked example

User: "trace how a chat message gets persisted"

**RAG-first approach (correct):**

```
Q-class: semantic  Pick: search  Why: NL feature query
-> search(query="chat message persist save", limit=8)
   -> sym:com.bank.chat.message.service.MessageService  (SERVICE)
   -> sym:com.bank.chat.message.api.MessageController    (CONTROLLER)

Q-class: inspect   Pick: describe Why: check edge_summary on controller
-> describe(id="sym:...MessageController")
   -> edge_summary { CALLS.out: 4, EXPOSES.out: 2 }

Hypothesis: controller calls MessageService to persist

Q-class: walk      Pick: neighbors Why: trace outbound calls from controller
-> neighbors(ids="sym:...MessageController", direction="out",
             edge_types=["CALLS"], filter={"exclude_roles": ["DTO","OTHER"]})
   -> MessageService#sendMessage(...)
   -> MessageService#saveMessage(...)

Hypothesis confirmed. Two relevant methods found.

Q-class: inspect   Pick: describe Why: check sendMessage details
-> describe(id="sym:...MessageService#sendMessage(...)")
   -> source_code snippet visible, edge_summary { CALLS.out: 3 }

Hypothesis: sendMessage calls repository to persist

Q-class: walk      Pick: neighbors Why: verify repository call
-> neighbors(ids="sym:...MessageService#sendMessage(...)", direction="out",
             edge_types=["CALLS"], filter={"role":"REPOSITORY"})
   -> MessageRepository#save(...)

Hypothesis confirmed. 2 hops, answer ready.

Answer: "Chat messages enter via MessageController, which calls
MessageService#sendMessage. That method calls MessageRepository#save
to persist. 2 hops, single service."
```

**Drowning approach (what to avoid):**

```
-> resolve("MessageController")
-> neighbors(out, CALLS)                    # 4 callees, no filter
-> for each callee: neighbors(out, CALLS)   # 12 more nodes
-> for each of those: neighbors(out, CALLS) # 30+ nodes, lost
-> Agent has walked into DTO constructors, mapper helpers, Spring utilities
-> Token budget exhausted, no coherent answer
```

## Do not

- Do not answer from training data or general Java knowledge.
- Do not walk more than 2-3 CALLS hops without reassessing.
- Do not call `neighbors` without `exclude_roles` or `filter` on CALLS traces.
- Do not fabricate ids — always obtain them from `search` / `find` / `resolve`.
- Do not follow CALLS into DTO, OTHER, or MAPPER nodes during business logic traces.
- Do not walk all edge types at once — one purpose per `neighbors` call.
- Do not continue walking when you have enough evidence to answer the question.
