# Agent Skills and Commands — High-level intents over the 5-tool MCP

**Status**: draft (revision 3)
**Author**: Dmitry + Computer
**Date**: 2026-05-08 (revised 2026-05-19)
**Blocker**: [#177 — CALLS-edge noise problem](https://github.com/HumanBean17/java-codebase-rag/issues/177) — the `/mini-map` skill (Tier 2) is motivated by and depends on this problem being understood; the 10 Tier 1 + other Tier 2 skills do not.

## TL;DR

- The 5-tool MCP navigation API (`search` / `find` / `describe` / `neighbors` / `resolve`) traded surface area for composability — the convenience that v1 tools (`list_routes`, `where_used`, `callers_of`, `implementations_of`, `outbound_calls`, …) provided is gone from the MCP.
- That convenience belongs at a different layer: **agent-side skills and slash-commands**, not MCP tools and not CLI subcommands.
- Ship a single shared skill source — `agent-skills/` — that compiles into `.claude/skills/<name>/SKILL.md` (Claude Code), `.qwen/skills/<name>/SKILL.md` (Qwen Code), and **`.cursor/skills/<name>/SKILL.md`** (Cursor — same format as the other hosts). **Same SKILL.md body on every host** (YAML frontmatter + markdown body); only the install path differs.
- Tier 1 (high-leverage, low cost): 10 navigation skills covering the 10 most common query patterns from the existing `AGENT-GUIDE.md` slash-style alias section — **graph-accurate one-hop chains**, identifier resolution via **`resolve`**.
- Tier 2 (polish): 4 **bounded workflow** skills (`/explain-feature`, `/impact-of`, `/trace-request-flow`, `/mini-map`) — explicit depth/stop rules; `/mini-map` adds heuristic post-processing (not a strict chain).
- Tier 3 (deferred): `java-codebase-rag dump-*` CLI helpers — out of scope here.
- Migration is **5 PRs**: (1) propose lock, (2) shared `agent-skills/` source + compile script, (3) Tier 1 skills, (4) Tier 2 workflow skills, (5) AGENT-GUIDE rewrite to point at the shipped skills instead of duplicating prose templates.

## §1 Frame: what is this thing, really?

**Skills and slash-commands are agent-side prompt scaffolding for high-level user intents — they are NOT a second MCP API and NOT a CLI.**

The MCP is a graph-and-vector navigator. It has **five** navigation tools because that is the smallest set that covers locate → inspect → walk without re-embedding v1 verbs. But "smallest set of primitives" and "things a developer wants to ask" are different shapes. A developer thinks `"who calls ChatController#joinOperator?"`, not `"resolve then neighbors with direction=in and edge_types=[CALLS]"`. Skills are how that gap gets bridged without breaking the MCP design.

This frame rules things out:

- **Skills are not a second MCP.** They contain no new graph queries, no new vector backends, no new edge types. Every Tier 1 skill is a **deterministic** chain of existing MCP calls; Tier 2 skills add **bounded** recursion and (for `/mini-map` only) heuristic post-processing on top of those calls.
- **Skills are not CLI subcommands.** The `java-codebase-rag` CLI is for ops (`init` / `increment` / `reprocess` / `meta` / `tables` / `diagnose-ignore` / `analyze-pr`). Adding `java-codebase-rag list-routes` would give the same query three homes (MCP + skill + CLI) — pick one.
- **Skills are not the AGENT-GUIDE.** The guide is reference doc that the agent reads once. Skills are *invokable* — the user types `/callees ChatController#joinOperator(JoinOperatorRequest)` and the model executes a known chain. Same intents, different actuation.
- **Skills are not the exploration strategy skill.** [`docs/skills/java-codebase-explore.md`](../docs/skills/java-codebase-explore.md) teaches *when and why* to explore (missions, fallbacks, anti-capabilities). Navigation skills here teach *exact MCP chains* for frequent intents. See §4b.
- **Skills are not free.** Each one is recurring tokens in the agent's context once invoked, plus maintenance cost when the MCP surface evolves. The set must be small and earn its place.

## §2 Design principles

1. **Single source of truth.** One markdown file per skill in `agent-skills/<skill-name>/SKILL.md`. Build step copies to `.claude/skills/`, `.qwen/skills/`, and `.cursor/skills/`. No drift between hosts.
2. **Identical format across Claude Code, Qwen Code, and Cursor.** All three accept `SKILL.md` with YAML frontmatter (`name` + `description`) and a markdown body. Verified May 2026 for Claude/Qwen — see Appendix A; Cursor already ships project skills under `.cursor/skills/` in this repo.
3. **Tier 1 = deterministic MCP chains.** No prose like "consider running `find` if appropriate" — the body must say exactly which calls to make and in what order. **Tier 2 = bounded workflows** with explicit recursion depth, stop conditions, and (for `/mini-map` only) documented heuristic filtering — not fully deterministic output.
4. **Skills wrap MCP, never replace it.** A skill body always names the underlying MCP tools used. This keeps the agent able to drop into raw MCP if the skill doesn't fit.
5. **Slash-name = skill-name = filename.** `/callees` ↔ `agent-skills/callees/SKILL.md`. No alias indirection. (Hosts derive the slash-name from the directory name.)
6. **Identifier-shaped arguments → `resolve` first.** Each skill's body specifies positional arguments and calls `resolve(identifier=…)` (optional `hint_kind`) when the input is not already a `sym:` / `route:` / `client:` / `producer:` id. Use `find` only for structured listing (`/controllers`, `/routes`, …), not for FQN disambiguation.
7. **Graph-accurate edge sets.** One-hop `neighbors` must respect `EDGE_SCHEMA` endpoint types (see `docs/EDGE-NAVIGATION.md`). Do not pass `HTTP_CALLS` or `ASYNC_CALLS` on a bare method `sym:` id — those edges are **Client→Route** and **Producer→Route**; reach them via `DECLARES_CLIENT` / `DECLARES_PRODUCER` (or composed dot-keys) from the declaring method.
8. **Skills are versioned with the MCP, not separately.** When `NodeFilter` keys, `edge_types`, or `kind` values change, skills get updated in the same PR. Lockstep with `AGENT-GUIDE.md` and `README.md`.
9. **No skill ships without a working example.** Every SKILL.md ends with a worked example using the bank-chat-system fixture, so a maintainer can verify the chain still works after MCP changes.

## §3 The three layers

This diagram lives at the top of the resulting README and is the canonical mental model:

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /callees, /controllers, /routes,      │
│   /impact-of, /mini-map                                      │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: skills shipped as SKILL.md files in        │
│   .claude/skills/, .qwen/skills/, .cursor/skills/ (same      │
│   source). Tier 1 = deterministic MCP chains; Tier 2 adds    │
│   bounded recursion + light post-processing (/mini-map).     │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — Composable primitives (the MCP API)                │
│   search, find, describe, neighbors, resolve                 │
│   ─────────────────────────────────────────────────────────  │
│   Navigation surface stays at 5. Ops stay on the CLI.        │
├──────────────────────────────────────────────────────────────┤
│ Layer 1 — Storage primitives                                 │
│   Kuzu Cypher + LanceDB tables                               │
└──────────────────────────────────────────────────────────────┘
```

**Why Layer 3 is the right home for `/list_routes` and friends:**

- It's per-host (Claude Code, Qwen Code, Cursor) — compile targets share one source; host-specific tuning stays in skill `description` trigger words only.
- It doesn't pollute the MCP surface — the 5-tool count stays load-bearing for tool-selection on weak models.
- It doesn't duplicate the CLI's audience — CLI is ops; skills are queries.
- It compiles from one source, so all hosts stay in sync without N× edits.

## §4 Audit by call site (who actually invokes these?)

Before deciding to ship a skill at all, list realistic callers. If the dominant caller isn't an interactive agent session, the feature belongs elsewhere.

| Skill candidate | Dominant caller | Verdict |
|---|---|---|
| `/callees`, `/callers`, `/handlers`, `/implements`, `/injects` | Interactive agent session ("show me X") | ✅ Ship — Tier 1 |
| `/controllers`, `/routes`, `/clients` | Interactive agent session ("list X in service Y") | ✅ Ship — Tier 1 |
| `/explain-feature`, `/impact-of`, `/trace-request-flow`, `/mini-map` | Interactive agent session, multi-step | ✅ Ship — Tier 2 (workflows) |
| `dump-routes`, `dump-symbols-by-role` | CI scripts, ad-hoc developer terminal | ❌ CLI helper, out of scope here |
| `who-changed-this`, `git-blame-via-mcp` | Interactive agent | ❌ No MCP primitive for it; out of scope until ontology covers VCS |
| `find-similar-code` | Interactive agent | ❌ Already covered by raw `search` — adding a skill is wasted surface |

Result: **14 skills total** — 10 Tier 1 + 4 Tier 2. No CLI work in this proposal.

## §4b Relationship to `java-codebase-explore` and existing Cursor skills

This repo already ships two adjacent agent artefacts — this propose must not fork a third parallel ontology copy.

| Artefact | Role | Relationship to Layer 3 navigation skills |
|---|---|---|
| [`docs/skills/java-codebase-explore.md`](../docs/skills/java-codebase-explore.md) | **Strategy guide** — missions, pre-flight `meta`, when MCP is the wrong layer, confidence/staleness, anti-capabilities | **Complements** navigation skills. Exploration skill links to AGENT-GUIDE for argument shapes; navigation skills link back to exploration skill for "understand this system" sessions. No duplicate cheat-sheet tables — exploration skill keeps strategy/mission content; navigation skills name exact MCP chains only. |
| [`docs/AGENT-GUIDE.md`](../docs/AGENT-GUIDE.md) | **Operating manual** — decision tree, recovery, edge taxonomy, `resolve` flow | PR-S-5 turns slash-alias bullets into pointers to `agent-skills/`; decision tree and taxonomy **stay** in AGENT-GUIDE. |
| [`.cursor/skills/`](../.cursor/skills/) (`propose`, `pr-review`, `plan-prompts`, …) | **Repo workflow skills** (Cursor-native) | Same `SKILL.md` format. `compile.py` emits **`.cursor/skills/<name>/SKILL.md`** alongside Claude/Qwen outputs so MCP navigation skills are slash-invokable in Cursor without maintaining a separate prose path. |

**Activation split:** use `java-codebase-explore` when the user is orienting on an unfamiliar estate; use `/routes`, `/callees`, etc. when the intent is a single structural question with a known chain.

## §5 The proposed skill set

### Tier 1 — Navigation (10 skills)

Direct wraps of the slash-style aliases already documented in `docs/AGENT-GUIDE.md` § "Slash-style aliases (prompt templates)". The aliases exist as prose templates today; this tier promotes them to first-class shipped skills with **graph-accurate** chains aligned to ontology v14 (`docs/EDGE-NAVIGATION.md`).

**Shared resolution rule (all Tier 1 skills that take an id or name):**

1. If the argument already starts with `sym:`, `route:`, `client:`, or `producer:`, use it directly.
2. Else call `resolve(identifier=<arg>, hint_kind=<symbol|route|client|producer as appropriate>)`.
3. On `status: many`, show `candidates` and stop for user pick; on `status: none`, fall back to `search(query=<arg>)` only when the skill is discovery-oriented (`/nl`).

| Slash | One-line purpose | MCP chain (after `resolve` when needed) |
|---|---|---|
| `/nl <text>` | Natural-language to graph navigation | `search({query, limit:8})` → `describe(top_hit.symbol_id)` when present |
| `/controllers <ms?>` | List controllers (optionally per service) | `find({kind:"symbol", filter:{role:"CONTROLLER", microservice?:ms}})` |
| `/routes <ms?>` | List HTTP routes (optionally per service) | `find({kind:"route", filter:{microservice?:ms}})` |
| `/clients <ms?>` | List outbound clients | `find({kind:"client", filter:{microservice?:ms}, limit:100})` — narrow with `client_kind` / `target_service` when needed |
| `/callers <id>` | Who calls this **method symbol** (in-process) | `neighbors({ids:sym_id, direction:"in", edge_types:["CALLS"]})` — for **routes**, use `/who-hits-route` instead |
| `/callees <id>` | What this **method symbol** calls (in-process) | `neighbors({ids:sym_id, direction:"out", edge_types:["CALLS"]})` — optional step 3 for outbound HTTP: `neighbors(out, ["DECLARES_CLIENT"])` on method id, then `neighbors(out, ["HTTP_CALLS"])` on each client id (same pattern for async via `DECLARES_PRODUCER` → `ASYNC_CALLS`) |
| `/handlers <route_id>` | Method that handles a route | `neighbors({ids:route_id, direction:"in", edge_types:["EXPOSES"]})` |
| `/who-hits-route <route_id>` | All inbound paths to a route | `neighbors({ids:route_id, direction:"in", edge_types:["HTTP_CALLS","ASYNC_CALLS","EXPOSES"]})` |
| `/implements <type_id>` | Concrete classes implementing an interface | `neighbors({ids:type_sym_id, direction:"in", edge_types:["IMPLEMENTS"]})` |
| `/injects <type_id>` | Where a type is injected | `neighbors({ids:type_sym_id, direction:"in", edge_types:["INJECTS"]})` |

Each skill body must include: trigger description for auto-discovery, exact MCP call(s) with required parameters, the shared resolution rule above, worked example with the bank-chat-system fixture, expected output shape. `/callees` **Out of scope** must mention `/mini-map` for noisy CALLS subgraphs and raw `/callees` as fallback when `/mini-map` returns a suspiciously thin map.

### Tier 2 — Workflow (4 skills)

Multi-step intents that compose Tier 1 with explicit bounds. Bodies must specify recursion depth, dedup, stop condition, and render format.

| Slash | Purpose | Chain shape (graph-accurate) |
|---|---|---|
| `/explain-feature <text>` | Understand how a feature works end-to-end | `search` → pick top 1–3 hits (role/symbol_kind fit) → `describe` each → walk with `neighbors` using **small** `edge_types` per step (usually `CALLS` on methods; `EXPOSES` / transport chains at boundaries) until stopping rule in skill body |
| `/impact-of <id>` | What breaks if this changes | `resolve` → `describe` → recursive `neighbors(in, ["CALLS","INJECTS","IMPLEMENTS","EXTENDS"])` depth ≤2 on symbols; for route/client impact include inbound `HTTP_CALLS`/`ASYNC_CALLS`/`EXPOSES` where applicable; dedupe; render impact list |
| `/trace-request-flow <route_or_path>` | Follow a request from HTTP entry to persistence/async | `resolve(..., hint_kind="route")` or `find(kind="route", filter={path_prefix:…})` → `neighbors(in, ["EXPOSES"])` to handler method → `neighbors(out, ["CALLS"])` depth ≤4 on methods → at HTTP/async boundaries `neighbors(out, ["DECLARES_CLIENT"])` then `neighbors(out, ["HTTP_CALLS"])` on client ids (or producer chain for async); ordered sequence render |
| `/mini-map <seed_id> [depth?]` | Noise-filtered call map for a method | `resolve` → `neighbors(out, ["CALLS"])` per hop only (not `HTTP_CALLS` on `sym:`) → heuristic filter → recurse on `DELEGATES`/`PUBLISHES` labels up to `depth` (default 2, max 4) |

#### `/mini-map` — detailed design

**Motivation.** The CALLS-edge noise problem ([#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)) makes every Tier 2 workflow that walks CALLS edges suffer from context-window pollution. `neighbors(out, ["CALLS"])` on a typical service method returns ~35 edges where ~7 are signal — the rest is entity getters/setters, phantom/chained-receiver edges, and JDK utility calls. `/mini-map` is the remedy: it absorbs the full edge set, applies heuristic classification, and returns only the business-logic-relevant skeleton.

**Designed for subagent invocation (preferred).** The subagent absorbs the full 35-edge-per-hop noise in its own context, applies the filter, and returns a compact result. The main agent receives a 10–20 line map and uses file reads to drill into specific methods. **Graceful degradation:** on hosts without subagents (or tight context budgets), run in the main agent with **`depth` default 1** and cap total raw edges examined; if the map has fewer than 3 signal lines after filtering, fall back to raw `/callees` and report that roles may be missing.

```
Main agent                          MiniMap subagent
    │                                      │
    │  "Build mini-map from               │
    │   POST /chat/assign, depth=2"       │
    │─────────────────────────────────────►│
    │                                      │── resolve(identifier=…)
    │                                      │── neighbors(in, [EXPOSES])  (if seed is route)
    │                                      │── neighbors(out, [CALLS]) → 35 edges
    │                                      │   ↳ classify → 6 signal edges
    │                                      │── neighbors(out, [CALLS]) on each
    │                                      │   delegate (depth 2) → more filtering
    │                                      │
    │  Compact map (15-20 lines):         │
    │◄─────────────────────────────────────│
```

**Classification rules** (heuristic, lives in skill body):

1. **Skip phantom/chained.** `attrs.strategy in (phantom, chained_receiver)` or `attrs.confidence < 0.3` → discard.
2. **Skip JDK/library.** `fqn` starts with `java.`, `javax.`, `org.slf4j.`, `org.apache.logging.`, `lombok.` → discard.
3. **Skip entity accessors.** Callee is a getter (`get*`), setter (`set*`), or constructor (`<init>`) on a type whose parent has `role=DTO` or whose name matches `*Entity`, `*Request`, `*Response`, `*Event`, `*DTO` → discard.
4. **Classify remainder:**
   - Callee's parent has `role in (REPOSITORY, MAPPER)` → label `PERSISTS` or `READS` (heuristic: `find*`/`get*` = reads, `save*`/`delete*` = persists).
   - Callee's parent has `role=SERVICE` or capabilities include `SCHEDULED_TASK`/`KAFKA_LISTENER` → label `DELEGATES`.
   - Callee's parent has `role=CLIENT` or `role=COMPONENT` with publisher capabilities → label `PUBLISHES`.
   - Everything else surviving → label `CALLS`.
5. **Deduplicate.** Same callee FQN from multiple call sites collapses to one line with a `(×N)` count.
6. **Recurse** on `DELEGATES` and `PUBLISHES` targets up to the depth limit.

**Output shape** (worked example — `ChatManagementService#assign`):

```
ChatManagementService#assign(AssignmentRequest)
  DELEGATES → SplitResolverService#resolveSplitName(String)
  DELEGATES → DistributionTriggerPublisher#publishTrigger()
  PERSISTS  → AssignChatRepository#save (×2)
  PERSISTS  → AssignQueueRepository#save
  READS     → AssignChatRepository#findByConversationId(String)
  READS     → AssignQueueRepository#findByAssignChat_Id(UUID)
  [filtered 29 edges: 15 entity accessors, 13 phantom/JDK, 1 duplicate]
```

7 lines instead of 35. The `[filtered …]` line gives the main agent a transparency signal — it can ask for the raw edges if needed.

**Argument contract:** seed id (with `sym:` / `route:` prefix, or bare name → `resolve`) + optional `depth` (default 2, max 4) + optional `microservice` scope.

**Stop condition:** depth limit reached, or no `DELEGATES`/`PUBLISHES` targets to recurse on, or all remaining callees are already in the map (cycle detection).

### Tier 3 — CLI helpers (NOT in this proposal)

Out of scope. If the user later wants `java-codebase-rag dump-routes` for scripting, that's a separate proposal. Frame: "graph debug helper for CI/scripts," not "list_routes for users."

## §6 Layout and build

### Source layout

```
java-codebase-rag/                         ← repo root (not a package prefix)
  agent-skills/                            ← source of truth
    callees/
      SKILL.md
    callers/
      SKILL.md
    controllers/
      SKILL.md
    ...
    explain-feature/
      SKILL.md
    impact-of/
      SKILL.md
    mini-map/
      SKILL.md
    trace-request-flow/
      SKILL.md
    README.md                              ← layout, layer diagram, compile, host precedence probe
    compile.py                             ← copies to .claude / .qwen / .cursor skills dirs
```

### SKILL.md template (verified compatible with Claude Code, Qwen Code, and Cursor)

```markdown
---
name: callees
description: Show what a method symbol calls (in-process CALLS). Use when the user asks "what does X call", "callees of X", or "what does X invoke". Argument is a sym: id, or an identifier resolved via resolve.
---
# /callees — Show callees of a method symbol

## Argument contract

Single positional argument: a method **symbol** id (`sym:…` preferred) OR an identifier-shaped string (FQN fragment, method signature) → `resolve(identifier=…, hint_kind="symbol")`.

This skill is for **method symbols**. For inbound traffic to an HTTP route, use `/who-hits-route`. For outbound Feign/HTTP from a method, see optional step 3 below.

## Steps

1. **Resolve.** If the argument starts with `sym:`, use it. Otherwise:
   `resolve(identifier=<arg>, hint_kind="symbol")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `search(query=<arg>, limit=5)` and stop if still empty.
2. **In-process callees:**
   `neighbors({ids: <sym_id>, direction: "out", edge_types: ["CALLS"]})`.
   Render grouped by edge type; show callee `fqn` + `microservice`.
3. **Optional — outbound HTTP (only when user asks about cross-service calls):**
   `neighbors({ids: <sym_id>, direction: "out", edge_types: ["DECLARES_CLIENT"]})`
   → for each client id:
   `neighbors({ids: <client_id>, direction: "out", edge_types: ["HTTP_CALLS"]})`.
   (Async: `DECLARES_PRODUCER` → `ASYNC_CALLS` on producer ids.)

## Worked example

User: /callees ChatController#joinOperator(JoinOperatorRequest)
You: → resolve(identifier="ChatController#joinOperator", hint_kind="symbol")
   → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
   → neighbors({ids:"sym:…", direction:"out", edge_types:["CALLS"]})
   → returns CALLS edges to in-process service methods
   → (optional step 3) neighbors(out, ["DECLARES_CLIENT"]) on sym id, then HTTP_CALLS from client ids

## Out of scope

- Recursive callees beyond depth 1 (use `/trace-request-flow` or `/mini-map`).
- Noisy CALLS subgraphs on service methods (prefer `/mini-map`; fall back here if the map is too thin).
- Filtering by microservice (compose with `/controllers` if needed).
```

### Compile step

`agent-skills/compile.py` reads `agent-skills/*/SKILL.md` and writes (copy mode; symlink mode optional in PR-S-3):

- `.claude/skills/<name>/SKILL.md`
- `.qwen/skills/<name>/SKILL.md`
- `.cursor/skills/<name>/SKILL.md`

Each output is prefixed with `# AUTOGENERATED — edit agent-skills/<name>/SKILL.md and run java-codebase-rag compile-skills`. Compile is idempotent. Invoked via **`java-codebase-rag compile-skills`** (new CLI subcommand) or `make skills` (deferred to plan).

**Git policy:** source under `agent-skills/` is canonical; compiled host dirs are **checked in** for zero-setup clones, with CI verifying banner + hash match. Teams that prefer source-only git may switch in the plan — default here is checked-in outputs for Claude/Qwen/Cursor parity with existing `.cursor/skills/` practice.

### Where they install for users

| Host | Project-scoped | User-scoped |
|---|---|---|
| Claude Code | `.claude/skills/<name>/SKILL.md` | `~/.claude/skills/<name>/SKILL.md` |
| Qwen Code | `.qwen/skills/<name>/SKILL.md` | `~/.qwen/skills/<name>/SKILL.md` |
| Cursor | `.cursor/skills/<name>/SKILL.md` | (user rules / global skills — out of scope; copy manually if needed) |

This proposal ships **project-scoped** skills checked into the repo.

## §7 Use-case re-walk

16 realistic use cases, walked through the proposed Layer-3 surface.

| # | User intent | Slash | MCP chain | Calls |
|---|---|---|---|---|
| UC1 | List all controllers | `/controllers` | `find(symbol, {role:CONTROLLER})` | 1 |
| UC2 | List controllers in chat-core | `/controllers chat-core` | `find(symbol, {role:CONTROLLER, microservice:chat-core})` | 1 |
| UC3 | List all HTTP routes | `/routes` | `find(route, {})` | 1 |
| UC4 | Routes in chat-assign | `/routes chat-assign` | `find(route, {microservice:chat-assign})` | 1 |
| UC5 | Who calls ChatController#joinOperator | `/callers …` | `resolve` → `neighbors(in, ["CALLS"])` on `sym:` | 2 |
| UC6 | What does ChatController#joinOperator call | `/callees …` | `resolve` → `neighbors(out, ["CALLS"])` | 2 |
| UC7 | What handles `POST /chat/join` | `/handlers route:…` | `neighbors(in, ["EXPOSES"])` | 1 |
| UC8 | Concrete impls of `OperatorAssignmentService` | `/implements sym:…` | `neighbors(in, ["IMPLEMENTS"])` | 1 |
| UC9 | Where is `OperatorAssignmentService` injected | `/injects sym:…` | `neighbors(in, ["INJECTS"])` | 1 |
| UC10 | Outbound clients in chat-core | `/clients chat-core` | `find(client, {microservice:chat-core}, limit=100)` | 1 |
| UC11 | "How does operator assignment work?" | `/explain-feature …` | `search` → `describe` × ≤3 → bounded `neighbors` | 5–10 |
| UC12 | What breaks if I change `ChatRepository` | `/impact-of sym:…` | `resolve` → bounded `neighbors` in + out per §5 Tier 2 | 4–8 |
| UC13 | Trace `POST /chat/escalate` end-to-end | `/trace-request-flow POST /chat/escalate` | `resolve`/`find(route)` → `neighbors(in,["EXPOSES"])` → `neighbors(out,["CALLS"])` × depth → client/producer hops | 5–10 |
| UC14 | "Find authentication-related code" | `/nl authentication` | `search` → `describe(top_hit)` | 2 |
| UC15 | All `@Scheduled` methods in chat-core | (no skill) — raw `find(symbol, {capability:SCHEDULED_TASK, microservice:chat-core})` | 1 |
| UC16 | Map what `ChatManagementService#assign` does | `/mini-map …` | `resolve` → `neighbors(out,["CALLS"])` → filter → recurse (subagent preferred) | 5–10 |

UC15 deliberately has no skill. UC16 is the canonical mini-map case (see [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)). **No use case requires a primitive that doesn't exist.**

## §8 What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Add MCP tools | Defeats v2's load-bearing small navigation surface. Skills do this work better. |
| Add CLI query subcommands | Different audience (ops vs. queries), different home. Tier 3 candidate, separate proposal. |
| Add skills for every possible filter combination | Long tail belongs in raw MCP. Skills are for high-frequency intents only. |
| Per-user customization of skill chains | Out of scope — ship one canonical chain per skill. Users can override at host user-scope dirs. |
| Skill versioning independent of MCP | Skills track MCP. Lockstep updates only. |
| Duplicate `java-codebase-explore` content | Strategy stays in `docs/skills/java-codebase-explore.md`; navigation chains stay in `agent-skills/`. |
| `/git-blame`, `/who-changed-this` | Requires VCS data not yet in the ontology. Out of scope until that's modeled. |
| Extra compile hosts (VS Code, Continue) | Three hosts (Claude, Qwen, Cursor) match current repo practice. Add hosts when there is a fourth real consumer. |
| Skills that reach into the CLI | Skills run at the agent layer; the agent calls MCP. CLI is for humans. Don't cross the streams. |
| One-hop `HTTP_CALLS` / `ASYNC_CALLS` on bare `sym:` ids | Graph endpoints are Client→Route and Producer→Route; use `/who-hits-route` on routes or DECLARES_* chains from methods (decision #13). |
| `/callers-direct` / `/callees-direct` as separate shipped skills | `/callers` and `/callees` **are** CALLS-only on method symbols (matches AGENT-GUIDE). Transport-wide "who hits this route" is `/who-hits-route`. Optional HTTP/async steps live in `/callees` step 3 and `/trace-request-flow`. |
| Server-side CALLS-edge noise filtering | `/mini-map` solves at skill layer ([#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)). Separate propose if usage justifies MCP filters. |

## §9 Migration plan — 5 PRs

### PR-S-1 — Lock the propose

Open this propose as a draft PR. Iterate. When merged, status flips to `locked` and the migration begins. No code yet.

**Test summary**: N/A.

### PR-S-2 — Shared `agent-skills/` source + compile script

Add `agent-skills/` with `README.md` (architecture + Layer-3 diagram + compile + host precedence probe), a **minimal-working `compile.py`** (≤ 100 lines: walk `agent-skills/*/SKILL.md`, write to `.claude/skills/`, `.qwen/skills/`, and `.cursor/skills/` with the `# AUTOGENERATED` banner; copy mode only — symlink mode lands in PR-S-3 if needed), and the **`java-codebase-rag compile-skills`** CLI subcommand. Skip SKILL.md bodies — those land in PR-S-3/4.

The compile script must be functional from this PR onwards (not stubbed). **No `NotImplementedError` placeholders.**

**Acceptance criterion (manual, both Claude Code and Qwen Code):** project-scoped install takes precedence over user-scoped when names collide. Document procedure and **record results for each host** in `agent-skills/README.md` and the PR description.

**Test summary**: 3 tests — (1) idempotent on empty source dir, (2) copies one fixture skill to all three output trees with banner, (3) `java-codebase-rag compile-skills` invokes the same code path.

### PR-S-3 — Tier 1 navigation skills (10 skills)

Add `agent-skills/<slash>/SKILL.md` for each Tier 1 entry. Run compile. Commit `.claude/skills/`, `.qwen/skills/`, and `.cursor/skills/` outputs. Each SKILL.md includes worked example + graph-accurate chains from §5.

**Test summary**: 10 frontmatter tests + 1 compile integration test + **1 static MCP-call validator** (`tests/test_agent_skills_static.py`, ~80 lines) that imports allowlists from code — not hand-maintained lists:

- **Tools:** `search`, `find`, `describe`, `neighbors`, `resolve` (same set as `server.py` navigation tools).
- **`find` `kind`:** `symbol`, `route`, `client`, `producer` — from `mcp_v2` / `NodeKind` (see `_NODEFILTER_APPLICABLE_FIELDS` keys).
- **`direction`:** `in`, `out`.
- **`edge_types`:** `mcp_v2.EdgeType` literals plus `mcp_v2.ComposedEdgeType` dot-keys (11 stored labels + 3 composed keys today).

### PR-S-4 — Tier 2 workflow skills (4 skills)

Add `/explain-feature`, `/impact-of`, `/trace-request-flow`, `/mini-map`. `/mini-map` requires `## Classification rules` and `## Output shape`; all Tier 2 require `## Stop conditions` and `## Recursion limit`.

**Test summary**: 4 frontmatter + 4 body-structure tests; **extend the static validator to all 14 skills**.

### PR-S-5 — `AGENT-GUIDE.md` rewrite

Slash-style aliases become a pointer to `agent-skills/` (and compiled host paths). Forced reasoning preamble, decision tree, edge taxonomy, and **`resolve` flow stay** in AGENT-GUIDE. README gets Layer-3 diagram from §3. Cross-link `docs/skills/java-codebase-explore.md` in §4b style (strategy vs navigation).

**Test summary:** add `tests/test_agent_guide_consistency.py` with an assertion that the slash-aliases section references `agent-skills/` (or compiled skills) rather than embedding the full bullet chains.

**5 PRs total.** No ontology bump, no schema delta, no MCP surface change.

## §10 Decisions taken (no longer open)

1. **Skills live at Layer 3, not Layer 2 (MCP) or ops CLI.**
2. **Single source at `agent-skills/<name>/SKILL.md`.** Compile to `.claude/skills/`, `.qwen/skills/`, `.cursor/skills/`.
3. **Identical SKILL.md format on all three hosts** — frontmatter `name` + `description` only (Appendix A).
4. **Project-scoped install by default** — checked into repo.
5. **Slash name = skill name = directory name.**
6. **No CLI query subcommands** in this proposal.
7. **No new MCP tools** as part of skill rollout.
8. **Cursor is in scope via `.cursor/skills/` compile target** — same artefact as Claude/Qwen; not deferred to AGENT-GUIDE prose only.
9. **Skill set = 10 Tier 1 + 4 Tier 2 = 14 total.**
10. **Lockstep versioning with MCP** — skills + AGENT-GUIDE + README in the same PR when navigation semantics change.
11. **`agent-skills/compile.py` + `java-codebase-rag compile-skills`.**
12. **Three test levels:** (a) schema/frontmatter, (b) static MCP-call validation against `mcp_v2` allowlists, (c) NOT full host E2E.
13. **Graph-accurate `/callers` and `/callees` on method symbols use `CALLS` only** — matches `docs/AGENT-GUIDE.md` and ontology v14. Cross-service inbound to a **route** is `/who-hits-route` (`HTTP_CALLS`, `ASYNC_CALLS`, `EXPOSES`). Outbound HTTP/async from a **method** uses optional `DECLARES_CLIENT` → `HTTP_CALLS` or `DECLARES_PRODUCER` → `ASYNC_CALLS` steps documented in `/callees` and `/trace-request-flow`, not one-hop `HTTP_CALLS` on `sym:`.
14. **`/mini-map` = skill-layer heuristic over `CALLS` hops**, subagent-preferred; not an MCP contract change ([#177](https://github.com/HumanBean17/java-codebase-rag/issues/177)).
15. **`resolve` is the default identifier path** for Tier 1 skills; `find` is for structured listing only.
16. **`java-codebase-explore` stays the strategy guide**; navigation skills do not duplicate its missions or anti-capability sections (§4b).

## §11 Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Skills drift from MCP behaviour | Lockstep (decision #10) + static validator importing `mcp_v2` allowlists (decision #12) |
| Compile produces stale output | `# AUTOGENERATED` banner; CI compile + consistency check |
| Tier 2 workflows blow context | Fixed depth caps; `/mini-map` subagent + thin-map fallback to `/callees` |
| Host precedence ambiguity | Manual probe **per host** documented in PR-S-2 (Claude + Qwen; Cursor noted separately) |
| Weak models pick wrong skill | Rich `description` trigger phrases per skill |
| Users expect v1 `callers_of` to include HTTP on methods | Decision #13 + skill docs: CALLS on `sym:`; transport via `/who-hits-route` or optional DECLARES_* steps |
| `/mini-map` misclassifies signal as noise | `[filtered N edges]` line; fallback to raw `/callees` when map &lt; 3 lines |
| Subagent unavailable | `/mini-map` runs in main agent with lower default depth (decision #14) |
| Overlap with `java-codebase-explore` | §4b activation split; no duplicated cheat-sheet ownership |
| Maintenance cost (14 skills) | Static validator + lockstep rule |

## Appendix A — Concrete artefacts

### A.1 SKILL.md format compatibility (verified May 2026)

Both **Claude Code** and **Qwen Code** accept:

```yaml
---
name: <slug>
description: <what + when>
---
```

**Cursor** uses the same `SKILL.md` under `.cursor/skills/<name>/` (this repo already ships `propose`, `pr-review`, `plan-prompts`, etc.).

Sources:
- Claude Code: [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills)
- Qwen Code: [qwenlm.github.io/qwen-code-docs/en/users/features/skills](https://qwenlm.github.io/qwen-code-docs/en/users/features/skills/)

Decision: source uses only `name` + `description`. Host-specific optional fields stay out of source unless all three hosts accept them.

### A.2 Layer diagram (canonical, copy verbatim into README §1)

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /callees, /controllers, /routes,      │
│   /impact-of, /mini-map                                      │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: SKILL.md in .claude/.qwen/.cursor/skills │
│   (one agent-skills/ source). Tier 1 = deterministic chains; │
│   Tier 2 = bounded workflows + /mini-map heuristics.         │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — Composable primitives (the MCP API)                │
│   search, find, describe, neighbors, resolve                 │
├──────────────────────────────────────────────────────────────┤
│ Layer 1 — Storage primitives                                 │
│   Kuzu Cypher + LanceDB tables                               │
└──────────────────────────────────────────────────────────────┘
```

### A.3 SKILL.md template

See §6 — `/callees` is the canonical Tier 1 template. Tier 2 adds `## Stop conditions` and `## Recursion limit`; `/mini-map` adds `## Classification rules` and `## Output shape`.

## Appendix B — What changed (traceability)

### What stayed unchanged (through revision 3)

- 3-layer mental model + ASCII diagram (updated labels only).
- 10 Tier 1 + 4 Tier 2 skill **names** and overall 5-PR migration.
- Single `agent-skills/` source with multi-host compile.
- Lockstep versioning + static validator (now imports `mcp_v2` allowlists).
- `/mini-map` as skill-layer answer to [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177).

### Revision 1 (2026-05-08)

Working `compile.py` in PR-S-2; manual host precedence probe; static MCP-call validator; decision #12 three test levels. (See prior Appendix B in git history.)

### Revision 2 (2026-05-17)

Added `/mini-map`, skill count 13→14, decision #14, [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177) blocker split.

### Revision 3 (2026-05-19) — critical review fixes

1. **5-tool MCP** — `resolve` added everywhere; diagram, validator, and decisions updated (was incorrectly frozen at 4 tools post-`resolve` ship).
2. **`java-codebase-rag` naming** — replaced stale `user-rag` paths and CLI subcommand name.
3. **Graph-accurate chains** — reverted incorrect one-hop `HTTP_CALLS`/`ASYNC_CALLS` on `sym:` for `/callers`/`/callees`; rewrote `/trace-request-flow`, `/impact-of`, and `/mini-map` hops; decision #13 replaced with graph-accurate decision #13–#16.
4. **`resolve` first** — Tier 1 template and shared resolution rule; dropped `find({fqn_prefix})` as primary disambiguation.
5. **§4b** — relationship to `java-codebase-explore`, AGENT-GUIDE, and existing `.cursor/skills/`.
6. **Cursor compile target** — `.cursor/skills/` is a first-class output (decision #8); removed incorrect "Cursor has no slash skills" claim.
7. **Static validator** — allowlists sourced from `mcp_v2` (`EdgeType`, `ComposedEdgeType`, find kinds including `producer`); removed bogus `text` kind.
8. **Principle split** — Tier 1 deterministic vs Tier 2 bounded/heuristic.
9. **Diagram / UC table** — slash names aligned with §5; UC15 column fix; 16 use cases.
10. **PR-S-5 test** — `test_agent_guide_consistency.py` specified as new file.
11. **Appendix B** — validator span corrected to 14 skills; revision 3 traceability.
