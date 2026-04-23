# SQLite code search MCP (export bundle)

Self-contained **stdio MCP server** for semantic search over a **SQLite + sqlite-vec** index (Java / Flyway SQL / YAML) produced by CocoIndex `java_index_flow_sqlite.py`.

**No `cocoindex` Python package is required to run search or MCP** — only `sentence-transformers`, `sqlite-vec`, `numpy`, `mcp`, and `pydantic`. CocoIndex is optional and only needed if you use the `refresh_code_index` tool.

`requirements.txt` installs **`sqlean.py`**, which replaces the stdlib `sqlite3` when extension loading is unavailable (typical on macOS). If you remove it, your Python must support SQLite **extension loading** (`enable_load_extension` / `load_extension`) for sqlite-vec to work.

## 1. Install

```bash
cd mcp_sqlite_bundle
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Use **Python 3.11+**. The embedding model must match the one used when the index was built (default: `sentence-transformers/all-MiniLM-L6-v2`).

## 2. Build the index (CocoIndex)

Indexer environment (separate venv is fine):

```bash
pip install -r requirements.txt
# or: pip install "cocoindex[sentence-transformers,sqlite]" sqlean.py
export SQLITE_CODE_INDEX_DB=/abs/path/java_code_index.sqlite
export COCOINDEX_DB=/abs/path/cocoindex_java_sqlite.db   # optional CocoIndex state
# from repo root (or adjust path if the flow lives next to you):
cocoindex update mcp_sqlite_bundle/java_index_flow_sqlite.py:JavaCodeIndexSqlite --full-reprocess -f
# from mcp_sqlite_bundle/ as cwd:
# cocoindex update java_index_flow_sqlite.py:JavaCodeIndexSqlite --full-reprocess -f
```

Run from the directory that contains your Java tree (default `PROJECT_ROOT` is cwd). Same table names as other bundles: `javacodeindex_java_code`, `sqlschemaindex_sql_schema`, `yamlconfigindex_yaml_config`.

## 3. Environment (MCP / search)

| Variable | Purpose |
|----------|---------|
| `SQLITE_CODE_INDEX_DB` | **Required for real use:** path to the SQLite file built by the flow. |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. |
| `SQLITE_MCP_PROJECT_ROOT` | Repo root containing `java_index_flow_sqlite.py` (for `refresh_code_index`). Defaults to this bundle directory. |
| `SQLITE_MCP_ALLOW_REFRESH` | Set to `1` to enable the heavy `refresh_code_index` tool. |

## 4. Claude Code / Cursor

Copy `mcp.json.example` to your project as `.mcp.json`, replace absolute paths, merge with existing `mcpServers` if any.

Or CLI:

```bash
claude mcp add --transport stdio sqlite-code -- \
  /path/to/mcp_sqlite_bundle/.venv/bin/python \
  /path/to/mcp_sqlite_bundle/server.py
```

## 5. Manual test

```bash
SQLITE_CODE_INDEX_DB=/path/to/java_code_index.sqlite .venv/bin/python search_sqlite.py "rate limit" --table java --limit 2
```

## 6. Limitations

- **Vector search only** — `hybrid` / `auto_hybrid` / `fts_text` are not supported (call returns a clear error), unlike the LanceDB bundle.

## 7. Syncing from the main repo

When changing behavior, keep in sync with other MCP bundles:

- `chunk_heuristics.py`
- `search_sqlite.py` (parallel to `search_lancedb.py`)
- `server.py`
- `index_common.py` stays bundle-specific (no CocoIndex import).
