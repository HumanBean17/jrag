# HINTS-MCP-JSON-IDS — copy-safe hint emissions and id-shape docs

## Status

Proposal — not yet implemented.

**Tracks:** [#195](https://github.com/HumanBean17/java-codebase-rag/issues/195) (battle-test: agents copy Python-style `neighbors(['<id>'],…)` from hints → `Unknown id prefix for \`['<id>']\``).

**Chosen fix combo (issue table):** **1** (hint templates) + **2** (agent guide + tool descriptions) + **6** (align docs with live graph id shape). **Explicitly not** runtime coercion (issue options 3–5, 9) or structured hints (7) in this effort.

**Amends (when implemented):** locked hint catalogs in `propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` Appendix A and downstream v2/v3/v4 appendices — emission strings only, not trigger logic. **Blocks or lands with:** in-flight [`DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](./DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md) tier-1/2 templates (still drafted with `neighbors(['{id}'],…)`); implementation PR must not merge structural hints on the old shape.

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

## Proposed Solution

### 1 — Hint emission contract (baseline)

Replace pseudo-Python `neighbors(['{id}'], 'out', ['EDGE'])` across the **entire** hint surface with **JSON-shaped** next-step fragments agents can paste into MCP tool calls.

**Canonical single-origin shape (locked):**

```text
<label>: {"ids":"<id>","direction":"<in|out>","edge_types":["<EDGE>"]}
```

Rules:

| Rule | Detail |
|------|--------|
| **`ids` for one origin** | Always a **JSON string** value `"<id>"`, not a one-element array, not Python `['…']`. Matches the simplest working wire shape from #195. |
| **Placeholder** | `{id}` is substituted with the **exact** `record.id` / `origin_id` already in the payload (typically 40-char hex for symbols). |
| **Batch / peer ids** | Hints that today say `client_ids`, `handler_ids`, `member_ids`, etc. must **not** invent a literal placeholder string agents will pass to `ids`. Replace with explicit prose inside the 120-char budget, e.g. `HTTP targets: {"ids":"<each client id>","direction":"out","edge_types":["HTTP_CALLS"]}` or shorten to `next: neighbors on each result.id (out, HTTP_CALLS)` — pick one style in implementation and apply consistently (see Open Questions). |
| **Optional params** | Only when the locked catalog already mentions them (`include_unresolved=True`, `edge_filter={…}`). Use JSON object syntax inside the hint string; no Python `True`/`False`. |
| **Multi-hop teaching** | Where Appendix A used `then neighbors(…)`, either (a) two compact JSON objects separated by ` → `, or (b) one hint that names the first hop only and relies on `edge_summary` for the second — prefer (a) only when both hops still fit ≤120 chars after substitution with a realistic 40-char id; otherwise drop the second hop from v1 emission (road-sign discipline). |
| **Char cap** | Still ≤120 chars **after** substitution with a realistic 40-char symbol id (existing HINTS discipline). Templates that cannot fit are shortened (drop redundant label prefix) or dropped from the catalog with a propose amendment note. |
| **Triggers unchanged** | `generate_hints` conditions, priorities, caps, and dedupe logic stay as-is; only template **strings** and `EDGE_SCHEMA.typical_traversals` values change. |

**Inventory (grep-driven; implementer refreshes counts in PR):**

- `mcp_hints.py` — all `TPL_*` constants that embed `neighbors(` (~30+ templates including describe/find/neighbors success and v2 resolve/find filter shapes).
- `java_ontology.py` — every `typical_traversals` value containing `neighbors(['{id}']` (empty-neighbors structural hints embed these via `typical_traversal_for`).
- `scripts/generate_edge_navigation.py` — if it mirrors ontology traversal strings for `docs/EDGE-NAVIGATION.md`, regenerate in the same PR.
- `tests/test_mcp_hints.py` — locked emission assertions and any `canonical_traversal` expected strings.
- `tests/test_mcp_hints.py` / `tests/test_java_ontology.py` (if present) — char-cap tests per template.

Update module header in `mcp_hints.py` to reference this propose (not only completed HINTS-ROAD-SIGNS).

Extend `MCP_HINTS_FIELD_DESCRIPTION` (and thus all output `hints` field descriptions) with one sentence: hints show **JSON argument objects**; copy **`record.id`** (or `neighbors.other.id`) verbatim — never Python list literals inside `ids`.

### 2 — Documentation and tool descriptions

**`docs/AGENT-GUIDE.md`** (inside `<!-- BEGIN … END -->` markers):

- New subsection under **Argument shapes** (or expand **JSON, not stringified JSON**): **Hints vs MCP calls**.
  - Hints are advisory strings showing JSON-shaped `neighbors` / `find` / `resolve` fragments.
  - **Wrong:** `"ids": "['abc…']"` (Python-style list as string).
  - **Right:** `"ids": "abc…"` or `"ids": ["abc…"]`.
  - Always take ids from the latest tool output (`describe.record.id`, `find.results[].id`, `neighbors.other.id`, `resolve.node.id`).
- Revise **Node ids** table and examples:
  - **Symbol:** primary form is **40-char lowercase hex** (SHA1 of kind|fqn|file|byte).
  - **Route / Client / Producer:** `r:` / `c:` / `p:` or long prefixes `route:` / `client:` / `producer:` + short hash (see graph builder).
  - **`sym:` / FQN-shaped ids:** not stored on Symbol nodes; use `resolve(identifier=<fqn>)` or `describe(fqn=…)` then `describe(id=record.id)`. Do not fabricate `sym:…` for `neighbors`.
- Update workflow table rows and slash-command examples (`/callers`, …) to use hex id placeholders, not `sym:…`.
- Keep existing “JSON, not stringified JSON” table; add `ids` row if missing: wrong = `"['id']"` or `"[\"id\"]"` when a bare string suffices.

**`server.py`:**

- `neighbors` tool `ids` `Field(description=…)` — state accepted shapes (string or list of strings), warn that Python-style quoted lists are rejected, point to `record.id` from prior tools.
- Optional one line in top-level `_INSTRUCTIONS` if agents read it before tool choice.

**`README.md` §4 tool table:**

- Replace `describe` / `neighbors` example ids with realistic hex / `r:` samples from fixture meta or documented placeholders (`<40-char-symbol-id>`).
- Add one-line callout under the table: symbol ids in responses are hex, not `sym:` FQN.

No change to MCP tool **names**, parameters, or response **schemas** beyond description strings.

### 6 — Id-shape alignment (docs-only semantics)

Same PR as §2; no graph or resolver code change.

| Surface | Today | After |
|---------|-------|--------|
| README `describe` example | `sym:com.bank…#method(…)` | `{"id":"<40-char-hex>"}` or `describe(fqn=…)` example |
| README `neighbors` example | `sym:…ChatController` | hex id + composed `edge_types` |
| AGENT-GUIDE Node ids | implies `sym:` is normal for Symbol | hex primary; `sym:` explained as non-stored legacy prefix |
| Hints `{id}` substitution | already uses real `origin_id` from payload | unchanged mechanism; emissions show JSON so copied id is visible |

Confirm with one manual check on `tests/bank-chat-system` index: `describe` on a known controller returns hex `record.id`; document that shape in examples.

## Scope

- Template string migration in `mcp_hints.py`.
- `EDGE_SCHEMA.typical_traversals` string migration in `java_ontology.py`.
- `MCP_HINTS_FIELD_DESCRIPTION` wording.
- `docs/AGENT-GUIDE.md`, `README.md` §4, `server.py` field descriptions / `_INSTRUCTIONS`.
- Tests that lock hint emissions and char caps.
- Regenerated `docs/EDGE-NAVIGATION.md` **only if** the generator script is the source of truth for traversal examples (verify in PR; do not hand-edit if generated).

## Schema / Ontology / Re-index impact

- **Ontology bump:** not required (teaching strings only; no edge semantics or enrichment change).
- **Re-index required:** no.
- **Config / tool surface changes:** none (same tools and parameters; hint **text** and doc examples only).

## Tests / Validation

- `tests/test_mcp_hints.py` — update expected strings for every affected template; keep char-cap and dedupe scenarios.
- Add or extend one test that documents the #195 failure mode as **documentation of intent** (optional): assert new route-via-members template does **not** contain `['{id}']` or `"['"`.
- `tests/test_mcp_v2.py` — only if any test asserts hint substrings (grep in PR).
- Manual (PR evidence): on bank fixture, `describe` → copy hint JSON with returned `record.id` → `neighbors` succeeds; paste `"ids":"['<id>']"` still fails (proves we did not add coercion).
- `.venv/bin/ruff check .` and `.venv/bin/python -m pytest tests -v` (no heavy gate).

## Open Questions ([TBD])

1. **Batch-id hint phrasing** — For `TPL_NEIGHBORS_SUCCESS_*` using `client_ids` / `handler_ids`, which style?
   - **Recommended:** `{"ids":"<id>","direction":"out","edge_types":["HTTP_CALLS"]}` with label text “repeat per client id from results” when multiple rows exist; drop fake `client_ids` token entirely.
2. **Label prefix vs raw JSON** — Keep `routes via members: {…}` or emit only `{…}`?
   - **Recommended:** keep short label prefix for scanability in `hints[]`; raw JSON alone is allowed if char cap forces it.
3. **Two-hop hints that exceed 120 chars** — Split into two hints, shorten to first hop only, or abbreviate keys?
   - **Recommended:** first hop only in hint; second hop remains discoverable via `edge_summary` / AGENT-GUIDE composed-edge table (matches road-sign discipline when cap bites).
4. **DESCRIBE-HINTS-STRUCTURAL landing order** — Land JSON hint migration first, then structural rows, or one PR?
   - **Recommended:** one PR if structural work is not yet on `master`; otherwise JSON migration PR first, structural follow-up amends templates before merge.

## Out of scope

- `_coerce_ids()` / `ast.literal_eval` / teaching-only fail-loud for `['…` strings (issue options 3–5, 9).
- Structured `hints_structured` / Shape 2 (option 7).
- Shorter hints with no embedded call syntax (option 8) — may be a follow-up if JSON hints still confuse models.
- Cursor host rules-only guidance (option 10).
- FastMCP upstream changes.
- Changing `neighbors_v2` id resolution or graph id generation.
- Bumping `ontology_version`.

## Sequencing / Follow-ups

**Single PR** recommended (docs + templates + ontology teaching strings + tests).

1. Lock emission format in `mcp_hints.py` + `java_ontology.py`.
2. Update tests.
3. Patch AGENT-GUIDE, README, server descriptions; regenerate EDGE-NAVIGATION if applicable.
4. Amend `DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md` template table to JSON shape before or in the same implementation PR.

**Follow-up (separate issue/propose if battle-testing still fails):** issue combo **1 + 5** (JSON hints + light coercion for `['single-id']` strings) — only if metrics show agents still stringify Python lists after this lands.

## PR body template (proposal-only)

```markdown
## What
Adds `propose/HINTS-MCP-JSON-IDS-PROPOSE.md` for #195 (combo 1+2+6).

## Why now
Agents copy Python-style hint syntax into MCP `ids` and get `Unknown id prefix`; docs teach misleading `sym:` ids.

## Highlights
- JSON-shaped hint emissions; single-origin `ids` as string
- AGENT-GUIDE + README + server `ids` description alignment
- 40-char hex symbol ids documented; `sym:` examples removed
- No ontology bump / no re-index / no runtime coercion

## Tests
Docs-only PR; baseline unchanged.

## Out of scope
Implementation deferred to follow-up PR(s).
```
