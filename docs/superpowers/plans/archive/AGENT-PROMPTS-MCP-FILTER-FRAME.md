<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Agent task prompts — MCP Filter Frame (PR-FRAME-1 → PR-FRAME-3)

Status: **completed** — reference template for the landed PR-FRAME-1 → PR-FRAME-3
sequence. Plan:
[`PLAN-MCP-FILTER-FRAME.md`](PLAN-MCP-FILTER-FRAME.md); propose:
[`propose/completed/MCP-FILTER-FRAME-PROPOSE.md`](../../propose/completed/MCP-FILTER-FRAME-PROPOSE.md).

One prompt per PR. Each is **self-contained**: copy the prompt verbatim
into Cursor, attach the files listed in its `@-files` block, and let
Sonnet execute. Each prompt fits comfortably in a single Sonnet session.

**Workflow per PR:**

1. Create a feature branch off `master` (or off the previous PR's branch if it hasn't merged yet).
2. Open Cursor in agent mode.
3. Attach the files from the prompt's `@-files` block.
4. Paste the prompt.
5. Let it run; review the diff; iterate via Cursor chat if needed.
6. Run `pytest`. If green, commit and open PR.

**Universal rules for every prompt:**

- Sonnet must keep `pytest` green at every commit.
- No ontology bump — all 3 PRs modify MCP filter vocabulary, tool
  descriptions, and validation only. No graph schema changes.
- No `git push` from the agent; you handle pushing.
- If Sonnet hits ambiguity, it should stop and ask, not guess.

---

## PR-FRAME-1 — Vocabulary renames (Appendix A audit)

**Branch:** `feat/filter-frame-vocabulary` off `master`.
**Base:** `master`.
**Plan section:** `plans/PLAN-MCP-FILTER-FRAME.md` § PR-FRAME-1.
**Estimated diff size:** ~4 files, ~80 LOC.

**Attach (`@-files`):**

- `@plans/PLAN-MCP-FILTER-FRAME.md` (the whole plan; only **PR-FRAME-1** section is in scope)
- `@propose/MCP-FILTER-FRAME-PROPOSE.md` (Appendix A vocabulary audit)
- `@mcp_v2.py`
- `@server.py` (verify no `client_method` references; update if found)
- `@docs/AGENT-GUIDE.md`
- `@README.md`
- `@tests/test_mcp_v2.py`

**Prompt:**

````
You are implementing PR-FRAME-1 from `plans/PLAN-MCP-FILTER-FRAME.md`.

Read the **PR-FRAME-1 — Vocabulary renames** section of the plan in full
before writing any code. The plan is the source of truth — if this prompt
and the plan disagree, the plan wins.

Also read Appendix A in `propose/MCP-FILTER-FRAME-PROPOSE.md` for the
vocabulary audit rationale.

## Scope

Implement PR-FRAME-1 exactly as specified in
`plans/PLAN-MCP-FILTER-FRAME.md` § PR-FRAME-1. **Nothing else.**

Concretely:

- **Remove `NodeFilter.client_method` field** from `mcp_v2.py`. The
  existing `http_method` field now serves both `route` and `client`
  kinds. After the change, `NodeFilter` has **16 fields** (down from 17).
- **Update `_NODEFILTER_APPLICABLE_FIELDS`**: replace `"client_method"`
  with `"http_method"` in the `"client"` tuple. The `"route"` tuple
  already has `"http_method"` — keep it. After this change, `http_method`
  is listed under both `route` and `client`.
- **Update `_node_matches_filter`**: in the `kind == "client"` branch,
  change `f.client_method` → `f.http_method`. The route branch already
  uses `f.http_method` — no change there. Both compare against
  `row.get("method")`.
- **Update `find_v2`**: in the `kind == "client"` path, change
  `method=nf.client_method` → `method=nf.http_method` in the
  `g.list_clients(...)` call.
- **Update `docs/AGENT-GUIDE.md`**: change the per-kind field table
  from `client_kind, target_service, target_path_prefix, client_method`
  to `client_kind, target_service, target_path_prefix, http_method`.
  Add a note that `http_method` applies to both `route` (server-side)
  and `client` (caller-side). Document the `source_layer` vs `role`
  distinction and the `target_service` vs `microservice` distinction.
- **Update `README.md`**: if it references `client_method`, update to
  `http_method`.
- **Add/update tests** in `tests/test_mcp_v2.py` for the cross-kind
  `http_method` alignment.

## Out of scope (do NOT touch)

- Wildcard validation — that's PR-FRAME-2.
- `describe(fqn=…)` parameter — that's PR-FRAME-2.
- Tool-description refresh in `server.py` — that's PR-FRAME-3.
- Local fail-loud counters — that's PR-FRAME-3.
- `kuzu_queries.py` — `list_clients` parameter stays `method=` (internal
  API, not user-facing vocabulary).
- Any ontology bump or graph schema change.
- Any file not listed in deliverables.

If you find yourself wanting to touch any of the above, **stop and ask** —
don't ship it.

## Deliverables

1. `NodeFilter.client_method` field removed; `NodeFilter` has 16 fields.
2. `_NODEFILTER_APPLICABLE_FIELDS["client"]` lists `"http_method"`
   instead of `"client_method"`. `http_method` appears under both
   `route` and `client`.
3. `_node_matches_filter` client branch uses `f.http_method`.
4. `find_v2` client path passes `method=nf.http_method`.
5. `docs/AGENT-GUIDE.md` updated: field table + vocabulary distinction
   notes (`source_layer` vs `role`, `target_service` vs `microservice`).
6. `README.md` updated if it contained `client_method` references.
7. Tests added/updated in `tests/test_mcp_v2.py`:
   - `test_http_method_field_applies_to_route_kind`
   - `test_http_method_field_applies_to_client_kind`
   - `test_http_method_field_inapplicable_to_symbol`
   - `test_nodefilter_rejects_old_client_method_field`
   - `test_nodefilter_applicability_table_covers_all_fields` (existing,
     must still pass)

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge
gate (CI on PR + `master`).

- `tests/test_mcp_v2.py` — exercises NodeFilter, find_v2, search_v2,
  neighbors_v2 filter paths; all rename-impacted code paths.
- `tests/test_mcp_v2_compose.py` — regression guard for composed
  edge-summary / filter interactions.

## Tests

Run:

```bash
.venv/bin/python -m pytest tests -q
```

Expected: all tests pass with zero failures. The existing
`test_nodefilter_applicability_table_covers_all_fields` must still pass
(it validates that every `NodeFilter` field appears in at least one
kind's applicable set).

## Sentinel checks

All must return zero matches:

```bash
rg 'client_method' mcp_v2.py server.py docs/AGENT-GUIDE.md
```

Verify `http_method` appears under both `route` and `client`:

```bash
rg 'http_method' mcp_v2.py | rg '_NODEFILTER_APPLICABLE_FIELDS'
```

Should show the field in both the `route` and `client` tuples (grep
context may require `-C 5`).

## Definition of Done

- [ ] `NodeFilter` has 16 fields. `client_method` does not exist.
- [ ] `_NODEFILTER_APPLICABLE_FIELDS` lists `http_method` under both
      `route` and `client`.
- [ ] All 5 listed tests pass + full suite green.
- [ ] `rg 'client_method' mcp_v2.py server.py docs/AGENT-GUIDE.md`
      returns zero.
- [ ] `docs/AGENT-GUIDE.md` documents `source_layer` vs `role` and
      `target_service` vs `microservice` distinctions.
- [ ] No file outside `mcp_v2.py`, `docs/AGENT-GUIDE.md`, `README.md`,
      and `tests/test_mcp_v2.py` is modified
      (`git diff --stat master..HEAD` and check).
- [ ] PR title: `feat: filter frame vocabulary renames (PR-FRAME-1)`.
- [ ] Branch: `feat/filter-frame-vocabulary`.
````

---

## PR-FRAME-2 — Lock the 7 frame-edge decisions in code

**Branch:** `feat/filter-frame-decisions` off PR-FRAME-1's branch (or
`master` if PR-FRAME-1 has merged).
**Base:** PR-FRAME-1 merged.
**Plan section:** `plans/PLAN-MCP-FILTER-FRAME.md` § PR-FRAME-2.
**Estimated diff size:** ~3 files, ~250 LOC.

**Attach (`@-files`):**

- `@plans/PLAN-MCP-FILTER-FRAME.md` (the whole plan; only **PR-FRAME-2** section is in scope)
- `@propose/MCP-FILTER-FRAME-PROPOSE.md` (§3.4 decisions 1–7)
- `@mcp_v2.py`
- `@server.py`
- `@tests/test_mcp_v2.py`

**Prompt:**

````
You are implementing PR-FRAME-2 from `plans/PLAN-MCP-FILTER-FRAME.md`.

Read the **PR-FRAME-2 — Lock the 7 frame-edge decisions** section of the
plan in full before writing any code. The plan is the source of truth.
Also read `propose/MCP-FILTER-FRAME-PROPOSE.md` §3.4 for the decision
rationale.

## Scope

Implement PR-FRAME-2 exactly as specified in
`plans/PLAN-MCP-FILTER-FRAME.md` § PR-FRAME-2. **Nothing else.**

Concretely:

- **Wildcard rejection (§3.4.1).** Add `_validate_no_wildcards(nf:
  NodeFilter) -> str | None` in `mcp_v2.py`. It checks prefix-match
  fields (`fqn_prefix`, `path_prefix`, `target_path_prefix`) for `*` or
  `?` characters. Returns an error message hinting at `search(query=…)`
  when found. Wire it into `find_v2`, `search_v2`, and `neighbors_v2`
  **after** applicability validation, **before** querying. On failure,
  return the appropriate `*Output(success=False, message=err)`.

- **`describe(fqn=…)` (§3.4.2).** Extend `describe_v2` signature:
  `describe_v2(id: str | None = None, fqn: str | None = None, ...)`.
  - When `fqn` is provided and `id` is not (or is `None`): look up by
    exact FQN match — `MATCH (s:Symbol) WHERE s.fqn = $fqn RETURN s.id
    AS id LIMIT 2`. If exactly one result, proceed as normal. If zero,
    return `success=False, message="No Symbol found for fqn='…'"`. If >1,
    proceed with the first but include a hint message in the output:
    "multiple symbols share this FQN; pass microservice to disambiguate".
  - When both `id` and `fqn` are provided, `id` wins (fqn ignored).
  - When neither is provided, return `success=False, message="id or fqn
    required"`.
  - Route and Client kinds accept `id` only; `fqn` on a non-Symbol ID
    is silently ignored (the ID already resolved the kind).

- **Add `fqn` parameter to the `describe` MCP tool in `server.py`.**
  `fqn: str | None = Field(default=None, description="Exact FQN for
  Symbol lookup (alternative to id; Symbol kind only)")`. Pass through
  to `describe_v2`. Update the tool description to mention `fqn` as an
  alternative identifier for Symbol nodes.

- **Multi-value semantics (§3.4.3).** No code change — already
  exercised. Lock via test only.

- **Negation predicates (§3.4.4).** No code change. Lock via test only.

- **Empty-filter semantics (§3.4.5).** No code change. Lock via test.

- **Revisit-trigger doc (§3.4.6).** Add a module-level docstring block
  at the top of `mcp_v2.py` documenting the frame contract and the N=3 /
  6-month revisit trigger, referencing
  `propose/MCP-FILTER-FRAME-PROPOSE.md` §3.4.6.

- **Identifier-resolution fallback (§3.4.7).** No code change in
  `mcp_v2.py`. The tool-description update for this decision is deferred
  to PR-FRAME-3.

## Out of scope (do NOT touch)

- `NodeFilter` field renames — frozen post-FRAME-1.
- `_NODEFILTER_APPLICABLE_FIELDS` changes — frozen post-FRAME-1.
- Tool-description refresh for `search` / `find` / `neighbors` — that's
  PR-FRAME-3.
- Local fail-loud counters — that's PR-FRAME-3.
- `docs/AGENT-GUIDE.md` updates — that's PR-FRAME-3.
- `kuzu_queries.py` — no new helpers needed; FQN lookup is inlined in
  `describe_v2` via `g._rows(...)`.
- Any ontology bump or graph schema change.
- The `resolve` tool — separate propose entirely.

If you find yourself wanting to touch any of the above, **stop and ask** —
don't ship it.

## Deliverables

1. `_validate_no_wildcards` helper in `mcp_v2.py`.
2. Wildcard validation wired into `find_v2`, `search_v2`, `neighbors_v2`.
3. `describe_v2` accepts optional `fqn` parameter; FQN lookup for
   Symbols with disambiguation hint for collisions.
4. `describe` MCP tool in `server.py` exposes `fqn` parameter.
5. Module-level docstring in `mcp_v2.py` documenting the frame contract
   and revisit trigger.
6. 13 tests in `tests/test_mcp_v2.py`:
   - `test_wildcard_in_fqn_prefix_rejected`
   - `test_wildcard_in_path_prefix_rejected`
   - `test_wildcard_in_target_path_prefix_rejected`
   - `test_wildcard_question_mark_in_fqn_prefix_rejected`
   - `test_describe_by_fqn_returns_symbol`
   - `test_describe_by_fqn_unknown_returns_error`
   - `test_describe_by_fqn_id_takes_precedence`
   - `test_describe_by_fqn_requires_id_or_fqn`
   - `test_multi_value_symbol_kinds_or_semantics`
   - `test_cross_field_and_semantics`
   - `test_exclude_roles_negation_predicate`
   - `test_empty_filter_returns_full_result_set`
   - `test_find_symbol_empty_filter_returns_results` (existing, verify
     still passes)

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge
gate (CI on PR + `master`).

- `tests/test_mcp_v2.py` — exercises wildcard rejection, describe(fqn),
  multi-value semantics, negation, empty-filter; all code paths touched.
- `tests/test_mcp_tools.py` — regression guard for MCP tool registration
  and describe tool schema changes.

## Tests

Run:

```bash
.venv/bin/python -m pytest tests -q
```

Expected: all tests pass with zero failures. 13 new/verified tests in
`test_mcp_v2.py`.

## Sentinel checks

Wildcard validation must exist:

```bash
rg '_validate_no_wildcards' mcp_v2.py
```

Must return at least 4 matches (definition + 3 call sites in find_v2,
search_v2, neighbors_v2).

`describe_v2` must accept `fqn`:

```bash
rg 'def describe_v2' mcp_v2.py
```

Must show `fqn` in the signature.

`describe` MCP tool must expose `fqn`:

```bash
rg 'fqn' server.py | rg -v '#'
```

Must show the `fqn` Field parameter.

## Definition of Done

- [ ] Wildcard values in `fqn_prefix`, `path_prefix`,
      `target_path_prefix` produce `success=False` with hint message.
- [ ] `describe(fqn=<exact FQN>)` works for Symbol nodes; returns error
      for unknown FQNs; returns first match with hint for collisions.
- [ ] `describe` MCP tool exposes `fqn` parameter.
- [ ] Module docstring documents frame contract + revisit trigger.
- [ ] All 13 tests pass + full suite green.
- [ ] No file outside `mcp_v2.py`, `server.py`, and
      `tests/test_mcp_v2.py` is modified
      (`git diff --stat master..HEAD` for this PR only).
- [ ] PR title: `feat: filter frame edge decisions (PR-FRAME-2)`.
- [ ] Branch: `feat/filter-frame-decisions`.
````

---

## PR-FRAME-3 — Lightweight local counters + tool-description refresh

**Branch:** `feat/filter-frame-counters-docs` off PR-FRAME-2's branch
(or `master` if PR-FRAME-2 has merged).
**Base:** PR-FRAME-2 merged.
**Plan section:** `plans/PLAN-MCP-FILTER-FRAME.md` § PR-FRAME-3.
**Estimated diff size:** ~4 files, ~200 LOC.

**Attach (`@-files`):**

- `@plans/PLAN-MCP-FILTER-FRAME.md` (the whole plan; only **PR-FRAME-3** section is in scope)
- `@propose/MCP-FILTER-FRAME-PROPOSE.md` (§3.4.6 revisit trigger, §3.4.7 fallback)
- `@mcp_v2.py`
- `@server.py`
- `@docs/AGENT-GUIDE.md`
- `@tests/test_mcp_v2.py`

**Prompt:**

````
You are implementing PR-FRAME-3 from `plans/PLAN-MCP-FILTER-FRAME.md`.

Read the **PR-FRAME-3 — Lightweight local counters + tool-description
refresh** section of the plan in full before writing any code. The plan
is the source of truth.

## Scope

Implement PR-FRAME-3 exactly as specified in
`plans/PLAN-MCP-FILTER-FRAME.md` § PR-FRAME-3. **Nothing else.**

Concretely:

- **Local fail-loud counter** in `mcp_v2.py`. Add a module-level
  counter (a `dict[str, int]` behind a `threading.Lock`) incremented
  whenever a fail-loud event fires:
  - `applicability` — `_nodefilter_applicability_error` returns non-None.
  - `wildcard` — `_validate_no_wildcards` returns non-None.
  - `unknown_key` — `NodeFilter` validation raises `ValidationError`.
  Add a `_log_fail_loud(category: str)` helper that increments the
  counter and emits a one-line stderr log:
  `[filter-frame] fail-loud category=<cat> count=<N>`.
  Wire it into the existing fail paths in `find_v2`, `search_v2`, and
  `neighbors_v2` — the counter must increment on every `success=False`
  return caused by filter validation.
  Expose counter state via `filter_frame_counters() -> dict[str, int]`
  for test access. This is **not** a new MCP tool.

- **Tool-description refresh in `server.py`.** Update `description=`
  strings for all four MCP tools:
  - `search`: `query` is opaque text (NL or code), ranked results.
    `filter` follows strict-frame rules (symbol-only applicability).
    Wildcards in prefix fields rejected. For identifier-shaped lookups,
    use `search(query=…)` + `describe` per candidate until `resolve`
    ships.
  - `find`: strict structured lookup by kind. Per-kind applicable
    fields: **symbol** (`microservice`, `module`, `role`,
    `exclude_roles`, `annotation`, `capability`, `fqn_prefix`,
    `symbol_kind`, `symbol_kinds`); **route** (`microservice`, `module`,
    `http_method`, `path_prefix`, `framework`); **client**
    (`microservice`, `module`, `source_layer`, `client_kind`,
    `target_service`, `target_path_prefix`, `http_method`). Wildcards
    rejected. Empty filter = all nodes of that kind.
  - `describe`: accepts `id` (any kind) or `fqn` (Symbol only). For
    identifier-shaped lookups without an exact ID/FQN, use
    `search(query=…)` + `describe` per candidate.
  - `neighbors`: required `direction` + `edge_types`. Filter applies to
    the neighbor endpoint. Mixed-kind neighborhoods fail on first
    inapplicable row.

- **Update `docs/AGENT-GUIDE.md`.**
  - Verify the per-kind field table reflects the `http_method`
    cross-kind alignment (should be done by PR-FRAME-1; fix if not).
  - Add or update a "strict frame contract" subsection: no wildcards in
    prefix fields, no DSL in `search.query`, per-kind applicable fields.
  - Document the identifier-resolution fallback pattern: `search` +
    `describe`-per-candidate until `resolve` ships.

- **Add 4 counter tests** to `tests/test_mcp_v2.py`:
  - `test_fail_loud_counter_increments_on_applicability_error`
  - `test_fail_loud_counter_increments_on_wildcard_rejection`
  - `test_fail_loud_counter_categories_are_distinct`
  - `test_fail_loud_counter_survives_multiple_calls`

## Out of scope (do NOT touch)

- `NodeFilter` field definitions or `_NODEFILTER_APPLICABLE_FIELDS` —
  frozen post-FRAME-1.
- `_validate_no_wildcards` logic or `describe(fqn=…)` logic — frozen
  post-FRAME-2.
- Any ontology bump or graph schema change.
- `kuzu_queries.py` or `build_ast_graph.py` — not touched by this PR.
- The `resolve` tool — separate propose entirely.
- Product telemetry, observability, or persistence for counters.
  Counters are process-local `dict[str, int]`, not shipped metrics.

If you find yourself wanting to touch any of the above, **stop and ask** —
don't ship it.

## Deliverables

1. `_fail_loud_counts: dict[str, int]` + `_fail_loud_lock:
   threading.Lock` at module level in `mcp_v2.py`.
2. `_log_fail_loud(category: str)` helper that increments + stderr logs.
3. `filter_frame_counters() -> dict[str, int]` exposed for tests.
4. Counter wired into `find_v2`, `search_v2`, `neighbors_v2` fail paths
   (applicability, wildcard, unknown key).
5. All four tool `description=` strings refreshed in `server.py`.
6. `docs/AGENT-GUIDE.md` updated with strict frame contract section and
   identifier-resolution fallback pattern.
7. 4 counter tests in `tests/test_mcp_v2.py`.

## Tests to run (iteration loop)

Run only these files during local iteration; full suite is the merge
gate (CI on PR + `master`).

- `tests/test_mcp_v2.py` — exercises counter increments, fail-loud
  paths, and existing filter tests that must not regress.
- `tests/test_mcp_tools.py` — regression guard for MCP tool
  registration; verifies updated descriptions don't break tool schema.

## Tests

Run:

```bash
.venv/bin/python -m pytest tests -q
```

Expected: all tests pass with zero failures. 4 new counter tests in
`test_mcp_v2.py`.

## Sentinel checks

Counter must exist and be wired:

```bash
rg '_log_fail_loud' mcp_v2.py
```

Must return at least 4 matches (definition + 3 call sites in find/search/neighbors).

```bash
rg 'filter_frame_counters' mcp_v2.py tests/test_mcp_v2.py
```

Must show definition in `mcp_v2.py` and usage in tests.

Tool descriptions must be refreshed:

```bash
rg 'applicable' server.py
```

Must show per-kind applicable field lists in tool description strings.

## Manual evidence (paste in PR description)

Fire a few fail-loud events and check counter state:

```python
import sys; sys.path.insert(0, '.')
from mcp_v2 import find_v2, filter_frame_counters

# Trigger applicability error
find_v2("symbol", {"path_prefix": "/api"})
# Trigger wildcard rejection
find_v2("symbol", {"fqn_prefix": "com.foo.*"})

counters = filter_frame_counters()
print(f"counters={counters}")
assert counters.get("applicability", 0) >= 1
assert counters.get("wildcard", 0) >= 1
print("OK — counters increment correctly")
```

## Definition of Done

- [ ] Local counter increments on every fail-loud event (applicability,
      wildcard, unknown key).
- [ ] `filter_frame_counters()` returns counter state.
- [ ] All four tool `description=` strings updated in `server.py`.
- [ ] `docs/AGENT-GUIDE.md` has strict frame contract section and
      identifier-resolution fallback.
- [ ] All 4 counter tests pass + full suite green.
- [ ] No file outside `mcp_v2.py`, `server.py`, `docs/AGENT-GUIDE.md`,
      and `tests/test_mcp_v2.py` is modified
      (`git diff --stat master..HEAD` for this PR only).
- [ ] PR title: `feat: filter frame counters + tool descriptions (PR-FRAME-3)`.
- [ ] Branch: `feat/filter-frame-counters-docs`.
````
