# Plan: HINTS-MCP-CALL-SHAPE (copy-safe hints + honest node-id docs)

Status: **active (planning)**. This plan implements
[`propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md`](../propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md)
(issue [#195](https://github.com/HumanBean17/java-codebase-rag/issues/195)).

Depends on: **none** (all landed hint catalogs v1–v4, NEIGHBORS-DOT-KEY, OVERRIDDEN-BY dot-keys are on `master`).

**Coordination:** In-flight [`propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](../propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md) must not land new `TPL_DESCRIBE_*` rows using `neighbors(['{id}']` syntax or a 120-char cap. Whichever lands first wins; the other rebases onto §1 rules + **250-char** cap within the same week.

## Goal

- Agents can **copy hint text into MCP tool calls** without producing `Unknown id prefix for \`['<id>']\`` (pseudo-Python `ids=['{id}']` / `"['…']"` strings).
- Public docs and `Field(description=…)` prose match **live graph id shapes**: Symbol = bare 40-char SHA1 hex; route/client/producer = `r:`/`c:`/`p:` + 16 hex.
- Per-hint rendered cap moves **120 → 250** via unified `HINT_MAX_RENDERED_CHARS`; `EDGE_SCHEMA` / `EDGE-NAVIGATION` traversal strings stay aligned with hint dialect.

## Principles (do not relitigate in review)

- **Named-parameter hint dialect** — every `neighbors` / `resolve` / `search` sketch in hints uses MCP-copyable named args (`ids="{id}"`, `direction="out"`, `edge_types=["…"]`); never `neighbors(['{id}'],…)` or `ids="['…']"`.
- **Single origin** — `ids="{id}"` (string placeholder); batch rows use bare tokens (`ids=client_ids`, `ids=member_ids`), not bracket-wrapped pseudo-lists.
- **`edge_filter` / `filter` in meta hints** — JSON object literals with double quotes, not Python `{{…}}` / single-quoted dicts.
- **Authoritative id source** — hints are advisory; agents copy `describe.record.id`, `find.results[i].id`, `neighbors.results[i].other.id`, or `resolve.candidates[i].id`.
- **No runtime coercion** — do not add `_coerce_ids()` / Python-bracket unwrap in `mcp_v2` (issue options 3–5); docs + hints only.
- **No ontology bump, no re-index** — string-only hint/doc/`description=` changes.
- **Drop-on-overflow unchanged** — substitution still `len > HINT_MAX_RENDERED_CHARS` → omit hint (no truncation).
- **≤ 5 hints per output unchanged** — only per-hint length cap moves.
- **N1a + N1b stay split** — combined dot-key `or` line remains optional follow-up (out of #195 MVP).
- **Amendment record** — do not edit `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` on disk; this propose/plan is the §7.6 (120→250) amendment.

## PR breakdown — overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Hint templates + cap; `EDGE_SCHEMA` traversals; EDGE-NAVIGATION regen; AGENT-GUIDE + README + `server.py` descriptions; sentinel tests | **No** | Mechanical rewrite of 40+ template strings; v4 tests that golden-match old syntax; `canonical_traversal` in empty-neighbors hints; CALLS meta hints with long `edge_filter` | `tests/test_mcp_hints.py` (rename cap tests, new sentinels, update substring asserts) | none |

**Landing order:** **PR-1** only (single implementation PR per propose decision §2).

**Branches:** **Planning** (this effort’s plan PR): `plan/hints-mcp-call-shape`. **Implementation** (code/docs PR-1): `feat/hints-mcp-call-shape-195` off `master` — do not reuse the planning branch name.

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Hint sketch format | Named-parameter MCP sketch in hint text; AGENT-GUIDE slash-alias table keeps JSON blobs for hosts that prefer one object |
| PR split | **One PR** — templates, docs, tests land together |
| DESCRIBE-HINTS-STRUCTURAL | Rebase whichever is second; never merge new `TPL_DESCRIBE_*` with `['{id}']` |
| CI guard | `test_hint_source_no_python_bracket_ids` (sources: `mcp_hints.py`, `java_ontology.py`, `scripts/generate_edge_navigation.py`) + rendered sentinels (`test_hint_templates_never_use_python_list_ids`) |
| Char cap | `HINT_MAX_RENDERED_CHARS = 250` replaces `_FIND_SUCCESS_MAX_CHARS`, `_RESOLVE_HINT_MAX_CHARS`, `_NEIGHBORS_SUCCESS_MAX_CHARS` |
| N1 combined line | Out of MVP — keep N1a + N1b separate |
| `hints_structured` / coercion / FastMCP changes | Out of scope |

---

# PR-1 — MCP-copyable hints, 250-char cap, honest id docs

**Branch:** `feat/hints-mcp-call-shape-195` off `master` (not `plan/hints-mcp-call-shape`).  
**PR title:** `fix(hints): MCP-copyable call sketches and honest symbol id docs (#195)`

## File-by-file changes

### 1. `mcp_hints.py`

- Add module constant **`HINT_MAX_RENDERED_CHARS = 250`**; remove `_FIND_SUCCESS_MAX_CHARS`, `_RESOLVE_HINT_MAX_CHARS`, `_NEIGHBORS_SUCCESS_MAX_CHARS` (all `120`). Wire every drop-on-overflow check through the single constant (`_append_find_success_hint`, `_append_neighbors_success_hint`, resolve render gate, etc.).
- Rewrite **`MCP_HINTS_FIELD_DESCRIPTION`**: MCP-copyable named-parameter sketches; copy `record.id` / `results[i].id` into `ids` (string for one origin), not Python list literals; **250** chars per hint after substitution; max **5** hints per output; retain describe dot-key + empty-neighbors dot-key prohibition wording.
- Module docstring: add pointer to `propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md`.
- Rewrite **all `TPL_*` strings** that embed tool calls to the normative dialect (grep checklist: `neighbors(['` must be zero after edit):

| Group | Current pattern | Target pattern (examples) |
| --- | --- | --- |
| Describe type rollups (3) | `neighbors(['{id}'],'out',['DECLARES.*'])` | `neighbors(ids="{id}", direction="out", edge_types=["DECLARES.DECLARES_CLIENT"])` |
| Describe method / route / client / producer (10) | positional `neighbors(['{id}'],…)` | same named form |
| Find success (3) | positional | `handler: neighbors(ids="{id}", direction="in", edge_types=["EXPOSES"])` |
| Find empty resolve | `resolve(identifier, hint_kind='{kind}')` | `resolve(identifier="{identifier}", hint_kind="{kind}")` — use consistent placeholders |
| Resolve none / many (4) | Python `{{…}}` filters | `find(kind="route", filter={"path_prefix": "{seed}"})`, `search(query="{identifier}")` |
| CALLS meta (3) | `edge_filter={{callee_declaring_role: 'SERVICE'}}` | `edge_filter={"callee_declaring_role":"SERVICE"}` (and siblings for OTHER fallback, exclude roles) |
| v4 success batch (6) | `neighbors(client_ids,'out',['HTTP_CALLS'])` | `neighbors(ids=client_ids, direction="out", edge_types=["HTTP_CALLS"])` (and `handler_ids`, `route_ids`, `producer_ids`, `handler_ids`) |
| Empty-neighbors structural | `{canonical_traversal}` from `EDGE_SCHEMA` | updated when ontology strings change (no local fork of traversal text) |

- **Do not** change hint trigger logic, priorities, cap-of-5, or v4 homogeneity rules — template **wording** and char-cap constant only.

### 2. `java_ontology.py`

- Rewrite **`_SYMBOL_TYPE_TRAVERSAL`**, **`_COMPOSED_MEMBER_TYPE_TRAVERSAL`**, and every `EDGE_SCHEMA[*].typical_traversals` entry (`type_subject`, `member_subject`, `route_subject`, `alien_subject`, two-hop ` then ` chains) from `neighbors(['{id}'],…)` / `neighbors(member_ids,…)` to named-parameter dialect matching `mcp_hints.py` rules.
- Grep sentinel after edit: **zero** `neighbors(['` in this file.

### 3. `scripts/generate_edge_navigation.py`

- Update any hardcoded traversal examples in the generator preamble (OVERRIDDEN_BY block uses `neighbors(['{id}']` today) to named-parameter form so regen does not reintroduce old syntax.

### 4. `docs/EDGE-NAVIGATION.md`

- Regenerate from `EDGE_SCHEMA`:

```bash
.venv/bin/python scripts/generate_edge_navigation.py
```

- Commit regenerated file in the same PR (no hand-edits that drift from `java_ontology.py`).

### 5. `docs/AGENT-GUIDE.md`

- **§ Node ids:** Symbol row = **bare 40-char lowercase SHA1 hex** (what tools emit); note `sym:` is accepted on **input** via prefix convention but not returned on symbol rows from `describe` / `find` / `neighbors`.
- Keep route/client/producer prefixes accurate (`r:`/`c:`/`p:` + 16 hex; document `route:`/`client:`/`producer:` as accepted aliases where already true).
- Add **§ Copying hints into tool calls** (after “JSON, not stringified JSON” or inside `neighbors`): hints advisory; copy ids from tool outputs; for `neighbors` use `ids` as string (one origin) or JSON array (batch) — never `"['…']"`; point to named-parameter hint dialect; FastMCP JSON-array string behavior is host detail, not required agent behavior.
- Update **`ids` (batch)** row if it still implies symbols are always `sym:…` in examples.
- Scan tool examples in this file for `sym:com.bank…` symbol ids → placeholder bare hex or “from `describe.record.id`”.

### 6. `README.md`

- MCP tool table examples: replace `sym:…ChatController…` symbol examples with bare hex placeholder or explicit “40-char hex from `describe.record.id`”; keep prefixed examples for route/client/producer where accurate.
- No new env vars or tools; no “Re-index required” callout (ontology unchanged).

### 7. `server.py`

- **`neighbors` → `ids` `Field(description=…)`:** copy-safe shapes — one id as string, multiple as JSON array; warn against Python-list string (`"['…']"`); symbol ids typically 40-char hex; route/client/producer use `r:`/`c:`/`p:`.
- **`describe` → `id` `Field(description=…)`:** graph-native ids (bare hex for symbols; prefixed terminals); de-emphasize `sym:` as the emitted form; keep input-prefix note briefly if useful for brownfield.
- Tool-level `description=` for `describe` / `neighbors` may add one line on symbol id shape if not redundant with Field text.
- **No** signature or Pydantic model changes beyond `description=`.

### 8. `tests/test_mcp_hints.py`

- Update every assertion that embeds old template substrings (describe integration tests, v4 success tests, `test_hints_neighbors_v2_declares_success_emits_dot_key_clients`, fuzzy/CALLS tests if they match verbatim template constants).
- **Rename / reparametrize char-cap tests:**
  - `test_hints_all_v4_templates_under_120_chars` → **`test_hints_all_v4_templates_under_250_chars`** (`<= 250`).
  - `test_hints_template_rendered_length_leq_120` → **`test_hints_template_rendered_length_leq_250`**; use realistic **40-char hex** `{id}` in parametrize `fmt` (not only `sym:a`); update `canonical_traversal` fixture strings in parametrize to new dialect.
- **Rewrite** `test_hints_neighbors_n1a_n1b_dropped_when_rendered_exceeds_char_cap`: either use an origin id long enough to exceed **250** after substitution, or replace with a direct unit test that `_append_neighbors_success_hint` / describe rollup respects `HINT_MAX_RENDERED_CHARS` (do not keep a test whose only purpose was proving N1 split due to 120-char limit).
- **Add** new tests (verbatim names from propose):
  1. `test_hint_templates_never_use_python_list_ids`
  2. `test_hint_source_no_python_bracket_ids` — assert no `neighbors(['` in `mcp_hints.py`, `java_ontology.py`, and `scripts/generate_edge_navigation.py`
- Extend parametrized catalog sweep to cover CALLS meta templates and resolve templates if not already in `test_hints_template_rendered_length_leq_250`.

## Tests for PR-1

**New (propose § Tests):**

1. `test_hint_templates_never_use_python_list_ids`
2. `test_hint_source_no_python_bracket_ids`

**Rename / update (must stay green with new templates):**

3. `test_hints_all_v4_templates_under_250_chars` (was `_under_120_chars`)
4. `test_hints_template_rendered_length_leq_250` (was `_leq_120`)

**Regression bucket (update golden substrings, do not drop coverage):**

5. All `test_hints_describe_*` that assert full rendered hint strings
6. All `test_hints_neighbors_*` / `test_hints_find_*` success-path tests referencing `TPL_*`
7. `test_hints_neighbors_v2_declares_success_emits_dot_key_clients`
8. `test_hints_resolve_*` / `test_hints_neighbors_calls_*` if they match `edge_filter={{…}}` or resolve template text
9. `test_hints_hv*` / `test_hints_edge_schema_*` only if they assert `canonical_traversal` verbatim strings

**Validation commands (PR evidence):**

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_hints.py -v
```

**Manual smoke (PR body):**

- `describe` on a controller in `tests/bank-chat-system` → copy `record.id` into `neighbors(ids="<id>", direction="out", edge_types=["DECLARES.EXPOSES"])` via MCP host or `neighbors_v2(..., ids=<str>)` — success.
- Optional note: `"ids": "['<id>']"` still fails (documents why hints changed).

## Definition of done (PR-1)

- [ ] `rg "neighbors\\(\\['" mcp_hints.py java_ontology.py scripts/generate_edge_navigation.py` returns **no matches**
- [ ] `HINT_MAX_RENDERED_CHARS = 250` is the only rendered-length constant for hint drop-on-overflow
- [ ] `MCP_HINTS_FIELD_DESCRIPTION` documents 250-char cap and copy-safe `ids` semantics
- [ ] `docs/EDGE-NAVIGATION.md` regenerated and matches `EDGE_SCHEMA`
- [ ] AGENT-GUIDE + README symbol id examples show bare hex (or explicit “from describe”)
- [ ] `server.py` `neighbors.ids` and `describe.id` descriptions warn against Python-list `ids` strings
- [ ] `test_hint_templates_never_use_python_list_ids` and `test_hint_source_no_python_bracket_ids` pass
- [ ] Char-cap tests renamed to `_250` and pass with realistic hex ids
- [ ] `pytest tests/test_mcp_hints.py -v` green without `JAVA_CODEBASE_RAG_RUN_HEAVY`
- [ ] Manual neighbors smoke documented in PR

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `HINT_MAX_RENDERED_CHARS`; unify overflow checks | `mcp_hints.py` | All `120` max-char refs removed |
| 2 | Rewrite describe/find/resolve/CALLS `TPL_*` to named-parameter dialect | `mcp_hints.py` | `rg "neighbors\\(\\['" mcp_hints.py` empty |
| 3 | Rewrite v4 batch `TPL_NEIGHBORS_SUCCESS_*` | `mcp_hints.py` | Batch placeholders use `ids=client_ids` form |
| 4 | Update `MCP_HINTS_FIELD_DESCRIPTION` + module docstring | `mcp_hints.py` | Describes 250 cap + copy rules |
| 5 | Rewrite `EDGE_SCHEMA` traversal strings | `java_ontology.py` | `rg "neighbors\\(\\['" java_ontology.py` empty |
| 6 | Fix generator preamble strings | `scripts/generate_edge_navigation.py` | `rg "neighbors\\(\\['" scripts/generate_edge_navigation.py` empty |
| 7 | Regenerate edge navigation doc | `docs/EDGE-NAVIGATION.md` | `git diff` shows only schema-driven updates |
| 8 | AGENT-GUIDE node ids + hint-copy section | `docs/AGENT-GUIDE.md` | Symbol = bare hex; copy section present |
| 9 | README MCP examples | `README.md` | No misleading `sym:` symbol emit examples |
| 10 | `server.py` Field descriptions | `server.py` | `ids` / `id` copy-safe prose |
| 11 | Add sentinel tests | `tests/test_mcp_hints.py` | Two new tests pass |
| 12 | Rename cap tests; fix parametrize ids/traversals | `tests/test_mcp_hints.py` | `_250` tests pass |
| 13 | Update integration/golden substring asserts | `tests/test_mcp_hints.py` | Full file green |
| 14 | Rewrite N1a/N1b overflow test | `tests/test_mcp_hints.py` | Tests 250-cap policy, not v4 split rationale |
| 15 | Ruff + pytest + manual smoke | — | PR evidence complete |

---

# Cross-PR risks and mitigations

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Large mechanical diff misses a `neighbors(['` string | High | `test_hint_source_no_python_bracket_ids` + `rg` in DoD |
| 2 | DESCRIBE-HINTS-STRUCTURAL lands old syntax on rebase conflict | Medium | Coordination note in PR; grep sentinel in CI |
| 3 | Longer hints exceed 250 and silently drop high-value rows | Medium | Parametrized `test_hints_template_rendered_length_leq_250` with realistic hex + FQN; spot-check CALLS meta templates |
| 4 | Empty-neighbors `canonical_traversal` tests stale | Medium | Update parametrize fixtures in char-cap test when ontology strings change |
| 5 | Agents still copy wrong shape from README slash-alias vs hints | Low | AGENT-GUIDE cross-links hint dialect vs JSON blob table |
| 6 | N1a/N1b overflow test removed without replacement | Low | Step 14: explicit 250-cap drop test |

# Out of scope

- `_coerce_ids()` / Python-bracket unwrap in `mcp_v2.neighbors_v2` (issue #195 options 3, 5)
- Fail-loud-only teaching without coercion (option 4)
- `hints_structured: [{tool, args}]` (option 7)
- Shorter hints-only labels (option 8)
- FastMCP `ast.literal_eval` fallback (option 9)
- Cursor host rules only (option 10)
- Raising **5 hints/output** cap
- Combined N1a+N1b single hint (HINTS-V4 optional follow-up)
- `ontology_version` bump or graph builder id assignment
- Editing files under `propose/completed/` (amendment lives in `propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md`)
- `build_ast_graph.py`, `mcp_v2.py` logic changes (unless a test proves a missing field — not expected)
- Heavy / graph-rebuild e2e (`JAVA_CODEBASE_RAG_RUN_HEAVY`)

# Whole-plan done definition

1. PR-1 merged; issue #195 combo **1+2+6** closed or linked.
2. `propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md` moved to `propose/completed/` after merge.
3. This plan and `plans/CURSOR-PROMPTS-HINTS-MCP-CALL-SHAPE.md` moved to `plans/completed/`.
4. If DESCRIBE-HINTS-STRUCTURAL is still in flight, its author confirms rebase onto named-parameter + 250-cap rules.

# Tracking

- `PR-1`: _pending_
