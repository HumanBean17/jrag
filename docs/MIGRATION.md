# Migration: `java-codebase-rag` → `jrag`

The tool formerly known as **`java-codebase-rag`** is now branded **`jrag`**. This is an **external-surfaces-only** rename: every command, package, and install path keeps working — you do not have to re-index, edit configs, or change env vars.

## TL;DR

- New brand: **`jrag`**. New PyPI package: **`jrag-cli`** (the bare name `jrag` is taken on PyPI by a third party).
- New commands: `jrag` and `jrag-mcp`.
- Old commands (`java-codebase-rag`, `java-codebase-rag-mcp`) and the old package (`java-codebase-rag`) keep working via aliases + a shim.
- On-disk state (`.java-codebase-rag*`) and env vars (`JAVA_CODEBASE_RAG_*`) are **unchanged**.

## Old → new command map

| Old command | New command | Status |
|---|---|---|
| `java-codebase-rag <verb>` | `jrag <verb>` | Old form aliases the new dispatcher (operator + agent verbs unified). |
| `java-codebase-rag-mcp` | `jrag-mcp` | Old form aliases the MCP server entrypoint. |
| `pip install java-codebase-rag` | `pip install jrag-cli` | Old package becomes a shim that pulls in `jrag-cli`. |

**Never run `pip install jrag`** — that name is owned by an unrelated third-party library. Always `pip install jrag-cli`.

## Old → new pip name

| Old | New | Behavior |
|---|---|---|
| `pip install java-codebase-rag` | `pip install jrag-cli` | `java-codebase-rag` remains on PyPI as a **shim** whose single dependency is `jrag-cli` (exact pin, version-locked). `pip install -U java-codebase-rag` transparently upgrades existing users to the new code. |

Both packages bump in lockstep forever; you can stay on either name.

## Untouched (do not migrate)

These on-disk artifacts and env vars are read and written **forever** under their existing names — a `ls -a` will still show `.java-codebase-rag/` on a tool branded `jrag`. This is an accepted cosmetic mismatch; zero functional impact.

- `.java-codebase-rag/` — index directory (Lance tables, LadybugDB graph, cocoindex state).
- `.java-codebase-rag.yml` / `.java-codebase-rag.yaml` — project config.
- `.java-codebase-rag.hosts` — installer marker (configured agent hosts + surface).
- `.java-codebase-rag/ignore` — layered ignore file.
- `JAVA_CODEBASE_RAG_INDEX_DIR`, `JAVA_CODEBASE_RAG_SOURCE_ROOT`, and every other `JAVA_CODEBASE_RAG_*` env var.

The internal Python import module `java_codebase_rag` is also unchanged.

## Deprecation alias behavior

Invoking `java-codebase-rag` or `java-codebase-rag-mcp` emits one line to stderr (TTY only; suppressed under `JRAG_NO_DEPRECATION=1` and in non-interactive contexts):

> `jrag: 'java-codebase-rag' is now 'jrag'; this alias continues to work. Set JRAG_NO_DEPRECATION=1 to silence.`

No removal date is announced; the aliases persist indefinitely. Direct `jrag` / `jrag-mcp` invocations never emit the notice.
