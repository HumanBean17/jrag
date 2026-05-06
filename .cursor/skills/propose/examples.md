# Propose Examples

This file contains a repo-grounded example shaped after merged propose PRs in this workspace.

## Golden sample: outbound HTTP client listing tool

```markdown
# LIST-CLIENTS-MCP-TOOL-PROPOSE

## Status
Proposal — depends on brownfield annotations v2 landing first. Design-only; no implementation in this PR.

## Problem Statement
After the annotation-direction cleanup, outbound HTTP declarations (Feign and annotated imperative clients) are no longer represented as inbound routes.

That leaves a practical gap:
> "Show every outbound HTTP call this service declares, which service it targets, and what client kind it uses."

`list_routes` is the wrong surface for this question:
1. It is inbound-oriented.
2. It misses some outbound imperative declarations.
3. Post-v2, Feign declarations are no longer expected in route rows.

## Proposed Solution
Add a first-class outbound declaration surface:

1. New `Client` node table (one row per outbound client declaration).
2. New `DECLARES_CLIENT` edge (`Symbol -> Client`) mirroring `EXPOSES(Symbol -> Route)`.
3. New MCP tool `list_clients` with filters:
   - `microservice`
   - `client_kind`
   - `target_service`
   - `path_prefix`
   - `method`
   - `limit`

`HTTP_CALLS(Symbol -> Route)` remains the call-edge truth for caller-to-callee resolution outcomes.
`Client` is caller-side declaration metadata, not a replacement for matched call edges.

## Scope
- graph schema additions for `Client` and `DECLARES_CLIENT`
- extraction + enrichment emission for outbound client declarations
- query helper + DTO + `list_clients` tool surface in server layer
- README tool list + usage notes update

## Schema / Ontology / Re-index impact
- Ontology bump: required (additive graph schema change)
- Re-index required: yes (new tables need population)
- Config/tool surface changes: one new MCP tool (`list_clients`)

## Tests / Validation
- schema smoke: `Client` and `DECLARES_CLIENT` exist after rebuild
- extraction tests: deterministic client IDs and expected field values
- query tests: each filter behaves independently and in combination
- tool tests: response DTO shape and limit bounds
- regression checks: existing route/call tools remain unchanged

## Open Questions ([TBD])
1. Should `target_service` be a plain string or foreign-key relation in v1?
   - Recommended: string in v1 (keeps schema simple, avoids lifecycle coupling).
2. Should unresolved declarations be excluded by default?
   - Recommended: no; include with `resolved` field so users can debug.
3. Should tool be named `list_clients` or `list_http_clients`?
   - Recommended: `list_clients` (concise, aligns with current naming style).

## Out of scope
- `get_client_by_path`
- `find_client_callers`
- `find_client_target_route`
- async outbound parity tooling (`Producer` + `list_async_producers`)

## Sequencing / Follow-ups
- Depends on annotation-shape v2 proposal implementation.
- Suggested split:
  - PR-1: schema + emission + tests
  - PR-2: MCP tool + docs + follow-up tests

## PR body (proposal-only) template
## What
Adds `propose/completed/LIST-CLIENTS-MCP-TOOL-PROPOSE.md` describing outbound client declarations and a new `list_clients` MCP tool.

## Why now
Outbound declaration discovery needs a first-class tool after direction-honest annotation reshaping.

## Highlights
- Introduces `Client` node and `DECLARES_CLIENT` relation.
- Keeps `HTTP_CALLS` as the matching truth surface.
- Defines `list_clients` filters and DTO behavior.
- Explicitly scopes follow-up tools out of v1.

## Tests
Docs-only; baseline unchanged.

## Out of scope
- Implementation and follow-up outbound helper tools.
```

## Notes on why this is "golden"

- Starts with status and dependency context.
- Uses concrete user workflow language in the problem statement.
- Separates declaration metadata from call-edge semantics clearly.
- Calls out ontology/re-index impact explicitly.
- Includes clear `[TBD]` questions with recommendations.
- Constrains scope and outlines practical sequencing.
