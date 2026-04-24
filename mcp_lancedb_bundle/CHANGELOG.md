# Changelog

## Unreleased

### MCP behavior

- **`codebase_search`:** Response includes `hybrid_attempted` and `hybrid_used` (single-table). When vector+FTS is requested but only vector results are returned, `message` is prefixed with `HYBRID_FALLBACK:`.
- **`codebase_vector_graph`:** New parameters `max_vector_text_chars` and `snippet_max_bytes` (default 2000 each); default `graph_limit` lowered to **28** for smaller merge payloads.

### Breaking (MCP tool names)

- `codebase_hybrid_rag` → **`codebase_vector_graph`** (vector + Kuzu structural graph + RRF; not vector+FTS). `rag` was dropped from the tool name in a follow-up rename.
- `graph_expand_neighbors` → **`graph_expand_from_type_seed`** (manual type-name substring seed; no vector step).

Update any `.mcp.json`, agent prompts, or scripts that referenced the old names. `codebase_search` is unchanged; its `hybrid` / `auto_hybrid` parameters still mean **vector + FTS**, documented on the tool and in server instructions.
