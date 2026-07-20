> **⚠️ LEGACY FORMAT — archived. Do not use as a template/pattern.** This
> document uses the pre-superpowers proposal/plan format and is kept here for
> history only. For the current spec/plan format, see
> `docs/superpowers/specs/active/` and `docs/superpowers/plans/active/`.

# Plan: `resolve` tool — identifier-shaped lookup primitive

Status: **completed (landed)** — PR #135 (`resolve`) + PR #140 (tool-description sweep). Implements
[`propose/completed/RESOLVE-TOOL-PROPOSE.md`](../propose/completed/RESOLVE-TOOL-PROPOSE.md).

Depends on: **MCP filter frame landed** ([`PLAN-MCP-FILTER-FRAME.md`](PLAN-MCP-FILTER-FRAME.md) — PR-FRAME-1 → PR-FRAME-3). No graph-builder or Lance work.

Per-PR Cursor prompts: [`AGENT-PROMPTS-RESOLVE-TOOL.md`](AGENT-PROMPTS-RESOLVE-TOOL.md).

## Goal

- Ship a **fifth MCP V2 tool**, `resolve(identifier, hint_kind?)`, as the strict-frame primitive for identifier-shaped lookups (Symbol, Route, Client).
- Return a **three-state discriminated envelope** (`status`: `one` | `many` | `none`) with closed per-candidate `reason` values — no silent best-guess.
- **Remove** the pre-`resolve` `search` + `describe`-per-candidate fallback wording from all agent-facing surfaces once the tool exists (PR-RESOLVE-2).
- Keep the four existing primitives, closed `EdgeType`, strict `NodeFilter`, and `search.query` carve-out **unchanged**.

## Principles (do not relitigate in review)

- **Identifier-shaped, not query-shaped.** Natural-language or wildcard inputs return `status="none"` (well-formed miss) or `success=False` (malformed). Fuzzy work stays on `search`.
- **Three loud states, no silent rank-away.** Ambiguity is always `status="many"` with ≥2 candidates; never a single `node` chosen by score alone.
- **`hint_kind` only.** No `microservice` co-hint, no `hints: dict`. Cross-microservice FQN collisions surface per-candidate `NodeRef.microservice`.
- **Closed `ResolveReason` vocabulary** in `java_ontology.py` (frame decision to add a new reason, like `EdgeType`).
- **Composability over convenience.** `resolve` returns `NodeRef`; the agent calls `describe(id=…)` next. No bundled describe payload.
- **No users, no deprecation aliases.** Two PRs, strict landing order. Breaking description changes are intentional.
- **No ontology bump, no reindex.** Tool-surface-only; graph schema and enrichment semantics are untouched.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-RESOLVE-1 | `resolve` models, handler, candidate generators, ranking, MCP registration, tests | none | Input-shape parsing (multi-token routes, client `target + path`); dedupe when generators overlap; reason-priority tiebreaks; namespace vs internal `_resolve_node_kind` | `tests/test_mcp_v2.py` + optional `kuzu_graph_fqn_collision_smoke` fixture | prerequisite only |
| PR-RESOLVE-2 | Agent-facing prose sweep: `server.py`, `mcp_v2` hint, `docs/AGENT-GUIDE.md`, `README.md`, `AGENTS.md`, `.cursor/rules/project-overview.mdc`, operator/skill/checklist docs | none | Removing fallback before RESOLVE-1 merges leaves agents without a path — **blocked on PR-RESOLVE-1**; keep “five tools” consistent everywhere Cursor/agents load rules | description / hint tests in `tests/test_mcp_v2.py` | PR-RESOLVE-1 |

Landing order: **RESOLVE-1 → RESOLVE-2**.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Tool count | Fifth tool `resolve`; not a mode on `search` or `find`. |
| Kinds in scope | Symbol, Route, Client in one tool; `hint_kind` narrows generators. |
| Output shape | `ResolveOutput`: `success`, `status`, optional `node`, `candidates[]`, optional `message`. |
| Malformed input | `success=False`, `status="none"`, `message` starts `"Invalid identifier:"` (agent branches on `success` first). |
| Well-formed miss | `success=True`, `status="none"`, non-empty `message` naming `search` as fallback. |
| Candidate cap | `K = 10` module constant in `mcp_v2.py`; not a tool parameter. |
| Dedup | By `node.id` before status decision; keep highest-priority `reason` per id. |
| Ranking contract | Order by reason priority → matched specificity (length) → `node.id` ascending. `score` is telemetry-only. |
| `describe(fqn=…)` | Unchanged behavior in PR-RESOLVE-1; PR-RESOLVE-2 updates collision `hint_message` to point at `resolve`. No `microservice` parameter on `describe`. |
| `ResolveReason` location | `java_ontology.py` (`VALID_RESOLVE_REASONS` + `ResolveReason` Literal). Models in `mcp_v2.py` import it. |
| Wildcards in identifier | No match (generators do not treat `*` / `?` specially); `status="none"`. |
| Internal helper naming | Do **not** rename `_resolve_node_kind` in PR-RESOLVE-1 unless a reviewer flags confusion; the new public tool is `resolve_v2`. |

---

# PR-RESOLVE-1 — Implement `resolve`

## File-by-file changes

### 1. `java_ontology.py`

- Add closed set and typing export:

```python
VALID_RESOLVE_REASONS: frozenset[str] = frozenset((
    "exact_id",
    "exact_fqn",
    "fqn_suffix",
    "short_name",
    "route_template",
    "route_method_path",
    "client_target",
    "client_target_path",
))

ResolveReason = Literal[
    "exact_id",
    "exact_fqn",
    "fqn_suffix",
    "short_name",
    "route_template",
    "route_method_path",
    "client_target",
    "client_target_path",
]
```

- Export in `__all__`.

### 2. `mcp_v2.py`

- Update module docstring first line to list five tools (`resolve` added).
- Add models (after existing `*Output` classes, before handlers):

  - `ResolveCandidate` (`node: NodeRef`, `score: float`, `reason: ResolveReason`, `extra="forbid"`).
  - `ResolveOutput` (`success`, `status: Literal["one","many","none"]`, optional `node`, `candidates`, `message`, `extra="forbid"`).

- Add module constants:

  - `_RESOLVE_CANDIDATE_CAP = 10`
  - `_RESOLVE_REASON_PRIORITY: dict[str, int]` per propose §3.5 (`exact_id` highest, then exact-tier reasons, then suffix/template, then short_name/client_target).

- Add private helpers (keep in `mcp_v2.py`; no `kuzu_queries` changes required unless a query is genuinely reusable):

  | Helper | Role |
  | --- | --- |
  | `_resolve_validate_identifier(raw: str) -> tuple[str \| None, str \| None]` | Strip; return `(None, err)` for empty/whitespace with `"Invalid identifier: …"`; else `(trimmed, None)`. |
  | `_resolve_kinds_to_search(hint_kind)` | `None` → all three kinds; else singleton list. |
  | `_resolve_symbol_candidates(g, identifier, …)` | Emit `(NodeRef, reason, specificity_len)` for: canonical `sym:` id (`exact_id`); `s.fqn = $fqn` (`exact_fqn`); suffix match on `s.fqn` (`fqn_suffix` — identifier equals suffix or `s.fqn ENDS WITH '.' + identifier`); `s.name = $name` for short name (`short_name`). Use `g._rows` with bounded `LIMIT` (e.g. 50 pre-dedup). |
  | `_resolve_route_candidates(g, identifier, …)` | `route:`/`r:` id (`exact_id`); `"METHOD /path"` split (`route_method_path` — match `r.method` + `r.path_template` or stored path); leading-`/` path (`route_template`); `"<microservice> METHOD /path"` three-token form (`route_method_path` + filter `r.microservice`). Reuse row shape from `list_routes` / `_ROUTE_RETURN` via `_rows`. |
  | `_resolve_client_candidates(g, identifier, …)` | `client:`/`c:` id (`exact_id`); single token → `c.target_service` (`client_target`); `"<service> <path_prefix>"` first-space split (`client_target_path` — `target_service` exact + `c.path` or `c.path_template` prefix match). Reuse `list_clients` or targeted `_rows`. |
  | `_resolve_dedupe_candidates(raw)` | Key by `node.id`; keep tuple with best (lowest) reason priority; merge specificity len as max. |
  | `_resolve_rank_candidates(deduped)` | Sort by priority, `-specificity`, `node.id`; assign `score` as descending rank index or `1.0 - i/K` (document as non-stable). |
  | `_resolve_build_output(matches)` | Apply §3.6 status rule + cap; call `_resolve_assert_invariants(out)` before return. |
  | `_resolve_assert_invariants(out)` | Debug assertion / internal guard for propose §3.2 invariants on `success=True`. |

- Add public handler:

```python
def resolve_v2(
    identifier: str,
    hint_kind: Literal["symbol", "route", "client"] | None = None,
    graph: KuzuGraph | None = None,
) -> ResolveOutput:
```

Flow: validate identifier → run enabled generators → dedupe → rank → slice `[:_RESOLVE_CANDIDATE_CAP]` → status decision → invariants.

- **Do not** change `find_v2`, `search_v2`, `neighbors_v2`, or `describe_v2` behavior in this PR (except importing new types if needed).

### 3. `server.py`

- Extend `_INSTRUCTIONS` so the tool inventory lists **five** tools (`search`, `find`, `describe`, `neighbors`, `resolve`) — one short clause for `resolve` is enough; do **not** remove the pre-`resolve` fallback from sibling tool descriptions yet (PR-RESOLVE-2).
- Register `@mcp.tool(name="resolve", …)`:

```python
async def resolve(
    identifier: str = Field(description="Identifier-shaped node lookup (FQN, id prefix, route path, client target, …)"),
    hint_kind: Literal["symbol", "route", "client"] | None = Field(
        default=None,
        description="Optional kind constraint. Omit to search all three kinds.",
    ),
) -> mcp_v2.ResolveOutput:
    return await asyncio.to_thread(mcp_v2.resolve_v2, identifier, hint_kind, None)
```

- Tool `description=` for PR-RESOLVE-1 should be **complete** (identifier-shaped lead, three statuses, `search` fallback on `none`, UC14/UC15 examples) even though sibling tools still mention the old fallback until PR-RESOLVE-2.

### 4. `tests/conftest.py`

- Add session fixture mirroring `kuzu_graph_route_extraction_smoke`:

```python
@pytest.fixture(scope="session")
def kuzu_graph_fqn_collision_smoke(kuzu_db_path_fqn_collision_smoke: Path):
    from kuzu_queries import KuzuGraph
    return KuzuGraph(str(kuzu_db_path_fqn_collision_smoke))
```

(`kuzu_db_path_fqn_collision_smoke` already exists.)

### 5. `tests/test_mcp_v2.py`

- Import `resolve_v2`, `ResolveOutput`, `VALID_RESOLVE_REASONS` (or collect reasons from responses).
- Add tests listed in **Tests for PR-RESOLVE-1** (below). Prefer:
  - **bank-chat** `kuzu_graph` for routes/clients/short-name ambiguity.
  - **`kuzu_graph_fqn_collision_smoke`** for UC3 (`com.example.SharedDto` — two microservices).
  - **`kuzu_graph_route_extraction_smoke`** for deterministic `GET /…` resolution when bank-chat is noisy.

## Tests for PR-RESOLVE-1

Contract tests (names are binding):

1. `test_resolve_exact_id_symbol_returns_one` — pick a known `sym:…` from `kuzu_graph` via `find_v2`; `resolve(that_id)` → `success`, `status=="one"`, matching `node.id` (`exact_id` reason coverage is test 16).
2. `test_resolve_exact_fqn_symbol_returns_one` — unique FQN from bank-chat; `hint_kind="symbol"` → `one`, reason coverage for `exact_fqn`.
3. `test_resolve_fqn_collision_across_microservices_returns_many` — `resolve("com.example.SharedDto", hint_kind="symbol", graph=kuzu_graph_fqn_collision_smoke)` → `many`, ≥2 candidates, distinct `microservice`, reasons include `exact_fqn`.
4. `test_resolve_short_name_ambiguity_returns_many` — bank-chat: resolve a common short class name (e.g. last segment of a duplicated simple name) with `hint_kind="symbol"` → `many`, reasons include `short_name`.
5. `test_resolve_status_none_returns_nonempty_message` — `resolve("com.nonexistent.ZzzMissing", hint_kind="symbol")` → `success`, `status=="none"`, `message` non-empty, mentions `search` (substring match).
6. `test_resolve_empty_identifier_success_false` — `resolve("")` → `success=False`, `status=="none"`, `message` startswith `"Invalid identifier:"`.
7. `test_resolve_whitespace_identifier_success_false` — `resolve("   ")` — same as above.
8. `test_resolve_cross_kind_without_hint_returns_mixed_kinds` — only if bank-chat yields a stable identifier matching multiple kinds; otherwise use a **small inline `FakeGraph`** stub (same pattern as `test_describe_by_fqn_duplicate_returns_first_with_disambiguation_hint`) asserting ≥2 kinds in `candidates[].node.kind`. Document skip if neither stub nor corpus cooperates.
9. `test_resolve_dedupes_overlapping_generator_paths` — **FakeGraph** stub: one symbol row returned from both FQN-equality and short-name queries; `len(candidates)==1` after dedupe.
10. `test_resolve_route_method_path_returns_one` — `kuzu_graph_route_extraction_smoke`: `resolve("GET /…", hint_kind="route")` → `one`, reason `route_method_path`.
11. `test_resolve_route_template_returns_one_or_many` — bare `"/…"` path with `hint_kind="route"` → `route_template` in reasons.
12. `test_resolve_client_target_service` — bank-chat: `resolve("<target_service>", hint_kind="client")` → `client_target` reason present; `one` or `many` accepted.
13. `test_resolve_client_target_path_pair` — `resolve("<target> /api/…", hint_kind="client")` → `client_target_path` in reasons.
14. `test_resolve_natural_language_sentence_returns_none` — UC14 sentence → `none`, not `success=False`.
15. `test_resolve_wildcard_identifier_returns_none` — `resolve("com.foo.*Service", hint_kind="symbol")` → `none`.
16. `test_resolve_every_reason_in_closed_set_appears` — parametrized or loop: run targeted resolves (or FakeGraph) so each `VALID_RESOLVE_REASONS` member appears at least once across the module's resolve tests.
17. `test_resolve_success_output_invariants` — on a `one` and a `many` response: assert propose §3.2 field population (`node` xor populated `candidates`, etc.).

Optional MCP registration smoke (if lightweight): extend existing server tool-list test to expect `"resolve"` in registered tools.

## Definition of done (PR-RESOLVE-1)

- `resolve` callable via MCP and `mcp_v2.resolve_v2` directly.
- `_INSTRUCTIONS` lists **five** tools including `resolve`.
- All 17 tests above pass (test 8 may `pytest.skip` only with a comment pointing at FakeGraph requirement — prefer stub over skip).
- `.venv/bin/ruff check .` clean.
- `.venv/bin/python -m pytest tests/test_mcp_v2.py -v -k resolve` green.
- Full `pytest tests -v` green without `JAVA_CODEBASE_RAG_RUN_HEAVY`.
- `grep -rn 'until.*resolve' server.py` may still hit PR-RESOLVE-2 strings — **allowed in PR-RESOLVE-1**; PR-RESOLVE-2 clears them.
- No `ontology_version` change in `build_ast_graph.py` / README reindex callout.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `VALID_RESOLVE_REASONS` + `ResolveReason` | `java_ontology.py` | Importable from `mcp_v2` |
| 2 | Add models + constants + invariant helper | `mcp_v2.py` | `ResolveOutput` validates in REPL |
| 3 | Implement symbol candidate generator | `mcp_v2.py` | Manual `resolve_v2("sym:…")` returns `one` on bank graph |
| 4 | Implement route + client generators | `mcp_v2.py` | Route smoke + bank client targets work |
| 5 | Wire dedupe, rank, cap, status decision | `mcp_v2.py` | Collision fixture returns `many` |
| 6 | Register MCP tool | `server.py` | Tool list includes `resolve` |
| 7 | Add `kuzu_graph_fqn_collision_smoke` fixture | `tests/conftest.py` | Fixture builds |
| 8 | Add tests 1–17 | `tests/test_mcp_v2.py` | `pytest -k resolve` green |
| 9 | Ruff + full test suite | repo | CI-equivalent local pass |

---

# PR-RESOLVE-2 — Tool-description sweep

## File-by-file changes

### 1. `server.py`

- **`_INSTRUCTIONS`**: Add `resolve` to the tool inventory sentence; remove any `search` + `describe`-per-candidate fallback chain wording.
- **`search` description**: Remove "until a dedicated `resolve` tool exists" and per-candidate describe fallback; add one line: identifier-shaped lookups → `resolve`.
- **`describe` description**: Remove fallback paragraph; note `describe(fqn=…)` keeps first-match on collision; canonical disambiguation → `resolve(…, hint_kind="symbol")`.
- **`find` / `neighbors` descriptions**: Scan and remove fallback wording if present; verify no smart-by-nature claims.

### 2. `mcp_v2.py`

- Update `describe_v2` collision `hint_message` (lines ~677–679 today):

  - **Remove** references to `find` + `search` as the primary disambiguation path.
  - **Point at** `resolve(identifier=<fqn>, hint_kind='symbol')` for FQN collisions.

### 3. `docs/AGENT-GUIDE.md`

- MCP surface line: five tools including `resolve`.
- Rename heading **`### Tool reference — four tools`** → **`### Tool reference — five tools`** (and any other “four tools” cardinal in this file).
- Rename **Identifier resolution (pre-`resolve`)** → **Identifier resolution**; document `resolve` three-state flow and `resolve → describe` chain; remove the `search` + describe-per-candidate fallback entirely.
- Decision tree row: add "Have identifier-shaped string" → `resolve` first.
- Forced reasoning preamble: add `resolve` to Pick line examples (`Pick: <search|find|describe|neighbors|resolve>`).
- `describe` section: point ambiguous FQN case at `resolve`.

### 4. `README.md`

- Opening paragraph: **five tools** (`search`, `find`, `describe`, `neighbors`, `resolve`).
- MCP tool table: add `resolve` row with signature and example JSON.
- Agent guide blurb: five tools.

### 5. `AGENTS.md`

- MCP tool list and any “four tools” wording → five tools including `resolve`.

### 6. `.cursor/rules/project-overview.mdc`

- MCP tools line and README cross-refs → five tools including `resolve`.

### 7. `docs/JAVA-CODEBASE-RAG-CLI.md`

- One-line clarification: MCP navigation surface is five tools (not operator CLI).

### 8. `docs/skills/java-codebase-explore.md`

- Workflow / fallback prose: identifier-shaped lookups → `resolve` first; keep `search`/`find` for discovery, not as the documented identifier-resolution fallback chain.

### 9. `docs/MANUAL-VERIFICATION-CHECKLIST.md`

- Navigation tools list → five tools including `resolve`.

### 10. `tests/test_mcp_v2.py`

- **Rename** `test_describe_by_fqn_duplicate_returns_first_with_disambiguation_hint` → `test_describe_by_fqn_duplicate_hint_points_to_resolve` (binding name).
- Update assertions: drop `find` / `search` disambiguation requirements in hint; require `resolve` substring.

## Canonical sentinel grep (PR-RESOLVE-2)

Use this exact command in PR descriptions and [`AGENT-PROMPTS-RESOLVE-TOOL.md`](AGENT-PROMPTS-RESOLVE-TOOL.md) (allow `search` only inside `resolve` tool description / `status="none"` messages):

```bash
grep -En 'per\.candidate|until.*resolve|promising candidates|search\(query=.*\).*describe' \
  server.py mcp_v2.py docs/AGENT-GUIDE.md README.md AGENTS.md \
  docs/JAVA-CODEBASE-RAG-CLI.md docs/MANUAL-VERIFICATION-CHECKLIST.md \
  docs/skills/java-codebase-explore.md .cursor/rules/project-overview.mdc || true
```

## Tests for PR-RESOLVE-2

1. `test_describe_by_fqn_duplicate_hint_points_to_resolve` — replaces the old duplicate-FQN hint test (rename required).
2. `test_server_tool_descriptions_no_pre_resolve_fallback` — load `create_mcp_server()`, inspect tool descriptions + `_INSTRUCTIONS`: assert no matches for the canonical sentinel patterns above on identifier-resolution paths. **Allow** `search` inside `resolve` tool description.

Manual evidence (PR description): run the **canonical sentinel grep** above, then:

```bash
.venv/bin/python -m pytest tests/test_mcp_v2.py -v -k 'resolve or describe_by_fqn_duplicate_hint_points_to_resolve or tool_descriptions_no_pre_resolve'
```

## Definition of done (PR-RESOLVE-2)

- All agent-facing surfaces in the propose §6 checklist **and** §5–§9 above updated (no doc still claims four MCP navigation tools).
- `test_describe_by_fqn_duplicate_hint_points_to_resolve` passes.
- Sentinel grep reviewed (zero actionable fallback recommendations).
- Full `pytest tests -v` green.
- No ontology bump.

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Sweep `server.py` tool descriptions + `_INSTRUCTIONS` | `server.py` | Grep clean |
| 2 | Update `describe_v2` hint | `mcp_v2.py` | Test 1 updated expectations pass |
| 3 | Rewrite identifier resolution + rename tool-reference heading | `docs/AGENT-GUIDE.md` | Five-tool surface; no “four tools” heading |
| 4 | README MCP table + intro | `README.md` | `resolve` row present |
| 5 | Agent entrypoints | `AGENTS.md`, `.cursor/rules/project-overview.mdc` | Five-tool wording |
| 6 | Operator / skill / checklist | `docs/JAVA-CODEBASE-RAG-CLI.md`, `docs/skills/java-codebase-explore.md`, `docs/MANUAL-VERIFICATION-CHECKLIST.md` | No stale four-tool lists |
| 7 | Rename hint test + sentinel test | `tests/test_mcp_v2.py` | PR-RESOLVE-2 tests green |
| 8 | Canonical sentinel grep | all touched files | Grep reviewed clean |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | PR-RESOLVE-2 merges before PR-RESOLVE-1 | High | Block PR-RESOLVE-2 in GitHub until RESOLVE-1 is on `master`; state landing order in both PR bodies. |
| 2 | Agent uses `resolve` for NL queries | Medium | Tool description + `none` message name `search`; test UC14 (`test_resolve_natural_language_sentence_returns_none`). |
| 3 | Ranking / score treated as stable API | Medium | Document telemetry-only `score`; tests assert `status` + `reason`, not score thresholds. |
| 4 | Generator overlap inflates `many` | Medium | Dedupe by `node.id` with best reason; dedicated test 9. |
| 5 | Route / client parsing false positives | Medium | Dedicated route + client tests on smoke fixtures; prefer explicit `hint_kind` in docs for ambiguous strings. |
| 6 | `_resolve_node_kind` vs `resolve_v2` confusion | Low | Do not rename internal helper in this effort unless necessary; code review checklist item. |
| 7 | Cross-kind test flaky on bank-chat | Low | Allow FakeGraph stub for test 8; do not special-case bank-chat in production code. |

# Out of scope

- `microservice` (or any) co-hint on `resolve` or `describe`.
- Wildcard / regex identifier parsing.
- Bundling `describe` payload into `ResolveOutput`.
- Per-kind tools (`resolve_symbol`, …).
- Changing `describe(fqn=…)` first-match semantics (optional follow-up propose only).
- Renaming internal `_resolve_node_kind` (unless reviewer-mandated).
- `kuzu_queries.py` new public list APIs (prefer targeted `_rows` in `mcp_v2` unless duplication hurts).
- Ontology version bump, graph builder, Lance indexer, ranking model.
- Pagination parameter on `resolve`.
- MCP tool to expose `filter_frame_counters` or resolve telemetry.

# Whole-plan done definition

1. `resolve` is registered and passes all PR-RESOLVE-1 contract tests on CI.
2. No agent-facing doc or tool description recommends `search` + `describe`-per-candidate for identifier-shaped lookups.
3. `describe` FQN-collision hint points at `resolve`.
4. README, AGENT-GUIDE, AGENTS.md, and `project-overview.mdc` list five MCP tools with `resolve` documented.
5. Propose lives at `propose/completed/RESOLVE-TOOL-PROPOSE.md`.
6. Plan + prompts live in `plans/completed/` (this file and `AGENT-PROMPTS-RESOLVE-TOOL.md`).

# Tracking

- `PR-RESOLVE-1`: merged (#135)
- `PR-RESOLVE-2`: merged (#140)
