# JRAG CLI — Agent-Facing Command-Line Interface

**Status**: draft
**Author**: Dmitry + Computer
**Date**: 2026-07-03

---

## TL;DR

- The MCP gives agents a graph-navigation primitive (`search`, `find`, `describe`, `neighbors`). It is the right shape for a reasoning loop. The CLI is a *different product* for a different caller: an AI coding agent that speaks in names, not IDs, and needs one command per intent.
- The CLI is **not** a wrapper around the MCP. It is a named-intent surface built on the same `ladybug_queries.py` backend, designed so every common agent task is achievable in one call, without a prior resolve step.
- **`neighbors` is removed entirely.** Every edge traversal gets a named command (`callers`, `callees`, `hierarchy`, `injectors`, `dependencies`, `target`, …). No agent should ever reason about edge labels or directions.
- **Resolve-first contract.** Every command that accepts a `<query>` runs an internal locate step first. The agent passes a name, FQN, route path, or topic name. If exactly one node matches → the command runs. If ambiguous → candidates are returned and the command stops. No raw node IDs are required or accepted.
- **Same repo, new PyPI entry point `jrag`** — separate from the existing `user-rag` operator CLI. Daemon auto-starts transparently on first call; agents never manage it.
- **v1 scope**: orientation, locate, direct listings, graph traversal, file inspection, search, daemon ops. `diff-impact`, `changed`, `unreferenced`, `todos` are explicitly deferred.
- **5 PRs**: daemon + entry point, locate tier, listing tier, traversal tier, orientation + search + packaging.

---

## §1 — Frame: what is the CLI, really?

The MCP's job is to expose the raw graph shape to an LLM reasoning loop. The CLI's job is different: **give an AI coding agent one command per engineering intent, using the vocabulary the agent already has from reading code.**

An agent reading a stack trace knows `com.acme.orders.OrderController`. It does not know `sym_a3f7b9`. Making the agent call `find` first to get an ID, then pass that ID to a traversal command, is the MCP's two-step pattern translated badly into a CLI. It is wrong for this surface.

The frame is: **the jrag CLI is an intent-named command surface where every positional argument is a human-readable identifier, and every command name is an engineering question.**

This frame rules out:
- Raw node IDs as required inputs (the resolve step is always internal).
- A `neighbors` command (it encodes graph topology, not engineering intent).
- Commands that are purely operational (`refresh`, `meta`, `diagnose`) — those remain in the `user-rag` operator CLI.
- A `resolve` command (resolution is not an agent intent; it is infrastructure).

---

## §2 — Design principles

1. **One command per engineering intent.** An agent should never need two commands to answer "who calls `OrderService.save`?".
2. **Names in, names out.** Every command accepts the identifier the agent already has from code context (FQN, simple name, `GET /path`, topic name). No raw IDs.
3. **Resolve-first, fail loud on ambiguity.** If a query matches multiple nodes, return candidates and stop — never silently pick one. Agents must narrow, not guess.
4. **Full locate flag set on every traversal command.** The same `--kind`, `--java-kind`, `--role`, `--fqn-prefix` flags used to narrow a `find` result are available on every command that accepts `<query>`, as disambiguation inputs.
5. **Global flags for scope, not per-command invention.** `--service`, `--module`, `--limit`, `--offset` apply uniformly. Per-command scope flags (e.g. `--producer-in` on `topics`) are only added when the command has two orthogonal scope axes.
6. **Named commands map to named backend functions.** `jrag callers` → `find_callers`/`find_route_callers`. `jrag flow` → `trace_request_flow`. The CLI is thin extraction, not reimplementation.
7. **Structured JSON output by default.** Every command emits the same envelope schema. `agent_next_actions` (capped at 5) replaces MCP hints.
8. **`edge_summary` always present in `jrag inspect`.** This is the documented pivot from "what is this node" to "which traversal command to call next". Losing it would break the locate → inspect → walk workflow.

---

## §3 — Global flags

Every command accepts these flags. They map directly to `NodeFilter` fields and daemon routing.

```
--service <microservice>    # NodeFilter.microservice
--module <module>           # NodeFilter.module (maven module; first-class field, distinct from --service)
--limit N                   # default: 20
--offset N                  # default: 0
--index-dir <path>          # override ~/.jrag/default index
--format json|text          # default: json
```

`--module` is not a post-filter. It maps to the stored `module` attribute on every node kind, exactly as it does in `NodeFilter` in the MCP. Agents can scope to a maven module independently of microservice boundaries.

---

## §4 — Resolve contract

Applies to every command that accepts `<query>` (all traversal, inspect, and orientation commands).

```
0 matches  → error:     { "status": "not_found", "message": "No node matches '<query>'. Try: jrag find <query>" }
1 match    → proceed
2+ matches → stop:      { "status": "ambiguous", "candidates": [NodeRef, ...], "hint": "narrow with --kind or --fqn-prefix" }
```

The full locate flag set is available on every `<query>`-accepting command for disambiguation:

```
--kind symbol|route|client|producer    # node table discriminator (hint_kind in resolve_v2)
--java-kind class|interface|method|enum|record|annotation|constructor
--role controller|service|repository|entity|config|mapper|dto|component
--fqn-prefix com.acme.orders
```

These are disambiguation inputs, not traversal filters. They narrow the resolve step; they do not filter traversal results.

---

## §5 — Command surface

### Orientation

```
jrag microservices
    # list all indexed microservices with node counts per kind

jrag map [--service svc] [--module mod]
    # structural density overview: node counts per kind per service/module

jrag conventions [--service svc]
    # auto-detected architectural patterns from the graph (dominant roles, framework)

jrag overview <microservice|route-path|topic>
    # orientation bundle depending on target type:
    #   microservice → connection summary + controller/endpoint count + Feign client list + entity list + scheduled-job list
    #   route        → flow from entry + all downstream callers/producers
    #   topic        → producers list + consumers list
```

### Locate

`find` accepts a positional query OR pure flags. Same resolve contract as traversal commands.
If the query resolves to exactly one node the full node record is returned. If ambiguous, candidates are returned.

```
jrag find [<query>]
    --kind symbol|route|client|producer
    --java-kind class|interface|method|enum|record|annotation|constructor
    --role controller|service|repository|entity|config|mapper|dto|component
    --exclude-role <role>[,role]
    --capability scheduled-task|message-listener|http-client|message-producer|exception-handler
    --fqn-prefix com.acme.orders
    --annotation <name>
    --http-method GET|POST|PUT|DELETE|PATCH
    --path-prefix /api/
    --target-path-prefix /items/
    --target-service <name>
    --client-kind feign|rest-template|web-client
    --producer-kind kafka|stream-bridge
    --topic-prefix order.
    --framework spring-mvc|webflux
    --source-layer builtin|layer-a|layer-b-ann|layer-b-fqn|layer-c
    --fuzzy          # string-fuzzy: exact → prefix → contains on the identifier string
                     # NOT semantic similarity; use jrag search for that
```

### Direct listings

All nodes of a kind, no query. All accept global `--service`, `--module`, `--limit`, `--offset`.

```
jrag routes     [--http-method GET|POST|PUT|DELETE|PATCH] [--path-prefix /api/] [--framework spring-mvc|webflux]
jrag clients    [--target-service <name>] [--client-kind feign|rest-template|web-client]
jrag producers  [--topic-prefix order.]
jrag topics     [--producer-in <svc>] [--consumer-in <svc>]
jrag jobs
jrag listeners  [--topic-prefix order.]
jrag entities
```

### Graph traversal

All traversal commands share the resolve contract from §4. The full locate flag set (`--kind`, `--java-kind`, `--role`, `--fqn-prefix`) is available on every command for disambiguation.

```
jrag trace <query>      [--kind ...] [--java-kind ...] [--role ...] [--fqn-prefix ...]
                        [--depth 2] [--follow-calls] [--max-stage N]
    # service decomposition trace: stages of a service call chain
    # backend: trace_flow()

jrag flow <query>       [--fqn-prefix ...]
                        [--max-hops 5]
    # end-to-end request flow: entry route → all downstream hops across service boundaries
    # query must resolve to a Route node
    # backend: trace_request_flow()

jrag impact <query>     [--kind ...] [--java-kind ...] [--role ...] [--fqn-prefix ...]
                        [--depth 2]
    # reverse reachability: what breaks if this node changes
    # backend: impact_analysis(); --service is a client-side post-filter (backend takes no microservice param)

jrag callers <query>    [--kind symbol|route] [--fqn-prefix ...]
                        [--depth N] [--min-confidence 0.8]
    # dispatches by resolved kind:
    #   Symbol → find_callers()       (CALLS-in, intra-service)
    #   Route  → find_route_callers() (HTTP_CALLS + ASYNC_CALLS in)

jrag callees <query>    [--kind ...] [--java-kind ...] [--role ...] [--fqn-prefix ...]
                        [--exclude-role OTHER,DTO]
                        [--min-confidence 0.8]
                        [--include-external]
                        [--depth N]
    # direct callees of a symbol (CALLS-out)
    # --exclude-role maps to EdgeFilter.exclude_callee_declaring_roles

jrag hierarchy <query>  [--kind ...] [--java-kind ...] [--fqn-prefix ...]
                        [--depth N]
    # full inheritance tree, both directions: EXTENDS + IMPLEMENTS in and out

jrag implementations <query>   [--fqn-prefix ...] [--capability ...]
    # interface → all implementing classes (IMPLEMENTS-in)
    # backend: find_implementors()

jrag subclasses <query>        [--fqn-prefix ...]
    # class → all subclasses (EXTENDS-in)
    # backend: find_subclasses()

jrag overrides <query>         [--fqn-prefix ...]
    # method → what it overrides (OVERRIDES-out, dispatch UP to superclass declaration)

jrag overridden-by <query>     [--fqn-prefix ...]
    # method → what overrides it (OVERRIDDEN_BY-out, dispatch DOWN to concrete implementations)

jrag injectors <query>         [--fqn-prefix ...]
    # bean type → who injects it (INJECTS-in)
    # backend: find_injectors()

jrag dependencies <query>      [--fqn-prefix ...]
    # bean/component → what it injects (INJECTS-out)

jrag target <query>            [--kind ...] [--fqn-prefix ...]
    # client or producer → the route or topic it calls
    # HTTP client: HTTP_CALLS-out → Route
    # Kafka/StreamBridge producer: ASYNC_CALLS-out → Route/topic

jrag connection <microservice>
                        [--inbound] [--outbound] [--both]
                        [--http-method ...] [--target-service ...]
    # cross-service connectivity map: who calls this service / who this service calls
    # first positional arg is a microservice name, not a resolve-first query
```

### File inspection

```
jrag outline <file>
    # class/method structure of a source file

jrag imports <file>
    # imports in a file, with resolved graph node references where available
```

### Inspection & search

```
jrag inspect <query>    [--kind ...] [--java-kind ...] [--role ...] [--fqn-prefix ...]
    # full node record + edge_summary (all labels, in/out counts, including composed keys)
    # same resolve contract as traversal commands
    # edge_summary is required; it is the pivot from inspect to the next traversal call

jrag search <query>
    --table java|sql|yaml|all
    --hybrid
    --path-contains <substring>
    --role ... --exclude-role ...
    --annotation ...
    --capability ...
    --fqn-prefix ...
    --java-kind ...
    # semantic/vector similarity search — use when find returns nothing
    # does NOT accept --fuzzy; it is already semantic by design
```

### Daemon

```
jrag daemon stop | status | list
    # ops only; auto-start is transparent on first CLI call
    # status reports index freshness and loaded index count
```

---

## §6 — Output envelope

All commands return the same JSON envelope.

```json
{
  "status": "ok | ambiguous | not_found | error | truncated",
  "nodes": { "<id>": { ...all node fields... } },
  "edges": [
    { "from": "<id>", "to": "<id>", "label": "CALLS", "confidence": 0.9 }
  ],
  "root": "<id>",
  "agent_next_actions": [
    "jrag callers OrderService.save",
    "jrag inspect OrderService"
  ],
  "warnings": [],
  "truncated": false,
  "confidence": 0.87,
  "file_location": "OrderController.java:42"
}
```

- `agent_next_actions` is capped at 5. It replaces MCP structured hints.
- Listing commands (`routes`, `clients`, etc.) omit `root` and `edges`.
- `truncated: true` is set whenever the result set was capped by `--limit`. This is the agent's signal that `--offset` pagination is needed.
- `edge_summary` appears only in `jrag inspect` output, nested under the node record. It covers all incident edge labels including composed keys (`DECLARES.EXPOSES`, `OVERRIDDEN_BY.DECLARES_CLIENT`, etc.).

---

## §7 — Use-case re-walk

Simulated agent: AI coding agent, ~15-service Spring Boot / Kafka / Feign fleet, 50k+ LoC services. Sources: `review_usecases.md` (10 tasks), session use-case analysis.

| # | Use case | Commands | Chain |
|---|---|---|---|
| UC1 | Bug: "orders after 6pm don't trigger inventory updates" — find the producer path | 2 | `jrag flow "POST /orders"` → `jrag inspect <kafka-producer-node>` |
| UC2 | Safe refactor: add parameter to `OrderService.calculateTotal` — get blast radius | 2 | `jrag impact OrderService.calculateTotal --role service` → `jrag callers OrderService.calculateTotal` |
| UC3 | Safe refactor: check if method implements an interface (affects blast radius) | 2 | `jrag inspect OrderService.calculateTotal` → `jrag implementations PricingStrategy` (if edge_summary shows IMPLEMENTS) |
| UC4 | New feature: find existing Feign multi-service join pattern to copy | 3 | `jrag find --kind client --target-service inventory-service --service reporting-service` → `jrag outline ReportingController.java` → `jrag trace "ReportingController#joinEndpoint" --follow-calls` |
| UC5 | Incident: NPE from `InventoryClient#checkAvailability` in payment-service — trace cross-service | 3 | `jrag inspect "InventoryClient#checkAvailability" --service payment-service` → `jrag target "InventoryClient#checkAvailability"` → `jrag callers "InventoryClient#checkAvailability"` |
| UC6 | Onboarding to reporting-service (cold start) | 2 | `jrag overview reporting-service` → `jrag routes --service reporting-service` |
| UC7 | Kafka topology: who produces and consumes `order.created` | 2 | `jrag overview order.created` → `jrag trace <consumer-entrypoint> --follow-calls` (per consumer) |
| UC8 | PR review: 3 files changed in order-service — blast radius | 3 | `jrag impact OrderController --service order-service --depth 3` + `jrag impact OrderService --service order-service` + `jrag impact OrderRepository --service order-service` |
| UC9 | Scheduled job audit: all `@Scheduled` jobs fleet-wide | 1 | `jrag find --capability scheduled-task` (no `--service` = fleet-wide) |
| UC10 | Security review: endpoints missing `@PreAuthorize` | 2 | `jrag routes` (fleet-wide) → inspect each result for annotation fields; `--annotation @PreAuthorize` on `jrag find --kind symbol --capability http-client` as secondary cross-check |
| UC11 | Architecture conventions: what patterns does payment-service use? | 1 | `jrag conventions --service payment-service` |
| UC12 | Find all Feign clients calling inventory-service across the fleet | 1 | `jrag clients --target-service inventory-service` |
| UC13 | Find the route handler for `GET /orders/{id}` | 1 | `jrag find "GET /orders/{id}" --kind route` |
| UC14 | Inheritance tree: full hierarchy of `AbstractOrderProcessor` | 1 | `jrag hierarchy AbstractOrderProcessor` |
| UC15 | Dependency injection: what does `OrderService` inject? | 1 | `jrag dependencies OrderService --role service` |
| UC16 | Where does `KafkaOrderProducer` actually publish to? | 1 | `jrag target KafkaOrderProducer` |
| UC17 | Fleet-wide: list all Kafka topics, filter by consumer service | 1 | `jrag topics --consumer-in inventory-service` |
| UC18 | Cross-service map: what calls payment-service inbound? | 1 | `jrag connection payment-service --inbound` |
| UC19 | Which methods does `OrderController` override from a parent? | 1 | `jrag overrides OrderController --java-kind class` (returns all OVERRIDES-out edges for all member methods) |
| UC20 | Structural size sanity before touching a service | 1 | `jrag map --service order-service` |

**Summary:** 15 of 20 use cases resolve in 1–2 commands. The 3-command cases (UC4, UC5, UC8) all involve genuine multi-step investigation, not accidental CLI friction. No use case requires a prior `find` call just to get an ID before running the real command — the resolve-first design eliminates that pattern entirely.

**Awkward cases:**
- UC10 (absent annotation): the CLI has no negative filter (`--without-annotation`). Fleet-wide routes listing + client-side annotation inspection is the only path. This is a known gap, explicitly deferred (see §9).
- UC8 (multi-symbol impact): requires 3 separate `jrag impact` calls because there is no multi-identifier input mode. Acceptable for v1.

---

## §8 — What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Negative/absence filters (`--without-annotation`, `--unreferenced`) | Non-trivial backend query shape; not addressed by existing `ladybug_queries.py` functions; deferred post-v1 |
| `diff-impact` (git diff → affected symbols) | No backend git/diff integration exists; full complexity deferred |
| `changed` (git diff → touched symbols) | Same — git integration is a future milestone |
| `todos` / `unreferenced` listing commands | Not needed for v1 agent workflows; can be added without API breaks |
| Batch/multi-identifier input | Each command takes one resolved node; batching is N sequential calls for v1 |
| `drift` detection | Explicitly a later milestone |
| Raw node IDs as primary input | Agents never construct internal IDs; the resolve contract covers all identifier forms |
| Standalone `jrag resolve` command | Resolution is infrastructure, not an agent intent; implicit in every command |
| `jrag diagnose`, `jrag refresh`, `jrag meta` | Operator commands; they remain in the `user-rag` CLI |
| `--without-annotation` on routes | Route nodes have no annotation field in the graph schema |
| `--caller-type intra\|http\|async` | Replaced by kind-based dispatch in `jrag callers`; cleaner and unambiguous |

---

## §9 — Migration plan — 5 PRs

**PR-JRAG-1**: Daemon + entry point
- Add `jrag` PyPI entry point; implement unix-socket daemon with JSON-Lines protocol; multi-index registry; transparent auto-start on first CLI call; `jrag daemon stop | status | list`.
- Test: daemon starts, accepts a ping, reports status, stops cleanly.

**PR-JRAG-2**: Locate tier
- Implement `jrag find` with all flags from §5; `jrag inspect` with full `edge_summary`; resolve-first shared library used by all subsequent commands.
- Test: find by FQN exact match, by `--role`, by `--capability`; inspect returns edge_summary with composed keys; ambiguous query returns candidates.

**PR-JRAG-3**: Listing tier
- Implement `jrag routes`, `jrag clients`, `jrag producers`, `jrag topics`, `jrag jobs`, `jrag listeners`, `jrag entities` with their respective flags and global flags.
- Test: each listing command returns nodes of the correct kind; `--service` and `--module` scope correctly; `truncated: true` fires when limit is hit.

**PR-JRAG-4**: Traversal tier
- Implement all traversal commands: `trace`, `flow`, `impact`, `callers`, `callees`, `hierarchy`, `implementations`, `subclasses`, `overrides`, `overridden-by`, `injectors`, `dependencies`, `target`, `connection`.
- Implement `jrag outline` and `jrag imports`.
- Test: each command exercises its named backend function; resolve ambiguity stops traversal; `callers` dispatches correctly to `find_callers` vs `find_route_callers` by resolved kind.

**PR-JRAG-5**: Orientation tier + search + packaging
- Implement `jrag microservices`, `jrag map`, `jrag conventions`, `jrag overview`; implement `jrag search` with all flags; finalize PyPI packaging, README, `agent_next_actions` generation.
- Test: `overview` returns correct bundle per target type; `search --hybrid` calls the BM25+vector path; `map` returns non-empty node counts for every indexed service.

---

## §10 — Decisions taken (no longer open)

1. **Same repo, new `jrag` PyPI entry point.** The CLI lives in `HumanBean17/java-codebase-rag`, not a fork. A separate entry point avoids collision with the existing `user-rag` operator CLI.
2. **`neighbors` is removed entirely.** Every edge traversal gets a named command. No agent should reason about `direction` or `edge_types`.
3. **Resolve-first: `<query>` not `<id>`.** All traversal and inspect commands take a human-readable query. The resolve step is internal and invisible. Raw node IDs are never required.
4. **Full locate flag set on all `<query>`-accepting commands.** `--kind`, `--java-kind`, `--role`, `--fqn-prefix` are disambiguation inputs, not traversal filters. They narrow the resolve step.
5. **`--in` renamed to `--service`.** `--in` is a shell keyword and collides with the "intra" ontology meaning. `--service` maps to `NodeFilter.microservice`.
6. **`--module` is a first-class global flag.** It maps to `NodeFilter.module` (maven module), distinct from `--service`. Not a post-filter.
7. **`--symbol-kind` renamed to `--java-kind`.** Avoids the triple "kind" overload (`--kind` for node table, `--java-kind` for Java declaration type, `--role` for architectural stereotype).
8. **`connection` replaces `boundary`/`contract`/`service-map`.** `boundary` was opaque without documentation. `connection` is self-describing.
9. **`microservices` replaces `services`.** Avoids confusion with Spring's `@Service` stereotype annotation.
10. **`callers` dispatches by resolved node kind.** Symbol → `find_callers` (CALLS-in). Route → `find_route_callers` (HTTP_CALLS + ASYNC_CALLS-in). No `--caller-type` flag needed.
11. **`overrides` and `overridden-by` are two separate commands.** Direction ambiguity in a single `overrides` command would be a silent correctness risk.
12. **`injectors` covers INJECTS-in; `dependencies` covers INJECTS-out.** Both directions are first-class named commands.
13. **`target` is the command for client/producer → route resolution.** Covers `HTTP_CALLS-out` (clients) and `ASYNC_CALLS-out` (producers). No MCP equivalent existed as a named command.
14. **`diff-impact` dropped from v1.** No backend git/diff logic exists. Deferred.
15. **`changed`, `unreferenced`, `todos` dropped from v1.** Not needed for the v1 agent workflow set.
16. **`--fuzzy` on `find` is string-fuzzy, not semantic.** Exact → prefix → contains on the identifier string. Semantic similarity is `jrag search`.
17. **`flow --max-hops` not `flow --depth`.** Different semantics from `trace --depth` (stage count vs hop count); distinct flag names prevent silent wrong usage.
18. **`impact --service` is a client-side post-filter.** `impact_analysis()` takes no microservice parameter; `--service` filters the returned node set after the backend call.
19. **Daemon auto-starts transparently.** Agents never call `jrag daemon start`. The first CLI call forks the daemon if it is not running.
20. **`jrag daemon status` reports index freshness.** An agent can verify the index is current before trusting results.
21. **`agent_next_actions` capped at 5, replaces MCP structured hints.** The CLI owns the hint surface; agents do not need to interpret edge labels to decide next steps.
22. **`edge_summary` is required in `jrag inspect` output.** It must include all incident edge labels and in/out counts, including composed keys (`DECLARES.EXPOSES`, `OVERRIDDEN_BY.DECLARES_CLIENT`, etc.). Losing it would break the locate → inspect → walk pivot.
23. **`truncated: true` in output envelope when `--limit` was hit.** Agents must be able to detect partial results and use `--offset` to page.

---

## §11 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Resolve ambiguity is too frequent — agent has to narrow too often | `--fqn-prefix` + `--service` on every traversal command collapses most collisions; use-case re-walk shows 15/20 cases need 0 narrowing |
| `callers` kind-dispatch is wrong — symbol resolves to the wrong kind | `--kind symbol\|route` is an explicit override; ambiguous cases surface candidates, not wrong results |
| `impact --service` post-filter silently misleads on cross-service blast radius | Warning emitted in `warnings[]` when `--service` filters out nodes from the result: "impact analysis ran fleet-wide; results filtered to --service. Cross-service nodes excluded." |
| Daemon socket unavailable (crash, stale PID) | CLI auto-recovers: on ENOENT/ECONNREFUSED, forks a new daemon and retries once before erroring |
| `edge_summary` missing for some node kinds (e.g. route nodes) | `inspect` must surface edge_summary for all four kinds: symbol, route, client, producer. Plan must verify this against `describe_v2` behavior for each kind |
| `jrag search` without `--offset` silently truncates on large codebases | `truncated: true` in envelope; `agent_next_actions` suggests narrowing with `--fqn-prefix` or `--service` rather than pagination (semantic search results degrade past page 1 anyway) |
| `--module` scope is silently ignored if node kind does not store it | `warnings[]` emits "module filter has no effect for kind=route" when module is supplied for a kind that lacks it |

---

## Appendix A — Concrete artefact: output envelope schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema",
  "type": "object",
  "required": ["status"],
  "properties": {
    "status":             { "type": "string", "enum": ["ok", "ambiguous", "not_found", "error", "truncated"] },
    "nodes":              { "type": "object", "additionalProperties": { "type": "object" } },
    "edges": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["from", "to", "label"],
        "properties": {
          "from":       { "type": "string" },
          "to":         { "type": "string" },
          "label":      { "type": "string" },
          "confidence": { "type": "number" }
        }
      }
    },
    "root":               { "type": "string" },
    "candidates":         { "type": "array", "items": { "type": "object" } },
    "agent_next_actions": { "type": "array", "maxItems": 5, "items": { "type": "string" } },
    "warnings":           { "type": "array", "items": { "type": "string" } },
    "truncated":          { "type": "boolean" },
    "confidence":         { "type": "number" },
    "file_location":      { "type": "string", "description": "filename:line" }
  }
}
```

`edge_summary` (inspect only) is nested under the node record, not at envelope level:

```json
"edge_summary": {
  "CALLS":                       { "in": 14, "out": 3 },
  "DECLARES.EXPOSES":            { "in": 0,  "out": 2 },
  "OVERRIDDEN_BY":               { "in": 0,  "out": 1 },
  "OVERRIDDEN_BY.DECLARES_CLIENT": { "in": 0, "out": 1 }
}
```
