# Implementation Review: PLAN-CAPABILITIES-MODEL

**Plan file:** `plans/PLAN-CAPABILITIES-MODEL.md`
**Review date:** 2026-04-26
**Status:** Partially implemented — 4 hard misses, 1 design gap, 2 doc gaps, 1 style nit

---

## Summary

The core capability machinery is correctly implemented:
- `ONTOLOGY_VERSION` bumped 2 → 3 in `ast_java.py`
- All four detector tables (`_METHOD_ANN_TO_CAPABILITY`, `_TYPE_ANN_TO_CAPABILITY`, `_INJECTED_TYPES_TO_CAPABILITY`, `_SUPERTYPE_TO_CAPABILITY`) are present with the right entries
- `TypeDecl.capabilities` field added; populated by `infer_capabilities_for_type` after construction in `_parse_type`
- `infer_capabilities_for_type` and all tables exported in `__all__`
- `ChunkEnrichment.capabilities` plumbed from `encl.capabilities` in `graph_enrich.py`
- `Symbol` schema extended with `capabilities STRING[]`; `_node_row` defaults and `_CREATE_SYMBOL` Cypher updated; type nodes write `list(d.capabilities)`; phantoms carry `"capabilities": []`
- `SymbolHit.capabilities` field added; `_symbol_return_for` and `_row_to_symbol` updated
- `list_by_capability` added to `KuzuGraph` with correct `list_contains` Cypher
- `list_by_capability` MCP tool added to `server.py`
- `capability` post-filter parameter added to `find_implementors`, `find_subclasses`, `list_by_role`, `list_by_annotation`
- `capabilities: list[str]` added to `SymbolDto`
- `_INSTRUCTIONS` and `trace_flow` tool description updated to mention capabilities
- `"capabilities"` added to `JAVA_ENRICHED_COLUMNS` in `search_lancedb.py`
- Version guard in `KuzuGraph.get` raises on ontology mismatch
- Unit tests in `tests/test_ast_java_capabilities.py` cover all 9 plan scenarios
- `test_symbol_has_capabilities_column` regression guard added to `test_ast_graph_build.py`

---

## Issues

### Issue 1 — `CodeChunkHit` missing `capabilities` field (Hard miss)

**File:** `server.py`

`JAVA_ENRICHED_COLUMNS` in `search_lancedb.py` includes `"capabilities"` so the value is fetched from LanceDB, but `CodeChunkHit` has no `capabilities` field and `_rows_to_hits` never maps it. The plan explicitly requires:

> Plumb `capabilities` through whatever Pydantic / dataclass models the search path uses to surface Java hits, so callers see them in results.

**Fix needed:** Add `capabilities: list[str] = Field(default_factory=list)` to `CodeChunkHit`, and map it in `_rows_to_hits` via `_clean_str_list(r.get("capabilities"))`.

---

### Issue 2 — `codebase_search` missing `capability` filter parameter (Hard miss)

**File:** `server.py`

The plan says:

> In `codebase_search`, `find_*`, `list_by_role`, add an optional parameter `capability: str | None` that, when set, AND-filters results to those carrying that capability. (Implementation: post-filter on the returned `SymbolHit.capabilities` list — no Cypher change needed.)

`list_by_role`, `find_implementors`, `find_subclasses`, and `list_by_annotation` all received the parameter. `codebase_search` did not.

Note: for `codebase_search` the post-filter would operate on `CodeChunkHit.capabilities` (which also depends on Issue 1 being fixed first).

**Fix needed:** Add `capability: str | None = Field(default=None, description="...")` to `codebase_search`; post-filter `hits` to `[h for h in hits if capability in h.capabilities]` when `capability` is set.

---

### Issue 3 — `find_injectors` missing `capability` parameter (Hard miss)

**File:** `server.py`

The plan says "In `codebase_search`, `find_*`, …". `find_injectors` is a `find_*` tool and did not receive the parameter. The other two `find_*` tools (`find_implementors`, `find_subclasses`) did.

For `find_injectors` the natural semantic is to filter on the injecting symbol (consumer): keep edges where `edge.src.capabilities` contains the requested capability.

**Fix needed:** Add `capability: str | None = Field(default=None, …)` to `find_injectors`; post-filter `edges` to those where `capability in e.src.capabilities`.

---

### Issue 4 — Kuzu capability-OR in `_run_seed_query` is effectively dead code (Design gap)

**File:** `kuzu_queries.py` + `server.py`

`_run_seed_query` (kuzu_queries.py) correctly adds:

```python
f"(s.role IN $entry_roles OR {cap_predicates})"
```

However, in `server.py`'s `trace_flow`, the first pass already filters LanceDB results with `role_in=["CONTROLLER", "COMPONENT", "SERVICE", "FEIGN_CLIENT"]`. Every FQN that arrives at Kuzu's seed query therefore already has a role in `_ENTRYPOINT_ROLES`, making the `OR cap_predicates` branch unreachable for any class with role `OTHER`.

Concretely: a plain `Job` implementor (role `OTHER`, capability `SCHEDULED_TASK`) is excluded by the LanceDB role filter before the Kuzu check ever sees it. The plan's stated test case #4 ("returns the `MESSAGE_LISTENER` class as a stage-0 seed even when its primary role is `SERVICE`") does work because `SERVICE` is in `entry_roles`. But the broader intent — expanding seeding beyond role boundaries via capabilities — is not achieved.

**Fix needed:** In `server.py`'s `trace_flow`, add a third LanceDB seed pass that searches without role restriction but filters on known entry-capability values (`MESSAGE_LISTENER`, `SCHEDULED_TASK`) using a LanceDB predicate on the `capabilities` column, then merges unique FQNs into the seed set before calling `graph.trace_flow`.

---

### Issue 5 — `README.md` not updated (Plan requirement skipped)

**File:** `README.md`

The plan requires:

> `README.md` — add a section "Capabilities" describing the multi-tag axis, the initial capability set, and `list_by_capability`. Keep the existing "Roles" section intact.

No change was made to `README.md`.

---

### Issue 6 — `CODEBASE_REQUIREMENTS.md` not updated (Plan requirement skipped)

**File:** `CODEBASE_REQUIREMENTS.md`

The plan requires:

> `CODEBASE_REQUIREMENTS.md` — note the type-level granularity choice and the deferred per-method storage (link to this plan).

No change was made to `CODEBASE_REQUIREMENTS.md`.

---

### Issue 7 — Missing blank line between `_SUPERTYPE_TO_CAPABILITY` and `_TYPE_KINDS` (Style nit)

**File:** `ast_java.py`, line ~113

```python
_SUPERTYPE_TO_CAPABILITY: dict[str, str] = {
    "Job": "SCHEDULED_TASK",
}
_TYPE_KINDS = {   # <-- no blank line before this
```

Every other pair of top-level variables in the file is separated by a blank line. The missing line here was likely a merge artefact.

---

## Priority Order for Fixes

| # | Severity | File | Description |
|---|----------|------|-------------|
| 1 | High | `server.py` | `CodeChunkHit` missing `capabilities` field |
| 2 | High | `server.py` | `codebase_search` missing `capability` filter |
| 3 | High | `server.py` | `find_injectors` missing `capability` filter |
| 4 | Medium | `server.py` + `kuzu_queries.py` | `trace_flow` capability seeding is dead code for role=OTHER classes |
| 5 | Low | `README.md` | "Capabilities" section not written |
| 6 | Low | `CODEBASE_REQUIREMENTS.md` | Granularity note not added |
| 7 | Nit | `ast_java.py` | Missing blank line between two dict constants |
