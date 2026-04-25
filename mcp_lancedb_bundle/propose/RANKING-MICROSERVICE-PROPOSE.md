# Ranking signals derived from `module` / `microservice` (proposal)

Status: **deferred** — out of scope for the rename PR that introduced the
`module` and `microservice` fields. This document captures the design
questions so we can revisit once cross-microservice edges (Phase 2:
`HTTP_CALLS`, `ASYNC_CALLS`) land.

## Why we did not change ranking in the rename PR

The rename pass (drop `service`, add `module` + `microservice`) is a
schema-and-API change. The graph is still **purely intra-microservice**:
`EXTENDS` / `IMPLEMENTS` / `INJECTS` only resolve inside one Maven /
Gradle reactor. Until cross-service edges exist, most ranking ideas tied
to microservice topology are either (a) trivial / not worth the
complexity, or (b) impossible to evaluate.

Concretely:

- "Boost results that share a microservice with a previously cited
  result" requires conversation state the MCP doesn't have.
- "Boost FEIGN_CLIENT hits whose target microservice is the same as the
  caller's" requires HTTP_CALLS edges to know the target.
- "Down-rank monorepo modules that are obviously infra (`*-common`,
  `*-shared`)" is half-heuristic and overlaps with the existing role
  weights for `CONFIG`.

So we keep ranking deterministic and module-agnostic for now and revisit
when there's actual cross-service signal to act on.

## Open ranking questions to revisit after Phase 2

1. **Cross-microservice INJECTS bonus.** Once Feign / Kafka edges exist,
   should an INJECTS edge that *crosses* a microservice boundary count
   for *more* than an in-process one (it's strictly more interesting for
   architectural questions)? Or for *less* (it's almost always a Feign
   stub, and the agent probably wants the implementation, not the stub)?
   - Risk: agents that ask "which class calls X" want the *consumer*; a
     Feign stub on the other side of the boundary is noise.
   - Counter: agents that ask "what depends on microservice Y" want
     exactly the cross-boundary callers.
   - Likely answer: a *separate* boost surfaced as
     `score_components.cross_microservice` rather than baked into
     `role_weight`, opt-in via a `prefer_cross_service: bool` search
     flag.

2. **Same-microservice cohesion bonus on `graph_expand`.** When we fuse
   vector top-k with graph neighbours via RRF, neighbours in the *same*
   microservice as the seed are almost certainly more relevant than
   neighbours in another microservice (which only become relevant once
   we have HTTP/Kafka edges anyway). Worth a small RRF re-weight.

3. **Module-cardinality penalty.** A microservice with 25 modules
   distributes its symbols thinly; vector hits inside it compete poorly
   against single-module microservices for any given query. Consider
   normalising by `microservice_counts[ms] / module_counts[(ms,mod)]`
   when ranking — but only if telemetry shows real bias.

4. **Microservice-aware DTO / ENTITY policy.** Today DTO and ENTITY are
   universally down-ranked. In a cross-service trace, contract-only
   microservices (`*-contracts`) are *all* DTOs by design — so the
   current weights make them invisible even when a question is
   explicitly about the contract surface. Possible fix: detect
   contract-only microservices (high DTO-ratio + low SERVICE-ratio) and
   neutralise the DTO penalty inside them.

5. **Role-weight differentiation by microservice role.** A `SERVICE` in
   an integration microservice (lots of FEIGN_CLIENT, few REPOSITORY)
   is a different beast than a `SERVICE` in a domain microservice. We
   could classify microservices by role-mix and tweak weights
   per-microservice, but this is firmly "only with telemetry"
   territory.

6. **`trace_flow` cross-microservice traversal.** Currently
   `microservice=...` constrains every stage. After Phase 2 we should
   add a `cross_service: bool` (default false) flag that lets stage
   transitions hop over Feign / Kafka edges. This is also where we'd
   pick the ranking strategy — same-microservice-first BFS, or honest
   bidirectional BFS with frontier ordering by `via.edge_type`.

## Out of scope here, in scope for Phase 2

- Designing the actual `HTTP_CALLS` / `ASYNC_CALLS` edge resolution.
- Picking a contract registry (Feign interface name → target
  microservice mapping).
- New tools (`get_service_topology`, `trace_request_flow`) that depend
  on those edges.

See `DEFERRED-CALL-GRAPH-PROPOSE.md` and `AST_GRAPH_RAG_JAVA.MD` for
the prior thinking on edge resolution.
