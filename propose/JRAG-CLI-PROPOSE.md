# JRAG CLI — Agent-Facing Command-Line Interface

**Status**: Proposal — not yet implemented.
**Author**: Dmitry + Computer
**Date**: 2026-07-03

---

## Revision summary (what changed in this pass)

This revision corrects the proposal against the actual codebase (`ladybug_queries.py`, `mcp_v2.py`, `java_ontology.py`, `server.py`, `java_codebase_rag/cli.py`, `config.py`, `pyproject.toml`). Headline changes:

- **Daemon deferred.** v1 is **in-process** (loads the index per call, like the MCP today). The unix-socket daemon is a post-v1 milestone, only if measured cold-start latency justifies it. PRs reordered so user-facing commands land first.
- **Naming fixes** (the `--help` is the agent's only documentation — names must be guessable):
  - `injectors` → **`dependents`** (keep `dependencies`); symmetric actor/target pair matching `callers`/`callees`.
  - `target` → **folded into `callees`** (a client *calls* a route, a producer *calls* a topic; `callees` already means "what this calls"). `callees` now dispatches by kind, mirroring `callers`. Standalone alternative `destination` is recorded as an open question.
  - `trace` → **`decompose`** (resolves the `trace`/`flow` collision — "trace" conventionally means an end-to-end distributed trace, which is `flow`'s job; `trace` actually returns a static role-waterfall decomposition). *(Confirmed.)*
- **`find` stays flat, not split.** Real fix is kind-inference + hard-error on flag/kind contradiction + `--help` grouped by kind. `--target-service` → **`--calls-service`** (kills a one-hyphen collision with global `--service`).
- **Factual corrections:** the operator CLI is `java-codebase-rag` (there is no `user-rag`); MCP has **5** tools incl. `resolve` (the CLI's resolve-first contract *is* `resolve` internalized); `--index-dir` defaults to `<source_root>/.java-codebase-rag` (`JAVA_CODEBASE_RAG_INDEX_DIR`), not an invented `~/.jrag`; `diff-impact`/`changed` are not "no backend" — the operator `analyze-pr` already does diff-based blast radius; `flow` outbound is intra-service only (cross-service is inbound); enum storage is `UPPER_SNAKE` (CLI normalizes); `OVERRIDDEN_BY` is a virtual key, not a stored edge; `NodeFilter` has no `kind` field.
- **Added §14 — token efficiency.** Output size is a first-class constraint for an agent-facing CLI (every byte enters the context window): per-command default field projection, `--brief`/`--fields`/`--count`, fan-out-scaled limits, de-dup, lean envelope, and a token-budget test assertion.
- **Default output format flipped to text** (JSON opt-in). JSON-default conflicted with the token-efficiency principle; text-default matches `gh`/`kubectl` and minimizes context-window cost. The envelope remains the canonical schema (Appendix A).
- **Text-rendering hardened via 3-agent adversarial review** (grounded in the backend). §6 now specifies: tiered endpoint disambiguation (simple name → `name @service` → FQN), direction conventions (`hierarchy` tree `↑`/`↓`, `connection` `inbound:`/`outbound:`, multi-hop `d=N`), root pinning, CALLS-family-only confidence, zero-vs-`not_found` distinction, candidate `reason` rendering, ASCII delimiters. Appendix A corrected: dropped phantom envelope-level `confidence`; `truncated` specified via +1-fetch (PR-JRAG-1); candidates annotated (cap-at-10, no file/score); edge `confidence` noted CALLS-family-only from `attrs`.
- **Install / skill / subagent integration + refactor verdicts** (grounded in the installer and the service layer). Added **PR-JRAG-5** (agent host integration: `Surface` dimension on `HostConfig`, a global MCP-vs-CLI wizard step, a CLI-flavored skill+subagent, an `ArtifactManifest`, a marker-file `detect_configured_hosts` fix, surface-conditional `resolve_mcp_command`) and **two prep refactors** (PR-JRAG-0a single-source shipped artifacts; PR-JRAG-0b `resolve_v2`→`resolve_service.py` extraction to kill the one duplication trap). Corrected the transport-edge inaccuracy: `neighbors_v2`'s generic flat-label path already reaches `:Route`/topic — `callees <client|producer>` composes `resolve_v2`+`neighbors`, **no new query for v1**. Confirmed there is no smeared-logic problem (`mcp_v2.py` imports zero MCP SDK); the CLI builds on the existing `LadybugGraph`-direct precedent (`pr_analysis.py`).

---

## TL;DR

- The MCP gives agents a graph-navigation primitive (`search`, `find`, `describe`, `neighbors`, **`resolve`** — five tools). It is the right shape for a reasoning loop. The CLI is a *different product* for a different caller: an AI coding agent that speaks in names, not IDs, and needs one command per intent.
- The CLI is **not** a wrapper around the MCP. It is a named-intent surface built on the same `LadybugGraph` backend (`ladybug_queries.py`), designed so every common agent task is achievable in one call, without a prior resolve step. It **internalizes the MCP `resolve` tool** (its `one`/`many`/`none` contract becomes the CLI's resolve-first step).
- **`neighbors` is removed entirely.** Every edge traversal gets a named command (`callers`, `callees`, `hierarchy`, `dependents`, `dependencies`, `decompose`, …). No agent should ever reason about edge labels or directions.
- **Resolve-first contract.** Every command that accepts a `<query>` runs an internal locate step first. The agent passes a name, FQN, route path, or topic name. If exactly one node matches → the command runs. If ambiguous → candidates are returned and the command stops. No raw node IDs are required or accepted.
- **Same repo, new PyPI entry point `jrag`** — separate from the existing `java-codebase-rag` operator CLI. **v1 is in-process** (no daemon); the index is loaded per call, reusing the operator's index directory.
- **v1 scope**: orientation, locate, direct listings, graph traversal, file inspection, search, and a lightweight `status`. `diff-impact`, `changed`, `unreferenced`, `todos`, and the **daemon** are explicitly deferred.
- **4 command PRs + PR-JRAG-5 (agent host integration) + 2 prep refactors**: locate, listing, traversal, orientation+search+packaging, then install branching / skill / subagent. Daemon is a separate post-v1 milestone.

---

## §1 — Frame: what is the CLI, really?

The MCP's job is to expose the raw graph shape to an LLM reasoning loop (`search` / `find` / `describe` / `neighbors` / `resolve`). The CLI's job is different: **give an AI coding agent one command per engineering intent, using the vocabulary the agent already has from reading code.**

An agent reading a stack trace knows `com.acme.orders.OrderController`. It does not know `sym_a3f7b9`. Making the agent call `find` first to get an ID, then pass that ID to a traversal command, is the MCP's two-step pattern translated badly into a CLI. It is wrong for this surface.

The frame is: **the jrag CLI is an intent-named command surface where every positional argument is a human-readable identifier, and every command name is an engineering question.**

This frame rules out:
- Raw node IDs as required inputs (the resolve step is always internal — it *is* `resolve_v2`, the existing resolve pipeline, called directly as a transport-agnostic function).
- A `neighbors` command (it encodes graph topology, not engineering intent).
- Commands that are purely operational (`init`, `increment`, `reprocess`, `meta`, `analyze-pr`, `diagnose-ignore`) — those remain in the **`java-codebase-rag`** operator CLI.
- A standalone `resolve` command (resolution is not an agent intent; it is infrastructure, already implicit in every command).

---

## §2 — Design principles

1. **One command per engineering intent.** An agent should never need two commands to answer "who calls `OrderService.save`?".
2. **Names in, names out.** Every command accepts the identifier the agent already has from code context (FQN, simple name, `GET /path`, topic name). No raw IDs.
3. **Resolve-first, fail loud on ambiguity.** If a query matches multiple nodes, return candidates and stop — never silently pick one. Agents must narrow, not guess. (This is the MCP `resolve` contract, surfaced as a CLI guarantee.)
4. **Disambiguation flags on every traversal command.** `--kind`, `--java-kind`, `--role`, `--fqn-prefix` narrow the resolve step on any command that accepts `<query>`.
5. **Global flags for scope, not per-command invention.** `--service`, `--module`, `--limit`, `--offset` apply uniformly. Per-command scope flags (e.g. `--consumer-in` on `topics`) are only added when the command has two orthogonal scope axes.
6. **Named commands map to named backend functions where one exists — and say so when they don't.** `jrag callers` → `find_callers`/`find_route_callers`. `jrag decompose` → `trace_flow`. `jrag flow` → `trace_request_flow`. `jrag impact` → `impact_analysis`. `jrag callees` for a client/producer composes `resolve_v2` + `neighbors_v2` (the generic flat-label path already reaches `:Route`/topic — **no new query for v1**); `jrag dependencies` (INJECTS-out) composes `neighbors(direction="out", edge_types=["INJECTS"])`. A dedicated `LadybugGraph` method for the transport edge is post-v1 polish. Flagged honestly in §9, not hidden behind "thin extraction."
7. **Compact text output by default; JSON envelope is the canonical schema.** Every command's data follows one envelope model (Appendix A). The default rendering is compact text — token-efficient for agent context windows; `--format json` emits the envelope verbatim for structured or pipeline use. `agent_next_actions` (capped at 5) replaces the MCP `StructuredHint` surface.
8. **`edge_summary` always present in `jrag inspect`.** This is the documented pivot from "what is this node" to "which traversal command to call next". Losing it would break the locate → inspect → walk workflow. (Verified: `describe_v2` produces `edge_summary` for **all four** kinds — symbol, route, client, producer — so this is safe.)
9. **`--help` is the spec.** Because agents discover the surface by reading help, command/flag names must be guessable and grouped, and inapplicable flag combinations must error loudly, not silently misbehave.

---

## §3 — Global flags

Every command accepts these flags. They map directly to `NodeFilter` fields and index resolution.

```
--service <microservice>    # NodeFilter.microservice
--module <module>           # NodeFilter.module (maven module; first-class, distinct from --service)
--limit N                   # default: 20
--offset N                  # default: 0
--index-dir <path>          # default: <source_root>/.java-codebase-rag  (see below)
--format text|json          # default: text (token-efficient); json emits the canonical envelope
--brief                     # name+fqn+one discriminator only (see §14)
--fields <a,b,c>            # opt-in field projection (see §14)
--count | --exists          # scalar result instead of node records (see §14)
```

**Index resolution reuses the operator's index, it does not invent a new one.** `--index-dir` defaults through the **same** resolver the operator CLI uses (`config.py:_resolve_index_dir_path`): explicit `--index-dir` → `JAVA_CODEBASE_RAG_INDEX_DIR` env → `index_dir` in `.java-codebase-rag.yml` → `<source_root>/.java-codebase-rag`. Project discovery walks up from cwd to find `.java-codebase-rag.yml` / the index dir. There is **no** `~/.jrag` directory and no `JRAG_*` env var — the CLI reads exactly the index that `java-codebase-rag init` built.

`--module` is not a post-filter. It maps to the stored `module` attribute on every node kind, exactly as it does in `NodeFilter` in the MCP. Agents can scope to a maven module independently of microservice boundaries.

**Where `--service` is a real backend filter vs. a post-filter varies by command** (see §10): commands whose backend takes a `microservice` param (`callers`, `callees`, `implementations`, `subclasses`, `dependents`, …) push it down; `impact` does not take one, so `--service` is a client-side post-filter there (with a warning).

---

## §4 — Resolve contract

Applies to every command that accepts `<query>` (all traversal, inspect, and orientation commands). The CLI runs `resolve_v2` internally and maps its `one`/`many`/`none` statuses onto the envelope:

```
resolve_v2 status   envelope status     behavior
------------------  ------------------  ---------------------------------------------
"one"               ok                  proceed with the single resolved node
"many"              ambiguous           stop, return candidates[], hint to narrow
"none"              not_found           stop, message: "No node matches '<query>'.
                                            Try: jrag search <query>"
```

`<query>` accepts any identifier form `resolve_v2` understands: simple name, FQN, `GET /orders/{id}` (method+path), topic name. The full disambiguation flag set is available on every `<query>`-accepting command:

```
--kind symbol|route|client|producer    # node table discriminator (hint_kind in resolve_v2)
--java-kind class|interface|method|enum|record|annotation|constructor
--role controller|service|repository|entity|config|mapper|dto|component|client|other
--fqn-prefix com.acme.orders
```

These are **disambiguation inputs**, not traversal-result filters — they narrow the resolve step only. (Filtering traversal *results* by role is a separate, deferred concern; see §8.)

**Enum casing.** Roles and capabilities are stored `UPPER_SNAKE` (`CONTROLLER`, `SCHEDULED_TASK`). The CLI accepts **either** form on input and normalizes (case-insensitive + kebab↔underscore), so `--role controller`, `--role Controller`, and `--role CONTROLLER` are equivalent. This normalization is net-new code in the CLI; no such layer exists today.

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

jrag overview <microservice|route-path|topic> [--as microservice|route|topic]
    # orientation bundle depending on target type:
    #   microservice → connection summary + controller/endpoint count + Feign client list + entity list + scheduled-job list
    #   route        → flow from entry + all downstream callers/producers (intra-service)
    #   topic        → producers list + consumers list
    # --as escapes polymorphic inference when a name could match >1 type
```

### Locate

`find` accepts a positional query OR pure flags. It is the **cross-kind structured-filter escape hatch** — the `gh search` to the listing tier's `gh issue list`. For "list all of one kind" prefer the listing commands below over `find --kind <k>`.

**Kind inference + hard-error (the real fix for "too many flags"):** ~13 of `find`'s flags apply to exactly one kind. When `--kind` is omitted, the CLI infers it from the domain flags passed (`--http-method`⇒route, `--client-kind`⇒client, `--producer-kind`⇒producer, `--role`/`--java-kind`/`--capability`⇒symbol). A domain flag that **contradicts** an explicit `--kind` is a hard error (`status: error`, naming the pair). Inapplicable flags are never silently ignored — silent-ignore on a green `status: ok` is the worst failure mode for an agent.

```
jrag find [<query>]

GLOBAL SCOPE:
  --service <name>            scope to microservice
  --module <name>             scope to maven module
  --limit N --offset N

APPLIES TO ALL KINDS:
  --kind symbol|route|client|producer
  --fqn-prefix <pkg>
  --fuzzy                     exact → prefix → contains on the identifier string
                              (NOT semantic similarity; use jrag search for that)
  --annotation <name>

SYMBOL ONLY (kind=symbol):
  --java-kind class|interface|method|enum|record|annotation|constructor
  --role controller|service|repository|entity|config|mapper|dto|component|client|other
  --exclude-role <role>[,<role>]
  --capability scheduled-task|message-listener|http-client|message-producer|exception-handler
  --framework spring-mvc|webflux
  --source-layer <layer>      # see legend: builtin / layer-a / layer-b-ann / layer-b-fqn / layer-c

ROUTE ONLY (kind=route):
  --http-method GET|POST|PUT|DELETE|PATCH
  --path-prefix /api/
  --framework spring-mvc|webflux

CLIENT ONLY (kind=client):
  --client-kind feign|rest-template|web-client
  --calls-service <name>      # service this client calls  (≠ global --service)
  --calls-path-prefix /items/ # path prefix this client calls

PRODUCER ONLY (kind=producer):
  --producer-kind kafka|stream-bridge
  --topic-prefix order.

KIND INFERENCE (when --kind omitted):
  --http-method / --path-prefix                 ⇒ route
  --client-kind / --calls-service / --calls-path-prefix  ⇒ client
  --producer-kind / --topic-prefix              ⇒ producer
  --role / --java-kind / --capability           ⇒ symbol
  A domain flag conflicting with explicit --kind is an error.
```

Notes:
- `--role` is single-valued today (matches `NodeFilter.role: Role | None`). Multi-value is a backend change, deferred.
- `--fuzzy`'s prefix stage overlaps `--fqn-prefix`; if both are set, `--fqn-prefix` wins for the prefix stage (documented, not undefined).
- `--source-layer` values are opaque provenance codes; `--help` carries a one-line legend (they encode which inference layer produced a node: built-in / annotation-driven / FQN-driven / convention).
- `--capability http-client` (a symbol-side annotation view) overlaps `--kind client` (the dedicated client-node view); help states the distinction explicitly.

### Direct listings

All nodes of a kind, no query. All accept global `--service`, `--module`, `--limit`, `--offset`.

```
jrag routes     [--http-method GET|POST|PUT|DELETE|PATCH] [--path-prefix /api/] [--framework spring-mvc|webflux]
jrag clients    [--calls-service <name>] [--client-kind feign|rest-template|web-client]
jrag producers  [--topic-prefix order.]
jrag topics     [--producer-in <svc>] [--consumer-in <svc>]
jrag jobs
jrag listeners  [--topic-prefix order.]
jrag entities
```

### Graph traversal

All traversal commands share the resolve contract from §4. Disambiguation flags (`--kind`, `--java-kind`, `--role`, `--fqn-prefix`) are available on every command.

```
jrag decompose <query>   [--fqn-prefix ...]
                          [--depth 2] [--follow-calls] [--max-stage N]
    # service-internal call-chain DECOMPOSITION by role layers
    # (Controller → Service/Component → Client/Repository/Mapper), seeded from entrypoint roles.
    # Static structural waterfall, NOT a runtime/distributed trace.
    # backend: trace_flow(); --depth is per-stage hop count (clamped 1..3)

jrag flow <query>        [--fqn-prefix ...]
                          [--max-hops 5]
    # request reachability for a Route node:
    #   inbound  → cross-service callers (Feign/RestTemplate clients + Kafka/StreamBridge producers)
    #   outbound → INTRA-service method CALLS hops only (does NOT descend into downstream services)
    # backend: trace_request_flow(); --max-hops clamped 1..8

jrag impact <query>      [--kind ...] [--java-kind ...] [--role ...] [--fqn-prefix ...]
                          [--depth 2]
    # transitive reverse reachability: what breaks if this node changes
    #   (INJECTS | IMPLEMENTS | EXTENDS, depth-bounded). Distinct from `callers` (direct, CALLS-edge).
    # backend: impact_analysis() — takes NO microservice param; --service is a client-side post-filter
    #          and emits a warning when it excludes cross-service nodes.

jrag callers <query>     [--kind symbol|route] [--fqn-prefix ...]
                          [--depth N] [--min-confidence 0.8]
    # dispatches by resolved kind:
    #   Symbol → find_callers()       (CALLS-in, intra-service)
    #   Route  → find_route_callers() (HTTP_CALLS + ASYNC_CALLS in)

jrag callees <query>     [--fqn-prefix ...]
                          [--min-confidence 0.8] [--include-external] [--depth N]
    # dispatches by resolved kind:
    #   Symbol  → find_callees()              (CALLS-out; direct method callees)
    #   Client  → HTTP_CALLS-out → Route      (the endpoint this Feign/RestTemplate/WebClient calls)
    #   Producer→ ASYNC_CALLS-out → topic     (the topic this KafkaTemplate/StreamBridge publishes to)
    # NOTE: --exclude-role is NOT supported (find_callees has only exclude_external: JDK/Spring/Lombok
    #       FQN filtering). Client/Producer cases compose `resolve_v2` (name→id) + `neighbors_v2(id, "out", ["HTTP_CALLS"|"ASYNC_CALLS"])` (the generic flat-label path already reaches the `:Route`/topic) —
    #       a dedicated `LadybugGraph.client_calls_route`/`producer_calls_topic` method is post-v1 polish (§9).

jrag hierarchy <query>   [--kind ...] [--java-kind ...] [--fqn-prefix ...]
                          [--depth N]
    # full inheritance tree, both directions: EXTENDS + IMPLEMENTS in and out

jrag implementations <query>   [--fqn-prefix ...] [--capability ...]
    # interface → all implementing classes (IMPLEMENTS-in)
    # backend: find_implementors()

jrag subclasses <query>        [--fqn-prefix ...]
    # class → all subclasses (EXTENDS-in)
    # backend: find_subclasses()

jrag overrides <query>         [--fqn-prefix ...]
    # method → what it overrides (dispatch UP to superclass declaration)
    # backend: override_axis_traversal_for()

jrag overridden-by <query>     [--fqn-prefix ...]
    # method → what overrides it (dispatch DOWN to concrete implementations)
    # backend: override_axis_rollup_for()

jrag dependents <query>        [--fqn-prefix ...]
    # bean type → who injects/depends-on it (INJECTS-in)
    # backend: find_injectors()
    # (renamed from `injectors` for symmetry with `dependencies`)

jrag dependencies <query>      [--fqn-prefix ...]
    # bean/component → what it injects/depends-on (INJECTS-out)
    # backend: NONE today — composed via neighbors(direction="out", edge_types=["INJECTS"]).
    #          Note: returns less edge detail than `dependents` (find_injectors gives mechanism/annotation/field).

jrag connection <microservice> [--inbound] [--outbound] [--both]
                          [--http-method ...] [--calls-service ...]
    # cross-service connectivity map: who calls this service / who this service calls.
    # First positional is a microservice NAME, not a resolve-first <query> (the one exception —
    # documented loudly in --help). `--calls-service` replaces the old `--target-service`.
```

### File inspection

```
jrag outline <file>
    # class/method structure of a source file

jrag imports <file>
    # import statements in a file, with resolved graph node references where available
    # (distinct from `dependencies`, which is DI/INJECTS — `imports` is source-level)
```

> **Scope note:** there is intentionally **no `jrag source <query>`** to read a method body. The workflow is `inspect` → read `file_location` → the agent reads the file with its own file tools. `outline`/`imports` exist because they return graph-resolved structure (imports linked to nodes), which a raw file read does not.

### Inspection & search

```
jrag inspect <query>     [--kind ...] [--java-kind ...] [--role ...] [--fqn-prefix ...]
    # full node record + edge_summary (all incident labels, in/out counts, incl. composed keys)
    # same resolve contract as traversal commands. edge_summary is required.

jrag search <query>
    --table java|sql|yaml|all
    --hybrid
    --path-contains <substring>
    --role ... --exclude-role ...
    --annotation ...
    --capability ...
    --fqn-prefix ...
    --java-kind ...
    # semantic/vector similarity search — use when find returns nothing.
    # Does NOT accept --fuzzy (already semantic by design).
```

### Daemon — deferred

A persistent unix-socket daemon (`jrag daemon start|stop|status|list`, transparent auto-start) is **out of scope for v1**. v1 loads the index in-process per call, exactly as the MCP server does today. The daemon is revisited as a post-v1 milestone only if cold-start latency on large estates measurably justifies it (see §9, §11).

### Health

```
jrag status
    # in-process index health: ontology version, loaded index count, index freshness, source root.
    # (No daemon in v1; a read-only check that the index the operator built is current.)
```

---

## §6 — Output model & format

Every command's result follows **one envelope data model** — the canonical schema of record (Appendix A). The **default output format is compact text**: the envelope rendered for a reader, token-efficient for agent context windows. `--format json` emits the envelope verbatim for structured or pipeline consumption.

### Envelope (canonical schema)

```json
{
  "status": "ok | ambiguous | not_found | error",
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
  "file_location": "OrderController.java:42"
}
```

- `status` ∈ {`ok`, `ambiguous`, `not_found`, `error`}. **`truncated` is a boolean only**, not a status value — a capped result set is always `status: ok` with `truncated: true`; that is the agent's signal to use `--offset`.
- `agent_next_actions` is capped at 5. It **replaces** the MCP `StructuredHint{tool,args,actionable,reason}` surface. Trade-off: plain command strings are directly runnable by the agent (no args to re-assemble); the structured tool+args form is dropped. This requires a new **edge-label → CLI-command** mapper (the existing hint engine maps edges to MCP tools, not CLI commands).
- Listing commands (`routes`, `clients`, etc.) omit `root` and `edges`.
- `edge_summary` appears only in `jrag inspect` output, nested under the node record. It covers all incident edge labels and in/out counts, including composed keys (`DECLARES.EXPOSES`, `OVERRIDDEN_BY.DECLARES_CLIENT`, etc.).

### Text rendering (default)

The default is compact **text**; `--format json` emits the envelope verbatim. **Parsing model:** the agent reads raw bytes with no reflow, so identifiers are **never** truncated or wrapped (a truncated FQN is a footgun — the agent re-emits it and resolve fails); columns are single-space aligned, one record per line.

**Output shapes:**
- **Listing** (`routes`, `clients`, …) → table, header once, one row per result. **FQN omitted** (the next command re-resolves on a name + `--service`) — the single biggest token lever.
- **Single-anchor traversal** (`callers`, `callees`, `dependents`, `dependencies`, `implementations`, `subclasses`, `overrides`, `overridden-by`) → a `root:` line, then one row per result where each row *is* the edge (`name  role  service  file:line  [LABEL]  conf:0.9`). No separate `edges:` block — every edge shares the root.
- **Graph traversal** (`flow`, `decompose`, `impact --depth≥2`, `hierarchy`) → `root:` line, `nodes:` block, then `edges:` block.
- **`inspect`** → `key: value` record (full FQN here) with `edge_summary:` as an indented sub-block.
- **`--count` / `--exists`** → bare scalar (`42` / `true` / `false`); exit 0 on every `ok` incl. `0`/`false`. `--exists` is read as a string, not a bash exit code (conflating "failed" with "false" is a silent-correctness footgun).

**Conventions (full per-kind templates finalized in `/plan`):**
- **Endpoint disambiguation.** Edge/row endpoints use the *shortest form unique within this result's node set*: simple name → `name @service` → FQN. Common case stays short; collisions escalate only as far as needed. (`@service` is a name matching the `--service` flag — never an opaque ID. This preserves the resolve-first guarantee on the *output* side, where result-set name collisions would otherwise re-introduce the very ambiguity JSON avoids via IDs.)
- **Direction.** Arrow = stored edge direction (caller→callee, child→parent, implementer→interface, injector→bean), documented once in `--help`. `hierarchy` → indented tree with `↑`/`↓` relative to root; `connection` → `inbound:` / `outbound:` section headers; multi-hop traversals → `d=N` depth column.
- **Root identity.** Resolved root = first line, `* ` prefix, `(root)` suffix; the marker survives `--brief` (correctness over brevity).
- **Confidence.** `conf:0.9` is shown **only on CALLS-family edges** (`callers`/`callees`/`flow`/route-callers); structural edges (EXTENDS/IMPLEMENTS/INJECTS) carry none. **Envelope-level aggregate `confidence` is dropped** — no backend produces it (Appendix A).
- **Zero results vs not_found.** `ok` + zero is never empty stdout: it prints `0 <noun>  <resolved-fqn>  @<service>` (the resolved FQN also hedges against silent-wrong-resolve). `not_found` → `not found: <msg>`.
- **Ambiguous candidates.** Header states the count **and lists the narrowing flags** (`--service | --fqn-prefix | --kind | --java-kind | --role`); each line: `name  FQN  java-kind  role  service  — <reason>` (`reason` = `ResolveReason`, e.g. `exact_fqn` vs `short_name` — a cheap "how to narrow" signal). No `file:line` (`NodeRef` lacks file fields) and no `score` (positional rank, redundant with order). Up to two pre-filled `next:` narrowing commands; `probable:` may prefix the strongest but **auto-pick is forbidden** (§4 resolve-first is non-negotiable).
- **`truncated`.** Tier-1 v1 via the **+1-fetch trick** (`LIMIT limit+1`; `truncated = rows_fetched > limit`, zero cost): text renders `truncated: more results — use --offset <offset+limit>`. `M of N` / `total_count` is deferred — it needs a separate COUNT query (and would also surface the silent resolve candidate cap-at-10).
- **`edge_summary`** → indented, labels padded to the longest key, alphabetical, zero-zero rows omitted, explicit `in:0` kept.
- **Projection.** `--fields` always uses a `key: value` record block; built-in listings use tables (≤4 short-token columns) and switch to record block at ≥5 fields or any long/whitespace field.
- **Delimiters.** ASCII by default for byte-efficiency (`->LABEL->`); Unicode (`—LABEL→`) permitted for human review; one form pinned in test snapshots.

`--brief` / `--fields` / `--count` layer on top of either format. Text is the default because every byte of stdout enters the agent's context window (§14); `--format json` is one flag away.

---

## §7 — Use-case re-walk

Simulated agent: AI coding agent, ~15-service Spring Boot / Kafka / Feign fleet, 50k+ LoC services.

| # | Use case | Commands | Chain |
|---|---|---|---|
| UC1 | Bug: "orders after 6pm don't trigger inventory updates" — find the producer path | 2 | `jrag flow "POST /orders"` (outbound intra-service hops incl. the Kafka send) → `jrag callees <producer>` (topic it publishes to) |
| UC2 | Safe refactor: add parameter to `OrderService.calculateTotal` — blast radius | 2 | `jrag impact OrderService.calculateTotal` (transitive) → `jrag callers OrderService.calculateTotal` (direct) |
| UC3 | Check if method implements an interface (affects blast radius) | 2 | `jrag inspect OrderService.calculateTotal` → `jrag implementations PricingStrategy` (if edge_summary shows IMPLEMENTS) |
| UC4 | Find existing Feign multi-service join pattern to copy | 3 | `jrag find --calls-service inventory-service --service reporting-service` (kind=client inferred) → `jrag outline ReportingController.java` → `jrag decompose "ReportingController#joinEndpoint" --follow-calls` |
| UC5 | Incident: NPE from `InventoryClient#checkAvailability` in payment-service | 3 | `jrag inspect "InventoryClient#checkAvailability" --service payment-service` → `jrag callees "InventoryClient#checkAvailability"` (route it calls) → `jrag callers "InventoryClient#checkAvailability"` |
| UC6 | Onboarding to reporting-service (cold start) | 2 | `jrag overview reporting-service` → `jrag routes --service reporting-service` |
| UC7 | Kafka topology: who produces and consumes `order.created` | 2 | `jrag overview order.created` → `jrag decompose <consumer-entrypoint> --follow-calls` (per consumer) |
| UC8 | PR review: 3 files changed in order-service — blast radius | 3 | `jrag impact OrderController --service order-service --depth 3` + `impact OrderService …` + `impact OrderRepository …` (each emits the post-filter warning) |
| UC9 | Scheduled job audit: all `@Scheduled` jobs fleet-wide | 1 | `jrag find --capability scheduled-task` (kind=symbol inferred; no `--service` = fleet-wide) |
| UC10 | Security review: endpoints missing `@PreAuthorize` | 2 | `jrag routes` (fleet-wide) → inspect each for annotation fields; `jrag find --annotation @PreAuthorize` as secondary cross-check |
| UC11 | Architecture conventions: what patterns does payment-service use? | 1 | `jrag conventions --service payment-service` |
| UC12 | Find all Feign clients calling inventory-service across the fleet | 1 | `jrag clients --calls-service inventory-service` |
| UC13 | Find the route handler for `GET /orders/{id}` | 1 | `jrag find "GET /orders/{id}" --kind route` |
| UC14 | Inheritance tree: full hierarchy of `AbstractOrderProcessor` | 1 | `jrag hierarchy AbstractOrderProcessor` |
| UC15 | Dependency injection: what does `OrderService` inject? | 1 | `jrag dependencies OrderService --role service` |
| UC16 | Where does `KafkaOrderProducer` actually publish to? | 1 | `jrag callees KafkaOrderProducer` ( folded target → topic) |
| UC17 | Fleet-wide: list all Kafka topics, filter by consumer service | 1 | `jrag topics --consumer-in inventory-service` |
| UC18 | Cross-service map: what calls payment-service inbound? | 1 | `jrag connection payment-service --inbound` |
| UC19 | Which methods does `OrderController` override from a parent? | 1 | `jrag overrides OrderController --java-kind class` |
| UC20 | Structural size sanity before touching a service | 1 | `jrag map --service order-service` |

**Summary:** 15 of 20 use cases resolve in 1–2 commands. The 3-command cases (UC4, UC5, UC8) involve genuine multi-step investigation. No use case requires a prior call just to obtain an ID.

**Important semantic correction (UC1/UC4/UC5/UC7):** `flow`'s *outbound* side is intra-service only — it does **not** descend into downstream services. Cross-service reachability is on `flow`'s *inbound* side (who calls this route from outside) and via `callees <client|producer>` (where this outbound infra sends). Use cases that previously implied downstream cross-service hops now route through `callees` for the transport hop.

**Awkward cases:**
- **UC10** (absent annotation): no negative filter (`--without-annotation`) in v1. Fleet-wide routes listing + annotation inspection is the only path. Known gap (§8).
- **UC8** (multi-symbol impact): 3 separate `impact` calls (no batch mode in v1). Note the operator `analyze-pr` already does single-diff blast radius — `jrag diff-impact` wrapping it is a natural post-v1 addition.

---

## §8 — What this deliberately does NOT do (v1)

| Feature | Why deferred / skipped |
|---|---|
| **Daemon** (unix-socket, auto-start) | Heaviest, riskiest piece; no infra exists today; §11 lists socket-recovery races. v1 is in-process; daemon revisited only if cold-start latency justifies it. |
| Negative/absence filters (`--without-annotation`, `--unreferenced`) | Non-trivial backend query shape; not addressed by existing functions; deferred. (UC10 gap.) |
| `diff-impact` / `changed` (git diff → symbols) | **Not** "no backend" — the operator `analyze-pr` already does diff-based blast radius. Deferred from the *agent* surface only because wrapping it cleanly (symbol-level, not file-level) is its own design; a natural post-v1 addition. |
| `todos` / `unreferenced` listing commands | Not needed for v1 agent workflows; addable without API breaks. |
| Batch/multi-identifier input | Each command takes one resolved node; batching is N sequential calls for v1. |
| `drift` detection | Explicitly a later milestone. |
| Raw node IDs as primary input | Agents never construct internal IDs; the resolve contract covers all identifier forms. |
| Standalone `jrag resolve` | Resolution is infrastructure (the `resolve` tool), implicit in every command. |
| `jrag source <query>` (read method body) | Out of scope — `inspect` → `file_location` → agent's own file tools covers it. |
| Operator commands (`init`, `increment`, `reprocess`, `meta`, `analyze-pr`, `diagnose-ignore`, `tables`, `unresolved-calls`, `install`, `update`, `erase`) | Remain in the `java-codebase-rag` operator CLI. `install`/`update` gain a `--surface mcp\|cli` branch + surface-keyed artifact set in PR-JRAG-5; the lifecycle commands themselves don't move. |

---

## §9 — Migration plan — 5 PRs + prep refactors + deferred daemon

**Two prep refactors (land first, independent of CLI commands):**
- **PR-JRAG-0a — Single source of truth for shipped agent artifacts.** Today `skills/explore-codebase/SKILL.md` and `agents/explorer-rag-enhanced.md` each exist in two byte-identical, hand-synced copies (dev path + `java_codebase_rag/install_data/...` shipped via `package_data`). Collapse to one dev source; generate the shipped copy at build time (or read the dev copy directly). **Must land before the CLI skill/subagent exist** — otherwise the CLI variant creates four hand-synced copies. Small, zero behavior change.
- **PR-JRAG-0b — Extract `resolve_v2` into `resolve_service.py`.** `mcp_v2.py` already imports zero MCP SDK, and `resolve_v2(identifier, hint_kind, graph=g)` is a transport-agnostic pure function (the test suite already calls it exactly this way). Lift `resolve_v2` + its ~370-line pipeline (identifier parse → four candidate collectors → dedupe → rank → finalize) + the `ResolveOutput`/`ResolveCandidate` models into a neutral-named module; `mcp_v2` re-exports. **This removes the single real duplication trap** — without it, the CLI's resolve-first layer would either re-implement the pipeline (silent drift) or import an `mcp_`-named module. Mechanical, protected by existing tests (they assert on output shapes, not internals). Land as the opening step of PR-JRAG-1, or standalone the same week.

**PR-JRAG-1: Entry point + locate tier (in-process)**
- Add the `jrag` console script to `pyproject.toml` (`[project.scripts]`); build the shared resolve-first library (wraps `resolve_v2` → envelope status mapping); implement `jrag find` with kind-inference + contradiction-error + grouped `--help`; `jrag inspect` with full `edge_summary`; `jrag status`. Index loaded in-process via the existing `config.py` resolver — **no daemon**.
- **Honest `truncated`**: the boolean isn't surfaced by the backend today — implement via the **+1-fetch trick** (`LIMIT limit+1`; `truncated = rows_fetched > limit`, zero extra cost); text renders `truncated: more results — use --offset <offset+limit>`. `total_count` / 'M of N' deferred (needs a COUNT query; would also surface the silent resolve candidate cap-at-10).
- Test: find by FQN exact, by `--role`, by `--capability`; kind-inference from flags; hard-error on `--kind symbol --http-method GET`; inspect returns `edge_summary` with composed keys; ambiguous → candidates (reason rendered, no file/score); `--index-dir` resolves to the operator's index; `truncated` fires correctly via +1-fetch.

**PR-JRAG-2: Listing tier**
- `jrag routes`, `clients`, `producers`, `topics`, `jobs`, `listeners`, `entities` with their flags + globals.
- Test: each returns nodes of the correct kind; `--service`/`--module` scope correctly; `truncated: true` fires when limit is hit.

**PR-JRAG-3: Traversal tier**
- `callers`, `callees`, `hierarchy`, `implementations`, `subclasses`, `overrides`, `overridden-by`, `dependents`, `dependencies`, `impact`, `decompose`, `flow`, `connection`; plus `outline`, `imports`.
- **Backend work (honest, not "thin extraction"):** (a) `callees` for Client/Producer composes `resolve_v2` (name→client/producer id) + `neighbors_v2(id, "out", ["HTTP_CALLS"|"ASYNC_CALLS"])` — the generic flat-label traversal branch already reaches `:Route`/topic nodes today, so **no new query is required for v1**; a dedicated `LadybugGraph.client_calls_route`/`producer_calls_topic` method (mirroring `find_route_callers`) is post-v1 polish for symmetry/testability. (b) `dependencies` composes `neighbors(direction="out", edge_types=["INJECTS"])`.
- Test: each command exercises its backend; resolve ambiguity stops traversal; `callers` and `callees` dispatch correctly by resolved kind; `flow` outbound is intra-service (assert no cross-service descent); `impact --service` post-filter emits its warning.

**PR-JRAG-4: Orientation + search + packaging**
- `microservices`, `map`, `conventions`, `overview` (with `--as`); `search` (incl. `--hybrid` → BM25+vector); README; finalize the PyPI entry point; `agent_next_actions` generation (new edge→command mapper).
- Test: `overview` returns the correct bundle per target type; `search --hybrid` calls the BM25+vector path; `map` returns non-empty counts for every indexed service; `agent_next_actions` are valid runnable commands capped at 5.

**PR-JRAG-5: Agent host integration (install branching, skill, subagent)**
- New wizard step `select_surface` — "MCP or CLI?" — applied **globally** to all selected agent hosts (one surface per install; per-host variation deferred). Non-interactive flag `--surface mcp|cli`, default `mcp`. This *enforces* what the README today only *warns* ("do not mix multiple mechanisms on the same agent — duplicate context confuses tool selection").
- Ship a **CLI-flavored skill** (`explore-codebase-cli`) + **CLI-flavored subagent** (`explorer-rag-cli`), mirroring today's MCP pair (`explore-codebase` + `explorer-rag-enhanced`). Two separate documents, not one mode-switching skill: the MCP and CLI tool vocabularies differ (MCP tool calls vs `jrag` shell invocations), and a dual-vocabulary skill would carry exactly the "duplicate context" cost the README warns against. The CLI skill teaches the §5 command grammar, the §4 resolve contract, and §6 text output.
- **`Surface` dimension** on the existing `HostConfig`/`HOSTS` registry (`installer.py:43-95`) — host × surface = artifact set. `HostConfig` today abstracts paths only; surface is added orthogonal to it (not a host-capability flag).
- **`ArtifactManifest`** — replace the two hardcoded 3-artifact lists in `deploy_artifacts` (`installer.py:558`) and `refresh_artifacts` (`installer.py:1049`) with a single manifest iterated by both, keyed by surface. Kills the existing deploy/refresh duplication as a side benefit.
- **Fix `detect_configured_hosts` (`installer.py:1001`)** — today it discovers hosts *exclusively* by scanning for the `java-codebase-rag` MCP entry, so a CLI-only install (skill, no MCP entry) is invisible to `update`, which then exits fatal ("No configured agent hosts found"). Write a marker file (`.java-codebase-rag.hosts`, recording host/scope/surface chosen at install); discovery reads it. **Forced into this PR** — shipping the branch without it is a known `update` regression.
- **`resolve_mcp_command` (`installer.py:424`)** becomes surface-conditional: the CLI surface resolves the `jrag` binary, not the MCP server (today it hard-fails if the MCP binary is missing, which would block a CLI install).
- Depends on PR-JRAG-0a (single-source artifacts) so the new skill/subagent land in a one-copy world, not a four-copy world.
- Test: `--surface cli` deploys the CLI skill/subagent and writes no MCP entry; `--surface mcp` reproduces today's behavior; `update` after a CLI-only install refreshes the CLI skill (pre-fix this exited fatal); the marker file round-trips host/scope/surface; `resolve_mcp_command` resolves `jrag` on the CLI surface.

**Deferred milestone — Daemon (post-v1):** unix-socket daemon with transparent auto-start, `jrag daemon stop|status|list`, multi-index registry. Taken on only if PR-JRAG-1..5 ship and cold-start latency on a large estate is measured to be a problem.

---

## §10 — Decisions taken

1. **Same repo, new `jrag` PyPI entry point.** Lives in `HumanBean17/java-codebase-rag`, alongside (not replacing) the `java-codebase-rag` operator CLI. *(Corrected: the operator CLI is `java-codebase-rag`; there is no `user-rag`.)*
2. **`neighbors` removed.** Every edge traversal gets a named command. No agent reasons about `direction` or `edge_types`.
3. **Resolve-first: `<query>` not `<id>`.** All traversal/inspect commands take a human-readable query; the resolve step (`resolve_v2`, called directly as a transport-agnostic function — extracted in PR-JRAG-0b) is internal and invisible. Raw node IDs are never required.
4. **Disambiguation flags on all `<query>` commands.** `--kind`, `--java-kind`, `--role`, `--fqn-prefix` narrow resolve, not traversal results.
5. **`--service` semantics vary by command and are documented.** Pushed to the backend where the function takes `microservice` (`callers`, `callees`, `implementations`, `subclasses`, `dependents`, …); client-side post-filter with warning on `impact` (whose `impact_analysis()` takes none).
6. **`--module` is a first-class global flag** mapping to `NodeFilter.module`, distinct from `--service`.
7. **`--symbol-kind` → `--java-kind`.** Avoids the triple-"kind" overload. *(Note: the underlying `NodeFilter` field is `symbol_kind`/`symbol_kinds`; `java_kind` is the CLI flag name only.)*
8. **`connection` replaces `boundary`/`contract`/`service-map`.** Self-describing.
9. **`microservices` replaces `services`.** Avoids confusion with Spring `@Service`.
10. **`callers` and `callees` both dispatch by resolved kind.** `callers`: Symbol→`find_callers`, Route→`find_route_callers`. `callees`: Symbol→`find_callees`, Client→HTTP_CALLS-out, Producer→ASYNC_CALLS-out. *(New: `callees` now absorbs the old `target` command.)*
11. **`overrides` / `overridden-by` are separate commands** (direction ambiguity in one command would be a silent correctness risk).
12. **`dependents` (INJECTS-in) / `dependencies` (INJECTS-out).** *(Renamed from `injectors` for symmetry + guessability.)*
13. **`target` folded into `callees`** rather than kept standalone (decided). A client/producer *calls* its route/topic; consistent with `callees` = "what this calls" and with `callers`' kind-dispatch. (`destination` was the standalone alternative; rejected in favor of the fold.)
14. **`trace` → `decompose`** (decided). Resolves the trace/flow collision: "trace" implies end-to-end (which is `flow`); `decompose` honestly names the static role-waterfall.
15. **`find` stays flat** with kind-inference + contradiction-error + grouped help. Not split by kind (the listing tier already owns kind-specific access).
16. **`--target-service` → `--calls-service`** (and `--target-path-prefix` → `--calls-path-prefix`). Eliminates a one-hyphen near-collision with global `--service`.
17. **`flow --max-hops` not `flow --depth`.** Distinct from `decompose --depth` (per-stage hops vs stage count).
18. **Daemon deferred; v1 in-process.** Agents never manage a process in v1.
19. **`agent_next_actions` (≤5) replaces MCP `StructuredHint`.** Requires a new edge→CLI-command mapper.
20. **`edge_summary` required in `inspect`**, incl. composed keys. Verified available for all four kinds.
21. **`truncated` is a boolean only** (dropped from the `status` enum). Capped results are `status: ok` + `truncated: true`.
22. **Enum casing normalized.** CLI accepts lowercase/kebab or UPPER_SNAKE; maps to stored UPPER_SNAKE. Roles include `client`; `other` exposed (used by `--exclude-role`).
23. **No `jrag source`.** `inspect` → `file_location` → agent's file tools.

---

## §11 — Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Resolve ambiguity too frequent — agent narrows too often | `--fqn-prefix` + `--service` on every traversal command collapses most collisions; use-case re-walk shows 15/20 cases need 0 narrowing |
| `callers`/`callees` kind-dispatch wrong — symbol resolves to wrong kind | `--kind symbol\|route` explicit override; ambiguous cases surface candidates, not wrong results |
| `impact --service` post-filter silently misleads on cross-service blast radius | Warning in `warnings[]`: "impact ran fleet-wide; results filtered to --service. Cross-service nodes excluded." |
| `callees` for Client/Producer composes two calls | `resolve_v2` + `neighbors_v2` generic path already reaches `:Route`/topic (no new query for v1); dedicated `LadybugGraph` method is post-v1 polish. Real risk is the compose returning less edge detail than the Symbol path — documented, acceptable |
| `dependencies` returns less detail than `dependents` | Documented (neighbors-composed vs `find_injectors` EdgeHit); acceptable for v1 |
| `flow` outbound cross-service expectation | Help text states outbound is intra-service; cross-service is inbound + `callees <client\|producer>`; use cases corrected |
| `agent_next_actions` suggests a non-existent/wrong command | New edge→command mapper must be tested against every edge label incl. composed keys (PR-JRAG-4) |
| `--source-layer` values opaque to agents | One-line legend in `--help` |
| `--calls-service` vs `--service` still confused | Distinct names + help cross-reference; grouped help separates global scope from client-call flags |
| ~~`edge_summary` missing for some kinds~~ | **Not a risk** — verified: `describe_v2`/`edge_counts_for` is kind-agnostic; `edge_summary` exists for all four kinds (composed dot-keys are symbol-only, which is correct) |
| `truncated` signal doesn't exist in the backend today (only `has_more_results`, fed to hints) | +1-fetch trick in PR-JRAG-1 (`LIMIT limit+1`); `total_count`/'M of N' deferred (needs COUNT) |
| Text edge names re-introduce ambiguity the resolve-first contract kills | Tiered endpoint rendering (simple → `name @service` → FQN) keyed to within-result uniqueness (§6) |
| `update` strands CLI-only installs after a `pip upgrade` | `detect_configured_hosts` reads a marker file (`.java-codebase-rag.hosts`) instead of scanning MCP entries (PR-JRAG-5); test covers CLI-only install → `update` refresh |
| CLI re-implements the resolve pipeline → silent drift | `resolve_v2` extracted to `resolve_service.py` (PR-JRAG-0b); both MCP and CLI import the same function |

*(Daemon-related risks — stale PID, socket unavailable, auto-start races — are deferred with the daemon itself.)*

---

## §12 — Open questions ([TBD])

1. **Daemon trigger threshold.** No daemon in v1; revisit only with measured cold-start latency data from a large estate after PR-JRAG-1..4 ship.
2. **`--role` multi-value.** Deferred enhancement: stay single-valued in v1 (matches `NodeFilter.role`); add multi-value (list) as a backend follow-up if agents hit the "controllers OR services" wall.
3. **Default field sets per command.** The token-efficiency projections in §14 are illustrative; the exact default field list per command is finalized in the `/plan` per-PR contracts.
4. **Discovery signal for CLI-only installs.** Marker file (`.java-codebase-rag.hosts`, recording host/scope/surface) vs. scanning for skill/agent files. Recommended: marker file — explicit, survives skill renames, round-trips the surface choice. (Decides a detail of PR-JRAG-5.)

---

## §13 — Schema / Ontology / Re-index impact

- **Ontology bump: not required.** The CLI is a read-only surface over the existing graph.
- **Re-index required: no.** It consumes the index that `java-codebase-rag init`/`increment` already produces.
- **Config/tool surface changes:** one new `[project.scripts]` entry (`jrag`); new CLI module; enum-casing normalization layer; new edge→command hint mapper. No change to the MCP, the operator CLI, `NodeFilter`, or the ontology.

---

## §14 — Token efficiency (CLI outputs)

Every byte of CLI output enters the agent's context window, so output size is a first-class design constraint, not an afterthought. Defaults favor small, intent-matched payloads; verbosity is opt-in.

1. **Per-command default field projection.** Each command returns a curated default field set for its intent, not the full node record. **FQN is omitted from listings and traversal rows** (the next command re-resolves on a name + `--service`); full FQN appears only in `inspect`, ambiguous candidates, and `--fields`:
   - `routes` → `{method, path, handler, service, file:line}`
   - `callers` / `callees` / `dependents` / `dependencies` → `{name, role, service, file:line, confidence}`
   - `impact` → `{name, role, service, depth, confidence}`, ranked
   - `inspect` → full record + `edge_summary` (the one "tell me everything" command)
   - `ambiguous` candidate lists → `{name, fqn, java-kind, role, service, reason}`
   - `--fields <a,b,c>` opts in to specific fields; `--full` returns everything.
2. **`--brief`.** Name + FQN + one discriminator (role/kind) only — for scanning/confirmation. Default for candidate lists.
3. **`--count` / `--exists`.** Bare scalar output (`42` / `true` / `false`) — no records. Exit 0 on every `ok` (including `0` / `false`); `--exists` is read as a string, not a bash exit code (conflating "failed" with "false" is a silent-correctness footgun).
4. **Fan-out-scaled default `--limit`.** High-fanout commands (`impact --depth≥2`, `callees`/`callers --depth≥2`) default lower (e.g. 10); listings default 20. `truncated: true` always signals more.
5. **De-duplication.** A node reached via multiple paths appears once in `nodes` (the MCP `neighbors` `dedup_calls` behavior carries over); `edges` still lists every path.
6. **Ranked output.** Results ranked by confidence/relevance so the agent can take top-K and stop. `agent_next_actions` suggests narrowing over paging for semantic search (results degrade past page 1).
7. **Text is the default format; JSON is opt-in.** Compact text (one line per result, header-once tables) minimizes tokens by default; `--format json` emits the canonical envelope (Appendix A) for structured/pipeline use. Flipped from JSON-default because defaulting to the verbose format conflicted with token-efficiency-first (§6).
8. **Lean envelope.** Omit empty optional fields (`warnings`, `edges`, `agent_next_actions`) rather than emitting `[]` — saves tokens on the common success path.
9. **IDs in edges, records in `nodes`.** Edges carry `from`/`to` IDs; `nodes` is the ID→record map, so multi-edge results don't duplicate node data. (Inlining endpoint names in every edge was rejected as duplicative.)

**Validation:** a token-budget assertion in the test suite — no command's *default* output exceeds a ceiling (e.g. 2k tokens) on the bank-chat fixture — guards against regression as fields accrete.

---

## Appendix A — Output envelope schema (canonical model)

Emitted verbatim by `--format json`; the default text rendering (§6) is a compact view of this same model.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema",
  "type": "object",
  "required": ["status"],
  "properties": {
    "status":             { "type": "string", "enum": ["ok", "ambiguous", "not_found", "error"] },
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
          "confidence": { "type": "number", "description": "CALLS-family edges only (callers/callees/flow/route-callers), sourced from edge.attrs['confidence']; absent on EXTENDS/IMPLEMENTS/INJECTS" }
        }
      }
    },
    "root":               { "type": "string" },
    "candidates":         { "type": "array", "items": { "type": "object" }, "description": "Capped at 10. Items: {node: NodeRef, score (positional rank), reason: ResolveReason}. Text renders name/FQN/java-kind/role/service/reason — not file (NodeRef lacks it), not score" },
    "agent_next_actions": { "type": "array", "maxItems": 5, "items": { "type": "string" } },
    "warnings":           { "type": "array", "items": { "type": "string" } },
    "truncated":          { "type": "boolean", "description": "v1: +1-fetch trick (LIMIT limit+1; truncated = rows_fetched > limit). total_count / 'M of N' deferred (needs COUNT; would also cover candidate cap-at-10)" },
    "file_location":      { "type": "string", "description": "filename:line — composed from root's record; rendered only when root is set" }
  }
}
```

`edge_summary` (inspect only) is nested under the node record. Labels seen include stored edges (`CALLS`, `HTTP_CALLS`, `ASYNC_CALLS`, `IMPLEMENTS`, `EXTENDS`, `OVERRIDES`, `INJECTS`, `DECLARES`, `EXPOSES`, `DECLARES_CLIENT`, `DECLARES_PRODUCER`) plus virtual/composed keys for symbols (`DECLARES.EXPOSES`, `OVERRIDDEN_BY`, `OVERRIDDEN_BY.DECLARES_CLIENT`, …). Note `OVERRIDDEN_BY` is a **virtual** key (reverse of stored `OVERRIDES`), not a stored edge.

```json
"edge_summary": {
  "CALLS":                       { "in": 14, "out": 3 },
  "DECLARES.EXPOSES":            { "in": 0,  "out": 2 },
  "OVERRIDDEN_BY":               { "in": 0,  "out": 1 },
  "OVERRIDDEN_BY.DECLARES_CLIENT": { "in": 0, "out": 1 }
}
```

## Appendix B — Backend mapping (verified)

| CLI command | Backend (`ladybug_queries.py`) | Status |
|---|---|---|
| `find` | `find_by_name_or_fqn` / `list_by_role` / `list_by_annotation` / `list_by_capability` + `resolve_v2` | exists |
| `inspect` | `describe_v2` (+ `edge_counts_for`, `member_edge_rollup_for`, `override_axis_rollup_for`) | exists |
| `decompose` | `trace_flow` | exists |
| `flow` | `trace_request_flow` (outbound is intra-service) | exists |
| `impact` | `impact_analysis` (no microservice param) | exists |
| `callers` | `find_callers` / `find_route_callers` | exists |
| `callees` (Symbol) | `find_callees` | exists |
| `callees` (Client/Producer) | — | **new query needed** |
| `hierarchy` | `neighbors` (EXTENDS+IMPLEMENTS, both) | exists |
| `implementations` | `find_implementors` | exists |
| `subclasses` | `find_subclasses` | exists |
| `overrides` / `overridden-by` | `override_axis_traversal_for` / `override_axis_rollup_for` | exists |
| `dependents` | `find_injectors` | exists |
| `dependencies` | `neighbors(direction="out", edge_types=["INJECTS"])` | **composed (no dedicated fn)** |
| `connection` | `list_clients` / `list_producers` + route-caller queries | exists |
| `routes`/`clients`/`producers`/`topics` | `list_routes` / `list_clients` / `list_producers` (+ topics) | exists |
| `outline` / `imports` | `find_symbols_in_file_range` + source parse | exists |
| `status` | `meta` / `microservice_counts` / `module_counts` | exists |
