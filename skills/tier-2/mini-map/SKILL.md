---
name: mini-map
description: Noise-filtered call map for a method. Shows delegation, persistence, and publish seams without entity accessor or JDK noise. Use when /callees returns too many rows, the user asks "map what X does", "simplify the call graph for X", "what does X actually do", or any time a hot SERVICE/COMPONENT method needs a clean readout. Argument is a sym: id or identifier, with optional depth (default 2, max 4).
---

# /mini-map — Noise-filtered call map for a method

## When to use

The user has a **hot method** (typically a SERVICE/COMPONENT) where raw `/callees` returns 30+ rows mixed with entity accessors and JDK calls. `/mini-map` composes MCP `edge_filter` axes with a small set of skill-side heuristics to produce a clean DELEGATES / PERSISTS / READS / PUBLISHES readout.

For a single one-hop callee listing use `/callees`. For a feature-level walk use `/explain-feature`. For impact analysis use `/impact-of`.

## Tools used

`resolve`, `neighbors` (with `edge_filter`). `describe` and `search` only as recovery fallbacks.

## Reasoning preamble (mandatory)

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

This skill is mostly `structured → walk → walk ...` (one `walk` per hop, possibly multiple per hop for filtered passes).

## Argument contract

- **Required:** seed id — `sym:` id or identifier-shaped string → `resolve(identifier=..., hint_kind="symbol")`.
- **Optional:** `depth` (default 2, max 4) — recursion depth on `DELEGATES` and `PUBLISHES` targets.
- **Optional:** `microservice` — scope filter to apply on each recursion (uses `microservice` on `find` lookups; on `neighbors` it's an out-of-band display filter — apply when rendering).

## Steps

### Step 1 — Resolve

If the argument starts with `sym:`, use it as the id. Otherwise call `resolve(identifier=<arg>, hint_kind="symbol")`. On `status="one"`, use `node.id`; on `many`, list `candidates` and stop; on `none`, call `search(query=<arg>, limit=5)` and stop if still empty.

### Step 2 — Fetch ordered CALLS

Call `neighbors(ids=<sym_id>, direction="out", edge_types=["CALLS"])`.

Rows are source-ordered (`call_site_line`, `call_site_byte`). After ontology 15, true receiver-failure sites are **not** on `CALLS` — they are `UnresolvedCallSite` nodes. `attrs.resolved=false` on remaining `CALLS` rows means known-receiver-external (JDK/Spring) callees, not receiver failure.

### Step 3 — Optional MCP pre-filter

When the raw `CALLS` set is large (e.g. > 30 rows), prefer MCP-side filtering over hand-rolled rules:

- **Skeleton pass** (delegation hops): `neighbors(direction="out", edge_types=["CALLS"], edge_filter={callee_declaring_role:"SERVICE"})`.
- **Trim JDK/low-signal:** `neighbors(direction="out", edge_types=["CALLS"], edge_filter={min_confidence:0.5})` and/or `edge_filter={exclude_callee_declaring_roles:["OTHER"]}` (blunt — also drops known-external rows; document in output).
- **Collapse identical callees:** `neighbors(direction="out", edge_types=["CALLS"], dedup_calls=True)`.
- **Full transcript with unresolved sites:** `neighbors(direction="out", edge_types=["CALLS"], include_unresolved=True)` — use **only when not using `edge_filter`** on the same call (mutual exclusivity).

### Step 4 — Skill heuristics

What `callee_declaring_role` cannot do (accessor noise + semantic labels):

1. **Skip entity accessors.** Callee simple name matches `get*` / `set*` / `is*` / `<init>` on types matching `*Entity`, `*Request`, `*Response`, `*Event`, `*DTO`, or parent `role=DTO`.
2. **Skip JDK/library** when step 3 did not run: callee `fqn` prefix `java.`, `javax.`, `org.slf4j.`, `lombok.`.
3. **Classify remainder** (use `attrs.callee_declaring_role` when present, else callee parent `role` from `describe`):
   - `REPOSITORY` / `MAPPER` → `PERSISTS` (`save*`/`delete*`) or `READS` (`find*`/`get*`).
   - `SERVICE` or listener/scheduled capabilities → `DELEGATES`.
   - `CLIENT` or publisher component → `PUBLISHES`.
   - Else → `CALLS`.
4. **Deduplicate for display.** Same callee FQN → one line with `(×N)`.

### Step 5 — Recurse

On `DELEGATES` and `PUBLISHES` targets, repeat steps 2–4 up to `depth` (default 2, max 4).

### Step 6 — Render output

```
<MethodFQN>(<params>)
  DELEGATES → …Service#method
  PERSISTS  → …Repository#save (×2)
  READS     → …Repository#findById
  PUBLISHES → …Publisher#publish (×1)
  [filtered ~N edges: ~A accessors, ~B JDK/OTHER, ~C deduped]
```

The `[filtered ...]` line is transparency. Offer raw `/callees` or `neighbors` with a documented `edge_filter` if the map looks too thin (< 3 signal lines).

## Stop conditions

- Depth limit reached.
- No `DELEGATES` or `PUBLISHES` targets to recurse on.
- Cycle detected (callee already in map).
- After two empty filtered `neighbors` calls on the same node, fall back to raw `/callees` for that node.

## Recursion limit

- Default depth 2, max 4.
- When running without subagent: default depth 1, cap total raw edges examined per hop.

## Subagent preference

This skill is designed for subagent invocation. The subagent runs the MCP + heuristic pipeline per hop in its own context and returns a compact map. The main agent drills in with file reads after.

**Graceful degradation:** on hosts without subagents (or tight context budgets), run in the main agent with depth default 1. If the map has fewer than 3 signal lines after filtering, fall back to raw `/callees` (optionally with `edge_filter`) and note that stereotype roles may be `OTHER`.

## Worked example

User: `/mini-map ClientMessageProcessor#process`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped argument
→ resolve(identifier="ClientMessageProcessor#process", hint_kind="symbol")
  → sym:com.bank.chat.core.processor.ClientMessageProcessor#process(ProcessingContext, InternalEvent)
Q-class: walk        Pick: neighbors Why: raw CALLS scan
→ neighbors(direction="out", edge_types=["CALLS"])
  → ~49 rows (post-ontology-15 re-index)
Q-class: walk        Pick: neighbors Why: skeleton pass via SERVICE role
→ neighbors(direction="out", edge_types=["CALLS"], edge_filter={callee_declaring_role:"SERVICE"})
  → skeleton: 8 rows
Skill classify → ~8–12 signal rows total.
Output:
  ClientMessageProcessor#process(ProcessingContext, InternalEvent)
    DELEGATES → SplitResolverService#resolveSplit
    DELEGATES → DistributionTriggerPublisher#trigger
    PERSISTS  → ChatRepository#save (×2)
    READS     → ChatRepository#findById
    [filtered ~37 edges: ~22 accessors, ~10 JDK/OTHER, ~5 deduped]
```

## Do not

- Do not answer from training data or general Java knowledge.
- Do not read source files when MCP can answer.
- Do not skip MCP calls and guess.
- Do not fabricate `sym:` ids — always obtain them from `resolve` / `find` / `search`.
- Do not bypass the MCP `edge_filter` when applicable — these heuristics **compose** with `edge_filter`, they don't replace it.

## Out of scope

- Cross-service tracing (use `/trace-request-flow`).
- Impact analysis (use `/impact-of`).
- Replacing MCP `edge_filter` — this skill **composes** MCP filters; heuristics cover accessor noise and semantic labels only.

## Going deeper

The full `edge_filter` axis list, the role taxonomy (every `callee_declaring_role` value), and the rationale behind the post-ontology-15 `UnresolvedCallSite` split are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/mini-map`.
