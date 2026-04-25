# lancedb-code MCP Test Report

## Context

Test query: "How chat assigns on operator?"

Tools used:
- `lancedb-code.trace_flow`
- `lancedb-code.codebase_search` (chat-assign and chat-core scoped)

## What Worked Well

- Fast orientation to the correct microservice (`chat-assign`) and key service classes.
- `trace_flow` helped discover relevant orchestration components (`DistributionService`, `DistributionChunkService`, Kafka trigger path).
- `codebase_search` returned the core assignment logic (`tryAssignNextFromQueue`, queueing in `ChatManagementService`) with usable signal.

## Issues Observed

- Ranking noise for behavioral queries:
  - Top results included low-signal DTO/entity classes for a "how it works" question.
- `trace_flow` seed quality:
  - Entrypoint stage included non-entrypoint types (e.g., response/body DTOs) instead of prioritizing controller/listener classes first.
- Cross-service recall uncertainty:
  - Scoped search in `chat-core` returned no hits for a related operator-selection query; may be correct but felt brittle for exploratory analysis.

## Suggestions (Prioritized)

1. Intent-aware role ranking
   - For "how/flow/what happens" queries, boost `CONTROLLER`, listener-like components, and `SERVICE`.
   - Down-rank `DTO`/`ENTITY` unless user asks for schema/domain details.

2. Improve `trace_flow` entrypoint seeding
   - Seed from entrypoint roles first, then expand through service chain.
   - Reduce probability of starting from DTOs or passive data types.

3. Stronger fallback strategy
   - If semantic-only results are weak/noisy, auto-run hybrid (semantic + FTS) before final response.

4. Better explainability in ranking output
   - Return compact human-readable score reasons (role boost, symbol match, semantic distance) to aid trust/debugging.

5. Query refinement hints
   - Auto-suggest follow-up narrowed queries when initial query is broad.

6. Optional exclusion filters
   - Add filters like `exclude_roles=[ENTITY, OTHER]` for architecture/behavior traces.

## Overall Assessment

The MCP is already useful and sped up analysis significantly. Main improvements are around first-hit quality (ranking) and entrypoint seeding in `trace_flow`.
