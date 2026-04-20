# LanceDB code search MCP (export bundle)

Self-contained **stdio MCP server** for semantic search over a LanceDB index (Java / SQL / YAML) produced by CocoIndex `java_index_flow_lancedb.py`.

**No `cocoindex` Python package is required to run search or MCP** — only `sentence-transformers`, `lancedb`, and `mcp`. CocoIndex is optional and only needed if you use the `refresh_code_index` tool.

## 1. Install

```bash
cd mcp_lancedb_bundle
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Use **Python 3.11+**. The embedding model must match the one used when the index was built (default: `sentence-transformers/all-MiniLM-L6-v2`).

## 2. Environment

| Variable | Purpose |
|----------|---------|
| `LANCEDB_URI` | **Required for real use:** absolute path to the `lancedb_data` directory (or remote LanceDB URI). |
| `SBERT_MODEL` | Hub id or local directory; must match indexer. |
| `SBERT_DEVICE` | Optional: `cpu`, `cuda`, `mps`. |
| `LANCEDB_MCP_PROJECT_ROOT` | Repo root containing `java_index_flow_lancedb.py` (for `refresh_code_index`). Defaults to this bundle directory. |
| `LANCEDB_MCP_ALLOW_REFRESH` | Set to `1` to enable the heavy `refresh_code_index` tool. |

## 3. Claude Code

**Project scope:** copy `mcp.json.example` to your repo as `.mcp.json`, replace absolute paths, merge with existing `mcpServers` if any.

Or use the CLI:

```bash
claude mcp add --transport stdio lancedb-code -- \
  /path/to/mcp_lancedb_bundle/.venv/bin/python \
  /path/to/mcp_lancedb_bundle/server.py
```

Then set env vars in `.mcp.json` or your shell profile as needed (`LANCEDB_URI`, etc.).

Official docs: [Claude Code settings](https://docs.anthropic.com/en/docs/claude-code/settings) (see MCP / `.mcp.json`).

## 4. Claude Desktop

Edit `claude_desktop_config.json` (e.g. macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`) and add an entry under `mcpServers` with the same `command`, `args`, and `env` as in `mcp.json.example`.

## 5. Manual test

```bash
LANCEDB_URI=/path/to/lancedb_data .venv/bin/python search_lancedb.py "rate limit" --table java --limit 2
```

## 6. Syncing from the main repo

If you develop in `chat-test`, copy these files into `mcp_lancedb_bundle/` when you change behavior:

- `chunk_heuristics.py`
- `search_lancedb.py` (switch imports to `index_common` as in this bundle)
- `server.py` (from `mcp_lancedb_server.py`, with bundle imports)

`index_common.py` stays bundle-specific (no CocoIndex import).
