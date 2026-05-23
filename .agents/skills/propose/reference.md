# Propose References

Use these examples as shape guides. Replace topic-specific details with the target feature/tool.

## Example A: Small propose (single PR, no schema change)

```markdown
# ROUTE-MATCH-LOGGING-PROPOSE

## Status
Proposal — not yet implemented.

## Problem Statement
Route matching failures are hard to diagnose because users only see final `unresolved` outcomes without compact reason details.

Current behavior forces manual graph inspection and slows iteration on brownfield overrides.

## Proposed Solution
Add structured debug metadata for matcher outcomes:
- emit a compact `reason_code` for each unresolved/ambiguous edge
- include per-run reason counters in `graph_meta`
- keep default tool responses unchanged unless debug mode is requested

## Scope
- matcher instrumentation in pass6
- read-path plumbing for counters
- docs update for debug fields

## Schema / Ontology / Re-index impact
- Ontology bump: not required
- Re-index required: yes (new `graph_meta` fields emitted during rebuild)
- Config/tool surface changes: optional debug flag on one tool

## Tests / Validation
- unit tests for reason-code assignment
- regression test for existing match labels unchanged
- fixture run verifying counter keys present

## Open Questions ([TBD])
1. Should reason details be persisted per-edge or only aggregated? — Recommended: aggregated only (v1).
2. Should debug payload be included by default in tool outputs? — Recommended: no, opt-in flag.

## Out of scope
- changing match algorithm ranking
- adding new call-edge types

## Sequencing / Follow-ups
Single implementation PR.
```

## Example B: Medium propose (ontology bump + additive schema)

```markdown
# LIST-PRODUCERS-MCP-TOOL-PROPOSE

## Status
Proposal — depends on outbound producer extraction already present.

## Problem Statement
Users can list inbound async listeners but cannot list outbound producer declarations in a first-class way.

This creates asymmetric discovery: outbound async intent is searchable only indirectly through edge traversal.

## Proposed Solution
Introduce:
1. `Producer` graph node table for outbound producer declarations
2. `DECLARES_PRODUCER(Symbol -> Producer)` relation
3. new MCP tool `list_producers` with filters (`microservice`, `producer_kind`, `topic_prefix`)

## Scope
- additive graph schema (new node + rel table)
- extraction emission for producer declarations
- query helper + server DTO + MCP tool
- README tool surface update

## Schema / Ontology / Re-index impact
- Ontology bump: required (additive schema expansion)
- Re-index required: yes (new tables populated on rebuild)
- Config/tool surface changes: one new MCP tool

## Tests / Validation
- schema smoke asserting new tables exist
- extraction tests for deterministic producer IDs
- MCP tool tests for filter behavior and limits
- no-regression tests for existing async route tools

## Open Questions ([TBD])
1. Should `Producer` include delivery semantics fields in v1? — Recommended: no, keep minimal.
2. Should `list_producers` include unresolved rows by default? — Recommended: yes, with `resolved` flag.
3. Should tool name be `list_producers` or `list_async_producers`? — Recommended: `list_producers`.

## Out of scope
- reverse traversal tools (`find_producer_callers`)
- producer-to-consumer match visualization tool

## Sequencing / Follow-ups
- PR-1: schema + extraction
- PR-2: tool + docs + test expansion
```

## Example C: Large propose (multi-PR program)

```markdown
# INCREMENTAL-GRAPH-REBUILD-PROPOSE

## Status
Proposal — design approved required before implementation plan.

## Problem Statement
Full graph rebuild runs on every change and dominates edit-query iteration latency on medium/large codebases.

## Proposed Solution
Add incremental rebuild mode with safe fallback:
- dirty-file detection from changed paths
- dependency closure expansion
- selective delete/re-emit for affected rows
- global final reconciliation pass for cross-service outcomes

## Scope
- incremental orchestrator and dependency index
- pass-level selective delete helpers
- determinism/equivalence test harness
- CLI wiring for full vs incremental mode selection

## Schema / Ontology / Re-index impact
- Ontology bump: not required (behavioral/runtime strategy change only)
- Re-index required: no one-time migration; normal rebuild still supported
- Config/tool surface changes: optional mode controls

## Tests / Validation
- mandatory equivalence: incremental(state) == full(state)
- determinism across repeated incremental runs
- fallback-path tests for unsafe change scenarios
- benchmark capture on fixture + one larger corpus

## Open Questions ([TBD])
1. Storage location for dependency index? — Recommended: sidecar file.
2. Should renamed files always force full rebuild? — Recommended: yes (v1 safety).
3. Is partial pass6 allowed? — Recommended: no, keep pass6 global in v1.

## Out of scope
- watch mode daemon
- multi-writer concurrency support
- distributed indexing

## Sequencing / Follow-ups
- PR-T1: dependency index + baseline tests
- PR-T2: selective delete helpers
- PR-T3: incremental orchestrator + equivalence checks
- PR-T4: mode decision engine integration
- PR-T5: optional brownfield closure refinements
```

## Quick adaptation checklist

- Keep section order stable unless there is a strong reason to change it.
- Use exact tool/symbol names when describing API surface.
- Call out ontology bump/reindex implications explicitly every time.
- For docs-only proposal PRs, state test baseline unchanged.
