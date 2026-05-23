# Agent task prompts — Tier 1 completion (PR-A1 → PR-C)

Status: **completed — all PRs merged**. Kept as a reference template for
future per-PR Cursor delegation work.

One prompt per PR. Each is **self-contained**: copy the prompt verbatim
into Cursor, attach the files listed in its `@-files` block, and let
Sonnet execute. Each prompt fits comfortably in a single Sonnet session.

**Workflow per PR:**

1. Create a feature branch off `master` (or off the previous PR's branch if it hasn't merged yet).
2. Open Cursor in agent mode with **Sonnet 4.6** (or whichever Sonnet you have credits for).
3. Attach the files from the prompt's `@-files` block.
4. Paste the prompt.
5. Let it run; review the diff; iterate via Cursor chat if needed.
6. Run `pytest`. If green, commit and open PR.

**Universal rules for every prompt:**

- Sonnet must keep `pytest` green at every commit.
- No new file-loading code in `_load_brownfield_overrides` (PR-A3 only) — extend, do not duplicate.
- No `git push` from the agent; you handle pushing.
- If Sonnet hits ambiguity, it should stop and ask, not guess.

---

## PR-A1 — Route schema + literal extractor

**Branch:** `feat/b2a-route-schema` off `master`.

**Attach (`@-files`):**
- `@plans/PLAN-TIER1-COMPLETION.md` (the whole plan, but only the **PR-A1** section is in scope)
- `@propose/TIER1-COMPLETION-PROPOSE.md` (§4 schema)
- `@ast_java.py`
- `@build_ast_graph.py`
- `@java_ontology.py`
- `@tests/test_ast_graph_build.py` (for pattern reference)
- `@plans/completed/PLAN-CALL-GRAPH.md` (style reference — the previous successful plan)

**Prompt:**

```
You are implementing PR-A1 from `plans/PLAN-TIER1-COMPLETION.md`.

Read the **PR-A1 — B2a schema + literal extractor** section of the plan
in full before writing any code. The plan is the source of truth — if
this prompt and the plan disagree, the plan wins. Every test case
number I mention (1, 2, …, 11) refers to the numbered test list in
PR-A1 §4.

Scope (do not exceed):
- Add `RouteDecl` dataclass + bump `ONTOLOGY_VERSION` 4 → 5 in
  `ast_java.py`.
- Add `VALID_ROUTE_FRAMEWORKS` and `VALID_ROUTE_KINDS` to
  `java_ontology.py`.
- Implement `_collect_routes` in `ast_java.py` for **literal strings
  only** (Spring MVC, WebFlux, Feign, Kafka, RabbitMQ, JMS, Spring
  Cloud Stream). SpEL / constant_ref → skip and increment
  `routes_skipped_unresolved`. Do **not** implement SpEL resolution
  here; that is PR-A2.
- Implement `_normalize_path` and `_route_id` in `build_ast_graph.py`.
  These are shared with B2b later — write them generically and
  unit-test them in isolation.
- Add `_SCHEMA_ROUTE` and `_SCHEMA_EXPOSES`. Edge direction is
  `(Symbol)-[:EXPOSES]->(Route)`. Do **not** reverse it.
- Implement `pass4_routes` and wire it after `pass3_calls`.
- Add `routes_total`, `exposes_total`, `routes_by_framework`,
  `routes_resolved_pct` to `graph_meta`.
- Build the new fixture `tests/fixtures/route_extraction_smoke/` and
  add the new test file `tests/test_route_extraction.py` with cases
  1–11.
- Extend `tests/test_ast_graph_build.py` with the cases listed in
  PR-A1 §4.3.

Do **not**:
- Implement SpEL or constant-ref resolution (PR-A2).
- Touch `graph_enrich.py` or any brownfield code (PR-A3).
- Add any MCP tools (PR-A2).
- Add `HTTP_CALLS` / `ASYNC_CALLS` rels (B2b — separate proposal).
- Reverse the EXPOSES edge direction.

Hard requirements:
- `pytest` must pass at every commit.
- After your changes, running `python build_ast_graph.py
  --source-root tests/bank-chat-system` must produce a graph with
  `ontology_version == 5` and a non-empty `Route` table. Quote the
  resulting `RouteExtractionStats` in your final summary.
- Re-use the microservice-derivation helper that `pass3_calls`
  already uses; do not reinvent.
- `_normalize_path` and `_route_id` must be deterministic — same
  input, same output.

When done, summarise:
1. The exact files touched.
2. The output of `pytest -q`.
3. The output of one manual run on `tests/bank-chat-system` showing
   `routes_total`, `routes_by_framework`, `routes_resolved_pct`.
4. Anything you decided ambiguously and want me to confirm.
```

---

## PR-A2 — SpEL/constant-ref resolution + read-only MCP tools

**Branch:** `feat/b2a-spel-mcp` off the merged PR-A1 (or off
`feat/b2a-route-schema` if A1 is still in review).

**Attach:**
- `@plans/PLAN-TIER1-COMPLETION.md`
- `@ast_java.py`
- `@kuzu_queries.py`
- `@server.py`
- `@tests/test_route_extraction.py` (now exists from PR-A1)
- `@tests/test_kuzu_queries.py`
- `@tests/test_mcp_tools.py`

**Prompt:**

```
You are implementing PR-A2 from `plans/PLAN-TIER1-COMPLETION.md`.

Read the **PR-A2 — B2a SpEL/constant-ref + MCP tools** section in
full first. The plan is the source of truth.

Scope:
- In `ast_java.py`, replace the "skip" branch in `_collect_routes`
  with the three-strategy ladder defined in PR-A2 §1:
  - literal           → `strategy='annotation'`,    `confidence=1.0`,  `resolved=True`
  - SpEL (`${…}`)     → `strategy='spel'`,          `confidence=0.85`, `resolved=False`,
                        `path_template==""`, `path_regex==""`
  - constant_ref      → `strategy='constant_ref'`,  `confidence=0.7`,  `resolved=False`
  SpEL detection: a `string_literal` whose decoded text contains
  `${`. Constant_ref detection: any annotation argument that is not
  a `string_literal`.
- In `kuzu_queries.py`, add `list_routes`, `find_route_handlers`,
  `get_route_by_path` (signatures and Cypher in PR-A2 §2). Do **not**
  add `find_route_callers` — that's B2b.
- **Kuzu MAP-as-STRING reminder:** PR-A1 shipped `routes_by_framework`
  as a `STRING` JSON blob (Kuzu's Python binder rejects `dict` for
  `MAP(STRING, INT64)`). If you add any new graph_meta field that is
  conceptually a map, follow the same pattern (STRING column +
  `json.dumps` on write + decode in `meta()`). PR-A2 likely doesn't
  add new map-shaped fields, but if it does — same rule.
- In `server.py`, expose all three as MCP tools and update
  `_INSTRUCTIONS`.
- Add the new tests numbered 12–18 in PR-A2 §4.

Do **not**:
- Touch brownfield code (PR-A3).
- Add `find_route_callers` or any caller-side resolution.
- Re-derive `_normalize_path` — re-use what PR-A1 shipped.
- Change the `Route` or `EXPOSES` schema.

Hard requirements:
- `pytest` green at every commit.
- After your changes, `graph_meta.routes_resolved_pct` must be
  populated. Quote it from a run on `tests/bank-chat-system`.
- The three new MCP tools must be callable through the server — add
  smoke tests proving this.

When done, summarise:
1. Files touched.
2. `pytest -q` output.
3. The new `routes_resolved_pct` from bank-chat-system.
4. Names of the three new MCP tools and one example call/response
   each.
```

---

## PR-A3 — Brownfield route_overrides + @CodebaseRoute

**Branch:** `feat/b2a-brownfield-routes` off the merged PR-A2.

**Attach (mandatory reading marked with ⭐):**
- `@plans/PLAN-TIER1-COMPLETION.md`
- ⭐ `@plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`
- `@graph_enrich.py`
- `@ast_java.py`
- `@build_ast_graph.py`
- `@README.md`
- `@tests/test_brownfield_overrides.py` (existing pattern — mirror it)

**Prompt:**

```
You are implementing PR-A3 from `plans/PLAN-TIER1-COMPLETION.md`.

⭐ MANDATORY FIRST STEP: read
`plans/completed/PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md`
end-to-end. Note the line numbers of Fix 1 (meta-chain), Fix 2
(iterative closure), and Fix 6 (sorted iteration). You will cite them
in the PR description. The route resolver you write must mirror
`resolve_role_and_capabilities` in `graph_enrich.py` shape-for-shape —
do not invent a parallel system.

Scope:
- Extend `BrownfieldOverrides` in `graph_enrich.py` with
  `annotation_to_route_hint` and `fqn_to_route_hint`. Add a frozen
  `RouteHint` dataclass.
- Extend `_load_brownfield_overrides` to read the new YAML keys
  `route_overrides.annotations` and `route_overrides.fqn` from
  `.lancedb-mcp.yml`. Add **parsing branches inside the existing
  function** — do not duplicate the file-loading code.
- Implement `resolve_routes_for_method` (5-layer last-writer-wins
  composition). Layer order is exactly:
    1. builtin routes
    2. Layer B annotations
    3. Layer A meta-chain (re-use `collect_annotation_meta_chain`)
    4. Layer C in-source `@CodebaseRoute` / `@CodebaseRoutes`
    5. Layer B fqn (outermost)
- Add `@CodebaseRoute(framework, kind, path, method, topic)` and
  `@CodebaseRoutes` (`@Repeatable` container) detection in
  `ast_java.py._collect_routes`. They emit `RouteDecl`s with
  `resolution_strategy='codebase_route'`.
- Wire `resolve_routes_for_method` into `pass4_routes` so brownfield
  overrides actually flow into the graph.
- Add `routes_from_brownfield_pct` and `routes_by_layer` to
  `RouteExtractionStats` and `graph_meta`. **`routes_by_layer` is
  map-shaped — store it as a `STRING` JSON blob, exactly like
  `routes_by_framework` from PR-A1.** Kuzu's Python binder (0.11.x)
  rejects native `dict` for `MAP(STRING, INT64)`. Encode with
  `json.dumps`, decode in `kuzu_queries.meta()`, and extend the
  `_META_LEGACY` query path PR-A1 added so older v5 graphs without
  this column still load.
- Add the 12 brownfield fixtures (tests 19–30) in
  `tests/test_brownfield_routes.py`.
- Update the README's brownfield section to document
  `route_overrides` and `@CodebaseRoute`.

Do **not**:
- Duplicate the file-loading logic in `_load_brownfield_overrides`.
- Touch B4 / B5.
- Add caller-side resolution (B2b).
- Reorder the 5 layers.

Hard requirements:
- `pytest` green at every commit.
- The PR description body (write it as a file
  `/tmp/pr-a3-body.md` for me to copy) must cite specific line numbers
  from `PLAN-BROWNFIELD-ROLE-OVERRIDES-design-fixes.md` (Fix 1, Fix 2,
  Fix 6) showing how your implementation follows the existing pattern.
- Determinism: running `pass4_routes` twice on the same fixture must
  produce byte-identical Route ids (test 29).
- `graph_meta.routes_from_brownfield_pct` must be populated. Define
  it as: % of final routes whose `resolution_strategy ∈ {
    layer_b_ann, layer_a_meta, layer_c_source, layer_b_fqn }`.
  Document this definition in code where the field is computed.

When done, summarise:
1. Files touched.
2. `pytest -q` output.
3. Each of the 12 fixture tests with pass/fail.
4. `routes_from_brownfield_pct` from bank-chat-system.
5. Contents of `/tmp/pr-a3-body.md` (the PR description with Fix
   line-number citations).
```

---

## PR-B — `analyze_pr` MCP tool

**Branch:** `feat/b4-analyze-pr` off `master` (or any prior PR — B
depends on PR-A1's edges only, no other PRs strictly required).

**Attach:**
- `@plans/PLAN-TIER1-COMPLETION.md`
- `@server.py`
- `@kuzu_queries.py`
- `@pyproject.toml`
- `@README.md`
- `@tests/test_mcp_tools.py`
- `@tests/bank-chat-system/` (folder — Sonnet picks one file to craft a sample diff against)

**Prompt:**

```
You are implementing PR-B from `plans/PLAN-TIER1-COMPLETION.md`.

Read the **PR-B — B4 `analyze_pr` MCP tool** section in full first.

Scope:
- Add `unidiff` to `pyproject.toml` (and `requirements.txt` if
  present) and confirm `pip install` works.
- Create new module `pr_analysis.py` with:
    - `ChangedSymbol` and `PrRiskReport` dataclasses (exact shapes
      in the plan).
    - `parse_unified_diff(diff_text)` using `unidiff`.
    - `map_hunks_to_symbols(graph, hunks)`.
    - `compute_risk(graph, changed)` with the formula in PR-B §1.2.
- In `kuzu_queries.py`, add `find_symbols_in_file_range`.
- In `server.py`, expose `analyze_pr(diff_unified: str) -> dict` as
  an MCP tool and update `_INSTRUCTIONS`.
- Add the tests numbered 31–38 in PR-B §4.
- Update README with one example.

Do **not**:
- Touch `graph_enrich.py` or any route code (B2a is independent).
- Parse Java content of added symbols (only graph-resident symbols
  are mapped; new symbols are reported as a count in `notes`).
- Touch B5 / ignore-pattern code.

Hard requirements:
- `pytest` green at every commit.
- The risk-score formula constants are v1 baselines — write a
  comment in `compute_risk` explicitly saying so.
- Binary diffs and renames must not crash; renames are reported in
  `notes`, not `changed_symbols`.
- One smoke test in `tests/test_mcp_tools.py` proves the MCP tool
  is callable.

When done, summarise:
1. Files touched.
2. `pytest -q` output.
3. A sample run: pick a real method in `tests/bank-chat-system`,
   craft a tiny diff, run `analyze_pr` on it, paste the resulting
   JSON.
```

---

## PR-C — Layered ignore patterns

**Branch:** `feat/b5-layered-ignores` off `master` (independent of all
other PRs).

**Attach:**
- `@plans/PLAN-TIER1-COMPLETION.md`
- `@graph_enrich.py`
- `@java_index_flow_lancedb.py`
- `@server.py`
- `@pyproject.toml`
- `@README.md`
- `@tests/test_lancedb_e2e.py`
- `@tests/test_mcp_tools.py`

**Prompt:**

```
You are implementing PR-C from `plans/PLAN-TIER1-COMPLETION.md`.

Read the **PR-C — B5 layered ignore patterns** section in full first.

Scope:
- Add `pathspec` to `pyproject.toml` (and `requirements.txt` if
  present).
- Create new module `path_filtering.py` with:
    - `IgnoreLayer` dataclass.
    - `LayeredIgnore(project_root, *, use_gitignore=True)` class
      with `is_ignored(path)` and `diagnose(path)` methods.
    - Resolution order is **exactly** as specified in PR-C §1:
      `builtin_default → project_root → nested → gitignore`,
      innermost wins, negation patterns honoured.
    - The legacy `COMMON_EXCLUDED_PATH_PATTERNS` constant lives
      here as the `builtin_default` layer.
- Replace every call site of `COMMON_EXCLUDED_PATH_PATTERNS` in
  `graph_enrich.py` and `java_index_flow_lancedb.py` with
  `LayeredIgnore`. After your changes, `grep -rn
  COMMON_EXCLUDED_PATH_PATTERNS *.py` must show only the canonical
  definition in `path_filtering.py`.
- Add a compatibility shim for `iter_java_source_files` that
  accepts the legacy `excludes` parameter, builds a `LayeredIgnore`
  from it, and emits a `DeprecationWarning`.
- Expose `diagnose_ignore(path) -> dict` as an MCP tool in
  `server.py`.
- Add the tests numbered 39–48 in PR-C §4. Include the
  `tests/test_lancedb_e2e.py` extension (test 47).
- Update README with a new "Ignore patterns" section.

Do **not**:
- Touch B2a / B4 code.
- Change the default behaviour for projects **without** a
  `.lancedb-mcp/ignore` file. Existing users must see zero
  difference.
- Use `WildMatchPattern` for the `gitignore` layer — use
  `pathspec.GitIgnoreSpec` so semantics match git exactly.

Hard requirements:
- `pytest` green at every commit.
- Behavioural compatibility test: a project with **no**
  `.lancedb-mcp/ignore` and `use_gitignore=False` must produce the
  exact same indexed file count as before this PR. Add this as an
  explicit test if not already covered.
- The compatibility shim's `DeprecationWarning` must fire when the
  legacy signature is used (test it).

When done, summarise:
1. Files touched.
2. `pytest -q` output.
3. Output of `grep -rn COMMON_EXCLUDED_PATH_PATTERNS *.py` proving
   only one canonical definition remains.
4. Before/after indexed file count on a fixture project that uses
   `.lancedb-mcp/ignore` to exclude generated code.
```

---

# Tips for running these in Cursor

**One PR at a time, in order A1 → A2 → A3 → B → C** unless you
explicitly want B or C earlier (they're independent of A2/A3).

**If Sonnet runs out of context mid-PR**, it can resume from the
step list in the plan (each PR has a numbered step table with "done
when" criteria — that's the resume point). Tell it: "Continue PR-XX
from step N; previous steps are committed."

**If you want a sanity-check before merging**, you have credits for a
quick Opus diff review. The prompt is one line:

```
Review the diff on this branch against `plans/PLAN-TIER1-COMPLETION.md`
PR-XX. Flag anything that violates the plan's "Do not" list or the
"Hard requirements" list.
```

**Do not let Sonnet skip these for any PR:**

- Reading the relevant plan section in full before coding.
- Keeping `pytest` green at every commit.
- Producing the summary block at the end (files / pytest output /
  manual-run evidence).

The plan is structured so that the summary block also serves as the
PR description — copy-paste it verbatim when opening the PR.
