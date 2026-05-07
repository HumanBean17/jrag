# MCP API v2 — Redesign Proposal (revised)

**Status**: draft v2 — supersedes the original draft and folds in the use-case validation
**Author**: Dmitry + Computer
**Date**: 2026-05-07
**Replaces**: prior `MCP-API-V2-REDESIGN-PROPOSE.md` and `MCP-API-V2-USE-CASES.md`

---

## TL;DR

- The current MCP API has 23 verb-first tools with overlapping responsibilities. Agents (and humans) pick the wrong one.
- The product is fundamentally a **graph navigator** for a Java codebase the agent doesn't fully understand. It is *not* a reasoning engine. The agent reasons; the MCP exposes structure.
- v2 MCP is **4 tools**: `search`, `find`, `describe`, `neighbors`. That's the entire agent-facing surface. Down from 23.
- Operational tools (`refresh`, `meta`, `tables`, `diagnose-ignore`, `analyze-pr`) move out of the MCP into a `user-rag` CLI. The agent never calls them; operators and ops scripts do.
- Trace / impact / behavioural-flow tools are removed. Multi-hop walking is the agent's job, achieved by iterating `neighbors` with explicit stop conditions in its own prompt.
- One shared `NodeFilter` schema is reused across `find`, `search`, and `neighbors`. The agent learns one filter shape and reuses it everywhere.
- Hard cutover (no aliases). v1 names go away. There are no external consumers.
- 4 PRs, ~700 LoC of test changes total, no graph schema changes.

---

## 1. Frame — what is this MCP, really?

The AMA agent is the dominant consumer (Dmitry, 2026-05-07). It serves analysts, tech support, and developers on a legacy Java microservices codebase with no docs. Their question types are stable and few:

> "How does X work?" · "Who invokes X?" · "What happens when Y?" · "Full trace for Z event?"

Each maps to the same loop:

```
LOCATE entry node(s) for the question
INSPECT what they are
WALK edges outward / inward until the agent has enough evidence to answer
```

That loop is the API. Three primitives suffice for the loop, plus one for filtered listing. The **GPS analogy** is exact:

| Phase | What the agent needs | Tool |
|---|---|---|
| Locate (NL) | "Drop me near `OrderService.assignOperator`" | `search` |
| Locate (filter) | "Give me every controller in `chat-assign`" | `find` |
| Inspect | "What is this thing?" | `describe` |
| Walk | "What roads lead away from here, in this direction, on this kind of edge?" | `neighbors` |

A GPS does not tell you where to go. It tells you **what is adjacent** and **what direction** each road heads. Multi-hop traversal — "trace the flow", "compute impact" — is the agent's reasoning, not the GPS's job. **This is the central design call of v2.** Every other choice falls out of it.

### What this rules out

- No `trace_*` tools. The agent walks via `neighbors` in a loop and decides its own stop condition.
- No `impact_*` tools. "What breaks if I change X?" is `neighbors(direction="in")` recursively, with the agent picking edge types.
- No NL "ask" tool. If an agent doesn't know where to start, that's a `search` call followed by `neighbors` — the agent's planner does the rest. Adding `ask` was scope creep masquerading as ergonomics.
- No verb-named tools (`find_callers`, `find_route_callers`, etc.). Verbs encode the agent's intent into the tool surface; we want the surface to encode the *graph shape*.

---

## 2. Design principles

1. **Noun-first, edge-type-aware.** Tools name *what you point at* and *what edges you cross*, not what high-level question you're asking.
2. **Edges over nodes.** `neighbors` returns edges (with their attributes), not bare nodes. The agent needs `confidence`, `strategy`, `match`, `mechanism`, etc. to reason.
3. **Required-by-default for hot params.** No silent fan-out. `direction` and `edge_types` on `neighbors` have **no defaults** — the agent must pick. The easy call is also the cheapest call.
4. **One filter schema, reused.** `NodeFilter` is shared across `find`, `search`, and `neighbors`. Agent learns it once.
5. **Symmetric.** Every edge type traversable in both directions. Cross-service and intra-service questions use the **same primitives** with different `edge_types`. (Intra-service is 7 of 9 edges and the dominant case.)
6. **Small defaults.** Everything paginates. `search.limit=5`, `find.limit=25`, `neighbors.limit=25`. Bigger pages on request.
7. **Description ≤ 4 lines.** If a tool needs an essay, it's the wrong abstraction.
8. **No magic.** No tool that "figures out the strategy". An LLM is already orchestrating; it doesn't need a second brain.

---

## 3. The four navigation tools

### 3.1 `search` — locate by natural language

```yaml
search(
  query: str,                    # NL or code-fragment
  table: "java" | "sql" | "yaml" | "all" = "java",
  hybrid: bool = false,          # vector + FTS fusion
  limit: int = 5,
  offset: int = 0,
  path_contains?: str,           # textual file-path filter
  filter?: NodeFilter,           # post-rank structural filter
) -> list[Hit {
  chunk_id, symbol_id?, fqn?, score, snippet, microservice, module, role
}]
```

**6 top-level args + optional shared filter.** Use case: "I have a question phrased in English, find me a starting node." Always populates `symbol_id` when the chunk is rooted in a known graph node so `search` composes directly into `describe`/`neighbors`.

### 3.2 `find` — locate by structured filter

```yaml
find(
  kind: "symbol" | "route" | "client",
  filter: NodeFilter,             # required; agents always know what they want
  limit: int = 25,
  offset: int = 0,
) -> list[NodeRef { id, kind, fqn, microservice, module, ... }]
```

**4 top-level args (filter is required).** Use case: "give me all X matching Y." Replaces `list_routes`, `list_clients`, `list_by_role`, `list_by_annotation`, `list_by_capability` in one tool. Filter keys irrelevant to the chosen `kind` are silently ignored (e.g. `path_prefix` on a `kind="symbol"` query) — agent-friendlier than strict rejection.

### 3.3 `describe` — inspect one node

```yaml
describe(
  id: str,                        # accepts symbol_id, route_id, client_id; auto-dispatched
) -> NodeRecord {
  kind: "symbol" | "route" | "client",
  ...all native node fields...,
  edge_summary: { edge_type: { in: int, out: int } }
}
```

**1 arg.** Use case: "I have an id; tell me what it is and what edges touch it so I can decide where to walk." The `edge_summary` is the agent's signpost — counts only, no node payloads.

### 3.4 `neighbors` — walk one hop

```yaml
neighbors(
  ids: str | list[str],            # batch-capable
  direction: "in" | "out",         # REQUIRED — no default
  edge_types: list[str],           # REQUIRED — no default
  limit: int = 25,
  offset: int = 0,
  filter?: NodeFilter,             # filters the *other* node
) -> list[Edge {
  origin_id, edge_type, direction,
  other: NodeRef { id, kind, fqn, microservice, module, role? },
  attrs: { confidence?, strategy?, match?, mechanism?, ... }
}]
```

**5 top-level args + optional filter.** Use case: "from here, what's adjacent across these specific edge types in this direction?" This is the workhorse. Multi-hop walks are the agent calling this in a loop.

**Why `direction` and `edge_types` are required**: the most common agent mistake is "give me everything connected to this node" — which fans out across 9 edge types and 100s of neighbours and either truncates or blows the context. Forcing the agent to specify what it's traversing (a) makes the call cheap, (b) forces the agent's prompt to be explicit about its own reasoning, (c) makes tool-call logs readable for debugging.

**Why batch ids**: questions like "all DTOs returned by these 12 controllers" become one call instead of 12. Agent prompts stay short.

---

## 4. The shared `NodeFilter` schema

```yaml
NodeFilter:
  # universal (applies to symbol | route | client)
  microservice?: str
  module?: str
  source_layer?: str             # builtin | brownfield

  # symbol-only (silently ignored on route/client)
  role?: str                     # CONTROLLER | SERVICE | REPOSITORY | ...
  exclude_roles?: list[str]
  annotation?: str               # exact annotation FQN or simple name
  capability?: str               # MESSAGE_LISTENER | SCHEDULED_TASK | HTTP_CLIENT | ...
  fqn_prefix?: str

  # route-only (silently ignored on symbol/client)
  http_method?: str              # GET | POST | ...
  path_prefix?: str
  framework?: str                # spring_mvc | codebase_async_route | ...

  # client-only
  client_kind?: str              # feign_method | rest_template | webclient | ...
  target_service?: str
  target_path_prefix?: str
  client_method?: str
```

**11 optional keys, 3 universal.** The agent learns this once and reuses it on `find`, `search.filter`, and `neighbors.filter`. JSON Schema can later add `oneOf` per-kind validation if we want strictness; v2 starts with silent-ignore for ergonomics.

---

## 5. Operational tools move to a `user-rag` CLI

The v1 MCP carried 5 operational tools (`graph_meta`, `list_code_index_tables`, `diagnose_ignore`, `analyze_pr`, `refresh_code_index`) that the AMA agent never realistically calls. Audit by call site:

| v1 tool | Real caller | In agent flow? |
|---|---|---|
| `refresh_code_index` | build pipeline, cron, operator | never — rebuilds take minutes; agent should not trigger |
| `graph_meta` | operator debugging "is the index built?" | borderline (could cite counts in answers) — YAGNI, drop from MCP |
| `list_code_index_tables` | operator | never |
| `diagnose_ignore` | human (Dmitry, last week's `**/out/**` debug) | never |
| `analyze_pr` | the **PR-triage agent**, not the AMA agent — already a CLI/bash workflow (`cursor-pr-review` skill) | never (different consumer) |

None of these belong on the AMA agent's surface. They move into a `user-rag` command-line tool — a unix-style operator's toolbelt, JSON output when piped, pretty when TTY:

| Subcommand | Purpose | Replaces |
|---|---|---|
| `user-rag refresh [--source-root DIR] [--kuzu-path DIR]` | Rebuild graph + lancedb | `refresh_code_index` |
| `user-rag meta` | Ontology version, node/edge counts, microservices | `graph_meta` |
| `user-rag tables` | LanceDB tables + row counts | `list_code_index_tables` |
| `user-rag diagnose-ignore <path>` | Trace why a path was / wasn't indexed | `diagnose_ignore` |
| `user-rag analyze-pr [--diff-file FILE]` | Map diff → changed symbols + blast radius + risk | `analyze_pr` |

Why this is the right cut:

1. **`refresh` was never safely agent-callable.** Rebuilds take minutes. CLI-only is *correct*, not just convenient.
2. **`diagnose-ignore` matches its caller.** Last week's `**/out/**` debug was a human-in-bash workflow; a CLI fits better than JSON-RPC.
3. **`analyze-pr` belongs with the PR-triage workflow,** which is already bash/`gh`/`cursor-pr-review` driven and doesn't need an MCP at all.
4. **`meta` and `tables`** are index-health inspection — operator concerns, not agent reasoning.

**Total v2 MCP surface: 4 tools.** Total CLI surface: 5 subcommands. They cover everything v1 covered, in the right place for each consumer.

---

## 6. Edge-type taxonomy (stable, no schema changes)

For agent prompts and documentation, edges are grouped:

| Group | Edges | Direction semantics |
|---|---|---|
| **Type wiring** (intra-service) | `EXTENDS`, `IMPLEMENTS`, `INJECTS` | `in` = "who depends on this type"; `out` = "what this type depends on" |
| **Containment** (intra-service) | `DECLARES`, `DECLARES_CLIENT` | `in` = "what owns this member"; `out` = "what members this owns" |
| **Method calls** (intra-service) | `CALLS` | `in` = "callers"; `out` = "callees" |
| **Service boundary** | `EXPOSES` | Symbol→Route. `in` from Route gives the handler; `out` from Symbol gives the route it handles. |
| **Cross-service** | `HTTP_CALLS`, `ASYNC_CALLS` | Symbol→Route across service boundaries. `in` from Route gives upstream callers; `out` from a producer gives the downstream Route. |

This taxonomy is the only thing the agent needs to know about the graph. It belongs in the system prompt of any agent built on this MCP.

---

## 7. Use-case validation (re-walked through final API)

Re-checking the 20 use cases from the prior draft against the tightened v2. Numbers below are MCP calls per question (lower is better, but a deep walk legitimately needs more).

| # | Question | Calls | Chain |
|---|---|---|---|
| UC1 | How is `ChatService.assignToOperator` called? | 2 | `search` → `neighbors(in, [CALLS])` |
| UC2 | What does `OrderController.createOrder` end up doing? | 2 + agent loop | `search` → `neighbors(out, [CALLS])` then recurse until role==REPOSITORY |
| UC3 | Who hits `POST /api/orders`, including from other services? | 2 | `find(route)` → `neighbors(in, [HTTP_CALLS, ASYNC_CALLS, EXPOSES])` |
| UC4 | What happens when a new chat message arrives? | agent-driven | `search` → `describe` → `neighbors` loop. Agent narrates as it walks. |
| UC5 | Feign clients in chat-assign hitting account-service? | 1 | `find(client, {microservice, client_kind, target_service})` |
| UC6 | All controllers in chat-assign? | 1 | `find(symbol, {microservice, role:CONTROLLER})` |
| UC7 | Who injects `OrderRepository`? | 2 | `search` → `neighbors(in, [INJECTS])` |
| UC8 | Full request path from `POST /api/orders` to DB | agent loop | `find(route)` → `neighbors(in, [EXPOSES])` then `neighbors(out, [CALLS])` until REPOSITORY |
| UC9 | What annotations does `PaymentService` carry? | 2 | `search` → `describe` |
| UC10 | All DTOs returned by chat-core controllers | 2 | `find(symbol, {microservice, role:CONTROLLER})` → `neighbors(ids=[...], out, [CALLS], filter:{role:DTO})` (batch form) |
| UC11 | PR added unauthorised tools? | 0 | not an MCP question (pure diff-grep) |
| UC12 | Verify PR added 4 HTTP_CALLS edges | CLI | `user-rag meta` (not navigation) |
| UC13 | New extractor produces clients in operator-api? | 1 | `find(client, {microservice})` |
| UC14 | If I change `OrderRepository.save`, what breaks? | 1 + agent loop | `search` → `neighbors(in, [CALLS, INJECTS])` recursively |
| UC15 | Rename `OrderEvent.payload` cross-service | 2 + agent loop | `search` → `neighbors(in, [CALLS, ASYNC_CALLS])`. Both edge types in one call. |
| UC16 | `find(client)` empty after rebuild — debug | CLI | `user-rag meta` → `user-rag diagnose-ignore <path>` (not navigation) |
| UC17 | Route_id from log → controller method | 1 | `neighbors(in, [EXPOSES])` |
| UC18 | What's at this file:line? | 2 | `search(path_contains)` → `describe` |
| UC19 | All Kafka producers + their topics | 2 | `find(symbol, {capability:MESSAGE_PRODUCER})` → `neighbors(ids=[...], out, [ASYNC_CALLS])` |
| UC20 | Brownfield-parsed clients | 1 | `find(client, {source_layer:brownfield})` |

**Distribution:**
- 12 of 20: **single-shot MCP** (1–2 calls)
- 5 of 20: **agent-driven MCP loop** of `neighbors` (UC2, UC4, UC8, UC14, UC15) — exactly the questions that *should* require the agent to think
- 3 of 20: **not navigation** — UC11 is pure diff-grep (no MCP / no CLI), UC12 and UC16 are CLI workflows (`user-rag meta` / `user-rag diagnose-ignore`)
- 0 of 20 require a primitive v2 doesn't have

The agent-loop cases gain *visibility* over v1: every hop is a separate tool call the agent narrates in its working memory or the doc-on-the-fly that the developer hands to a thinking agent later.

---

## 8. The cross-service vs intra-service balance

Restating because it's important: cross-service was the visible bug, intra-service is the steady-state value.

- **7 of 9 edges are intra-service or service-boundary.** Most analyst questions ("who injects X", "who calls X", "who implements X") never cross a service boundary.
- **The API is edge-type-agnostic by construction.** Same `neighbors` call, different `edge_types`. An agent walking `[CALLS]` and an agent walking `[HTTP_CALLS, ASYNC_CALLS]` use literally the same code path.
- **Cross-service multi-hop is the agent's loop.** Walking from a Kafka producer in service A → its Route in service B → that Route's handler → its outbound Client → next Route in service C is 4 `neighbors` calls. Cheap (~50ms each on Kuzu) and fully visible to the human reading the agent's trace.

---

## 9. What v2 deliberately does not do

| Question | Why we skip it |
|---|---|
| "Compute the full impact of this change" | Agent reasoning. MCP exposes edges; agent decides what counts as impact. |
| "Trace the behavioural flow for this event" | Same. `neighbors` loop. |
| "Answer this English question and tell me the answer" | Out of scope for the MCP. The AMA agent is the answerer. |
| "Show SQL queries this repository runs" | Different parser layer. |
| "Test coverage of this method" | Different data source. |
| "Git blame / history" | Different data source. |
| "Class diagrams" | Visualisation, not retrieval. |

---

## 10. Migration plan — 4 PRs, hard cutover

No external consumers. v1 names go away in PR-V2-3 with no deprecation period. Internal callers are the agent prompt + this README + tests; all updated in lockstep.

### PR-V2-1 — implement the four navigation tools
- Add `search`, `find`, `describe`, `neighbors` as new MCP tools. Existing engine code is reused — `kuzu_queries.py` already has the underlying graph walks; this PR is mostly handler glue + the shared `NodeFilter` schema.
- Implement `NodeFilter` once, reuse across all three tools.
- Add `id`-prefix dispatch in `describe` and `neighbors` so they auto-detect symbol/route/client.
- Add batch `ids` support to `neighbors`.
- v1 tools remain registered (untouched).
- **Tests**: equivalence tests for every v1 call against its v2 mapping (table in §11). Also: `neighbors` rejects calls without `direction` or `edge_types` (R5+R6).

### PR-V2-2 — operational tweaks
- `meta` adds per-edge-type counts (R3).
- `search` always populates `symbol_id` when the chunk is rooted in a known graph node.
- `describe` returns the new `edge_summary` field.
- v1 tools still registered.
- **Tests**: schema tests for new fields, integration test exercising `search → describe → neighbors` chain.

### PR-V2-3 — remove v1 navigation tools
- Delete v1 navigation tool registrations: `codebase_search`, `find_implementors`, `find_subclasses`, `find_injectors`, `find_callers`, `find_callees`, `list_routes`, `list_clients`, `find_route_handlers`, `get_route_by_path`, `find_route_callers`, `trace_request_flow`, `list_by_role`, `list_by_annotation`, `list_by_capability`, `graph_neighbors`, `impact_analysis`, `trace_flow`. (18 tools removed.)
- Update README §"Tool reference" — now lists 4 navigation tools + the 5 still-MCP-registered operational tools (these move to CLI in PR-V2-4).
- Update agent-recipe examples in README and product vision to use v2 only.
- **Tests**: full suite green; remove v1 equivalence tests from PR-V2-1 (they served their purpose); verify exactly the v2 navigation tools + still-pending operational tools registered.

### PR-V2-4 — extract operational tools into a CLI
- Create `user_rag/cli.py` (argparse or click) with subcommands `refresh`, `meta`, `tables`, `diagnose-ignore`, `analyze-pr`.
- Each subcommand reuses the same engine code today's MCP handlers call (`pr_analysis.analyze_pr_pipeline`, `_graph_meta_output`, `LayeredIgnore`, etc.) — no new logic.
- JSON output when `not sys.stdout.isatty()`, pretty-print otherwise.
- Remove the 5 operational tool registrations from `server.py`.
- Add `[project.scripts] user-rag = "user_rag.cli:main"` to `pyproject.toml`.
- Update README with a `### CLI reference` section; remove ops tools from `### Tool reference`.
- Update `cursor-pr-review` skill (or the equivalent ops-side workflow) to call `user-rag analyze-pr --diff-file /tmp/pr.diff` instead of an MCP call.
- **Tests**: CLI integration tests via `subprocess.run(['user-rag', ...])` on the bank-chat-system fixture for each subcommand; final MCP surface assertion = exactly 4 tools registered.

Total work estimate: ~700 LoC of test changes, ~500 LoC of handler + CLI code (mostly mechanical). No graph schema changes, no extraction pipeline changes, no LanceDB schema changes.

---

## 11. v1 → v2 mapping (for the equivalence tests in PR-V2-1)

| v1 tool | v2 equivalent |
|---|---|
| `codebase_search(query, ...)` | `search(query, ...)` |
| `find_implementors(fqn)` | resolve fqn → `neighbors(id, "in", ["IMPLEMENTS"])` |
| `find_subclasses(fqn)` | resolve fqn → `neighbors(id, "in", ["EXTENDS"])` |
| `find_injectors(fqn)` | resolve fqn → `neighbors(id, "in", ["INJECTS"])` |
| `find_callers(fqn)` | resolve fqn → `neighbors(id, "in", ["CALLS"])` |
| `find_callees(fqn)` | resolve fqn → `neighbors(id, "out", ["CALLS"])` |
| `list_routes(filter)` | `find(kind="route", filter=...)` |
| `list_clients(filter)` | `find(kind="client", filter=...)` |
| `find_route_handlers(route_id)` | `neighbors(route_id, "in", ["EXPOSES"])` |
| `get_route_by_path(path, method)` | `find(kind="route", filter={path_prefix, http_method})` then `describe(top_hit.id)` |
| `find_route_callers(route_id)` | `neighbors(route_id, "in", ["HTTP_CALLS", "ASYNC_CALLS"])` |
| `trace_request_flow(route_id)` | DROPPED. Agent loops `neighbors`. |
| `list_by_role(role)` | `find(kind="symbol", filter={role})` |
| `list_by_annotation(annotation)` | `find(kind="symbol", filter={annotation})` |
| `list_by_capability(capability)` | `find(kind="symbol", filter={capability})` |
| `graph_neighbors(symbol_id, ...)` | `neighbors(symbol_id, ...)` |
| `impact_analysis(symbol_id)` | DROPPED. Agent loops `neighbors`. |
| `analyze_pr(...)` | CLI: `user-rag analyze-pr` |
| `diagnose_ignore(path)` | CLI: `user-rag diagnose-ignore <path>` |
| `graph_meta()` | CLI: `user-rag meta` |
| `trace_flow(query)` | DROPPED. Agent does `search` then `neighbors` loop. |
| `refresh_code_index()` | CLI: `user-rag refresh` |
| `list_code_index_tables()` | CLI: `user-rag tables` |

---

## 12. Decisions taken (no longer open)

For the record, before split into Cursor task prompts:

1. **Hard cutover.** No aliases. v1 names go in PR-V2-3.
2. **No `ask` / `trace` / `impact` tool.** Agent reasoning, not MCP responsibility.
3. **`direction` and `edge_types` required on `neighbors`.** No defaults. Agent must specify.
4. **One shared `NodeFilter`.** Silent-ignore for kind-irrelevant keys.
5. **Edge metadata always returned** by `neighbors`. No "node-only" mode.
6. **Batch ids on `neighbors`.** Single call for fan-out questions.
7. **`id` parameter is kind-agnostic.** Internal dispatch by id-prefix; agent doesn't pass `kind`.
8. **No NL escape hatch.** The AMA agent itself is the NL layer.
9. **`analyze_pr` moves to the CLI.** Different consumer (PR-triage agent, already CLI-driven), no AMA agent need.
10. **All operational tools move to a `user-rag` CLI.** MCP becomes pure graph-navigation surface; CLI is the operator's toolbelt.

---

## 13. Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Agent prompts get longer because they describe the loop | Counter-evidence: the four-tool API + edge taxonomy fit in <200 tokens. Today's 23-tool prompt is already longer. |
| Agent over-walks (calls `neighbors` 50 times per question) | `direction` + `edge_types` are required → agent must reason explicitly each call. Default `limit=25` caps fan-out per call. |
| We lose the "single magic call" `trace_flow` ergonomic | We never had it working well — confirmed today: trace_request_flow vs trace_flow confused the author of this proposal. The "magic" was a liability. |
| Filter schema accidentally drifts between tools | One source: `NodeFilter` Pydantic model imported into all three handlers. Test asserts identity. |
| Renames break agent system prompts elsewhere | There are no other consumers (per Dmitry, 2026-05-06: "nobody uses this MCP bundle yet"). |
| Splitting cross-service trace into N `neighbors` calls is slower | Each Kuzu call ~50ms; 4-5 calls < 250ms total. Agent latency is dominated by LLM, not MCP. |

---

## Appendix A — System prompt the AMA agent should run with v2

For reference / co-design with the agent. Not part of this PR.

```
You have a graph navigator MCP for a Java microservices codebase. The graph has 3 node
kinds (Symbol, Route, Client) and 9 edge types:

  Type wiring:    EXTENDS, IMPLEMENTS, INJECTS                    [Symbol → Symbol]
  Containment:    DECLARES, DECLARES_CLIENT                       [Symbol → Symbol/Client]
  Method calls:   CALLS                                           [Symbol → Symbol]
  Service boundary: EXPOSES                                       [Symbol → Route]
  Cross-service:  HTTP_CALLS, ASYNC_CALLS                         [Symbol → Route]

Tools:
  search(query, filter?)                — locate nodes by NL/code text
  find(kind, filter)                    — locate nodes by structured filter
  describe(id)                          — full record + edge counts for one node
  neighbors(ids, direction, edge_types) — one-hop walk; REQUIRED direction + edge_types

To answer a question:
  1. LOCATE entry node(s) via search or find
  2. INSPECT what they are via describe  
  3. WALK via neighbors, narrating which edge types you follow and why
  4. STOP when you have enough evidence; do not pre-fetch
```

---

## Appendix B — What stayed unchanged from the original draft

For traceability:

- The five-primitive insight (now four — dropped `trace`, `ask` → folded into agent reasoning).
- The shared `NodeFilter` idea (now finalised with 11 keys split universal/symbol/route/client).
- The "edges over nodes" return shape for `neighbors`.
- The hard-cutover migration approach.
- The `analyze_pr` keep-as-is decision.

What changed:

- Dropped `trace`, dropped `ask`. Reframed the API as pure GPS.
- Made `direction` and `edge_types` required on `neighbors` (R5+R6).
- Confirmed `find` filter is required, no fallback to "list all".
- Batch ids on `neighbors` (R1); `source_layer` filter on clients preserved (R4).
- Operational tools moved out of MCP entirely into a `user-rag` CLI. R3 (per-edge-type counts in `meta`) now applies to the CLI subcommand, not an MCP tool.
