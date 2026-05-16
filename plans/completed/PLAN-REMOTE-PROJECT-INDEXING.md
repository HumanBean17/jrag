# Plan: Remote Project Indexing via MCP Configuration

Status: **completed** — shipped (`PROJECT_ROOT` via `_cocoindex_subprocess_env`; see §Status checklist below).

## Goal

Enable users to run `refresh_code_index` from any Java project root without copying Python scripts. Users should only need to provide a valid MCP configuration pointing to their project.

## Current Problem

1. `java_index_flow_lancedb.py` sets `PROJECT_ROOT = Path(".").resolve()` at CocoIndex lifespan initialization
2. `server.py` runs CocoIndex with `cwd=str(flow_path.parent)` — the directory containing the flow file
3. When using the bundled flow file (fallback), `flow_path.parent` is the RAG repo directory
4. **Result:** CocoIndex indexes the RAG bundle directory, not the target Java project
5. `LANCEDB_MCP_PROJECT_ROOT` env var is only used by MCP-side tools (`list_code_index_tables`, `build_ast_graph.py`), not by the CocoIndex flow itself

## Solution

Make `java_index_flow_lancedb.py` respect `LANCEDB_MCP_PROJECT_ROOT` environment variable for determining which directory to index, and ensure `server.py` passes this variable to the CocoIndex subprocess.

---

## Implementation Steps

### Step 1: Modify `java_index_flow_lancedb.py`

**File:** `java_index_flow_lancedb.py`  
**Location:** Lines 107-112 (inside `coco_lifespan`)

**Current code:**
```python
@coco.lifespan
async def coco_lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
    builder.settings.db_path = Path(
        os.environ.get("COCOINDEX_DB", "./cocoindex_java_lance.db")
    )
    root = Path(".").resolve()
    builder.provide(PROJECT_ROOT, root)
```

**New code:**
```python
@coco.lifespan
async def coco_lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
    builder.settings.db_path = Path(
        os.environ.get("COCOINDEX_DB", "./cocoindex_java_lance.db")
    )
    env_root = os.environ.get("LANCEDB_MCP_PROJECT_ROOT", "").strip()
    if env_root:
        root = Path(env_root).expanduser().resolve()
    else:
        root = Path(".").resolve()
    builder.provide(PROJECT_ROOT, root)
```

**Rationale:** 
- Checks for `LANCEDB_MCP_PROJECT_ROOT` first
- Falls back to `Path(".")` for backward compatibility (standalone usage)
- Uses `expanduser()` to handle `~` in paths

---

### Step 2: Modify `server.py` to pass environment to subprocess

**File:** `server.py`  
**Location:** Lines 1059-1070 (inside `refresh_code_index`)

**Current code:**
```python
try:
    proc = await asyncio.create_subprocess_exec(
        str(cocoindex_bin),
        "update",
        _COCOINDEX_TARGET,
        "--full-reprocess",
        "-f",
        cwd=str(flow_path.parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
```

**New code:**
```python
try:
    sub_env = os.environ.copy()
    sub_env["LANCEDB_MCP_PROJECT_ROOT"] = str(root)
    proc = await asyncio.create_subprocess_exec(
        str(cocoindex_bin),
        "update",
        _COCOINDEX_TARGET,
        "--full-reprocess",
        "-f",
        cwd=str(flow_path.parent),
        env=sub_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
```

**Rationale:**
- `cwd=flow_path.parent` is preserved so Python imports work (bundle modules like `java_index_v1_common`, `ast_java`, etc.)
- `LANCEDB_MCP_PROJECT_ROOT` is explicitly set to the resolved project root
- Inherits all other environment variables (including `LANCEDB_URI`, `SBERT_MODEL`, etc.)

---

### Step 3: Update documentation

**File:** `README.md`

Add a section or update existing documentation to clarify:
- Users can index any Java project by setting `LANCEDB_MCP_PROJECT_ROOT` in MCP config
- No need to copy `java_index_flow_lancedb.py` or other scripts
- Example MCP configuration for remote project indexing

---

## User Configuration After Implementation

Users will configure their MCP client (Cursor, Claude Code, etc.) like this:

```json
{
  "mcpServers": {
    "lancedb-code": {
      "type": "stdio",
      "command": "/path/to/java-codebase-rag/.venv/bin/python",
      "args": ["/path/to/java-codebase-rag/server.py"],
      "env": {
        "LANCEDB_MCP_ALLOW_REFRESH": "1",
        "LANCEDB_MCP_PROJECT_ROOT": "/path/to/my-java-project",
        "LANCEDB_URI": "/path/to/my-java-project/.rag/lancedb_data",
        "SBERT_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
        "LANCEDB_MCP_GRAPH_ENABLED": "1"
      }
    }
  }
}
```

**Key points:**
- `LANCEDB_MCP_PROJECT_ROOT` points to the Java project to index
- `LANCEDB_URI` can be anywhere (often inside the target project's `.rag/` folder)
- No scripts need to be copied to the target project

---

## Testing Plan

1. **Unit test:** Verify `java_index_flow_lancedb.py` respects `LANCEDB_MCP_PROJECT_ROOT`
2. **Integration test:** Run `refresh_code_index` with MCP config pointing to a separate Java project
3. **Verify:** Indexed files are from the target project, not the bundle directory
4. **Backward compatibility:** Ensure standalone `cocoindex update` still works when env var is not set

---

## Files Changed

| File | Change |
|------|--------|
| `java_index_flow_lancedb.py` | Read `LANCEDB_MCP_PROJECT_ROOT` for `PROJECT_ROOT`; docstring |
| `server.py` | `_cocoindex_subprocess_env(root)` + pass `env=` to CocoIndex subprocess |
| `README.md` | Env table + `refresh_code_index` subprocess note |
| `mcp.json.example` | Example `LANCEDB_MCP_PROJECT_ROOT` path |
| `CODEBASE_REQUIREMENTS.md` | Project root / CocoIndex / MCP consistency |
| `tests/test_mcp_tools.py` | `test_cocoindex_subprocess_env_sets_project_root` |

---

## Status

- [x] Step 1: Modify `java_index_flow_lancedb.py`
- [x] Step 2: Modify `server.py` (including `_cocoindex_subprocess_env` helper + unit test)
- [x] Step 3: Update documentation (`README.md`, `mcp.json.example`, `CODEBASE_REQUIREMENTS.md`, flow docstring)
- [x] Testing: `test_cocoindex_subprocess_env_sets_project_root`; heavy e2e unchanged (`cwd` + no env → `.` root)
