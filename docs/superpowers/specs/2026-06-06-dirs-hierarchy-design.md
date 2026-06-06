# Directory Hierarchy: Config Discovery and Source Root Resolution

**Date:** 2026-06-06
**Status:** Draft

## Problem

Users organize Java projects in varied directory structures. The tool currently requires running from the exact directory containing `.java-codebase-rag.yml`, which breaks common workflows:

1. **User A** runs `init` from a parent directory containing multiple unrelated systems — gets a single mixed index
2. **User B** runs `init` correctly from `System-C/` but then uses MCP from `System-C/microservice-C-1/` — config not found
3. **User C** has config in `system-D-context/` with code at `../` via `--source-root` — config/source-root mismatch causes issues

The tool couples three things: config file location, source code location, and cwd. All three must currently be the same directory.

## Solution

Two changes that decouple these concerns:

1. **Walk-up config discovery** — the tool walks up from cwd to find `.java-codebase-rag.yml` (like git)
2. **`source_root` field in config** — config can point to source code in a different directory

## Design

### 1. Walk-up config discovery

New function `discover_project_root(start: Path) -> Path | None`:

- Starts from `start` directory
- Checks for `.java-codebase-rag.yml` or `.java-codebase-rag.yaml` in current directory
- If not found, moves to parent and repeats
- **Boundary conditions:** stops before reaching `$HOME` (does not check `$HOME` itself), stops at filesystem root
- Returns the directory containing the config file, or `None`

**Integration point:** Both CLI and MCP server call this function before config resolution. The returned path feeds into the existing `source_root` parameter of `resolve_operator_config()`.

**Precedence chain for source root:**
1. `--source-root` CLI flag
2. `JAVA_CODEBASE_RAG_SOURCE_ROOT` environment variable
3. Walk-up discovery (config file's parent directory)
4. `Path.cwd()` (current behavior, unchanged fallback)

When walk-up discovery finds a config and no explicit source root is set, the config's directory becomes both the project root and the default source root (unless `source_root` is set in the YAML — see below).

### 2. `source_root` field in config YAML

Add optional `source_root` field to `.java-codebase-rag.yml`:

```yaml
# Optional: override where Java source code lives.
# Relative paths resolve relative to the config file's directory.
source_root: ../
```

This field slots into the existing precedence chain for source root resolution, between the env var and the default:

**Full precedence for source root (after walk-up discovers the config):**
1. `--source-root` CLI flag
2. `JAVA_CODEBASE_RAG_SOURCE_ROOT` environment variable
3. `source_root` field in YAML config (new)
4. Config file's parent directory (from walk-up discovery)

**Index directory** always resolves relative to the final source root: `<source-root>/.java-codebase-rag/`. This is unchanged — the index lives with the code, not with the config.

### 3. Where changes happen

**`config.py`** — Add `discover_project_root()`. Update `resolve_operator_config()` to read `source_root` from YAML and resolve it relative to the config file's directory. The `source_root` parameter semantics change: when `None`, the function first discovers the project root via walk-up, then reads `source_root` from the discovered config, and only falls back to cwd if no config is found anywhere.

**`server.py`** — Update `_project_root()` to call `discover_project_root()` before falling back to cwd. Env var takes precedence over walk-up.

**`cli.py`** — Update `_resolved_from_ns()` to call `discover_project_root()` when `--source-root` is not provided. CLI flag takes precedence over walk-up.

**No changes to `init` command behavior.** The `init` command creates config + index in the specified directory as before. The walk-up only helps find existing configs.

### 4. Error messages

**No config found (MCP server / query / index commands):**

> No `.java-codebase-rag.yml` found in `[cwd]` or any parent directory (stopped at home). Run `java-codebase-rag init` in your project root first.

**`init` finds existing config in parent:**

> Found existing config at `[parent]/.java-codebase-rag.yml`. Creating a new project here will create a separate index. Continue? [y/N]

### 5. What each user scenario looks like after

**User A** — runs `init` from each `System-X/` directory. Then uses MCP from any subdirectory — walk-up finds the config for the current system.

**User B** — runs `init` from `System-C/`. Then `cd`s to `microservice-C-1/` and starts MCP. Walk-up finds `System-C/.java-codebase-rag.yml`, source root = `System-C/`. Works.

**User C** — creates config at `system-D-context/.java-codebase-rag.yml` with `source_root: ../`. Runs `init` from `system-D-context/`. Walk-up from any subdirectory of `System-D/` or `system-D-context/` finds the config. Source root = `System-D/`.

## Scope

- Config discovery and source root resolution only
- No changes to indexing logic, query logic, or graph building
- No changes to `init` command beyond the parent-config warning
- No multi-project or multi-system support

## Out of scope

- Auto-detecting multiple systems and splitting indexes
- Changing index directory structure
- Global config or project registry
