# java-enterprise-codebase-rag

RAG helpers for a Java-heavy enterprise codebase.

## MCP bundles

| Bundle | Backend | Purpose |
|--------|---------|---------|
| [`mcp_lancedb_bundle/`](mcp_lancedb_bundle/) | LanceDB files | Semantic `codebase_search` + optional `refresh_code_index` (CocoIndex flow must be supplied) |
| [`mcp_pgvector_bundle/`](mcp_pgvector_bundle/) | PostgreSQL + pgvector | Same tool surface; connects with `PGVECTOR_MCP_DATABASE_URL` |
| [`mcp_chromadb_bundle/`](mcp_chromadb_bundle/) | ChromaDB (persistent / HTTP / cloud) | Same tool surface; see bundle README for `CHROMA_MCP_*` |

Indexer-only dependencies (not required for search MCPs) are listed in [`requirements-indexer.txt`](requirements-indexer.txt).

## Postgres indexing flow

[`java_index_flow_postgres.py`](java_index_flow_postgres.py) exports Java, SQL, and YAML chunks into three tables. Use `COCOINDEX_DATABASE_URL` and run:

`cocoindex update java_index_flow_postgres.py:java_index_postgres_flow --full-reprocess`

See [`mcp_pgvector_bundle/README.md`](mcp_pgvector_bundle/README.md) for environment variables and scoring notes.

## ChromaDB indexing flow

[`java_index_flow_chroma.py`](java_index_flow_chroma.py) exports the same logical chunks into three Chroma collections (install with `pip install "cocoindex[chromadb]"`). Run:

`cocoindex update java_index_flow_chroma.py:java_index_chroma_flow --full-reprocess`

See [`mcp_chromadb_bundle/README.md`](mcp_chromadb_bundle/README.md) for MCP env vars and hybrid-search behavior.
