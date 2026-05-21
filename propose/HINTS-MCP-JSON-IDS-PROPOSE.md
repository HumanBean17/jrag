# HINTS-MCP-JSON-IDS — copy-safe hint emissions and id-shape docs

## Status

Proposal — not yet implemented.

**Tracks:** [#195](https://github.com/HumanBean17/java-codebase-rag/issues/195) (battle-test: agents copy Python-style `neighbors(['<id>'],…)` from hints → `Unknown id prefix for \`['<id>']\``).

**Chosen fix combo (issue table):** **1** (hint templates) + **2** (agent guide + tool descriptions) + **6** (align docs with live graph id shape). **Explicitly not** runtime coercion (issue options 3–5, 9) or structured hints (7) in this effort.

**Amends (when implemented):** locked hint catalogs in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` Appendix A and downstream v2/v3/v4 appendices — emission strings only, not trigger logic. **Blocks or lands with:** in-flight [`DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](./DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md) tier-1/2 templates; implementation PR must not merge structural hints on the old shape.

**Handoff:** add `plans/PLAN-HINTS-MCP-JSON-IDS.md` + `plans/CURSOR-PROMPTS-HINTS-MCP-JSON-IDS.md` when opening the implementation PR (~23 `neighbors(` templates + ontology traversals + EDGE-NAVIGATION regen).

## Problem Statement

Road-sign hints in `mcp_hints.py` (and `EDGE_SCHEMA.typical_traversals` in `java_ontology.py`, which feeds empty-`neighbors` teaching strings) use **pseudo-Python** call syntax:

```text
routes via members: neighbors(['{id}'],'out',['DECLARES.EXPOSES'])
```

Agents treat these as literal MCP argument values. A common failure mode:

1. `resolve` / `describe` returns a **40-character hex** symbol id (no prefix).
2. `describe` hint embeds `neighbors(['<sha1>'],…)`.
3. Agent calls `neighbors` with `"ids": "['<sha1>']"` (single-quoted “list” as a string).
4. FastMCP `pre_parse_json` does **not** parse that string (invalid JSON) → one bogus origin id → `_resolve_node_kind` → `success=false`, `Unknown id prefix for \`['<sha1>']\``.

The same flow succeeds when the agent sends valid JSON: `"ids": "<sha1>"`, `"ids": ["<sha1>"]`, or `"ids": "[\"<sha1>\"]"` (FastMCP pre-parse).

This is **hint-format / documentation drift**, not broken graph data or wrong ids from upstream tools.

Secondary failure mode (**#6**): README and `docs/AGENT-GUIDE.md` still show `sym:…FQN…` as the canonical `describe` / `neighbors` example id. **Stored** symbol ids are SHA1 hex from `graph_enrich.symbol_id` (no `sym:` prefix). `sym:` is recognized in `_node_kind_from_id` for kind detection only; passing `sym:<fqn>` as `describe(id=…)` does not hit the graph unless that exact string is stored (it is not). Agents that learn id shape from docs copy the wrong form.

**Related copy-risk (deferred):** `resolve` / `find` / `search` hints use Python kwargs and dict literals (`resolve(identifier, hint_kind='…')`, `filter={{path_prefix: '…'}}`). Same failure class for MCP JSON paste, but **out of scope** for this propose (see §1 scope boundary).

## Proposed Solution

### 1 — Hint emission contract (neighbors-shaped)

Replace pseudo-Python `neighbors(['{id}'], 'out', ['EDGE'])` in **neighbors-shaped** templates and `EDGE_SCHEMA.typical_traversals` with **JSON-shaped** fragments agents can paste into MCP tool calls.

**Scope boundary (locked):** In scope: every `mcp_hints.py` constant that embeds `neighbors(`, all `java_ontology.py` `typical_traversals` values, and v4 neighbors success strings that today use fake `client_ids` / `handler_ids` / `route_ids`. **Out of scope for #195 implementation:** `TPL_FIND_EMPTY_RESOLVE`, `TPL_RESOLVE_NONE_TRY_*`, `TPL_RESOLVE_MANY_TIGHTEN`, `TPL_SEARCH_WEAK` — follow-up propose or same epic second PR if battle-testing shows paste failures on `filter` / `identifier`.

**Canonical single-origin shape (locked):**

```text
<label>: {"ids":"<id>","direction":"<in|out>","edge_types":["<EDGE>"]}
```

When the label prefix would push the rendered string over 120 chars with a 40-char hex id, emit **JSON only** (no label prefix).

**Rules:**

| Rule | Detail |
|------|--------|
| **`ids` for one origin** | Always a **JSON string** value `"<id>"`, not a one-element array, not Python `['…']`. Matches the simplest working wire shape from #195. |
| **Placeholder** | `{id}` is substituted with the **exact** `record.id` / `origin_id` already in the payload (typically 40-char hex for symbols). |
| **Batch / peer ids (normative)** | Never emit literal tokens `client_ids`, `handler_ids`, `route_ids`, `member_ids`, or `producer_ids` as `ids` values. **Locked emission:** `HTTP: per result.id → {"ids":"<id>","direction":"out","edge_types":["HTTP_CALLS"]}` (same pattern for async targets, callers, declaring-method hints). Agent substitutes each concrete `id` from `neighbors.results[].other.id` or the prior `find` row. |
| **Optional params** | Only when the locked catalog already mentions them. Use JSON: `"include_unresolved":true`, `"edge_filter":{"callee_declaring_role":"SERVICE"}` — no Python `True`/`False` or single-quoted dicts. |
| **Multi-hop teaching** | Where Appendix A used `then neighbors(…)`, use two JSON objects separated by ` → ` only when both fit ≤120 chars with a 40-char id; otherwise **first hop only** (see fallout table). |
| **Char cap** | ≤120 chars **after** substitution with `id = "a" * 40` (lowercase hex). Tests must use this fixture, not `sym:a` (see Tests). |
| **Triggers unchanged** | `generate_hints` conditions, priorities, caps, and dedupe logic stay as-is; only template **strings** and `typical_traversals` values change. |
| **Existing cap exemptions** | `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` / `TPL_NEIGHBORS_CALLS_HAS_UNRESOLVED` already exceed 120 chars and are appended without the v4 success gate. This effort does **not** require shortening them unless a drive-by fits the PR; if touched, convert optional params to JSON but do not block #195 on rewriting fanout prose. |

### Template fallout table (40-char hex `id`)

Measured on `id = "a" * 40`. Implementer copies **locked resolution** column verbatim unless a propose amendment is filed.

| Template / family | Current len | JSON + label len | Locked resolution |
|-------------------|------------|------------------|-------------------|
| `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS` | 102 | 122 | Drop label; emit JSON only (102 chars). |
| `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS` | ~102 | ~125 | Same as routes. |
| `TPL_DESCRIBE_TYPE_PRODUCERS_VIA_MEMBERS` | ~105 | ~128 | Same as routes. |
| `TPL_DESCRIBE_METHOD_*_IN_OVERRIDERS` (×3) | 118–118 | 129–138 | Short label (`clients:` / `routes:` / `producers:`) + JSON, or JSON only if still >120. |
| `TPL_DESCRIBE_METHOD_OVERRIDERS` | ~95 | ~115 | JSON with optional short `overriders:` label. |
| `TPL_DESCRIBE_METHOD_OUTBOUND_*` / `INBOUND_ROUTE` | ~90–98 | ~100–110 | JSON + short label; fits cap. |
| `TPL_FIND_SUCCESS_*` (handler / HTTP / async) | ~80–95 | ~90–105 | JSON only or minimal label; fits cap. |
| `TPL_NEIGHBORS_SUCCESS_*` (fake `*_ids`) | ~56 | N/A | **Batch rule** (normative); no fake id token. |
| `TPL_NEIGHBORS_*` structural (`canonical_traversal` from ontology) | varies | varies | Migrate `java_ontology.py` strings; same JSON-only rule when >120. |
| `DESCRIBE-HINTS-STRUCTURAL` rows A–H (not on `master`) | ≤120 with `sym:…` in draft | re-measure with hex | Amend structural propose table to JSON + hex id before merge. |
| `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` | ~400+ | worse if JSON-ified | **Exempt** from 120 cap in this PR; optional JSON for `edge_filter` snippets only if edited. |

**Inventory (grep `neighbors(` in `mcp_hints.py`; expect ~23 templates):**

- All `TPL_DESCRIBE_*` / `TPL_FIND_SUCCESS_*` with `neighbors(`.
- All `TPL_NEIGHBORS_SUCCESS_*` (batch rule rewrite).
- `java_ontology.py` — every `typical_traversals` value containing `neighbors(['{id}']`.
- `scripts/generate_edge_navigation.py` — regenerate `docs/EDGE-NAVIGATION.md` in the same PR if generated from ontology.
- `tests/test_mcp_hints.py` — locked emissions, char-cap parametrization, `canonical_traversal` expectations.

Update `mcp_hints.py` module header to reference this propose.

Extend `MCP_HINTS_FIELD_DESCRIPTION`: hints show **JSON argument objects** for `neighbors`; copy **`record.id`** (or `neighbors.other.id`) verbatim — never Python list literals inside `ids`.

### 2 — Documentation and tool descriptions

**`docs/AGENT-GUIDE.md`** (inside `<!-- BEGIN … END -->` markers):

- New subsection **Hints vs MCP calls** under **Argument shapes**:
  - Neighbors hints use JSON objects; copy ids from tool outputs.
  - **Wrong:** `"ids": "['abc…']"` (Python-style list as string).
  - **Right:** `"ids": "abc…"` or `"ids": ["abc…"]`.
  - Resolve/find hints may still show Python-shaped fragments until the follow-up PR; always prefer constructing calls from the tool schema, not pasting hint punctuation literally.
- Revise **Node ids** table and examples:
  - **Symbol:** primary form is **40-char lowercase hex** (SHA1 of kind|fqn|file|byte).
  - **Route / Client / Producer:** `r:` / `c:` / `p:` or long prefixes + short hash.
  - **`sym:` / FQN-shaped ids:** not stored on Symbol nodes; use `resolve(identifier=<fqn>)` or `describe(fqn=…)` then `describe(id=record.id)`.
- Update workflow table rows and slash-command examples to hex / terminal prefixes, not `sym:…`.
- Extend **JSON, not stringified JSON** table: add `ids` row (wrong = `"['id']"` when a bare string suffices).

**`server.py`:** `neighbors` `ids` `Field(description=…)` — accepted shapes, reject Python-style quoted lists, point to `record.id`.

**`README.md` §4:** Replace `sym:…` examples with hex / `r:` placeholders; one-line callout that symbol ids in responses are hex.

No change to MCP tool **names**, parameters, or response **schemas** beyond description strings.

### 6 — Id-shape alignment (docs-only semantics)

Same PR as §2; no graph or resolver code change.

| Surface | Today | After |
|---------|-------|--------|
| README `describe` example | `sym:com.bank…#method(…)` | `{"id":"<40-char-hex>"}` or `describe(fqn=…)` |
| README `neighbors` example | `sym:…ChatController` | hex id + composed `edge_types` |
| AGENT-GUIDE Node ids | implies `sym:` is normal for Symbol | hex primary; `sym:` as non-stored legacy prefix |
| Char-cap tests | `sym:a` placeholders | **`"a" * 40`** hex fixture |

Confirm on `tests/bank-chat-system`: `describe` on a known controller returns hex `record.id`.

## Scope

**Implementation checklist:**

- [ ] `mcp_hints.py` — neighbors-shaped templates per fallout table + batch rule.
- [ ] `java_ontology.py` — `typical_traversals` JSON migration.
- [ ] `MCP_HINTS_FIELD_DESCRIPTION` + `server.py` / `README.md` / `docs/AGENT-GUIDE.md`.
- [ ] `tests/test_mcp_hints.py` — emissions, char-cap with 40-char hex, no `['{id}']` in rendered neighbors hints.
- [ ] Regenerate `docs/EDGE-NAVIGATION.md` if script-driven.
- [ ] Amend [`DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](./DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md) tier table (rows A–H): JSON emissions + **hex** id in char-cap column (not `sym:…`).
- [ ] Rebase before editing if a branch touches the same `TPL_DESCRIBE_METHOD_*` strings (e.g. completed dot-key work on `master`).

## Schema / Ontology / Re-index impact

- **Ontology bump:** not required (teaching strings only).
- **Re-index required:** no.
- **Config / tool surface changes:** none.

## Tests / Validation

- `tests/test_mcp_hints.py` — update expected strings; **char-cap parametrization must use `id = "a" * 40`** (fixes false green with `sym:a`).
- Assert rendered neighbors hints do not contain `['` or `"['"` after substitution.
- `test_hints_all_v4_templates_under_120_chars` — same 40-char hex fixture for describe/find success templates.
- Optional: table-driven test mirroring §1 fallout rows (template → rendered len ≤120).
- Manual: bank fixture `describe` → paste hint JSON with `record.id` → `neighbors` succeeds; `"ids":"['<id>']"` still fails (no coercion).
- `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v`.

## Open Questions ([TBD])

1. **Label prefix when JSON-only fits** — Prefer dropping label entirely vs ultra-short `routes:` prefix when both are ≤120?
   - **Recommended:** drop label when JSON-only is ≤120; use `routes:` only when it saves agent scanning and still fits.
2. **DESCRIBE-HINTS-STRUCTURAL landing order** — One implementation PR vs JSON migration first?
   - **Recommended:** one PR if structural is not on `master`; else JSON migration PR first, structural amended before merge.
3. **Resolve/find/search JSON hints** — Separate PR?
   - **Recommended:** yes (follow-up); document in AGENT-GUIDE that those hints may remain Python-shaped until then.

## Out of scope

- `_coerce_ids()` / teaching-only fail-loud for `['…` strings (issue options 3–5, 9).
- **JSON migration for** `TPL_FIND_EMPTY_RESOLVE`, `TPL_RESOLVE_NONE_TRY_*`, `TPL_SEARCH_WEAK` (follow-up).
- Structured `hints_structured` (option 7).
- Rewriting `TPL_NEIGHBORS_CALLS_HIGH_FANOUT` length (unless drive-by).
- FastMCP upstream, `neighbors_v2` id generation changes, `ontology_version` bump.

## Sequencing / Follow-ups

1. Land this propose; add `plans/PLAN-HINTS-MCP-JSON-IDS.md` + `CURSOR-PROMPTS-…` for implementation PR.
2. Implementation PR: templates + ontology + tests + docs + structural propose amendment.
3. **Follow-up PR:** resolve/find/search hint JSON + coercion (**1+5**) only if battle-testing still shows paste failures.

Implementation PR must not close #195 until templates, tests, and README/AGENT-GUIDE/server descriptions land.

## PR body template (proposal-only)

```markdown
## What
Adds `propose/HINTS-MCP-JSON-IDS-PROPOSE.md` for #195 (combo 1+2+6).

## Why now
Agents copy Python-style hint syntax into MCP `ids` and get `Unknown id prefix`; docs teach misleading `sym:` ids.

## Highlights
- JSON neighbors hints; 40-char hex char-cap table with per-template resolutions
- Normative batch-id rule (no fake `client_ids`)
- Narrow scope: neighbors-shaped templates; resolve/find deferred
- AGENT-GUIDE + README + server `ids` alignment

## Tests
Docs-only; baseline unchanged.

## Out of scope
Implementation deferred.
```
