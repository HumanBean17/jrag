<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Agent task prompts: MCP API v2 redesign

Per-PR delegation prompts derived from
[`plans/PLAN-MCP-API-V2.md`](./PLAN-MCP-API-V2.md). Each prompt below is one
hand-off — copy the section corresponding to the PR you're delegating into a
Cursor task and link the agent to the plan.

**Landing order:** V2-1 → V2-2 → V2-3 → V2-4. Do not start the next prompt
until the prior PR is merged to `master`.

**Common rules for all four prompts:**

- The plan is the source of truth. If a deliverable looks ambiguous, re-read
  the relevant `plans/PLAN-MCP-API-V2.md` section before writing code.
- Do **not** modify files outside the deliverables list. If a refactor is
  tempting, stop and ask.
- "Breaking changes are allowed" — no v1 deprecation period, no aliases. The
  hard cutover is intentional (PR-V2-3).
- No graph schema changes anywhere in this workstream. Do not bump
  `ONTOLOGY_VERSION` in any PR.
- Run the full suite (`python -m pytest tests -q`) before opening the PR.
  Express completion as **baseline + N new** (or − deletions), not as an
  absolute total.
- PR description must include: scope statement (one sentence), manual evidence
  block, link to the matching `PLAN-MCP-API-V2.md` section, and the new-test
  delta line.
- **Manual-evidence commands are samples.** They reference the
  `tests/bank-chat-system` fixture and concrete ids/paths for illustration.
  Sample output may vary depending on the local fixture state (rebuilds,
  ontology drift, machine-specific paths). Adapt ids to whatever the local
  fixture actually contains; the **shape** of the output is the contract, not
  the exact strings.

---

## PR-V2-1: implement search/find/describe/neighbors alongside v1

**Branch:** `feat/mcp-v2-tools` (off latest `master`)
**Base:** `master` at the SHA where PR #48 (the propose) merged
**Plan section:** [`plans/PLAN-MCP-API-V2.md` § PR-V2-1](./PLAN-MCP-API-V2.md#pr-v2-1--implement-searchfinddescribeneighbors-alongside-v1) — read this first
**Estimated diff size:** ~5 files, ~600 LOC (handlers + tests)

### Scope

Implement PR-V2-1 exactly as specified in
[`plans/PLAN-MCP-API-V2.md` § PR-V2-1](./PLAN-MCP-API-V2.md#pr-v2-1--implement-searchfinddescribeneighbors-alongside-v1).
**Nothing else.**

### Out of scope (do NOT touch)

- `describe_v2.edge_summary` — that's PR-V2-2; field stays `None` for now.
- `search_v2.symbol_id` consistent population — that's PR-V2-2.
- `_graph_meta_output` per-edge-type counts — that's PR-V2-2.
- Any v1 tool registration in `server.py` — left untouched until PR-V2-3.
- `user_rag/cli.py` / `pyproject.toml` packaging — that's PR-V2-4.
- Any ontology bump — none in this workstream.
- Drive-by lint fixes, dependency upgrades, performance refactors.

If you find yourself wanting to touch any of the above, **stop and ask**.

### Deliverables

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-1 → File-by-file changes](./PLAN-MCP-API-V2.md#file-by-file-changes)
for the authoritative list. Headline items:

1. New file `mcp_v2.py` with `NodeFilter`, output models, four `*_v2` handlers,
   `_node_kind_from_id` helper. `direction` and `edge_types` are Pydantic
   `Field(...)` (required) on `neighbors_v2`.
2. Modify `server.py` to register four new `@mcp.tool` blocks (`search`,
   `find`, `describe`, `neighbors`) **after** the existing v1 block. Tool
   `description` strings exact per propose §3.
3. Modify `README.md` to add a `### v2 navigation tools (preview)` subsection
   right after the v1 tool reference. Mark "preview, will replace v1 in
   PR-V2-3".
4. New file `tests/test_mcp_v2.py` with the 18 unit tests enumerated in the
   plan.
5. New file `tests/test_mcp_v2_equivalence.py` with the 14 v1↔v2 equivalence
   tests enumerated in the plan.

### Tests

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-1 → Tests for PR-V2-1](./PLAN-MCP-API-V2.md#tests-for-pr-v2-1)
for the full enumeration (18 + 14 = 32 new tests; names are prescribed).

Final: full suite is green and 32 new tests are added — 18 in
`tests/test_mcp_v2.py` and 14 in `tests/test_mcp_v2_equivalence.py`. Express
completion as **baseline + 32 new**, not an absolute count (baselines drift).

### Manual evidence (paste in PR description)

```bash
# Show v2 tools registered alongside v1
grep -nE "@mcp.tool" server.py | wc -l   # expect: 27
# Show no kind argument on describe/neighbors
grep -nE 'def (describe_v2|neighbors_v2)\(' mcp_v2.py
# End-to-end: search → describe → neighbors-in CALLS on bank-chat-system
python -c "
from mcp_v2 import search_v2, describe_v2, neighbors_v2
from kuzu_queries import KuzuGraph
g = KuzuGraph('tests/bank-chat-system/.kuzu')
hits = search_v2('ChatService', table='java', hybrid=True, limit=3, offset=0,
                 path_contains=None, filter=None, graph=g)
sym_id = hits.results[0].symbol_id
print('symbol_id:', sym_id)
rec = describe_v2(sym_id, graph=g)
print('kind:', rec.record.kind, 'fqn:', rec.record.fqn)
edges = neighbors_v2(sym_id, direction='in', edge_types=['CALLS'],
                     limit=10, offset=0, filter=None, graph=g)
print('callers:', len(edges.results))
"
```

### Definition of Done

- [ ] All deliverables shipped per plan §PR-V2-1.
- [ ] Full suite green; 32 new tests (18 in `tests/test_mcp_v2.py` + 14 in
      `tests/test_mcp_v2_equivalence.py`) all pass.
- [ ] MCP surface includes both v1 and v2 registrations in this PR (target: 27
      total tools; verify via test and/or script, not grep formatting quirks).
- [ ] `grep -nE "trace_v2|ask_v2|impact_v2" mcp_v2.py` → 0 matches.
- [ ] Diff is confined to deliverables in this prompt, plus narrowly-related
      test harness/import updates required to make those changes pass.
- [ ] PR description contains: scope statement, manual evidence block, test
      count line, link to `plans/PLAN-MCP-API-V2.md` § PR-V2-1.
- [ ] No ontology bump.

### Context (read these, don't paste them)

- [`propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md`](../../propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md)
  — the why; specifically §3 (proposed surface) and §4 (`NodeFilter` shape).
- [`plans/PLAN-MCP-API-V2.md` § Resolved design decisions](./PLAN-MCP-API-V2.md#resolved-design-decisions-from-the-propose)
  — locked decisions; do not relitigate.
- `kuzu_queries.py` — existing helpers `find_v2` will delegate to
  (`list_routes`, `list_clients`, `list_by_*`).
- `tests/test_kuzu_queries.py` — fixture-loading patterns to mirror.

---

## PR-V2-2: composition tweaks — edge_summary, symbol_id, meta edge counts

**Branch:** `feat/mcp-v2-compose` (off `master` after V2-1 merged)
**Base:** `master` at the SHA where PR-V2-1 merged
**Plan section:** [`plans/PLAN-MCP-API-V2.md` § PR-V2-2](./PLAN-MCP-API-V2.md#pr-v2-2--composition-tweaks-edge_summary-symbol_id-meta-edge-counts) — read this first
**Estimated diff size:** ~4 files, ~250 LOC

### Scope

Implement PR-V2-2 exactly as specified in
[`plans/PLAN-MCP-API-V2.md` § PR-V2-2](./PLAN-MCP-API-V2.md#pr-v2-2--composition-tweaks-edge_summary-symbol_id-meta-edge-counts).
Three small additions that make the four primitives compose cleanly. **Nothing
else.**

### Out of scope (do NOT touch)

- Any v1 tool registration — left until PR-V2-3.
- New v2 handler arguments or behaviours beyond the three composition adds.
- `analyze_pr` / `diagnose_ignore` / `refresh_code_index` / `list_code_index_tables`
  / CLI extraction — that's PR-V2-4.
- Schema migrations, ontology bumps.
- The `v2 navigation tools (preview)` README subsection text — leave as-is for
  PR-V2-3 to promote.
- Removal of v1 fields, types, or models.

If you find yourself wanting to touch any of the above, **stop and ask**.

### Deliverables

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-2 → File-by-file changes](./PLAN-MCP-API-V2.md#file-by-file-changes-1).
Headline items:

1. Modify `mcp_v2.py`: extend `NodeRecord.edge_summary` field; populate via a
   single grouped Cypher count query inside `describe_v2`.
2. Modify `mcp_v2.py`: add `_chunk_to_symbol_id` helper; ensure `search_v2`
   populates `symbol_id` whenever the chunk row carries it.
3. Modify `kuzu_queries.py` (and `GraphMetaOutput` in `server.py`): extend
   `_graph_meta_output` to return `edge_counts: dict[str, int]` for all 9 edge
   types defined in `build_ast_graph.py:_SCHEMA_*`.
4. New file `tests/test_mcp_v2_compose.py` with the 6 tests enumerated in the
   plan.
5. Apply PR-V2-1 review follow-ups that are explicitly in-scope for V2-2:
   - add an inline comment near `neighbors_v2` required `Field(...)` params
     clarifying the intentional direct-call + MCP validation contract;
   - run a light perf checkpoint for `_resolve_node_kind` extra round-trips and
     keep behavior unchanged unless profiling shows meaningful impact.

### Tests

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-2 → Tests for PR-V2-2](./PLAN-MCP-API-V2.md#tests-for-pr-v2-2)
(6 new tests; names are prescribed).

Final: full suite green and 6 new tests added in
`tests/test_mcp_v2_compose.py`. Express completion as **baseline + 6 new**, not
an absolute count.

### Manual evidence (paste in PR description)

```bash
# describe returns edge_summary
python -c "
from mcp_v2 import describe_v2
from kuzu_queries import KuzuGraph
g = KuzuGraph('tests/bank-chat-system/.kuzu')
# Pick any controller id from the fixture
out = describe_v2('sym:BANK_CHAT::ChatController#assignChat', graph=g)
print(out.record.edge_summary)
"

# graph_meta returns edge_counts with all 9 edge types
python -c "
from kuzu_queries import KuzuGraph, _graph_meta_output
g = KuzuGraph('tests/bank-chat-system/.kuzu')
m = _graph_meta_output(g)
print(sorted(m.edge_counts.keys()))
"
# Expect: ['ASYNC_CALLS', 'CALLS', 'DECLARES', 'DECLARES_CLIENT', 'EXPOSES',
#          'EXTENDS', 'HTTP_CALLS', 'IMPLEMENTS', 'INJECTS']
```

### Definition of Done

- [ ] All deliverables shipped per plan §PR-V2-2.
- [ ] Full suite green; 6 new tests in `tests/test_mcp_v2_compose.py` all pass.
- [ ] `describe(any_known_id).edge_summary` is non-None and only contains
      edge-type keys with non-zero counts.
- [ ] `graph_meta()` output includes `edge_counts` covering all 9 edge types.
- [ ] Diff is confined to deliverables in this prompt (`mcp_v2.py`,
      `kuzu_queries.py`, `server.py` for `GraphMetaOutput`/`_graph_meta_output`,
      `tests/test_mcp_v2_compose.py`), plus narrowly-related test
      harness/import updates required to make those changes pass.
- [ ] PR description contains: scope statement, manual evidence block, test
      count line, link to `plans/PLAN-MCP-API-V2.md` § PR-V2-2.
- [ ] No ontology bump.

### Context (read these, don't paste them)

- [`plans/PLAN-MCP-API-V2.md` § PR-V2-2](./PLAN-MCP-API-V2.md#pr-v2-2--composition-tweaks-edge_summary-symbol_id-meta-edge-counts).
- `mcp_v2.py` (from PR-V2-1) — extend, don't rewrite.
- `build_ast_graph.py` `_SCHEMA_*` constants — authoritative list of 9 edge
  types.

---

## PR-V2-3: delete v1 navigation tools

**Branch:** `feat/mcp-v2-cutover` (off `master` after V2-2 merged)
**Base:** `master` at the SHA where PR-V2-2 merged
**Plan section:** [`plans/PLAN-MCP-API-V2.md` § PR-V2-3](./PLAN-MCP-API-V2.md#pr-v2-3--delete-v1-navigation-tools) — read this first
**Estimated diff size:** ~3-4 files, ~−1500 LOC (mostly deletions)

### Scope

Implement PR-V2-3 exactly as specified in
[`plans/PLAN-MCP-API-V2.md` § PR-V2-3](./PLAN-MCP-API-V2.md#pr-v2-3--delete-v1-navigation-tools).
Delete 18 v1 navigation tool registrations and rewrite README's tool reference.
Operational tools stay registered (they move to CLI in V2-4). **Nothing else.**

### Out of scope (do NOT touch)

- Operational tools: `graph_meta`, `analyze_pr`, `diagnose_ignore`,
  `list_code_index_tables`, `refresh_code_index` — they stay registered.
- `user_rag/cli.py` / `pyproject.toml` packaging — that's PR-V2-4.
- `mcp_v2.py` — handlers are stable; do not modify.
- Ontology / schema changes.
- README sections other than the tool reference and migration notes.
- Parameter renaming debates outside explicit scope. PR-V2-3 should record a
  final decision on keeping `describe(id=...)` as-is (contract-first) versus
  renaming; do not let this spill into unrelated files.

If you find yourself wanting to touch any of the above, **stop and ask**.

### Deliverables

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-3 → File-by-file changes](./PLAN-MCP-API-V2.md#file-by-file-changes-2).
Headline items:

1. `server.py`: delete the 18 v1 `@mcp.tool` registrations enumerated in the
   plan (search by name; line numbers will have shifted post-V2-1/V2-2). Also
   delete each tool-private output Pydantic model + helper + unused imports.
2. `README.md`: delete v1 tool reference; promote "v2 navigation tools
   (preview)" to primary `### Tool reference`. Keep ops tools listed as
   "operational — moving to `user-rag` CLI in next release".
3. `docs/PRODUCT-VISION.md`: rewrite v1 example invocations to v2 (per
   propose §11 mapping).
4. Delete `tests/test_mcp_v2_equivalence.py` entirely — v1 no longer exists.
5. Update `tests/test_server.py` (or add if missing) tool-count assertion to:
   exactly 9 MCP tools = 4 navigation + 5 operational.

### Tests

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-3 → Tests for PR-V2-3](./PLAN-MCP-API-V2.md#tests-for-pr-v2-3).
No new tests other than the surface assertion.

Final: full suite green; `tests/test_mcp_v2_equivalence.py` deleted entirely;
surface-assertion test updated to expect 9 registered MCP tools. Express
completion as **baseline − 14 (deleted equivalence) ± 1 (surface assertion)**,
not an absolute count.

### Manual evidence (paste in PR description)

```bash
# Surface assertion
grep -cE "@mcp.tool" server.py   # expect: 9

# No deleted v1 tool name remains
grep -cE 'name="(codebase_search|find_implementors|find_subclasses|find_injectors|find_callers|find_callees|list_routes|list_clients|find_route_handlers|get_route_by_path|find_route_callers|trace_request_flow|list_by_role|list_by_annotation|list_by_capability|graph_neighbors|impact_analysis|trace_flow)"' server.py   # expect: 0

# Equivalence file is gone
ls tests/test_mcp_v2_equivalence.py 2>&1   # expect: No such file

# README tool reference is v2-only
grep -nE "^### Tool reference" README.md
```

### Definition of Done

- [ ] All deliverables shipped per plan §PR-V2-3.
- [ ] Full suite green; `tests/test_mcp_v2_equivalence.py` no longer exists
      (`ls tests/test_mcp_v2_equivalence.py 2>&1` reports "No such file").
- [ ] Surface assertion verifies 9 registered MCP tools.
- [ ] Surface assertion (or equivalent parse-based check) verifies no removed
      v1 navigation tool name remains registered.
- [ ] `tests/test_mcp_v2_equivalence.py` does not exist.
- [ ] README §"Tool reference" lists exactly the 4 v2 tools as primary; ops
      tools noted as transitional.
- [ ] `docs/PRODUCT-VISION.md` example invocations updated to v2.
- [ ] Diff is confined to deliverables in this prompt (`server.py`, `README.md`,
      `docs/PRODUCT-VISION.md`, deleted `tests/test_mcp_v2_equivalence.py`,
      `tests/test_server.py` or equivalent surface-assertion test), plus
      narrowly-related test harness/import updates required to make those
      changes pass.
- [ ] PR description contains: scope statement, manual evidence block, test
      count line, link to `plans/PLAN-MCP-API-V2.md` § PR-V2-3.
- [ ] No ontology bump.

### Context (read these, don't paste them)

- [`plans/PLAN-MCP-API-V2.md` § PR-V2-3](./PLAN-MCP-API-V2.md#pr-v2-3--delete-v1-navigation-tools)
  — list of the 18 tools to delete.
- [`propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md`](../../propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md)
  §11 mapping table — for rewriting `docs/PRODUCT-VISION.md` examples.
- `server.py` history (git log) — to identify each tool's helper-function
  graveyard.

---

## PR-V2-4: extract operational tools into `user-rag` CLI

**Branch:** `feat/user-rag-cli` (off `master` after V2-3 merged)
**Base:** `master` at the SHA where PR-V2-3 merged
**Plan section:** [`plans/PLAN-MCP-API-V2.md` § PR-V2-4](./PLAN-MCP-API-V2.md#pr-v2-4--extract-operational-tools-into-user-rag-cli) — read this first
**Estimated diff size:** ~7 files, ~700 LOC (CLI module + tests + packaging)

### Scope

Implement PR-V2-4 exactly as specified in
[`plans/PLAN-MCP-API-V2.md` § PR-V2-4](./PLAN-MCP-API-V2.md#pr-v2-4--extract-operational-tools-into-user-rag-cli).
Move 5 operational tools from MCP into a console-script CLI. After this PR,
the MCP surface is exactly 4 tools. **Nothing else.**

### Out of scope (do NOT touch)

- The 4 navigation tools — they stay in MCP unchanged.
- Engine code (`pr_analysis`, `LayeredIgnore`, `_graph_meta_output`,
  refresh code path) — CLI subcommands wrap them, do not reimplement.
- Repo-wide package restructure beyond declaring `packages = ["user_rag"]` and
  promoting the new CLI module. Existing root scripts (`server.py`,
  `build_ast_graph.py`, etc.) stay outside the package.
- Ontology / schema changes.
- The `pr-review` skill under `.cursor/skills/pr-review/` — if the MCP V2 migration touches review bash, update `SKILL.md` in-repo or note a follow-up in the PR description.

If you find yourself wanting to touch any of the above, **stop and ask**.

### Deliverables

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-4 → File-by-file changes](./PLAN-MCP-API-V2.md#file-by-file-changes-3).
Headline items:

1. New package `user_rag/` with `__init__.py` (empty) and `cli.py` containing
   `main()` + 5 argparse subcommands (`refresh`, `meta`, `tables`,
   `diagnose-ignore`, `analyze-pr`). Output mode: JSON when piped, pretty when
   TTY. Exit codes 0/1/2 per plan.
2. `pyproject.toml`: `packages = ["user_rag"]`, `[project.scripts]` table with
   `user-rag = "user_rag.cli:main"`.
3. `server.py`: delete 5 operational `@mcp.tool` registrations + their output
   models + unused imports. After this PR, MCP surface is exactly 4 tools
   (`search`/`find`/`describe`/`neighbors`), verified via surface assertion.
4. `README.md`: add top-level `## CLI reference` section (5 subcommands with
   synopsis, flags, examples). Delete the "operational tools (transitional)"
   block from `## Tool reference`. Add "Migration from v1" subsection mapping
   old MCP calls to CLI invocations.
5. `AGENTS.md`: update tool-list paragraph; reference `user-rag --help` for
   ops.
6. New file `tests/test_user_rag_cli.py` with the 8 subprocess tests
   enumerated in the plan.
7. Update final-surface assertion: exactly 4 MCP tools registered.

### Tests

See [`plans/PLAN-MCP-API-V2.md` § PR-V2-4 → Tests for PR-V2-4](./PLAN-MCP-API-V2.md#tests-for-pr-v2-4)
(8 new subprocess tests; names are prescribed).

Final: full suite green and 8 new subprocess tests added in
`tests/test_user_rag_cli.py`. Express completion as **baseline + 8 new**, not
an absolute count.

### Manual evidence (paste in PR description)

```bash
# CLI installed
pip install -e .
user-rag --help   # lists 5 subcommands

# JSON when piped
user-rag meta | python -c "import json,sys; d=json.loads(sys.stdin.read()); print(sorted(d['edge_counts'].keys()))"
# Expect: 9 edge type keys

# Final MCP surface
grep -cE "@mcp.tool" server.py   # expect: 4

# Diagnose ignore on a real path
user-rag diagnose-ignore tests/bank-chat-system/.git/HEAD
# Expect: ignored=True

# Refresh into a temp kuzu path
user-rag refresh --source-root tests/bank-chat-system --kuzu-path /tmp/v24_smoke --quiet
ls /tmp/v24_smoke   # expect: kuzu files
```

### Definition of Done

- [ ] All deliverables shipped per plan §PR-V2-4.
- [ ] Full suite green; 8 new subprocess tests in `tests/test_user_rag_cli.py`
      all pass.
- [ ] Surface assertion verifies **4** registered MCP tools.
- [ ] `pip install .` (or `pip install -e .`) succeeds; `user-rag --help`
      lists 5 subcommands.
- [ ] `user-rag meta | python -c "import json,sys; json.loads(sys.stdin.read())"`
      exits 0.
- [ ] README has top-level `## CLI reference`; `## Tool reference` lists only
      the 4 navigation tools.
- [ ] `AGENTS.md` references `user-rag --help` for ops.
- [ ] No `mcp_v2.py` changes.
- [ ] Diff is confined to deliverables in this prompt (`user_rag/__init__.py`,
      `user_rag/cli.py`, `pyproject.toml`, `server.py`, `README.md`,
      `AGENTS.md`, `tests/test_user_rag_cli.py`, plus the surface-assertion
      test), plus narrowly-related test harness/import updates required to make
      those changes pass.
- [ ] PR description contains: scope statement, manual evidence block, test
      count line, link to `plans/PLAN-MCP-API-V2.md` § PR-V2-4, **plus a
      manual TODO note** to update `.cursor/skills/pr-review/SKILL.md` (or equivalent) for the `pr-review`
      skill (its `analyze_pr` MCP call → `user-rag analyze-pr --diff-file`).
- [ ] No ontology bump.

### Context (read these, don't paste them)

- [`plans/PLAN-MCP-API-V2.md` § PR-V2-4](./PLAN-MCP-API-V2.md#pr-v2-4--extract-operational-tools-into-user-rag-cli).
- Existing engine entry points: `pr_analysis.analyze_pr_pipeline`,
  `LayeredIgnore.diagnose_dict`, `_graph_meta_output`, refresh code path —
  CLI subcommands wrap these. Do not reimplement.
- `pyproject.toml` (current) — `packages = []` is the starting point.
- `tests/test_pr_analysis.py` — fixture-loading patterns to mirror in CLI
  tests.

---

## After all four PRs land

The MCP surface is exactly 4 tools (`search`, `find`, `describe`, `neighbors`)
and `user-rag` is a console-script CLI exposing 5 ops subcommands. The triplet
of artifacts (propose / plan / cursor-prompts) is the audit trail; close the
loop by:

1. Move [`propose/MCP-API-V2-REDESIGN-PROPOSE.md`](../../propose/completed/MCP-API-V2-REDESIGN-PROPOSE.md)
   to `propose/completed/` and update `**Status**` to `locked`.
2. Move [`plans/PLAN-MCP-API-V2.md`](./PLAN-MCP-API-V2.md) to
   `plans/completed/PLAN-MCP-API-V2.md` (mirrors `PLAN-LIST-CLIENTS-MCP-TOOL.md`
   convention).
3. This file (`plans/AGENT-PROMPTS-MCP-API-V2.md`) can stay in `plans/` or
   move alongside the plan — convention is yours; mirror what the previous
   completed plan did.
