<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# Plan: Fix cocoindex Binary Path Resolution in Venv

Status: **completed** — shipped (`Path(sys.executable).parent / "cocoindex"` without `.resolve()` in `server.py`).

## Goal

Fix `refresh_code_index` failing to find `cocoindex` binary when Python venv uses symlinks.

## Current Problem

**File:** `server.py`, line 1048

**Current code:**
```python
cocoindex_bin = Path(sys.executable).resolve().parent / "cocoindex"
```

**What happens:**
1. In a venv, `sys.executable` is often a symlink: `/path/to/venv/bin/python` → `/usr/bin/python3.11`
2. `.resolve()` follows the symlink to its ultimate target
3. `Path(sys.executable).resolve()` returns `/usr/bin/python3.11`
4. `.parent` returns `/usr/bin/`
5. `/ "cocoindex"` yields `/usr/bin/cocoindex` — **wrong location**

**Expected behavior:**
- Should find `/path/to/venv/bin/cocoindex` (where pip installs the binary)

---

## Solution

Remove `.resolve()` when constructing the `cocoindex` binary path so we keep the venv symlink location (`venv/bin/python`) instead of jumping to the system interpreter location (`/usr/bin/python3.x`).

---

## Implementation Steps

### Step 1: Fix cocoindex binary path resolution

**File:** `server.py`  
**Location:** Line 1048

**Current code:**
```python
cocoindex_bin = Path(sys.executable).resolve().parent / "cocoindex"
```

**New code:**
```python
cocoindex_bin = Path(sys.executable).parent / "cocoindex"
```

**Rationale:**
- `sys.executable` is always absolute in normal execution
- Removing `.resolve()` preserves the symlink path, keeping us in the venv's `bin/` directory
- `cocoindex` is installed by pip into the same `bin/` directory as the venv's Python symlink
- Existing `is_file()` guard continues to fail safely with a clear error if the binary is genuinely missing

---

### Step 2: Verify no similar issues exist

**Check:** Line 1055 uses `Path(__file__).resolve().parent`

```python
bundle_dir = Path(__file__).resolve().parent
```

**Analysis:** This is correct and should NOT be changed because:
- `__file__` can be a relative path
- We want the canonical location of `server.py` itself
- If `server.py` is symlinked, we want to find sibling files (`java_index_flow_lancedb.py`, `build_ast_graph.py`) relative to the actual file location, not the symlink

---

### Step 3: Verify behavior end-to-end

**Manual validation commands:**

```bash
python -c "import sys; from pathlib import Path; print(sys.executable); print(Path(sys.executable).parent / 'cocoindex')"
```

Expected:
- first line points to venv python path
- second line points to venv `cocoindex` path

Then run:
- `refresh_code_index(confirm=true)` via MCP tool
- confirm it no longer errors with `cocoindex not found next to Python`

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `sys.executable` is relative | Very low (only in embedded Python) | Not a concern for MCP server use case |
| Path contains `..` components | Very low | `sys.executable` is typically clean |
| Different behavior on Windows | Low | Existing `is_file()` check and explicit error message cover missing binary cases |

---

## Testing Plan

1. **Manual test:** Activate venv, verify `Path(sys.executable).parent / "cocoindex"` returns correct path
2. **Regression check:** Ensure `Path(__file__).resolve().parent` remains unchanged and flow fallback logic still works
3. **Integration test:** Run `refresh_code_index` MCP tool and verify it finds `cocoindex`
4. **Verify existing tests pass:** No test changes needed (path resolution is internal detail)

---

## Files Changed

| File | Change |
|------|--------|
| `server.py` | Remove `.resolve()` from cocoindex binary path construction |

---

## Status

- [x] Step 1: Fix cocoindex binary path resolution
- [x] Step 2: Verify no similar path resolution issues exist (no other `cocoindex` path uses `.resolve()` on `sys.executable`)
- [x] Step 3: Verify fix works in venv environment (`tests/test_mcp_tools.py::test_cocoindex_subprocess_env_sets_project_root`)
