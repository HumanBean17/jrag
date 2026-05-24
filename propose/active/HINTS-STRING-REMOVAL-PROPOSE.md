# HINTS-STRING-REMOVAL-PROPOSE

## Status
Proposal — **revised** (v3). Resolves the category-mismatch gap with a three-field output model.

## Problem Statement
Two parallel hint fields (`hints: list[str]` and `hints_structured: list[StructuredHint]`) coexist on all five MCP tool outputs, carrying largely the same information in different formats.

This redundancy creates concrete problems:

1. **Maintenance burden** — every hint trigger requires dual emission (string template + structured object) in `mcp_hints.py`, with a parity test ensuring they stay in sync.
2. **Information asymmetry** — non-actionable hints lose advisory context in structured form. A string like `"results look weak — narrow the query"` becomes `StructuredHint(tool="find", args={}, actionable=False)` with no explanatory text, while the string version carries the full advisory.
3. **Wider surface than needed** — the project principles explicitly warn against widening public surface "just in case" and state that breaking changes are always allowed.

The original string `hints` field predates `hints_structured` (added in #209). Now that structured hints are established and include `label` (#217), the string field's only unique contribution is the advisory reason text — which should simply be a field on `StructuredHint`.

## Proposed Solution

Replace the current two-field system (`hints` + `hints_structured`) with a clean three-field model where each field has a distinct, non-overlapping purpose:

| Field | Purpose | Contains |
| --- | --- | --- |
| `hints_structured` | Tool call suggestions | Structured hints with `tool` + `args` + `actionable` + `label` + `reason`. Only entries that represent actual tool invocations. |
| `advisories` | Pure informational text | String messages that have no corresponding tool call — query quality warnings, strategy explanations, educational nudges. |
| ~~`hints`~~ | Removed | Deleted entirely. |

### Three-field separation

**`hints_structured`** — only tool call suggestions. Every entry has a meaningful `tool` and `args`. `actionable=True` means "call this directly"; `actionable=False` means "here's a tool call that might help, but you need to adjust." `reason` explains why the hint was emitted.

**`advisories: list[str]`** — pure informational strings. No tool invocation. Things like "check attrs.strategy on each row" or "NodeFilter.role filters the neighbor method's role, not the callee's declaring type." These are not tool calls — they're contextual education.

**`reason` on `StructuredHint`** — explains why a structured hint was emitted. Not advisory text (that goes to `advisories`), but the rationale for the tool call suggestion (e.g. "no match — try ranked fuzzy lookup", "results look weak — narrow the query").

### Changes

1. **Add `reason: str` to `StructuredHint`** — carries the rationale for a tool call suggestion.
2. **Add `advisories: list[str]`** to all five output models — carries pure informational text with no tool call.
3. **Remove `hints: list[str]`** from all five output models.
4. **Remove string hint generation** — `generate_hints()` returns `tuple[list[_StructuredHint], list[str]]` (structured hints + advisories); all `TPL_*` string template constants and `MCP_HINTS_FIELD_DESCRIPTION` are deleted.
5. **Remove parity test** — `test_structured_hints_parity_with_string_hints` no longer applies.

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
) -> tuple[list[_StructuredHint], list[str]]:
    # Returns (structured_hints, advisories)
```

Note: the return type is still a tuple, but the first element is structured hints only (no string hints), and the second is advisories only (pure informational text, not tool call strings).

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

After (two fields, different split):
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
    ],
    "advisories": []
}
```

### Example: non-actionable tool call hint

Before:
```python
{
    "hints": ["results look weak — narrow the query or try find(role=…)"],
    "hints_structured": [{"tool": "find", "args": {}, "actionable": False}]
}
```

After — weak results still has a concrete tool call suggestion, so it stays in `hints_structured`:
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
    ],
    "advisories": []
}
```

### Example: pure advisory (no tool call)

Before — fuzzy strategy is a string-only hint with `args={}`:
```python
{
    "hints": ["some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"],
    "hints_structured": [{"tool": "neighbors", "args": {}, "actionable": False, "label": "fuzzy strategy"}]
}
```

After — no tool call, goes to `advisories`:
```python
{
    "hints_structured": [],
    "advisories": [
        "some edges resolved via brownfield/fallback strategy — check attrs.strategy on each row"
    ]
}
```

## Which hints go where

### `hints_structured` — tool call suggestions

Every entry has meaningful `tool` + `args`:

| Hint | Tool call | Reason |
| --- | --- | --- |
| Empty-result fallbacks (search→find, describe→neighbors) | Concrete traversal | "no match — try …" |
| Success follow-ups (handler→EXPOSES, implementors→IMPLEMENTS) | Concrete traversal | Explains what you'll find |
| Resolve none/many | Concrete resolve/search/find call | "no match — try …" / "tighten identifier" |
| Search weak results | `find(role="SERVICE")` | "results look weak — narrow the query" |
| Find page full | Same query re-stated | "result page full at N — narrow filter" |
| Neighbors empty structural | Correct traversal with proper direction/edge_types | Explains what went wrong |
| Describe type/method rollups | Concrete dot-key traversal | Explains what's behind the edge |
| High fanout (describe) | `neighbors(ids, "out", ["CALLS"])` | "many CALLS — consider filtering" |
| Unresolved sites (neighbors) | `neighbors(..., include_unresolved=True)` | "N CALLS shown; K unresolved call sites" |
| High fanout (neighbors) | `neighbors(ids, "out", ["CALLS"], edge_filter={})` | "N CALLS — noisy axes are …" |

### `advisories` — pure informational text

No tool invocation. These are contextual education or multi-strategy warnings:

| Advisory | Why it's not a tool call |
| --- | --- |
| Fuzzy strategy — "check attrs.strategy on each row" | Read-only inspection of current results, no new call |
| Brownfield absence — "absence may mean unresolved" | Informational about edge source, no action to take |
| Role-filter OTHER fallback — "targets may be OTHER, try different edge_filter" | Multiple possible edge_filter values, not one concrete call |
| NodeFilter.role collision — "filters neighbor's role, not callee's declaring type" | Pure educational text |
| Describe "many CALLS" — "consider filtering by target microservice" | Vague suggestion, not a concrete call |

## Scope
- `mcp_hints.py` — remove `hints` string emission and templates; add `reason` to `_StructuredHint`; change `generate_hints` to return `tuple[list[_StructuredHint], list[str]]` (structured + advisories); delete `MCP_HINTS_FIELD_DESCRIPTION`, `finalize_hint_list`; reclassify pure advisory hints from `hints_structured` to advisory strings
- `mcp_v2.py` — remove `hints: list[str]` from all five output models; add `reason: str = ""` to `StructuredHint`; add `advisories: list[str]` to all five output models; update `_to_structured_hints` to forward `reason`; update all tool functions to unpack the new return type
- `tests/test_mcp_hints.py` — remove parity test; migrate `out.hints` assertions to `out.hints_structured[*].reason` or `out.advisories`; add advisory-content tests
- `server.py` — update tool descriptions to reference `hints_structured` and `advisories`
- `docs/AGENT-GUIDE.md` — document `advisories` field and `reason` on structured hints

## Schema / Ontology / Re-index impact
- Ontology bump: not required
- Re-index required: no — this is an output-only change with no graph or index schema impact
- Config/tool surface changes: `hints` field removed; `hints_structured` gains `reason`; `advisories` field added

## Tests / Validation
- Existing hint tests rewritten to assert `reason` content on structured hints or presence in `advisories`
- Parity test removed (no longer applicable)
- String-hint-specific test cases migrated to structured-hint or advisory assertions
- Full test suite must pass — confirms no regressions in hint generation logic

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| `reason` default | `reason: str = ""` — avoids forcing a reason on every hint |
| String template deletion | Delete entirely; git history preserves them |
| `generate_hints` return type | `tuple[list[_StructuredHint], list[str]]` — structured hints + advisories |
| Pure advisory text location | `advisories: list[str]` — separate field, not forced into `hints_structured` |
| `hints_structured` scope | Tool call suggestions only — every entry has meaningful `tool` + `args` |
| `advisories` scope | Pure informational text — no tool call, no args |
| String-only meta-advisories | Preserved in `advisories` (fanout detail, role-filter fallback, role collision, fuzzy strategy, brownfield absence) |

## Out of scope
- Changing hint trigger logic or priority tiers
- Adding new hint categories or triggers
- Modifying the `_StructuredHint` `priority` field semantics
- Changing the `label` field behavior (added in #217)

## Sequencing / Follow-ups
Single implementation PR covering all files listed in scope.
