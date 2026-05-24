---
name: mini-map
description: Noise-filtered call map for a method. Shows delegation, persistence, and publish seams without entity accessor or JDK noise. Use when /callees returns too many rows or the user asks "map what X does", "simplify the call graph for X", or "what does X actually do". Argument is a sym: id or identifier, with optional depth.
---

# /mini-map — Noise-filtered call map for a method

## Argument contract

- Required: seed id — `sym:` id or identifier-shaped string → `resolve(identifier=..., hint_kind="symbol")`.
- Optional: `depth` (default 2, max 4) — recursion depth on DELEGATES and PUBLISHES targets.
- Optional: `microservice` — scope filter.

## Steps

### Step 1 — Resolve

If the argument starts with `sym:`, use it. Otherwise:
`resolve(identifier=<arg>, hint_kind="symbol")` → on `one`, use `node.id`; on `many`, list `candidates` and stop; on `none`, try `search(query=<arg>, limit=5)` and stop if still empty.

### Step 2 — Fetch ordered CALLS

`neighbors({ids: <sym_id>, direction: "out", edge_types: ["CALLS"]})`.

Rows are source-ordered (`call_site_line`, `call_site_byte`). After ontology 15, true receiver-failure sites are **not** on `CALLS` — they are `UnresolvedCallSite` nodes. `attrs.resolved=false` on remaining `CALLS` rows means known-receiver-external (JDK/Spring) callees, not receiver failure.

### Step 3 — Optional MCP pre-filter

When the raw CALLS set is large (e.g. > 30 rows), prefer MCP-side filtering over hand-rolled rules:

- **Skeleton pass** (delegation hops): `neighbors(out, ["CALLS"], edge_filter={callee_declaring_role: "SERVICE"})`.
- **Trim JDK/low-signal**: `neighbors(out, ["CALLS"], edge_filter={min_confidence: 0.5})` and/or `edge_filter={exclude_callee_declaring_roles: ["OTHER"]}` (blunt — also drops known-external rows; document in output).
- **Collapse identical callees**: `neighbors(out, ["CALLS"], dedup_calls=True)`.
- **Full transcript with unresolved sites**: `neighbors(out, ["CALLS"], include_unresolved=True)` **only when not using `edge_filter`** on the same call (mutual exclusivity).

### Step 4 — Skill heuristics

What `callee_declaring_role` cannot do (accessor noise + semantic labels):

1. **Skip entity accessors.** Callee simple name matches `get*` / `set*` / `is*` / `<init>` on types matching `*Entity`, `*Request`, `*Response`, `*Event`, `*DTO`, or parent `role=DTO`.
2. **Skip JDK/library** when step 3 did not run: callee `fqn` prefix `java.`, `javax.`, `org.slf4j.`, `lombok.`.
3. **Classify remainder** (use `attrs.callee_declaring_role` when present, else callee parent `role` from `describe`):
   - `REPOSITORY` / `MAPPER` → `PERSISTS` (save*/delete*) or `READS` (find*/get*).
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
  [filtered ~N edges: ~A accessors, ~B JDK/OTHER, ~C deduped]
```

The `[filtered ...]` line is transparency. Offer raw `/callees` or `neighbors` with a documented `edge_filter` if the map looks too thin (< 3 signal lines).

## Stop conditions

- Depth limit reached.
- No `DELEGATES` or `PUBLISHES` targets to recurse on.
- Cycle detected (callee already in map).

## Recursion limit

- Default depth 2, max 4.
- When running without subagent: default depth 1, cap total raw edges examined per hop.

## Subagent preference

This skill is designed for subagent invocation. The subagent runs the MCP + heuristic pipeline per hop in its own context and returns a compact map. The main agent drills in with file reads.

**Graceful degradation:** on hosts without subagents (or tight context budgets), run in the main agent with depth default 1. If the map has fewer than 3 signal lines after filtering, fall back to raw `/callees` (optionally with `edge_filter`) and note that stereotype roles may be `OTHER`.

## Worked example

User: /mini-map ClientMessageProcessor#process
You: → resolve(identifier="ClientMessageProcessor#process", hint_kind="symbol")
   → sym:com.bank.chat.core.processor.ClientMessageProcessor#process(ProcessingContext, InternalEvent)
   → neighbors(out, ["CALLS"]) → ~49 rows (post-ontology-15 re-index)
   → optional edge_filter={callee_declaring_role: "SERVICE"} → skeleton pass
   → skill classify → ~8–12 signal rows
   → Output:
     ClientMessageProcessor#process(ProcessingContext, InternalEvent)
       DELEGATES → …SplitResolverService#…
       DELEGATES → …DistributionTriggerPublisher#…
       PERSISTS  → …Repository#save (×2)
       READS     → …Repository#find…
       [filtered ~37 edges: ~22 accessors, ~10 JDK/OTHER, ~5 deduped]

## Out of scope

- Cross-service tracing (use `/trace-request-flow`).
- Impact analysis (use `/impact-of`).
- Replacing MCP `edge_filter` — this skill **composes** MCP filters; heuristics cover accessor noise and semantic labels only.
