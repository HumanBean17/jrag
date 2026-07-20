<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# HINTS-STRUCTURED — machine-parseable next-action objects alongside road-sign strings

## Status
Completed — landed in [#209](https://github.com/HumanBean17/java-codebase-rag/pull/209).

**Tracks:** [#195](https://github.com/HumanBean17/java-codebase-rag/issues/195) (item 7).

**Depends on (landed):** v1–v4 hint catalogs (`propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`, `propose/completed/HINTS-V2-PROPOSE.md`, `propose/completed/HINTS-V3-PROPOSE.md`, `propose/completed/HINTS-V4-SUCCESS-PATH-PROPOSE.md`), `mcp_hints.py` `generate_hints`, response models in `mcp_v2.py`.

## Problem Statement

Battle testing (issue #195) shows agents copying `hints` strings literally into MCP calls. Templates like:

```
clients via members: neighbors(['{id}'],'out',['DECLARES.DECLARES_CLIENT'])
```

use **Python-style list syntax** (`['…']`). When an agent copies that into a JSON `ids` parameter, it sends `"['<id>']"` — invalid JSON (single quotes), which FastMCP's `json.loads` rejects. The call fails with `Unknown id prefix for '['…]'`.

The root cause is that `hints: list[str]` embeds pseudo-call syntax that is:
1. **Not machine-parseable** — agents must reverse-engineer tool name, positional args, and kwargs from freeform text.
2. **Syntactically ambiguous** — Python lists vs JSON arrays, single vs double quotes, positional vs keyword args.
3. **Unreliable to fix by coercion** — heuristic `_coerce_ids()` in `mcp_v2` handles one symptom but not the underlying gap.

String hints remain valuable as **human-readable** road signs (operator logs, debug traces). But LLM agents need a **structured** form they can use directly.

## Proposed Solution

Add an optional `hints_structured` field to all five MCP output models. Each element is a typed object `{tool, args}` that maps 1:1 to an MCP tool call. Generation reuses the same trigger logic as string hints; rendering switches from template strings to structured arg dicts.

### Shape

```python
class StructuredHint(BaseModel):
    tool: Literal["search", "find", "describe", "neighbors", "resolve"]
    args: dict[str, Any]  # values must be JSON-serializable (str, int, list[str], etc.)
    actionable: bool = True
```

`args` values are constrained to **JSON-serializable primitives** (`str`, `int`, `float`, `bool`, `None`, `list`, `dict`). Python `set`, `tuple`, or custom objects must not appear — they break MCP serialization silently. Implementers should use `list` (not `tuple`) for array values.

All five `*Output` models gain:

```python
hints_structured: list[StructuredHint] = Field(default_factory=list)
```

### Mapping from string templates

Every `TPL_*` constant that embeds a `neighbors(…)` / `search(…)` / `find(…)` / `resolve(…)` call pattern gains a **structured counterpart** — a function or constant that returns `StructuredHint` instead of a formatted string.

Example mapping (describe type rollup):

| String template | Structured equivalent |
|---|---|
| `clients via members: neighbors(['{id}'],'out',['DECLARES.DECLARES_CLIENT'])` | `StructuredHint(tool="neighbors", args={"ids": [id], "direction": "out", "edge_types": ["DECLARES.DECLARES_CLIENT"]})` |
| `routes via members: neighbors(['{id}'],'out',['DECLARES.EXPOSES'])` | `StructuredHint(tool="neighbors", args={"ids": [id], "direction": "out", "edge_types": ["DECLARES.EXPOSES"]})` |
| `handler: neighbors(['{id}'],'in',['EXPOSES'])` | `StructuredHint(tool="neighbors", args={"ids": [id], "direction": "in", "edge_types": ["EXPOSES"]})` |
| `no match — try search(query='{identifier}') for ranked fuzzy lookup` | `StructuredHint(tool="search", args={"query": identifier})` |
| `no matches — try resolve(identifier='{kind}', hint_kind='…')` | `StructuredHint(tool="resolve", args={"identifier": identifier, "hint_kind": kind})` |

**Prose-only hints** (e.g. `results look weak — narrow the query`, `many CALLS — consider filtering`) map to `StructuredHint` with only `tool` set and an `args` dict that encodes the advisory signal as a structured recommendation, **not** a direct tool call. Agents can inspect `tool` to decide if the hint is actionable.

| String | Structured |
|---|---|
| `results look weak — narrow the query or try find(role=…)` | `StructuredHint(tool="find", args={"filter": {"role": "SERVICE"}})` (suggests a concrete starting filter) |
| `{n} CALLS — consider filtering by target microservice` | `StructuredHint(tool="neighbors", args={"ids": [id], "direction": "out", "edge_types": ["CALLS"], "edge_filter": {}})` (placeholder; agent fills in filter) |

**Open question:** whether prose-only hints should use a separate `action: "suggest"` field or embed a partial `args` — see Open Questions.

### Coexistence with `hints`

- `hints: list[str]` — **unchanged**, backward compatible. Remains the human-readable field.
- `hints_structured: list[StructuredHint]` — new field, machine-parseable.
- Both fields are populated from the same trigger logic. Same priority, same cap (5), same dedup.
- Clients that ignore `hints_structured` continue working identically.

### Generation refactor

`generate_hints` returns `(list[str], list[StructuredHint])` instead of `list[str]`. Call sites in `mcp_v2.py` destructure the tuple:

```python
str_hints, struct_hints = generate_hints("describe", hint_payload)
return DescribeOutput(success=True, record=record, hints=str_hints, hints_structured=struct_hints)
```

Alternatively (less invasive): a parallel `generate_structured_hints` function that reads the same payload and returns `list[StructuredHint]`, called alongside `generate_hints` at each call site. This avoids changing the `generate_hints` return type but duplicates trigger logic.

**Recommended:** single `generate_hints` returning both. The trigger logic is already complex; maintaining two parallel generators is fragile. The return-type change is internal to `mcp_hints.py` + `mcp_v2.py`; no external API break.

### Prose-only hints: `actionable` flag

Add an optional field to `StructuredHint`:

```python
class StructuredHint(BaseModel):
    tool: Literal["search", "find", "describe", "neighbors", "resolve"]
    args: dict[str, Any]
    actionable: bool = True  # False = advisory, args may be partial/placeholder
```

When `actionable=False`, the hint is a recommendation (e.g. "consider filtering by role"), not a direct call. Agents can skip or use as guidance. Direct-call hints (`neighbors(['id'],'out',['EXPOSES'])`) always set `actionable=True` with complete args.

**`actionable=False` has two distinct flavors** that implementers must distinguish:

1. **Incomplete args** — batch-placeholder hints (v4 N2–N7) where `args.ids` is empty because the agent must fill ids from the previous result. These have a concrete `tool` and mostly-complete `args`; only one field (typically `ids`) is a placeholder.
2. **Advisory recommendation** — prose-only hints (weak-score spread, high-fanout nudge, wrong-kind/direction recovery) where `args` is partial or schematic. These suggest a *kind* of follow-up rather than a specific call.

Agents that only want ready-to-execute calls should filter to `actionable=True`. Agents that want navigational guidance can inspect `actionable=False` hints but should not treat `args` as a complete call.

### Batch-placeholder hints (N2, N3, N4, N5, N6, N7 from v4)

v4 templates like `HTTP targets: neighbors(client_ids,'out',['HTTP_CALLS'])` use batch placeholders (`client_ids`, `route_ids`) that are never substituted — the agent is expected to pick ids from results. For structured hints, these map to:

```python
StructuredHint(
    tool="neighbors",
    args={"ids": [], "direction": "out", "edge_types": ["HTTP_CALLS"]},
    actionable=False,  # ids placeholder — agent must fill from results
)
```

Alternatively, the structured hint could carry `args.ids` populated from the payload's result ids when available. See Open Questions.

## Scope

- New `StructuredHint` model in `mcp_v2.py`
- `hints_structured` field on `SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput`, `ResolveOutput`
- Refactor `generate_hints` in `mcp_hints.py` to return `(list[str], list[StructuredHint])`
- Update all 5 call sites in `mcp_v2.py`
- Tests in `tests/test_mcp_hints.py`
- `MCP_HINTS_FIELD_DESCRIPTION` update in `mcp_hints.py`
- README mention under MCP tool reference `hints` paragraph

## Schema / Ontology / Re-index impact

- Ontology bump: **not required** (MCP response shape only, no graph/index changes)
- Re-index required: **no**
- Config/tool surface changes: new response field `hints_structured` on all five tools (additive; no removals)

## Tests / Validation

| Test name | Asserts |
|---|---|
| `test_structured_hint_describe_type_rollup_clients` | `hints_structured` contains `StructuredHint(tool="neighbors", args={"ids": [id], …})` matching string `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS` |
| `test_structured_hint_describe_type_rollup_routes` | Same for `DECLARES.EXPOSES` |
| `test_structured_hint_describe_method_overriders` | `OVERRIDDEN_BY` mapped correctly |
| `test_structured_hint_find_route_handler` | F1 → `StructuredHint(tool="neighbors", args={"ids": [id], "direction": "in", "edge_types": ["EXPOSES"]})` |
| `test_structured_hint_resolve_none_search` | `StructuredHint(tool="search", args={"query": identifier})` |
| `test_structured_hint_resolve_none_find_route` | `StructuredHint(tool="find", args={"kind": "route", "filter": {"path_prefix": seed}})` |
| `test_structured_hint_neighbors_empty_wrong_kind` | `actionable=False` on structural hint |
| `test_structured_hint_prose_only_not_actionable` | weak-score / high-fanout hints have `actionable=False` |
| `test_structured_hints_cap_5` | `len(hints_structured) <= 5` on payloads that generate many triggers |
| `test_structured_hints_dedup` | Identical `(tool, args)` deduped like string hints |
| `test_structured_hints_parity_with_string_hints` | For every output where `hints != []`, `len(hints_structured) <= len(hints)` — structured hints may omit entries that have no meaningful tool reference (v1 aims for parity but the invariant allows omission) |
| `test_structured_hint_round_trip` | Building structured hint args into an actual MCP call succeeds (integration with `neighbors_v2`) |

Regression: all existing string-hint tests continue passing unchanged.

## Open Questions ([TBD])

1. **Should `generate_hints` return a tuple or should `generate_structured_hints` be a separate function?**
   — Recommended: single function returning `(list[str], list[StructuredHint])`. Keeps trigger logic unified.

2. **Should batch-placeholder structured hints (v4 N2–N7) carry concrete result ids in `args.ids`?**
   — Recommended: **yes** when available from the payload. If the payload contains `results[*].other.id`, populate `args.ids` with those ids and set `actionable=True`. If ids cannot be extracted (meta-hints), leave `args.ids` empty with `actionable=False`.

3. **Should prose-only hints (weak-score, high-fanout) have `actionable=False` with partial args, or should they be omitted from `hints_structured` entirely?**
   — Recommended: include with `actionable=False`. Agents that only process actionable hints can filter; agents that want all signals get structured data either way. Maintains 1:1 parity with string hints.

4. **Should `StructuredHint.args` be validated against MCP tool schemas?**
   — Recommended: **no** in v1. Args are advisory; strict validation couples hints to tool signature changes. Add later if traces show agents breaking on stale args.

5. **Should `hints_structured` be added to `MCP_HINTS_FIELD_DESCRIPTION` or get its own field description?**
   — Recommended: own field description on the new field, referencing `hints` for the human-readable form.

6. **String hint template refactor: should string templates be derived from structured hints (DRY), or kept as independent constants?**
   — Recommended: **keep independent** in v1. String templates have char caps and prose labels ("clients via members:") that don't map to `StructuredHint` fields. Deriving strings from structured hints risks losing the concise human-readable format. Revisit if drift becomes a problem.

## Out of scope

- Removing or deprecating `hints: list[str]` (kept indefinitely)
- Changing `neighbors_v2` argument parsing or adding `_coerce_ids()` (separate fix from issue #195)
- Reindexing or ontology changes
- Per-row structured hints (output-level only, matching v1–v4 discipline)
- `hints_version` field
- Conditioning on `attrs.match` / confidence values
- FastMCP `ast.literal_eval` fallback (upstream; out of scope)

## Sequencing / Follow-ups

**Single PR** recommended (response model + generation + tests):

1. Add `StructuredHint` model and `hints_structured` field to all outputs
2. Refactor `generate_hints` return type
3. Map all existing template triggers to structured equivalents
4. Update call sites in `mcp_v2.py`
5. Add tests
6. README mention

After landing: close issue #195 item 7. Items 1–6 from the issue (string template fix, coercion, docs) remain independent and can land before or after this proposal.
