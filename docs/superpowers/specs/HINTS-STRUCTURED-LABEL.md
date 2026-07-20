<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# HINTS-STRUCTURED-LABEL — Add label field to StructuredHint

Issue: [#216](https://github.com/HumanBean17/java-codebase-rag/issues/216)
Related: [#211](https://github.com/HumanBean17/java-codebase-rag/issues/211) (reason field)

## Problem

`hints_structured` captures `tool` + `args` but loses the semantic name embedded
in string hints. String hint `"routes via members: neighbors([...])"` carries the
label `"routes via members"` in its prefix — structured hints have no equivalent.
An LLM agent must infer hint purpose from args alone, which is ambiguous.

## Proposal

Add `label: str` to both `_StructuredHint` (internal NamedTuple) and
`StructuredHint` (public Pydantic model). Labels are 1–4 word semantic tags
extracted from the colon-prefix of existing `TPL_*` string templates.

## Label values

Labels are derived from template prefixes (text before first `:`) or assigned
a short descriptive tag for colon-less advisory templates. Full mapping in
issue #216.

## Scope

- `mcp_hints.py` — add `label` field to `_StructuredHint`, update all constructors
- `mcp_v2.py` — add `label` field to `StructuredHint`, update `_to_structured_hints`
- `tests/test_mcp_hints.py` — assert on `label` values

## Out of scope

- No changes to string `hints` generation or templates
- No changes to `reason` field (#211) — that is a separate effort
- No graph/index schema changes

## Reindex

None — output-only change.

## Tests

- Update existing structured hint tests to assert `label` is present and correct
- No new test files needed
