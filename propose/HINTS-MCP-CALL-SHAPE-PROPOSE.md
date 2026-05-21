# HINTS-MCP-CALL-SHAPE — copy-safe hint strings and honest node-id docs

## Status

**Proposal** — not yet implemented.

**Tracks:** [issue #195](https://github.com/HumanBean17/java-codebase-rag/issues/195) (battle-test: agents copy `neighbors(['<id>'],…)` from hints → `Unknown id prefix for \`['<id>']\``).

**Implements fix combo:** **1** (hint templates) + **2** (agent docs + `server.py` field descriptions) + **6** (align id-shape documentation with live graph ids). Does **not** implement runtime coercion (options 3–5), structured hints (7), or host-only rules (10).

**Amends (catalog lock):** [`propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md`](./completed/HINTS-ROAD-SIGNS-PROPOSE.md) Appendix A, **§7.6 rendered-length cap (120 → 500)**, and follow-on hint proposes (v2/v3/v4, CALLS high-fanout strings). In-flight [`propose/DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md`](./DESCRIBE-HINTS-STRUCTURAL-PROPOSE.md) must adopt the same call-shape convention and **500-char** cap for any **new** `TPL_DESCRIBE_*` rows landed after this work (or land structural templates in the same PR as this propose).

## Problem statement

Road-sign hints intentionally embed a **next-call sketch** so agents can chain tools without re-reading the schema. The locked v1 catalog uses **pseudo-Python** call syntax:

```text
routes via members: neighbors(['{id}'],'out',['DECLARES.EXPOSES'])
```

Agents treat that sketch as the literal MCP argument shape. Two failures follow:

| Agent copies | FastMCP `pre_parse_json` | `neighbors_v2` sees | Result |
|--------------|---------------------------|---------------------|--------|
| `"ids": "['<sha1>']"` | `json.loads` fails (single quotes) → string passed through | one origin id = the literal `['<sha1>']` | `Unknown id prefix for \`['<sha1>']\`` |
| `"ids": "[\"<sha1>\"]"` | parses to `list[str]` | correct | works |
| `"ids": "<sha1>"` or `"ids": ["<sha1>"]` | string or list | correct | works |

Root cause is **not** bad graph data or wrong ids from `resolve` / `describe` — `record.id` is already correct. The failure mode is **hint ergonomics** plus **misleading docs** that teach `sym:`-prefixed examples while the indexer emits **bare 40-character SHA1 hex** for `Symbol.id` (and short prefixed ids for route/client/producer — see §6).

Secondary confusion: README and [`docs/AGENT-GUIDE.md`](../docs/AGENT-GUIDE.md) show `sym:com.bank…#method(…)` in tool examples while battle tests and brownfield corpora return unprefixed symbol ids. Agents prefix ids that do not need prefixing, or distrust bare hex as “wrong.”

## Proposed solution

### 1 — Hint template dialect (option 1)

Replace pseudo-Python positional/`['{id}']` forms with an **MCP-copyable named-parameter sketch** aligned with the slash-alias JSON style already in AGENT-GUIDE § “Slash-style aliases”:

```text
routes via members: neighbors(ids="{id}", direction="out", edge_types=["DECLARES.EXPOSES"])
```

**Normative rules for every hint emission that references `neighbors` or `resolve`:**

| Rule | Rationale |
|------|-----------|
| **Single origin** | `ids="{id}"` (string), never `ids=['{id}']` or `ids="['{id}']"`. |
| **Batch / placeholder lists** | `ids=member_ids` or `ids=client_ids` (bare placeholder token), not bracket-wrapped pseudo-lists. |
| **`direction` / `edge_types`** | Always named; `edge_types` uses JSON double-quoted strings inside the sketch. |
| **Two-hop chains** | Keep ` then ` between two `neighbors(…)` sketches when the catalog already uses a chain; each hop obeys the same rules. Prefer **dot-key** shortcuts where they are clearer; with the 500-char cap, re-expanding a two-hop chain is allowed when it improves copy clarity (see §1b). |
| **`edge_filter` / `filter` in meta hints** | Use JSON object literals with double quotes, e.g. `edge_filter={"callee_declaring_role":"SERVICE"}`, not Python `{{…}}` / single-quoted dicts. |
| **`resolve` / `find` / `search` sketches** | Same convention: `resolve(identifier="{identifier}", hint_kind="{kind}")`, `search(query="{q}")` — no Python-only quoting. |
| **Rendered length** | See §1b (500 chars, not 120). |

Update `MCP_HINTS_FIELD_DESCRIPTION` in `mcp_hints.py` to state explicitly: *hints use MCP-copyable named-parameter sketches; copy `record.id` (or `results[i].id`) into `ids`, not Python list literals; each hint is at most 500 characters after placeholder substitution.*

### 1b — Hint length cap: 120 → 500

Road-sign discipline keeps **≤ 5 hints per output** and **no prose tutorials** (HINTS-ROAD-SIGNS §2–§3). The **per-hint rendered length** limit moves from **120** to **500** characters (measured on the string **after** `{id}`, `{identifier}`, and other placeholders are substituted with realistic values — same rule as v1 §7.6, new numeric cap).

**Why raise it now**

- MCP-copyable named-parameter sketches are longer than pseudo-Python `neighbors(['{id}'],…)`.
- CALLS meta hints (`TPL_NEIGHBORS_CALLS_HIGH_FANOUT`, role-collision, unresolved) were written against the 120 cap with compressed grammar; 500 allows full `edge_filter={…}` examples without drop-on-overflow.
- HINTS-V4 **rejected** a combined N1 dot-key line at ~194 chars (v4 propose); that row fits under 500 if we choose to emit it (optional, not required for #195).

**Implementation**

| Item | Change |
|------|--------|
| `mcp_hints.py` | Replace `_FIND_SUCCESS_MAX_CHARS`, `_RESOLVE_HINT_MAX_CHARS`, `_NEIGHBORS_SUCCESS_MAX_CHARS` (all `120`) with one module constant **`HINT_MAX_RENDERED_CHARS = 500`** used by every drop-on-overflow check. |
| `MCP_HINTS_FIELD_DESCRIPTION` | Mention **500** chars per hint (still max **5** hints per output). |
| `tests/test_mcp_hints.py` | Rename/update `test_hints_*_under_120_chars` → **`test_hints_*_under_500_chars`**; assert `len(rendered) <= 500`. Remove or rewrite tests that **require** overflow past the cap (e.g. N1a rendered length `> 120` used only to justify split hints). |
| Completed proposes | Not edited on disk; this file is the amendment for §7.6. |

**Drop-on-overflow unchanged:** if substitution still exceeds 500 chars, the hint is **not** emitted (no truncation, no ellipsis) — same policy as v2 §7.18.

**Files — template source of truth**

| File | Change |
|------|--------|
| `mcp_hints.py` | All `TPL_*` constants with `neighbors(['{id}']` or positional `neighbors(…)`; v4 success templates (`client_ids`, `route_ids`, …); CALLS meta hints with `edge_filter=`; **`HINT_MAX_RENDERED_CHARS = 500`**. |
| `java_ontology.py` | `EDGE_SCHEMA[*].typical_traversals` strings (feed empty-`neighbors` structural hints via `canonical_traversal`). |
| `scripts/generate_edge_navigation.py` + regenerate `docs/EDGE-NAVIGATION.md` | Keep generated doc aligned with `EDGE_SCHEMA` (same PR). |

**Inventory (grep-driven, implementation checklist)**

- `mcp_hints.py`: 16+ template lines with `neighbors(['{id}']` (describe/find/v4 success) plus v4 placeholder-id rows and CALLS filter hints.
- `java_ontology.py`: `_SYMBOL_TYPE_TRAVERSAL`, `_COMPOSED_MEMBER_TYPE_TRAVERSAL`, and per-edge `member_subject` / `type_subject` strings (19 occurrences of `['{id}']` pattern).
- Tests asserting verbatim template text: `tests/test_mcp_hints.py` (primary), any integration tests that golden-match hint substrings.

### 2 — Documentation and schema descriptions (option 2)

Add a short, normative **“Copying hints into tool calls”** subsection to [`docs/AGENT-GUIDE.md`](../docs/AGENT-GUIDE.md) (after “JSON, not stringified JSON” or inside the `neighbors` section):

- Hints are **advisory**; authoritative id is always `describe.record.id`, `find.results[i].id`, `neighbors.results[i].other.id`, or `resolve.candidates[i].id`.
- For `neighbors`, pass that id as **`ids` string** for one origin, or **`ids` JSON array** for batch — never a string that looks like a Python list (`"['…']"`).
- FastMCP may accept a **JSON-encoded array string** (`"[\"id\"]"`); that is host behavior, not something hints should require agents to manufacture.
- Point to the new hint dialect (named parameters in hint text).

Tighten tool-facing prose:

| Location | Change |
|----------|--------|
| `server.py` → `neighbors` → `ids` `Field(description=…)` | State copy-safe shapes; warn against Python-list string for `ids`. |
| `server.py` → `describe` → `id` (if present) | One line: ids are graph-native (hex or prefixed route/client/producer). |
| `MCP_HINTS_FIELD_DESCRIPTION` | As in §1. |

No change to MCP tool **signatures** or Pydantic models beyond `description=` strings.

### 6 — Honest node-id documentation (option 6)

Correct the public docs so examples match what the graph and tools return.

| Kind | Live shape (indexer / Kuzu) | Docs today (misleading) |
|------|----------------------------|-------------------------|
| **Symbol** | 40-char lowercase SHA1 hex (`graph_enrich.symbol_id`) | `sym:` + FQN in README / AGENT-GUIDE tables |
| **Route** | `r:` + 16 hex | `route:` or `r:` (partially OK) |
| **Client** | `c:` + 16 hex | `client:` or `c:` (partially OK) |
| **Producer** | `p:` + 16 hex | `producer:` or `p:` (partially OK) |
| **UnresolvedCallSite** | `ucs:` + … | Documented; unchanged |

**Doc edits**

- [`docs/AGENT-GUIDE.md`](../docs/AGENT-GUIDE.md) § “Node ids”: Symbol row = **bare SHA1 hex**; note `sym:` is accepted on input via prefix convention but **not** what `describe` / `find` / `neighbors` emit for symbols.
- [`README.md`](../README.md) MCP tool examples: replace `sym:…ChatController#…` symbol examples with a **placeholder** bare hex (or “40-char hex from `describe.record.id`”) while keeping prefixed examples for route/client/producer where accurate.
- Optional one-line in `server.py` `describe` / `neighbors` descriptions: “Symbol ids are typically 40-char hex; route/client/producer use `r:`/`c:`/`p:` prefixes.”

**Runtime:** No change to `_resolve_node_kind` — it already accepts bare hex and optional prefixes. This slice is documentation + hints only.

## Scope

| In scope | Out of scope (issue #195 options) |
|----------|-----------------------------------|
| Rewrite hint + `EDGE_SCHEMA` traversal strings to MCP-copyable form | `_coerce_ids()` / Python-bracket unwrap (3, 5) |
| **500-char** rendered hint cap (replaces 120) + unified `HINT_MAX_RENDERED_CHARS` | Raising **5 hints/output** cap |
| AGENT-GUIDE + README id-shape + hint-copy section | Fail-loud-only teaching error (4) without coercion |
| `server.py` `Field(description=…)` updates | `hints_structured` (7) |
| Regenerate `docs/EDGE-NAVIGATION.md` | Shorter hints-only labels (8) |
| `tests/test_mcp_hints.py` + sentinel regression test | FastMCP `ast.literal_eval` fallback (9) |
| Cross-reference in-flight DESCRIBE-HINTS-STRUCTURAL | Cursor host rules only (10) |

## Schema / Ontology / Re-index impact

- **Ontology bump:** not required.
- **Re-index required:** no (string-only hint and doc changes).
- **Config / tool surface:** no new tools or env vars; optional `description=` text on existing fields only.

## Tests / validation

1. **`.venv/bin/ruff check .`**
2. **`.venv/bin/python -m pytest tests/test_mcp_hints.py -v`** — existing template equality tests follow `mcp_hints.TPL_*` constants; update expectations when constants change.
3. **`test_hint_templates_never_use_python_list_ids`** — assert no rendered catalog template contains `(['` or `"['"` (after format with a realistic 40-char hex id).
4. **`test_hint_source_no_python_bracket_ids`** — source-level sentinel: no `neighbors(['` in `mcp_hints.py` or `java_ontology.py` (decision §5).
5. **Char-cap sweep:** `test_hints_template_rendered_length_leq_500` (and v4 parametrized sibling) — every catalog template renders to **≤ 500** chars with a realistic 40-char hex `{id}` (and realistic long FQN for resolve/search templates).
6. **Manual smoke (PR evidence):**
   - `describe` on a controller from `tests/bank-chat-system` → copy `record.id` into `neighbors(ids="<id>", direction="out", edge_types=["DECLARES.EXPOSES"])` via MCP host or `mcp_v2.neighbors_v2` with `list[str]` / bare string — success.
   - Confirm copying the **old** hint shape `"ids": "['<id>']"` still fails (documents why we changed hints; optional post-implementation note in PR).

Heavy / graph rebuild tests not required.

## Decisions (resolved)

All former open questions — **chosen** 2026-05-21 (align with issue #195 recommendations).

| # | Question | **Chosen** |
|---|----------|------------|
| 1 | Hint sketch vs strict JSON object | **Named-parameter sketch** in hint text (`neighbors(ids="…", direction="out", edge_types=[…])`). AGENT-GUIDE slash-alias table keeps JSON objects for hosts that prefer a single blob. |
| 2 | Single PR vs docs-first | **One implementation PR** — templates, docs, and tests land together (no docs-only lead). |
| 3 | DESCRIBE-HINTS-STRUCTURAL sequencing | Whichever lands first wins; the other rebases within the same week onto §1 rules + 500-char cap. **Never** merge new `TPL_DESCRIBE_*` using `['{id}']` syntax. |
| 4 | Mechanical CI lint | **Yes** — `test_hint_source_no_python_bracket_ids` (no `neighbors(['` in `mcp_hints.py` / `java_ontology.py`) plus rendered-output sentinels. |
| 5 | Re-merge HINTS-V4 N1a+N1b | **Out of #195 MVP** — keep N1a + N1b separate; combined dot-key line remains optional follow-up only. |

## Out of scope

- Runtime acceptance of Python-literal `ids` strings in `mcp_v2.neighbors_v2` (options 3–5) — follow-up propose if battle tests still show copy errors after 1+2+6.
- Changing FastMCP or MCP host JSON pre-parse behavior.
- `hints_structured: [{tool, args}]` (issue option 7).
- Bumping `ontology_version` or graph builder id assignment.
- Editing completed propose files in `propose/completed/` (this file is the amendment record).

## Sequencing / Follow-ups

| Step | Deliverable |
|------|-------------|
| **Implementation PR (one)** | Templates + `EDGE_SCHEMA` traversals + EDGE-NAVIGATION regen + AGENT-GUIDE/README/server descriptions + tests (decision §2) |
| **Follow-up (optional)** | Coercion / fail-loud hybrid (issue #195 options 4–5) if telemetry still shows `['…` id strings |

**Suggested branch:** `cursor/hints-mcp-call-shape-195` or `feat/hints-mcp-call-shape-195`.

## PR body template (implementation PR)

```markdown
## Summary
- Fixes #195 (combo 1+2+6): MCP-copyable hint sketches; docs state bare SHA1 symbol ids.
- Implements `propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md`.

## Highlights
- Hint templates use `ids="{id}"`, named `direction` / `edge_types` (no `['{id}']`).
- Per-hint rendered cap **500** chars (`HINT_MAX_RENDERED_CHARS`; was 120).
- AGENT-GUIDE + README + server Field descriptions aligned with live id shapes.
- EDGE_SCHEMA / EDGE-NAVIGATION traversal strings updated to match.

## Test plan
- [ ] pytest tests/test_mcp_hints.py
- [ ] new sentinel: no `(['` in hint sources / rendered templates
- [ ] manual neighbors call with id from describe (bank-chat fixture)

## Re-index
Not required.
```
