# Postgres + pgvector code search MCP (bundle)

Self-contained **stdio MCP server** for semantic search over code chunks in **PostgreSQL** with the **pgvector** extension. Chunks are produced by CocoIndex [`java_index_flow_postgres.py`](../java_index_flow_postgres.py) (Java / SQL / YAML).

**No `cocoindex` Python package is required to run search or MCP** — only `sentence-transformers`, `psycopg`, `pgvector`, and `mcp`. CocoIndex is optional and only needed if you use the `refresh_code_index` tool.

## Scoring note

Vector hits use **pgvector cosine distance** (`<=>`); the MCP maps scores as `1 - distance`. The LanceDB bundle uses **L2 distance on unit‑normalized embeddings** with a different formula — treat cross‑backend scores as not directly comparable.

## 1. Install

```bash
cd mcp_pgvector_bundle
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Use **Python 3.11+**. The embedding model must match the one used when the index was built (default: `sentence-transformers/all-MiniLM-L6-v2`).

## 2. Environment

| Variable | Purpose |
|----------|---------|
| `PGVECTOR_MCP_DATABASE_URL` | **Required for real use:** PostgreSQL URL (same DB as indexing with `COCOINDEX_DATABASE_URL` is fine). |
| `DATABASE_URL` / `COCOINDEX_DATABASE_URL` | Fallback if `PGVECTOR_MCP_DATABASE_URL` is unset. |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. |
| `PGVECTOR_MCP_SCHEMA` | Schema for tables (default `public`). |
| `PGVECTOR_MCP_TABLE_JAVA` / `_SQL` / `_YAML` | Override default table names if you changed the flow. |
| `PGVECTOR_MCP_PROJECT_ROOT` | Repo root containing `java_index_flow_postgres.py` (for `refresh_code_index`). Defaults to this bundle directory. |
| `PGVECTOR_MCP_ALLOW_REFRESH` | Set to `1` to enable the heavy `refresh_code_index` tool. |

## 3. Indexing (CocoIndex)

See [requirements-indexer.txt](../requirements-indexer.txt) for a minimal indexer venv. From the repo root, with Postgres and pgvector available:

```bash
export COCOINDEX_DATABASE_URL="postgresql://..."
export COCOINDEX_CODE_ROOT="/path/to/your/java/repo"
cocoindex update java_index_flow_postgres.py:java_index_postgres_flow --full-reprocess
```

## 4. Cursor / Claude Code

Copy `mcp.json.example` into your project as `.mcp.json`, replace absolute paths, and merge with existing `mcpServers` if any.

Or:

```bash
claude mcp add --transport stdio pgvector-code -- \
  /path/to/mcp_pgvector_bundle/.venv/bin/python \
  /path/to/mcp_pgvector_bundle/server.py
```

Then set env vars (`PGVECTOR_MCP_DATABASE_URL`, etc.).

## 5. Manual test

```bash
PGVECTOR_MCP_DATABASE_URL="postgresql://..." .venv/bin/python search_postgres.py "rate limit" --table java --limit 2
```

## 6. Syncing from the main repo

If you maintain a parallel Lance bundle, keep `chunk_heuristics.py` in sync with `mcp_lancedb_bundle/chunk_heuristics.py` when behavior changes.
