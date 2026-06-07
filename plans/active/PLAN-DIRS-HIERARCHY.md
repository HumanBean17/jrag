# Plan: Walk-up config discovery, configurable source root, and microservice auto-scope

Status: **active (planning)**. This plan implements
[`propose/active/DIRS-HIERARCHY-PROPOSE.md`](../../propose/active/DIRS-HIERARCHY-PROPOSE.md)
as a single PR.

Depends on: none.

## Goal

- Users can run CLI and MCP commands from any subdirectory within their project — the tool walks up to find `.java-codebase-rag.yml`, like git finds `.git`.
- The YAML config gains an optional `source_root` field so the config can live separately from the Java source code, and the index dir auto-derives from the resolved source root.
- When working from a microservice subdirectory, queries automatically scope to that microservice — no manual filter needed. Agents see correct codebase boundaries.
- Existing workflows where cwd = config dir produce identical behavior. No breaking changes.

## Principles (do not relitigate in review)

- **First match wins.** Closest config to cwd, not "most specific" or "deepest". Matches git behavior.
- **`$HOME` is the inclusive boundary.** Check `$HOME` itself, do not walk past it.
- **Walk-up is always-on** when no explicit source root is given (CLI flag or env var). No `--walk-up` opt-out flag.
- **YAML `source_root` resolves relative to the config file directory.** CLI `--source-root` resolves relative to cwd. Different resolution bases are intentional — the precedence table handles priority.
- **Index dir follows source root.** Default index dir = `<source_root>/.java-codebase-rag/`. This does not change; walk-up just changes how source root itself is found.
- **No changes to `init` behavior** beyond a soft warning when a parent config exists.
- **No changes to indexing, query, or graph-building logic.** This is config discovery only.
- **Microservice scope matches working context.** Auto-scope queries to the detected microservice when inside a microservice directory. Explicit filters always override auto-detected scope.

## PR breakdown - overview

| PR | Scope | Ontology bump | Areas of concern | Test buckets | Independent of |
| --- | --- | --- | --- | --- | --- |
| PR-1 | Walk-up config discovery + `source_root` YAML field | none | Precedence chain correctness; server/CLI parity; boundary conditions ($HOME, root) | unit tests for discovery + precedence + integration | — |

Landing order: **PR-1** (single PR).

## Resolved design decisions

| Topic | Decision |
| --- | --- |
| Config file name checked | `.java-codebase-rag.yml` and `.java-codebase-rag.yaml` (existing `YAML_CONFIG_FILENAMES` tuple) |
| Boundary for walk-up | `$HOME` inclusive — check `$HOME` itself but do not walk past it |
| YAML field name | `source_root` — same name as CLI flag for conceptual consistency |
| Resolution base for YAML `source_root` | Config file's parent directory (not cwd) |
| `init` behavior | Unchanged — creates config + index in the specified directory. Only adds a soft warning if a parent config is detected |
| Multiple nested configs | First match wins (closest to cwd). Mirrors git behavior |
| New function location | `config.py` — all config resolution logic lives there already |
| `discover_project_root` return | `Path | None` — returns the directory containing the config file, not the config file path itself |
| Microservice detection approach | Reuse indexing logic — use `microservice_for_path()` from `graph_enrich.py` for consistency |
| Microservice scope behavior | Scoped inside, all at root — auto-scope inside microservice, all microservices at root with advisory |
| Microservice auto-scope implementation | Server-level scope injection — detect at startup, inject into queries, explicit filters override |
| Microservice detection location | `graph_enrich.py` — new `detect_microservice_from_path()` function |
| Scope manager location | `server.py` — new `ScopeManager` class for caching and applying auto-scope |

---

# PR-1 — Walk-up config discovery and configurable source root

## File-by-file changes

### 1. `java_codebase_rag/config.py`

**New function: `discover_project_root(start: Path) -> Path | None`**

- Canonicalize `start` via `Path.resolve()`
- Walk from `start` upward, checking each directory for files matching `YAML_CONFIG_FILENAMES`
- First match returns that directory (the parent of the found config file, not the file path itself)
- Stop at `$HOME` (inclusive — check `$HOME` itself) or filesystem root. Do not walk past `$HOME`.
- Return `None` if no config found

**Modify: `resolve_operator_config()` — two-phase resolution**

The core change is separating *config file discovery* from *effective source root resolution*. The exact sequence:

1. **Phase 1 — find the config file directory.** If `source_root` is provided (CLI flag or env var), the config dir = that value (no walk-up). Otherwise, call `discover_project_root(Path.cwd())`. If walk-up found a config dir, use it. Otherwise fall back to `Path.cwd().resolve()` (unchanged behavior).
2. **Load YAML** from the config dir via `load_yaml_mapping(config_dir)`.
3. **Phase 2 — resolve effective source root.** Check for a `source_root` key in the YAML. If present, resolve it relative to the config dir (not cwd). The effective source root is then:
   - CLI `--source-root` (already handled — `source_root` is not `None` in phase 1, so phase 2 is skipped)
   - env `JAVA_CODEBASE_RAG_SOURCE_ROOT` (checked before walk-up in both `server.py` and `resolve_operator_config`)
   - YAML `source_root` (resolved relative to config dir)
   - Walk-up discovery result (= config dir itself, which is the default when no YAML override)
   - `Path.cwd()` (no config found, no YAML override)
4. **Derive index dir** from the effective source root via `_resolve_index_dir_path()`. No edits to `_resolve_index_dir_path` itself — the caller ensures the effective source root (after YAML resolution) is what gets passed through.

**Note:** Do NOT introduce a `find_config_dir` wrapper. The two-phase logic lives directly in `resolve_operator_config()` for clarity. The only new public function is `discover_project_root()`.

### 2. `server.py`

**Modify: `_project_root()`**

- Current logic: env var → cwd fallback
- New logic: env var → `discover_project_root(Path.cwd())` → cwd fallback
- Import `discover_project_root` from `java_codebase_rag.config`

**Modify: `_resolve_lancedb_uri()`**

- Currently falls back to `Path.cwd() / ".java-codebase-rag"` when `JAVA_CODEBASE_RAG_INDEX_DIR` is unset.
- After walk-up, this should use the discovered source root (via `_project_root()`) for consistency.
- The server's `list_code_index_tables_payload()` calls `resolve_operator_config(source_root=_project_root())`, so index dir is derived from the effective source root. But `_resolve_lancedb_uri()` is called independently in some paths. Ensure it uses `_project_root()` instead of raw `Path.cwd()` when the env var is unset.

### 3. `java_codebase_rag/cli.py`

**Modify: `_parse_source_root()` / `_resolved_from_ns()`**

- `_parse_source_root()` stays the same (returns `None` when `--source-root` is not given)
- `_resolved_from_ns()` already passes `source_root=root` to `resolve_operator_config()` — walk-up logic in `resolve_operator_config()` handles the `None` case

**Modify: `init` command handler**

- After resolving `cfg = _resolved_from_ns(args)`, check for a parent config by calling `discover_project_root(cfg.source_root.parent)` — this checks whether a config exists in any ancestor of the *resolved source root* (not the config dir, since `init` creates the config at the source root)
- If found, emit a soft warning to stderr:
  > Warning: found existing config at `[parent]/.java-codebase-rag.yml`. Creating a new project here will create a separate index.

### 4. `graph_enrich.py`

**New function: `detect_microservice_from_path(cwd: Path, source_root: Path) -> str | None`**

- Check if cwd is outside source_root → return None
- Check if cwd IS source_root → return None (at system level, no specific scope)
- Otherwise, call existing `microservice_for_path(cwd, source_root, overrides)` to detect microservice
- Return microservice name or None if not found

**Purpose:** Reuse indexing logic for microservice detection, ensuring query-time scope matches index-time boundaries.

### 5. `server.py`

**New class: `ScopeManager`**

```python
class ScopeManager:
    def __init__(self, source_root: Path):
        self.source_root = source_root
        self.default_scope: str | None = self._detect_scope()
        self._log_detection()

    def _detect_scope(self) -> str | None:
        from graph_enrich import detect_microservice_from_path
        return detect_microservice_from_path(Path.cwd(), self.source_root)

    def _log_detection(self) -> None:
        if self.default_scope:
            print(f"[scope] Detected microservice: {self.default_scope}", file=sys.stderr)
            print(f"[scope] Queries scoped to {self.default_scope}", file=sys.stderr)
        else:
            print(f"[scope] No microservice detected (at project root)", file=sys.stderr)
            print(f"[scope] Queries will span all microservices", file=sys.stderr)

    def apply_auto_scope(self, filter: NodeFilter | dict | None) -> NodeFilter | dict | None:
        if self.default_scope is None:
            return filter
        # Convert to dict for manipulation
        if filter is None:
            filter_dict = {}
        elif isinstance(filter, NodeFilter):
            filter_dict = filter.model_dump(exclude_none=True)
        else:
            filter_dict = dict(filter)
        # Only inject if user didn't specify microservice
        if "microservice" not in filter_dict:
            filter_dict["microservice"] = self.default_scope
        return filter_dict
```

**Modify: server initialization**

- After `_project_root()` resolves source_root, create module-level `_scope_manager = ScopeManager(source_root)`

**Modify: each MCP tool wrapper function**

- Before calling the underlying `mcp_v2` function, apply auto-scope:
  ```python
  scoped_filter = _scope_manager.apply_auto_scope(_coerce_filter(filter))
  return search_v2(query=query, filter=scoped_filter, ...)
  ```

**Modify: add advisory when at system level**

- In tool wrappers, when `_scope_manager.default_scope is None` and filter has no explicit microservice, include advisory in response

### 6. `tests/test_microservice_scope.py` (NEW FILE)

### 4. `mcp.json.example`

- Add a comment block showing the minimal zero-env-var config
- Keep the existing full example as an alternative
- Show both Claude Desktop and Claude Code variants

### 5. `README.md`

- Update the MCP host wiring section to mention walk-up discovery
- Document the `source_root` YAML field
- Update the minimal `.mcp.json` example to show that env vars are now optional

### 6. `docs/CONFIGURATION.md`

- Add `source_root` to the YAML config reference table
- Document the walk-up discovery behavior
- Update the precedence chain table to include the YAML `source_root` field

## Tests for PR-1

All new tests go in **`tests/test_config.py`** (new file). Tests that exercise `_project_root()` in `server.py` go in **`tests/test_mcp_server_project_root.py`** (new file) to keep MCP test concerns separate.

**Test file organization justification:**
- `test_config.py` — Pure unit tests for config discovery and resolution logic (no server/process dependencies)
- `test_mcp_server_project_root.py` — Integration test that specifically exercises server.py's `_project_root()` function in the MCP server context
- **Rationale**: Keeping these separate prevents test pollution — config tests remain fast and isolated, while server integration tests can assume MCP server context and potentially mock server internals
- **Alternative considered**: Adding server tests to `test_config.py` would require importing server.py and its dependencies, making config tests slower and more brittle

### Config discovery tests (`tests/test_config.py`)

1. `test_discover_project_root_finds_config_in_cwd` — config in cwd, returns cwd
2. `test_discover_project_root_walks_up` — config in parent, returns parent
3. `test_discover_project_root_stops_at_home_boundary` — config in `$HOME` itself, walk-up from subdirectory of `$HOME` finds it (inclusive boundary)
4. `test_discover_project_root_not_found_above_home` — no config anywhere under `$HOME`, returns `None`
5. `test_discover_project_root_not_found` — no config anywhere, returns `None`
6. `test_discover_project_root_first_match_wins` — configs at two levels (cwd subdirectory has one, parent has another), closest to cwd wins

### Source root resolution tests (`tests/test_config.py`)

7. `test_source_root_from_yaml_relative` — `source_root: ../` resolves to parent of config dir
8. `test_source_root_from_yaml_absolute` — `source_root: /abs/path` resolves to absolute path
9. `test_source_root_precedence_cli_over_yaml` — CLI flag wins over YAML `source_root`
10. `test_source_root_precedence_yaml_over_discovery` — YAML `source_root` wins over config dir default
11. `test_source_root_precedence_env_over_yaml` — env var wins over YAML `source_root`
12. `test_existing_behavior_unchanged` — no walk-up, cwd = config dir → identical behavior to today

### Server integration test (`tests/test_mcp_server_project_root.py`)

13. `test_project_root_uses_discover_when_env_unset` — `_project_root()` returns discovered config dir when `JAVA_CODEBASE_RAG_SOURCE_ROOT` is unset

### Microservice scope detection tests (`tests/test_microservice_scope.py`)

14. `test_detect_microservice_deep_inside` — Deep inside microservice directory detects that microservice
15. `test_detect_microservice_at_microservice_root` — At microservice root detects that microservice
16. `test_detect_microservice_at_system_root` — At system root returns None (no specific scope)
17. `test_detect_microservice_outside_source` — Outside source_root returns None
18. `test_apply_scope_when_filter_none` — No filter provided injects auto-detected scope
19. `test_apply_scope_when_filter_exists_no_microservice` — Filter without microservice gets auto-scope injected
20. `test_apply_scope_preserves_explicit_microservice` — Explicit microservice not overridden
21. `test_apply_scope_no_default` — No auto-detected scope leaves filter unchanged

## Definition of done (PR-1)

- [ ] `discover_project_root()` works with first-match-wins semantics, stops at `$HOME` (inclusive)
- [ ] `source_root` YAML field is parsed and resolved relative to config dir
- [ ] Precedence chain: CLI > env > YAML > discovery > cwd
- [ ] `_project_root()` in `server.py` uses walk-up when env var is unset
- [ ] `_resolve_lancedb_uri()` in `server.py` uses `_project_root()` instead of raw `Path.cwd()` for fallback
- [ ] CLI commands work from subdirectories (walk-up finds config)
- [ ] `init` emits soft warning when parent config detected
- [ ] `detect_microservice_from_path()` correctly detects microservice from cwd
- [ ] `ScopeManager` detects and caches microservice scope at server startup
- [ ] Tool wrappers apply auto-scope when no explicit microservice filter provided
- [ ] Advisory messages shown when queries span multiple microservices
- [ ] All 21 named tests pass (13 discovery + precedence + 8 microservice scope)
- [ ] Existing test suite passes (no regressions)
- [ ] `mcp.json.example` shows minimal zero-env-var config
- [ ] README and CONFIGURATION docs updated

## Implementation step list

| # | Step | File(s) | Done when |
| - | - | - | - |
| 1 | Add `discover_project_root(start)` | `config.py`, `tests/test_config.py` | Tests 1–6 pass |
| 2 | Add `source_root` YAML field parsing and resolution | `config.py`, `tests/test_config.py` | Tests 7–8 pass |
| 3 | Wire precedence chain in `resolve_operator_config()` | `config.py`, `tests/test_config.py` | Tests 9–12 pass |
| 4 | Update `_project_root()` to use walk-up | `server.py`, `tests/test_mcp_server_project_root.py` | Test 13 passes; server resolves source root via walk-up when env var unset |
| 5 | Update `_resolve_lancedb_uri()` to use `_project_root()` fallback | `server.py` | Lance URI and source root derive from same discovered root |
| 6 | Add `init` parent-config warning | `cli.py` | `init` prints warning when parent config exists |
| 7 | Add `detect_microservice_from_path()` | `graph_enrich.py`, `tests/test_microservice_scope.py` | Tests 14–17 pass |
| 8 | Add `ScopeManager` class | `server.py`, `tests/test_microservice_scope.py` | Tests 18–21 pass |
| 9 | Wire `ScopeManager` into tool wrappers | `server.py` | Auto-scope applied to queries |
| 10 | Add advisory messages for system-level queries | `server.py` | Advisory shown when no microservice detected |
| 11 | Update `mcp.json.example` | `mcp.json.example` | Shows minimal zero-env-var config |
| 12 | Update README and CONFIGURATION docs | `README.md`, `docs/CONFIGURATION.md` | Walk-up, `source_root`, and auto-scope documented |
| 13 | Run full validation | all | `ruff check` + `pytest tests -v` green |

**Documentation timing:**
- Documentation updates (step 12) should happen AFTER implementation is complete and tests pass
- This ensures docs accurately reflect the final implementation
- mcp.json.example (step 11) can be updated in parallel with implementation as it's straightforward

---

# Cross-PR risks and mitigations

N/A — single PR.

# Out of scope

- Auto-detecting multiple systems and splitting indexes
- Changing index directory structure
- Global config or project registry
- Changes to indexing, query, or graph-building logic
- `init` command behavior changes beyond the parent-config warning
- Changes to `build_ast_graph.py` or `search_lancedb.py`
- CLI-level microservice auto-scope (MCP server only for now)
- Dynamic microservice scope re-detection if cwd changes during a long-running session (server restart required)
- Config-based microservice boundary overrides (use existing `microservice_roots` YAML field instead)

# Whole-plan done definition

1. All 21 named tests pass (13 discovery + precedence, 8 microservice scope).
2. Existing test suite passes without `JAVA_CODEBASE_RAG_RUN_HEAVY`.
3. `ruff check .` is clean.
4. CLI and MCP server both resolve source root via walk-up from subdirectories.
5. MCP server auto-detects microservice scope and applies it to queries.
6. `mcp.json.example` shows a zero-env-var configuration.
7. README and CONFIGURATION docs reflect walk-up, `source_root`, and auto-scope behavior.

# Tracking

- `PR-1`: _pending_