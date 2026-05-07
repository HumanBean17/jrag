# Plan: MCP API v2 redesign — 4-tool graph navigator + ops CLI

Status: **completed**. Pairs with
[`propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md`](../../propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md).

Depends on: brownfield annotations v2 (PR #38) and async route merge (PR #46) — both
merged before this work. No new graph-data dependencies.

## Goal

Reduce the MCP surface from 23 verb-first tools to **4 noun-first navigation tools**
(`search`, `find`, `describe`, `neighbors`) and move the 5 operational tools into a
`user-rag` CLI. Multi-hop walking (today's `trace_*`/`impact_*`/`trace_flow`) becomes
the agent's responsibility, achieved by iterating `neighbors`.

## Principles (do not relitigate in review)

These were locked during the propose discussion (PR #48). If a reviewer wants to
revisit one, they revisit the propose, not this plan.

- **The MCP is a graph navigator (GPS), not a reasoning engine.** Multi-hop traversal
  is the agent's job. No `trace_*`, `impact_*`, or `ask` tool.
- **Noun-first, edge-type-aware naming.** No verb-named tools (`find_callers`,
  `find_route_handlers`, etc.). Tools encode graph shape, not agent intent.
- **`direction` and `edge_types` on `neighbors` are required (no defaults).** Forces
  the agent to reason explicitly each call; prevents accidental fan-out.
- **One shared `NodeFilter` schema.** Reused across `search.filter`, `find.filter`,
  `neighbors.filter`. Silent-ignore for kind-irrelevant keys.
- **`id` parameter is kind-agnostic.** Internal dispatch by id-prefix
  (`sym:`/`route:`/`client:`); agent never passes a `kind` argument to
  `describe`/`neighbors`.
- **Hard cutover, no aliases.** v1 tool names are deleted in PR-V2-3 with no
  deprecation period. Internal callers are tests + README + agent system prompt;
  all updated in lockstep.
- **`analyze_pr` and operational tools belong in a CLI, not the MCP.** The AMA
  agent never calls them; operators and CI scripts do.
- **No graph schema changes.** No ontology bump in any PR of this plan.
- **Hot params have small default limits.** `search.limit=5`, `find.limit=25`,
  `neighbors.limit=25`. Agent must opt into bigger pages.

## PR breakdown — overview

| PR | Scope | Ontology bump | Files touched (approx) | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| **PR-V2-1** | Add `search`/`find`/`describe`/`neighbors` alongside v1; shared `NodeFilter`; equivalence tests v1↔v2 | none | 5 | new-tool unit + v1↔v2 equivalence | propose merged |
| **PR-V2-2** | Compose-friendly tweaks: `search` populates `symbol_id`, `describe` returns `edge_summary`, `meta` per-edge-type counts | none | 4 | new-field schema + `search→describe→neighbors` integration | PR-V2-1 |
| **PR-V2-3** | Delete 18 v1 navigation tool registrations; rewrite README §"Tool reference"; update agent-recipe examples | none | 3-4 | tool-count assertion + README link sanity | PR-V2-2 |
| **PR-V2-4** | Extract 5 ops tools into `user_rag/cli.py`; `pyproject.toml` console-script; remove ops registrations from `server.py` | none | 5-6 | CLI subprocess integration + final MCP surface=4 | PR-V2-3 |

Landing order: **V2-1 → V2-2 → V2-3 → V2-4**. Each PR builds on the previous one's
landed state; do not start the next prompt until the prior PR is merged.

## Resolved design decisions from the propose

| Topic | Decision |
| --- | --- |
| What ships in MCP vs CLI | MCP: 4 navigation tools. CLI: 5 ops subcommands. |
| `kind` argument on `describe`/`neighbors` | None — internal id-prefix dispatch. |
| `direction` / `edge_types` defaults on `neighbors` | None — both are required Pydantic fields. |
| `find` filter shape per kind | One shared `NodeFilter`; irrelevant keys silently ignored. |
| Batch ids on `neighbors` | Yes — `ids: str \| list[str]`; results carry `origin_id`. |
| Edge return shape on `neighbors` | Edge objects with `attrs` (confidence/strategy/match/mechanism), not bare nodes. |
| NL escape hatch (`ask`) | Dropped. The AMA agent itself is the NL layer. |
| `analyze_pr` placement | CLI subcommand `user-rag analyze-pr`. PR-triage workflow already CLI-driven. |
| `meta` placement | CLI subcommand `user-rag meta`. YAGNI in MCP. |
| Aliases or hard cutover | Hard cutover — no v1 aliases survive PR-V2-3. |
| File organisation | New module `mcp_v2.py` at repo root for handlers + `NodeFilter`. CLI at `user_rag/cli.py` (introduces minimal package layout in PR-V2-4). |

---

# PR-V2-1 — implement search/find/describe/neighbors alongside v1

**Goal:** ship the four v2 navigation tools as new MCP registrations alongside the
existing v1 ones. v1 stays untouched. Equivalence tests prove v1 and v2 return the
same node ids on the same fixture.

## File-by-file changes

### 1. New file `mcp_v2.py` — handlers + shared schema

- Define Pydantic model `NodeFilter` per propose §4 (3 universal + 5 symbol-only +
  3 route-only + 4 client-only optional keys; total 15 optional fields, 0 required).
- Define output models:
  - `SearchHit { chunk_id, symbol_id?, fqn?, score, snippet, microservice?, module?, role? }`
  - `NodeRef { id, kind, fqn, microservice?, module?, role? }`
  - `NodeRecord` — full per-kind native fields; `kind` discriminator.
  - `Edge { origin_id, edge_type, direction, other: NodeRef, attrs: dict }`
  - `SearchOutput`, `FindOutput`, `DescribeOutput`, `NeighborsOutput` — wrap with
    `success: bool`, `message: str | None`, payload field.
- Add helper `_node_kind_from_id(id_str: str) -> Literal["symbol", "route", "client"]`
  — dispatches by `sym:` / `route:` / `client:` prefix; raises `ValueError` for
  unknown prefixes (translated to `success=False` at the handler boundary).
- Implement four handler functions:
  - `search_v2(query, table, hybrid, limit, offset, path_contains, filter, graph)`
    — delegates to existing `search_lancedb` code path; adds post-rank `NodeFilter`
    application and `path_contains` substring filter on the result set.
  - `find_v2(kind, filter, limit, offset, graph)` — switches on `kind`, calls the
    existing `kuzu_queries` helpers used by `list_routes`/`list_clients`/`list_by_*`.
    Filter keys irrelevant to the chosen `kind` are ignored at the call boundary.
  - `describe_v2(id, graph)` — id-prefix dispatch; reuses node-row accessors used
    today by `get_route_by_path`, `find_route_handlers`, etc. **Does not** populate
    `edge_summary` in this PR (PR-V2-2 adds that field).
  - `neighbors_v2(ids, direction, edge_types, limit, offset, filter, graph)` —
    accepts `str | list[str]`; uniform Cypher template parameterised by direction
    and edge-type list. Returns Edge objects with full attribute set per edge type.
    `direction` and `edge_types` are Pydantic `Field(...)` (required) — calls
    without them raise `ValidationError`.

### 2. `server.py` — register four new tools

- Add `@mcp.tool(name="search", ...)`, `@mcp.tool(name="find", ...)`,
  `@mcp.tool(name="describe", ...)`, `@mcp.tool(name="neighbors", ...)` after the
  existing v1 block. Each registration is ≤ 8 lines: decorator + thin async wrapper
  delegating to the matching `mcp_v2.*_v2` function.
- Tool `description` strings are exactly the one-liners from propose §3
  (`search` = "locate nodes by NL/code text", `find` = "locate nodes by structured
  filter", `describe` = "full record + edge counts for one node", `neighbors` =
  "one-hop walk; REQUIRED direction + edge_types").
- **Do not** delete, rename, or modify any v1 tool registration.
- **Do not** add the per-edge-type-count expansion to `_graph_meta_output` —
  that's PR-V2-2.

### 3. `README.md` — preview section

- Add a new subsection `### v2 navigation tools (preview)` immediately after the
  existing v1 tool reference. List the four tools with their one-liners and a link
  to `propose/MCP-API-V2-REDESIGN-PROPOSE.md`. Mark as "preview, will replace v1
  in PR-V2-3."
- **Do not** delete or shorten the existing v1 tool reference.

### 4. New file `tests/test_mcp_v2.py` — handler unit tests

(See "Tests for PR-V2-1" below.)

### 5. New file `tests/test_mcp_v2_equivalence.py` — v1↔v2 parity

(See "Tests for PR-V2-1" below.)

## Tests for PR-V2-1

`tests/test_mcp_v2.py` (target 18 tests):

1. `test_search_basic_returns_hits_with_symbol_id` — `search("ChatService")` on
   bank-chat-system returns ≥ 1 hit with non-null `symbol_id`.
2. `test_search_filter_microservice` — post-rank filter narrows to one service.
3. `test_search_path_contains_filter` — `path_contains="ChatAssign"` only returns
   chunks whose `filename` matches.
4. `test_find_symbol_by_role` — `find(kind="symbol", filter={role:"CONTROLLER"})`
   returns only CONTROLLER nodes.
5. `test_find_route_by_path_prefix` — `find(kind="route", filter={path_prefix:"/api"})`.
6. `test_find_client_by_client_kind` — `find(kind="client", filter={client_kind:"feign_method"})`.
7. `test_find_silent_ignore_irrelevant_filter_keys` — `find(kind="symbol",
   filter={path_prefix:"/api"})` returns symbols (path_prefix is ignored).
8. `test_describe_symbol_returns_record` — given a known `sym:...` id.
9. `test_describe_route_returns_record` — given a known `route:...` id.
10. `test_describe_client_returns_record` — given a known `client:...` id.
11. `test_describe_unknown_id_returns_error` — `success=False` with helpful message.
12. `test_neighbors_in_calls` — incoming CALLS to a known controller method.
13. `test_neighbors_out_calls` — outgoing CALLS from a service method.
14. `test_neighbors_route_in_exposes_returns_handler` — Route ← EXPOSES ← Symbol.
15. `test_neighbors_route_in_http_calls_returns_callers` — cross-service callers
    of an HTTP route.
16. `test_neighbors_batch_ids_carries_origin_id` — passing `ids=[id1, id2]` returns
    edges where each has `origin_id ∈ {id1, id2}`.
17. `test_neighbors_missing_direction_rejected` — calling `neighbors(id,
    edge_types=["CALLS"])` without `direction` raises `ValidationError`.
18. `test_neighbors_missing_edge_types_rejected` — calling `neighbors(id,
    direction="in")` without `edge_types` raises `ValidationError`.

`tests/test_mcp_v2_equivalence.py` (target 14 tests, one per row in propose §11
mapping table excluding DROPPED rows and CLI rows):

1. `test_eq_codebase_search` — `codebase_search(q)` ↔ `search(q)` (compare top-K
   `chunk_id` set).
2. `test_eq_find_implementors`
3. `test_eq_find_subclasses`
4. `test_eq_find_injectors`
5. `test_eq_find_callers`
6. `test_eq_find_callees`
7. `test_eq_list_routes`
8. `test_eq_list_clients`
9. `test_eq_find_route_handlers`
10. `test_eq_find_route_callers`
11. `test_eq_list_by_role`
12. `test_eq_list_by_annotation`
13. `test_eq_list_by_capability`
14. `test_eq_graph_neighbors`

Equivalence comparison: assert sorted set of returned ids is equal between v1 and
v2 calls for the same input. Ignore field-by-field DTO differences; the
equivalence is at the ids level. v2 is a refactor, not a behaviour change.

Final test count expectation: **baseline + 32 new** (18 unit + 14 equivalence).
DoD is the delta and the new tests being green, not an absolute total — baseline
counts drift between branches and across machines.

## Definition of done (PR-V2-1)

- Full suite green (`python -m pytest tests -q` reports zero failures).
- The 32 new tests are present in `tests/test_mcp_v2.py` (18) and
  `tests/test_mcp_v2_equivalence.py` (14), all passing.
- MCP tool surface includes both v1 and v2 registrations in this PR (target: 27
  total tools; verify via test and/or script, not formatting-dependent grep
  alone).
- `grep -nE "trace_v2|ask_v2|impact_v2" mcp_v2.py` returns 0.
- `grep -nE 'kind\s*[:=]' mcp_v2.py | grep -E 'def (describe_v2|neighbors_v2)'`
  returns 0 (no `kind` parameter on those handlers).
- Diff is confined to deliverables for this PR:
  `mcp_v2.py`, `server.py`, `tests/test_mcp_v2.py`,
  `tests/test_mcp_v2_equivalence.py`, `README.md`, plus any narrowly-related
  test harness/import updates required to make those changes pass.
- Manual evidence (find Controller → describe → neighbors-in CALLS) included in
  PR description.
- No ontology bump.

---

# PR-V2-2 — composition tweaks: `edge_summary`, `symbol_id`, `meta` edge counts

**Goal:** make the four primitives compose cleanly. Three small, focused additions
to handlers introduced in PR-V2-1, plus one v1 enhancement (`graph_meta`) so the
not-yet-extracted-to-CLI ops tool grows the per-edge-type counts the future CLI
will export.

## File-by-file changes

### 1. `mcp_v2.py` — `describe.edge_summary`

- Extend `NodeRecord` with `edge_summary: dict[str, dict[str, int]] | None`. Shape:
  `{"CALLS": {"in": 7, "out": 3}, "INJECTS": {"in": 0, "out": 2}, ...}`. One entry
  per edge type that has a non-zero count in either direction.
- Implement via single grouped Cypher count query (avoid 9 round-trips). Reuse
  any `kuzu_queries` helper that already aggregates per-edge counts; otherwise add
  `KuzuGraph.edge_counts_for(node_id) -> dict[str, dict[str, int]]`.
- Population is unconditional in this PR (no opt-out flag). Cost is one extra
  query per `describe` call; acceptable.
- PR-V2-1 follow-up (readability only): add a short inline comment near
  `neighbors_v2` required `Field(...)` parameters clarifying that direct Python
  calls intentionally use the same validation contract as MCP-bound calls.

### 2. `mcp_v2.py` — `search.symbol_id` always populated when known

- After ranking, look up each chunk's `symbol_id` from the LanceDB row metadata
  (chunks rooted in a known symbol already carry this; chunks rooted in raw text
  remain `symbol_id=None`).
- Add a helper `_chunk_to_symbol_id(chunk_row) -> str | None` so the lookup logic
  is single-source.
- This was inconsistent in v1's `codebase_search` — fix here.
- PR-V2-1 follow-up (perf checkpoint only): while touching `mcp_v2.py`, measure
  whether `_resolve_node_kind` round-trips are visible in `describe` /
  `neighbors` latency. Keep correctness-first behavior unless profiling shows a
  meaningful hotspot; if needed, spin into a focused perf micro-PR.

### 3. `kuzu_queries.py` — `meta` per-edge-type counts

- Extend `_graph_meta_output` (and underlying `meta()` Kuzu helper) to return
  `edge_counts: dict[str, int]` for all 9 edge types. Existing `counts.types`
  / `counts.routes` / `counts.clients` stay; this is additive.
- Update `GraphMetaOutput` Pydantic model accordingly.
- v1 `graph_meta` tool keeps its registration; the new field flows through
  unchanged. (PR-V2-4 will move this output into `user-rag meta` CLI subcommand.)

### 4. New tests — `tests/test_mcp_v2_compose.py`

(See "Tests for PR-V2-2" below.)

## Tests for PR-V2-2

`tests/test_mcp_v2_compose.py` (target 6 tests):

1. `test_describe_edge_summary_for_controller` — sum of CALLS in/out matches
   v1 `find_callers` + `find_callees` count.
2. `test_describe_edge_summary_omits_zero_count_types` — types with 0 in both
   directions absent from dict.
3. `test_describe_edge_summary_for_route` — Route node returns `EXPOSES.in >= 1`.
4. `test_search_populates_symbol_id_when_chunk_rooted_in_symbol` — every hit whose
   chunk corresponds to a known symbol has non-null `symbol_id`.
5. `test_meta_returns_per_edge_type_counts` — `graph_meta()` output includes
   `edge_counts` with all 9 keys (each ≥ 0).
6. `test_search_describe_neighbors_chain_end_to_end` — single test exercising
   `search` → pick top hit → `describe` → `neighbors(in, [CALLS])`. Asserts each
   step returns ≥ 1 result on bank-chat-system fixture.

Final test count: **previous baseline + 6 new** (`tests/test_mcp_v2_compose.py`).
DoD is the delta + suite-green, not an absolute total.

## Definition of done (PR-V2-2)

- Full suite green.
- 6 new tests in `tests/test_mcp_v2_compose.py` present and passing.
- `describe(any_id)` always returns `edge_summary` (non-None) for known nodes.
- `search` populates `symbol_id` whenever the chunk row carries it.
- `graph_meta()` output schema includes `edge_counts: dict[str, int]` covering
  all 9 edge types defined in `build_ast_graph.py:_SCHEMA_*`.
- Diff is confined to deliverables for this PR: `mcp_v2.py`, `kuzu_queries.py`,
  `server.py` (for `GraphMetaOutput` model + `_graph_meta_output` callsite),
  `tests/test_mcp_v2_compose.py`, plus any narrowly-related test harness/import
  updates required to make those changes pass.
- No ontology bump.

---

# PR-V2-3 — delete v1 navigation tools

**Goal:** remove the 18 v1 navigation tool registrations from `server.py` and
update the README so the v2 surface is the public one. Operational tools
(`graph_meta`, `analyze_pr`, `diagnose_ignore`, `list_code_index_tables`,
`refresh_code_index`) **stay registered** in this PR — they move to CLI in
PR-V2-4.

## File-by-file changes

### 1. `server.py` — delete 18 v1 registrations

The following `@mcp.tool` blocks are removed (line numbers are pre-PR-V2-1 master,
will shift after V2-1/V2-2 land — search by tool name):

- `codebase_search`
- `find_implementors`
- `find_subclasses`
- `find_injectors`
- `find_callers`
- `find_callees`
- `list_routes`
- `list_clients`
- `find_route_handlers`
- `get_route_by_path`
- `find_route_callers`
- `trace_request_flow`
- `list_by_role`
- `list_by_annotation`
- `list_by_capability`
- `graph_neighbors`
- `impact_analysis`
- `trace_flow`

For each, also delete:
- The output Pydantic model if not used elsewhere (most are tool-private).
- Any helper function only called from the deleted handler.
- The corresponding import line if it becomes unused.
- PR-V2-1 follow-up decision: confirm whether keeping `describe(id=...)`
  parameter name is the final API choice post-cutover (it currently follows the
  propose/plan contract). Record the decision in PR-V2-3 notes so reviewers do
  not re-open the naming discussion in PR-V2-4.

### 2. `README.md` — replace v1 tool reference with v2

- Delete the v1 tool reference subsection.
- Promote the "v2 navigation tools (preview)" subsection added in PR-V2-1 to the
  primary `### Tool reference` heading. Drop the "preview" qualifier.
- Each of the four tools gets:
  - One-line description.
  - Argument list with types.
  - 1-2 line example call.
- Keep the operational tools (`graph_meta`, `analyze_pr`, etc.) listed as
  "operational — moving to `user-rag` CLI in next release". This is a one-PR
  transition state.

### 3. `propose/PRODUCT-VISION.md` — agent-recipe examples

- Update any example invocations from v1 to v2. Search for `find_callers`,
  `list_routes`, `list_clients`, `find_route_*`, `trace_*`, `impact_*` and
  rewrite each to the v2 equivalent (per propose §11 mapping table).

### 4. `tests/test_mcp_v2_equivalence.py` — delete

- The v1↔v2 equivalence tests served their purpose (proving the v2 implementation
  was a faithful refactor). v1 no longer exists; the tests cannot run.
- Delete the file entirely.

## Tests for PR-V2-3

No new tests. Modify the existing surface assertion:

- Update `tests/test_server.py` (or wherever the registered-tool-surface
  assertion lives — check during implementation; if no such test exists, add it
  now in this PR) to assert: exactly 9 MCP tools registered = 4 navigation (`search`, `find`,
  `describe`, `neighbors`) + 5 operational (`graph_meta`, `analyze_pr`,
  `diagnose_ignore`, `list_code_index_tables`, `refresh_code_index`).

Final test count: **previous baseline − 14 deleted equivalence tests + 0–1 new
surface assertion**. DoD is suite-green and the deleted file no longer present,
not an absolute total.

## Definition of done (PR-V2-3)

- Full suite green.
- `tests/test_mcp_v2_equivalence.py` is deleted (verify
  `ls tests/test_mcp_v2_equivalence.py 2>&1` returns "No such file").
- Surface assertion verifies 9 registered MCP tools.
- Surface assertion (or equivalent parse-based check) verifies none of the 18
  removed v1 navigation tool names remain registered.
- README §"Tool reference" lists exactly the 4 v2 tools as primary; ops tools
  are noted as transitional.
- `tests/test_mcp_v2_equivalence.py` does not exist.
- No ontology bump.

---

# PR-V2-4 — extract operational tools into `user-rag` CLI

**Goal:** move the 5 ops tools out of the MCP into a console-script CLI. After
this PR, the MCP surface is exactly 4 tools — pure graph navigation.

## File-by-file changes

### 1. New module `user_rag/cli.py` — argparse entry point

Introduce a minimal package layout for the CLI:
- `user_rag/__init__.py` (empty).
- `user_rag/cli.py` with `main()` entry point and 5 subcommands.

Subcommand surface:

```
user-rag refresh [--source-root DIR] [--kuzu-path DIR] [--lancedb-path DIR]
                 [--quiet]
user-rag meta
user-rag tables
user-rag diagnose-ignore <path>
user-rag analyze-pr [--diff-file FILE | --diff-stdin]
```

Implementation rules:
- Each subcommand is a thin wrapper around the same engine code today's MCP
  handlers call. Reuse, do not reimplement:
  - `refresh` → calls the same code path `refresh_code_index` MCP tool used.
  - `meta` → calls `_graph_meta_output()` (with the `edge_counts` field added in
    PR-V2-2).
  - `tables` → calls the LanceDB table-listing helper today's `list_code_index_tables`
    uses.
  - `diagnose-ignore <path>` → instantiates `LayeredIgnore(root)` and calls
    `diagnose_dict(abs_path)`.
  - `analyze-pr` → reads diff text from `--diff-file` or stdin and calls
    `pr_analysis.analyze_pr_pipeline(graph, diff_text)`.
- Output mode auto-detected; **no user-facing flag controls it**:
  - `sys.stdout.isatty()` → pretty-print (use `rich` if already a transitive dep;
    otherwise plain indented text).
  - Not a TTY (piped) → `json.dumps(..., default=_jsonable, sort_keys=True,
    indent=None)` — single-line JSON for shell pipelines.
  - Do **not** add `--pretty`, `--json`, or any equivalent override. The single
    `isatty()` switch is the contract; tests force it via PTY (see Tests §
    below).
- Exit codes: 0 on success, 1 on user error (bad path, missing diff), 2 on
  internal error.

### 2. `pyproject.toml` — console script

- Change `[tool.setuptools] packages = []` to declare the new package:
  `packages = ["user_rag"]`.
- Add `[project.scripts]` table: `user-rag = "user_rag.cli:main"`.
- Verify `pip install .` (or `pip install -e .`) makes `user-rag` available on
  `$PATH`.

### 3. `server.py` — delete 5 ops tool registrations

Remove `@mcp.tool` blocks for: `graph_meta`, `analyze_pr`, `diagnose_ignore`,
`list_code_index_tables`, `refresh_code_index`. Also remove their output Pydantic
models if not used elsewhere, and any helper imports that become unused.

After this PR, MCP surface is **exactly 4 tools**
(`search`/`find`/`describe`/`neighbors`), verified via test and/or script.

### 4. `README.md` — CLI reference

- Add new top-level section `## CLI reference` listing the 5 subcommands with:
  - Synopsis.
  - All flags with types.
  - One example invocation each.
  - Note: "JSON output when piped, pretty when TTY."
- Delete the "operational tools (transitional)" entries from `## Tool reference`.
  After this PR, `## Tool reference` lists only the 4 navigation tools.
- Add a "Migration from v1" subsection mapping old MCP tool calls to new CLI
  invocations (subset of propose §11).

### 5. `AGENTS.md` — entry-point updates

- Update the "Where to look" / tool-list paragraph to reflect the 4-tool MCP
  surface and reference `user-rag --help` for ops.

### 6. `cursor-pr-review` skill (user-scoped) — update bash snippets

- Update any references to `analyze_pr` MCP calls to `user-rag analyze-pr
  --diff-file /tmp/pr.diff`. Use the `update_user_skill` flow (or have the user
  edit manually — note in PR description). **Do NOT** include skill changes in
  the repo PR diff; this is a separate user-skill update.

## Tests for PR-V2-4

`tests/test_user_rag_cli.py` (target 8 tests):

Use `subprocess.run(["python", "-m", "user_rag.cli", ...], capture_output=True,
text=True, env={"LANCEDB_MCP_PROJECT_ROOT": ...})` so tests don't depend on
`pip install`.

1. `test_cli_meta_outputs_valid_json_when_piped` — assert stdout is JSON-parseable
   and contains `edge_counts`.
2. `test_cli_meta_pretty_when_tty` — invoke under a real PTY using
   `os.openpty()` (or `pty.spawn`) so `sys.stdout.isatty()` returns True; assert
   output is **not** valid JSON (i.e. pretty-printed). Do **not** add a
   `--pretty` CLI flag — the only switch is `isatty()`. If CI on the target
   platform makes PTY-based testing flaky, mark this single test
   `@pytest.mark.skipif(...)` with a clear reason rather than introducing a
   side-door flag.
3. `test_cli_tables_lists_known_table` — bank-chat-system fixture rebuilt; assert
   `java` table in output.
4. `test_cli_diagnose_ignore_walked_path` — pass a path inside the fixture;
   assert `ignored=False` (or equivalent).
5. `test_cli_diagnose_ignore_unconditional_prune` — pass `.git/foo`; assert
   `ignored=True`.
6. `test_cli_analyze_pr_with_diff_file` — write a small diff, assert output
   contains `risk_score` and `blast_radius_total`.
7. `test_cli_refresh_rebuilds_kuzu_path` — invoke `refresh --source-root
   tests/bank-chat-system --kuzu-path /tmp/cli_refresh`; assert directory
   exists and `meta` against it returns non-zero counts.
8. `test_cli_unknown_subcommand_exits_2` — invoke with `user-rag bogus`; assert
   exit code 2 and stderr mentions "unknown".

`tests/test_server.py` (or final-surface test) — update tool-count assertion to
**exactly 4** registered MCP tools.

Final test count: **previous baseline + 8 new** (`tests/test_user_rag_cli.py`).
DoD is the delta + suite-green, not an absolute total.

## Definition of done (PR-V2-4)

- Full suite green.
- 8 new tests in `tests/test_user_rag_cli.py` present and passing.
- Surface assertion verifies **4** registered MCP tools.
- `pip install .` (or `pip install -e .`) succeeds; `user-rag --help` lists 5
  subcommands.
- `user-rag meta | python -c "import json,sys; json.loads(sys.stdin.read())"`
  exits 0 (valid JSON when piped).
- README has top-level `## CLI reference` section; `## Tool reference` lists
  only the 4 v2 tools.
- `AGENTS.md` references `user-rag --help` for ops.
- Diff is confined to deliverables for this PR:
  `user_rag/__init__.py`, `user_rag/cli.py`, `pyproject.toml`, `server.py`,
  `README.md`, `AGENTS.md`, `tests/test_user_rag_cli.py`, plus the
  surface-assertion test and any narrowly-related test harness/import updates
  required to make those changes pass.
- No ontology bump.
- No `mcp_v2.py` changes.

---

## Risk register

| Risk | PR | Mitigation |
| --- | --- | --- |
| v2 handlers diverge in behaviour from v1 | V2-1 | Equivalence tests (14 of them) compare returned id sets directly. Drift is caught at PR review. |
| `direction`/`edge_types` required-field change breaks existing clients | V2-1 | No existing clients — confirmed by Dmitry ("nobody uses this MCP bundle yet"). Tests assert `ValidationError` is raised, which is the contract. |
| `describe.edge_summary` adds N round-trips per call | V2-2 | Single grouped count query, not 9 round-trips. Test asserts call count via Kuzu connection mock. |
| Removing v1 tools breaks the agent system prompt | V2-3 | `propose/PRODUCT-VISION.md` and README are updated in the same PR. Agent prompt is separate (not in this repo). |
| CLI subprocess tests are slow / flaky | V2-4 | Each subprocess invocation hits a pre-built fixture under `/tmp`; no rebuilds inside tests. Targeted at < 5s total. |
| `pyproject.toml` package layout breaks the existing flat-script bundle | V2-4 | Today's `packages = []` is intentional; we promote it to `packages = ["user_rag"]` only — root scripts (`server.py`, `build_ast_graph.py`, etc.) stay outside the package. Tested by `pip install .` succeeding. |
| User skill `cursor-pr-review` still calls `analyze_pr` MCP after V2-4 | V2-4 | PR description includes a manual TODO for the user to update the skill. CLI version of the call is documented in README's "Migration from v1" subsection. |
