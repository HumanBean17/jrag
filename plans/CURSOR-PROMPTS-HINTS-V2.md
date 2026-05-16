# Cursor task prompts — Hints v2

Status: **active (planning)**. Plan:
[`plans/PLAN-HINTS-V2.md`](./PLAN-HINTS-V2.md). Propose:
[`propose/HINTS-V2-PROPOSE.md`](../propose/HINTS-V2-PROPOSE.md).

One prompt per PR. Copy the fenced **Prompt** block into Cursor agent mode with the
listed `@-files` attached.

**Landing order:** PR-HINTS-V2-A → PR-HINTS-V2-B. Do not start PR-HINTS-V2-B until
PR-HINTS-V2-A is merged to `master`.

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only (repo venv).
- Nothing reachable from MCP tool handlers may write to **stdout** (`server.py` stdio rule).
- If ambiguous versus the plan, stop and ask — do not expand scope.
- Do not push git from the agent unless the user explicitly asked.

---

## PR-HINTS-V2-A — `resolve` hints

**Branch:** `feat/hints-v2-resolve` off `master`.
**Base:** `master`.
**Plan section:** [`plans/PLAN-HINTS-V2.md`](./PLAN-HINTS-V2.md) § PR-A.
**PR title:** `feat(hints): add hints field and rules to ResolveOutput`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS-V2.md` (PR-A section + principles)
- `@propose/HINTS-V2-PROPOSE.md` (§3–§4, Appendix A, Decisions §7.14–§7.22)
- `@propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (v1 cap/priority context only)
- `@mcp_hints.py`
- `@mcp_v2.py` (`ResolveOutput`, `resolve_v2`, parsers `_resolve_parse_*`)
- `@server.py` (`resolve` tool description)
- `@README.md` (MCP v2 response extras)
- `@tests/test_mcp_hints.py`

**Prompt:**

````
You are implementing PR-HINTS-V2-A from `plans/PLAN-HINTS-V2.md` (the **PR-A** section).

Read the PR-A **File-by-file changes** and **Tests for PR-A** before coding. If this
prompt and the plan disagree, the plan wins; the propose fills background and locked
template strings (Appendix A).

## Scope

1. **`mcp_v2.py`** — Add `resolved_identifier` and `hints` to `ResolveOutput`
   (`extra="forbid"` preserved). On every `success=True` path set
   `resolved_identifier` to the trimmed identifier. On `success=False` set
   `hints=[]` and `resolved_identifier=None`. After assembling success output, build
   hint payload (`status`, `resolved_identifier`, `candidates`, plumbed `hint_kind`,
   optional `path_prefix_seed` / `target_service_seed` from existing parsers) and set
   `hints=generate_hints("resolve", payload)`.
2. **`mcp_hints.py`** — Extend `generate_hints` for `output_kind == "resolve"` with
   the four templates and rules in `propose/HINTS-V2-PROPOSE.md` Appendix A (120-char
   drop-on-overflow, wildcard suppression, seed suppression). Use `PRIORITY_META`.
3. **`README.md`** — Document `resolve` `hints` + `resolved_identifier` under MCP v2
   response extras; link v2 propose.
4. **`server.py`** — Minimal `resolve` tool description mention of advisory `hints`.
5. **Tests** — Implement every `test_*` name listed under **Tests for PR-A** in
   `plans/PLAN-HINTS-V2.md` (verbatim names). Assert hint presence via substrings, not
   full-string equality.

## Out of scope (do NOT touch)

- `FUZZY_STRATEGY_SET`, neighbors fuzzy template (PR-HINTS-V2-B).
- `java_ontology.py` (except if you discover an unrelated typo — stop and ask).
- `build_ast_graph.py`, `ONTOLOGY_VERSION`, graph schema.
- Changes to v1 `search` / `find` / `describe` / `neighbors` hint catalogs.
- Per-candidate hints, `truncated` on `ResolveOutput`, structured `next_actions`.
- Special-casing `tests/bank-chat-system/` in production code.
- Drive-by refactors outside listed files.

## Deliverables

1. `ResolveOutput` exposes `hints` and `resolved_identifier` per contract.
2. Resolve hint rules wired; `status: one` and validation failures emit `hints: []`.
3. All PR-A named tests exist and pass.
4. README + server copy updated.

## Tests to run (iteration loop)

```bash
.venv/bin/ruff check mcp_hints.py mcp_v2.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k "resolve"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks

On `git diff master..HEAD`, these patterns should be **zero** matches outside allowed files:

- `FUZZY_STRATEGY_SET` (PR-B only)
- `TPL_NEIGHBORS_FUZZY_STRATEGY` (PR-B only)
- `ONTOLOGY_VERSION` changes (not in this PR)

## Definition of Done

- [ ] PR-A plan definition of done satisfied.
- [ ] PR title: `feat(hints): add hints field and rules to ResolveOutput`
- [ ] PR body: scope, link to plan + propose, test commands run, note no re-index.
````

---

## PR-HINTS-V2-B — neighbors fuzzy-strategy hint

**Branch:** `feat/hints-v2-neighbors-fuzzy` off `master` **after PR-HINTS-V2-A is merged**.
**Base:** `master` at merge commit of PR-HINTS-V2-A.
**Plan section:** [`plans/PLAN-HINTS-V2.md`](./PLAN-HINTS-V2.md) § PR-B.
**PR title:** `feat(hints): emit fuzzy-strategy hint when neighbors results carry brownfield/fallback edges`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS-V2.md` (PR-B section + principles)
- `@propose/HINTS-V2-PROPOSE.md` (§3.2 fuzzy set, §4 UC6–UC11/UC17, Appendix A)
- `@java_ontology.py`
- `@mcp_hints.py`
- `@mcp_v2.py` (`neighbors_v2` payload shape — read only unless echo change needed)
- `@README.md`
- `@server.py` (optional neighbors description tweak)
- `@tests/test_mcp_hints.py`
- `@tests/conftest.py` (if round-trip needs fixture/session context)
- `@tests/test_call_graph_smoke_roundtrip.py` (reference for fuzzy `CALLS` strategies)

**Prompt:**

````
You are implementing PR-HINTS-V2-B from `plans/PLAN-HINTS-V2.md` (the **PR-B** section).

PR-HINTS-V2-A is already on `master` (`ResolveOutput.hints`, resolve catalog). Do not
re-land resolve work here.

Read **Tests for PR-B** and propose §3.2 / Appendix A before coding.

## Scope

1. **`java_ontology.py`** — Add `FUZZY_STRATEGY_SET` frozenset with locked members from
   the propose; export in `__all__`.
2. **`mcp_hints.py`** — Import set; add `TPL_NEIGHBORS_FUZZY_STRATEGY` and
   `_any_fuzzy_strategy`; extend `neighbors` branch to append one meta hint when any
   result edge has fuzzy `attrs.strategy`. Preserve existing empty-result hint (UC11).
3. **`README.md`** — Document neighbors fuzzy-strategy hint under MCP v2 extras.
4. **`server.py`** — Only if needed: brief mention that edge `attrs.strategy` indicates
   resolution quality.
5. **Tests** — Implement all `test_*` names under **Tests for PR-B** in the plan
   (verbatim). Craft payloads for unit tests; round-trip via `neighbors_v2` + graph
   (discover fuzzy edge with Cypher — avoid unconditional skip).

## Out of scope (do NOT touch)

- `ResolveOutput`, resolve templates, `resolve_v2` plumbing (PR-A).
- Ontology version bump, `build_ast_graph.py`, re-index docs.
- Issue #147 CI grep invariant (unless user explicitly expanded scope).
- Per-row neighbors hints, confidence thresholds, v1 catalog changes beyond neighbors branch.
- Special-casing `tests/bank-chat-system/` in production code.

## Deliverables

1. `FUZZY_STRATEGY_SET` in ontology; neighbors fuzzy hint wired.
2. All PR-B named tests + round-trip pass.
3. README updated.

## Tests to run (iteration loop)

```bash
.venv/bin/ruff check java_ontology.py mcp_hints.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_hints.py -v -k "neighbors and fuzzy or neighbors_empty"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks

On `git diff master..HEAD`, these should be **zero** outside resolve-related files
(PR-A already merged — should not appear in this PR diff at all):

- Changes to `ResolveOutput` fields beyond what master already has from PR-A
- `TPL_RESOLVE_*` additions (belong to PR-A only)

## Definition of Done

- [ ] PR-B plan definition of done satisfied.
- [ ] PR title: `feat(hints): emit fuzzy-strategy hint when neighbors results carry brownfield/fallback edges`
- [ ] PR body: scope, plan + propose links, test commands, no re-index callout.
````
