# HINTS-STRING-REMOVAL-PROPOSE

## Status
Proposal — not yet implemented.

## Problem Statement
Two parallel hint fields (`hints: list[str]` and `hints_structured: list[StructuredHint]`) coexist on all five MCP tool outputs, carrying largely the same information in different formats.

This redundancy creates concrete problems:

1. **Maintenance burden** — every hint trigger requires dual emission (string template + structured object) in `mcp_hints.py`, with a parity test ensuring they stay in sync.
2. **Information asymmetry** — non-actionable hints lose advisory context in structured form. A string like `"results look weak — narrow the query"` becomes `StructuredHint(tool="find", args={}, actionable=False)` with no explanatory text, while the string version carries the full advisory.
3. **Wider surface than needed** — the project principles explicitly warn against widening public surface "just in case" and state that breaking changes are always allowed.

The original string `hints` field predates `hints_structured` (added in #209). Now that structured hints are established and include `label` (#217), the string field's only unique contribution is the advisory reason text — which should simply be a field on `StructuredHint`.

## Proposed Solution
Consolidate to a single hint mechanism:

1. **Add `reason: str` to `StructuredHint`** — carries the advisory text previously only in string hints (e.g. `"no match — try ranked fuzzy lookup"`, `"results look weak — narrow the query"`).
2. **Remove `hints: list[str]`** from all five output models in `mcp_v2.py`.
3. **Remove string hint generation** — `generate_hints()` returns only `list[_StructuredHint]`; all string template constants and `MCP_HINTS_FIELD_DESCRIPTION` are deleted.
4. **Remove parity test** — `test_structured_hints_parity_with_string_hints` no longer applies.

### `StructuredHint` after change

```python
class StructuredHint(BaseModel):
    label: str = ""
    tool: Literal["search", "find", "describe", "neighbors", "resolve"]
    args: dict[str, Any]
    actionable: bool = True
    reason: str = ""
```

### `generate_hints` after change

```python
def generate_hints(
    output_kind: Literal["search", "find", "describe", "neighbors", "resolve"],
    payload: dict[str, Any],
) -> list[_StructuredHint]:  # was tuple[list[str], list[_StructuredHint]]
```

### Example: actionable hint (describe finds no match)

Before (two fields):
```python
{
    "results": [],
    "hints": [
        "no match — try search(query='BankAccount') for ranked fuzzy lookup",
        "try find(role='service') to browse by role"
    ],
    "hints_structured": [
        {"tool": "search", "args": {"query": "BankAccount"}, "actionable": True, "label": ""},
        {"tool": "find", "args": {"role": "service"}, "actionable": True, "label": ""}
    ]
}
```

After (single field):
```python
{
    "results": [],
    "hints_structured": [
        {
            "tool": "search",
            "args": {"query": "BankAccount"},
            "actionable": True,
            "label": "",
            "reason": "no match — try ranked fuzzy lookup"
        },
        {
            "tool": "find",
            "args": {"role": "service"},
            "actionable": True,
            "label": "",
            "reason": "browse by role to discover related symbols"
        }
    ]
}
```

### Example: non-actionable advisory hint

Before:
```python
{
    "hints": ["results look weak — narrow the query or try find(role=…)"],
    "hints_structured": [{"tool": "find", "args": {}, "actionable": False}]
}
```

After:
```python
{
    "hints_structured": [
        {
            "tool": "find",
            "args": {"role": "service"},
            "actionable": False,
            "label": "",
            "reason": "results look weak — narrow the query or try find with a role filter"
        }
    ]
}
```

## Scope
- `mcp_hints.py` — remove string return, string templates, `MCP_HINTS_FIELD_DESCRIPTION`; add `reason` to `_StructuredHint`; update all hint templates to emit `reason`
- `mcp_v2.py` — remove `hints: list[str]` from all five output models; add `reason: str = ""` to `StructuredHint`; update `_to_structured_hints` conversion
- `tests/test_mcp_hints.py` — remove parity test; update assertions to check `reason` content instead of string hints
- `README.md`, `docs/AGENT-GUIDE.md`, `AGENTS.md` — remove references to `hints` string field

## Schema / Ontology / Re-index impact
- Ontology bump: not required
- Re-index required: no — this is an output-only change with no graph or index schema impact
- Config/tool surface changes: `hints` field removed from all five tool outputs; `hints_structured` gains `reason` field

## Tests / Validation
- Existing hint tests rewritten to assert `reason` content on structured hints instead of string hints
- Parity test removed (no longer applicable)
- String-hint-specific test cases migrated to structured-hint assertions
- Full test suite must pass — confirms no regressions in hint generation logic
- Agent-facing behavior unchanged: structured hints already carry all tool-call information; `reason` adds advisory context

## Open Questions ([TBD])
1. Should `reason` default to `""` or be required (`str` without default)? — Recommended: default `""` for backward compatibility during transition and to avoid forcing a reason on every hint.
2. Should non-actionable hints always carry a `reason`? — Recommended: yes, non-actionable hints without a reason are noise and should be reconsidered at authoring time.
3. Should string hint templates be deleted entirely or archived for reference? — Recommended: delete entirely; git history preserves them.

## Out of scope
- Changing hint trigger logic or priority tiers
- Adding new hint categories or triggers
- Modifying the `_StructuredHint` `priority` field semantics
- Changing the `label` field behavior (added in #217)

## Sequencing / Follow-ups
Single implementation PR covering all files listed in scope.
