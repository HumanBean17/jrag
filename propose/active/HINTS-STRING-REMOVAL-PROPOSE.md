# HINTS-STRING-REMOVAL-PROPOSE

## Status
Proposal — **revised** after implementation attempt revealed a category-mismatch gap (see below).

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

## Discovered Gap: Category Mismatch Between Structured Hints and Pure Advisories

During implementation of this proposal, a category mismatch was discovered that the original proposal did not account for.

### The core tension

Structured hints are designed as **next actions** — they carry `tool`, `args`, and `actionable` to tell the caller "call this tool with these parameters next." But some current string hints are **pure advisories** — informational context about query quality, strategy, or edge-case conditions that do not correspond to any meaningful tool call.

Forcing these advisories into `reason` on a `_StructuredHint` with placeholder `tool/args` and `actionable=False` creates semantically awkward objects: "structured hints that aren't really structured."

### Three categories of current hints

**1. Cleanly mappable (~90%)** — String text maps to a structured hint with meaningful tool+args. The `reason` field would carry advisory text, and `tool/args` carry the real next-action. Examples: empty-result fallbacks (search→find, describe→neighbors), success-path follow-ups (handler→EXPOSES, implementors→IMPLEMENTS), resolve tighten/ambiguous. No issue here.

**2. Pure advisory, pseudo-structured** — The structured hint exists but with `actionable=False` and either empty or weak `args`. The string carries real context that doesn't translate to a tool call. Examples:
- Search weak results: `"results look weak — narrow the query or try find(role=…)"` → structured has `tool="find", args={"role": "service"}, actionable=False`. The tool+args are a suggestion, not a direct action — the caller may need to do something entirely different.
- Page full: `"result page full at N — narrow filter or paginate"` → structured has the same query re-stated as args, `actionable=False`. Not a real "call this tool" hint.
- Fuzzy strategy: `"some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"` → structured has `args={}, actionable=False`. Empty args — this is pure informational text wearing a structured-hint costume.
- High fanout warning: structured has the neighbors query re-stated as args, `actionable=False` — but the real value is the advisory text about noise axes.

**3. String-only, no structured counterpart** — Two functions emit string hints that are **entirely lost** in structured mode:
- `neighbors_calls_fanout_hints()` — warns about high CALLS fanout and unresolved call sites. No structured equivalent.
- `neighbors_calls_meta_hints()` — warns about role-filter OTHER fallback and NodeFilter.role collision semantics. No structured equivalent.

These carry genuine meta-advisory content about query patterns and edge-case behavior that would disappear entirely if string hints are removed.

### Why this matters

The original proposal assumed all string hints have a structured counterpart and that `reason` is a simple text migration. In reality:
- Adding `reason` to pseudo-structured hints with empty `args` conflates two different signal types in one list.
- String-only advisories would be silently lost, reducing the information available to callers in edge cases (high fanout, strategy ambiguity, filter collision).

### Open design questions

1. **Where should pure advisory text live?** Options include (a) a separate `advisories: list[str]` field on output models, (b) pseudo-structured hints with empty tool/args carrying `reason`, (c) a dedicated advisory type within `hints_structured`, (d) dropping them entirely. Each option has different trade-offs in schema simplicity vs. information preservation.
2. **Should string-only meta-advisories be preserved?** The fanout and meta hints (`neighbors_calls_fanout_hints`, `neighbors_calls_meta_hints`) currently reach only string-hint consumers. Removing string hints loses them. Is this acceptable, or should they gain structured equivalents?
3. **What is `hints_structured` for?** If it includes non-actionable, no-tool-call entries, the list becomes a mix of "next actions" and "informational notes," which may confuse consumers expecting actionable tool-call suggestions.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `reason` default | `reason: str = ""` — avoids forcing a reason on every hint |
| String template deletion | Delete entirely; git history preserves them |
| `generate_hints` return type | `list[_StructuredHint]` — no tuple wrapper |

## Open Questions ([TBD])
1. Should `reason` default to `""` or be required (`str` without default)? — Recommended: default `""` for backward compatibility during transition and to avoid forcing a reason on every hint.
2. Should non-actionable hints always carry a `reason`? — Recommended: yes, non-actionable hints without a reason are noise and should be reconsidered at authoring time.
3. Should string hint templates be deleted entirely or archived for reference? — Recommended: delete entirely; git history preserves them.
4. How to handle pure advisory text that does not map to a tool call? — See "Discovered Gap" section above.
5. Should the two string-only advisory functions (`neighbors_calls_fanout_hints`, `neighbors_calls_meta_hints`) gain structured equivalents, or is losing them acceptable?

## Out of scope
- Changing hint trigger logic or priority tiers
- Adding new hint categories or triggers
- Modifying the `_StructuredHint` `priority` field semantics
- Changing the `label` field behavior (added in #217)

## Sequencing / Follow-ups
Single implementation PR covering all files listed in scope.
