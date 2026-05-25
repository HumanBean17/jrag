---
name: trace-request-flow
description: Follow a request from an HTTP/async route entry point through the in-process call chain to persistence or async boundaries, with cross-service hops at clients/producers. Use when the user asks "trace POST /foo", "follow the request for X", "what happens when X is called", or "end-to-end flow of route Y". Argument is a route id, METHOD /path string, or path fragment.
---

# /trace-request-flow — Follow a request end-to-end

## When to use

The user has a **route** (or path) and wants a forward trace: route → handler method → service calls → repository / outbound client / producer. This is the *forward* counterpart to `/impact-of`.

For a feature explanation that isn't anchored to a route use `/explain-feature`. For a single noisy method use `/mini-map`.

## Tools used

`resolve`, `find` (route fallback), `neighbors`. `describe` optional. `search` only as a deep-fallback.

## Reasoning preamble (mandatory)

```
Q-class: <semantic | structured | inspect | walk>
Pick: <search|find|describe|neighbors|resolve>  Why: <≤8 words>
```

Typical sequence: `structured → walk → walk → walk` (with the occasional `inspect`).

## Argument contract

Single positional argument: a route identifier — `route:` id, `METHOD /path`, or path fragment → `resolve(identifier=..., hint_kind="route")` then `find(kind="route", filter={path_prefix:...})` on `none`.

## Steps

1. **Resolve route.** If argument starts with `route:` or `r:`, use directly. Else `resolve(identifier=<arg>, hint_kind="route")`:
   - `one` → use `node.id`.
   - `many` → list candidates, stop.
   - `none` → `find(kind="route", filter={path_prefix:<arg>})`; if still empty, stop and report.
2. **Handler method.** `neighbors(ids=<route_id>, direction="in", edge_types=["EXPOSES"])` → exactly one handler `sym:` in well-formed projects.
3. **Walk call chain (depth ≤ 4 on methods).** `neighbors(ids=<method_id>, direction="out", edge_types=["CALLS"])`. For each callee:
   - **SERVICE / COMPONENT** → likely to delegate further; recurse one more hop.
   - **REPOSITORY / MAPPER** → classify as persistence, **stop** that branch.
   - **CLIENT-declaring** (parent has `DECLARES_CLIENT.out > 0` on `describe`) → go to step 4 for that method.
   - **PRODUCER-declaring** → same, for producers.
4. **Cross-service boundaries.** At methods with outbound clients: `neighbors(<method_id>, "out", ["DECLARES_CLIENT"])`, then for each Client id `neighbors(<client_id>, "out", ["HTTP_CALLS"])`. At methods with async producers: `neighbors(<method_id>, "out", ["DECLARES_PRODUCER"])`, then for each Producer id `neighbors(<producer_id>, "out", ["ASYNC_CALLS"])`.
5. **Render ordered sequence.**
   ```
   Route → Handler → Service → ... → Repository / Client → DownstreamRoute / Producer → DownstreamTopic
   ```
   with edge type annotations at every boundary.

## Stop conditions

- Depth limit reached (≤ 4 hops from the handler).
- No more `CALLS` edges to follow.
- All branches terminated at REPOSITORY / MAPPER / CLIENT / PRODUCER endpoints.
- Cycle detected (method already in trace).
- After two empty `neighbors` calls in a row on the same branch, stop that branch.

## Recursion limit

- Depth ≤ 4 from the handler method on the CALLS walk.
- Maximum 10 `neighbors` calls total. Cross-service hops in step 4 count toward this budget.

## Worked example

User: `/trace-request-flow POST /chat/join`
You:
```
Q-class: structured  Pick: resolve   Why: identifier-shaped route arg
→ resolve(identifier="POST /chat/join", hint_kind="route")
  → route:POST /chat/join
Q-class: walk        Pick: neighbors Why: route → handler
→ neighbors(ids="route:POST /chat/join", direction="in", edge_types=["EXPOSES"])
  → sym:com.bank.chat.core.api.ChatController#joinOperator(JoinOperatorRequest)
Q-class: walk        Pick: neighbors Why: depth-1 CALLS from handler
→ neighbors(ids="sym:...ChatController#joinOperator(...)", direction="out", edge_types=["CALLS"])
  → ChatService#join (SERVICE — recurse)
Q-class: walk        Pick: neighbors Why: depth-2 from ChatService#join
→ neighbors(ids="sym:...ChatService#join(...)", direction="out", edge_types=["CALLS"])
  → ChatRepository#save (REPOSITORY — persistence, stop branch)
  → ChatEventPublisher#publishJoined (PRODUCER-declaring — step 4 hop)
Q-class: walk        Pick: neighbors Why: producer fan-out
→ neighbors(ids="sym:...ChatService#join(...)", direction="out", edge_types=["DECLARES_PRODUCER"])
  → producer:ChatEventPublisher
→ neighbors(ids="producer:ChatEventPublisher", direction="out", edge_types=["ASYNC_CALLS"])
  → route:chat-events.joined  (chat-assign)
Render:
  POST /chat/join → ChatController#joinOperator → ChatService#join → ChatRepository#save (persists)
                                                                  └→ producer:ChatEventPublisher
                                                                     → chat-events.joined  (chat-assign)
```

## Do not

- Do not pass `HTTP_CALLS`/`ASYNC_CALLS` on bare method `sym:` — those edges originate at Client/Producer nodes.
- Do not fabricate ids.
- Do not walk **inbound** here — that's `/impact-of`.
- Do not walk all edge types at once — single `edge_types=["CALLS"]` per call (or single boundary edge in step 4).

## Out of scope

- Reverse blast radius (use `/impact-of`).
- Noise-filtered single-method map (use `/mini-map`).
- Feature explanation without a route anchor (use `/explain-feature`).

## Going deeper

The full forward-trace workflow, the role taxonomy used in the classification heuristic (SERVICE / REPOSITORY / CLIENT / PRODUCER), and the cross-service edge schema are in `docs/AGENT-GUIDE.md`. This skill is self-sufficient for `/trace-request-flow`.
