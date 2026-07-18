# java-codebase-rag (renamed to `jrag-cli`)

This package has been **renamed to [`jrag-cli`](https://github.com/HumanBean17/jrag)**.

`java-codebase-rag` remains on PyPI as a thin compatibility shim that depends
on `jrag-cli` and ships no code of its own. Upgrading an existing install
pulls `jrag-cli` in transparently:

```bash
pip install -U java-codebase-rag   # installs jrag-cli behind the scenes
```

For new setups, install the canonical package directly:

```bash
pip install jrag-cli
```

The CLI entry points, MCP server, and module layout are unchanged — `jrag`
and `jrag-mcp` (plus the legacy `java-codebase-rag` / `java-codebase-rag-mcp`
aliases) are all provided by `jrag-cli`. See
[HumanBean17/jrag](https://github.com/HumanBean17/jrag) for documentation.
