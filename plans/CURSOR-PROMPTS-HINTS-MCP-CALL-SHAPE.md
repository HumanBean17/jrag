# Cursor task prompts — HINTS-MCP-CALL-SHAPE

Status: **active**. Plan:
[`plans/PLAN-HINTS-MCP-CALL-SHAPE.md`](./PLAN-HINTS-MCP-CALL-SHAPE.md). Propose:
[`propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md`](../propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md).

**Tracks:** [#195](https://github.com/HumanBean17/java-codebase-rag/issues/195).

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-HINTS-MCP-CALL-SHAPE-1 — copy-safe hints + honest id docs

**Branch:** `plan/hints-mcp-call-shape` off `master`.  
**Base:** `master`.  
**Plan section:** [`plans/PLAN-HINTS-MCP-CALL-SHAPE.md`](./PLAN-HINTS-MCP-CALL-SHAPE.md) § PR-1.  
**PR title:** `fix(hints): MCP-copyable call sketches and honest symbol id docs (#195)`

**Attach (`@-files`):**

- `@plans/PLAN-HINTS-MCP-CALL-SHAPE.md`
- `@propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md`
- `@propose/completed/HINTS-ROAD-SIGNS-PROPOSE.md` (§7.6 cap amendment context)
- `@mcp_hints.py`
- `@java_ontology.py`
- `@scripts/generate_edge_navigation.py`
- `@docs/AGENT-GUIDE.md`
- `@README.md`
- `@server.py`
- `@tests/test_mcp_hints.py`

**Prompt:**

````
You are implementing PR-HINTS-MCP-CALL-SHAPE-1 from `plans/PLAN-HINTS-MCP-CALL-SHAPE.md`.
Implements `propose/HINTS-MCP-CALL-SHAPE-PROPOSE.md` (issue #195 combo 1+2+6).

## Scope

1. **`mcp_hints.py`**
   - Add `HINT_MAX_RENDERED_CHARS = 250`; remove `_FIND_SUCCESS_MAX_CHARS`, `_RESOLVE_HINT_MAX_CHARS`, `_NEIGHBORS_SUCCESS_MAX_CHARS`.
   - Rewrite every `TPL_*` tool-call sketch to named-parameter MCP dialect:
     - Single origin: `ids="{id}"` (never `ids=['{id}']` or `neighbors(['{id}'],…)`).
     - Batch: `ids=client_ids`, `ids=member_ids`, etc.
     - `direction="out"`, `edge_types=["DECLARES.EXPOSES"]` (JSON double quotes).
     - `edge_filter={"callee_declaring_role":"SERVICE"}` not Python dict syntax.
     - `resolve(identifier="…", hint_kind="…")`, `search(query="…")`, `find(kind="route", filter={"path_prefix": "…"})`.
   - Update `MCP_HINTS_FIELD_DESCRIPTION` (250 chars, copy-safe `ids`, named-parameter sketches).
   - **Do not** change hint triggers, priorities, or cap-of-5 logic.

2. **`java_ontology.py`**
   - Rewrite all `typical_traversals` / `_SYMBOL_TYPE_TRAVERSAL` / `_COMPOSED_MEMBER_TYPE_TRAVERSAL` strings to the same dialect.
   - Zero `neighbors(['` after edit.

3. **`scripts/generate_edge_navigation.py`** + regenerate **`docs/EDGE-NAVIGATION.md`**
   - Update generator hardcoded examples; run:
     `.venv/bin/python scripts/generate_edge_navigation.py`
   - Commit regenerated doc.

4. **`docs/AGENT-GUIDE.md`**
   - Symbol ids: bare 40-char hex (emitted); `sym:` input-only.
   - New subsection **Copying hints into tool calls** (advisory hints; copy from tool outputs; never `"['…']"` for `ids`).
   - Fix examples that teach `sym:` as the emitted symbol shape.

5. **`README.md`**
   - MCP table examples: symbol ids as bare hex or “from describe.record.id”, not `sym:…` emit examples.

6. **`server.py`**
   - `neighbors` → `ids` and `describe` → `id` `Field(description=…)` only (no signature changes): copy-safe `ids` shapes; warn against Python-list string; symbol = bare hex.

7. **`tests/test_mcp_hints.py`**
   - Add: `test_hint_templates_never_use_python_list_ids`, `test_hint_source_no_python_bracket_ids`.
   - Rename: `test_hints_all_v4_templates_under_250_chars`, `test_hints_template_rendered_length_leq_250`.
   - Update all golden substring asserts for new template text.
   - Rewrite `test_hints_neighbors_n1a_n1b_dropped_when_rendered_exceeds_char_cap` for 250-cap (not 120 split rationale).

## Out of scope (do NOT touch)

- `mcp_v2.py` (no `_coerce_ids`, no neighbors logic changes).
- `build_ast_graph.py`, `graph_enrich.py`, `ONTOLOGY_VERSION`, indexer.
- `hints_structured`, FastMCP, MCP host behavior.
- `propose/completed/*.md` (amendment stays in active propose).
- Combined N1a+N1b single hint.
- Bumping 5-hints-per-output cap.
- `JAVA_CODEBASE_RAG_RUN_HEAVY` tests.
- Drive-by lint in unrelated files.

## Sentinel (must be zero on `git diff master..HEAD`)

```bash
rg "neighbors\(\['" mcp_hints.py java_ontology.py
```

## Deliverables

1. MCP-copyable hint templates + unified 250-char cap.
2. `EDGE_SCHEMA` + regenerated `EDGE-NAVIGATION.md` aligned.
3. AGENT-GUIDE + README + server descriptions honest about symbol ids.
4. New sentinel tests + renamed cap tests + updated integration asserts.

## Tests (run exactly)

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests/test_mcp_hints.py -v
```

## Manual evidence (PR body)

From repo root, after graph fixture exists in session tests or:

```bash
rm -rf /tmp/hints-smoke && .venv/bin/python build_ast_graph.py \
  --source-root tests/bank-chat-system \
  --kuzu-path /tmp/hints-smoke/code_graph.kuzu
```

Pick a controller symbol id via describe (or test helper), then call:

`neighbors_v2(<id>, direction="out", edge_types=["DECLARES.EXPOSES"], graph=…)`

with `ids` as a **bare string** — must succeed. Note in PR that copying `"ids": "['<id>']"` still fails (optional).

## Definition of done

- [ ] Sentinel `rg` clean on `mcp_hints.py` and `java_ontology.py`
- [ ] `test_hint_templates_never_use_python_list_ids` and `test_hint_source_no_python_bracket_ids` pass
- [ ] Char-cap tests use `_250` naming and realistic 40-char hex ids
- [ ] `pytest tests/test_mcp_hints.py -v` green
- [ ] PR references propose + fixes #195; states **no re-index**
````
