---
name: java-codebase-explore
title: Explore a Java microservices codebase with the java-codebase-rag MCP
description: |
  Use when exploring an unfamiliar Java microservices estate indexed by the
  java-codebase-rag MCP. Activates on "explore this codebase", "help me
  understand this system", "map the call graph", "plan a change to service",
  "onboard onto this code", "write a propose doc for redesign". Teaches
  exploration *strategy* — when to call MCP vs fall back to rg/file reads/CLI,
  how to read staleness and confidence, and a catalogue of named missions
  (understand / plan / onboard / trace / propose / debug) with stopping rules.
  Complements but does not require docs/AGENT-GUIDE.md.
when_to_load:
  - "explore this codebase / repo / service"
  - "help me understand this microservices system"
  - "map the call graph for <service>"
  - "plan a change to <service>"
  - "onboard onto this code"
  - "write a propose doc for redesign of <component>"
when_not_to_load:
  - routine PR review (use a review skill such as `pr-review` if you have it; example external skill, not shipped from this repo)
  - single-question lookups answerable by one MCP call
  - editing existing code where the agent is already oriented
---

# java-codebase-explore

## Activation

This skill is **`java-codebase-explore`**: use it when the user’s intent matches the activation phrases in the YAML metadata above (explore the estate, understand the system, map call paths, plan a change, onboard, draft a propose, or debug a symptom). It is the **strategy guide** (when to use MCP versus other tools, how to sequence exploration, stopping rules, and anti-capabilities). **[`docs/AGENT-GUIDE.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/docs/AGENT-GUIDE.md)** remains the **operating manual** for exact JSON argument shapes, recovery moves, and slash-style aliases—open or link it when you need those details; this document does not duplicate them. Cross-links below use **GitHub `blob/master` URLs** (not `../` paths) so they still resolve when this skill is installed from the Perplexity zip without a full repo checkout.

## Pre-flight: is the index built?

Before any mission, establish whether the project is indexed and whether graph and vector layers are aligned.

1. Run **`java-codebase-rag meta`** (with the same `--source-root` / `--index-dir` / env as the operator setup). Parse the JSON payload: ontology version, counts, `edge_counts`, paths, and any `success: false` outcome (see [`docs/JAVA-CODEBASE-RAG-CLI.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/docs/JAVA-CODEBASE-RAG-CLI.md) for `meta` semantics and exit codes).
2. **Unindexed or missing graph:** if there is no usable index or `meta` indicates failure, do not pretend the MCP can see the repo. Stop MCP-driven missions until **`java-codebase-rag init`** (or **`reprocess`** where appropriate) has been run per the CLI guide; meanwhile use **`rg`**, README, and build files to orient.
3. **Lance vs Kuzu after `increment`:** **`increment`** refreshes the CocoIndex / Lance side **without** rebuilding Kuzu. Expect **stale graph navigation** until **`reprocess`** (full Lance reprocess + full Kuzu rebuild). Treat `meta` (and last `reprocess` / provenance fields there) as the source of truth for how fresh the graph is; if graph and source disagree, prefer the **open file** and report staleness.

## Map the seams first

On any new estate, **enumerate before you search-hunt**:

1. **`find(kind="route", …)`** — list HTTP seams (paths, methods, microservices).
2. **`find(kind="client", …)`** — list outbound clients (Feign, `RestTemplate`, Kafka, etc.) and targets.
3. Optionally **`find(kind="symbol", filter={"role":"CONTROLLER"})`** (or equivalent `NodeFilter`) to anchor web entrypoints.

You cannot reason reliably about cross-service behaviour until these surfaces exist in your working mental model (or you have consciously fallen back to non-MCP discovery).

## Mission catalogue

### Mission: Understand a feature

**When it applies:** The user asks how a feature, flow, or implementation detail works (for example “how does X work?” or “explain Y in service Z”).

**Goal:** A coherent story: entrypoints (routes/controllers), core symbols, and principal callees/callers, with explicit gaps where the graph is silent.

**Opening move:** `java-codebase-rag meta` if not yet done this session, then **`search`** with a tight query **or** `find` when you already know a symbol/route id.

**Sequence:**

1. From `search` hits or `find`, pick the best anchor id; **`describe(id)`** for full node + `edge_summary`.
2. Walk **one hop at a time** with **`neighbors`**: e.g. outbound `CALLS` / `EXPOSES` / cross-service `HTTP_CALLS` / `ASYNC_CALLS` as the question requires; inbound `CALLS` / `INJECTS` to see who drives a method.
3. If the feature spans services, pivot through **`find(kind="client", …)`** and matching routes on the callee side rather than deep blind `search` loops.

**Stopping rule:** You can name the main controller/handler, the core service methods, and the outbound/inbound seams relevant to the question; you have called out anything unresolved (dynamic dispatch, unindexed paths) per anti-capabilities.

**Fallbacks:** If `search` is empty or vague, use **`rg`** on distinctive strings, read files directly, then return to `find`/`describe` with ids you discovered.

### Mission: Plan a change

**When it applies:** The user will modify code and needs blast radius, touched routes, or dependency direction (for example renames, API changes, or refactors).

**Goal:** Bounded sets: callers, callees, injected collaborators, exposed routes, and cross-service consumers/producers touched by the change.

**Opening move:** **`find(kind="symbol", …)`** to resolve the target symbol **or** `search` if the qualified name is unknown—then **`describe`**.

**Sequence:**

1. **`neighbors(direction="in", edge_types=["CALLS","INJECTS",…])`** from the target method(s); repeat one hop at a time until diminishing returns.
2. For API or route changes: **`find(kind="route", filter={…})`** on the owning microservice; **`neighbors`** with `EXPOSES` / `HTTP_CALLS` / `ASYNC_CALLS` as needed.
3. For PR-shaped work, use **`java-codebase-rag analyze-pr`** (CLI only) with a diff when the user has a branch or patch—see [`docs/JAVA-CODEBASE-RAG-CLI.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/docs/JAVA-CODEBASE-RAG-CLI.md).

**Stopping rule:** You can list concrete symbols/routes/clients at risk and say what is **not** knowable from static graph alone (reflection, unindexed services).

**Fallbacks:** **`rg`** for string literals and annotations; **`git log` / `git blame`** for ownership and recency; README / `pom.xml` / `build.gradle` for module boundaries.

### Mission: Onboard onto an unfamiliar service

**When it applies:** The user is new to a service and wants orientation (for example “I’m new to billing-service; orient me.”).

**Goal:** A map of that service’s **outward HTTP API**, **inbound dependencies** (other services calling it), and **outbound clients** it uses to call others.

**Opening move:** **`find(kind="route", filter={"microservice":"<service>"})`** then **`find(kind="client", filter={"microservice":"<service>"})`** (adjust filter keys to match live `NodeFilter` / tool schema in the MCP client).

**Sequence:**

1. Cluster routes by path prefix; **`describe`** on representative `route:` ids.
2. For each major route, **`neighbors(direction="in", edge_types=["EXPOSES"])`** (and `HTTP_CALLS` / `ASYNC_CALLS` when tracing callers) to land on handler symbols; then outbound `CALLS` as needed.
3. Use **`find(kind="client", …)`** with the same microservice filter to list outbound integration points; follow **`HTTP_CALLS` / `ASYNC_CALLS`** edges when present.

**Stopping rule:** You can summarize how traffic enters the service, what modules/controllers own key paths, and what external systems it calls—**without** claiming tests, runtime config, or unindexed siblings exist in MCP.

**Fallbacks:** If the service **does not appear** in `meta` / `find`, assume **unindexed or wrong root**—verify with `meta`, then **`rg`** + README + adjacent services’ `find` results; do not assert absence from an empty MCP result alone.

### Mission: Trace a cross-service flow

**When it applies:** The user asks what happens when a route is invoked or how control moves from an entrypoint to clients/async boundaries (for example “POST /orders …” or “from controller A to client B”).

**Goal:** A directed chain (or tree) of nodes linked by **`EXPOSES`**, **`CALLS`**, and resolver-backed **`HTTP_CALLS` / `ASYNC_CALLS`**, with explicit notes on confidence and dead ends.

**Opening move:** **`find(kind="route", filter={path/http_method/microservice…})`** to resolve the `route:` id.

**Sequence:**

1. **`neighbors(direction="in", edge_types=["EXPOSES"])`** onto the handling symbol; walk **`CALLS`** outbound method-by-method.
2. When a method shows outbound HTTP/async, use **`neighbors`** with **`HTTP_CALLS` / `ASYNC_CALLS`** (direction per question) and follow to target routes or async targets.
3. Stop at leaves, framework boundaries, or unresolved edges; read **`edge.attrs`** (`attrs.confidence`, `attrs.strategy`, `attrs.match`) and report low-confidence segments as resolver gaps, not as facts.

**Stopping rule:** You reach a stable leaf (external IO, message publish, clear terminal layer) **or** you document every unresolved hop with a concrete next non-MCP check.

**Fallbacks:** **`rg`** for string-built URLs; **`git log`** on suspect handlers; runtime logs are out of MCP scope—say so.

### Mission: Prepare to write a propose doc

**When it applies:** The user is about to author a design/propose document and needs scoped evidence from the codebase (for example a migration or cross-cutting redesign).

**Goal:** An evidence inventory: affected routes/clients/symbols, dependency direction, and known unknowns suitable for a propose’s “scope / risks / alternatives” sections.

**Opening move:** **`java-codebase-rag meta`**, then **`find(kind="client", …)`** or **`find(kind="route", …)`** scoped to the hypothesis (e.g. all `feign_method` clients if replacing Feign).

**Sequence:**

1. Enumerate candidates with **`find`**; **`describe`** representatives.
2. Use **`neighbors`** to measure **fan-in / fan-out** and cross-service edges.
3. Capture **staleness** (`increment` vs `reprocess`) and **confidence** (`attrs.*`) limitations explicitly for the propose’s risk section.

**Stopping rule:** You have a bullet list of graph-backed facts vs gaps, enough that a propose author can write without further blind exploration.

**Fallbacks:** Repo **`propose/`** and **`plans/`** examples via file reads; **`java-codebase-rag analyze-pr`** when there is an existing diff to ground scope.

### Mission: Debug a specific symptom

**When it applies:** The user has a symptom (error string, latency on a path, unexpected behaviour) and wants likely code loci.

**Goal:** Short ranked hypotheses tied to symbols/routes, each with a suggested verification step (read file, log line, metric, or reindex).

**Opening move:** If tied to an HTTP path, **`find(kind="route", …)`** for that path/method/service; otherwise **`search`** on distinctive error tokens **then** `describe`/`neighbors`.

**Sequence:**

1. From the route/handler, **`neighbors`** for **`HTTP_CALLS`**, **`ASYNC_CALLS`**, **`CALLS`** as the symptom suggests (sync slowness vs fan-out vs messaging).
2. Read **`edge.attrs.confidence`** / **`attrs.strategy`** / **`attrs.match`** on cross-service edges before blaming a downstream.
3. **`git log` / `git blame`** on the hot handler or client when the graph looks stale or incomplete.

**Stopping rule:** You can point to a small set of files/methods worth inspecting and you have separated **MCP-backed** claims from **speculation**.

**Fallbacks:** **`rg`** across logs strings and config; read **`application*.yml`**, feature flags, and build/CI files; **`java-codebase-rag`** CLI help for commands (`analyze-pr`, `diagnose-ignore`) when the question is operational—see CLI doc, not MCP tools.

## When MCP is the wrong layer

Use non-MCP work **first-class**, not as a last resort:

| Layer | Rule of thumb |
| ----- | ------------- |
| **`rg` / grep** | Fastest for exact strings, stack traces, config keys, test code (**tests are not indexed**). |
| **File reads** | Single-file truth for logic the graph has not ingested or when **`increment`** left Kuzu stale. |
| **`git log` / `git blame`** | History, ownership, “when did this regress”—never MCP. |
| **README, `pom.xml`, `build.gradle`, Docker, workflows** | Build/run/deploy and module structure—never MCP. |
| **`java-codebase-rag` CLI** | Lifecycle (`init` / `increment` / `reprocess`), **`meta`**, **`tables`**, **`analyze-pr`**, **`diagnose-ignore`**—per [`docs/JAVA-CODEBASE-RAG-CLI.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/docs/JAVA-CODEBASE-RAG-CLI.md). |

If answering the question would require runtime truth (live traffic, actual DB rows, dynamic classpath), say so and stop relying on MCP.

## What this MCP cannot see, cannot do, and cannot guarantee

If you find yourself surprised by an empty result, a missing edge, or a stale
fact, read this section first. The MCP is a static graph + vector index over
indexed Java production code. The following are out of frame:

- **Test files.** Not indexed. Use `rg` for test discovery.
- **Build, deploy, runtime story.** Not indexed. Read `README.md`, `pom.xml`,
  `build.gradle`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/`.
- **When something changed.** Use `git log` / `git blame`.
- **Reflection and dynamic dispatch.** `CALLS` edges resolve static method
  calls; reflective invocations, SPI lookups, and dynamic proxies are
  invisible. Treat the resolved caller set as a lower bound, not a complete
  set.
- **Unindexed services.** A service that was never `init`'d does not exist
  from MCP's point of view. Verify with `java-codebase-rag meta` before
  claiming absence.
- **Cross-service edge completeness.** `HTTP_CALLS` / `ASYNC_CALLS` depend on
  the resolver matching a client invocation to a route. Low `confidence` in
  `edge.attrs` (`attrs.confidence`, `attrs.strategy`, `attrs.match`) is a resolver gap signal, not a hallucination. Report it
  as such.
- **Stale graph after `increment`.** `increment` updates Lance but not Kuzu.
  A graph older than the source tree is normal mid-development. Check the
  last `reprocess` time via `meta`; when in doubt, the open file wins.

When MCP and the open file disagree, **the file wins.** Report the
disagreement as evidence of staleness, not as a contradiction.

## Confidence and staleness

- **Wire fields:** Cross-service and resolver-heavy edges carry **`edge.attrs`** (same map surfaced as `attrs` in payloads). Treat **`attrs.confidence`**, **`attrs.strategy`**, and **`attrs.match`** as structured hints: low confidence means “resolver could not pin this cleanly,” not “definitely false.”
- **MCP vs editor:** If the open buffer contradicts graph edges (deleted method, renamed class), **trust the file** and treat MCP as stale until **`reprocess`** (or at least acknowledge incremental lag after **`increment`**).
- **Operational check:** Use **`java-codebase-rag meta`** to compare index health, ontology version (currently **v11** in this repo’s README), and recency signals—then decide whether to re-run **`reprocess`** before continuing a mission.

## Anti-patterns

1. **Fishing with `search`.** Repeated broad `search` without `find`/`describe` anchors burns context and hides the real seam structure.
2. **Unbounded graph walks.** Hopping `neighbors` without a stopping rule; you must terminate with a stated evidence bar from the mission template.
3. **Empty result = proof of absence.** Could be unindexed, wrong table, stale Kuzu, or not in Java production scope—verify per **What this MCP cannot see** before claiming “does not exist.”
4. **Ignoring `attrs.confidence` / `attrs.strategy` / `attrs.match`.** Especially on **`HTTP_CALLS` / `ASYNC_CALLS`**, low confidence is a **gap signal**, not noise to omit from the answer.
5. **Using MCP for CLI-only or repo-meta questions.** `analyze-pr` shape, exit codes, ignore diagnostics → **CLI doc + `--help`**, not `search`.
6. **Treating the graph as runtime telemetry.** Throughput, live queues, and prod-only config are out of scope—say so and switch tools.
7. **Over-trusting post-`increment` navigation.** If users ran **`increment`** without **`reprocess`**, expect **stale Kuzu**; prefer **`meta`**, file reads, or queue **`reprocess`** before high-stakes conclusions.
8. **Asking the user for JSON shapes** that already ship in MCP tool descriptions and **[`docs/AGENT-GUIDE.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/docs/AGENT-GUIDE.md)**—read those surfaces instead of inventing parameters.

## Cheat sheet (inline reference)

Four MCP tools:

- `search(query, table, hybrid, limit, filter)` — fuzzy locate.
- `find(kind, filter, limit)` — structured listing; `filter` is required.
- `describe(id)` — full node + `edge_summary`.
- `neighbors(ids, direction, edge_types, limit, offset, filter)` — one hop;
  `direction` and `edge_types` are required.

*Shorthand:* MCP calls are always **named JSON** arguments per the live tool schema, not positional Python. The lines above are mnemonic; for pagination and optional filters, match the tool surface (e.g. `neighbors` exposes `limit`, then `offset`, then optional `filter`).

Three node kinds: `symbol`, `route`, `client`. Ids carry a prefix
(`sym:`, `route:` / `r:`, `client:` / `c:`).

Nine edge types:

| Group | Edges |
| ----- | ----- |
| Type wiring | `EXTENDS`, `IMPLEMENTS`, `INJECTS` |
| Containment | `DECLARES`, `DECLARES_CLIENT` |
| Method calls | `CALLS` |
| Service boundary | `EXPOSES` |
| Cross-service | `HTTP_CALLS`, `ASYNC_CALLS` |

For exact argument shapes, recovery playbook, and slash aliases see
[`docs/AGENT-GUIDE.md`](https://github.com/HumanBean17/java-codebase-rag/blob/master/docs/AGENT-GUIDE.md) in the java-codebase-rag repo.
