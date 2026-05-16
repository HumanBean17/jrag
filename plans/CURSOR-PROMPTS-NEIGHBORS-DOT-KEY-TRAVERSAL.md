# Cursor task prompts — NEIGHBORS-DOT-KEY-TRAVERSAL

Status: **active (planning)**. Plan:
[`plans/PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md`](./PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md). Propose:
[`propose/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md`](../propose/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md).

**Depends on:** none.

One prompt: **PR-1** (single implementation PR).

**Universal rules:**

- Use `.venv/bin/python` and `.venv/bin/ruff` only.
- No stdout from MCP handlers.
- Do not expand scope beyond the plan.
- Do not push git unless the user asked.

---

## PR-NEIGHBORS-DOT-1 — `DECLARES.*` dot-key 2-hop traversal

**Branch:** `feat/neighbors-dot-key-traversal` off `master`.
**Base:** `master`.
**Plan section:** [`plans/PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md`](./PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md) § PR-1.
**PR title:** `feat(neighbors): navigate DECLARES.* composed edge types in one call`

**Attach (`@-files`):**

- `@plans/PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md`
- `@propose/NEIGHBORS-DOT-KEY-TRAVERSAL-PROPOSE.md`
- `@kuzu_queries.py` (`member_edge_rollup_for` — mirror for traversal)
- `@mcp_v2.py` (`neighbors_v2`, `EdgeType`, `NodeRecord.edge_summary`, `_TYPE_SYMBOL_KINDS_FOR_EDGE_ROLLUP`)
- `@mcp_hints.py` (`TPL_DESCRIBE_TYPE_*`, `MCP_HINTS_FIELD_DESCRIPTION`, `_filter_neighbors_dotkey_hints`)
- `@server.py` (`neighbors` / `describe` tool descriptions)
- `@docs/AGENT-GUIDE.md`
- `@docs/EDGE-NAVIGATION.md`
- `@README.md` (MCP tool table only)
- `@tests/test_mcp_v2_compose.py`
- `@tests/test_mcp_hints.py`

**Prompt:**

````
You are implementing PR-NEIGHBORS-DOT-1 from `plans/PLAN-NEIGHBORS-DOT-KEY-TRAVERSAL.md`.

## Scope

1. **`kuzu_queries.py`** — Add `member_edge_traversal_for(type_id, composed_key)` sharing the rel map with `member_edge_rollup_for`; return traversal rows with `via_id` and the same edge attr columns flat `neighbors_v2` projects.

2. **`mcp_v2.py`**
   - Add `ComposedEdgeType` Literal (three `DECLARES.*` keys).
   - Extend `_NEIGHBOR_EDGE_TYPES_ADAPTER` to accept composed keys; still reject `OVERRIDDEN_BY` / `OVERRIDDEN_BY.*`.
   - Refactor `neighbors_v2`: partition flat vs composed; enforce `direction="out"` and type Symbol origin for composed; merge results; echo full `requested_edge_types`.
   - Composed `Edge.edge_type` = dot-key; `attrs` includes `via_id`.
   - Update `NodeRecord.edge_summary` description (DECLARES.* navigable; OVERRIDDEN_BY.* not).

3. **`server.py`** — Widen `edge_types` on `neighbors` tool; update `describe` / `neighbors` descriptions.

4. **`mcp_hints.py`** — Rewrite `TPL_DESCRIBE_TYPE_CLIENTS_VIA_MEMBERS`, `TPL_DESCRIBE_TYPE_ROUTES_VIA_MEMBERS`, `TPL_DESCRIBE_TYPE_PRODUCERS_VIA_MEMBERS` to single-call dot-key `neighbors(...)` per propose. Update `MCP_HINTS_FIELD_DESCRIPTION` carve-out. **Do not** remove `_filter_neighbors_dotkey_hints`.

5. **Docs** — `docs/AGENT-GUIDE.md`, `docs/EDGE-NAVIGATION.md`, minimal `README.md` neighbors row.

6. **Tests** — Implement every test name under **Tests for PR-1** in the plan:
   - Split `test_neighbors_rejects_overridden_by_and_dot_keys` → accept `DECLARES.*`, keep `test_neighbors_still_rejects_overridden_by` (or equivalent).
   - Add `test_neighbors_declares_dot_key_{client,producer,exposes}`, `test_neighbors_dot_key_mixed_with_flat`, `test_neighbors_dot_key_inbound_rejected`, `test_neighbors_dot_key_method_origin_rejected`, `test_neighbors_dot_key_count_matches_edge_summary`.
   - Update describe hint tests for new template strings; keep `test_hints_hv20_no_dotkey_edge_labels_in_rendered_neighbors_hints` passing.

## Out of scope (do NOT touch)

- `build_ast_graph.py`, `java_ontology.py` `ONTOLOGY_VERSION`, graph DDL, enrichment passes
- `OVERRIDDEN_BY.*` traversal ([#165](https://github.com/HumanBean17/java-codebase-rag/issues/165))
- Per-method `NodeRef` signals ([#167](https://github.com/HumanBean17/java-codebase-rag/issues/167))
- `propose/HINTS-V4-SUCCESS-PATH-PROPOSE.md` / HINTS-V4 implementation
- Inbound composed `direction="in"`
- Moving propose to `completed/` (reviewer may do on merge)

## Deliverables

1. `neighbors(['<type_id>'], 'out', ['DECLARES.DECLARES_CLIENT'])` returns Client `NodeRef`s with `via_id` in attrs.
2. Count parity with `describe(...).record.edge_summary` for the same dot-key.
3. `OVERRIDDEN_BY*` still fails validation; wrong origin/direction returns `success=False` with clear messages.
4. Docs + hints aligned with “what you see in edge_summary is what you can request” for `DECLARES.*` only.

## Tests to run

```bash
.venv/bin/ruff check kuzu_queries.py mcp_v2.py mcp_hints.py server.py tests/test_mcp_v2_compose.py tests/test_mcp_hints.py
.venv/bin/python -m pytest tests/test_mcp_v2_compose.py tests/test_mcp_hints.py -v -k "dot_key or declares_dot or overridden_by or hints_describe_type or hv20"
```

Before PR open:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest tests -v
```

## Sentinel checks (`git diff master..HEAD` — zero matches)

- `ONTOLOGY_VERSION` assignment changes in `java_ontology.py`
- `ontology_version` bump in `build_ast_graph.py`
- New graph pass files or `pass7` references
- `HINTS-V4` / `TPL_NEIGHBORS_SUCCESS` (v4 not in this PR)
- Edits under `tests/bank-chat-system/` fixture sources

## Definition of done

- All plan PR-1 checklist items checked.
- PR description: scope, link to propose #162, manual evidence (one `describe` + `neighbors` dot-key call on bank fixture), explicit note that HINTS-ROAD-SIGNS decision #11 is partially reversed for three describe templates only.

## Manual evidence (paste in PR)

```bash
tid=$(.venv/bin/python -c "
from kuzu_queries import KuzuGraph
from tests.conftest import BANK_KUZU_PATH  # or query session graph path used in tests
g = KuzuGraph(str(BANK_KUZU_PATH))
rows = g._rows(
    'MATCH (t:Symbol)-[:DECLARES]->(m:Symbol)-[:DECLARES_CLIENT]->() '
    \"WHERE t.kind IN ['class','interface','enum','record','annotation'] \"
    'RETURN t.id AS id LIMIT 1', {})
print(rows[0]['id'] if rows else '')
")
.venv/bin/python -c "
from kuzu_queries import KuzuGraph
from mcp_v2 import describe_v2, neighbors_v2
from tests.conftest import BANK_KUZU_PATH
import os
g = KuzuGraph(os.environ.get('JAVA_CODEBASE_RAG_KUZU', str(BANK_KUZU_PATH)))
tid = '$tid'  # substitute
d = describe_v2(tid, graph=g)
n = neighbors_v2(tid, direction='out', edge_types=['DECLARES.DECLARES_CLIENT'], graph=g, limit=500)
print('summary', d.record.edge_summary.get('DECLARES.DECLARES_CLIENT'))
print('neighbors', len(n.results), n.results[0].edge_type if n.results else None, n.results[0].attrs.get('via_id') if n.results else None)
"
```

(Adjust graph path to match your session fixture if the snippet differs.)

````
