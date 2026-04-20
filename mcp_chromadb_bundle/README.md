# ChromaDB code search MCP (export bundle)

Self-contained **stdio MCP server** for semantic search over a ChromaDB index (Java / SQL / YAML) produced by CocoIndex [`java_index_flow_chroma.py`](../java_index_flow_chroma.py) at the repo root.

**No `cocoindex` Python package is required to run search or MCP** — only `chromadb`, `sentence-transformers`, and `mcp`. CocoIndex is optional and only needed if you use the `refresh_code_index` tool.

## 1. Install

```bash
cd mcp_chromadb_bundle
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Use **Python 3.11+**. The embedding model must match the one used when the index was built (default: `sentence-transformers/all-MiniLM-L6-v2`).

## 2. Indexing (CocoIndex)

From the repo root (with `cocoindex[chromadb]` installed):

```bash
pip install "cocoindex[chromadb]"
cocoindex update java_index_flow_chroma.py:java_index_chroma_flow --full-reprocess
```

Chroma connection options for the **indexer** are controlled by `CHROMA_DB_PATH`, `COCOINDEX_CHROMA_CLIENT`, `COCOINDEX_CHROMA_HOST`, etc. (see the flow file docstring). Defaults write a persistent DB under `./chromadb_data`.

## 3. Environment (MCP / CLI search)

| Variable | Purpose |
|----------|---------|
| `CHROMA_MCP_CLIENT` | `persistent` (default), `http`, or `cloud`. |
| `CHROMA_MCP_PATH` | Directory for `PersistentClient` (default `./chromadb_data`). |
| `CHROMA_MCP_HOST` / `CHROMA_MCP_PORT` / `CHROMA_MCP_SSL` | HTTP client. |
| `CHROMA_MCP_API_KEY` / `CHROMA_MCP_TENANT` / `CHROMA_MCP_DATABASE` | Cloud client (`CHROMA_MCP_CLIENT=cloud`). |
| `CHROMA_MCP_COLLECTION_JAVA` / `_SQL` / `_YAML` | Override default collection names. |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. |
| `CHROMA_MCP_PROJECT_ROOT` | Repo root containing `java_index_flow_chroma.py` (for `refresh_code_index`). Defaults to this bundle directory. |
| `CHROMA_MCP_ALLOW_REFRESH` | Set to `1` to enable the heavy `refresh_code_index` tool. |

## 4. Claude Code

Copy `mcp.json.example` to your repo as `.mcp.json`, replace absolute paths, merge with existing `mcpServers` if any.

Or use the CLI:

```bash
claude mcp add --transport stdio chromadb-code -- \
  /path/to/mcp_chromadb_bundle/.venv/bin/python \
  /path/to/mcp_chromadb_bundle/server.py
```

## 5. Manual test

```bash
CHROMA_MCP_PATH=/path/to/chromadb_data .venv/bin/python search_chroma.py "rate limit" --table java --limit 2
```

## 6. Hybrid mode note

`hybrid=true` runs Chroma **vector** search with an additional **`where_document` `$contains`** filter (substring). That is **not** the same as LanceDB’s RRF hybrid in `mcp_lancedb_bundle`, but it helps for identifier-like queries when combined with `fts_text` or `auto_hybrid`.
