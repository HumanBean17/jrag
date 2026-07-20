> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Agent Skills and Commands — High-level intents over the 5-tool MCP

**Status**: draft (revision 5)
**Author**: Dmitry + Computer
**Date**: 2026-05-08 (revised 2026-05-24)
**Related**: [#177 — CALLS-edge noise](https://github.com/HumanBean17/java-codebase-rag/issues/177) (server-side work **landed** in [`propose/completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md`](../completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md), ontology **15**). `/mini-map` covers **remaining** noise (accessor heuristics, semantic labels); the 10 Tier 1 + other Tier 2 skills do not depend on #177.

## TL;DR

- The 5-tool MCP navigation API (`search` / `find` / `describe` / `neighbors` / `resolve`) traded surface area for composability — the convenience that v1 tools (`list_routes`, `where_used`, `callers_of`, `implementations_of`, `outbound_calls`, …) provided is gone from the MCP.
- That convenience belongs at a different layer: **agent-side skills and slash-commands**, not MCP tools and not CLI subcommands.
- Ship skills as `skills/<name>/SKILL.md` at the **project root**. All hosts read from this single directory. No compile step, no multi-host copy pipeline. Developer workflow skills (propose, pr-review, etc.) remain in `.agents/skills/` — `skills/` is for **consumer-facing** navigation and workflow skills only.
- Tier 1 (high-leverage, low cost): 10 navigation skills covering the 10 most common query patterns from the existing `AGENT-GUIDE.md` slash-style alias section — **graph-accurate one-hop chains**, identifier resolution via **`resolve`**.
- Tier 2 (polish): 4 **bounded workflow** skills (`/explain-feature`, `/impact-of`, `/trace-request-flow`, `/mini-map`) — explicit depth/stop rules; `/mini-map` composes optional MCP `edge_filter` on `CALLS` with skill-layer accessor/semantic labeling (not a strict chain).
- Tier 3 (deferred): `java-codebase-rag dump-*` CLI helpers — out of scope here.
- Migration is **4 PRs**: (1) propose lock, (2) `skills/` directory + Tier 1 skills, (3) Tier 2 workflow skills, (4) AGENT-GUIDE rewrite to point at the shipped skills instead of duplicating prose templates.

## §1 Frame: what is this thing, really?

**Skills and slash-commands are agent-side prompt scaffolding for high-level user intents — they are NOT a second MCP API and NOT a CLI.**

The MCP is a graph-and-vector navigator. It has **five** navigation tools because that is the smallest set that covers locate → inspect → walk without re-embedding v1 verbs. But "smallest set of primitives" and "things a developer wants to ask" are different shapes. A developer thinks `"who calls ChatController#joinOperator?"`, not `"resolve then neighbors with direction=in and edge_types=[CALLS]"`. Skills are how that gap gets bridged without breaking the MCP design.

This frame rules things out:

- **Skills are not a second MCP.** They contain no new graph queries, no new vector backends, no new edge types. Every Tier 1 skill is a **deterministic** chain of existing MCP calls; Tier 2 skills add **bounded** recursion and (for `/mini-map` only) heuristic post-processing on top of those calls.
- **Skills are not CLI subcommands.** The `java-codebase-rag` CLI is for ops (`init` / `increment` / `reprocess` / `meta` / `tables` / `diagnose-ignore` / `analyze-pr`). Adding `java-codebase-rag list-routes` would give the same query three homes (MCP + skill + CLI) — pick one.
- **Skills are not the AGENT-GUIDE.** The guide is reference doc that the agent reads once. Skills are *invokable* — the user types `/callees ChatController#joinOperator(JoinOperatorRequest)` and the model executes a known chain. Same intents, different actuation.
- **Skills are not free.** Each one is recurring tokens in the agent's context once invoked, plus maintenance cost when the MCP surface evolves. The set must be small and earn its place.

## §2 Design principles

1. **Single source of truth.** One markdown file per skill in `skills/<skill-name>/SKILL.md` at the project root. No compile step — all hosts read from `skills/` directly.
2. **Identical SKILL.md format across Claude Code, Qwen Code, and Cursor.** All three accept `SKILL.md` with YAML frontmatter (`name` + `description`) and a markdown body. Verified May 2026 for Claude/Qwen — see Appendix A. Developer workflow skills (`propose`, `pr-review`, etc.) live separately in `.agents/skills/`.
3. **Tier 1 = deterministic MCP chains.** No prose like "consider running `find` if appropriate" — the body must say exactly which calls to make and in what order. **Tier 2 = bounded workflows** with explicit recursion depth, stop conditions, and (for `/mini-map` only) documented heuristic filtering — not fully deterministic output.
4. **Skills wrap MCP, never replace it.** A skill body always names the underlying MCP tools used. This keeps the agent able to drop into raw MCP if the skill doesn't fit.
5. **Slash-name = skill-name = filename.** `/callees` ↔ `skills/callees/SKILL.md`. No alias indirection. (Hosts derive the slash-name from the directory name.)
6. **Identifier-shaped arguments → `resolve` first.** Each skill's body specifies positional arguments and calls `resolve(identifier=…)` (optional `hint_kind`) when the input is not already a `sym:` / `route:` / `client:` / `producer:` id. Use `find` only for structured listing (`/controllers`, `/routes`, …), not for FQN disambiguation.
7. **Graph-accurate edge sets.** One-hop `neighbors` must respect `EDGE_SCHEMA` endpoint types (see `docs/EDGE-NAVIGATION.md`). Do not pass `HTTP_CALLS` or `ASYNC_CALLS` on a bare method `sym:` id — those edges are **Client→Route** and **Producer→Route**; reach them via `DECLARES_CLIENT` / `DECLARES_PRODUCER` (or composed dot-keys) from the declaring method.
8. **Skills are versioned with the MCP, not separately.** When `NodeFilter` keys, `edge_filter` axes, `edge_types`, or `kind` values change, skills get updated in the same PR. Lockstep with `AGENT-GUIDE.md` and `README.md`.
9. **No skill ships without a working example.** Every SKILL.md ends with a worked example using the bank-chat-system fixture, so a maintainer can verify the chain still works after MCP changes.
10. **Skills compose with `hints_structured`.** MCP responses now include `hints_structured` — machine-parseable suggested next-tool calls with `tool`, `args`, `actionable`, `label`, and `reason` fields. Skills define the *intent-to-chain* mapping; `hints_structured` provides runtime *step-to-step* guidance within a chain. Skills should not duplicate what `hints_structured` already provides.

## §3 The three layers

This diagram lives at the top of the resulting README and is the canonical mental model:

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /callees, /controllers, /routes,      │
│   /impact-of, /mini-map                                      │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: SKILL.md files in skills/ at project root. │
│   Tier 1 = deterministic MCP chains; Tier 2 adds bounded     │
│   recursion + light post-processing (/mini-map).             │
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

- It's host-agnostic — one directory, no compile step, no host-specific tuning.
- It doesn't pollute the MCP surface — the 5-tool count stays load-bearing for tool-selection on weak models.
- It doesn't duplicate the CLI's audience — CLI is ops; skills are queries.
- It's a single `skills/` directory — no N× edits to keep hosts in sync.

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

## §4b Relationship to existing developer skills

This repo ships developer workflow skills in `.agents/skills/` (propose, pr-review, plan-prompts, etc.) — those are for contributors working **on** java-codebase-rag. Navigation skills in `skills/` are for **consumers** using java-codebase-rag to explore their own codebases. The two directories must not be confused.

| Artefact | Role | Relationship to Layer 3 navigation skills |
|---|---|---|
| [`.agents/skills/`](../../.agents/skills/) (`propose`, `pr-review`, `plan-prompts`, …) | **Repo workflow skills** for contributors | Same `SKILL.md` format, different audience. Navigation skills do **not** compile into `.agents/skills/`. |
| [`docs/AGENT-GUIDE.md`](../../docs/AGENT-GUIDE.md) | **Operating manual** — decision tree, recovery, edge taxonomy, `resolve` flow | PR-S-4 turns slash-alias bullets into pointers to `skills/`; decision tree and taxonomy **stay** in AGENT-GUIDE. |

**Activation split:** use navigation skills (`/routes`, `/callees`, etc.) when the intent is a single structural question with a known chain; use AGENT-GUIDE when the agent needs to reason about *which* tools to call.

## §5 The proposed skill set

### Tier 1 — Navigation (10 skills)

Direct wraps of the slash-style aliases already documented in `docs/AGENT-GUIDE.md` § "Slash-style aliases (prompt templates)". The aliases exist as prose templates today; this tier promotes them to first-class shipped skills with **graph-accurate** chains aligned to ontology **15** (`docs/EDGE-NAVIGATION.md`; CALLS-NOISE landed).

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
| `/callees <id>` | What this **method symbol** calls (in-process) | `neighbors({ids:sym_id, direction:"out", edge_types:["CALLS"]})` — optional `edge_filter` on hot methods (e.g. `{callee_declaring_role:"SERVICE"}`) per AGENT-GUIDE; optional step 3 for outbound HTTP: `neighbors(out, ["DECLARES_CLIENT"])` on method id, then `neighbors(out, ["HTTP_CALLS"])` on each client id (same pattern for async via `DECLARES_PRODUCER` → `ASYNC_CALLS`) |
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
| `/mini-map <seed_id> [depth?]` | Noise-filtered call map for a method | `resolve` → optional MCP `edge_filter` on `CALLS` → skill heuristics (accessors + labels) → recurse on `DELEGATES`/`PUBLISHES` up to `depth` (default 2, max 4); not `HTTP_CALLS` on bare `sym:` |

#### `/mini-map` — detailed design

**Motivation.** [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177) documented CALLS-edge noise on service methods. **Server-side remediation landed** ([`CALLS-NOISE-AND-RESOLUTION`](../completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md)): true receiver-failure sites moved off `CALLS`, `callee_declaring_role` is on the edge, and `neighbors` supports `edge_filter`, source ordering, `dedup_calls`, and `include_unresolved`. Default `neighbors(out, ["CALLS"])` on a high-fanout method (pinned fixture: `ClientMessageProcessor#process`) is **~49 rows** after re-index (was **57** pre–PR-3) — still dominated by entity accessors and known-external JDK rows that stereotype filters alone cannot label (`DELEGATES` / `PERSISTS` / `READS`). `/mini-map` is the **skill-layer** remedy for that remainder (Decision 39 in CALLS-NOISE): compose MCP projection first, then accessor/semantic heuristics, return a 10–20 line skeleton.

**Designed for subagent invocation (preferred).** The subagent runs the MCP + heuristic pipeline per hop in its own context and returns a compact map. The main agent drills in with file reads. **Graceful degradation:** on hosts without subagents (or tight context budgets), run in the main agent with **`depth` default 1** and cap total raw edges examined; if the map has fewer than 3 signal lines after filtering, fall back to raw `/callees` (optionally with `edge_filter`) and note that stereotype roles may be `OTHER`.

```
Main agent                          MiniMap subagent
    │                                      │
    │  "Build mini-map from               │
    │   ClientMessageProcessor#process"   │
    │─────────────────────────────────────►│
    │                                      │── resolve(identifier=…)
    │                                      │── neighbors(out, [CALLS]) → ~49 rows
    │                                      │── optional edge_filter trim
    │                                      │   ↳ skill classify → ~8–12 signal
    │                                      │── neighbors(out, [CALLS]) on DELEGATES
    │                                      │   (depth 2) → repeat pipeline
    │                                      │
    │  Compact map (10–20 lines):         │
    │◄─────────────────────────────────────│
```

**Pipeline per hop** (lives in skill body; do not duplicate `edge_filter` semantics in prose when MCP can do it):

1. **Fetch ordered `CALLS`.** `neighbors({ids: sym_id, direction: "out", edge_types: ["CALLS"]})` — rows are source-ordered (`call_site_line`, `call_site_byte`). Do **not** expect `strategy in (phantom, chained_receiver)` on `CALLS` after ontology 15 PR-3; unresolved receiver/chained sites are on `UnresolvedCallSite` (see `describe` rollup or `include_unresolved=True`, which is **mutually exclusive** with `edge_filter`).
2. **Optional MCP trim** (prefer over hand-rolled FQN rules when it fits):
   - Skeleton pass: `edge_filter={callee_declaring_role: "SERVICE"}` for delegation hops only.
   - Trim JDK/low-signal: `edge_filter={min_confidence: 0.5}` and/or `exclude_callee_declaring_roles: ["OTHER"]}` (blunt — also drops known-external rows; document in output).
   - Collapse identical callees: `dedup_calls=True` (optional; skill may still `(×N)` in prose).
   - Full transcript with unresolved sites: `include_unresolved=True` **only when not using `edge_filter`** on the same call.
3. **Skill heuristics** (what `callee_declaring_role` cannot do — CALLS-NOISE Decision 39):
   - **Skip entity accessors.** Callee simple name is `get*` / `set*` / `is*` / `<init>` on types matching `*Entity`, `*Request`, `*Response`, `*Event`, `*DTO`, or parent `role=DTO`.
   - **Skip JDK/library** when step 2 did not run: `fqn` prefix `java.`, `javax.`, `org.slf4j.`, `lombok.`, etc. (on `neighbors`, `exclude_external` is **not** available — use `edge_filter` or this fallback).
4. **Classify remainder** (use `attrs.callee_declaring_role` when present, else callee parent `role` from `describe`):
   - `REPOSITORY` / `MAPPER` → `PERSISTS` or `READS` (`find*`/`get*` vs `save*`/`delete*`).
   - `SERVICE` or listener/scheduled capabilities → `DELEGATES`.
   - `CLIENT` or publisher component → `PUBLISHES`.
   - Else → `CALLS`.
5. **Deduplicate** for display. Prefer `dedup_calls=True` from MCP when collapsing identical `(src, dst)`; otherwise same FQN → one line with `(×N)`.
6. **Recurse** on `DELEGATES` and `PUBLISHES` targets up to `depth` (default 2, max 4).

**Output shape** (illustrative — re-verify counts on fresh `bank-chat-system` re-index):

```
ClientMessageProcessor#process(ProcessingContext, InternalEvent)
  DELEGATES → …SplitResolverService#…
  DELEGATES → …DistributionTriggerPublisher#…
  PERSISTS  → …Repository#save (×2)
  READS     → …Repository#find…
  [filtered ~37 edges: ~22 accessors, ~10 JDK/OTHER (edge_filter optional), ~5 deduped]
```

The `[filtered …]` line is transparency — offer raw `/callees` or `neighbors` with a documented `edge_filter` if the map looks too thin.

**Argument contract:** seed id (`sym:` / `route:` / bare name → `resolve`) + optional `depth` (default 2, max 4) + optional `microservice` scope.

**Stop condition:** depth limit reached, or no `DELEGATES`/`PUBLISHES` targets to recurse on, or cycle detected (callee already in map).

### Tier 3 — CLI helpers (NOT in this proposal)

Out of scope. If the user later wants `java-codebase-rag dump-routes` for scripting, that's a separate proposal. Frame: "graph debug helper for CI/scripts," not "list_routes for users."

## §6 Layout

### Source layout

```
java-codebase-rag/                         ← repo root (not a package prefix)
  skills/                                  ← user-facing skills (Layer 3)
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
    README.md                              ← layout, layer diagram, skill index
  .agents/                                 ← developer workflow skills (NOT for consumers)
    skills/
      propose/SKILL.md
      pr-review/SKILL.md
      ...
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

### Where skills live

All hosts read from the same `skills/` directory at the project root. No per-host copies, no compile step.

| Host | Skill location |
|---|---|
| Claude Code | `skills/<name>/SKILL.md` (project root) |
| Qwen Code | `skills/<name>/SKILL.md` (project root) |
| Cursor | `skills/<name>/SKILL.md` (project root) |

Developer workflow skills (propose, pr-review, etc.) live in `.agents/skills/` — a separate directory for repo contributors, not consumers.

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
| UC16 | Map what `ClientMessageProcessor#process` does | `/mini-map …` | `resolve` → `neighbors(out,["CALLS"])` → optional `edge_filter` → skill heuristics → recurse (subagent preferred) | 5–10 |

UC15 deliberately has no skill. UC16 is the canonical mini-map case (pinned in CALLS-NOISE; [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177) server work landed). **No use case requires a primitive that doesn't exist.**

## §8 What this deliberately does NOT do

| Question / feature | Why we skip it |
|---|---|
| Add MCP tools | Defeats v2's load-bearing small navigation surface. Skills do this work better. |
| Add CLI query subcommands | Different audience (ops vs. queries), different home. Tier 3 candidate, separate proposal. |
| Add skills for every possible filter combination | Long tail belongs in raw MCP. Skills are for high-frequency intents only. |
| Per-user customization of skill chains | Out of scope — ship one canonical chain per skill. Users can override at host user-scope dirs. |
| Skill versioning independent of MCP | Skills track MCP. Lockstep updates only. |
| Compile step or multi-host copy pipeline | Single `skills/` directory at project root. All hosts read from it directly. |
| `/git-blame`, `/who-changed-this` | Requires VCS data not yet in the ontology. Out of scope until that's modeled. |
| Skills that reach into the CLI | Skills run at the agent layer; the agent calls MCP. CLI is for humans. Don't cross the streams. |
| One-hop `HTTP_CALLS` / `ASYNC_CALLS` on bare `sym:` ids | Graph endpoints are Client→Route and Producer→Route; use `/who-hits-route` on routes or DECLARES_* chains from methods (decision #13). |
| `/callers-direct` / `/callees-direct` as separate shipped skills | `/callers` and `/callees` **are** CALLS-only on method symbols (matches AGENT-GUIDE). Transport-wide "who hits this route" is `/who-hits-route`. Optional HTTP/async steps live in `/callees` step 3 and `/trace-request-flow`. |
| Re-implement CALLS-NOISE in skill prose only | `edge_filter`, phantom/chained removal, and `callee_declaring_role` **landed** ([`CALLS-NOISE-AND-RESOLUTION`](../completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md)). `/mini-map` **composes** MCP filters; skill heuristics cover accessor noise and semantic labels only (Decision 39). |
| Port `/mini-map` classification into the indexer | Intentional skill-layer split; server closes stereotype + receiver-failure buckets. |
| `include_unresolved=True` together with `edge_filter` on the same `neighbors` call | MCP fail-loud mutual exclusivity (CALLS-NOISE Decision 25); skill must pick one mode per hop. |

## §9 Migration plan — 4 PRs

### PR-S-1 — Lock the propose

Open this propose as a draft PR. Iterate. When merged, status flips to `locked` and the migration begins. No code yet.

**Test summary**: N/A.

### PR-S-2 — `skills/` directory + Tier 1 navigation skills (10 skills)

Add `skills/` directory with `README.md` (architecture + Layer-3 diagram + skill index). Add `skills/<slash>/SKILL.md` for each Tier 1 entry. Each SKILL.md includes worked example + graph-accurate chains from §5.

**Test summary**: 10 frontmatter tests + **1 static MCP-call validator** (`tests/test_agent_skills_static.py`, ~80 lines) that imports allowlists from code — not hand-maintained lists:

- **Tools:** `search`, `find`, `describe`, `neighbors`, `resolve` (same set as `server.py` navigation tools).
- **`find` `kind`:** `symbol`, `route`, `client`, `producer` — from `mcp_v2` / `NodeKind` (see `_NODEFILTER_APPLICABLE_FIELDS` keys).
- **`direction`:** `in`, `out`.
- **`edge_types`:** `mcp_v2.EdgeType` literals plus `mcp_v2.ComposedEdgeType` dot-keys (11 stored labels + 3 composed keys today).

### PR-S-3 — Tier 2 workflow skills (4 skills)

Add `/explain-feature`, `/impact-of`, `/trace-request-flow`, `/mini-map`. `/mini-map` requires `## MCP pre-filter (optional)`, `## Classification rules`, and `## Output shape`; all Tier 2 require `## Stop conditions` and `## Recursion limit`. Skill examples assume ontology **15** graph (re-indexed `bank-chat-system`).

**Test summary**: 4 frontmatter + 4 body-structure tests; **extend the static validator to all 14 skills**.

### PR-S-4 — `AGENT-GUIDE.md` rewrite

Slash-style aliases become a pointer to `skills/`. Forced reasoning preamble, decision tree, edge taxonomy, and **`resolve` flow stay** in AGENT-GUIDE. README gets Layer-3 diagram from §3.

**Test summary:** add `tests/test_agent_guide_consistency.py` with an assertion that the slash-aliases section references `skills/` rather than embedding the full bullet chains.

**4 PRs total.** No ontology bump, no schema delta, no MCP surface change.

## §10 Decisions taken (no longer open)

1. **Skills live at Layer 3, not Layer 2 (MCP) or ops CLI.**
2. **Skills live in `skills/<name>/SKILL.md` at the project root.** No compile step, no multi-host copy pipeline. All hosts read from `skills/` directly.
3. **Identical SKILL.md format across all hosts** — frontmatter `name` + `description` only (Appendix A).
4. **Project-scoped install by default** — checked into repo in `skills/`.
5. **Slash name = skill name = directory name.**
6. **No CLI query subcommands** in this proposal.
7. **No new MCP tools** as part of skill rollout.
8. **Developer workflow skills stay in `.agents/skills/`** — they are for repo contributors, not consumers. Navigation skills do not compile into `.agents/skills/`.
9. **Skill set = 10 Tier 1 + 4 Tier 2 = 14 total.**
10. **Lockstep versioning with MCP** — skills + AGENT-GUIDE + README in the same PR when navigation semantics change.
11. **No compile step.** Skills are authored directly in `skills/`. No `compile.py`, no `java-codebase-rag compile-skills` subcommand.
12. **Three test levels:** (a) schema/frontmatter, (b) static MCP-call validation against `mcp_v2` allowlists, (c) NOT full host E2E.
13. **Graph-accurate `/callers` and `/callees` on method symbols use `CALLS` only** — matches `docs/AGENT-GUIDE.md` and ontology **15**. Cross-service inbound to a **route** is `/who-hits-route` (`HTTP_CALLS`, `ASYNC_CALLS`, `EXPOSES`). Outbound HTTP/async from a **method** uses optional `DECLARES_CLIENT` → `HTTP_CALLS` or `DECLARES_PRODUCER` → `ASYNC_CALLS` steps documented in `/callees` and `/trace-request-flow`, not one-hop `HTTP_CALLS` on `sym:`.
14. **`/mini-map` = optional MCP `edge_filter` + skill-layer accessor/semantic heuristics over ordered `CALLS` hops**, subagent-preferred; complements CALLS-NOISE Decision 39 — does not replace `edge_filter` or re-filter phantom/chained on `CALLS` (those strategies are gone post–PR-3).
15. **`resolve` is the default identifier path** for Tier 1 skills; `find` is for structured listing only.
16. **Skill worked examples assume ontology 15** — maintainers verify against a fresh `bank-chat-system` graph build after CALLS-NOISE; no ontology bump from this propose alone.
17. **Skills compose with `hints_structured`.** MCP responses include `hints_structured` (machine-parseable next-tool calls with `tool`, `args`, `actionable`, `label`, `reason`). Skills define intent-to-chain mapping; `hints_structured` provides runtime step-to-step guidance. Skills should not duplicate what `hints_structured` already provides.

## §11 Risks and how we mitigate

| Risk | Mitigation |
|---|---|
| Skills drift from MCP behaviour | Lockstep (decision #10) + static validator importing `mcp_v2` allowlists (decision #12) |
| Tier 2 workflows blow context | Fixed depth caps; `/mini-map` subagent + thin-map fallback to `/callees` |
| Weak models pick wrong skill | Rich `description` trigger phrases per skill |
| Users expect v1 `callers_of` to include HTTP on methods | Decision #13 + skill docs: CALLS on `sym:`; transport via `/who-hits-route` or optional DECLARES_* steps |
| `/mini-map` misclassifies signal as noise | `[filtered N edges]` line; fallback to raw `/callees` or `neighbors` with a narrower `edge_filter` when map &lt; 3 lines |
| Subagent unavailable | `/mini-map` runs in main agent with lower default depth (decision #14) |
| Skill body drifts from CALLS-NOISE MCP knobs | PR-S-3 `/mini-map` documents MCP pre-filter step; static validator does not parse `edge_filter` dicts — AGENT-GUIDE cross-link + manual re-verify on re-index |
| `exclude_callee_declaring_roles: ["OTHER"]` drops known-external JDK rows | Document in `/mini-map` output transparency; prefer `min_confidence` when JDK rows must stay visible |
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

Sources:
- Claude Code: [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills)
- Qwen Code: [qwenlm.github.io/qwen-code-docs/en/users/features/skills](https://qwenlm.github.io/qwen-code-docs/en/users/features/skills/)

Decision: source uses only `name` + `description`. Host-specific optional fields stay out of source unless all hosts accept them.

### A.2 Layer diagram (canonical, copy verbatim into README §1)

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 3 — High-level intents (what the user actually thinks) │
│   /trace-request-flow, /callees, /controllers, /routes,      │
│   /impact-of, /mini-map                                      │
│   ─────────────────────────────────────────────────────────  │
│   Implementation: SKILL.md in skills/ at project root.       │
│   Tier 1 = deterministic chains; Tier 2 = bounded workflows │
│   + /mini-map heuristics.                                    │
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

### What stayed unchanged (through revision 5)

- 3-layer mental model + ASCII diagram (updated labels only).
- 10 Tier 1 + 4 Tier 2 skill **names** and overall migration scope.
- Lockstep versioning + static validator (imports `mcp_v2` allowlists).
- `/mini-map` as the skill-layer complement to CALLS-NOISE (accessor + semantic labels).

### Revision 1 (2026-05-08)

Initial draft. (See prior Appendix B in git history.)

### Revision 2 (2026-05-17)

Added `/mini-map`, skill count 13→14, decision #14, [#177](https://github.com/HumanBean17/java-codebase-rag/issues/177) blocker split.

### Revision 3 (2026-05-19) — critical review fixes

1. **5-tool MCP** — `resolve` added everywhere; diagram, validator, and decisions updated.
2. **`java-codebase-rag` naming** — replaced stale `user-rag` paths and CLI subcommand name.
3. **Graph-accurate chains** — reverted incorrect one-hop `HTTP_CALLS`/`ASYNC_CALLS` on `sym:`.
4. **`resolve` first** — Tier 1 template and shared resolution rule.
5. **Static validator** — allowlists sourced from `mcp_v2`.

### Revision 4 (2026-05-20) — align with CALLS-NOISE on `master`

Rebased onto `master` after [`CALLS-NOISE-AND-RESOLUTION`](../completed/CALLS-NOISE-AND-RESOLUTION-PROPOSE.md) landed (ontology **15**).

1. **Removed #177 blocker** — replaced with **Related** cross-link.
2. **`/mini-map` pipeline** — MCP `edge_filter` / `dedup_calls` / `include_unresolved` first.
3. **Evidence** — pinned `ClientMessageProcessor#process` (~49 default `CALLS` post–PR-3).

### Revision 5 (2026-05-24) — refresh for master drift + skill architecture simplification

Rebased onto `master` (26 commits ahead of revision 4). Structural and content changes:

1. **`skills/` at project root replaces `agent-skills/` + compile pipeline.** No `compile.py`, no `java-codebase-rag compile-skills` subcommand, no AUTOGENERATED banner, no multi-host copy. All hosts read from `skills/` directly. (Decisions #2, #11 rewritten.)
2. **`.cursor/` → `.agents/`** — developer workflow skills reference updated (PR #203 on master).
3. **`hints_structured` awareness** — MCP responses now carry `hints_structured` (machine-parseable next-tool calls). New principle #10: skills compose with `hints_structured`, don't duplicate it. New decision #17.
4. **5-PR → 4-PR migration** — compile step PR removed; Tier 1 skills PR absorbs the `skills/` directory creation.
5. **Moved to `propose/active/`** — per PR #218's new folder structure.
6. **Deleted `docs/skills/`** — removed `java-codebase-explore.md`, `.zip`, and `scripts/build-explore-skill.sh`. The explore skill's content is removed; its strategy/mission concepts may re-emerge as a future Layer 3 skill.
7. **§4b rewritten** — removed `docs/skills/java-codebase-explore.md` and `.cursor/skills/` rows; added clear consumer vs developer skill directory split.
8. **Decision #8 rewritten** — `.agents/skills/` for developer workflow skills only; consumer skills never go there.
9. **Appendix B traceability** — preserved revisions 1–4 history; added revision 5.
